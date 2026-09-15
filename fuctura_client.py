"""
Cliente do painel Fuctura (schoolfine.fuctura.com.br).

Cada instancia de FucturaClient representa a sessao de UM funcionario logado
no sistema-mascara com o proprio login/senha do Fuctura - nao ha credencial
compartilhada. Reaproveita os endpoints e o formato de formularios mapeados
por engenharia reversa ao longo deste projeto.
"""
import html
import re
import threading
import time

import fitz  # PyMuPDF
import requests

BASE = "https://schoolfine.fuctura.com.br"
AUTH = ("sistema", "alegre@2021")  # autenticacao fixa de infraestrutura, nao e login pessoal

# Achado real em 2026-09-07: o WAF (Mod_Security) do Fuctura passou a
# devolver 406 Not Acceptable pra TODA requisicao, disfarcado como "login
# incorreto" - por 2 dias seguidos, mesmo com IP diferente a cada tentativa
# (confirmado que nao era o IP). Isolado trocando SO o header User-Agent:
# com "curl/8.0.1" (o default do requests/curl) sempre bloqueava; com um
# User-Agent de navegador de verdade, voltou a funcionar na hora. Nunca
# mais usar um User-Agent de cliente HTTP "cru" aqui.
_HEADERS_PADRAO = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "*/*",
}

TURMA_DEVEDOR_ID = "1138"   # DEVEDOR/PENDENCIA
TURMA_ADVOGADO_ID = "1136"  # AGUARDANDO ADVOGADO

# Situacao (campo "status" do cadastro do aluno, detalhes_alunos.php) -
# mapeado ao vivo em 2026-09-03 lendo o <select name="status"> real.
STATUS_ALUNO = {
    "4": "-Matriculado", "30": "Advogado", "5": "Cancelado",
    "29": "Cliente sem Interesse", "26": "Convenio", "7": "Devedor",
    "22": "Ex-aluno", "19": "Interessado", "6": "Pediu Pausa",
    "31": "Provi/Pravaler",
}

# Tipo do comentario/acompanhamento (campo "tipo" de gravar_comentario/
# editar_comentario) - mapeado ao vivo lendo o <select name="tipo"> real de
# lista_alunos.php. Deixa de fora o lixo/depreciado (10,12,18,27="x---",
# 24="x--- Empenho - Acompanhar") - nao faz sentido oferecer isso num
# formulario novo.
TIPO_COMENTARIO = {
    "3": "-Comentário", "15": "-Matricula", "11": "-Pagamento Realizado",
    "13": "Abatimento/Devolução", "14": "Aguardando Turma",
    "1": "Importante", "2": "Urgente",
}

# Forma de pagamento (campo "formaPagamento" de gravar_comentario) - mapeada
# ao vivo lendo o <select name="formaPagamento3"> real de lista_alunos.php.
FORMA_PAGAMENTO = {
    "cartao": "Cartão de Crédito", "debito": "Débito Bancário",
    "especie": "Dinheiro", "boleto": "Boleto Bancário", "cheque": "Cheque",
}


def strip_tags(s):
    return re.sub("<[^<]+?>", "", s or "").strip()


def parse_money(s):
    if not s:
        return 0.0
    s = s.replace("&nbsp;", " ").replace("R$", "").strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


class FucturaAuthError(Exception):
    pass


# Achado real (identificado 2026-09-08, corrigido a seguir): o Fuctura nao
# aceita atualizar um campo isolado do cadastro - atualizar_cadastro_aluno()
# sempre busca o cadastro INTEIRO, mescla a mudanca, e reenvia tudo. Se
# dois funcionarios (duas instancias de FucturaClient, uma por sessao)
# chamarem isso pro MESMO aluno quase ao mesmo tempo, o ciclo
# ler-mesclar-reenviar de um pode terminar DEPOIS do outro e apagar a
# mudanca dele silenciosamente - classica "lost update". Um Lock por
# id_aluno, compartilhado entre TODAS as instancias no mesmo processo,
# serializa esse ciclo pro mesmo aluno - reduz bastante o risco pra quem
# usa o sistema-mascara (nao elimina: alguem editando direto no site do
# Fuctura, fora do nosso alcance, ainda pode colidir).
#
# _locks_por_aluno nunca remove entradas (um Lock por id_aluno que algum
# dia foi editado fica pra sempre) - aceitavel de proposito: cada Lock e
# minusculo (poucas dezenas de bytes), e remover um lock enquanto alguem
# ainda pode estar segurando referencia a ele e uma fonte de bug pior que
# o espaco economizado vale a pena.
_locks_por_aluno = {}
_locks_por_aluno_meta = threading.Lock()


def _lock_para_aluno(id_aluno):
    with _locks_por_aluno_meta:
        if id_aluno not in _locks_por_aluno:
            _locks_por_aluno[id_aluno] = threading.Lock()
        return _locks_por_aluno[id_aluno]


class FucturaClient:
    # Achado real 2026-09-15: rodando um lote de ~90 gravacoes na mesma
    # sessao, so as primeiras ~25 (75 requisicoes, 3 por aluno: perfil +
    # comentarios + gravar) realmente persistiram - as demais devolveram
    # HTTP 200 normalmente, mas NUNCA apareceram no cadastro do aluno
    # (confirmado via dois caminhos de leitura independentes, perfil_aluno
    # e roster_turma). Reescrever a MESMA gravacao com uma sessao nova
    # (login do zero) funcionou na hora, sempre - leitura nunca teve esse
    # problema, so escrita. Ou seja: a sessao do Fuctura degrada por
    # VOLUME de requisicoes acumuladas (nao so por tempo, que ja era
    # tratado em _get/_post via _logged_in_at), silenciosamente, sem
    # avisar (HTTP continua 200). Corrigido forcando relogin periodico
    # por CONTAGEM de requisicao, nao so por tempo - valor escolhido com
    # boa margem abaixo do ponto onde o problema comecou a aparecer nos
    # testes reais.
    LIMITE_REQUESTS_POR_SESSAO = 30

    def __init__(self, login, senha):
        self.login = login
        self.senha = senha
        self.session = requests.Session()
        self.session.headers.update(_HEADERS_PADRAO)
        self._logged_in_at = 0
        self._requests_desde_login = 0

    def entrar(self):
        r = self.session.post(
            f"{BASE}/ctrl_acesso.php", auth=AUTH,
            data={"login": self.login, "senha": self.senha},
            timeout=15, allow_redirects=False,
        )
        # Achado real 2026-09-12: um bloqueio de IP do lado do Fuctura (403
        # "Acesso negado", pagina de hospedagem tipo cPanel, nem chega a
        # avaliar login/senha) reproduzia a MESMA mensagem "Login ou senha
        # incorretos" - confundindo bloqueio de infraestrutura com senha
        # errada de verdade (mesmo padrao do achado do WAF/User-Agent em
        # 2026-09-07, ver fuctura_client.AUTH). Separado aqui pra nao
        # induzir ninguem a trocar a propria senha por engano quando o
        # problema real e o IP nao estar autorizado.
        if r.status_code in (403, 406) or r.status_code >= 500:
            raise FucturaAuthError(
                f"O Fuctura recusou a conexão antes de checar login/senha (HTTP {r.status_code}) - "
                "geralmente é bloqueio de IP/infraestrutura do lado do Fuctura, não senha errada. "
                "Confirme se o IP deste servidor está autorizado."
            )
        if r.status_code != 302:
            raise FucturaAuthError("Login ou senha incorretos.")
        self._logged_in_at = time.time()
        self._requests_desde_login = 0
        self.nome_usuario = self._buscar_nome_usuario()

    def _buscar_nome_usuario(self):
        """O proprio Fuctura mostra o nome de quem esta logado no menu
        (<a href='ctrl_navegacao.php?p=dados_usuario'>NOME</a>) - usado pra
        permitir configurar administradores do sistema-mascara por nome, nao
        so pelo login numerico."""
        try:
            r = self.session.get(f"{BASE}/principal.php", auth=AUTH, timeout=15)
            m = re.search(r"p=dados_usuario\"[^>]*>([^<]+)</a>", r.text)
            return html.unescape(m.group(1)).strip() if m else ""
        except requests.exceptions.RequestException:
            return ""

    def _relogar_se_necessario(self):
        if time.time() - self._logged_in_at <= 600 and self._requests_desde_login < self.LIMITE_REQUESTS_POR_SESSAO:
            return
        # sessao NOVA (nao so re-logar na mesma) - o teste real que
        # confirmou o achado de 2026-09-15 usou sempre um FucturaClient
        # novo do zero; nao temos certeza se so re-postar login na MESMA
        # requests.Session (mesmo cookie jar) resolve a degradacao, entao
        # nao arrisca - troca a sessao tambem, mesmo padrao ja usado no
        # retry por excecao de rede em _get().
        self.session.close()
        self.session = requests.Session()
        self.session.headers.update(_HEADERS_PADRAO)
        self.entrar()

    def _get(self, path, params=None):
        self._relogar_se_necessario()
        self._requests_desde_login += 1
        for attempt in range(3):
            try:
                return self.session.get(f"{BASE}/{path}", auth=AUTH, params=params, timeout=25)
            except requests.exceptions.RequestException:
                self.session.close()
                self.session = requests.Session()
                self.session.headers.update(_HEADERS_PADRAO)
                self.entrar()
        raise RuntimeError(f"Falha ao buscar {path} apos 3 tentativas")

    def _post(self, path, data):
        self._relogar_se_necessario()
        self._requests_desde_login += 1
        return self.session.post(f"{BASE}/{path}", auth=AUTH, data=data, timeout=25)

    # ---------- turmas ----------
    ENTIDADES_TURMA = {
        "2": "Boa Viagem - BV", "5": "Caruaru - Car", "1": "Espinheiro - ESP",
        "4": "Porto Digital - POR", "3": "Todas - All",
    }
    # limites reais do banco, descobertos ao vivo em 2026-09-04: o form em
    # lista_turmas.php anuncia maxlength=100 pros dois campos, mas o servidor
    # trunca silenciosamente sem avisar (testado com turma --TESTE IA--,
    # id_turma=1373: "abreviada" de 12 chars virou "--TES", 5 chars;
    # "descricao" de 50 chars foi cortada em ~30). Usado pra validar no
    # nosso front ANTES de enviar, pra funcionario nao digitar um nome e o
    # Fuctura salvar outro sem aviso nenhum.
    LIMITE_ABREVIADA_TURMA = 5
    LIMITE_DESCRICAO_TURMA = 30

    def criar_turma(self, abreviada, entidade, descricao, professor, data_inicio="", data_termino=""):
        """Cria uma turma NOVA de verdade no Fuctura. Ao contrario de
        criar_aluno(), aqui NAO existe campo oculto tipo 'cadTurma' - o
        proprio form 'formCadastro2' embutido em lista_turmas.php cria ao
        POSTar com idTurma vazio (confirmado ao vivo, funcionou de primeira).

        CUIDADO: 'abreviada' e 'descricao' sao truncados pelo banco sem
        aviso (ver LIMITE_ABREVIADA_TURMA/LIMITE_DESCRICAO_TURMA) - valide
        o tamanho ANTES de chamar, nao confie no maxlength=100 do HTML.

        Retorna o id_turma recem-criado (string), ou None se a resposta nao
        trouxer um idTurma preenchido."""
        data = {
            "abreviada": abreviada, "entidade": str(entidade), "descricao": descricao,
            "professor": professor, "dataInicio": data_inicio, "dataTermino": data_termino,
            "idTurma": "",
        }
        r = self._post("lista_turmas.php", data)
        m = re.search(r'name="idTurma"[^>]*value="([^"]*)"', r.text)
        return m.group(1) if m and m.group(1) else None

    def buscar_turma_por_nome(self, termo):
        """Autocomplete de turma (mesmo endpoint usado pelo formulario de acompanhamento)."""
        r = self._get("ctrl_autocomplete_turma.php", params={"query": termo, "identifier": "buscaTurma"})
        out = []
        for m in re.finditer(r"<li id=\"autocomplete_(\d+)\" rel=\"(\d+)\">([^<]*)</li>", r.text):
            out.append({"id_turma": m.group(2), "nome": html.unescape(m.group(3))})
        return out

    NOME_TURMA_TRIAGEM_INTERESSADOS = "-IA- Interessados"

    def roster_triagem_interessados(self):
        """Roster da turma de triagem "-IA- Interessados" (criada em
        2026-09-13, ver colocar_interessados_sem_turma.py) - None se a
        turma ainda não existir (ex: antes da primeira vez que alguém for
        colocado lá)."""
        encontradas = self.buscar_turma_por_nome(self.NOME_TURMA_TRIAGEM_INTERESSADOS)
        for t in encontradas:
            if t["nome"].strip() == self.NOME_TURMA_TRIAGEM_INTERESSADOS:
                return t["id_turma"], self.roster_turma(t["id_turma"])
        return None, []

    def listar_turmas_interessados(self):
        """Turmas "fila de interessados" por curso - convenção real
        "-I-(CURSO)" (achado ao vivo em 2026-09-12, pedido do Diógenes via
        Caio: aluno Interessado num curso deve ficar matriculado numa
        dessas, não só ter o resumo registrado num comentário solto).
        Confirmadas 27 no sistema (Java, PHP, Linux, LPI I/II, Python,
        Android, Ruby, Arduino, etc.) buscando "-I-(" no autocomplete de
        turma - reaproveita o mesmo endpoint, sem mecanismo novo."""
        return sorted(self.buscar_turma_por_nome("-I-("), key=lambda t: t["nome"].strip().upper())

    def buscar_alunos_por_nome(self, termo):
        """Autocomplete de aluno (mesmo endpoint usado pela busca rapida no
        topo de qualquer pagina do painel, ctrl_autocomplete.php - descoberto
        investigando o JS de lista_alunos.php). Retorna nome, matricula,
        celular e unidade de cada resultado, quando presentes."""
        r = self._post("ctrl_autocomplete.php", {"searchword": termo})
        out = []
        for bloco in re.finditer(
            r"idAluno=(\d+)[^>]*>.*?<b>([^<]+)</b>(.*?)<br",
            r.text, re.DOTALL,
        ):
            id_aluno, nome_negrito, resto = bloco.groups()
            # o Fuctura so deixa em NEGRITO o trecho da busca que bateu, nao
            # o nome inteiro (ex: buscar "tey" produz "<b>TEY</b>LON NUNES
            # GOMES (ADVOGADO)&nbsp;-&nbsp;913782..." - o resto do nome
            # continua em texto normal, ANTES do separador de verdade) -
            # achado real buscando "Teylor Nunes" (2026-09-08): nome
            # devolvido vinha truncado pro pedaco em negrito ("TEY") em vez
            # do nome completo. A sequencia literal '&nbsp;-&nbsp;' marca
            # onde o nome acaba e os campos (matricula/celular/unidade)
            # comecam - tudo ANTES dela ainda faz parte do nome (inclusive
            # sufixo tipo "(ADVOGADO)", que o proprio Fuctura guarda junto
            # do nome de verdade - confirmado comparando com
            # buscar_cadastro_completo).
            idx_sep = resto.find("&nbsp;-&nbsp;")
            if idx_sep != -1:
                nome_completo = nome_negrito + resto[:idx_sep]
                resto = resto[idx_sep:]
            else:
                nome_completo = nome_negrito
            partes = [html.unescape(p).strip() for p in resto.replace("&nbsp;", " ").split(" - ") if p.strip()]
            out.append({
                "id_aluno": id_aluno, "nome": html.unescape(nome_completo).strip(),
                "matricula": partes[0] if len(partes) > 0 else "",
                "celular": partes[1] if len(partes) > 1 else "",
                "unidade": partes[2] if len(partes) > 2 else "",
            })
        return out

    def roster_turma(self, id_turma):
        params = {
            "idT": id_turma, "idC": "15", "sEcho": "1", "iColumns": "6",
            "iDisplayStart": "0", "iDisplayLength": "5000",
            "iSortingCols": "1", "iSortCol_0": "1", "sSortDir_0": "desc", "sSearch": "",
        }
        r = self._get("server_datatables_aluno_turmas.php", params=params)
        out = []
        for row in r.json().get("aaData", []):
            clean = [strip_tags(x) for x in row]
            if len(clean) < 6:
                continue
            out.append({
                "id_aluno": clean[0], "data_entrada": clean[1], "nome": html.unescape(clean[2]),
                "status": clean[3], "observacao": clean[4], "telefone": clean[5],
            })
        return out

    def ids_turmas_controle_detalhado(self):
        """Como ids_turmas_controle(), mas com os dois conjuntos SEPARADOS
        ({'devedor': set, 'advogado': set}) - o Fechamento/Acompanhamento de
        Turma precisa saber EM QUAL turma de controle o aluno esta (ou se
        nao esta em nenhuma), nao so se esta em alguma."""
        return {
            "devedor": {a["id_aluno"] for a in self.roster_turma(TURMA_DEVEDOR_ID)},
            "advogado": {a["id_aluno"] for a in self.roster_turma(TURMA_ADVOGADO_ID)},
        }

    def ids_turmas_controle(self):
        d = self.ids_turmas_controle_detalhado()
        return d["devedor"] | d["advogado"]

    def _baixar_ata_chamada_pdf(self, id_turma):
        """POST bruto pro relatorio de Ata de Chamada - fatorado de
        gerar_ata_chamada_oficial() pra poder ser reaproveitado tambem por
        gerar_ata_chamada_pdf_bruto() (quando so o PDF original interessa,
        ex: anexo de email, sem precisar re-parsear o texto). Retorna os
        bytes do PDF, ou None se a turma nao tiver ninguem vinculado
        (o painel devolve um alerta em HTML em vez de PDF nesse caso)."""
        data = {
            "tp": "ataChamada", "turma": id_turma, "situacao": "", "nome": "",
            "ordem": "t.descricao ASC, al.nome ASC, ac.data ASC, en.nome_entidade ASC",
        }
        r = self._post("impressao_ata_chamada.php", data)
        return r.content if r.content.startswith(b"%PDF") else None

    def gerar_ata_chamada_pdf_bruto(self, id_turma):
        """PDF original da Ata de Chamada, sem nenhum parsing (pra anexar
        em email, imprimir etc.) - mesmo endpoint de gerar_ata_chamada_oficial,
        so que devolve os bytes crus em vez dos dados extraidos."""
        return self._baixar_ata_chamada_pdf(id_turma)

    def gerar_ata_chamada_oficial(self, id_turma):
        """Gera a 'Ata de Chamada' EM BRANCO do proprio painel (relatorio
        oficial, Relatorios > Ata de Chamada) pra uma turma - fonte de
        verdade mais confiavel que roster_turma() pra saber quem realmente
        esta vinculado a turma, porque traz TODOS os alunos vinculados (nao
        so quem tem status ativo/matriculado no filtro do roster_turma) mais
        a coluna Situacao (SIT/Observacao) e celular direto do cadastro, sem
        depender de OCR de uma ata manuscrita.

        Endpoint descoberto navegando o proprio painel (Relatorios > Ata de
        Chamada -> 'Visualizar' gera um PDF via impressao_ata_chamada.php).
        Retorna {'turma', 'professor', 'alunos': [{'nome','situacao','celular'}]}."""
        pdf_bytes = self._baixar_ata_chamada_pdf(id_turma)
        if pdf_bytes is None:
            # sem alunos vinculados a turma, o painel devolve um alerta em HTML em vez de PDF
            return {"turma": None, "professor": None, "alunos": []}

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        texto = "\n".join(pagina.get_text() for pagina in doc)
        doc.close()

        turma_m = re.search(r"Turma:\s*(.+)", texto)
        professor_m = re.search(r"Professor:\s*(.+)", texto)

        linhas = [l.strip() for l in texto.split("\n")]
        try:
            i = linhas.index("Observação") + 1
        except ValueError:
            i = 0
        alunos = []
        n = len(linhas)
        while i < n:
            linha = linhas[i]
            if not linha or linha.startswith("Alunos Listados"):
                break
            nome = linha
            i += 1
            if i >= n:
                break
            situacao = linhas[i]
            i += 1
            if i >= n:
                break
            celular = linhas[i]
            i += 1
            while i < n and linhas[i] == "":
                i += 1
            alunos.append({"nome": nome, "situacao": situacao, "celular": celular.split("/")[0]})

        return {
            "turma": turma_m.group(1).strip() if turma_m else None,
            "professor": professor_m.group(1).strip() if professor_m else None,
            "alunos": alunos,
        }

    def listar_devedores_ao_vivo(self, max_paginas=4):
        """Lista viva da pagina 'Devedores' do proprio painel (tp=devedores) -
        pega TODO devedor do sistema, independente de turma atual (util pra
        cruzar aluno que sumiu do roster de uma turma mas ainda consta como
        devedor em algum lugar). Reaproveitado do projeto fuctura-propensao-
        pagamento, ja validado ao vivo la."""
        candidatos = {}
        for pagina in range(1, max_paginas + 1):
            params = {"tp": "devedores"}
            if pagina > 1:
                params["pagina"] = str(pagina)
            r = self._get("lista_alunos_geral.php", params=params)
            rows = re.findall(
                r"<td height='25'[^>]*>(\d+)</td>\s*<td[^>]*>([^<]*)</td>\s*"
                r"<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>",
                r.text,
            )
            if not rows:
                break
            for id_aluno, data_reg, nome, celular in rows:
                nome = html.unescape(nome).strip()
                if id_aluno not in candidatos:
                    candidatos[id_aluno] = {
                        "id_aluno": id_aluno, "nome": nome, "celular": celular.strip(),
                        "data_mais_recente": data_reg.strip(),
                    }
        return list(candidatos.values())

    # ---------- aluno ----------
    def perfil_aluno(self, id_aluno):
        r = self._get("lista_alunos.php", params={"idAluno": id_aluno})
        c = r.text
        out = {"id_aluno": id_aluno}
        m = re.search(r'name="nome" id="nome"[^>]*value="([^"]*)"', c)
        out["nome"] = html.unescape(m.group(1)) if m else ""
        m = re.search(r'name="celular" id="celular"[^>]*value="([^"]*)"', c)
        out["celular"] = html.unescape(m.group(1)) if m else ""
        m = re.search(r"<option value='\d+' selected='selected'>([^<]*)</option>", c)
        out["status"] = m.group(1) if m else ""
        turmas = re.search(r"Turmas:</td>\s*<td[^>]*>(.*?)</td>\s*<td", c, re.DOTALL)
        turmas_raw = turmas.group(1) if turmas else ""
        entradas = re.findall(
            r"-\s*(\d{2}/\d{2}/\d{4})\s*-\s*\.?([^|<]+)",
            strip_tags(turmas_raw.replace("</br>", "|").replace("<br>", "|").replace("<br/>", "|")),
        )
        out["turmas_atuais"] = [{"data": d.strip(), "nome": n.strip()} for d, n in entradas]
        for label, key in [("Contratado", "contratado"), ("Recebido", "recebido"), ("Diferença", "diferenca")]:
            m = re.search(label + r"</strong></td>\s*<td[^>]*>(?:<font[^>]*>)?([^<]*)", c)
            out[key] = parse_money(m.group(1)) if m else 0.0
        return out

    def comentarios_aluno(self, id_aluno):
        params = {
            "idA": id_aluno, "sEcho": "1", "iColumns": "6", "iDisplayStart": "0",
            "iDisplayLength": "1000", "iSortingCols": "1", "iSortCol_0": "1",
            "sSortDir_0": "asc", "sSearch": "",
        }
        r = self._get("server_datatables_aluno.php", params=params)
        out = []
        for row in r.json().get("aaData", []):
            clean = [strip_tags(x) for x in row]
            if len(clean) < 6:
                continue
            out.append({
                "id_acomp": clean[0], "data": clean[1], "titulo": clean[2],
                "tipo": clean[3], "autor": clean[4], "texto": clean[5],
            })
        return out

    def buscar_comentarios_por_tipo(self, tipo, termo="", max_registros=5000,
                                     tamanho_pagina=500, recentes_primeiro=False):
        """Busca GERAL de comentarios/acompanhamentos em TODO o sistema, de
        QUALQUER aluno, independente do status atual dele - endpoint
        descoberto em 2026-09-08 (`server_datatables_acomp.php`, achado a
        partir da secao 'Suas Postagens' do proprio painel) enquanto
        investigavamos por que a limpeza do flood de 04/09 nao pegava
        alunos cujo status tinha mudado depois do flood (roster de devedor
        so lista quem E devedor AGORA, nao quem era na epoca).

        Ao contrario de comentarios_aluno() (por aluno), essa busca e por
        TIPO (`tipo`, ver TIPO_COMENTARIO - vira o parametro 'cl') + texto
        livre em titulo/descricao (`termo`, mesmo campo sSearch do
        DataTables) - cruza o sistema inteiro de uma vez, paginando
        sozinho. Confirmado ao vivo em 2026-09-08: reproduziu exatamente os
        434 comentarios de "--Removido Por Flood--" da limpeza do flood,
        sem faltar nem sobrar nenhum.

        CUIDADO: a linha devolvida NAO traz id_aluno (so id_acomp) - use
        resolver_id_aluno_por_acomp() se precisar editar o comentario
        encontrado (ex: neutralizar um comentario indevido).

        ATENCAO ordem (achado real 2026-09-10): esse endpoint devolve os
        registros do MAIS ANTIGO pro mais recente (ignora o sSortDir_0=desc
        que a gente manda). Por padrao a paginacao vai do inicio (registros
        de 2017 primeiro) - com recentes_primeiro=True ela pula direto pro
        FIM (le o total numa sondagem rapida, busca so os ultimos
        max_registros) e devolve ja invertido, mais recente primeiro. Use
        isso sempre que quiser "os N mais recentes" (ex: tela inicial) em
        vez de "todos" (ex: auditoria do flood)."""
        out = []
        if recentes_primeiro:
            sonda = self._get("server_datatables_acomp.php", params={
                "cl": str(tipo), "pf": "2", "clD": "7", "sEcho": "1", "iColumns": "6",
                "iDisplayStart": "0", "iDisplayLength": "1", "iSortingCols": "1",
                "iSortCol_0": "1", "sSortDir_0": "desc", "sSearch": termo,
            })
            total = int(sonda.json().get("iTotalDisplayRecords") or 0)
            inicio = max(0, total - max_registros)
        else:
            inicio = 0
        while True:
            params = {
                "cl": str(tipo), "pf": "2", "clD": "7",
                "sEcho": "1", "iColumns": "6", "iDisplayStart": str(inicio),
                "iDisplayLength": str(tamanho_pagina), "iSortingCols": "1",
                "iSortCol_0": "1", "sSortDir_0": "desc", "sSearch": termo,
            }
            r = self._get("server_datatables_acomp.php", params=params)
            data = r.json()
            rows = data.get("aaData", [])
            for row in rows:
                clean = [strip_tags(x) for x in row]
                if len(clean) < 5:
                    continue
                out.append({
                    "id_acomp": clean[0], "data": clean[1], "titulo": clean[2],
                    "tipo": clean[3], "texto": clean[4],
                })
            total = int(data.get("iTotalDisplayRecords") or len(rows))
            inicio += len(rows)
            if inicio >= total or len(rows) < tamanho_pagina or len(out) >= max_registros:
                break
        if recentes_primeiro:
            out.reverse()
        return out

    def resolver_id_aluno_por_acomp(self, id_acomp):
        """Descobre o id_aluno dono de um id_acomp (comentario/acompanhamento) -
        usado depois de buscar_comentarios_por_tipo(), que so devolve
        id_acomp. O proprio lista_alunos.php aceita 'idAcomp' no lugar de
        'idAluno' e carrega a pagina do aluno dono do comentario (achado ao
        vivo em 2026-09-08). Retorna None se nao encontrar."""
        r = self._get("lista_alunos.php", params={"idAcomp": id_acomp})
        m = re.search(r'name="idAluno"[^>]*value="([^"]*)"', r.text)
        return m.group(1) if m and m.group(1) else None

    def resolver_aluno_por_acomp(self, id_acomp):
        """Como resolver_id_aluno_por_acomp, mas tambem devolve o NOME - a
        mesma pagina (lista_alunos.php?idAcomp=X) ja traz os dois campos,
        entao sai de graca (sem chamada extra) quando quem chama precisa
        identificar o aluno numa lista, nao so o id. Retorna (id_aluno, nome)
        ou (None, None) se nao encontrar."""
        r = self._get("lista_alunos.php", params={"idAcomp": id_acomp})
        m_id = re.search(r'name="idAluno"[^>]*value="([^"]*)"', r.text)
        if not m_id or not m_id.group(1):
            return None, None
        m_nome = re.search(r'name="nome" id="nome"[^>]*value="([^"]*)"', r.text)
        nome = html.unescape(m_nome.group(1)) if m_nome else ""
        return m_id.group(1), nome

    def gravar_comentario(self, id_aluno, assunto, tipo, descricao, turma_id=None,
                           valor_contratado="0,00", forma_pagamento="---", turma_nome=None):
        """tipo: '3'=-Comentario, '2'=Urgente, '15'=-Matricula (matricula tambem na turma se turma_id for passado).

        valor_contratado/forma_pagamento: SO importam de verdade quando
        tipo='15' (-Matricula) - confirmado ao vivo em 2026-09-05 que
        'valorContratado' (mesmo sem sufixo, o nome de campo usado aqui) e
        somado de verdade ao total 'Contratado' do aluno (perfil_aluno) a
        cada matricula gravada. Usar '0,00' (padrao) pra matriculas
        administrativas/de controle (ex: Devedor/Pendencia, Aguardando
        Advogado - nao sao curso pago de verdade); passar o valor real
        pra uma matricula de curso de verdade (ver FORMA_PAGAMENTO pros
        valores validos de forma_pagamento).

        CAUSA RAIZ REAL, achada em 2026-09-15 (superou o diagnostico
        anterior de "degradacao de sessao"): rodando
        colocar_interessados_sem_turma.py em lote, so 25-27 de 92
        realmente gravaram a matricula COM VINCULO DE TURMA - o resto
        devolvia HTTP 200 mas nunca aparecia em perfil_aluno nem
        roster_turma. Isolando 1 aluno com sessao 100% fresca (novo login,
        1 unica escrita) o problema REPRODUZIU DE NOVO - descartando a
        teoria de volume/degradacao de sessao como causa principal.
        Comparando byte a byte com o POST que o formulario de verdade
        manda, achei a diferenca: o campo 'buscaTurmaAutoComplete' (o
        texto que o widget de autocomplete da turma preenche na tela,
        nunca preenchido aqui - sempre ia vazio) precisa vir com o NOME da
        turma, nao vazio. Confirmado ao vivo, A/B controlado 2x (aluno
        JANE CLEIDE id 48760: sem o campo falhou, com ele (=assunto, que
        por acaso era o nome da turma) funcionou; aluno IGOR id 47979,
        DECISIVO: assunto proposital MENTIROSO/generico + turma_nome
        correto em buscaTurmaAutoComplete -> vinculou do mesmo jeito,
        provando que e o buscaTurmaAutoComplete que importa pro Fuctura
        RESOLVER a turma, nao o assunto). Por isso 'turma_nome' agora e um
        parametro PROPRIO, separado de 'assunto' - NAO dava pra so
        reaproveitar assunto (achado ao investigar reconciliacao_devedor.
        aplicar_acao: la o assunto e um titulo descritivo tipo "Matrícula
        em turma de controle (reconciliação)", NUNCA o nome real da turma
        de controle - esse call site estava 100% quebrado, matriculacoes
        de Devedor/Pendencia e Aguardando Advogado via "Aplicar Ação"
        nunca vincularam turma nenhuma desde que a funcionalidade existe).
        Fallback pra assunto quando turma_nome nao e passado, por
        seguranca com call sites antigos onde os dois coincidem - mas todo
        call site NOVO deve passar turma_nome explicito. A garantia de
        precondicao (GET antes) e o relogin por volume
        (LIMITE_REQUESTS_POR_SESSAO) continuam, sem atrapalhar."""
        self._get("lista_alunos.php", params={"idAluno": id_aluno})
        busca_turma = (turma_nome or assunto) if turma_id else ""
        data = {
            "assunto": assunto, "tipo": tipo, "valorContratado": valor_contratado,
            "formaPagamento": forma_pagamento, "descricao": descricao,
            "buscaTurmaAutoComplete": busca_turma, "turma": turma_id or "",
            "idAluno": id_aluno, "idAcomp": "", "cadAcompanhamento": "1",
        }
        r = self._post("lista_alunos.php", data)
        return r.status_code

    def editar_comentario(self, id_aluno, id_acomp, assunto, tipo, descricao, turma_id=None, turma_nome=None):
        """O Fuctura NAO tem funcao de excluir comentario/acompanhamento -
        confirmado investigando a interface (server_datatables_aluno.php so
        linka pra edicao via idAcomp, sem nenhum botao/endpoint de exclusao
        em lugar nenhum). O unico jeito de 'desfazer' um comentario e
        EDITAR o registro existente (reenviando o mesmo POST de
        gravar_comentario, mas com idAcomp preenchido em vez de vazio, que
        atualiza a linha ao inves de criar uma nova).

        ACHADO REAL 2026-09-11 (investigando a revisao do flood): o POST de
        edicao devolve 200 e PARECE ter funcionado mesmo quando NAO
        funciona de verdade - se a sessao nunca carregou nenhuma pagina
        lista_alunos.php (nem por idAluno nem por idAcomp) antes deste
        POST, o Fuctura aceita a requisicao, responde 200, mas nao atualiza
        nada - falha silenciosa. Reproduzido isolado: `entrar()` seguido
        direto de editar_comentario() nao gravava; bastava UM GET anterior
        em lista_alunos.php (por idAluno OU por idAcomp, tanto faz) na
        mesma sessao pra passar a funcionar. As telas que ja usam esta
        funcao (Consulta de Alunos) nunca pegaram esse bug porque sempre
        chamam perfil_aluno() (que faz esse GET) antes de deixar editar -
        mas a funcao em si nao devia depender de um GET incidental feito
        em outro lugar do codigo. Por isso agora ela GARANTE a propria
        pre-condicao aqui dentro, sempre - fica segura de chamar de
        qualquer lugar, mesmo isolada.

        turma_nome: mesmo achado real 2026-09-15 de gravar_comentario -
        'buscaTurmaAutoComplete' (aqui tambem sempre ia vazio) precisa do
        NOME da turma pra vincular de verdade quando turma_id e passado;
        sem isso a edicao tambem vira no-op silencioso pro vinculo de
        turma (o resto da edicao - assunto/descricao - grava normalmente,
        so a turma que nao entra). Fallback pra assunto por compatibilidade
        com call sites antigos onde os dois coincidem."""
        self._get("lista_alunos.php", params={"idAluno": id_aluno})
        busca_turma = (turma_nome or assunto) if turma_id else ""
        data = {
            "assunto": assunto, "tipo": tipo, "valorContratado": "0,00",
            "formaPagamento": "---", "descricao": descricao,
            "buscaTurmaAutoComplete": busca_turma, "turma": turma_id or "",
            "idAluno": id_aluno, "idAcomp": id_acomp, "cadAcompanhamento": "1",
        }
        r = self._post("lista_alunos.php", data)
        return r.status_code

    # ---------- cadastro completo (detalhes_alunos.php) ----------
    # Limites reais do banco, descobertos ao vivo em 2026-09-08 (mesmo tipo
    # de achado que LIMITE_ABREVIADA_TURMA/LIMITE_DESCRICAO_TURMA - o
    # Fuctura trunca silenciosamente, sem avisar, sem o HTML anunciar
    # limite nenhum). Testado no aluno de teste (JOAO SILVA(TESTE),
    # 49912): mandou string de 100-160 caracteres em cada campo, leu de
    # volta e mediu o que sobreviveu. Campos nao listados aqui nao tiveram
    # o limite descoberto (nao testados, ou o teste nao truncou - "identidade"
    # aceitou 30 sem truncar, limite real dela e >=30, desconhecido).
    LIMITES_CADASTRO_ALUNO = {
        "nome": 100, "email": 100, "endereco": 100, "bairro": 50, "cidade": 50,
        "complemento": 20, "numero": 10, "cep": 10,
        "nomeResponsavel": 100, "emailResponsavel": 100,
    }

    _CAMPOS_CADASTRO_TEXTO = [
        "nome", "matricula", "email", "fixo", "celular", "cpf", "identidade",
        "dataNascimento", "endereco", "numero", "bairro", "complemento", "cidade", "cep",
        "nomeResponsavel", "telefoneResponsavel", "celularResponsavel",
        "cpfResponsavel", "identidadeResponsavel", "emailResponsavel",
    ]
    _CAMPOS_CADASTRO_SELECT = ["sexo", "estado", "status"]

    def buscar_cadastro_completo(self, id_aluno):
        """Le TODOS os campos do formulario de cadastro (detalhes_alunos.php) -
        usado tanto pra mostrar dados cadastrais (CPF, email, endereco...)
        quanto como base pra reenviar o formulario inteiro ao mudar o status,
        ja que o Fuctura nao aceita salvar um campo isolado - o form inteiro
        e resubmetido, entao precisa dos valores atuais de tudo o mais pra
        nao apagar nada sem querer."""
        r = self._get("detalhes_alunos.php", params={"id": id_aluno})
        c = r.text
        dados = {}
        for campo in self._CAMPOS_CADASTRO_TEXTO:
            m = re.search(rf'name="{campo}"[^>]*value="([^"]*)"', c)
            dados[campo] = html.unescape(m.group(1)) if m else ""
        for campo in self._CAMPOS_CADASTRO_SELECT:
            m = re.search(rf'<select[^>]*name="{campo}"[^>]*>(.*?)</select>', c, re.DOTALL)
            valor = ""
            if m:
                # o Fuctura as vezes marca 'selected' tanto no placeholder
                # quanto na opcao real - o navegador usa a ULTIMA marcada,
                # entao pega a ultima ocorrencia, nao a primeira.
                for opt in re.finditer(r"<option\s+value=['\"]?([^'\">]*)['\"]?[^>]*>", m.group(1)):
                    if "selected" in opt.group(0).lower():
                        valor = opt.group(1)
            dados[campo] = valor
        # achado real investigando o mesmo tipo de bug do parser de busca de
        # aluno (2026-09-08): '[^<]*' exige que NENHUM '<' apareca antes do
        # </textarea> - se o funcionario tiver digitado um '<' dentro da
        # observacao (e o Fuctura nao escapar isso ao devolver o HTML), o
        # regex falha por INTEIRO (nao so trunca) e o campo volta vazio,
        # sem erro nenhum. '.*?' com DOTALL nao se importa com o que tem no
        # meio, so procura o fechamento literal '</textarea>' de verdade.
        m = re.search(r'name="observacoes"[^>]*>(.*?)</textarea>', c, re.DOTALL)
        dados["observacoes"] = html.unescape(m.group(1)) if m else ""
        dados["idAluno"] = id_aluno
        return dados

    def atualizar_cadastro_aluno(self, id_aluno, alteracoes):
        """Atualiza qualquer subconjunto de campos do cadastro (ver
        _CAMPOS_CADASTRO_TEXTO/_CAMPOS_CADASTRO_SELECT) - busca o cadastro
        atual e reenvia com so os campos de 'alteracoes' trocados,
        preservando o resto exatamente como estava. Generaliza
        atualizar_status_aluno pra qualquer campo (usado tambem pra
        completar CPF/endereco/etc. logo apos a criacao rapida).

        Serializado por aluno (ver _lock_para_aluno) - o Fuctura sempre
        reenvia o cadastro inteiro, entao duas chamadas concorrentes pro
        MESMO aluno (dois funcionarios editando quase ao mesmo tempo)
        podem se atropelar e uma apagar a mudanca da outra silenciosamente
        sem a trava."""
        with _lock_para_aluno(id_aluno):
            dados = self.buscar_cadastro_completo(id_aluno)
            dados.update(alteracoes)
            r = self._post("detalhes_alunos.php", dados)
        return r.status_code

    def atualizar_status_aluno(self, id_aluno, novo_status):
        """Muda a Situacao do aluno (campo 'status', ver STATUS_ALUNO)."""
        return self.atualizar_cadastro_aluno(id_aluno, {"status": str(novo_status)})

    def gerar_contrato_aluno(self, id_aluno):
        """PDF do contrato do aluno, gerado na hora pelo proprio Fuctura a
        partir do cadastro atual (nao e um documento assinado guardado -
        confirmado ao vivo: comeca com %PDF, gerado fresco a cada chamada).
        Retorna os bytes do PDF, ou None se a resposta nao for um PDF."""
        r = self._get("impressao_contrato_aluno.php", params={"id": id_aluno})
        return r.content if r.content.startswith(b"%PDF") else None

    def gerar_ficha_aluno(self, id_aluno):
        """PDF da ficha do aluno (mesmo padrao do contrato - gerado na hora)."""
        r = self._get("impressao_ficha_aluno.php", params={"id": id_aluno})
        return r.content if r.content.startswith(b"%PDF") else None

    def obter_turma(self, id_turma):
        """Le os campos atuais da turma (lista_turmas.php?idTurma=X, a mesma
        pagina que 'editarTurma' no proprio painel abre pra editar) - base
        pra atualizar_turma() fazer o mesmo ciclo ler-mesclar-reenviar ja
        usado em atualizar_cadastro_aluno (o Fuctura tambem nao aceita
        update parcial de turma: reenviar so um campo apaga o resto).
        Retorna {'abreviada','entidade','descricao','professor',
        'dataInicio','dataTermino'}, ou None se o idTurma nao existir."""
        r = self._get("lista_turmas.php", params={"idTurma": id_turma})
        dados = {}
        for campo in ("abreviada", "descricao", "professor", "dataInicio", "dataTermino"):
            m = re.search(rf"name=['\"]{campo}['\"][^>]*value=['\"]([^'\"]*)['\"]", r.text)
            dados[campo] = html.unescape(m.group(1)) if m else ""
        m = re.search(r"<select[^>]*name=['\"]entidade['\"](.*?)</select>", r.text, re.DOTALL)
        entidade = ""
        if m:
            for opt in re.finditer(r"<option\s+value=['\"]?([^'\">]*)['\"]?[^>]*>", m.group(1)):
                if "selected" in opt.group(0).lower():
                    entidade = opt.group(1)
        dados["entidade"] = entidade
        # sem idTurma real, o form devolve tudo em branco (mesmo mecanismo
        # de novaTurma()) - usamos a descricao vazia como sinal de "nao existe"
        if not dados["descricao"] and not dados["abreviada"]:
            return None
        return dados

    def _descricao_turma(self, id_turma):
        """Nome de exibicao da turma (campo 'descricao') - e o mesmo texto
        que precisa ir no campo visivel 'buscaTurmaAutoCompleteRelatorio'
        dos relatorios de Relatorios > Verificacao de Turma / Pagamentos.
        Achado real 2026-09-09: sem esse campo preenchido, os dois
        relatorios devolvem 'sem registros' mesmo com o id certo no campo
        escondido 'turma' - foi exatamente isso que fez uma investigacao
        anterior (2026-09-08) concluir, erroneamente, que estavam quebrados."""
        turma = self.obter_turma(id_turma)
        return turma["descricao"] if turma else ""

    def atualizar_turma(self, id_turma, alteracoes):
        """Atualiza qualquer subconjunto de campos de uma turma existente
        (abreviada/entidade/descricao/professor/dataInicio/dataTermino) -
        mesmo padrao ler-mesclar-reenviar de atualizar_cadastro_aluno,
        porque o form 'formCadastro2' de lista_turmas.php reenvia TUDO de
        uma vez (o mesmo endpoint de criar_turma - a unica diferenca entre
        criar e editar e o campo 'idTurma' vir vazio ou preenchido).

        CUIDADO: mesma truncagem silenciosa de criar_turma
        (LIMITE_ABREVIADA_TURMA/LIMITE_DESCRICAO_TURMA) - valide antes de
        chamar. Levanta ValueError se a turma nao existir."""
        atual = self.obter_turma(id_turma)
        if atual is None:
            raise ValueError(f"Turma {id_turma} não encontrada.")
        atual.update(alteracoes)
        atual["idTurma"] = str(id_turma)
        self._post("lista_turmas.php", atual)
        return True

    def _gerar_pdf_relatorio_por_turma(self, tp, endpoint, id_turma):
        """POST compartilhado por Verificacao de Turma e Pagamentos - os
        unicos dois relatorios reais do menu Relatorios que sao filtrados
        por turma e devolvem PDF de verdade (os outros, 'Sem Turma' e
        'Ficha do Aluno' em lote, nao funcionam no proprio Fuctura -
        documentado em STATUS.md). Retorna os bytes do PDF, ou None se a
        turma nao tiver alunos vinculados (o painel devolve um alerta em
        HTML nesse caso, igual a Ata de Chamada)."""
        data = {
            "tp": tp, "turma": id_turma, "situacao": "", "nome": "",
            "ordem": "t.descricao ASC, al.nome ASC, ac.data ASC, en.nome_entidade ASC",
            "id_aluno": "", "buscaTurmaAutoCompleteRelatorio": self._descricao_turma(id_turma),
        }
        r = self._post(endpoint, data)
        return r.content if r.content.startswith(b"%PDF") else None

    def gerar_verificacao_turma_pdf(self, id_turma):
        """PDF oficial de 'Verificacao de Turma' (Relatorios > Verificacao
        de Turma) - roster da turma com etiqueta de situacao por aluno
        (ADVOGADO/ONLINE/PRESENCIAL/EX-ALUNO etc)."""
        return self._gerar_pdf_relatorio_por_turma(
            "verificacaoTurma", "impressao_verificacao_turma.php", id_turma,
        )

    def gerar_pagamentos_turma_pdf(self, id_turma):
        """PDF oficial de 'Pagamentos' (Relatorios > Pagamentos) - roster da
        turma com Valor Contratado e Valor Pago por aluno."""
        return self._gerar_pdf_relatorio_por_turma(
            "pagamentos", "impressao_pagamentos.php", id_turma,
        )

    # gerar_certificado_aluno NAO existe de proposito: confirmado ao vivo em
    # 2026-09-05 que certificado.php esta QUEBRADO no proprio servidor do
    # Fuctura (FPDF error: "Can't open image file: images/certificado.png" -
    # falta um arquivo de imagem la, reproduzido com os 3 tipos validos
    # java/linux/php e dados corretos) - nao e um problema do nosso lado,
    # nao da pra mascarar uma funcionalidade que nao funciona no sistema de
    # origem. Reportar pro suporte do Fuctura.

    def criar_aluno(self, nome, email="", fixo="", celular="", status=""):
        """Cria um aluno NOVO de verdade no Fuctura. NAO e o mesmo form/
        endpoint usado pra editar um aluno existente (detalhes_alunos.php) -
        esse so reexibe o form em branco sem salvar nada quando idAluno
        vazio (confirmado por tentativa real). O cadastro de verdade e um
        form REDUZIDO (so nome/email/fixo/celular/situacao) que vive dentro
        de lista_alunos.php (form 'formCadastro1'), com dois detalhes
        essenciais: (1) tem um campo oculto 'cadAluno'='1' que marca a
        intencao de criar, e (2) o campo 'matricula' vem PRE-GERADO pelo
        servidor a cada carregamento da pagina (nao fica vazio) - por isso
        aqui sempre faz um GET fresco em lista_alunos.php pra pegar a
        matricula reservada NA HORA, imediatamente antes do POST.

        CPF/endereco/data de nascimento etc. nao existem nesse form reduzido -
        pra completar o cadastro, edite depois via atualizar_status_aluno()/
        buscar_cadastro_completo() (que usam detalhes_alunos.php, o form de
        EDICAO - esse sim funciona pra quem ja tem id_aluno).

        Retorna o id_aluno recem-criado (string), ou None se a resposta nao
        trouxer um id_aluno preenchido (indicando que a criacao falhou)."""
        r_form = self._get("lista_alunos.php")
        m = re.search(r'name="matricula"[^>]*value="([^"]*)"', r_form.text)
        matricula = m.group(1) if m else ""

        data = {
            "nome": nome, "matricula": matricula, "email": email, "fixo": fixo,
            "celular": celular, "status": status,
            "idAluno": "", "idAcomp": "", "cadAluno": "1",
        }
        r = self._post("lista_alunos.php", data)
        m2 = re.search(r'name="idAluno"[^>]*value="([^"]*)"', r.text)
        return m2.group(1) if m2 and m2.group(1) else None

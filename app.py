"""
Sistema-mascara Fuctura - ponto de entrada.

Roda um servidor web local. Cada funcionario loga com o PROPRIO login/senha
do Fuctura (nunca uma credencial compartilhada). Depois do login ve um painel
com um processo disponivel por enquanto: Fechamento de Turma.

    python app.py

Abre http://localhost:8766 automaticamente. Ctrl+C para parar.
"""
import json
import os
import re
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import academia_progresso
import anexos_email
import config_store
import cora_client
import drive_client
import email_cobranca
import extrato_bancario
import fotos_aluno
import gerador_certificado
import gmail_client
import manutencao
import fechamento_logic as logic
import pdf_utils
import reconciliacao_devedor as reconciliacao
import relatorios_pdf
import vision_providers as vision
from fuctura_client import FucturaClient, FucturaAuthError, TURMA_DEVEDOR_ID, TURMA_ADVOGADO_ID, STATUS_ALUNO, TIPO_COMENTARIO, FORMA_PAGAMENTO

PORT = 8766
# Achado ao implantar na VPS (2026-09-09): abrir o navegador automaticamente
# so faz sentido rodando numa maquina de mesa da Fuctura, com tela na frente.
# Num servidor (systemd, sem sessao grafica), webbrowser.open() tenta varios
# navegadores via subprocess e pode falhar/travar - a variavel de ambiente
# FUCTURA_MASCARA_ABRIR_NAVEGADOR (setada como "0" no service da VPS)
# desliga isso, sem mudar o comportamento padrao pra quem roda localmente.
ABRIR_NAVEGADOR_AUTOMATICO = os.environ.get("FUCTURA_MASCARA_ABRIR_NAVEGADOR", "1") != "0"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(OUT_DIR, "atas_recebidas")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Tipos de comentario oferecidos no formulario manual de "Novo comentário"
# (tela Consulta de Alunos) - so os que sao seguros pra um funcionario
# escrever uma anotacao livre. Ficam de fora -Matricula (mexe em turma de
# verdade, ja tem fluxo proprio) e -Pagamento Realizado/Abatimento-Devolução
# (mexem no financeiro que a reconciliação usa como fonte de verdade -
# criar isso por engano corromperia contratado/recebido de um aluno real).
TIPOS_COMENTARIO_MANUAL = ["3", "1", "2", "14"]  # -Comentário, Importante, Urgente, Aguardando Turma


def _texto(valor):
    """Converte com seguranca qualquer valor vindo de um body JSON pra uma
    string stripada, mesmo quando o cliente manda um tipo inesperado
    (numero, lista, dict, None) - achado real via teste de estresse
    (testes_estresse/stress_test.py, cenario de input adversarial,
    2026-09-05): o padrao antigo '(body.get(x) or "").strip()' quebrava
    com AttributeError quando o valor era, por exemplo, um int
    ({"tipo": 123}), derrubando a thread da requisicao com uma excecao
    crua e SEM RESPOSTA NENHUMA pro cliente (conexao so caia) - violava a
    garantia que o proprio projeto ja tinha (nunca deixar a conexao cair
    sem resposta, ver docstring de _iniciar_fechamento). Usado em todo
    handler que le campos de um body JSON."""
    if valor is None:
        return ""
    if isinstance(valor, str):
        return valor.strip()
    return str(valor).strip()


def _cpf_normalizado(valor):
    """Extrai so os digitos e confere que sao exatamente 11 - CPF
    brasileiro sempre tem 11 digitos. Achado real (2026-09-08, caso VITOR
    FONSECA VELOSO): o campo cpfResponsavel dele tinha "99742511" (8
    digitos) - claramente um telefone digitado no campo errado, sem
    nenhuma validacao pra pegar isso. So confere a quantidade de digitos
    (trava contra erro de digitacao grosseiro), NAO calcula o digito
    verificador oficial do CPF - isso seria uma validacao mais forte, mas
    nao foi o que foi pedido.

    Retorna (valor_normalizado, None) se vazio ou valido, (None, mensagem)
    se invalido. Campo vazio nao e invalido aqui - obrigatoriedade e
    decidida por quem chama."""
    valor = (valor or "").strip()
    if not valor:
        return "", None
    digitos = re.sub(r"\D", "", valor)
    if len(digitos) != 11:
        return None, f"precisa ter 11 dígitos numéricos (recebido {len(digitos)})."
    return digitos, None


def _idade(data_nascimento_str, hoje=None):
    """Idade em anos completos a partir de uma data DD/MM/AAAA. Retorna
    None se a data estiver vazia ou nao reconhecida - nesse caso nao da
    pra confirmar maioridade/menoridade."""
    try:
        nascimento = datetime.strptime((data_nascimento_str or "").strip(), "%d/%m/%Y")
    except ValueError:
        return None
    hoje = hoje or datetime.now()
    return hoje.year - nascimento.year - ((hoje.month, hoje.day) < (nascimento.month, nascimento.day))


def _validar_cpf_e_responsavel(dados):
    """Valida CPF do aluno e do responsavel (quando preenchidos) e confere
    se o responsavel e obrigatorio nesse caso - pedido explicito do
    usuario (2026-09-08). 'Responsavel' aqui cobre tanto responsavel
    LEGAL (aluno menor de idade) quanto um adulto cujo terceiro paga
    (pai, tio, amigo) - o Fuctura nao distingue os dois casos, e so um
    campo generico. So fica OBRIGATORIO quando da pra confirmar que o
    aluno e menor de idade pela dataNascimento - sem data reconhecida,
    nao da pra confirmar, entao fica opcional (nao forca).

    Espera um dict com pelo menos as chaves 'cpf', 'cpfResponsavel',
    'dataNascimento', 'nomeResponsavel' (string vazia = nao preenchido).
    Normaliza 'cpf'/'cpfResponsavel' pro formato so-digitos DENTRO do
    proprio dict (efeito colateral intencional). Retorna uma mensagem de
    erro (string) ou None se estiver tudo certo."""
    cpf_norm, erro = _cpf_normalizado(dados.get("cpf"))
    if erro:
        return f"CPF do aluno {erro}"
    dados["cpf"] = cpf_norm

    cpf_resp_norm, erro = _cpf_normalizado(dados.get("cpfResponsavel"))
    if erro:
        return f"CPF do responsável {erro}"
    dados["cpfResponsavel"] = cpf_resp_norm

    idade = _idade(dados.get("dataNascimento"))
    if idade is not None and idade < 18:
        if not dados.get("nomeResponsavel") or not dados.get("cpfResponsavel"):
            return "Aluno menor de idade (pela data de nascimento informada) - nome e CPF do responsável são obrigatórios."
    return None


def _validar_limites_cadastro(dados):
    """Confere os limites reais de caractere descobertos ao vivo em
    2026-09-08 (ver FucturaClient.LIMITES_CADASTRO_ALUNO) - o Fuctura
    trunca esses campos SILENCIOSAMENTE, sem o HTML anunciar limite
    nenhum (mesmo padrão já visto com abreviada/descrição de turma).
    Sem essa checagem aqui, o funcionário digitaria um endereço/nome
    completo e o Fuctura salvaria uma versão cortada sem avisar. Retorna
    mensagem de erro (string) ou None."""
    for campo, limite in FucturaClient.LIMITES_CADASTRO_ALUNO.items():
        valor = dados.get(campo) or ""
        if len(valor) > limite:
            return f"Campo '{campo}' tem {len(valor)} caracteres, mas o Fuctura só aceita até {limite} (ele trunca sem avisar - encurte o texto)."
    return None

# ---------------------------------------------------------------------------
# Estado em memoria (processo unico local - ok pra escala de uma escola)
# ---------------------------------------------------------------------------
sessions = {}          # session_id -> {"login":, "client":, "is_admin":}
fechamento_jobs = {}    # job_id -> dados da extracao em andamento
reconciliacao_jobs = {}  # job_id -> {"acoes_pendentes": {id_aluno: analise}}
gmail_estados_pendentes = {}  # state (CSRF do OAuth) -> session_id, ver /api/gmail/autorizar
drive_estados_pendentes = {}  # idem, pra /api/drive/autorizar

# Achado real via teste de estresse (2026-09-06), corrigido em 2026-09-08:
# nenhum dos 3 dicionarios acima tinha expiracao - so saiam de la com
# logout explicito (sessions) ou nunca (os dois jobs). Numa aba fechada
# sem logout, ou numa analise que ninguem voltou pra confirmar, o dado
# ficava pra sempre na memoria do processo, crescendo sem limite enquanto
# o servidor roda (200 logins de teste = +200 entradas permanentes).
# TTLs escolhidos: sessao dura um dia de trabalho (12h) sem precisar logar
# de novo; jobs de fechamento/reconciliacao sao de uso pontual (comeca,
# confirma item por item, termina numa sentada so), 4h e generoso pra isso
# e ainda limpa lixo acumulado rapido.
SESSAO_TTL_SEGUNDOS = 12 * 60 * 60
JOB_TTL_SEGUNDOS = 4 * 60 * 60


def novo_id():
    return secrets.token_hex(16)


def _limpar_expirados(dicionario, ttl_segundos, campo_data="criado_em"):
    """Remove do dicionario, em memoria, qualquer entrada mais velha que
    ttl_segundos - chamado de forma preguicosa (nao tem thread de limpeza
    separada, roda um pouco a cada request/criacao de job, o que basta
    pra um numero pequeno de sessoes/jobs simultaneos). campo_data pode
    ser um datetime.isoformat() (jobs) ou um float de time.time() (sessions)."""
    agora = datetime.now()
    expirados = []
    for chave, valor in dicionario.items():
        data_criacao = valor.get(campo_data)
        if data_criacao is None:
            continue
        if isinstance(data_criacao, str):
            try:
                data_criacao = datetime.fromisoformat(data_criacao)
            except ValueError:
                continue
        else:
            data_criacao = datetime.fromtimestamp(data_criacao)
        if (agora - data_criacao).total_seconds() > ttl_segundos:
            expirados.append(chave)
    for chave in expirados:
        del dicionario[chave]


class ErroProcessamento(Exception):
    """Erro conhecido durante o processamento de uma ata - mensagem ja
    pronta pra mostrar pro usuario (tanto na resposta HTTP quanto num script
    de teste que chame processar_ata_fechamento diretamente)."""
    pass


def enriquecer_nao_casados(client, nao_casados, roster_oficial=None):
    """Pra cada nome da ata que nao bateu no roster ATIVO da turma, tenta
    achar o aluno em fontes mais amplas antes de so descartar como 'nao
    encontrado' - evita que um aluno que cancelou, foi pro juridico, ou
    saiu do roster ativo simplesmente suma do relatorio sem explicacao.

    Ordem de busca: (1) Ata de Chamada OFICIAL da turma (mais confiavel -
    lista TODO aluno vinculado, ativo ou nao, com situacao/celular direto
    do cadastro, mas sem id_aluno pra gravar); (2) turma de controle
    Devedor; (3) turma de controle Advogado; (4) lista viva de Devedores
    do painel (essas tres ultimas tem id_aluno, entao ainda da pra gravar
    comentario se o aluno for confirmado por ali)."""
    if not nao_casados:
        return []
    try:
        candidatos_devedor = client.roster_turma(TURMA_DEVEDOR_ID)
    except Exception:
        candidatos_devedor = []
    try:
        candidatos_advogado = client.roster_turma(TURMA_ADVOGADO_ID)
    except Exception:
        candidatos_advogado = []
    try:
        candidatos_geral = client.listar_devedores_ao_vivo()
    except Exception:
        candidatos_geral = []

    resultado = []
    for nome in nao_casados:
        match_oficial, _score = logic.casar_nome(nome, roster_oficial or [])
        if match_oficial:
            resultado.append({"nome": nome, "contexto": logic.contexto_ata_oficial(match_oficial["situacao"])})
            continue

        achado, origem = None, None
        for candidatos, origem_nome in (
            (candidatos_devedor, "devedor"), (candidatos_advogado, "advogado"),
            (candidatos_geral, "lista_devedores_geral"),
        ):
            match, _score = logic.casar_nome(nome, candidatos)
            if match:
                achado, origem = match, origem_nome
                break
        if not achado:
            resultado.append({"nome": nome, "contexto": None})
            continue
        try:
            perfil = client.perfil_aluno(achado["id_aluno"])
            comentarios = client.comentarios_aluno(achado["id_aluno"])
            contexto = logic.contexto_aluno_fora_da_turma(perfil, comentarios, origem)
        except Exception as e:
            contexto = f"Encontrado em busca ampla ({origem}), mas falhou ao buscar detalhes: {e}"
        resultado.append({"nome": nome, "contexto": contexto})
    return resultado


def processar_ata_fechamento(client, turma_id, turma_nome, modo, caminhos_arquivos, pasta_paginas):
    """Pipeline completo de leitura de ata: divide PDFs em paginas, chama a
    IA de visao por unidade (pagina/imagem), mescla os resultados, casa com
    o roster da turma (ativo + oficial), e monta o relatorio - tudo LEITURA,
    nada e gravado no Fuctura aqui. Reaproveitado tanto pela rota HTTP
    (_iniciar_fechamento_impl) quanto por scripts de teste locais, pra nao
    ter duas implementacoes divergentes do mesmo pipeline.

    Levanta ErroProcessamento com mensagem pronta pro usuario em caso de
    falha irrecuperavel (ex: sem provedor de IA configurado)."""
    # Um PDF e dividido em uma imagem por pagina AQUI, antes de qualquer
    # chamada de IA. Mas as paginas do MESMO PDF vao juntas na MESMA chamada
    # (um grupo) - e a mesma folha de ata continuando, entao a IA precisa
    # ver as duas pra manter a contagem de colunas consistente entre elas
    # (achado real 2026-09-10: pagina 1 saia com 4 colunas e pagina 2 com 6,
    # porque eram lidas separadas). Arquivos DIFERENTES (ex: fotos de atas
    # de sessoes distintas) continuam em grupos separados, uma chamada cada.
    grupos_paginas = []  # cada elemento: lista de caminhos de imagem de UM arquivo
    try:
        for caminho in caminhos_arquivos:
            if os.path.splitext(caminho)[1].lower() == ".pdf":
                grupos_paginas.append(pdf_utils.dividir_pdf_em_imagens(caminho, pasta_paginas))
            else:
                grupos_paginas.append([caminho])
    except Exception as e:
        raise ErroProcessamento(f"Falha ao processar PDF da ata: {e}")

    cfg = config_store.carregar()
    provider = cfg.get("provider_ativo")
    if not provider:
        raise ErroProcessamento("Nenhum provedor de IA configurado. Peça a um administrador para configurar em /admin.")
    provider_cfg = cfg["providers"][provider]

    try:
        roster = client.roster_turma(turma_id)
    except Exception as e:
        raise ErroProcessamento(f"Falha ao buscar roster da turma: {e}")

    # A "Ata de Chamada" oficial do proprio painel (Relatorios > Ata de
    # Chamada) e mais completa que roster_turma() - traz TODO aluno
    # vinculado a turma (mesmo cancelado/devedor/com advogado), com celular
    # e a situacao/observacao atual, direto do cadastro, sem depender de
    # OCR. roster_turma() continua sendo a fonte pro id_aluno (necessario
    # pra gravar comentario); esta aqui e usada como contexto extra pra
    # quem nao aparecer no roster ativo.
    try:
        roster_oficial_bruto = client.gerar_ata_chamada_oficial(turma_id)
        roster_oficial = [
            {"id_aluno": None, "nome": a["nome"], "telefone": a["celular"], "situacao": a["situacao"]}
            for a in roster_oficial_bruto["alunos"]
        ]
    except Exception:
        roster_oficial = []

    # Uma ata pode vir espalhada em mais de um arquivo (mais de uma folha,
    # ou mais de um documento enviado juntos). Manda cada arquivo pra IA
    # SEPARADAMENTE (em vez de tudo numa chamada so) e mescla os resultados
    # aqui no codigo - isso evita o modelo tratar um arquivo como
    # "referencia principal" e subaproveitar os outros, que e o que
    # acontecia quando todos eram enviados juntos numa unica chamada.
    turma_identificada = ""
    professor = ""
    datas_de_aula = []
    duvidas = []
    confianca_geral = None
    alunos_por_id = {}  # id_aluno -> {id_aluno, nome_fuctura, nome_ata, score_match, presencas_por_data}
    nao_casados = set()
    colunas_por_grupo = []  # quantas colunas de aula cada arquivo reportou (ver PROMPT_EXTRACAO)

    for grupo in grupos_paginas:
        nome_arquivo = os.path.basename(grupo[0]) if grupo else "?"
        # Achado real 2026-09-10: uma queda de conexao transitoria com o
        # provedor ("Connection aborted / RemoteDisconnected") derrubava o
        # arquivo inteiro sem retentar - o resto do fechamento saia baseado
        # so nos outros arquivos. Agora tenta ate 3 vezes antes de desistir.
        extraido = None
        ultimo_erro = None
        for tentativa in range(3):
            try:
                texto, tok_in, tok_out = vision.extrair_ata(
                    provider, provider_cfg["modelo"], provider_cfg["api_key"], grupo, logic.PROMPT_EXTRACAO
                )
                config_store.registrar_uso(provider, vision.estimar_custo_usd(provider, tok_in, tok_out))
                extraido = logic.parse_json_resposta(texto)
                break
            except Exception as e:
                ultimo_erro = e
                if tentativa < 2:
                    time.sleep(1.5 * (tentativa + 1))
        if extraido is None:
            # um arquivo que falhou nas 3 tentativas nao pode derrubar o job
            # inteiro e perder o que ja foi lido dos outros - registra como
            # duvida e segue.
            duvidas.append(f"[{nome_arquivo}] Falha ao ler este arquivo após 3 tentativas, foi ignorado: {ultimo_erro}")
            continue

        if not turma_identificada:
            turma_identificada = extraido.get("turma_identificada", "")
        if not professor:
            professor = extraido.get("professor", "")
        duvidas.extend(f"[{nome_arquivo}] {d}" for d in extraido.get("duvidas", []))
        confianca_geral = logic.pior_confianca(confianca_geral, extraido.get("confianca_geral"))

        # nº de colunas de aula reportado por esta pagina - usado depois pra
        # avisar se as paginas da MESMA ata discordam (a folha e a mesma, as
        # colunas deviam ser as mesmas em todas).
        try:
            n_col = int(extraido.get("colunas_de_marcacao") or 0)
        except (TypeError, ValueError):
            n_col = 0
        if n_col <= 0:  # provedor antigo/sem o campo - infere pelo aluno com mais marcas
            n_col = max((len(a.get("presencas_nesta_ata", [])) for a in extraido.get("alunos", [])), default=0)
        if n_col:
            colunas_por_grupo.append(n_col)

        # a IA as vezes devolve data sem ano - descobre o ano certo a partir
        # das datas completas que ela mesma relatou NESTE arquivo.
        ano_ata = None
        for d_str in extraido.get("datas_de_aula_nesta_ata", []):
            m = re.match(r"^\d{2}/\d{2}/(\d{4})$", (d_str or "").strip())
            if m:
                ano_ata = int(m.group(1))
                break

        # normaliza ANTES de deduplicar - senao a mesma aula relatada em
        # formatos diferentes (com/sem ano, ano com 2 digitos...) conta como
        # duas datas distintas e infla "quantidade de aulas".
        for d in extraido.get("datas_de_aula_nesta_ata", []):
            d_norm = logic.normalizar_data(d, ano_ata)
            if d_norm not in datas_de_aula:
                datas_de_aula.append(d_norm)

        for aluno_ata in extraido.get("alunos", []):
            # celular vem impresso pelo sistema (nao manuscrito) - bate
            # antes do nome, que pode ter OCR ruim ou sobrenome comum.
            match = logic.casar_por_telefone(aluno_ata.get("celular"), roster)
            score = 1.0 if match else 0.0
            if not match:
                match, score = logic.casar_nome(aluno_ata["nome"], roster)
            if not match:
                nao_casados.add(aluno_ata["nome"])
                continue
            entry = alunos_por_id.setdefault(match["id_aluno"], {
                "id_aluno": match["id_aluno"], "nome_fuctura": match["nome"],
                "nome_ata": aluno_ata["nome"], "score_match": round(score, 2),
                "presencas_por_data": {}, "presencas_sem_data": [],
            })
            for p in aluno_ata.get("presencas_nesta_ata", []):
                data_norm = logic.normalizar_data(p.get("data"), ano_ata)
                marcacao = p.get("marcacao")
                if logic.data_valida(data_norm):
                    # data real - mescla por data (mesma aula relatada em
                    # mais de uma pagina/arquivo vira uma entrada so).
                    atual = entry["presencas_por_data"].get(data_norm)
                    entry["presencas_por_data"][data_norm] = logic.mesclar_marcacao(atual, marcacao) if atual else marcacao
                else:
                    # data desconhecida/placeholder ('sem_data', 'coluna_1'...) -
                    # NUNCA mescla por essa chave: duas colunas reais e
                    # distintas podem virar o mesmo texto placeholder, e
                    # mesclar apagaria uma marcacao real.
                    entry["presencas_sem_data"].append(marcacao)

    # se mais de um arquivo de ata foi enviado e eles discordam no numero
    # de colunas de aula, avisa - a contagem por aluno pode ficar
    # inconsistente entre os alunos de cada arquivo.
    if len(set(colunas_por_grupo)) > 1:
        duvidas.append(
            "Os arquivos de ata enviados leram números diferentes de colunas de aula "
            f"({', '.join(str(n) for n in colunas_por_grupo)}) — confira na ata quantas aulas "
            "a turma teve; a contagem por aluno pode ficar inconsistente."
        )

    nao_casados_enriquecidos = enriquecer_nao_casados(client, sorted(nao_casados), roster_oficial)
    alunos_processados = [
        {
            "id_aluno": a["id_aluno"], "nome_fuctura": a["nome_fuctura"],
            "nome_ata": a["nome_ata"], "score_match": a["score_match"],
            "presencas": (
                [{"data": d, "marcacao": m} for d, m in a["presencas_por_data"].items()]
                + [{"data": f"data_nao_identificada_{i+1}", "marcacao": m} for i, m in enumerate(a["presencas_sem_data"])]
            ),
        }
        for a in alunos_por_id.values()
    ]
    extraido = {
        "turma_identificada": turma_identificada, "professor": professor,
        "datas_de_aula_nesta_ata": datas_de_aula, "confianca_geral": confianca_geral,
        "duvidas": duvidas,
    }

    relatorio = logic.montar_relatorio_turma(
        extraido.get("turma_identificada", turma_nome), extraido.get("professor", ""), alunos_processados,
        rotulo="Acompanhamento" if modo == "acompanhamento" else "Fechamento",
        infantil=logic.eh_turma_infantil(turma_nome),
    )

    return {
        "extraido": extraido,
        "alunos": alunos_processados,
        "nao_casados": nao_casados_enriquecidos,
        "relatorio_turma": relatorio,
        # modalidade (ONLINE/PRESENCIAL/...) por aluno, do roster - usada no
        # Acompanhamento pra averiguar se o aluno e online (a ata de sala
        # nao confirma presenca online).
        "roster_modalidade": {a["id_aluno"]: (a.get("observacao") or "").strip() for a in roster},
    }


# ---------------------------------------------------------------------------
# Parser minimo de multipart/form-data (o modulo cgi foi removido no Python 3.13)
# ---------------------------------------------------------------------------
def parse_multipart(body, boundary):
    partes = []
    marcador = b"--" + boundary.encode()
    blocos = body.split(marcador)
    for bloco in blocos:
        bloco = bloco.strip(b"\r\n")
        if not bloco or bloco == b"--":
            continue
        if b"\r\n\r\n" not in bloco:
            continue
        cabecalho_raw, conteudo = bloco.split(b"\r\n\r\n", 1)
        conteudo = conteudo.rstrip(b"\r\n")
        cabecalhos = {}
        for linha in cabecalho_raw.decode("utf-8", "replace").split("\r\n"):
            if ":" in linha:
                k, v = linha.split(":", 1)
                cabecalhos[k.strip().lower()] = v.strip()
        disposicao = cabecalhos.get("content-disposition", "")
        m_name = re.search(r'name="([^"]*)"', disposicao)
        m_file = re.search(r'filename="([^"]*)"', disposicao)
        partes.append({
            "name": m_name.group(1) if m_name else None,
            "filename": m_file.group(1) if m_file else None,
            "content": conteudo,
        })
    return partes


# ---------------------------------------------------------------------------
# Design system compartilhado por todas as paginas - tokens (cor/tipografia/
# espacamento) + componentes reusaveis (card, botao, badge, tabela, topbar,
# grade de autocomplete). Cada pagina concatena BASE_CSS com suas poucas
# regras especificas, em vez de reescrever tudo do zero.
# ---------------------------------------------------------------------------
BASE_CSS = """
:root {
  --bg: #f5f3ee; --surface: #ffffff; --surface-alt: #faf9f6;
  --text: #1c2620; --text-muted: #6b7269; --border: #e3e0d6;
  --accent: #2a5c8c; --accent-dark: #1d4568;
  --success: #2f7d52; --success-bg: #eef7f1;
  --danger: #a23b2e; --danger-bg: #fdf1ef;
  --warning: #9a6b0a; --warning-bg: #fff8e6;
  --radius: 10px; --radius-sm: 6px;
  --shadow: 0 1px 3px rgba(30,38,32,.08), 0 1px 2px rgba(30,38,32,.04);
  --shadow-md: 0 6px 16px rgba(30,38,32,.10);
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: var(--bg); color: var(--text); margin: 0; -webkit-font-smoothing: antialiased;
}
.page { max-width: 960px; margin: 0 auto; padding: 28px 20px 60px; }
.page.narrow { max-width: 460px; }

.topbar { display:flex; align-items:center; justify-content:space-between; padding:14px 24px; background:var(--surface); border-bottom:1px solid var(--border); flex-wrap:wrap; gap:8px; }
.topbar .brand { font-weight:700; font-size:1.05rem; color:var(--text); text-decoration:none; }
.topbar .right { display:flex; align-items:center; gap:16px; font-size:0.85rem; color:var(--text-muted); }
.topbar .right a { color:var(--accent); text-decoration:none; }
.topbar .right a.sair { color:var(--danger); }
.topbar .right a:hover { text-decoration:underline; }

a.voltar { display:inline-flex; align-items:center; gap:4px; font-size:0.85rem; color:var(--accent); text-decoration:none; margin-bottom:16px; }
a.voltar:hover { text-decoration:underline; }

h1 { font-size:1.4rem; margin:0 0 4px; letter-spacing:-0.01em; }
h2 { font-size:1.1rem; margin:0 0 12px; }
h3 { font-size:0.92rem; margin:0 0 10px; }
p.subtitulo { color:var(--text-muted); font-size:0.88rem; margin:0 0 20px; max-width:640px; line-height:1.5; }

.card { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:20px; margin-bottom:16px; box-shadow:var(--shadow); }

label { display:block; font-size:0.76rem; font-weight:600; color:var(--text-muted); margin-bottom:6px; text-transform:uppercase; letter-spacing:0.03em; }
input[type=text], input[type=number], input[type=password], input[type=file], select, textarea {
  font-family:inherit; font-size:0.92rem; padding:9px 12px; border:1px solid var(--border); border-radius:var(--radius-sm);
  background:var(--surface); color:var(--text); width:100%;
}
input:focus, select:focus, textarea:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px rgba(42,92,140,.15); }
::placeholder { color:#a3a89c; }

button { font-family:inherit; cursor:pointer; border:none; border-radius:var(--radius-sm); font-size:0.86rem; font-weight:600; padding:10px 18px; transition:transform .05s ease, opacity .15s ease, background .15s ease; }
button:active { transform:scale(0.98); }
button.acao, button.btn-primary { background:var(--accent); color:#fff; }
button.acao:hover, button.btn-primary:hover { background:var(--accent-dark); }
button.btn-sim { background:var(--success); color:#fff; }
button.btn-sim:hover { opacity:0.88; }
button.btn-nao { background:var(--danger); color:#fff; }
button.btn-nao:hover { opacity:0.88; }

.badge { display:inline-block; font-size:0.7rem; font-weight:700; text-transform:uppercase; letter-spacing:0.03em; padding:3px 9px; border-radius:999px; }
.badge-urgente { background:var(--danger-bg); color:var(--danger); }
.badge-ok { background:var(--success-bg); color:var(--success); }

.aviso { background:var(--warning-bg); border:1px solid #e0c260; border-radius:var(--radius-sm); padding:10px 14px; font-size:0.85rem; margin-bottom:10px; }

table { width:100%; border-collapse:collapse; font-size:0.88rem; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); vertical-align:top; }
th { font-size:0.7rem; text-transform:uppercase; letter-spacing:0.03em; color:var(--text-muted); font-weight:600; }

.aluno-card { border-left:3px solid var(--border); }
.aluno-card.ok { border-left-color:var(--success); background:var(--success-bg); }
.aluno-card.feito { border-left-color:var(--success); background:var(--success-bg); opacity:0.75; }

.duvida { background:var(--warning-bg); border:1px solid #e0c260; padding:8px 12px; border-radius:var(--radius-sm); font-size:0.85rem; margin:6px 0; }
.sugestao { color:var(--danger); font-weight:600; }
.fonte { color:var(--text-muted); font-size:0.78rem; }

.autocomplete-box { position:relative; }
.autocomplete-list { position:absolute; top:100%; left:0; right:0; background:var(--surface); border:1px solid var(--border); border-top:none; border-radius:0 0 var(--radius-sm) var(--radius-sm); box-shadow:var(--shadow-md); z-index:20; max-height:280px; overflow-y:auto; }
.autocomplete-list .item { padding:9px 12px; cursor:pointer; font-size:0.88rem; border-bottom:1px solid var(--border); }
.autocomplete-list .item:last-child { border-bottom:none; }
.autocomplete-list .item:hover { background:var(--surface-alt); }

.comentario { border-left:3px solid var(--border); padding:8px 12px; margin-bottom:8px; background:var(--surface-alt); border-radius:var(--radius-sm); }
.comentario .meta { font-size:0.75rem; color:var(--text-muted); }

button[data-bloqueado="1"] { opacity:0.55; cursor:not-allowed; pointer-events:none; }
"""

# Trava contra clique duplicado (pedido explicito do usuario em 2026-09-05,
# depois do "flood" de comentarios em lote - ver memoria
# fuctura_mascara_confirmacao_humana_obrigatoria): um clique duplo/repetido
# num botao de acao (Salvar, Matricular, Cadastrar, Aplicar...) poderia
# gravar a mesma coisa duas vezes no Fuctura antes da 1a resposta voltar.
# Em vez de mexer em CADA funcao de cada tela (haveriam dezenas), um unico
# listener de clique em fase de CAPTURA no document intercepta qualquer
# <button> antes do proprio onclick do botao disparar: se o botao ja esta
# marcado como bloqueado, cancela o clique (nao chega nem a rodar o
# onclick); senao marca e libera sozinho depois de BASE_JS_COOLDOWN_MS.
# Nao precisou mudar nenhuma funcao de salvar existente - so incluir este
# script (BASE_JS) em cada pagina, igual ja fazemos com BASE_CSS.
BASE_JS = """
(function() {
  var COOLDOWN_MS = 1500;
  document.addEventListener('click', function(e) {
    var btn = e.target.closest('button');
    if (!btn) return;
    if (btn.dataset.bloqueado === '1') {
      e.stopImmediatePropagation();
      e.preventDefault();
      return;
    }
    btn.dataset.bloqueado = '1';
    setTimeout(function() { delete btn.dataset.bloqueado; }, COOLDOWN_MS);
  }, true);
})();
"""


# ---------------------------------------------------------------------------
# HTML - login
# ---------------------------------------------------------------------------
LOGIN_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Sistema Fuctura</title>\n<style>" + BASE_CSS + """
  .login-wrap { min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px; }
  .login-card { width:100%; max-width:380px; }
  .sub { color:var(--text-muted); font-size:0.85rem; margin-bottom:22px; line-height:1.5; }
  .login-card form label { margin-top:14px; }
  #erro { color:var(--danger); font-size:0.85rem; margin-top:12px; min-height:1.2em; }
</style></head><body>
<div class="login-wrap"><div class="card login-card">
<h1>Sistema Fuctura</h1>
<div class="sub">Use o mesmo login e senha do schoolfine.fuctura.com.br. Cada funcionário entra com a própria credencial.</div>
<form id="f">
  <label>Login</label><input type="text" id="login" required>
  <label>Senha</label><input type="password" id="senha" required>
  <button type="submit" class="btn-primary" style="width:100%; margin-top:18px;">Entrar</button>
  <div id="erro"></div>
</form>
</div></div>
<script>""" + BASE_JS + """
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const login = document.getElementById('login').value;
  const senha = document.getElementById('senha').value;
  document.getElementById('erro').textContent = 'Entrando...';
  const r = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({login, senha})});
  const d = await r.json();
  if (d.ok) window.location.href = '/';
  else document.getElementById('erro').textContent = d.mensagem || 'Não foi possível entrar.';
});
</script></body></html>"""


HOME_TEMPLATE = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Sistema Fuctura</title>\n<style>" + BASE_CSS + """
  .processos-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(240px, 1fr)); gap:16px; margin-top:8px; }
  .proc-card { display:flex; flex-direction:column; gap:8px; background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:22px; text-decoration:none; color:var(--text); box-shadow:var(--shadow); transition:transform .12s ease, box-shadow .12s ease, border-color .12s ease; }
  .proc-card:hover { transform:translateY(-2px); box-shadow:var(--shadow-md); border-color:var(--accent); }
  .proc-icon { font-size:1.6rem; }
  .proc-title { font-weight:700; font-size:1rem; }
  .proc-desc { font-size:0.82rem; color:var(--text-muted); line-height:1.45; }
</style></head><body>
<div class="topbar">
  <span class="brand">Sistema Fuctura</span>
  <div class="right">
    <span>__SAUDACAO__</span>
    __ADMIN_LINK__
    <a href="/api/logout" class="sair">Sair</a>
  </div>
</div>
<div class="page">
__AVISOS__
<h1>Processos</h1>
<p class="subtitulo">Escolha um processo para começar.</p>
<div class="processos-grid">
<a class="proc-card" href="/fechamento?modo=fechamento">
  <div class="proc-icon">📋</div>
  <div class="proc-title">Fechamento de Turma</div>
  <div class="proc-desc">Turma encerrada — registra o fechamento definitivo (só permite uma vez por turma).</div>
</a>
<a class="proc-card" href="/fechamento?modo=acompanhamento">
  <div class="proc-icon">🔄</div>
  <div class="proc-title">Acompanhamento de Turma</div>
  <div class="proc-desc">Turma em andamento — pode rodar quantas vezes quiser, sem fechar nada.</div>
</a>
<a class="proc-card" href="/reconciliacao">
  <div class="proc-icon">⚖</div>
  <div class="proc-title">Reconciliação de Devedores</div>
  <div class="proc-desc">Confere Situação x Turma de Controle e sinaliza inconsistências (comentário automático + confirmação por ação).</div>
</a>
<a class="proc-card" href="/alunos">
  <div class="proc-icon">🔍</div>
  <div class="proc-title">Consulta de Alunos</div>
  <div class="proc-desc">Busca por nome, mostra cadastro, financeiro, turmas e comentários, e permite registrar um novo comentário.</div>
</a>
<a class="proc-card" href="/alunos/novo">
  <div class="proc-icon">📝</div>
  <div class="proc-title">Cadastro de Aluno</div>
  <div class="proc-desc">Cria um aluno novo no Fuctura com os mesmos dados do cadastro rápido, mais Profissão (obrigatória).</div>
</a>
<a class="proc-card" href="/ata">
  <div class="proc-icon">🗒</div>
  <div class="proc-title">Ata de Chamada</div>
  <div class="proc-desc">Gera o PDF da Ata de Chamada oficial de uma turma, pronto pra imprimir — todo aluno vinculado, ativo ou não. Só leitura.</div>
</a>
<a class="proc-card" href="/turmas">
  <div class="proc-icon">🏫</div>
  <div class="proc-title">Consulta de Turmas</div>
  <div class="proc-desc">Busca turma e mostra o roster ativo. Só leitura.</div>
</a>
<a class="proc-card" href="/turmas/nova">
  <div class="proc-icon">➕</div>
  <div class="proc-title">Cadastro de Turma</div>
  <div class="proc-desc">Cria uma turma nova no Fuctura, já validando os limites reais de caractere.</div>
</a>
<a class="proc-card" href="/matricular">
  <div class="proc-icon">🎓</div>
  <div class="proc-title">Matricular Aluno</div>
  <div class="proc-desc">Matricula um aluno numa turma, com valor contratado e forma de pagamento reais.</div>
</a>
<a class="proc-card" href="/pagamento">
  <div class="proc-icon">💳</div>
  <div class="proc-title">Registrar Pagamento</div>
  <div class="proc-desc">Forma, parcela, valor e data — no padrão combinado com o Diógenes. Soma de verdade no financeiro do aluno.</div>
</a>
<a class="proc-card" href="/interessados">
  <div class="proc-icon">🙋</div>
  <div class="proc-title">Cadastrar Interessados</div>
  <div class="proc-desc">Cadastro de interessado com turma "-I-(curso)" obrigatória — não deixa esquecer esse passo.</div>
</a>
<a class="proc-card" href="/interessados/triagem">
  <div class="proc-icon">📞</div>
  <div class="proc-title">Interessados</div>
  <div class="proc-desc">Quem está aguardando contato — nome, telefone e resumo do histórico, pra ligar e depois colocar na turma certa.</div>
</a>
<a class="proc-card" href="/calculadora-multa">
  <div class="proc-icon">🧮</div>
  <div class="proc-title">Calculadora de Multa</div>
  <div class="proc-desc">Multa de cancelamento — 10% ou 20% dos módulos não cursados, conforme o contrato.</div>
</a>
<a class="proc-card" href="/turmas-entrada">
  <div class="proc-icon">🚪</div>
  <div class="proc-title">Turmas de Entrada</div>
  <div class="proc-desc">J1/PY1 recentes — quantos matriculados são de primeira vez de verdade (mínimo de 10 pra abrir a turma).</div>
</a>
</div>

<h1 style="margin-top:36px;">Atividade recente</h1>
<p class="subtitulo">
  Comentários marcados Urgente por algum funcionário (nunca automático) e matrículas feitas hoje, em todo o sistema — só leitura, carrega sob demanda.
</p>
<button class="btn-sim" onclick="carregarAtividadeRecente()">Carregar atividade recente</button>
<div id="atividadeRecente" style="margin-top:14px;"></div>

</div>
<script>""" + BASE_JS + """
async function carregarAtividadeRecente() {
  const div = document.getElementById('atividadeRecente');
  div.innerHTML = '<div class="card">Carregando (busca em todo o sistema, pode levar alguns segundos)...</div>';
  const r = await fetch('/api/atividade-recente');
  const d = await r.json();
  if (d.erro) { div.innerHTML = `<div class="card">Erro: ${d.erro}</div>`; return; }

  const linkAluno = (c) => c.id_aluno
    ? `<a href="/alunos?id=${c.id_aluno}">${c.titulo}</a>` : c.titulo;

  const listaUrgentes = d.urgentes.length
    ? d.urgentes.map(c => `<div class="item">${c.data} — ${linkAluno(c)}<br><span class="fonte">${c.texto || ''}</span></div>`).join('')
    : '<div class="fonte">Nenhum comentário Urgente encontrado.</div>';

  const listaMatriculas = d.matriculas_hoje.length
    ? d.matriculas_hoje.map(c => `<div class="item">${linkAluno(c)}<br><span class="fonte">${c.texto || ''}</span></div>`).join('')
    : '<div class="fonte">Nenhuma matrícula registrada hoje ainda.</div>';

  div.innerHTML = `
    <div class="card">
      <h3 style="margin-top:0;">⚠ Comentários Urgentes (marcados por funcionário)</h3>
      ${listaUrgentes}
    </div>
    <div class="card" style="margin-top:14px;">
      <h3 style="margin-top:0;">📝 Matrículas de hoje (${d.data})</h3>
      ${listaMatriculas}
    </div>
  `;
}
</script>
</body></html>"""


MANUTENCAO_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Modo manutenção</title>\n<style>" + BASE_CSS + """
  .estado-box { border-radius:var(--radius); padding:24px; text-align:center; margin:16px 0; border:2px solid; }
  .estado-box.normal { background:var(--success-bg); border-color:var(--success); }
  .estado-box.manut { background:var(--danger-bg); border-color:var(--danger); }
  .estado-box .rot { font-size:1.5rem; font-weight:800; letter-spacing:0.02em; }
  .estado-box .meta { font-size:0.85rem; color:var(--text-muted); margin-top:8px; }
</style></head><body>
<div class="topbar">
  <span class="brand">Modo manutenção</span>
  <div class="right"><a href="/api/logout" class="sair">Sair</a></div>
</div>
<div class="page" style="max-width:620px;">
<h1>Manutenção do sistema</h1>
<p class="subtitulo">Ativa/desativa o modo manutenção. Com o modo ativo, o sistema fica indisponível pra todo mundo até ser desativado aqui.</p>

<div id="estado" class="estado-box"><div class="rot">Carregando...</div></div>

<div id="acoes"></div>
<div id="msg" style="margin-top:12px;"></div>
</div>

<script>""" + BASE_JS + """
async function carregar() {
  const r = await fetch('/api/manutencao/status');
  const d = await r.json();
  const box = document.getElementById('estado');
  const acoes = document.getElementById('acoes');
  if (d.ativo) {
    box.className = 'estado-box manut';
    box.innerHTML = `<div class="rot">EM MANUTENÇÃO</div>
      <div class="meta">Ativado em ${d.quando || '?'} por ${d.por || '?'}${d.motivo ? '<br>Motivo: ' + d.motivo : ''}</div>`;
    acoes.innerHTML = `<button class="btn-sim" id="btAcao" onclick="desativar()">Desativar manutenção</button>`;
  } else {
    box.className = 'estado-box normal';
    box.innerHTML = `<div class="rot">OPERAÇÃO NORMAL</div>
      <div class="meta">Sistema disponível${d.quando ? ' — última mudança em ' + d.quando + ' por ' + (d.por || '?') : ''}</div>`;
    acoes.innerHTML = `
      <label>Motivo (opcional, fica registrado):</label>
      <input type="text" id="motivo" placeholder="Ex: atualização / ajuste">
      <div style="margin-top:12px;">
        <button class="btn-nao" id="btAcao" onclick="ativar()">Ativar manutenção</button>
      </div>`;
  }
  document.getElementById('msg').textContent = '';
}

async function ativar() {
  const btn = document.getElementById('btAcao');
  if (btn.dataset.armado !== '1') {
    btn.textContent = 'Confirmar: deixar o sistema indisponível pra todos?';
    btn.dataset.armado = '1';
    return;
  }
  const motivo = (document.getElementById('motivo') || {}).value || '';
  const r = await fetch('/api/manutencao/ativar', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ motivo }),
  });
  const d = await r.json();
  if (d.erro) { document.getElementById('msg').textContent = d.erro; return; }
  carregar();
}

async function desativar() {
  const btn = document.getElementById('btAcao');
  if (btn.dataset.armado !== '1') {
    btn.textContent = 'Confirmar: voltar o sistema à operação normal?';
    btn.dataset.armado = '1';
    return;
  }
  const r = await fetch('/api/manutencao/desativar', { method: 'POST' });
  const d = await r.json();
  if (d.erro) { document.getElementById('msg').textContent = d.erro; return; }
  carregar();
}

carregar();
</script>
</body></html>"""


ATIVIDADE_RECENTE_DIAS_URGENTE = 90


def _montar_atividade_recente(client, limite_urgentes=20, limite_matriculas=20,
                               dias_urgente=ATIVIDADE_RECENTE_DIAS_URGENTE):
    """So leitura - pra tela inicial. Busca comentarios tipo Urgente (2) e
    -Matricula (15) em TODO o sistema, qualquer aluno, via
    buscar_comentarios_por_tipo (ver fuctura_client.py - endpoint "Suas
    Postagens"). Pedido do usuario (2026-09-09).

    ORDEM (achado real 2026-09-10, depois de a tela mostrar comentarios
    Urgente de 2017): esse endpoint devolve do MAIS ANTIGO pro mais recente
    (ignora o sSortDir que a gente manda). Por isso aqui usa
    recentes_primeiro=True (pula pro fim da lista) E reordena por data de
    verdade no Python - nao da pra confiar na ordem do painel.

    Urgente: como nada e marcado Urgente automaticamente desde 2026-09-08
    (ver memoria/STATUS.md), essa lista e 100% sinal humano - ainda assim
    filtra fora qualquer sobra do proprio sistema (_e_titulo_analise). E,
    por ser "atividade RECENTE", corta o que for mais velho que
    dias_urgente (padrao 90) - um Urgente de anos atras nao e atividade
    recente, e so poluia a tela.

    Matricula: filtra so as de HOJE (buscando a partir do fim da lista, que
    e onde estao as recentes)."""
    hoje_dt = datetime.now()
    hoje = hoje_dt.strftime("%d/%m/%Y")

    def _data(c):
        try:
            return datetime.strptime(c["data"], "%d/%m/%Y")
        except (ValueError, TypeError):
            return datetime.min

    urgentes_brutos = client.buscar_comentarios_por_tipo("2", max_registros=300, recentes_primeiro=True)
    urgentes_brutos = [c for c in urgentes_brutos if not reconciliacao._e_titulo_analise(c["titulo"])]
    corte = hoje_dt - timedelta(days=dias_urgente)
    urgentes_brutos = [c for c in urgentes_brutos if _data(c) >= corte]
    urgentes_brutos.sort(key=_data, reverse=True)
    urgentes = [
        {**c, "id_aluno": client.resolver_id_aluno_por_acomp(c["id_acomp"])}
        for c in urgentes_brutos[:limite_urgentes]
    ]

    matriculas_brutas = client.buscar_comentarios_por_tipo("15", max_registros=800, recentes_primeiro=True)
    matriculas_brutas = [c for c in matriculas_brutas if c["data"] == hoje]
    matriculas_brutas.sort(key=_data, reverse=True)
    matriculas = [
        {**c, "id_aluno": client.resolver_id_aluno_por_acomp(c["id_acomp"])}
        for c in matriculas_brutas[:limite_matriculas]
    ]

    return {"urgentes": urgentes, "matriculas_hoje": matriculas, "data": hoje}


LIMITE_TURMAS_ENTRADA = 15  # as N mais recentes de CADA modulo, pra nao carregar historico inteiro


def _montar_turmas_entrada(client):
    """So leitura - pra tela inicial. Pedido do usuario (2026-09-18): J1 e
    PY1 sao os modulos de ENTRADA da academia (porta de entrada dos
    clientes novos) - so podem iniciar com no minimo 10 matriculados de
    PRIMEIRA VEZ de verdade (nem refazendo, nem ex-aluno/continuidade).
    Busca as turmas J1/PY1 mais recentes (pela data no proprio nome, ver
    academia_progresso.extrair_data_turma) e conta quantos matriculados
    em cada uma sao de primeira vez (so pela marca do nome - rapido, sem
    consultar o Fuctura aluno por aluno, mesmo metodo do funil no
    Fechamento de Turma).

    ACHADO (2026-09-18, relatado pelo usuario: "so apareceram turmas de
    python"): o autocomplete de turma do Fuctura (ctrl_autocomplete_turma.php)
    exige um termo de busca com pelo menos 3 caracteres - buscar "J1"/"J2"
    (2 caracteres) sempre devolvia 0 resultados, enquanto "PY1"/"JA4"/"JS3"
    (3+) funcionavam normal. Toda turma de curso de verdade comeca com "."
    no nome (ex: ".J1 08/07/25 TER N") - buscar "." + modulo em vez do
    modulo cru contorna o minimo de 3 caracteres pra QUALQUER modulo (com
    ou sem esse limite) sem mudar os resultados de quem ja tinha 3+
    (confirmado: ".PY1" devolve os mesmos 64 resultados que "PY1"). Junto
    com isso, o corte pros N mais recentes tambem era feito no total
    combinado (J1+PY1), entao se um modulo nao trouxesse nada (como no bug
    acima) o outro sozinho preenchia as 15 vagas - agora o corte e por
    modulo, garantindo turmas recentes de cada um.

    ACHADO 2 (2026-09-18, segunda rodada, relatado pelo usuario: "ele
    mostrou ai interessados, tem que mostrar o que estao matriculados
    apenas contabilizando" e "nao tem 30 turmas abertas, tem bem menos"):
      - o roster de uma turma (roster_turma) traz qualquer aluno cuja
        Situacao ATUAL no Fuctura esteja ligada aquele id_turma -
        inclusive quem so demonstrou interesse ("Interessado"/"Cliente sem
        Interesse") ou ja desistiu ("Cancelado"), nunca matriculando de
        verdade. Corrigido: conta e lista so quem tem status de
        matricula real (academia_progresso.eh_status_matriculado_real).
      - o corte pras N mais recentes so olhava a data EMBUTIDA NO NOME da
        turma (data de INICIO) - nao checava se ela ja tinha TERMINADO.
        Corrigido: so entra na lista quem ainda nao terminou, pela
        dataTermino de verdade (obter_turma), nao pelo nome
        (academia_progresso.turma_ainda_aberta).

    ACHADO 3 (2026-09-18, terceira rodada, relatado pelo usuario: "Tem que
    ver se esses realmente estao matriculados ou so foram matriculados na
    turma, sem ser matriculados de verdade"): status != Interessado/
    Cancelado NAO garante matricula de verdade - cruzando o roster com o
    financeiro de cada aluno (perfil_aluno) achei gente com status
    "Devedor" e ate "-Matriculado" com Contratado = R$0,00 e nunca pagou
    nada (ex real: LUANA DE LIMA POROCA ALMEIDA - Devedor, contratado=0,
    recebido=0 - so ficou vinculada a turma administrativamente). Por
    decisao do usuario, quem pagou um sinal mesmo sem "Contratado" formal
    preenchido ainda conta (ex real: JADSON - "-Matriculado", contratado=0
    mas recebido=75) - ver academia_progresso.eh_matricula_financeira_real.
    Isso exige 1 chamada extra (perfil_aluno) por candidato que passar no
    filtro de status - aceitavel dado que agora sao poucas turmas abertas
    (ver ACHADO 2) com poucos candidatos cada.

    ACHADO 4 (2026-09-18, mesmo dia, revisao comentario a comentario
    pedida pelo usuario apos ver "17 no mesmo bolo" - conferi TODOS os 17,
    nao so uma amostra): dois problemas concretos que os filtros
    anteriores nao pegavam:
      - EDSON VINICIUS SOUZA DOS SANTOS aparecia como "primeira vez" mas e
        MONITOR (ex-aluno ajudando o professor) - o proprio campo
        'observacao' do roster ja mostra isso ("AG J2 A4 CONT MONITO",
        truncado) - nao precisa de chamada extra nenhuma. Corrigido:
        academia_progresso.eh_observacao_monitor exclui monitor da
        contagem, mesmo padrao dos outros filtros de "nao e matricula de
        verdade pra este proposito".
      - JADSON AUGUSTO PEREIRA DA ROSA so passava no filtro financeiro
        (recebido=75) por causa de 2 comentarios de TESTE gravados por
        engano nesse aluno real (sobra de um teste anterior da tela
        Registrar Pagamento) - o historico real dele mostra que nem
        assinou contrato ainda. Por decisao do usuario, NAO mexe no
        historico real do Fuctura (evita gravacao em lote/edicao
        arriscada) - so quando Contratado=0 (a zona cinzenta que already
        exige essa checagem extra), busca o historico de comentarios e
        recalcula o recebido de verdade ignorando comentarios de teste
        (academia_progresso.recebido_real) em vez de confiar cego no
        agregado de perfil_aluno.

    ACHADO 5 (2026-09-18, revisao comentario a comentario de TODOS os
    matriculados das 4 turmas abertas, pedido do usuario apos "sim, seria
    interessante fazer o comment-by-comment de cada registro pra
    confirmar"): o nome do aluno nem sempre e atualizado com a marca "-"
    quando ele volta pra refazer um modulo - o campo 'observacao' (ja
    disponivel no roster, sem custo extra) costuma trazer "REF"/"CONT"
    mesmo sem marca nenhuma no nome. Casos reais: DAVID ARMSTRONG SOARES
    SIMAO (nome sem marca, observacao='J1 REF', comentario confirma
    "pediu para refazer J1" - contava como primeira vez, mas estava
    refazendo); MARCOS AUGUSTO FERREIRA CAMPOS (observacao='PY1 PRES J2
    CONT' - e aluno de Java continuando pra J2, nao aluno novo de
    Python). Corrigido em academia_progresso.indicador_funil_turma/
    categoria_nome (ver eh_observacao_refazendo).

    Testando a correcao acima ao vivo apareceu mais um caso que ela nao
    cobria: ALBERTO RICARDO MENDES DE SOUZA - status Ex-aluno, nome sem
    marca, observacao AMBIGUA ('AG J1', nao 'REF') - so o status revelava
    que ele nao e aluno novo (historico confirma: ja completou J1/J2/S3/A4
    antes, voltando pra refazer J1). Um Ex-aluno nunca pode ser "primeira
    vez" de verdade numa turma de entrada - corrigido com
    academia_progresso.eh_status_ex_aluno, mesmo padrao dos outros dois
    sinais. Nenhuma das correcoes desta rodada muda quem CONTA como
    matriculado de verdade (total_matriculados), so a classificacao
    primeira_vez/refazendo, que e o numero comparado com o minimo de 10."""
    candidatas = []
    for modulo in academia_progresso.MODULOS_ENTRADA:
        encontradas = client.buscar_turma_por_nome("." + modulo)
        do_modulo = [t for t in encontradas if academia_progresso.identificar_modulo(t["nome"]) == modulo]
        do_modulo.sort(key=lambda t: academia_progresso.extrair_data_turma(t["nome"]) or datetime.min, reverse=True)
        candidatas.extend(do_modulo[:LIMITE_TURMAS_ENTRADA])

    candidatas.sort(key=lambda t: academia_progresso.extrair_data_turma(t["nome"]) or datetime.min, reverse=True)

    turmas = []
    for t in candidatas:
        detalhe_turma = client.obter_turma(t["id_turma"])
        data_termino = detalhe_turma.get("dataTermino") if detalhe_turma else None
        if not academia_progresso.turma_ainda_aberta(data_termino):
            continue
        roster = client.roster_turma(t["id_turma"])
        matriculados_de_verdade = []
        for a in roster:
            if not academia_progresso.eh_status_matriculado_real(a.get("status")):
                continue
            if academia_progresso.eh_observacao_monitor(a.get("observacao")):
                continue
            perfil = client.perfil_aluno(a["id_aluno"])
            contratado = perfil.get("contratado")
            recebido = perfil.get("recebido")
            if not contratado:
                # zona cinzenta (sem contrato formal) - nao confia cego no
                # agregado de perfil_aluno, recalcula ignorando testes.
                recebido = academia_progresso.recebido_real(client.comentarios_aluno(a["id_aluno"]))
            if not academia_progresso.eh_matricula_financeira_real(contratado, recebido):
                continue
            matriculados_de_verdade.append(a)
        analise = academia_progresso.analisar_turma_entrada(t["nome"], matriculados_de_verdade)
        turmas.append({
            **analise, "id_turma": t["id_turma"],
            "data": analise["data"].strftime("%d/%m/%Y") if analise["data"] else None,
            # Pedido do usuario (2026-09-18): "importante poder conferir" -
            # lista os alunos (nao so a contagem) pra dar pra checar quem
            # entrou em cada categoria antes de confiar no numero. So quem
            # matriculou de verdade (mesmo filtro do total/primeira_vez).
            "alunos": [
                {"nome": a["nome"], "categoria": academia_progresso.categoria_nome(a["nome"], a.get("observacao"), a.get("status"))}
                for a in matriculados_de_verdade
            ],
        })
    return {"turmas": turmas, "limiar_minimo": academia_progresso.LIMIAR_MINIMO_TURMA_ENTRADA}


def _financeiro_resumido(perfil):
    def _r(v):
        return f"{(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return (
        f"Contratado R$ {_r(perfil.get('contratado'))} | "
        f"Recebido R$ {_r(perfil.get('recebido'))} | "
        f"Diferença R$ {_r(perfil.get('diferenca'))}"
    )


def _montar_resumo_deterministico(comentarios, perfil):
    """So os fatos, sem IA - reaproveita resumir_comentarios (histórico +
    último pagamento) e montar_resumo_pagamentos_desistencia (linha do
    tempo de pagamento/boleto/cancelamento/estorno)."""
    return {
        "modo": "deterministico",
        "financeiro": _financeiro_resumido(perfil),
        "resumo_historico": reconciliacao.resumir_comentarios(comentarios),
        "pagamentos_desistencia": email_cobranca.montar_resumo_pagamentos_desistencia(comentarios),
    }


def _prompt_resumo_ia(nome, financeiro, comentarios):
    linhas = []
    for c in comentarios:
        texto = " ".join((c.get("texto") or "").split())  # achata qualquer quebra/espaco duplo
        linhas.append(f"- {c.get('data', '?')} [{c.get('tipo', '?')}] {c.get('titulo', '')}: {texto}".rstrip())
    historico = "\n".join(linhas) if linhas else "(nenhum comentário no histórico)"
    return (
        "Você é um assistente administrativo de uma escola de cursos. Resuma, em português do "
        "Brasil, o histórico do aluno abaixo para um funcionário entender rápido a situação. "
        "Máximo ~8 linhas, em bullet points. Cubra: (1) situação atual e trajetória do aluno; "
        "(2) situação financeira / cobranças / pagamentos; (3) sinais de alerta (cancelamento, "
        "pedido de estorno, menção a advogado, reclamação). NÃO invente nada que não esteja nos "
        "comentários; se algo não aparece, diga que não há registro. Não repita o histórico "
        "inteiro, só o essencial.\n\n"
        f"Aluno: {nome}\n"
        f"Financeiro (números do cadastro): {financeiro}\n\n"
        f"Comentários (ordem cronológica):\n{historico}\n"
    )


def home_html(is_admin, avisos, nome_usuario=""):
    avisos_html = "".join(f'<div class="aviso">{a}</div>' for a in avisos)
    admin_link = '<a href="/admin">⚙ Configurações</a>' if is_admin else ""
    saudacao = f"Olá, {nome_usuario}." if nome_usuario else "Olá."
    html = HOME_TEMPLATE
    html = html.replace("__SAUDACAO__", saudacao)
    html = html.replace("__ADMIN_LINK__", admin_link)
    html = html.replace("__AVISOS__", avisos_html)
    return html


def pagina_em_manutencao():
    # Pagina generica - de proposito NAO cita quem/quando/por que.
    return (
        '<!doctype html><html lang="pt-br"><head><meta charset="utf-8">'
        '<title>Sistema indisponível</title><style>' + BASE_CSS + '</style></head><body>'
        '<div class="page" style="max-width:520px; margin:14vh auto; text-align:center;">'
        '<h1>Sistema temporariamente indisponível</h1>'
        '<p style="color:var(--text-muted);">Estamos em manutenção. Tente novamente mais tarde.</p>'
        '</div></body></html>'
    )


def carregar_sessao(handler):
    _limpar_expirados(sessions, SESSAO_TTL_SEGUNDOS)
    cookie = handler.headers.get("Cookie", "")
    m = re.search(r"sessao=([a-f0-9]+)", cookie)
    if not m:
        return None
    return sessions.get(m.group(1))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200, cookie=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_str, cookie=None, status=200):
        body = html_str.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_pdf(self, content, filename):
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.end_headers()
        self.wfile.write(content)

    def _send_imagem_jpeg(self, content):
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, max-age=300")
        self.end_headers()
        self.wfile.write(content)

    def _redirect(self, path, cookie=None):
        self.send_response(302)
        self.send_header("Location", path)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    # ---------------- GET ----------------
    def do_GET(self):
        """Rede de seguranca (achado real via teste de estresse, 2026-09-05):
        antes disso, qualquer excecao nao tratada dentro de um handler de
        rota (ex: path malformado, tipo inesperado) derrubava a conexao
        crua, sem resposta nenhuma pro navegador - violava a garantia que
        o projeto ja tinha ("nunca deixar a conexao cair sem resposta").
        Cobre TODA rota GET de uma vez so, incluindo as que ainda vao ser
        adicionadas no futuro."""
        try:
            self._do_GET_impl()
        except Exception as e:
            try:
                self._send_json({"erro": f"Erro inesperado no servidor: {e}"}, 500)
            except Exception:
                pass

    def _do_GET_impl(self):
        parsed = urlparse(self.path)
        sessao = carregar_sessao(self)

        eh_manut = bool(sessao and sessao.get("is_manut"))
        rota_manut = parsed.path == "/manutencao" or parsed.path.startswith("/api/manutencao/")

        # sessao de operador de manutencao (credencial propria, SEM
        # FucturaClient) so enxerga o painel de manutencao - qualquer outra
        # rota volta pra la.
        if eh_manut and not rota_manut and parsed.path != "/api/logout":
            self._redirect("/manutencao")
            return

        # modo manutencao ativo: quem nao e operador ve a pagina generica.
        # ACHADO REAL 2026-09-15 (bug critico relatado pelo usuario): "/"
        # tambem caia nesse bloqueio quando nao havia sessao - ou seja,
        # depois de deslogar (ou a sessao expirar) com a manutencao ja
        # ativa, NAO HAVIA COMO VER O FORMULARIO DE LOGIN pra digitar a
        # credencial de operador e desativar. O POST /api/login sempre
        # processou credencial de operador corretamente mesmo em
        # manutencao (ver _do_POST_impl) - faltava so conseguir chegar
        # ate a pagina que tem o formulario. Excecao: "/" sem sessao
        # nenhuma sempre mostra o login, mesmo em manutencao - uma sessao
        # NORMAL (nao-operador) ainda cai no bloqueio de qualquer jeito
        # (nao ganha acesso real ao sistema so por bater em "/").
        pode_ver_login = parsed.path == "/" and not sessao
        if manutencao.em_manutencao() and not eh_manut and parsed.path != "/api/logout" and not pode_ver_login:
            self._send_html(pagina_em_manutencao(), status=503)
            return

        if parsed.path == "/":
            if not sessao:
                self._send_html(LOGIN_HTML)
                return
            avisos = []
            if sessao["is_admin"]:
                cfg = config_store.carregar()
                for prov in cfg["providers"]:
                    a = config_store.alerta_orcamento(prov)
                    if a:
                        avisos.append(a)
            self._send_html(home_html(sessao["is_admin"], avisos, sessao.get("nome_usuario", "")))
            return

        if not sessao:
            self._redirect("/")
            return

        if parsed.path == "/api/logout":
            cookie = self.headers.get("Cookie", "")
            m = re.search(r"sessao=([a-f0-9]+)", cookie)
            if m:
                sessions.pop(m.group(1), None)
            self._redirect("/")
            return

        # rotas do painel de manutencao: pra quem nao e operador, caem no
        # 404 generico la embaixo (igual a qualquer URL inexistente).
        if parsed.path == "/manutencao" and eh_manut:
            self._send_html(MANUTENCAO_HTML)
            return

        if parsed.path == "/api/manutencao/status" and eh_manut:
            self._send_json(manutencao.carregar())
            return

        if parsed.path == "/fechamento":
            self._send_html(FECHAMENTO_HTML)
            return

        if parsed.path == "/reconciliacao":
            self._send_html(RECONCILIACAO_HTML)
            return

        if parsed.path == "/alunos":
            self._send_html(ALUNOS_HTML)
            return

        if parsed.path == "/alunos/novo":
            self._send_html(ALUNO_NOVO_HTML)
            return

        if parsed.path == "/api/aluno/buscar":
            termo = parse_qs(parsed.query).get("q", [""])[0]
            try:
                out = sessao["client"].buscar_alunos_por_nome(termo)
                self._send_json(out)
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
            return

        if parsed.path == "/api/atividade-recente":
            try:
                self._send_json(_montar_atividade_recente(sessao["client"]))
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
            return

        if parsed.path == "/ata":
            self._send_html(ATA_HTML)
            return

        if parsed.path.startswith("/api/turma/") and parsed.path.endswith("/ata"):
            id_turma = parsed.path.split("/")[3]
            try:
                self._send_json(sessao["client"].gerar_ata_chamada_oficial(id_turma))
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
            return

        if parsed.path == "/turmas":
            self._send_html(TURMAS_HTML)
            return

        if parsed.path == "/turmas/nova":
            self._send_html(TURMA_NOVA_HTML)
            return

        if parsed.path == "/matricular":
            self._send_html(MATRICULAR_HTML)
            return

        if parsed.path == "/pagamento":
            self._send_html(PAGAMENTO_HTML)
            return

        if parsed.path == "/interessados":
            self._send_html(INTERESSADO_HTML)
            return

        if parsed.path == "/api/turmas-interessados":
            try:
                self._send_json(sessao["client"].listar_turmas_interessados())
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
            return

        if parsed.path == "/interessados/triagem":
            self._send_html(INTERESSADOS_TRIAGEM_HTML)
            return

        if parsed.path == "/calculadora-multa":
            self._send_html(CALCULADORA_MULTA_HTML)
            return

        if parsed.path == "/turmas-entrada":
            self._send_html(TURMAS_ENTRADA_HTML)
            return

        if parsed.path == "/api/turmas-entrada":
            try:
                self._send_json(_montar_turmas_entrada(sessao["client"]))
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
            return

        if parsed.path == "/api/interessados/triagem":
            try:
                id_turma, roster = sessao["client"].roster_triagem_interessados()
                self._send_json({"id_turma": id_turma, "alunos": roster})
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
            return

        if parsed.path.startswith("/api/turma/") and parsed.path.endswith("/roster"):
            id_turma = parsed.path.split("/")[3]
            try:
                self._send_json(sessao["client"].roster_turma(id_turma))
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
            return

        if parsed.path.startswith("/api/turma/") and parsed.path.endswith("/ata-pdf"):
            # PDF original da Ata de Chamada do Fuctura, pronto pra imprimir
            # (pedido do usuário, 2026-09-23: "onde gerar a ata para
            # impressão assim como faz o schoolfine") - a tela /ata só
            # mostrava a tabela extraída, nunca entregava o PDF.
            id_turma = parsed.path.split("/")[3]
            try:
                pdf = sessao["client"].gerar_ata_chamada_pdf_bruto(id_turma)
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
                return
            if pdf is None:
                self._send_json({"erro": "O Fuctura não retornou um PDF pra essa turma (provavelmente sem alunos vinculados)."}, 500)
                return
            self._send_pdf(pdf, f"ata_chamada_{id_turma}.pdf")
            return

        if parsed.path.startswith("/api/turma/") and parsed.path.endswith("/verificacao"):
            id_turma = parsed.path.split("/")[3]
            try:
                pdf = sessao["client"].gerar_verificacao_turma_pdf(id_turma)
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
                return
            if pdf is None:
                self._send_json({"erro": "O Fuctura não retornou um PDF pra essa turma (provavelmente sem alunos vinculados)."}, 500)
                return
            self._send_pdf(pdf, f"verificacao_turma_{id_turma}.pdf")
            return

        if parsed.path.startswith("/api/turma/") and parsed.path.endswith("/detalhe"):
            id_turma = parsed.path.split("/")[3]
            try:
                turma = sessao["client"].obter_turma(id_turma)
                if turma is None:
                    self._send_json({"erro": "Turma não encontrada."}, 404)
                    return
                self._send_json(turma)
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
            return

        if parsed.path.startswith("/api/turma/") and parsed.path.endswith("/pagamentos"):
            id_turma = parsed.path.split("/")[3]
            try:
                pdf = sessao["client"].gerar_pagamentos_turma_pdf(id_turma)
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
                return
            if pdf is None:
                self._send_json({"erro": "O Fuctura não retornou um PDF pra essa turma (provavelmente sem alunos vinculados)."}, 500)
                return
            self._send_pdf(pdf, f"pagamentos_turma_{id_turma}.pdf")
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/detalhe"):
            id_aluno = parsed.path.split("/")[3]
            self._detalhe_aluno(sessao, id_aluno)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/resumo"):
            id_aluno = parsed.path.split("/")[3]
            self._resumo_aluno(sessao, id_aluno, com_ia=False)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/resumo-ia"):
            id_aluno = parsed.path.split("/")[3]
            self._resumo_aluno(sessao, id_aluno, com_ia=True)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/contrato"):
            id_aluno = parsed.path.split("/")[3]
            pdf = sessao["client"].gerar_contrato_aluno(id_aluno)
            if pdf is None:
                self._send_json({"erro": "O Fuctura não retornou um PDF de contrato pra este aluno."}, 500)
                return
            self._send_pdf(pdf, f"contrato_{id_aluno}.pdf")
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/ficha"):
            id_aluno = parsed.path.split("/")[3]
            pdf = sessao["client"].gerar_ficha_aluno(id_aluno)
            if pdf is None:
                self._send_json({"erro": "O Fuctura não retornou um PDF de ficha pra este aluno."}, 500)
                return
            self._send_pdf(pdf, f"ficha_{id_aluno}.pdf")
            return

        if parsed.path.startswith("/api/aluno/") and "/certificado/" in parsed.path:
            partes = parsed.path.split("/")
            id_aluno, trilha = partes[3], partes[5]
            self._gerar_certificado_trilha(sessao, id_aluno, trilha)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/certificado-biblia3d"):
            id_aluno = parsed.path.split("/")[3]
            self._gerar_certificado_biblia3d(sessao, id_aluno)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/foto"):
            id_aluno = parsed.path.split("/")[3]
            dados = fotos_aluno.ler_foto(id_aluno)
            if dados is None:
                self._send_json({"erro": "Este aluno ainda não tem foto cadastrada."}, 404)
                return
            self._send_imagem_jpeg(dados)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/boletos-cora"):
            id_aluno = parsed.path.split("/")[3]
            self._boletos_cora_aluno(sessao, id_aluno)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/preview-email"):
            id_aluno = parsed.path.split("/")[3]
            try:
                preview = email_cobranca.montar_preview_email(sessao["client"], id_aluno)
                self._send_json(preview)
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
            return

        if parsed.path == "/admin":
            if not sessao["is_admin"]:
                self._send_html("<p>Acesso restrito a administradores.</p>")
                return
            self._send_html(admin_html())
            return

        if parsed.path == "/api/turma/buscar":
            termo = parse_qs(parsed.query).get("q", [""])[0]
            try:
                out = sessao["client"].buscar_turma_por_nome(termo)
                self._send_json(out)
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
            return

        if parsed.path.startswith("/api/fechamento/aluno/") and parsed.path.endswith("/previa"):
            partes = parsed.path.split("/")
            job_id, id_aluno = partes[4], partes[5]
            self._previa_aluno(sessao, job_id, id_aluno)
            return

        if parsed.path == "/api/config":
            if not sessao["is_admin"]:
                self._send_json({"erro": "restrito"}, 403)
                return
            cfg = config_store.carregar()
            for prov in cfg["providers"]:
                cfg["providers"][prov]["uso_mes_atual"] = config_store.uso_do_mes(prov)
            self._send_json(cfg)
            return

        if parsed.path == "/api/gmail/status":
            gcfg = config_store.carregar().get("gmail", {})
            self._send_json({
                "configurado": bool(gcfg.get("configurado") and gcfg.get("client_id")),
                "conectado": gmail_client.conectado(sessao["login"]),
                "email_conectado": gmail_client.email_conectado(sessao["login"]),
            })
            return

        if parsed.path == "/api/gmail/autorizar":
            gcfg = config_store.carregar().get("gmail", {})
            if not gcfg.get("configurado") or not gcfg.get("client_id"):
                self._send_html("<p>O Gmail ainda não foi configurado pelo administrador (ver tela de Configurações). <a href='/reconciliacao'>Voltar</a></p>")
                return
            cookie = self.headers.get("Cookie", "")
            m = re.search(r"sessao=([a-f0-9]+)", cookie)
            sid = m.group(1) if m else None
            estado = novo_id()
            gmail_estados_pendentes[estado] = sid
            self._redirect(gmail_client.montar_url_autorizacao(gcfg["client_id"], estado))
            return

        if parsed.path == "/api/gmail/callback":
            qs = parse_qs(parsed.query)
            erro_google = qs.get("error", [""])[0]
            if erro_google:
                self._send_html(f"<p>Autorização cancelada ou negada pelo Google: {erro_google}. <a href='/reconciliacao'>Voltar</a></p>")
                return
            code = qs.get("code", [""])[0]
            estado = qs.get("state", [""])[0]
            sid_esperado = gmail_estados_pendentes.pop(estado, None)
            cookie = self.headers.get("Cookie", "")
            m = re.search(r"sessao=([a-f0-9]+)", cookie)
            sid_atual = m.group(1) if m else None
            if not estado or not sid_esperado or sid_esperado != sid_atual:
                self._send_html("<p>Não foi possível confirmar a autorização (sessão inválida ou expirada). Tente conectar de novo. <a href='/reconciliacao'>Voltar</a></p>")
                return
            sessao_atual = sessions.get(sid_atual)
            if not sessao_atual:
                self._send_html("<p>Sessão expirada. Faça login de novo e tente conectar o Gmail outra vez.</p>")
                return
            gcfg = config_store.carregar().get("gmail", {})
            try:
                tokens = gmail_client.trocar_code_por_tokens(gcfg["client_id"], gcfg["client_secret"], code)
            except Exception as e:
                self._send_html(f"<p>Erro trocando o código de autorização com o Google: {e}. <a href='/reconciliacao'>Voltar</a></p>")
                return
            refresh_token = tokens.get("refresh_token")
            if not refresh_token:
                self._send_html(
                    "<p>O Google não devolveu uma autorização permanente (refresh_token). "
                    "Tente remover o acesso do sistema em <a href='https://myaccount.google.com/permissions' target='_blank'>"
                    "myaccount.google.com/permissions</a> e conectar de novo. <a href='/reconciliacao'>Voltar</a></p>"
                )
                return
            email = gmail_client.descobrir_email(tokens["access_token"])
            gmail_client.salvar_conexao(sessao_atual["login"], refresh_token, email)
            self._redirect("/reconciliacao")
            return

        if parsed.path == "/api/drive/status":
            if not sessao["is_admin"]:
                self._send_json({"erro": "restrito"}, 403)
                return
            dcfg = config_store.carregar().get("drive", {})
            gcfg = config_store.carregar().get("gmail", {})
            self._send_json({
                "configurado": bool(dcfg.get("configurado") and gcfg.get("client_id")),
                "conectado": drive_client.conectado(),
                "email_conectado": drive_client.email_conectado(),
            })
            return

        if parsed.path == "/api/drive/autorizar":
            if not sessao["is_admin"]:
                self._send_html("<p>Acesso restrito a administradores.</p>")
                return
            gcfg = config_store.carregar().get("gmail", {})
            if not gcfg.get("client_id"):
                self._send_html("<p>Configure o Client ID/Secret do Gmail primeiro (mesma credencial é reaproveitada pro Drive). <a href='/admin'>Ir pra Configurações</a></p>")
                return
            cookie = self.headers.get("Cookie", "")
            m = re.search(r"sessao=([a-f0-9]+)", cookie)
            sid = m.group(1) if m else None
            estado = novo_id()
            drive_estados_pendentes[estado] = sid
            self._redirect(drive_client.montar_url_autorizacao(gcfg["client_id"], estado))
            return

        if parsed.path == "/api/drive/callback":
            qs = parse_qs(parsed.query)
            erro_google = qs.get("error", [""])[0]
            if erro_google:
                self._send_html(f"<p>Autorização cancelada ou negada pelo Google: {erro_google}. <a href='/admin'>Voltar</a></p>")
                return
            code = qs.get("code", [""])[0]
            estado = qs.get("state", [""])[0]
            sid_esperado = drive_estados_pendentes.pop(estado, None)
            cookie = self.headers.get("Cookie", "")
            m = re.search(r"sessao=([a-f0-9]+)", cookie)
            sid_atual = m.group(1) if m else None
            if not estado or not sid_esperado or sid_esperado != sid_atual:
                self._send_html("<p>Não foi possível confirmar a autorização (sessão inválida ou expirada). Tente conectar de novo. <a href='/admin'>Voltar</a></p>")
                return
            sessao_atual = sessions.get(sid_atual)
            if not sessao_atual or not sessao_atual.get("is_admin"):
                self._send_html("<p>Acesso restrito a administradores.</p>")
                return
            gcfg = config_store.carregar().get("gmail", {})
            try:
                tokens = drive_client.trocar_code_por_tokens(gcfg["client_id"], gcfg["client_secret"], code)
            except Exception as e:
                self._send_html(f"<p>Erro trocando o código de autorização com o Google: {e}. <a href='/admin'>Voltar</a></p>")
                return
            refresh_token = tokens.get("refresh_token")
            if not refresh_token:
                self._send_html(
                    "<p>O Google não devolveu uma autorização permanente (refresh_token). "
                    "Tente remover o acesso do sistema em <a href='https://myaccount.google.com/permissions' target='_blank'>"
                    "myaccount.google.com/permissions</a> e conectar de novo. <a href='/admin'>Voltar</a></p>"
                )
                return
            email = drive_client.descobrir_email(tokens["access_token"])
            drive_client.salvar_conexao(refresh_token, email, sessao_atual["login"])
            self._redirect("/admin")
            return

        if parsed.path == "/api/drive/pastas":
            if not sessao["is_admin"]:
                self._send_json({"erro": "restrito"}, 403)
                return
            nome = parse_qs(parsed.query).get("nome", [""])[0]
            if not nome:
                self._send_json({"erro": "Informe ?nome="}, 400)
                return
            gcfg = config_store.carregar().get("gmail", {})
            try:
                pastas = drive_client.listar_pastas_por_nome(gcfg["client_id"], gcfg["client_secret"], nome)
                self._send_json({"pastas": pastas})
            except drive_client.DriveNaoConectadoError as e:
                self._send_json({"erro": str(e)}, 400)
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
            return

        if parsed.path.startswith("/api/drive/pasta/") and parsed.path.endswith("/arquivos"):
            if not sessao["is_admin"]:
                self._send_json({"erro": "restrito"}, 403)
                return
            id_pasta = parsed.path.split("/")[4]
            gcfg = config_store.carregar().get("gmail", {})
            try:
                arquivos = drive_client.listar_arquivos_da_pasta(gcfg["client_id"], gcfg["client_secret"], id_pasta)
                self._send_json({"arquivos": arquivos})
            except drive_client.DriveNaoConectadoError as e:
                self._send_json({"erro": str(e)}, 400)
            except Exception as e:
                self._send_json({"erro": str(e)}, 500)
            return

        if parsed.path.startswith("/api/reconciliacao/") and parsed.path.endswith("/pdf/devedores"):
            job_id = parsed.path.split("/")[3]
            self._pdf_devedores_reconciliacao(job_id)
            return

        if parsed.path.startswith("/api/reconciliacao/") and parsed.path.endswith("/pdf/fechamento"):
            job_id = parsed.path.split("/")[3]
            periodo_label = parse_qs(parsed.query).get("periodo", [""])[0]
            self._pdf_fechamento_reconciliacao(job_id, periodo_label)
            return

        if parsed.path.startswith("/api/reconciliacao/") and "/pdf/individual/" in parsed.path:
            partes = parsed.path.split("/")
            job_id, id_aluno = partes[3], partes[6]
            self._pdf_individual_reconciliacao(sessao, job_id, id_aluno)
            return

        self.send_response(404)
        self.end_headers()

    # ---------------- POST ----------------
    def do_POST(self):
        """Mesma rede de seguranca de _do_GET_impl, pro lado das gravacoes -
        aqui importa ainda mais, porque um handler de escrita que quebra
        no meio pode deixar o funcionario sem saber se a gravacao foi ou
        nao aplicada. Cobre TODA rota POST de uma vez so."""
        try:
            self._do_POST_impl()
        except Exception as e:
            try:
                self._send_json({"erro": f"Erro inesperado no servidor: {e}"}, 500)
            except Exception:
                pass

    def _do_POST_impl(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/login":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._send_json({"ok": False, "mensagem": "Corpo da requisição inválido."}, 400)
                return
            login, senha = body.get("login", ""), body.get("senha", "")

            # operador de manutencao: credencial propria (ver
            # manutencao.OPERADORES_MANUTENCAO), nao passa pelo Fuctura. A
            # sessao nao tem FucturaClient e so acessa /manutencao.
            if manutencao.eh_operador(login):
                if not manutencao.verificar_operador(login, senha):
                    # mesma mensagem EXATA de um login normal errado.
                    self._send_json({"ok": False, "mensagem": "Login ou senha incorretos."})
                    return
                sid = novo_id()
                _limpar_expirados(sessions, SESSAO_TTL_SEGUNDOS)
                sessions[sid] = {
                    "login": login, "client": None, "nome_usuario": login,
                    "is_admin": True, "is_manut": True, "criado_em": time.time(),
                }
                self._send_json({"ok": True}, cookie=f"sessao={sid}; Path=/; HttpOnly")
                return

            # modo manutencao ativo: passou o bloco do operador, entao e um
            # login comum - mensagem generica, sem citar ninguem.
            if manutencao.em_manutencao():
                self._send_json({"ok": False, "mensagem": "Sistema temporariamente indisponível. Tente novamente mais tarde."})
                return
            try:
                client = FucturaClient(login, senha)
                client.entrar()
            except FucturaAuthError as e:
                self._send_json({"ok": False, "mensagem": str(e)})
                return
            except Exception as e:
                self._send_json({"ok": False, "mensagem": f"Erro de conexão: {e}"})
                return
            nome_usuario = getattr(client, "nome_usuario", "")
            sid = novo_id()
            _limpar_expirados(sessions, SESSAO_TTL_SEGUNDOS)
            sessions[sid] = {
                "login": login, "client": client, "nome_usuario": nome_usuario,
                "is_admin": config_store.eh_admin(login, nome_usuario),
                "is_manut": False,
                "criado_em": time.time(),
            }
            self._send_json({"ok": True}, cookie=f"sessao={sid}; Path=/; HttpOnly")
            return

        sessao = carregar_sessao(self)
        if not sessao:
            self._send_json({"erro": "não autenticado"}, 401)
            return

        eh_manut = bool(sessao.get("is_manut"))

        # sessao de operador de manutencao (sem FucturaClient) so mexe no
        # painel de manutencao - qualquer outra rota POST responde 404.
        if eh_manut and not parsed.path.startswith("/api/manutencao/"):
            self.send_response(404)
            self.end_headers()
            return

        # modo manutencao ativo: quem nao e operador recebe a resposta
        # generica.
        if manutencao.em_manutencao() and not eh_manut:
            self._send_json({"erro": "Sistema temporariamente indisponível. Tente novamente mais tarde."}, 503)
            return

        # rotas do painel de manutencao: pra quem nao e operador, nao
        # existem (404 generico la embaixo).
        if parsed.path == "/api/manutencao/ativar" and eh_manut:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            quem = sessao.get("nome_usuario") or sessao.get("login") or "?"
            self._send_json({"ok": True, **manutencao.ativar(por=quem, motivo=body.get("motivo", ""))})
            return

        if parsed.path == "/api/manutencao/desativar" and eh_manut:
            quem = sessao.get("nome_usuario") or sessao.get("login") or "?"
            self._send_json({"ok": True, **manutencao.desativar(por=quem)})
            return

        if parsed.path == "/api/config" and sessao["is_admin"]:
            length = int(self.headers.get("Content-Length", 0))
            novo_cfg = json.loads(self.rfile.read(length) or b"{}")
            config_store.salvar(novo_cfg)
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/fechamento/iniciar":
            self._iniciar_fechamento(sessao)
            return

        if parsed.path.startswith("/api/fechamento/aluno/") and parsed.path.endswith("/confirmar"):
            partes = parsed.path.split("/")
            job_id, id_aluno = partes[4], partes[5]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._confirmar_aluno(sessao, job_id, id_aluno, body)
            return

        if parsed.path == "/api/reconciliacao/iniciar":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._iniciar_reconciliacao(sessao, body)
            return

        if parsed.path.startswith("/api/reconciliacao/") and parsed.path.endswith("/aplicar"):
            # formato: /api/reconciliacao/<job_id>/aluno/<id_aluno>/aplicar
            partes = parsed.path.split("/")
            job_id, id_aluno = partes[3], partes[5]
            self._aplicar_acao_reconciliacao(sessao, job_id, id_aluno)
            return

        if parsed.path.startswith("/api/reconciliacao/") and parsed.path.endswith("/comentario"):
            # formato: /api/reconciliacao/<job_id>/aluno/<id_aluno>/comentario
            partes = parsed.path.split("/")
            job_id, id_aluno = partes[3], partes[5]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._confirmar_comentario_reconciliacao(sessao, job_id, id_aluno, body)
            return

        if parsed.path == "/api/aluno/criar":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._criar_aluno(sessao, body)
            return

        if parsed.path == "/api/turma/criar":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._criar_turma(sessao, body)
            return

        if parsed.path == "/api/interessados/cadastrar":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._cadastrar_interessado(sessao, body)
            return

        if parsed.path.startswith("/api/interessados/triagem/") and parsed.path.endswith("/mover"):
            id_aluno = parsed.path.split("/")[4]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._mover_interessado_triagem(sessao, id_aluno, body)
            return

        if parsed.path.startswith("/api/turma/") and parsed.path.endswith("/editar"):
            id_turma = parsed.path.split("/")[3]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._editar_turma(sessao, id_turma, body)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/comentario"):
            id_aluno = parsed.path.split("/")[3]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._criar_comentario_aluno(sessao, id_aluno, body)
            return

        if parsed.path.startswith("/api/aluno/") and "/comentario/" in parsed.path:
            partes = parsed.path.split("/")
            id_aluno, id_acomp = partes[3], partes[5]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._editar_comentario_aluno(sessao, id_aluno, id_acomp, body)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/foto/remover"):
            id_aluno = parsed.path.split("/")[3]
            fotos_aluno.remover_foto(id_aluno)
            self._send_json({"ok": True})
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/foto"):
            id_aluno = parsed.path.split("/")[3]
            self._upload_foto_aluno(id_aluno)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/situacao"):
            id_aluno = parsed.path.split("/")[3]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._mudar_situacao_aluno(sessao, id_aluno, body)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/cadastro"):
            id_aluno = parsed.path.split("/")[3]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._editar_cadastro_aluno(sessao, id_aluno, body)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/matricular"):
            id_aluno = parsed.path.split("/")[3]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._matricular_aluno(sessao, id_aluno, body)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/pagamento"):
            id_aluno = parsed.path.split("/")[3]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._registrar_pagamento(sessao, id_aluno, body)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/auditar-pagamentos"):
            id_aluno = parsed.path.split("/")[3]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._auditar_pagamentos(sessao, id_aluno, body)
            return

        if parsed.path.startswith("/api/aluno/") and parsed.path.endswith("/rascunho-gmail"):
            id_aluno = parsed.path.split("/")[3]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._criar_rascunho_gmail(sessao, id_aluno, body)
            return

        if parsed.path == "/api/gmail/desconectar":
            gmail_client.desconectar(sessao["login"])
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/drive/desconectar":
            if not sessao["is_admin"]:
                self._send_json({"erro": "restrito"}, 403)
                return
            drive_client.desconectar()
            self._send_json({"ok": True})
            return

        self.send_response(404)
        self.end_headers()

    # ---------------- logica dos endpoints grandes ----------------
    def _iniciar_fechamento(self, sessao):
        """Nunca deve deixar a conexao cair sem resposta - qualquer erro
        inesperado (ex: a IA devolver um dado mal formado que quebra algo
        mais na frente) tem que virar um JSON de erro, nao um crash mudo."""
        try:
            self._iniciar_fechamento_impl(sessao)
        except Exception as e:
            try:
                self._send_json({"erro": f"Erro inesperado processando a ata: {e}"}, 500)
            except Exception:
                pass  # resposta ja pode ter sido parcialmente enviada

    def _iniciar_fechamento_impl(self, sessao):
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=(.+)", ctype)
        if not m:
            self._send_json({"erro": "upload inválido"}, 400)
            return
        boundary = m.group(1).strip().strip('"')
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        partes = parse_multipart(body, boundary)

        turma_id = turma_nome = None
        caminhos_arquivos = []
        job_id = novo_id()
        pasta_job = os.path.join(UPLOAD_DIR, job_id)
        os.makedirs(pasta_job, exist_ok=True)

        modo = "fechamento"
        for p in partes:
            if p["name"] == "turma_id":
                turma_id = p["content"].decode("utf-8")
            elif p["name"] == "turma_nome":
                turma_nome = p["content"].decode("utf-8")
            elif p["name"] == "modo":
                modo = p["content"].decode("utf-8") or "fechamento"
            elif p["name"] == "arquivos" and p["filename"]:
                caminho = os.path.join(pasta_job, p["filename"])
                with open(caminho, "wb") as f:
                    f.write(p["content"])
                caminhos_arquivos.append(caminho)

        if not turma_id or not caminhos_arquivos:
            self._send_json({"erro": "turma e ao menos um arquivo são obrigatórios"}, 400)
            return

        try:
            resultado = processar_ata_fechamento(
                sessao["client"], turma_id, turma_nome, modo, caminhos_arquivos, pasta_job
            )
        except ErroProcessamento as e:
            self._send_json({"erro": str(e)}, 500)
            return

        turma_nome_final = turma_nome or resultado["extraido"].get("turma_identificada", "")
        _limpar_expirados(fechamento_jobs, JOB_TTL_SEGUNDOS)
        fechamento_jobs[job_id] = {
            "turma_id": turma_id, "turma_nome": turma_nome_final,
            "modo": modo, "extraido": resultado["extraido"],
            "alunos": {a["id_aluno"]: a for a in resultado["alunos"]},
            "roster_modalidade": resultado.get("roster_modalidade", {}),
            "criado_em": datetime.now().isoformat(),
        }
        # persiste em disco (fechamento_jobs e so em memoria, morre num
        # restart) - achado real 2026-09-07: os arquivos originais da ata
        # PREENCHIDA (a prova de presenca de verdade, diferente da Ata de
        # Chamada EM BRANCO que o Fuctura gera) ja ficavam salvos em
        # atas_recebidas/<job_id>/, mas sem nenhum indice ligando pasta ->
        # turma - impossivel de recuperar depois pra anexar num email sem
        # vasculhar nome de arquivo. Agora grava um metadata.json na hora
        # do upload, pra buscas futuras (ver anexos_email.py).
        try:
            with open(os.path.join(pasta_job, "metadata.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "job_id": job_id, "turma_id": turma_id, "turma_nome": turma_nome_final,
                    "modo": modo, "criado_em": fechamento_jobs[job_id]["criado_em"],
                    "arquivos": [os.path.basename(c) for c in caminhos_arquivos],
                }, f, ensure_ascii=False, indent=2)
        except OSError:
            pass  # nao impede o fechamento de continuar so por falha ao gravar o indice

        self._send_json({
            "job_id": job_id,
            "turma_identificada": resultado["extraido"].get("turma_identificada"),
            "confianca_geral": resultado["extraido"].get("confianca_geral"),
            "duvidas": resultado["extraido"].get("duvidas", []),
            "alunos": resultado["alunos"],
            "nao_casados": resultado["nao_casados"],
            "relatorio_turma": resultado["relatorio_turma"],
        })

    def _montar_previa(self, sessao, job, id_aluno):
        """So leitura - monta o resumo academico+financeiro sem gravar nada.
        Reaproveitado tanto pela pre-visualizacao quanto pela confirmacao."""
        client = sessao["client"]
        aluno_ata = job["alunos"].get(id_aluno)
        turma_nome = job["turma_nome"]
        modo = job.get("modo", "fechamento")
        infantil = logic.eh_turma_infantil(turma_nome)
        rotulo = "Acompanhamento" if modo == "acompanhamento" else "Fechamento"
        if infantil:
            rotulo += " (Infantil)"

        perfil = client.perfil_aluno(id_aluno)
        comentarios = client.comentarios_aluno(id_aluno)

        # o Acompanhamento pode ser rodado quantas vezes quiser - so o
        # Fechamento (definitivo) bloqueia repeticao pra mesma turma.
        if modo == "fechamento" and logic.ja_tem_fechamento(comentarios, turma_nome):
            return {"ja_fechado": True}

        # "Ex-aluno de verdade" (pedido do usuario, 2026-09-16, correcao do
        # Diogenes): o campo Situacao do Fuctura sozinho nao confirma que o
        # aluno completou a trilha (Java: J1/J2/JS3/JA4, Python: PY1-4) -
        # roda de graca aqui porque perfil_aluno() ja foi buscado por
        # outro motivo (nota de abandono/devedor), sem consulta extra.
        ex_aluno_de_verdade = academia_progresso.eh_ex_aluno_de_verdade(
            perfil.get("nome"), perfil.get("status"), perfil.get("turmas_atuais"),
        )

        montar_resumo = logic.montar_resumo_academico_infantil if infantil else logic.montar_resumo_academico
        resumo_academico = montar_resumo(aluno_ata["presencas"])
        texto_acad = resumo_academico["texto"]

        # Sinal A MAIS no FECHAMENTO: possível abandono (faltou tudo OU
        # faltou o bloco de aulas finais - ver montar_resumo_academico ->
        # possivel_abandono). O texto já traz a frase, sem repetir o fato
        # aqui (achado do usuário 2026-09-16: dizer duas vezes que faltou
        # tudo/faltou as finais é redundante) - a nota só traz CONFERÊNCIA:
        # mesmo padrão que o Diógenes gostou num comentário real (aluno
        # Miguel Tomaz, turma JA4) - "confira se é ex-aluno" e "revise na
        # ata" - só que automatizado: perfil_aluno() já foi buscado, então
        # o sistema CONFERE o status de verdade em vez de só lembrar.
        alerta_finais = bool(resumo_academico.get("possivel_abandono"))
        nota_faltas_finais = None
        if alerta_finais:
            datas_validas = [d for d in (logic._parse_data_segura(p["data"]) for p in aluno_ata["presencas"]) if d]
            data_referencia = max(datas_validas) if datas_validas else None
            turmas_mesmo_periodo = logic.turmas_no_mesmo_mes_ano(perfil.get("turmas_atuais"), turma_nome, data_referencia)
            nota_faltas_finais = logic.nota_verificacao_abandono(perfil.get("nome"), perfil.get("status"), turmas_mesmo_periodo)

        # Pedido do usuario (2026-09-12), respondendo a duvida sobre alunos
        # marcados "REF"/"MONITOR" no campo de situacao do roster: REF =
        # aluno refazendo o modulo (deve continuar sinalizando o alerta de
        # abandono, so com esse contexto a mais - nao filtrar); MONITOR =
        # ex-aluno ajudando o professor (sinaliza normal, sem tratamento
        # especial). So MONITOR nao precisa de codigo - so REF ganha nota.
        modalidade = (job.get("roster_modalidade") or {}).get(id_aluno, "")
        if alerta_finais and logic.eh_situacao_ref(modalidade):
            nota_ref = logic.nota_ref_faltas_finais(modalidade)
            nota_faltas_finais += " " + nota_ref
            texto_acad += " " + nota_ref

        # ACOMPANHAMENTO: averiguar se o aluno é ONLINE. A ata de chamada é
        # da SALA — não registra participação online —, então falta na ata
        # de um aluno online pode não ser falta real.
        eh_online = modalidade.strip().upper().startswith("ONLINE")
        nota_online = None
        if modo == "acompanhamento" and eh_online and resumo_academico["faltas"] > 0:
            nota_online = (
                f"Aluno marcado como \"{modalidade.strip()}\" (online). A ata de chamada da sala não "
                f"confirma presença online — conferir a participação na plataforma antes de tratar as "
                f"{resumo_academico['faltas']} falta(s) como reais."
            )
            texto_acad += " " + nota_online

        financeiro = logic.checagem_financeira(perfil, comentarios)
        controle = client.ids_turmas_controle_detalhado()
        ids_controle = controle["devedor"] | controle["advogado"]
        urgente, _msg_urgente = logic.regra_urgente_devedor_advogado(perfil, financeiro["tem_debito"], ids_controle)

        texto_fin = "; ".join(financeiro["inconsistencias"]) if financeiro["inconsistencias"] else "Situação financeira conferida, sem inconsistência identificada."
        # Pedido do usuario (2026-09-10): o Fechamento E o Acompanhamento de
        # Turma tem que dizer quando o aluno e devedor - estando ele ou nao
        # na turma de controle Devedor/Pendencia. nota_devedor() cruza a
        # diferenca financeira, a Situacao do cadastro e a presenca nas duas
        # turmas de controle numa frase so, na frente do comentario
        # financeiro (substitui a mensagem antiga de regra_urgente_devedor_
        # advogado, que so aparecia no caso "deve E nao esta em controle").
        nota_dev = logic.nota_devedor(perfil, financeiro["tem_debito"], controle["devedor"], controle["advogado"])
        if nota_dev:
            texto_fin = nota_dev + " " + texto_fin

        # Pedido do usuario (2026-09-10): o comentario FINANCEIRO so deve ser
        # gravado quando ha de fato uma questao financeira - inconsistencia
        # detectada (debito em aberto, pagamento duplicado) ou sinal de
        # devedor. Se esta tudo certo, grava so o comentario de Fechamento.
        financeiro_relevante = bool(financeiro["inconsistencias"]) or bool(nota_dev)

        return {
            "ja_fechado": False, "perfil": perfil, "turma_nome": turma_nome, "rotulo": rotulo,
            "resumo_academico": texto_acad,
            "resumo_financeiro": texto_fin,
            "financeiro_relevante": financeiro_relevante,
            # frase de devedor tambem separada (alem de ja estar no comeco do
            # resumo_financeiro) pra tela poder destacar sem depender de ler
            # o texto inteiro - None quando nao ha sinal de devedor.
            "devedor_nota": nota_dev,
            # sinais academicos a mais, separados pra tela destacar:
            "alerta_faltas_finais": alerta_finais,
            "nota_faltas_finais": nota_faltas_finais,
            "ex_aluno_de_verdade": ex_aluno_de_verdade,
            "modalidade": modalidade,
            "nota_online": nota_online,
            # Pedido explicito do usuario (2026-09-08), depois do flood: nada
            # de tipo="2" (Urgente) gravado automaticamente no Fuctura por
            # padrao, e (achado depois, mesmo dia) nenhuma mencao a "urgente"
            # no TEXTO gravado tambem (regra_urgente_devedor_advogado ja nao
            # usa mais essa palavra) nem destaque visual na nossa tela. O
            # booleano 'urgente' continua calculado (fica no dict pra quem
            # quiser usar no futuro) mas nao decide mais tipo, texto nem
            # aparencia de nada que o usuario ve.
            "urgente": bool(urgente or financeiro["inconsistencias"]),
            "tipo_fin": "3",
        }

    def _previa_aluno(self, sessao, job_id, id_aluno):
        job = fechamento_jobs.get(job_id)
        if not job:
            self._send_json({"erro": "sessão de fechamento não encontrada (pode ter expirado)"}, 404)
            return
        try:
            previa = self._montar_previa(sessao, job, id_aluno)
            self._send_json({"ok": True, **previa})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _confirmar_aluno(self, sessao, job_id, id_aluno, body):
        job = fechamento_jobs.get(job_id)
        if not job:
            self._send_json({"erro": "sessão de fechamento não encontrada (pode ter expirado)"}, 404)
            return
        confirmado = bool(body.get("confirmado"))
        if not confirmado:
            self._send_json({"ok": True, "gravado": False})
            return

        client = sessao["client"]
        turma_nome = job["turma_nome"]

        try:
            previa = self._montar_previa(sessao, job, id_aluno)
            if previa["ja_fechado"]:
                self._send_json({"ok": True, "gravado": False, "motivo": "já tinha fechamento para esta turma"})
                return

            # Pedido do usuario (2026-09-09): a tela manda sempre o que
            # estiver nas caixas de resumo (editadas ou nao) - comparamos
            # aqui contra o que _montar_previa acabou de gerar de novo pra
            # decidir o prefixo -IA-/-MOD- de cada comentario, academico e
            # financeiro de forma INDEPENDENTE (um pode ter sido editado
            # sem o outro). O backend e a autoridade da comparacao, nunca um
            # flag mandado pronto pela tela.
            texto_academico = _texto(body.get("resumo_academico"))
            texto_financeiro = _texto(body.get("resumo_financeiro"))
            academico_editado = bool(texto_academico) and texto_academico.strip() != previa["resumo_academico"].strip()
            financeiro_editado = bool(texto_financeiro) and texto_financeiro.strip() != previa["resumo_financeiro"].strip()
            resumo_academico_final = texto_academico if academico_editado else previa["resumo_academico"]
            resumo_financeiro_final = texto_financeiro if financeiro_editado else previa["resumo_financeiro"]
            prefixo_academico = logic.PREFIXO_TEXTO_EDITADO if academico_editado else logic.PREFIXO_TEXTO_ORIGINAL
            prefixo_financeiro = logic.PREFIXO_TEXTO_EDITADO if financeiro_editado else logic.PREFIXO_TEXTO_ORIGINAL

            # O comentario de Fechamento vem SEMPRE, e SEMPRE ANTES do
            # financeiro (pedido do usuario, 2026-09-10 - a ordem importa).
            status_acad = client.gravar_comentario(
                id_aluno, f"{prefixo_academico}{(previa['rotulo'] + ' — ' + turma_nome).upper()}", "3", resumo_academico_final
            )
            # O comentario financeiro so vem se houver questao financeira de
            # verdade (inconsistencia ou sinal de devedor) - se o
            # funcionario editou o texto, respeita a edicao e grava mesmo
            # assim (ele pode ter escrito algo relevante a mao).
            gravar_fin = bool(previa.get("financeiro_relevante")) or financeiro_editado
            status_fin = None
            if gravar_fin:
                status_fin = client.gravar_comentario(
                    id_aluno, f"{prefixo_financeiro}{('Financeiro (' + previa['rotulo'] + ') — ' + turma_nome).upper()}",
                    previa["tipo_fin"], resumo_financeiro_final,
                )

            self._send_json({
                "ok": True, "gravado": True,
                "resumo_academico": resumo_academico_final,
                "resumo_financeiro": resumo_financeiro_final if gravar_fin else None,
                "financeiro_gravado": gravar_fin,
                "academico_editado": academico_editado,
                "financeiro_editado": financeiro_editado if gravar_fin else False,
                "urgente": previa["urgente"],
                "status_http": [status_acad, status_fin],
            })
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _iniciar_reconciliacao(self, sessao, body):
        """Roda a analise de reconciliacao (reconciliacao_devedor.py) - SO
        LEITURA, nao grava nada. Gravar o comentario de analise (com aviso
        se ja existe analise anterior) e aplicar mudanca de status/turma
        exigem confirmacao explicita por aluno em endpoints separados."""
        try:
            limite = body.get("limite")
            limite = int(limite) if limite else None
            dias_minimos = body.get("dias_minimos")
            dias_minimos = int(dias_minimos) if dias_minimos else reconciliacao.PRAZO_PADRAO_DIAS
            dias_maximos = body.get("dias_maximos")
            dias_maximos = int(dias_maximos) if dias_maximos else reconciliacao.PRAZO_MAXIMO_DIAS
            job_id = novo_id()

            resultado = reconciliacao.rodar_reconciliacao(
                sessao["client"], limite=limite, dias_minimos=dias_minimos, dias_maximos=dias_maximos
            )

            # so guarda (e mostra na tela) quem tem algo relevante - "nao
            # elegivel" fica de fora por nao ter nenhuma acao ou comentario
            # a oferecer.
            itens = [a for a in resultado["analises"] if a["caso"] != "nao_elegivel"]
            _limpar_expirados(reconciliacao_jobs, JOB_TTL_SEGUNDOS)
            reconciliacao_jobs[job_id] = {
                "analises": {a["id_aluno"]: a for a in itens},
                # completo (INCLUI "nao_elegivel"), guardado a parte pro
                # Relatório de Fechamento (pedido do Diógenes, 2026-09-15) -
                # esse relatório precisa do universo TODO analisado (pra
                # "sem dívida em aberto no momento" bater certo), não só do
                # subconjunto "com algo pra decidir" que a tela mostra.
                "todas_analises": resultado["analises"],
                "resumo_por_caso": resultado["resumo_por_caso"],
                "total_analisados": resultado["total_analisados"],
                "criado_em": datetime.now().isoformat(),
            }

            itens_json = [
                {
                    "id_aluno": a["id_aluno"], "matricula": a.get("matricula", ""), "nome": a["nome"], "caso": a["caso"],
                    "valor_final": a["valor_final"], "urgente": a["urgente"],
                    "vencimento_referencia": a["vencimento_mais_antigo"].strftime("%d/%m/%Y") if a["vencimento_mais_antigo"] else None,
                    "fonte_vencimento": a["fonte_vencimento"],
                    "acao_sugerida": a["acao_sugerida"],
                    "resumo_comentarios": a.get("resumo_comentarios", ""),
                    "data_analise_anterior": a.get("data_analise_anterior"),
                    # texto exato que seria gravado (pedido do usuario,
                    # 2026-09-09): mostrado editavel na tela ANTES de gravar,
                    # em vez de so os campos que o compoem - ver
                    # reconciliacao.registrar_analise(texto_editado=...).
                    "texto_comentario": reconciliacao.montar_comentario_analise(a)[2],
                }
                for a in itens
            ]

            self._send_json({
                "job_id": job_id,
                "total_analisados": resultado["total_analisados"],
                "resumo_por_caso": resultado["resumo_por_caso"],
                "itens": itens_json,
            })
        except Exception as e:
            self._send_json({"erro": f"Erro na reconciliação: {e}"}, 500)

    def _confirmar_comentario_reconciliacao(self, sessao, job_id, id_aluno, body):
        job = reconciliacao_jobs.get(job_id)
        if not job:
            self._send_json({"erro": "sessão de reconciliação não encontrada (pode ter expirado)"}, 404)
            return
        analise = job["analises"].get(id_aluno)
        if not analise:
            self._send_json({"erro": "aluno não encontrado nesta sessão"}, 404)
            return
        if not body.get("confirmado"):
            self._send_json({"ok": True, "gravado": False})
            return
        try:
            texto_editado = body.get("texto")
            resultado = reconciliacao.registrar_analise(
                sessao["client"], analise,
                ignorar_analise_anterior=bool(body.get("ignorar_analise_anterior")),
                texto_editado=_texto(texto_editado) if texto_editado is not None else None,
            )
            self._send_json({"ok": True, **resultado})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _aplicar_acao_reconciliacao(self, sessao, job_id, id_aluno):
        job = reconciliacao_jobs.get(job_id)
        if not job:
            self._send_json({"erro": "sessão de reconciliação não encontrada (pode ter expirado)"}, 404)
            return
        analise = job["analises"].get(id_aluno)
        if not analise or not analise.get("acao_sugerida"):
            self._send_json({"erro": "ação não encontrada para este aluno nesta sessão"}, 404)
            return
        try:
            sucesso, descricao = reconciliacao.aplicar_acao(sessao["client"], analise)
            self._send_json({"ok": sucesso, "descricao": descricao})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _pdf_devedores_reconciliacao(self, job_id):
        """Relatório de Devedores (pedido do Diógenes, item 9 da
        transcrição 2026-09-14) - SEMPRE a partir de uma análise já
        rodada (job_id de /api/reconciliacao/iniciar), nunca busca dado
        novo sozinho (pedido explícito dele, item 10: PDF é consequência
        da análise, não um botão solto)."""
        job = reconciliacao_jobs.get(job_id)
        if not job:
            self._send_json({"erro": "sessão de reconciliação não encontrada (pode ter expirado - rode a análise de novo)"}, 404)
            return
        try:
            pdf = relatorios_pdf.gerar_pdf_devedores(list(job["analises"].values()))
            self._send_pdf(pdf, "relatorio_devedores.pdf")
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _pdf_fechamento_reconciliacao(self, job_id, periodo_label):
        job = reconciliacao_jobs.get(job_id)
        if not job:
            self._send_json({"erro": "sessão de reconciliação não encontrada (pode ter expirado - rode a análise de novo)"}, 404)
            return
        try:
            resultado = {
                "total_analisados": job["total_analisados"],
                "resumo_por_caso": job["resumo_por_caso"],
                "analises": job["todas_analises"],
            }
            pdf = relatorios_pdf.gerar_pdf_fechamento(resultado, periodo_label=periodo_label)
            self._send_pdf(pdf, "relatorio_fechamento.pdf")
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _pdf_individual_reconciliacao(self, sessao, job_id, id_aluno):
        job = reconciliacao_jobs.get(job_id)
        if not job:
            self._send_json({"erro": "sessão de reconciliação não encontrada (pode ter expirado - rode a análise de novo)"}, 404)
            return
        analise = job["analises"].get(id_aluno) or next((a for a in job["todas_analises"] if a["id_aluno"] == id_aluno), None)
        if not analise:
            self._send_json({"erro": "aluno não encontrado nesta sessão"}, 404)
            return
        try:
            comentarios = sessao["client"].comentarios_aluno(id_aluno)
            pdf = relatorios_pdf.gerar_pdf_individual(analise, comentarios)
            self._send_pdf(pdf, f"relatorio_individual_{id_aluno}.pdf")
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _detalhe_aluno(self, sessao, id_aluno):
        """So LEITURA - junta cadastro completo (CPF, email, endereco...),
        financeiro/turmas (perfil_aluno) e historico de comentarios num so
        payload pra tela de consulta de aluno."""
        try:
            client = sessao["client"]
            cadastro = client.buscar_cadastro_completo(id_aluno)
            perfil = client.perfil_aluno(id_aluno)
            comentarios = client.comentarios_aluno(id_aluno)
            status_label = STATUS_ALUNO.get(cadastro.get("status"), cadastro.get("status") or "(não definido)")
            turmas_atuais = perfil.get("turmas_atuais", [])
            # Pedido do usuario (2026-09-22): botao de certificado so
            # aparece quando o aluno realmente atende os criterios (mesma
            # regra de eh_ex_aluno_de_verdade) - a decisao de gerar ou nao
            # nao fica a criterio de quem esta na tela, o backend calcula.
            self._send_json({
                "cadastro": cadastro,
                "status_label": status_label,
                "financeiro": {
                    "contratado": perfil.get("contratado"), "recebido": perfil.get("recebido"),
                    "diferenca": perfil.get("diferenca"),
                },
                "turmas_atuais": turmas_atuais,
                "comentarios": list(reversed(comentarios)),
                "certificado": {
                    "elegivel_java": gerador_certificado.elegivel_para_certificado(
                        cadastro.get("nome"), status_label, turmas_atuais, trilha="java"),
                    "elegivel_python": gerador_certificado.elegivel_para_certificado(
                        cadastro.get("nome"), status_label, turmas_atuais, trilha="python"),
                    # Achado ao vivo (2026-09-22): existem 4 módulos de
                    # verdade (B3DM1-B3DM4) - mesma regra de elegibilidade
                    # de Java/Python agora (completou os 4, não é Devedor,
                    # sem marca no nome), não só "tem alguma turma infantil".
                    "elegivel_biblia3d": gerador_certificado.elegivel_para_certificado(
                        cadastro.get("nome"), status_label, turmas_atuais, trilha="biblia3d"),
                },
            })
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _resumo_aluno(self, sessao, id_aluno, com_ia):
        """So LEITURA - resume o historico do aluno. Dois modos:
        - deterministico: so os fatos (resumir_comentarios +
          montar_resumo_pagamentos_desistencia), nunca chama IA;
        - com_ia: alem dos fatos, manda o historico pro provedor de IA
          configurado e devolve um texto interpretado. Nada e gravado no
          Fuctura em nenhum dos casos."""
        try:
            client = sessao["client"]
            comentarios = client.comentarios_aluno(id_aluno)
            perfil = client.perfil_aluno(id_aluno)
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)
            return

        base = _montar_resumo_deterministico(comentarios, perfil)
        if not com_ia:
            self._send_json({"ok": True, **base})
            return

        cfg = config_store.carregar()
        prov = cfg.get("provider_ativo")
        if not prov:
            self._send_json({"erro": "Nenhum provedor de IA configurado (ver Configurações)."}, 400)
            return
        pconf = cfg["providers"].get(prov, {})
        prompt = _prompt_resumo_ia(perfil.get("nome") or id_aluno, base["financeiro"], comentarios)
        try:
            texto, tin, tout = vision.completar_texto(prov, pconf.get("modelo", ""), pconf.get("api_key", ""), prompt)
        except vision.VisionError as e:
            self._send_json({"erro": f"IA ({prov}): {e}"}, 502)
            return
        except Exception as e:
            self._send_json({"erro": f"Falha ao gerar resumo com IA: {e}"}, 502)
            return
        try:
            config_store.registrar_uso(prov, vision.estimar_custo_usd(prov, tin, tout))
        except Exception:
            pass
        self._send_json({"ok": True, **base, "modo": "ia", "provedor": prov, "resumo_ia": (texto or "").strip()})

    def _gerar_certificado_trilha(self, sessao, id_aluno, trilha):
        """Certificado de Java/Python - so gera quando o aluno realmente
        atende os criterios (mesma regra de eh_ex_aluno_de_verdade, ver
        gerador_certificado.elegivel_para_certificado) - reconfere aqui
        no backend, nunca confia so no que a tela mostrou (pode estar
        desatualizado se algo mudou entre carregar a tela e clicar)."""
        try:
            client = sessao["client"]
            cadastro = client.buscar_cadastro_completo(id_aluno)
            perfil = client.perfil_aluno(id_aluno)
            status_label = STATUS_ALUNO.get(cadastro.get("status"), cadastro.get("status") or "")
            turmas_atuais = perfil.get("turmas_atuais", [])
            if not gerador_certificado.elegivel_para_certificado(
                cadastro.get("nome"), status_label, turmas_atuais, trilha=trilha,
            ):
                self._send_json({
                    "erro": ("Este aluno ainda não atende aos critérios reais de conclusão "
                              "(completar todos os módulos da trilha, não ser Devedor, sem marca "
                              "de abandono/refazendo no nome)."),
                }, 400)
                return
            pdf = gerador_certificado.gerar_certificado_trilha_pdf(cadastro.get("nome"), trilha)
        except ValueError as e:
            self._send_json({"erro": str(e)}, 400)
            return
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)
            return
        self._send_pdf(pdf, f"certificado_{trilha}_{id_aluno}.pdf")

    def _gerar_certificado_biblia3d(self, sessao, id_aluno):
        """Certificado infantil (Bíblia 3D) - decisão do usuário
        (2026-09-22, depois de descobrir os 4 módulos reais B3DM1-B3DM4):
        mesma regra de elegibilidade de Java/Python agora (completou os 4
        módulos, não é Devedor, sem marca de abandono/refazendo no nome),
        reconferida aqui no backend, nunca só confiando no que a tela
        mostrou. O template ainda é só o de "Módulo I" (sem arte pros
        módulos II-IV ainda)."""
        try:
            client = sessao["client"]
            cadastro = client.buscar_cadastro_completo(id_aluno)
            perfil = client.perfil_aluno(id_aluno)
            status_label = STATUS_ALUNO.get(cadastro.get("status"), cadastro.get("status") or "")
            turmas_atuais = perfil.get("turmas_atuais", [])
            if not gerador_certificado.elegivel_para_certificado(
                cadastro.get("nome"), status_label, turmas_atuais, trilha="biblia3d",
            ):
                self._send_json({"erro": ("Este aluno ainda não atende aos critérios reais de conclusão "
                          "(completar os 4 módulos da Bíblia 3D, não ser Devedor, sem marca "
                          "de abandono/refazendo no nome).")}, 400)
                return
            pdf = gerador_certificado.gerar_certificado_biblia3d_pdf(cadastro.get("nome"))
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)
            return
        self._send_pdf(pdf, f"certificado_biblia3d_{id_aluno}.pdf")

    def _upload_foto_aluno(self, id_aluno):
        """Foto de aluno e uma funcionalidade nova, guardada só localmente
        no servidor - o Fuctura não tem esse campo (confirmado ao vivo,
        2026-09-22). Não escreve nada no Fuctura, então não precisa da
        mesma cautela de confirmação individual usada nas gravações reais
        lá (ver fotos_aluno.py) - só valida e salva."""
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=(.+)", ctype)
        if not m:
            self._send_json({"erro": "upload inválido"}, 400)
            return
        boundary = m.group(1).strip().strip('"')
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        partes = parse_multipart(body, boundary)

        arquivo = next((p for p in partes if p["name"] == "foto" and p["filename"]), None)
        if not arquivo:
            self._send_json({"erro": "Nenhum arquivo enviado."}, 400)
            return
        try:
            fotos_aluno.salvar_foto(id_aluno, arquivo["content"])
        except ValueError as e:
            self._send_json({"erro": str(e)}, 400)
            return
        self._send_json({"ok": True})

    def _criar_comentario_aluno(self, sessao, id_aluno, body):
        """Grava UM comentário manual no aluno, escrito diretamente pelo
        funcionário (nao e uma acao sugerida por analise automatica) - o
        clique em "Salvar comentário" e a propria confirmacao humana desse
        item especifico, mesmo padrao ja usado em _criar_aluno/_criar_turma
        (ver memoria fuctura_mascara_confirmacao_humana_obrigatoria: exige
        confirmacao individual no momento da gravacao, nunca em lote - aqui
        cada chamada grava exatamente 1 comentario em 1 aluno, por acao
        direta do usuario, nunca em loop)."""
        tipo = _texto(body.get("tipo"))
        # Pedido do usuario (2026-09-16): titulo de comentario sempre em
        # MAIUSCULO (convencao real da equipe) - forcado aqui pra valer
        # nao importa como o funcionario digitou. turma_nome (autocomplete
        # de turma) fica com a caixa original - so o assunto/titulo muda.
        assunto = _texto(body.get("assunto")).upper()
        descricao = _texto(body.get("descricao"))
        turma_id = _texto(body.get("turma_id")) or None
        turma_nome = _texto(body.get("turma_nome")) or None
        if tipo not in TIPOS_COMENTARIO_MANUAL:
            self._send_json({"erro": "Tipo de comentário inválido."}, 400)
            return
        if not assunto:
            self._send_json({"erro": "Assunto é obrigatório."}, 400)
            return
        if not descricao:
            self._send_json({"erro": "Descrição é obrigatória."}, 400)
            return
        try:
            client = sessao["client"]
            status_code = client.gravar_comentario(id_aluno, assunto, tipo, descricao, turma_id=turma_id, turma_nome=turma_nome)
            if status_code != 200:
                self._send_json({"erro": f"O Fuctura respondeu com status {status_code}."}, 500)
                return
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _editar_comentario_aluno(self, sessao, id_aluno, id_acomp, body):
        """Edita um comentario JA EXISTENTE (achado real 2026-09-09: o
        proprio Fuctura tem essa funcao - clicar num comentario que voce
        mesmo fez volta pra pagina do aluno com uma caixa abaixo dos dados
        preenchida pra alterar; endpoint e mecanismo identicos ao que ja
        usamos internamente pra neutralizar o flood, so nunca tinha virado
        botao pra uso geral do funcionario). O Fuctura NAO tem exclusao de
        comentario - editar e o unico jeito de corrigir um.

        Clique em "Salvar edição" e a confirmacao humana desse item
        especifico (mesmo padrao de _criar_comentario_aluno)."""
        tipo = _texto(body.get("tipo"))
        assunto = _texto(body.get("assunto")).upper()
        descricao = _texto(body.get("descricao"))
        turma_id = _texto(body.get("turma_id")) or None
        turma_nome = _texto(body.get("turma_nome")) or None
        if tipo not in TIPOS_COMENTARIO_MANUAL:
            self._send_json({"erro": "Tipo de comentário inválido."}, 400)
            return
        if not assunto:
            self._send_json({"erro": "Assunto é obrigatório."}, 400)
            return
        if not descricao:
            self._send_json({"erro": "Descrição é obrigatória."}, 400)
            return
        try:
            client = sessao["client"]
            status_code = client.editar_comentario(id_aluno, id_acomp, assunto, tipo, descricao, turma_id=turma_id, turma_nome=turma_nome)
            if status_code != 200:
                self._send_json({"erro": f"O Fuctura respondeu com status {status_code}."}, 500)
                return
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _mudar_situacao_aluno(self, sessao, id_aluno, body):
        """Muda a Situacao do aluno manualmente, fora do fluxo de
        Reconciliação (ex: aluno liga pedindo pausa, cancela por conta
        propria). Sempre grava um comentario de rastreabilidade (de X pra Y
        + motivo, se houver) - toda mudanca manual de Situacao fica
        registrada, mesmo sem motivo preenchido, pra nunca virar uma
        alteracao muda no cadastro. Clique em "Alterar Situação" e a
        confirmacao humana desse item."""
        novo_status = _texto(body.get("novo_status"))
        motivo = _texto(body.get("motivo"))
        if novo_status not in STATUS_ALUNO:
            self._send_json({"erro": "Situação inválida."}, 400)
            return
        try:
            client = sessao["client"]
            cadastro_atual = client.buscar_cadastro_completo(id_aluno)
            status_atual = cadastro_atual.get("status", "")
            if status_atual == novo_status:
                self._send_json({"erro": f'O aluno já está em "{STATUS_ALUNO[novo_status]}".'}, 400)
                return

            # Restrição pedida pelo usuário (2026-09-16): "impedir que
            # alguém tirando eu ou Diógenes possa marcar como ex-aluno sem
            # atender aos filtros" - so administradores (config_store,
            # hoje só Caio/Diógenes) podem marcar "Ex-aluno" (código "22")
            # sem restrição; qualquer outro funcionário só consegue se o
            # aluno realmente bate os critérios reais (completou todos os
            # módulos da trilha, não é Devedor, sem marca de abandono/
            # refazendo no nome agora - ver academia_progresso.
            # eh_ex_aluno_de_verdade, a mesma checagem já mostrada no
            # Fechamento de Turma).
            if novo_status == "22" and not sessao["is_admin"]:
                perfil = client.perfil_aluno(id_aluno)
                if not academia_progresso.eh_ex_aluno_de_verdade(
                    perfil.get("nome"), status_atual, perfil.get("turmas_atuais"),
                ):
                    self._send_json({
                        "erro": (
                            'Este aluno ainda não atende aos critérios reais de "Ex-aluno" '
                            "(completar todos os módulos da trilha, não ser Devedor, sem marca "
                            "de abandono/refazendo no nome) - só um administrador (Caio/Diógenes) "
                            "pode marcar Ex-aluno fora desses critérios."
                        ),
                    }, 403)
                    return

            label_atual = STATUS_ALUNO.get(status_atual, "(não definido)")
            label_novo = STATUS_ALUNO[novo_status]

            status_code = client.atualizar_status_aluno(id_aluno, novo_status)
            if status_code != 200:
                self._send_json({"erro": f"O Fuctura respondeu com status {status_code}."}, 500)
                return

            texto = f'Situação alterada manualmente de "{label_atual}" para "{label_novo}".'
            if motivo:
                texto += f" Motivo: {motivo}"
            client.gravar_comentario(id_aluno, "SITUAÇÃO ALTERADA MANUALMENTE", "3", texto)
            self._send_json({"ok": True, "situacao_anterior": label_atual, "situacao_nova": label_novo})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    # Campos editaveis do cadastro (contato/endereco/responsavel) - fora
    # daqui de proposito: 'matricula' (gerada pelo sistema, nao e pra
    # editar a mao) e 'status' (Situacao - tela propria, ainda por fazer,
    # nao mistura com essa edicao de dados cadastrais simples).
    _CAMPOS_EDITAVEIS_CADASTRO = [
        "nome", "email", "fixo", "celular", "cpf", "identidade", "dataNascimento", "sexo",
        "endereco", "numero", "complemento", "bairro", "cidade", "estado", "cep",
        "nomeResponsavel", "telefoneResponsavel", "celularResponsavel",
        "cpfResponsavel", "identidadeResponsavel", "emailResponsavel",
    ]

    def _editar_cadastro_aluno(self, sessao, id_aluno, body):
        """Atualiza dados cadastrais simples (contato/endereco/responsavel) -
        NUNCA mexe em matricula/status/turma. Clique em "Salvar cadastro" e
        a confirmacao humana desse item especifico, mesmo padrao das outras
        telas (ver memoria fuctura_mascara_confirmacao_humana_obrigatoria)."""
        nome = _texto(body.get("nome"))
        if not nome:
            self._send_json({"erro": "Nome é obrigatório."}, 400)
            return
        alteracoes = {campo: _texto(body.get(campo)) for campo in self._CAMPOS_EDITAVEIS_CADASTRO}
        erro_validacao = _validar_cpf_e_responsavel(alteracoes) or _validar_limites_cadastro(alteracoes)
        if erro_validacao:
            self._send_json({"erro": erro_validacao}, 400)
            return
        try:
            client = sessao["client"]
            status_code = client.atualizar_cadastro_aluno(id_aluno, alteracoes)
            if status_code != 200:
                self._send_json({"erro": f"O Fuctura respondeu com status {status_code}."}, 500)
                return
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _boletos_cora_aluno(self, sessao, id_aluno):
        """Boletos em aberto de verdade, vindos da CORA - ESTRUTURA PRONTA,
        esperando credenciais reais (ver cora_client.py). Enquanto nao
        configurado, devolve {'configurado': False} e a tela mostra um
        aviso em vez de quebrar - assim a tela de Reconciliação ja pode
        ser construida e testada agora, sem depender de credencial
        nenhuma, e passa a funcionar sozinha assim que alguem preencher a
        configuracao da CORA (nao precisa de mudanca nenhuma aqui)."""
        if not cora_client.esta_configurado():
            self._send_json({"configurado": False})
            return
        try:
            client = sessao["client"]
            cadastro = client.buscar_cadastro_completo(id_aluno)
            cpf = cadastro.get("cpf", "")
            boletos = cora_client.CoraClient().listar_boletos_em_aberto(cpf)
            resumo = cora_client.montar_resumo_boletos(cadastro.get("nome", ""), boletos)
            self._send_json({"configurado": True, **resumo})
        except cora_client.CoraNaoConfiguradoError as e:
            self._send_json({"configurado": False, "erro": str(e)})
        except Exception as e:
            self._send_json({"configurado": True, "erro": str(e)}, 500)

    def _matricular_aluno(self, sessao, id_aluno, body):
        """Matricula um aluno numa turma de verdade (curso pago, nao turma de
        controle) - grava um comentario tipo '15' (-Matricula) com o valor
        contratado e a forma de pagamento reais. Confirmado ao vivo em
        2026-09-05 que 'valorContratado' soma de verdade no total
        'Contratado' do aluno (perfil_aluno) - por isso valida o valor com
        cuidado aqui, nao deixa passar nada que nao seja um numero valido.

        O clique em "Matricular" e a confirmacao humana desse item
        especifico (mesmo padrao de _criar_aluno/_criar_turma/
        _criar_comentario_aluno - ver memoria
        fuctura_mascara_confirmacao_humana_obrigatoria)."""
        turma_id = _texto(body.get("turma_id"))
        turma_nome = _texto(body.get("turma_nome"))
        forma_pagamento = _texto(body.get("forma_pagamento"))
        observacao = _texto(body.get("observacao"))
        valor_str = _texto(body.get("valor_contratado")).replace(",", ".")

        if not turma_id or not turma_nome:
            self._send_json({"erro": "Selecione uma turma."}, 400)
            return
        if forma_pagamento not in FORMA_PAGAMENTO:
            self._send_json({"erro": "Forma de pagamento inválida."}, 400)
            return
        try:
            valor = float(valor_str)
            if valor < 0:
                raise ValueError
        except ValueError:
            self._send_json({"erro": "Valor contratado inválido."}, 400)
            return

        valor_fmt = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        try:
            client = sessao["client"]
            status_code = client.gravar_comentario(
                id_aluno, turma_nome.upper(), "15", observacao, turma_id=turma_id,
                valor_contratado=valor_fmt, forma_pagamento=forma_pagamento, turma_nome=turma_nome,
            )
            if status_code != 200:
                self._send_json({"erro": f"O Fuctura respondeu com status {status_code}."}, 500)
                return
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _registrar_pagamento(self, sessao, id_aluno, body):
        """Grava um comentario '-Pagamento Realizado' (tipo 11) seguindo a
        convencao pedida pelo Diogenes via Caio (2026-09-14): forma de
        pagamento + parcela + valor + data, sempre no mesmo formato -
        antes disso o sistema-mascara nunca gravava esse tipo (so lia,
        via checagem_financeira). Confirmado AO VIVO (2026-09-15, aluno de
        teste) que 'valorContratado' aqui soma de verdade no 'Recebido'
        do aluno (perfil_aluno), igual matricula soma no 'Contratado' -
        mesma cautela de _matricular_aluno: formulario proprio, nunca o
        generico de comentario (que de proposito nao aceita tipo 11)."""
        forma_pagamento = _texto(body.get("forma_pagamento"))
        parcela = _texto(body.get("parcela"))
        data_pagamento = _texto(body.get("data_pagamento"))
        observacao = _texto(body.get("observacao"))
        valor_str = _texto(body.get("valor")).replace(",", ".")

        if forma_pagamento not in FORMA_PAGAMENTO:
            self._send_json({"erro": "Forma de pagamento inválida."}, 400)
            return
        if not parcela:
            self._send_json({"erro": "Informe a parcela / mês de referência."}, 400)
            return
        if not data_pagamento:
            self._send_json({"erro": "Informe a data do pagamento."}, 400)
            return
        try:
            valor = float(valor_str)
            if valor <= 0:
                raise ValueError
        except ValueError:
            self._send_json({"erro": "Valor inválido."}, 400)
            return

        valor_fmt = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        texto = (
            f"Forma: {FORMA_PAGAMENTO[forma_pagamento]}. Parcela: {parcela}. "
            f"Valor: R$ {valor_fmt}. Data: {data_pagamento}."
        )
        if observacao:
            texto += f" Observação: {observacao}"

        try:
            client = sessao["client"]
            status_code = client.gravar_comentario(
                id_aluno, "PAGAMENTO REALIZADO", "11", texto,
                valor_contratado=valor_fmt, forma_pagamento=forma_pagamento,
            )
            if status_code != 200:
                self._send_json({"erro": f"O Fuctura respondeu com status {status_code}."}, 500)
                return
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _auditar_pagamentos(self, sessao, id_aluno, body):
        """Confere os comentários "-Pagamento Realizado" deste aluno
        contra um extrato bancário colado em CSV - pedido do usuário
        (2026-09-16), direção confirmada com o Diógenes: o comentário do
        Fuctura é quem precisa ser confirmado contra o extrato (fonte de
        verdade), não o contrário. Casamento só por data+valor (nomes em
        PIX vêm truncados no extrato, não são confiáveis pra casar - ver
        extrato_bancario.py). Nada é gravado - só leitura/conferência."""
        extrato_csv = _texto(body.get("extrato_csv"))
        if not extrato_csv:
            self._send_json({"erro": "Cole o extrato em CSV."}, 400)
            return
        try:
            extrato = extrato_bancario.parsear_csv_extrato(extrato_csv)
            if not extrato:
                self._send_json({"erro": "Não consegui ler nenhuma linha válida (data + valor) do CSV colado."}, 400)
                return
            comentarios = sessao["client"].comentarios_aluno(id_aluno)
            pagamentos = extrato_bancario.auditar_pagamentos_aluno(comentarios, extrato)
            for p in pagamentos:
                if p["data_extraida"]:
                    p["data_extraida"] = p["data_extraida"].strftime("%d/%m/%Y")
                if p["confirmacao"]["melhor_match"]:
                    m = p["confirmacao"]["melhor_match"]
                    p["confirmacao"]["melhor_match"] = {**m, "data": m["data"].strftime("%d/%m/%Y")}
            self._send_json({"pagamentos": pagamentos, "total_linhas_extrato": len(extrato)})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _criar_rascunho_gmail(self, sessao, id_aluno, body):
        """Cria um rascunho DE VERDADE no Gmail do funcionario (via API,
        OAuth ja autorizado por ele) - pedido do usuario (2026-09-15):
        o link tipo wa.me (abrirNoGmail, ver JS) nao carrega anexo
        (limitacao do proprio navegador); esta versao passa pela
        autorizacao real do Gmail e por isso PODE anexar de verdade.

        assunto/corpo vem prontos do frontend (mesmo texto que
        abrirNoGmail ja monta, sem duplicar a logica de formatacao em
        Python) - os anexos (contrato + atas) sao buscados aqui, de
        novo, direto do Fuctura/disco via anexos_email.montar_anexos_aluno
        (a mesma fonte que a previa do email ja usa) - nunca confia em
        bytes vindos do navegador."""
        assunto = _texto(body.get("assunto"))
        corpo = _texto(body.get("corpo"))
        if not assunto or not corpo:
            self._send_json({"erro": "Assunto e corpo são obrigatórios."}, 400)
            return

        gcfg = config_store.carregar().get("gmail", {})
        if not gcfg.get("configurado") or not gcfg.get("client_id"):
            self._send_json({"erro": "Gmail ainda não foi configurado pelo administrador."}, 400)
            return

        try:
            resultado_anexos = anexos_email.montar_anexos_aluno(sessao["client"], id_aluno)
            draft_id = gmail_client.criar_rascunho(
                sessao["login"], gcfg["client_id"], gcfg["client_secret"],
                destinatario="", assunto=assunto, corpo_texto=corpo,
                anexos=resultado_anexos["anexos"],
            )
            self._send_json({
                "ok": True, "draft_id": draft_id,
                "quantidade_anexos": len(resultado_anexos["anexos"]),
                "avisos_anexos": resultado_anexos["avisos"],
            })
        except gmail_client.GmailNaoConectadoError as e:
            self._send_json({"erro": str(e), "precisa_conectar": True}, 400)
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    # Bloco 2: campos do cadastro completo do Fuctura (detalhes_alunos.php)
    # que NAO existem no formulario de criacao rapida - opcionais na
    # criacao, completados via atualizar_cadastro_aluno() logo em seguida
    # se o usuario preencher algum.
    _CAMPOS_BLOCO2 = [
        "cpf", "identidade", "dataNascimento", "sexo", "endereco", "numero",
        "complemento", "bairro", "cidade", "estado", "cep",
        "nomeResponsavel", "telefoneResponsavel", "celularResponsavel",
        "cpfResponsavel", "identidadeResponsavel", "emailResponsavel",
    ]

    # Bloco 3: dados que so existem no nosso sistema (o Fuctura nao tem
    # campo nativo pra nenhum deles) - viram UM comentario estruturado so,
    # nunca um comentario por campo. So 'profissao' e obrigatoria; o resto
    # e opcional mas, quando preenchido, vem de dropdown no front (nao
    # texto livre) pra nao reintroduzir a mesma fragilidade que motivou
    # tudo isso (ver memoria fuctura_mascara_formularios_padronizados).
    _LABELS_BLOCO3 = [
        ("situacao_emprego", "Situação de emprego"),
        ("empresa_atual", "Empresa atual"),
        ("experiencia_programacao", "Experiência com programação"),
        ("curso_interesse", "Curso de interesse"),
        ("objetivo", "Objetivo"),
        ("como_conheceu", "Como conheceu a Fuctura"),
        ("disponibilidade", "Disponibilidade de horário"),
        ("contato_emergencia_nome", "Contato de emergência (nome)"),
        ("contato_emergencia_telefone", "Contato de emergência (telefone)"),
    ]

    def _criar_aluno(self, sessao, body):
        """Cria um aluno de verdade no Fuctura em ate 3 etapas:
        1) cadastro rapido (nome/email/fixo/celular/situacao) - os mesmos
           campos que o proprio Schoolfine pede na criacao;
        2) se algum campo do cadastro completo do Fuctura (CPF, endereco,
           responsavel...) foi preenchido, completa via
           atualizar_cadastro_aluno() logo em seguida;
        3) grava UM comentario estruturado com os dados que so existem no
           nosso sistema (profissao obrigatoria + extras opcionais).

        O clique em "Cadastrar" e a propria confirmacao do usuario - e um
        cadastro manual unico, nao uma acao em lote decidida por analise
        automatica, entao nao precisa de uma segunda tela de confirmacao
        como no Fechamento/Reconciliação."""
        nome = _texto(body.get("nome"))
        profissao = _texto(body.get("profissao"))
        status = _texto(body.get("status"))
        if not nome:
            self._send_json({"erro": "Nome é obrigatório."}, 400)
            return
        if not profissao:
            self._send_json({"erro": "Profissão é obrigatória."}, 400)
            return
        if not status:
            self._send_json({"erro": "Situação é obrigatória."}, 400)
            return

        # valida CPF/responsavel/limites de tamanho ANTES de criar o aluno
        # no Fuctura - se falhar aqui, nada e gravado (nem o cadastro rapido).
        bloco2 = {c: _texto(body.get(c)) for c in self._CAMPOS_BLOCO2}
        erro_validacao = (
            _validar_cpf_e_responsavel(bloco2)
            or _validar_limites_cadastro(dict(bloco2, nome=nome))
        )
        if erro_validacao:
            self._send_json({"erro": erro_validacao}, 400)
            return

        try:
            client = sessao["client"]
            id_aluno = client.criar_aluno(
                nome=nome, email=_texto(body.get("email")),
                fixo=_texto(body.get("fixo")), celular=_texto(body.get("celular")),
                status=status,
            )
            if not id_aluno:
                self._send_json({"erro": "O Fuctura não confirmou a criação (nenhum id retornado). Nada foi salvo."}, 500)
                return

            bloco2_preenchido = {k: v for k, v in bloco2.items() if v}
            if bloco2_preenchido:
                client.atualizar_cadastro_aluno(id_aluno, bloco2_preenchido)

            linhas = [f"Profissão: {profissao}"]
            for campo, rotulo in self._LABELS_BLOCO3:
                valor = body.get(campo)
                if isinstance(valor, list):
                    valor = ", ".join(v for v in valor if v)
                valor = (valor or "").strip() if isinstance(valor, str) else valor
                if valor:
                    linhas.append(f"{rotulo}: {valor}")
            client.gravar_comentario(
                id_aluno, "CADASTRO — DADOS COMPLEMENTARES", "3", "\n".join(linhas),
            )
            self._send_json({"ok": True, "id_aluno": id_aluno})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _cadastrar_interessado(self, sessao, body):
        """Cadastro de Interessado com turma "-I-(curso)" OBRIGATÓRIA -
        pedido do Diógenes via Caio (2026-09-12): a falha real que ele
        reportou é registrar o resumo do interessado no cadastro mas
        esquecer de matriculá-lo na fila "-I-" do curso certo (existe uma
        família de 27 turmas nesse padrão - achado ao vivo no mesmo dia).
        Aqui as duas coisas SEMPRE acontecem juntas, no mesmo clique - não
        dá pra criar o aluno sem escolher a turma. Situação fica fixa em
        "Interessado" (19), não é um campo escolhido nesta tela de
        propósito (é a razão da tela existir)."""
        nome = _texto(body.get("nome"))
        turma_id = _texto(body.get("turma_id"))
        turma_nome = _texto(body.get("turma_nome"))
        resumo = _texto(body.get("resumo"))
        if not nome:
            self._send_json({"erro": "Nome é obrigatório."}, 400)
            return
        if not turma_id or not turma_nome:
            self._send_json({"erro": "Escolha a turma de interesse — é obrigatório."}, 400)
            return
        try:
            client = sessao["client"]
            id_aluno = client.criar_aluno(
                nome=nome, email=_texto(body.get("email")),
                fixo=_texto(body.get("fixo")), celular=_texto(body.get("celular")),
                status="19",
            )
            if not id_aluno:
                self._send_json({"erro": "O Fuctura não confirmou a criação (nenhum id retornado). Nada foi salvo."}, 500)
                return
            status_matricula = client.gravar_comentario(
                id_aluno, turma_nome.upper(), "15", resumo,
                turma_id=turma_id, valor_contratado="0,00", forma_pagamento="---", turma_nome=turma_nome,
            )
            if status_matricula != 200:
                self._send_json({
                    "erro": f"Aluno criado (id {id_aluno}), mas o Fuctura respondeu {status_matricula} "
                            "ao colocar na turma de interessados. Confira manualmente pela Consulta de Alunos.",
                    "id_aluno": id_aluno,
                }, 500)
                return
            self._send_json({"ok": True, "id_aluno": id_aluno})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _mover_interessado_triagem(self, sessao, id_aluno, body):
        """Matricula o aluno (ja na turma de triagem "-IA- Interessados")
        numa turma "-I-(curso)" especifica, depois que o funcionario
        conferiu o resumo e falou com ele. NAO remove da triagem - o
        Fuctura nao tem como remover matricula, so adicionar (mesmo
        principio de nunca apagar historico usado no resto do sistema);
        a matricula de triagem so fica como registro, sem efeito
        colateral (curso administrativo, valor 0,00)."""
        turma_id = _texto(body.get("turma_id"))
        turma_nome = _texto(body.get("turma_nome"))
        if not turma_id or not turma_nome:
            self._send_json({"erro": "Escolha a turma de destino."}, 400)
            return
        try:
            client = sessao["client"]
            status = client.gravar_comentario(
                id_aluno, turma_nome.upper(), "15",
                f"Movido da triagem \"{client.NOME_TURMA_TRIAGEM_INTERESSADOS}\" após contato.",
                turma_id=turma_id, valor_contratado="0,00", forma_pagamento="---", turma_nome=turma_nome,
            )
            if status != 200:
                self._send_json({"erro": f"O Fuctura respondeu com status {status}."}, 500)
                return
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _criar_turma(self, sessao, body):
        """Cria uma turma de verdade no Fuctura. Valida os limites reais de
        caractere de 'abreviada'/'descricao' ANTES de enviar (ver
        fuctura_client.LIMITE_ABREVIADA_TURMA/LIMITE_DESCRICAO_TURMA) - o
        Fuctura trunca esses campos sem avisar, entao a validacao tem que
        acontecer aqui, nao la."""
        abreviada = _texto(body.get("abreviada"))
        entidade = _texto(body.get("entidade"))
        descricao = _texto(body.get("descricao"))
        professor = _texto(body.get("professor"))
        if not abreviada:
            self._send_json({"erro": "Abreviada é obrigatória."}, 400)
            return
        if len(abreviada) > FucturaClient.LIMITE_ABREVIADA_TURMA:
            self._send_json({"erro": f"Abreviada só pode ter até {FucturaClient.LIMITE_ABREVIADA_TURMA} caracteres (o Fuctura trunca sem avisar)."}, 400)
            return
        if not entidade:
            self._send_json({"erro": "Unidade é obrigatória."}, 400)
            return
        if not descricao:
            self._send_json({"erro": "Descrição é obrigatória."}, 400)
            return
        if len(descricao) > FucturaClient.LIMITE_DESCRICAO_TURMA:
            self._send_json({"erro": f"Descrição só pode ter até {FucturaClient.LIMITE_DESCRICAO_TURMA} caracteres (o Fuctura trunca sem avisar)."}, 400)
            return
        if not professor:
            self._send_json({"erro": "Professor é obrigatório."}, 400)
            return
        try:
            client = sessao["client"]
            id_turma = client.criar_turma(
                abreviada=abreviada, entidade=entidade, descricao=descricao,
                professor=professor,
                data_inicio=_texto(body.get("dataInicio")),
                data_termino=_texto(body.get("dataTermino")),
            )
            if not id_turma:
                self._send_json({"erro": "O Fuctura não confirmou a criação (nenhum id retornado). Nada foi salvo."}, 500)
                return
            self._send_json({"ok": True, "id_turma": id_turma})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)

    def _editar_turma(self, sessao, id_turma, body):
        """Edita uma turma EXISTENTE (Consulta de Turmas) - mesma validacao
        de limite de caractere de _criar_turma, so que so exige os campos
        que vieram no body (edicao parcial do lado de fora; por baixo,
        atualizar_turma() sempre reenvia o cadastro inteiro pro Fuctura)."""
        alteracoes = {}
        for campo in ("abreviada", "entidade", "descricao", "professor", "dataInicio", "dataTermino"):
            if campo in body:
                alteracoes[campo] = _texto(body.get(campo))
        if "abreviada" in alteracoes:
            if not alteracoes["abreviada"]:
                self._send_json({"erro": "Abreviada é obrigatória."}, 400)
                return
            if len(alteracoes["abreviada"]) > FucturaClient.LIMITE_ABREVIADA_TURMA:
                self._send_json({"erro": f"Abreviada só pode ter até {FucturaClient.LIMITE_ABREVIADA_TURMA} caracteres (o Fuctura trunca sem avisar)."}, 400)
                return
        if "descricao" in alteracoes:
            if not alteracoes["descricao"]:
                self._send_json({"erro": "Descrição é obrigatória."}, 400)
                return
            if len(alteracoes["descricao"]) > FucturaClient.LIMITE_DESCRICAO_TURMA:
                self._send_json({"erro": f"Descrição só pode ter até {FucturaClient.LIMITE_DESCRICAO_TURMA} caracteres (o Fuctura trunca sem avisar)."}, 400)
                return
        try:
            sessao["client"].atualizar_turma(id_turma, alteracoes)
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"erro": str(e)}, 500)


# ---------------------------------------------------------------------------
# HTML - fechamento e admin (paginas maiores, definidas a parte)
# ---------------------------------------------------------------------------
FECHAMENTO_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Fechamento de Turma</title>\n<style>" + BASE_CSS + """
  #relatorioTurma table { margin-top:10px; }
  #resumoFinal { background:var(--success-bg); border:1px solid #cfe0d6; }
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1 id="tituloModo">Fechamento de Turma</h1>

<div class="card">
  <label>Turma</label>
  <div class="autocomplete-box">
    <input type="text" id="buscaTurma" placeholder="Digite o nome da turma..." autocomplete="off">
    <div id="resultadosTurma" class="autocomplete-list"></div>
  </div>
  <input type="hidden" id="turmaId">
  <div id="turmaEscolhida" style="margin-top:8px; font-weight:600;"></div>
  <br>
  <label>Ata (imagens ou PDF, pode selecionar várias)</label>
  <input type="file" id="arquivos" multiple accept=".jpg,.jpeg,.png,.pdf">
  <br><br>
  <button class="acao" onclick="iniciar()">Processar ata</button>
  <span id="status"></span>
</div>

<div id="duvidas"></div>
<div id="relatorioTurma" class="card" style="display:none;"></div>
<div id="listaAlunos"></div>
<div id="resumoFinal" class="card" style="display:none;"></div>
</div>

<script>""" + BASE_JS + """
let turmaSelecionada = null;
let jobAtual = null;
let contagem = { total: 0, ja_fechados: 0, fechados_agora: 0, inconsistencia: 0, debito_sem_turma: 0 };
const modo = new URLSearchParams(window.location.search).get('modo') === 'acompanhamento' ? 'acompanhamento' : 'fechamento';
document.getElementById('tituloModo').textContent = modo === 'acompanhamento' ? 'Acompanhamento de Turma' : 'Fechamento de Turma';

// Pedido do Diogenes via Caio (2026-09-11), especificado em 2026-09-12:
// botao SEPARADO pra atualizar a Situacao do cadastro aqui mesmo no
// Fechamento/Acompanhamento, sem depender de ir na Consulta de Alunos.
// So dispara quando o usuario escolhe de proposito (opcional, comeca sem
// selecao nenhuma) e sempre com confirmacao antes de gravar - mesmo padrao
// "clique de novo pra confirmar" ja usado no resto do sistema. Reaproveita
// o MESMO endpoint /api/aluno/<id>/situacao ja usado na Consulta de Alunos
// (grava sozinho um comentario de rastreabilidade "de X pra Y").
const STATUS_ALUNO_OPCOES = {
  "4": "-Matriculado", "30": "Advogado", "5": "Cancelado", "29": "Cliente sem Interesse",
  "26": "Convenio", "7": "Devedor", "22": "Ex-aluno", "19": "Interessado",
  "6": "Pediu Pausa", "31": "Provi/Pravaler",
};

document.getElementById('buscaTurma').addEventListener('input', async (e) => {
  const q = e.target.value;
  if (q.length < 2) { document.getElementById('resultadosTurma').innerHTML = ''; return; }
  const r = await fetch('/api/turma/buscar?q=' + encodeURIComponent(q));
  const turmas = await r.json();
  const div = document.getElementById('resultadosTurma');
  div.innerHTML = turmas.map(t => `<div class="item" onclick='escolherTurma("${t.id_turma}", ${JSON.stringify(t.nome)})'>${t.nome}</div>`).join('');
});

function escolherTurma(id, nome) {
  turmaSelecionada = { id, nome };
  document.getElementById('turmaId').value = id;
  document.getElementById('turmaEscolhida').textContent = 'Selecionada: ' + nome;
  document.getElementById('resultadosTurma').innerHTML = '';
  document.getElementById('buscaTurma').value = nome;
}

async function iniciar() {
  if (!turmaSelecionada) { alert('Escolha uma turma primeiro.'); return; }
  const arquivos = document.getElementById('arquivos').files;
  if (arquivos.length === 0) { alert('Selecione ao menos um arquivo.'); return; }

  document.getElementById('status').textContent = ' Lendo ata, pode levar um tempo...';
  const fd = new FormData();
  fd.append('turma_id', turmaSelecionada.id);
  fd.append('turma_nome', turmaSelecionada.nome);
  fd.append('modo', modo);
  for (const f of arquivos) fd.append('arquivos', f);

  const r = await fetch('/api/fechamento/iniciar', { method: 'POST', body: fd });
  const d = await r.json();
  document.getElementById('status').textContent = '';
  if (d.erro) { alert(d.erro); return; }

  jobAtual = d.job_id;
  contagem.total = d.alunos.length;

  const duvidasDiv = document.getElementById('duvidas');
  if (d.duvidas && d.duvidas.length) {
    duvidasDiv.innerHTML = '<div class="card"><b>Pontos que a ata não deixou claros:</b>' +
      d.duvidas.map(x => `<div class="duvida">${x}</div>`).join('') + '</div>';
  }
  if (d.nao_casados && d.nao_casados.length) {
    duvidasDiv.innerHTML += '<div class="card"><b>Nomes da ata que não bateram com o roster atual da turma:</b>' +
      d.nao_casados.map(x => `<div class="duvida"><b>${x.nome}</b>` +
        (x.contexto ? `<br><span style="color:#a55;">${x.contexto}</span>` : '<br><span style="color:#888;">Não encontrado em nenhuma busca ampla (devedor/advogado/lista de devedores) - conferir manualmente.</span>') +
        `</div>`).join('') + '</div>';
  }

  const rel = d.relatorio_turma;
  const relDiv = document.getElementById('relatorioTurma');
  if (rel) {
    relDiv.style.display = 'block';
    relDiv.innerHTML = `
      <h2 style="margin-top:0;">${rel.titulo}</h2>
      <div><b>Alunos:</b> ${rel.alunos}</div>
      <div><b>Professor:</b> ${rel.professor}</div>
      <div><b>Quantidade de aulas:</b> ${rel.quantidade_aulas}</div>
      <div><b>Primeira vez nesta turma:</b> ${rel.funil.primeira_vez} de ${rel.funil.total}
        (${(rel.funil.proporcao_primeira_vez * 100).toFixed(0)}%)
        ${rel.funil.proporcao_primeira_vez < 0.3 ? ' <span style="color:var(--danger)">⚠ baixo — avalie se compensa abrir a próxima turma da sequência</span>' : ''}
      </div>
      <table>
        <thead><tr><th>Nome</th><th>Categoria</th><th>Frequência</th><th>Sugestão</th></tr></thead>
        <tbody>
          ${rel.tabela.map(l => `<tr><td>${l.nome}</td><td>${l.categoria}</td><td>${l.resumo}</td><td class="sugestao">${l.sugestao || '—'}</td></tr>`).join('')}
        </tbody>
      </table>
      <div style="margin-top:12px;"><b>Observações:</b> ${rel.observacoes}</div>
    `;
  }

  const lista = document.getElementById('listaAlunos');
  lista.innerHTML = d.alunos.map(a => `
    <div class="card aluno-card" id="card-${a.id_aluno}">
      <b>${a.nome_fuctura}</b>
      <div class="detalhe">
        <button class="acao" onclick="analisar('${a.id_aluno}')">Conferir</button>
      </div>
    </div>
  `).join('');
}

async function analisar(idAluno) {
  const card = document.getElementById('card-' + idAluno);
  card.querySelector('.detalhe').innerHTML = 'Conferindo situação acadêmica e financeira (lendo o Fuctura)...';
  const r = await fetch(`/api/fechamento/aluno/${jobAtual}/${idAluno}/previa`);
  const d = await r.json();
  if (d.erro) { card.querySelector('.detalhe').innerHTML = 'Erro: ' + d.erro; return; }
  if (d.ja_fechado) {
    card.classList.add('ok');
    card.querySelector('.detalhe').innerHTML = 'Este aluno já tem fechamento registrado para esta turma — nada a fazer.';
    contagem.ja_fechados++;
    return;
  }
  const banner = (txt, cor) => txt ? `<div style="margin-bottom:10px; padding:8px 10px; border-radius:var(--radius-sm); background:var(--${cor}-bg); color:var(--${cor}); font-weight:600;">${txt}</div>` : '';
  const temFin = !!d.financeiro_relevante;
  const blocoFin = temFin
    ? `<div style="margin-top:6px;"><b>Financeiro</b> <span class="fonte">(pode editar antes de confirmar)</span>:</div>
       <textarea id="financeiro-${idAluno}" rows="3" style="width:100%; font-family:monospace; font-size:0.85rem;"></textarea>`
    : `<div style="margin-top:6px;"><b>Financeiro:</b> <span class="fonte">${d.resumo_financeiro} — sem questão financeira, nenhum comentário financeiro será gravado.</span></div>`;
  card.querySelector('.detalhe').innerHTML = `
    ${banner(d.devedor_nota, 'danger')}
    ${banner(d.nota_faltas_finais, 'danger')}
    ${(d.perfil && (d.perfil.status || '').toLowerCase() === 'ex-aluno' && !d.ex_aluno_de_verdade)
      ? banner('⚠ Fuctura mostra "Ex-aluno" mas o histórico não confirma que completou todos os módulos da trilha (Java: J1/J2/JS3/JA4, Python: PY1-4) — confira antes de considerar concluído.', 'danger')
      : ''}
    ${d.ex_aluno_de_verdade ? banner('✓ Completou todos os módulos da trilha — Ex-aluno confirmado de verdade.', 'success') : ''}
    ${banner(d.nota_online, 'warning')}
    ${d.modalidade ? `<div class="fonte" style="margin-bottom:8px;">Modalidade no roster: <b>${d.modalidade}</b></div>` : ''}
    <div><b>Acadêmico</b> <span class="fonte">(pode editar antes de confirmar)</span>:</div>
    <textarea id="academico-${idAluno}" rows="4" style="width:100%; font-family:monospace; font-size:0.85rem;"></textarea>
    ${blocoFin}
    <div style="margin-top:10px;">
      <button class="btn-sim" id="btConfirmar-${idAluno}" onclick="confirmarGravar('${idAluno}')">Sim, gravar fechamento</button>
      <button class="btn-nao" onclick="confirmar('${idAluno}', false)">Pular este aluno</button>
    </div>
    <div style="margin-top:16px; padding-top:14px; border-top:1px solid var(--border);">
      <b>Situação do cadastro</b> <span class="fonte">(separado do fechamento acima — só mexe se você escolher)</span>
      <p class="fonte" style="margin:4px 0 8px;">Situação atual: <b>${d.perfil.status || '(não definido)'}</b>. Trocar aqui muda o campo "Situação" do cadastro do aluno no Fuctura e grava sozinho um comentário de rastreabilidade (de X pra Y). Deixe em branco se não quiser mexer.</p>
      <select id="situacao-${idAluno}">
        <option value="">— não alterar —</option>
        ${Object.entries(STATUS_ALUNO_OPCOES).map(([valor, rotulo]) => `<option value="${valor}">${rotulo}</option>`).join('')}
      </select>
      <input type="text" id="motivoSituacao-${idAluno}" placeholder="Motivo (opcional)" style="margin-top:8px;">
      <div style="margin-top:8px;">
        <button class="btn-nao" id="btSituacao-${idAluno}" onclick="atualizarSituacaoFechamento('${idAluno}')">Atualizar Situação</button>
      </div>
      <div id="msgSituacao-${idAluno}" style="margin-top:8px;"></div>
    </div>
  `;
  // preenche via .value (nao interpolado no HTML acima) - mesmo cuidado de
  // editarComentario() na Consulta de Alunos.
  const taAcad = document.getElementById('academico-' + idAluno);
  taAcad.value = d.resumo_academico; taAcad.dataset.original = d.resumo_academico;
  const taFin = document.getElementById('financeiro-' + idAluno);
  if (taFin) { taFin.value = d.resumo_financeiro; taFin.dataset.original = d.resumo_financeiro; }
}

async function atualizarSituacaoFechamento(idAluno) {
  const select = document.getElementById('situacao-' + idAluno);
  const novoStatus = select.value;
  const msg = document.getElementById('msgSituacao-' + idAluno);
  if (!novoStatus) { msg.innerHTML = '<span style="color:var(--danger)">Escolha uma situação antes de atualizar.</span>'; return; }
  const btn = document.getElementById('btSituacao-' + idAluno);
  // mesma confirmacao dupla ja usada no resto do sistema - primeiro clique
  // so arma, segundo grava de verdade.
  if (btn.dataset.armado !== '1') {
    btn.textContent = 'Clique de novo pra confirmar: ' + select.options[select.selectedIndex].text;
    btn.dataset.armado = '1';
    return;
  }
  const motivo = document.getElementById('motivoSituacao-' + idAluno).value.trim();
  msg.textContent = 'Atualizando...';
  const r = await fetch(`/api/aluno/${idAluno}/situacao`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ novo_status: novoStatus, motivo }),
  });
  const d = await r.json();
  btn.dataset.armado = '0';
  btn.textContent = 'Atualizar Situação';
  if (d.erro) { msg.innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  msg.innerHTML = `<span style="color:var(--success)">Situação alterada de "${d.situacao_anterior}" para "${d.situacao_nova}".</span>`;
}

function confirmarGravar(idAluno) {
  const taAcad = document.getElementById('academico-' + idAluno);
  const taFin = document.getElementById('financeiro-' + idAluno);  // null quando não há questão financeira
  const btn = document.getElementById('btConfirmar-' + idAluno);
  const editado = taAcad.value.trim() !== taAcad.dataset.original.trim()
    || (taFin && taFin.value.trim() !== taFin.dataset.original.trim());
  // pedido do usuario (2026-09-09): editar o resumo sugerido exige uma
  // confirmacao A MAIS - primeiro clique so "arma", segundo clique grava.
  if (editado && btn.dataset.armado !== '1') {
    btn.textContent = 'Texto alterado — clique de novo pra confirmar e gravar';
    btn.dataset.armado = '1';
    return;
  }
  confirmar(idAluno, true, taAcad.value, taFin ? taFin.value : '');
}

async function confirmar(idAluno, sim, resumoAcademico, resumoFinanceiro) {
  const card = document.getElementById('card-' + idAluno);
  card.querySelector('.detalhe').innerHTML = 'Processando...';
  const body = { confirmado: sim };
  if (sim) { body.resumo_academico = resumoAcademico; body.resumo_financeiro = resumoFinanceiro; }
  const r = await fetch(`/api/fechamento/aluno/${jobAtual}/${idAluno}/confirmar`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const d = await r.json();
  if (d.erro) { card.querySelector('.detalhe').innerHTML = 'Erro: ' + d.erro; return; }
  if (!d.gravado) {
    card.classList.add('ok');
    card.querySelector('.detalhe').innerHTML = d.motivo ? ('Não gravado — ' + d.motivo) : 'Pulado.';
    if (d.motivo) contagem.ja_fechados++;
    return;
  }
  contagem.fechados_agora++;
  card.classList.add('ok');
  card.querySelector('.detalhe').innerHTML = `
    <div><b>Acadêmico${d.academico_editado ? ' (editado — marcado -MOD-)' : ''}:</b> ${d.resumo_academico}</div>
    ${d.financeiro_gravado
      ? `<div style="margin-top:6px;"><b>Financeiro${d.financeiro_editado ? ' (editado — marcado -MOD-)' : ''}:</b> ${d.resumo_financeiro}</div>`
      : `<div style="margin-top:6px;" class="fonte">Nenhum comentário financeiro gravado (sem questão financeira).</div>`}
  `;
  atualizarResumo();
}

function atualizarResumo() {
  const div = document.getElementById('resumoFinal');
  div.style.display = 'block';
  div.innerHTML = `
    <b>Resumo da turma</b><br>
    Total de alunos: ${contagem.total}<br>
    Fechados agora: ${contagem.fechados_agora}
  `;
}
</script></body></html>"""


RECONCILIACAO_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Reconciliação de Devedores</title>\n<style>" + BASE_CSS + """
  #limite, #diasMinimos, #diasMaximos { width:140px; display:inline-block; }
  #resumo { background:var(--success-bg); border:1px solid #cfe0d6; }
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1>Reconciliação de Devedores</h1>
<p class="subtitulo">Confere Situação x Turma de Controle pra todo devedor com dívida a partir do prazo escolhido.
Nada é gravado sozinho: o comentário de análise, a mudança de status e a matrícula em turma de controle
só acontecem com sua confirmação, um aluno por vez. Antes de gravar um comentário, o sistema avisa se já existe
uma análise anterior pra aquele aluno.</p>

<div class="card">
  <label>Limite de alunos a analisar (deixe em branco pra rodar todos os devedores)</label>
  <input type="number" id="limite" placeholder="ex: 50" min="1">
  <br><br>
  <label>Prazo mínimo de dívida em aberto, em dias (padrão 365, ~1 ano)</label>
  <input type="number" id="diasMinimos" placeholder="365" min="1" value="365">
  <button class="btn-nao" type="button" onclick="document.getElementById('diasMinimos').value = 45" style="margin-left:8px;">45 dias (regra oficial p/ Devedor/Pendência)</button>
  <br><br>
  <label>Prazo máximo de dívida em aberto, em dias (padrão 1825, ~5 anos — prazo legal de cobrança; além disso a dívida provavelmente prescreveu)</label>
  <input type="number" id="diasMaximos" placeholder="1825" min="1" value="1825">
  <br><br>
  <button class="acao" onclick="iniciar()">Rodar análise</button>
  <span id="status"></span>
</div>

<div id="resumo" class="card" style="display:none;"></div>
<div id="listaAcoes"></div>
</div>

<script>""" + BASE_JS + """
let jobAtual = null;

async function iniciar() {
  const limite = document.getElementById('limite').value;
  const diasMinimos = document.getElementById('diasMinimos').value;
  const diasMaximos = document.getElementById('diasMaximos').value;
  document.getElementById('status').textContent = ' Rodando... isso pode demorar alguns minutos.';
  document.getElementById('listaAcoes').innerHTML = '';
  document.getElementById('resumo').style.display = 'none';

  const r = await fetch('/api/reconciliacao/iniciar', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ limite: limite || null, dias_minimos: diasMinimos || null, dias_maximos: diasMaximos || null }),
  });
  const d = await r.json();
  document.getElementById('status').textContent = '';
  if (d.erro) { alert(d.erro); return; }

  jobAtual = d.job_id;

  const resumoDiv = document.getElementById('resumo');
  resumoDiv.style.display = 'block';
  let linhasResumo = Object.entries(d.resumo_por_caso).map(([caso, qtd]) => `${caso}: ${qtd}`).join('<br>');
  resumoDiv.innerHTML = `
    <b>Total analisado: ${d.total_analisados}</b><br>${linhasResumo}
    <div style="margin-top:12px; padding-top:12px; border-top:1px solid var(--border);">
      <b>Relatórios em PDF</b> (a partir desta análise que acabou de rodar)
      <div style="margin-top:8px; display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
        <a class="btn-nao" style="text-decoration:none; display:inline-block;" href="/api/reconciliacao/${jobAtual}/pdf/devedores" target="_blank">Relatório de Devedores (PDF)</a>
        <input type="text" id="periodoFechamento" placeholder="Período (opcional, ex: Setembro/2026)" style="max-width:220px;">
        <a class="btn-nao" style="text-decoration:none; display:inline-block;" href="#" onclick="abrirFechamentoPdf(); return false;">Relatório de Fechamento (PDF)</a>
      </div>
    </div>
  `;

  const lista = document.getElementById('listaAcoes');
  if (!d.itens.length) {
    lista.innerHTML = '<div class="card">Nenhum caso a revisar nesta rodada (todo mundo veio "não elegível").</div>';
    return;
  }

  lista.innerHTML = d.itens.map(a => `
    <div class="card aluno-card" id="item-${a.id_aluno}">
      <b>${a.nome}</b> — <a href="/alunos?id=${a.id_aluno}" target="_blank">matrícula ${a.matricula || '?'}</a><br>
      Caso: ${a.caso} | Valor final: R$ ${a.valor_final.toFixed(2).replace('.', ',')}<br>
      <span class="fonte">Referência de vencimento: ${a.vencimento_referencia || 'desconhecida'} (fonte: ${a.fonte_vencimento})</span><br>
      <span class="fonte">${a.resumo_comentarios || ''}</span><br>
      ${a.data_analise_anterior ? `<span class="fonte">⚠ Já existe uma análise anterior deste aluno, de ${a.data_analise_anterior}.</span><br>` : ''}
      ${a.acao_sugerida ? `<b>Ação sugerida:</b> ${descreverAcao(a.acao_sugerida)}<br>` : ''}
      <label class="fonte" style="display:block; margin-top:10px; margin-bottom:4px;">Comentário de análise a gravar — pode editar antes de confirmar:</label>
      <textarea id="texto-${a.id_aluno}" rows="6" style="width:100%; font-family:monospace; font-size:0.85rem;"></textarea>
      <br><br>
      <button class="btn-sim" id="btGravar-${a.id_aluno}" onclick="gravarComentario('${a.id_aluno}')">Gravar comentário de análise</button>
      ${a.acao_sugerida ? `<button class="btn-sim" onclick="aplicar('${a.id_aluno}')">Aplicar ação</button>` : ''}
      <button class="btn-nao" onclick="verBoletosCora('${a.id_aluno}')">Ver boletos em aberto (CORA)</button>
      <button class="btn-nao" onclick="verPreviaEmail('${a.id_aluno}')">Ver prévia do e-mail</button>
      <button class="btn-nao" onclick="toggleContato('${a.id_aluno}')">Registrar contato</button>
      <a class="btn-nao" style="text-decoration:none; display:inline-block;" href="/api/reconciliacao/${jobAtual}/pdf/individual/${a.id_aluno}" target="_blank">Relatório Individual (PDF)</a>
      <div id="msg-${a.id_aluno}"></div>
      <div id="cora-${a.id_aluno}" class="fonte" style="margin-top:8px;"></div>
      <div id="email-${a.id_aluno}" style="margin-top:8px;"></div>
      <div id="contato-${a.id_aluno}" style="display:none; margin-top:8px; padding-top:8px; border-top:1px solid var(--border);">
        <label>Resultado do contato</label>
        <select id="contatoResultado-${a.id_aluno}">
          <option value="Não respondeu">Não respondeu</option>
          <option value="Bloqueou mensagem">Bloqueou mensagem</option>
          <option value="Respondeu — vai pagar">Respondeu — vai pagar</option>
          <option value="Respondeu — não vai pagar">Respondeu — não vai pagar</option>
          <option value="Outro">Outro (ver observação)</option>
        </select>
        <label style="margin-top:8px;">Observação (opcional)</label>
        <input type="text" id="contatoObs-${a.id_aluno}" placeholder="Ex: ligou, caixa postal">
        <div style="margin-top:8px;">
          <button class="acao" onclick="registrarContato('${a.id_aluno}')">Salvar ocorrência</button>
        </div>
        <div id="contatoMsg-${a.id_aluno}" style="margin-top:6px;"></div>
      </div>
    </div>
  `).join('');

  // preenche via .value (nao interpolado no HTML acima) pra nao correr
  // risco nenhum de um texto de historico real quebrar o template -
  // mesmo cuidado que editarComentario() ja toma na Consulta de Alunos.
  d.itens.forEach(a => {
    const ta = document.getElementById('texto-' + a.id_aluno);
    ta.value = a.texto_comentario;
    ta.dataset.original = a.texto_comentario;
  });
}

function descreverAcao(acao) {
  if (acao.tipo === 'mudar_status') return `Mudar Situação para ${acao.novo_status_nome}`;
  if (acao.tipo === 'matricular_turma') return `Matricular em ${acao.turma_nome}`;
  return acao.tipo;
}

function abrirFechamentoPdf() {
  const periodo = document.getElementById('periodoFechamento').value.trim();
  const url = `/api/reconciliacao/${jobAtual}/pdf/fechamento` + (periodo ? `?periodo=${encodeURIComponent(periodo)}` : '');
  window.open(url, '_blank');
}

async function gravarComentario(idAluno, ignorarAnaliseAnterior) {
  const ta = document.getElementById('texto-' + idAluno);
  const btn = document.getElementById('btGravar-' + idAluno);
  const editado = ta.value.trim() !== ta.dataset.original.trim();
  // pedido do usuario (2026-09-09): editar o texto sugerido exige uma
  // confirmacao A MAIS - o primeiro clique so "arma" o botao, so o segundo
  // grava de verdade. Se o texto nao foi alterado, funciona como sempre
  // (um clique grava).
  if (editado && btn.dataset.armado !== '1') {
    btn.textContent = 'Texto alterado — clique de novo pra confirmar e gravar';
    btn.dataset.armado = '1';
    return;
  }
  const msgDiv = document.getElementById('msg-' + idAluno);
  const r = await fetch(`/api/reconciliacao/${jobAtual}/aluno/${idAluno}/comentario`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ confirmado: true, ignorar_analise_anterior: !!ignorarAnaliseAnterior, texto: ta.value }),
  });
  const d = await r.json();
  if (d.erro) { alert(d.erro); return; }

  if (d.ja_existe_analise) {
    msgDiv.innerHTML = `
      <div class="fonte">⚠ Já existe uma análise de ${d.data_analise_anterior} pra este aluno.</div>
      <button class="btn-sim" onclick="gravarComentario('${idAluno}', true)">Gravar mesmo assim</button>
    `;
    return;
  }
  if (d.gravado) {
    msgDiv.innerHTML = d.editado ? '<b>✓ Comentário gravado (texto editado — marcado -MOD- no Fuctura).</b>' : '<b>✓ Comentário gravado.</b>';
    document.getElementById('item-' + idAluno).classList.add('feito');
  } else {
    msgDiv.innerHTML = '<b>✗ Falhou ao gravar o comentário.</b>';
  }
}

async function aplicar(idAluno) {
  const msgDiv = document.getElementById('msg-' + idAluno);
  const r = await fetch(`/api/reconciliacao/${jobAtual}/aluno/${idAluno}/aplicar`, { method: 'POST' });
  const d = await r.json();
  if (d.erro) { alert(d.erro); return; }
  if (d.ok) {
    msgDiv.innerHTML += `<br><b>✓ ${d.descricao}</b>`;
    document.getElementById('item-' + idAluno).classList.add('feito');
  } else {
    alert('Falhou: ' + d.descricao);
  }
}

async function verBoletosCora(idAluno) {
  const div = document.getElementById('cora-' + idAluno);
  div.textContent = 'Consultando CORA...';
  const r = await fetch(`/api/aluno/${idAluno}/boletos-cora`);
  const d = await r.json();
  if (!d.configurado) {
    div.innerHTML = '⚠ CORA ainda não está configurado (ver tela de Configurações).' + (d.erro ? ` (${d.erro})` : '');
    return;
  }
  if (d.erro) { div.innerHTML = `Erro consultando a CORA: ${d.erro}`; return; }

  div.innerHTML = `
    <b>${d.resumo_texto}</b>
    ${d.boletos.length ? `
      <table style="margin-top:8px;">
        <thead><tr><th>Vencimento</th><th>Valor</th><th>Status</th></tr></thead>
        <tbody>
          ${d.boletos.map(b => `<tr><td>${b.vencimento || '—'}</td><td>R$ ${b.valor.toFixed(2).replace('.', ',')}</td><td>${b.status || '—'}</td></tr>`).join('')}
        </tbody>
      </table>
      <div style="margin-top:4px;"><b>Total: R$ ${d.total.toFixed(2).replace('.', ',')}</b></div>
    ` : ''}
  `;
}

let ultimosPreviewsEmail = {};  // idAluno -> ultima resposta de preview-email, pra "Abrir no Gmail" reaproveitar sem buscar de novo

async function verPreviaEmail(idAluno) {
  const div = document.getElementById('email-' + idAluno);
  div.innerHTML = '<div class="card">Montando prévia...</div>';
  const r = await fetch(`/api/aluno/${idAluno}/preview-email`);
  const d = await r.json();
  if (d.erro) { div.innerHTML = `<div class="card">Erro: ${d.erro}</div>`; return; }
  ultimosPreviewsEmail[idAluno] = d;

  const cora = !d.boletos_cora
    ? '<div class="fonte">⚠ CORA ainda não está configurado.</div>'
    : d.boletos_cora.erro
      ? `<div class="fonte">Erro consultando a CORA: ${d.boletos_cora.erro}</div>`
      : `
        <table>
          <thead><tr><th>Cliente</th><th>Valor</th><th>Vencimento</th></tr></thead>
          <tbody>
            ${d.boletos_cora.boletos.map(b => `<tr><td>${d.boletos_cora.aluno}</td><td>R$ ${b.valor.toFixed(2).replace('.', ',')}</td><td>${b.vencimento || '—'}</td></tr>`).join('')}
          </tbody>
        </table>
        <div style="margin-top:4px;"><b>Total: R$ ${d.boletos_cora.total.toFixed(2).replace('.', ',')}</b></div>
      `;

  div.innerHTML = `
    <div class="card">
      <h3 style="margin-top:0;">Prévia do e-mail (não enviado, não é rascunho ainda)</h3>
      <div class="fonte" style="margin-bottom:10px;">
        ⚠ Este e-mail é um <b>rascunho pro advogado</b> (controle de informações do caso) — nunca é enviado
        ao aluno/responsável. Para: <i>ainda pendente</i> (endereço do advogado/escritório não configurado).
      </div>
      <button class="btn-nao" onclick="abrirNoGmail('${idAluno}')">Abrir rascunho no Gmail</button>
      <div class="fonte" style="margin-top:4px;">Abre o Gmail com assunto e corpo já preenchidos — falta só completar o destinatário (endereço do advogado, ainda pendente) e anexar os arquivos manualmente (o link não carrega anexo). Nada é enviado sozinho.</div>

      <div id="gmail-anexo-area-${idAluno}" style="margin-top:10px; padding-top:10px; border-top:1px solid var(--border);">Verificando conexão com o Gmail...</div>
      <div id="gmail-anexo-msg-${idAluno}" style="margin-top:6px;"></div>

      <b style="display:block; margin-top:14px;">Dados do aluno (vão no corpo do e-mail assim)</b>
      <table style="margin-top:6px;">
        ${d.dados_pessoais.map(c => `<tr><th>${c.rotulo}</th><td>${c.valor}</td></tr>`).join('')}
      </table>

      ${d.responsavel_contrato.papel === 'responsavel' ? `
        <b style="display:block; margin-top:14px;">Responsável pelo contrato (quem assinou — informação extra pro advogado)</b>
        <table style="margin-top:6px;">
          <tr><th>Nome</th><td>${d.responsavel_contrato.nome}</td></tr>
          <tr><th>CPF</th><td>${d.responsavel_contrato.cpf}${d.responsavel_contrato.cpf_suspeito ? ' <span style="color:var(--danger)">⚠ não tem 11 dígitos — cadastro antigo, conferir/corrigir</span>' : ''}</td></tr>
          <tr><th>Telefone</th><td>${d.responsavel_contrato.telefone}</td></tr>
          <tr><th>Email</th><td>${d.responsavel_contrato.email}</td></tr>
        </table>
      ` : ''}

      <b style="display:block; margin-top:14px;">Resumo do aluno</b>
      <div class="fonte" style="margin-bottom:4px;">Pré-preenchido com o último contato registrado — edite antes de gerar o e-mail.</div>
      <textarea id="resumoAlunoEmail-${idAluno}" rows="3" style="width:100%; font-family:inherit;">${d.resumo_aluno_sugerido}</textarea>

      <b style="display:block; margin-top:14px;">Deve os meses</b>
      <div class="comentario" style="white-space:pre-wrap;">${d.deve_os_meses}</div>

      <b style="display:block; margin-top:14px;">Resumo — pagamentos e possíveis motivos de desistência (histórico completo, de apoio)</b>
      <div class="comentario" style="white-space:pre-wrap;">${d.resumo_pagamentos_desistencia}</div>

      <b style="display:block; margin-top:14px;">Valores em aberto (CORA)</b>
      ${cora}

      <b style="display:block; margin-top:14px;">Turmas em que o aluno está registrado</b>
      ${d.turmas.length ? d.turmas.map(t => `<div>${t.data} — ${t.nome}</div>`).join('') : '<div class="fonte">Nenhuma turma no cadastro.</div>'}

      <b style="display:block; margin-top:14px;">Anexos</b>
      ${d.anexos.length ? d.anexos.map(a => `<div>📎 ${a.nome_arquivo} (${(a.tamanho / 1024).toFixed(0)} KB)</div>`).join('') : '<div class="fonte">Nenhum anexo disponível.</div>'}
      ${d.avisos_anexos.length ? `<div class="fonte" style="margin-top:6px; color:var(--warning);">${d.avisos_anexos.map(a => '⚠ ' + a).join('<br>')}</div>` : ''}
    </div>
  `;
  renderizarAreaGmailAnexo(idAluno);
}

async function renderizarAreaGmailAnexo(idAluno) {
  const area = document.getElementById('gmail-anexo-area-' + idAluno);
  if (!area) return;
  const status = await statusGmail();
  if (!status.configurado) {
    area.innerHTML = '<div class="fonte">Criação de rascunho com anexo automático ainda não foi configurada pelo administrador.</div>';
  } else if (!status.conectado) {
    area.innerHTML =
      '<a class="btn-nao" style="text-decoration:none; display:inline-block;" href="/api/gmail/autorizar">Conectar meu Gmail (uma vez só, pra anexar automaticamente)</a>' +
      '<div class="fonte" style="margin-top:4px;">Depois de conectar, o botão "Criar rascunho com anexo" aparece aqui — junta contrato + atas automaticamente, sem precisar anexar na mão.</div>';
  } else {
    area.innerHTML =
      `<button class="acao" onclick="criarRascunhoComAnexo('${idAluno}')">Criar rascunho no Gmail com anexo automático</button>` +
      `<div class="fonte" style="margin-top:4px;">Conectado como ${status.email_conectado} — cria o rascunho já com contrato + atas anexados de verdade, via API do Gmail. Confira e complete o destinatário antes de enviar.</div>`;
  }
}

// Monta assunto/corpo do email jurídico a partir da prévia já carregada -
// reaproveitado tanto por abrirNoGmail (link tipo wa.me, sem anexo) quanto
// por criarRascunhoComAnexo (API de verdade, com anexo - ver mais abaixo).
// Formato alinhado com o modelo real que o Diógenes usa (exemplo trazido
// pelo usuário, 2026-09-16): "Devedor: NOME", dados direto (sem separar
// "responsável" quando não existe), "Resumo do aluno" (editável - nunca
// gerado por IA, regra travada 2026-09-07) e "Deve os meses" (sempre da
// CORA - fonte real de vencimento; placeholder pré-pronto até configurar).
function montarConteudoEmailJuridico(idAluno, d, comAnexoAutomatico) {
  const nome = (d.dados_pessoais.find(c => c.rotulo === 'Nome') || {}).valor || '(nome não encontrado)';
  const assunto = `Encaminhamento para análise jurídica — ${nome}`;
  const resumoAlunoTextarea = document.getElementById('resumoAlunoEmail-' + idAluno);
  const resumoAluno = resumoAlunoTextarea ? resumoAlunoTextarea.value.trim() : d.resumo_aluno_sugerido;

  const linhas = [
    'RASCUNHO - revisar antes de enviar. Preencha o destinatário (advogado/escritório)' +
      (comAnexoAutomatico ? ' antes de enviar.' : ' e anexe o contrato/ata manualmente - este link não carrega anexo nem destinatário sozinho.'),
    '',
    `Devedor: ${nome}`,
    '',
    ...d.dados_pessoais.map(c => `${c.rotulo}: ${c.valor}`),
    '',
  ];
  if (d.responsavel_contrato.papel === 'responsavel') {
    linhas.push(
      'Responsável pelo contrato (quem assinou):',
      `  Nome: ${d.responsavel_contrato.nome}`,
      `  CPF: ${d.responsavel_contrato.cpf}`,
      `  Telefone: ${d.responsavel_contrato.telefone}`,
      `  Email: ${d.responsavel_contrato.email}`,
      '',
    );
  }
  linhas.push(
    'Resumo do aluno:',
    resumoAluno,
    '',
    'Deve os meses:',
    '',
    d.deve_os_meses,
    '',
    'Resumo — pagamentos e possíveis motivos de desistência (histórico completo, de apoio):',
    d.resumo_pagamentos_desistencia,
    '',
  );
  linhas.push('Turmas em que o aluno está registrado:');
  if (d.turmas.length) linhas.push(...d.turmas.map(t => `  ${t.data} — ${t.nome}`));
  else linhas.push('  Nenhuma turma no cadastro.');
  if (d.anexos.length) {
    linhas.push('', comAnexoAutomatico ? 'Anexado automaticamente:' : 'Anexar manualmente:', ...d.anexos.map(a => `  - ${a.nome_arquivo}`));
  }
  return { assunto, corpo: linhas.join('\\n') };
}

// "Abrir no Gmail" (pedido do usuario, 2026-09-15: "algo como o wa.me,
// mas pro Gmail") - o Gmail tem uma URL de composicao que abre o rascunho
// JA PREENCHIDO (assunto + corpo) numa aba, sem enviar nada sozinho -
// mesmo espirito do link wa.me pro WhatsApp. So NAO carrega anexo (link
// nao suporta isso) nem o destinatario (ainda pendente, ver STATUS.md) -
// os dois ficam pra completar manualmente antes de mandar.
function abrirNoGmail(idAluno) {
  const d = ultimosPreviewsEmail[idAluno];
  if (!d) { alert('Monte a prévia do e-mail primeiro.'); return; }
  const { assunto, corpo } = montarConteudoEmailJuridico(idAluno, d, false);
  const url = `https://mail.google.com/mail/?view=cm&fs=1&su=${encodeURIComponent(assunto)}&body=${encodeURIComponent(corpo)}`;
  window.open(url, '_blank');
}

// Criar rascunho COM anexo de verdade (pedido do usuario, 2026-09-15) -
// usa a API oficial do Gmail (OAuth ja autorizado pelo proprio
// funcionario, ver gmail_client.py) - passa PELA autorizacao do Gmail,
// nunca burla nada. Anexos (contrato + atas) sao buscados de novo no
// servidor (mesma fonte da previa), nunca reenviados pelo navegador.
let gmailStatusCache = null;

async function statusGmail() {
  if (gmailStatusCache) return gmailStatusCache;
  const r = await fetch('/api/gmail/status');
  gmailStatusCache = await r.json();
  return gmailStatusCache;
}

async function criarRascunhoComAnexo(idAluno) {
  const d = ultimosPreviewsEmail[idAluno];
  if (!d) { alert('Monte a prévia do e-mail primeiro.'); return; }
  const msgDiv = document.getElementById('gmail-anexo-msg-' + idAluno);
  msgDiv.textContent = 'Criando rascunho (buscando anexos)...';
  const { assunto, corpo } = montarConteudoEmailJuridico(idAluno, d, true);
  const r = await fetch(`/api/aluno/${idAluno}/rascunho-gmail`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ assunto, corpo }),
  });
  const resp = await r.json();
  if (resp.erro) {
    msgDiv.innerHTML = `<span style="color:var(--danger)">${resp.erro}</span>`;
    return;
  }
  msgDiv.innerHTML = `<span style="color:var(--success)">Rascunho criado com ${resp.quantidade_anexos} anexo(s)! ` +
    `<a href="https://mail.google.com/mail/u/0/#drafts" target="_blank">Abrir rascunhos no Gmail</a></span>` +
    (resp.avisos_anexos.length ? `<div class="fonte" style="margin-top:4px;">${resp.avisos_anexos.map(a => '⚠ ' + a).join('<br>')}</div>` : '');
}

// Registro de ocorrencia de contato (pedido do Diogenes via Caio,
// 2026-09-14): "aluno nao responde"/"bloqueou mensagem" precisam ficar
// registrados em algum lugar pra a pergunta "quem nao responde aos
// contatos" ter resposta. Reaproveita o MESMO endpoint generico de
// comentario manual ja usado na Consulta de Alunos (/api/aluno/<id>/
// comentario) - nao e um mecanismo novo, so um atalho com um titulo fixo
// ("Ocorrência de Contato") pra ficar facil de identificar depois.
function toggleContato(idAluno) {
  const div = document.getElementById('contato-' + idAluno);
  div.style.display = div.style.display === 'none' ? 'block' : 'none';
}

async function registrarContato(idAluno) {
  const resultado = document.getElementById('contatoResultado-' + idAluno).value;
  const obs = document.getElementById('contatoObs-' + idAluno).value.trim();
  const msg = document.getElementById('contatoMsg-' + idAluno);
  const hoje = new Date().toLocaleDateString('pt-BR');
  let texto = `Resultado: ${resultado}. Data: ${hoje}.`;
  if (obs) texto += ` Observação: ${obs}`;

  msg.textContent = 'Registrando...';
  const r = await fetch(`/api/aluno/${idAluno}/comentario`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ tipo: '3', assunto: 'Ocorrência de Contato', descricao: texto }),
  });
  const d = await r.json();
  if (d.erro) { msg.innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  msg.innerHTML = `<span style="color:var(--success)">Ocorrência registrada!</span>`;
}
</script></body></html>"""


ALUNOS_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Consulta de Alunos</title>\n<style>" + BASE_CSS + """
  th { width:160px; }
  .autocomplete-box.pequeno { max-width:360px; }
  .campo { margin-top: 14px; }
  fieldset { border:1px solid var(--border); border-radius:var(--radius-sm); padding:14px; margin-top:6px; }
  legend { padding:0 6px; font-size:0.8rem; color:var(--text-muted); }
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1>Consulta de Alunos</h1>
<p class="subtitulo">Busca por nome, mostra o cadastro completo e permite registrar um comentário. Nenhum outro dado é alterado por esta tela.
<a href="/alunos/novo">+ Cadastrar novo aluno</a></p>

<div class="card">
  <label>Nome do aluno</label>
  <div class="autocomplete-box">
    <input type="text" id="busca" placeholder="Digite o nome..." autocomplete="off">
    <div id="resultadosBusca" class="autocomplete-list"></div>
  </div>
</div>

<div id="detalheAluno"></div>
</div>

<script>""" + BASE_JS + """
const STATUS_ALUNO_OPCOES = {
  "4": "-Matriculado", "30": "Advogado", "5": "Cancelado", "29": "Cliente sem Interesse",
  "26": "Convenio", "7": "Devedor", "22": "Ex-aluno", "19": "Interessado",
  "6": "Pediu Pausa", "31": "Provi/Pravaler",
};

let debounceId = null;
let comentariosAtuais = [];
// so comentarios com tipo "manual" (ver TIPOS_COMENTARIO_MANUAL no backend)
// podem ser editados por aqui - -Matricula, -Pagamento Realizado etc. tem
// efeito financeiro/estrutural real no Fuctura e nao devem ser editados por
// um formulario generico sem tratamento especifico (mesma cautela ja usada
// em reconciliacao_devedor.aplicar_acao pra nao reaproveitar formulario
// errado pra tipo com efeito colateral).
const TIPO_LABEL_PARA_CODIGO = {"-Comentário": "3", "Importante": "1", "Urgente": "2", "Aguardando Turma": "14"};

document.getElementById('busca').addEventListener('input', (e) => {
  const q = e.target.value;
  clearTimeout(debounceId);
  if (q.length < 3) { document.getElementById('resultadosBusca').innerHTML = ''; return; }
  debounceId = setTimeout(() => buscar(q), 300);
});

async function buscar(q) {
  const r = await fetch('/api/aluno/buscar?q=' + encodeURIComponent(q));
  const d = await r.json();
  const div = document.getElementById('resultadosBusca');
  if (d.erro) { div.innerHTML = `<div class="item">Erro: ${d.erro}</div>`; return; }
  if (!d.length) { div.innerHTML = '<div class="item">Nenhum resultado.</div>'; return; }
  div.innerHTML = d.map(a => `
    <div class="item" onclick="selecionar('${a.id_aluno}')">
      <b>${a.nome}</b> ${a.matricula ? '— matrícula ' + a.matricula : ''} ${a.celular ? '— ' + a.celular : ''} ${a.unidade ? '— ' + a.unidade : ''}
    </div>
  `).join('');
}

async function selecionar(idAluno) {
  document.getElementById('resultadosBusca').innerHTML = '';
  document.getElementById('busca').value = '';
  document.getElementById('detalheAluno').innerHTML = '<div class="card">Carregando...</div>';

  const r = await fetch(`/api/aluno/${idAluno}/detalhe`);
  const d = await r.json();
  const div = document.getElementById('detalheAluno');
  if (d.erro) { div.innerHTML = `<div class="card">Erro: ${d.erro}</div>`; return; }

  const c = d.cadastro;
  const f = d.financeiro;
  const fmt = (v) => 'R$ ' + (v ?? 0).toFixed(2).replace('.', ',');

  div.innerHTML = `
    <div class="card">
      <div style="display:flex; gap:16px; align-items:flex-start; margin-bottom:10px;">
        <div style="width:110px; height:110px; border-radius:8px; overflow:hidden; background:var(--surface-alt); border:1px solid var(--border); display:flex; align-items:center; justify-content:center; flex-shrink:0;">
          <img id="fotoImg" src="/api/aluno/${idAluno}/foto?t=${Date.now()}" style="width:100%; height:100%; object-fit:cover;" onerror="this.hidden=true; document.getElementById('fotoPlaceholder').hidden=false;">
          <div id="fotoPlaceholder" class="fonte" style="text-align:center; padding:8px;" hidden>Sem foto</div>
        </div>
        <div>
          <h2 style="margin-top:0; margin-bottom:8px;">${c.nome}</h2>
          <input type="file" id="fotoInput" accept="image/jpeg,image/png,image/webp" hidden onchange="enviarFoto('${idAluno}')">
          <button class="acao" style="font-size:0.78rem; padding:5px 12px;" onclick="document.getElementById('fotoInput').click()">Trocar foto</button>
          <button class="btn-nao" style="font-size:0.78rem; padding:5px 12px;" onclick="removerFoto('${idAluno}')">Remover foto</button>
          <div id="fotoStatus" class="fonte" style="margin-top:4px;"></div>
        </div>
      </div>
      <table>
        <tr><th>Situação</th><td>${d.status_label}</td></tr>
        <tr><th>Matrícula</th><td>${c.matricula}</td></tr>
        <tr><th>CPF</th><td>${c.cpf || '—'}</td></tr>
        <tr><th>E-mail</th><td>${c.email || '—'}</td></tr>
        <tr><th>Celular</th><td>${c.celular || '—'}</td></tr>
        <tr><th>Endereço</th><td>${c.endereco || '—'}${c.numero ? ', ' + c.numero : ''} ${c.complemento || ''}</td></tr>
        <tr><th>Bairro / Cidade</th><td>${c.bairro || '—'} / ${c.cidade || '—'} — ${c.estado || ''}</td></tr>
        <tr><th>CEP</th><td>${c.cep || '—'}</td></tr>
        <tr><th>Nascimento</th><td>${c.dataNascimento || '—'}</td></tr>
      </table>
      <div style="margin-top:14px; display:flex; gap:10px; flex-wrap:wrap;">
        <a class="acao" style="text-decoration:none; display:inline-block;" href="/api/aluno/${idAluno}/contrato" target="_blank">Ver Contrato (PDF)</a>
        <a class="acao" style="text-decoration:none; display:inline-block;" href="/api/aluno/${idAluno}/ficha" target="_blank">Ver Ficha (PDF)</a>
        <button class="btn-nao" onclick="toggleEditarCadastro()">Editar cadastro</button>
      </div>
      ${d.certificado.elegivel_java || d.certificado.elegivel_python || d.certificado.elegivel_biblia3d ? `
      <div style="margin-top:10px; display:flex; gap:10px; flex-wrap:wrap;">
        ${d.certificado.elegivel_java ? `<a class="acao" style="text-decoration:none; display:inline-block; background:var(--success);" href="/api/aluno/${idAluno}/certificado/java" target="_blank">Gerar Certificado — Java (PDF)</a>` : ''}
        ${d.certificado.elegivel_python ? `<a class="acao" style="text-decoration:none; display:inline-block; background:var(--success);" href="/api/aluno/${idAluno}/certificado/python" target="_blank">Gerar Certificado — Python (PDF)</a>` : ''}
        ${d.certificado.elegivel_biblia3d ? `<a class="acao" style="text-decoration:none; display:inline-block; background:var(--success);" href="/api/aluno/${idAluno}/certificado-biblia3d" target="_blank">Gerar Certificado — Bíblia 3D (PDF)</a>` : ''}
      </div>` : ''}
    </div>

    <div class="card">
      <h3 style="margin-top:0;">Mudar Situação</h3>
      <p class="fonte">Fora do fluxo de Reconciliação — uso manual (ex: aluno pediu pausa, cancelou por conta própria). Sempre grava um comentário de rastreabilidade.</p>
      <label>Nova Situação</label>
      <select id="novaSituacao">
        ${Object.entries(STATUS_ALUNO_OPCOES).map(([valor, rotulo]) =>
          `<option value="${valor}" ${c.status === valor ? 'selected' : ''}>${rotulo}${c.status === valor ? ' (atual)' : ''}</option>`
        ).join('')}
      </select>
      <label style="margin-top:12px;">Motivo (opcional, mas recomendado)</label>
      <input type="text" id="motivoSituacao" placeholder="Ex: aluno ligou pedindo pausa">
      <div style="margin-top:16px;">
        <button class="acao" onclick="mudarSituacao('${idAluno}')">Alterar Situação</button>
      </div>
      <div id="msgSituacao" style="margin-top:10px;"></div>
    </div>

    <div class="card" id="cardEditarCadastro" style="display:none;">
      <h3 style="margin-top:0;">Editar cadastro</h3>
      <p class="fonte">Não mexe em matrícula, Situação ou turma — só contato, endereço e responsável.</p>

      <fieldset>
        <legend>Contato</legend>
        <div class="campo"><label>Nome</label><input type="text" id="edNome" value="${c.nome || ''}"></div>
        <div class="campo"><label>E-mail</label><input type="text" id="edEmail" value="${c.email || ''}"></div>
        <div class="campo"><label>Telefone fixo</label><input type="text" id="edFixo" value="${c.fixo || ''}"></div>
        <div class="campo"><label>Celular</label><input type="text" id="edCelular" value="${c.celular || ''}"></div>
        <div class="campo"><label>CPF</label><input type="text" id="edCpf" maxlength="14" placeholder="000.000.000-00" value="${c.cpf || ''}"></div>
        <div class="campo"><label>Identidade</label><input type="text" id="edIdentidade" value="${c.identidade || ''}"></div>
        <div class="campo"><label>Data de nascimento</label><input type="text" id="edDataNascimento" placeholder="dd/mm/aaaa" value="${c.dataNascimento || ''}"></div>
        <div class="campo"><label>Sexo</label><select id="edSexo">
          <option value="" ${!c.sexo ? 'selected' : ''}>Selecione</option>
          <option value="M" ${c.sexo === 'M' ? 'selected' : ''}>Masculino</option>
          <option value="F" ${c.sexo === 'F' ? 'selected' : ''}>Feminino</option>
        </select></div>
      </fieldset>

      <fieldset style="margin-top:12px;">
        <legend>Endereço</legend>
        <div class="campo"><label>Endereço</label><input type="text" id="edEndereco" value="${c.endereco || ''}"></div>
        <div class="campo"><label>Número</label><input type="text" id="edNumero" value="${c.numero || ''}"></div>
        <div class="campo"><label>Complemento</label><input type="text" id="edComplemento" value="${c.complemento || ''}"></div>
        <div class="campo"><label>Bairro</label><input type="text" id="edBairro" value="${c.bairro || ''}"></div>
        <div class="campo"><label>Cidade</label><input type="text" id="edCidade" value="${c.cidade || ''}"></div>
        <div class="campo"><label>Estado (UF)</label><input type="text" id="edEstado" maxlength="2" value="${c.estado || ''}"></div>
        <div class="campo"><label>CEP</label><input type="text" id="edCep" value="${c.cep || ''}"></div>
      </fieldset>

      <fieldset style="margin-top:12px;">
        <legend>Responsável</legend>
        <div class="campo"><label>Nome do responsável</label><input type="text" id="edNomeResponsavel" value="${c.nomeResponsavel || ''}"></div>
        <div class="campo"><label>Telefone do responsável</label><input type="text" id="edTelefoneResponsavel" value="${c.telefoneResponsavel || ''}"></div>
        <div class="campo"><label>Celular do responsável</label><input type="text" id="edCelularResponsavel" value="${c.celularResponsavel || ''}"></div>
        <div class="campo"><label>CPF do responsável</label><input type="text" id="edCpfResponsavel" maxlength="14" placeholder="000.000.000-00" value="${c.cpfResponsavel || ''}"></div>
        <div class="campo"><label>Identidade do responsável</label><input type="text" id="edIdentidadeResponsavel" value="${c.identidadeResponsavel || ''}"></div>
        <div class="campo"><label>E-mail do responsável</label><input type="text" id="edEmailResponsavel" value="${c.emailResponsavel || ''}"></div>
      </fieldset>

      <div style="margin-top:16px;">
        <button class="acao" onclick="salvarCadastro('${idAluno}')">Salvar cadastro</button>
      </div>
      <div id="msgEditarCadastro" style="margin-top:10px;"></div>
    </div>

    <div class="card">
      <h3 style="margin-top:0;">Financeiro</h3>
      <table>
        <tr><th>Contratado</th><td>${fmt(f.contratado)}</td></tr>
        <tr><th>Recebido</th><td>${fmt(f.recebido)}</td></tr>
        <tr><th>Diferença</th><td>${fmt(f.diferenca)}</td></tr>
      </table>
    </div>

    <div class="card">
      <h3 style="margin-top:0;">Turmas atuais</h3>
      ${d.turmas_atuais.length ? d.turmas_atuais.map(t => `<div>${t.data} — ${t.nome}</div>`).join('') : '<div>Nenhuma turma no cadastro.</div>'}
    </div>

    <div class="card">
      <h3 style="margin-top:0;">Novo comentário</h3>
      <label>Tipo</label>
      <select id="novoComentarioTipo">
        <option value="3">-Comentário</option>
        <option value="1">Importante</option>
        <option value="2">Urgente</option>
        <option value="14">Aguardando Turma</option>
      </select>

      <label style="margin-top:12px;">Assunto</label>
      <input type="text" id="novoComentarioAssunto" placeholder="Ex: LIGAÇÃO DE COBRANÇA">

      <label style="margin-top:12px;">Descrição</label>
      <textarea id="novoComentarioDescricao" rows="4" style="width:100%;" placeholder="O que aconteceu..."></textarea>

      <label style="margin-top:12px;">Turma (opcional)</label>
      <div class="autocomplete-box pequeno">
        <input type="text" id="novoComentarioBuscaTurma" placeholder="Digite pra vincular uma turma..." autocomplete="off">
        <div id="novoComentarioResultadosTurma" class="autocomplete-list"></div>
      </div>
      <input type="hidden" id="novoComentarioTurmaId">
      <div id="novoComentarioTurmaEscolhida" style="margin-top:4px; font-size:0.85rem; color:var(--text-muted);"></div>

      <div style="margin-top:16px;">
        <button class="acao" onclick="salvarComentario('${idAluno}')">Salvar comentário</button>
      </div>
      <div id="novoComentarioMsg" style="margin-top:10px;"></div>
    </div>

    <div class="card" id="cardEditarComentario" style="display:none;">
      <h3 style="margin-top:0;">Editar comentário</h3>
      <p class="fonte">O Fuctura não tem exclusão de comentário — editar é o único jeito de corrigir um já gravado. Alterar aqui muda o comentário original, não cria um novo.</p>
      <input type="hidden" id="editarComentarioIdAcomp">
      <label>Tipo</label>
      <select id="editarComentarioTipo">
        <option value="3">-Comentário</option>
        <option value="1">Importante</option>
        <option value="2">Urgente</option>
        <option value="14">Aguardando Turma</option>
      </select>

      <label style="margin-top:12px;">Assunto</label>
      <input type="text" id="editarComentarioAssunto">

      <label style="margin-top:12px;">Descrição</label>
      <textarea id="editarComentarioDescricao" rows="4" style="width:100%;"></textarea>

      <label style="margin-top:12px;">Turma (opcional)</label>
      <div class="autocomplete-box pequeno">
        <input type="text" id="editarComentarioBuscaTurma" placeholder="Digite pra vincular uma turma..." autocomplete="off">
        <div id="editarComentarioResultadosTurma" class="autocomplete-list"></div>
      </div>
      <input type="hidden" id="editarComentarioTurmaId">
      <div id="editarComentarioTurmaEscolhida" style="margin-top:4px; font-size:0.85rem; color:var(--text-muted);"></div>

      <div style="margin-top:16px;">
        <button class="acao" onclick="salvarEdicaoComentario('${idAluno}')">Salvar edição</button>
        <button class="btn-nao" onclick="document.getElementById('cardEditarComentario').style.display='none';">Cancelar</button>
      </div>
      <div id="editarComentarioMsg" style="margin-top:10px;"></div>
    </div>

    <div class="card">
      <h3 style="margin-top:0;">Resumo</h3>
      <div style="display:flex; gap:10px; flex-wrap:wrap;">
        <button class="acao" onclick="carregarResumo('${idAluno}', false)">Resumo</button>
        <button class="btn-nao" onclick="carregarResumo('${idAluno}', true)">Resumo com IA</button>
      </div>
      <p class="fonte" style="margin-top:6px;">"Resumo" junta só os fatos (histórico de comentários + pagamentos/cobranças). "Resumo com IA" envia esse histórico pro provedor de IA configurado e devolve um texto interpretado — pode conter erro, confira. Nenhum dos dois grava nada no Fuctura.</p>
      <div id="resumoAluno" style="margin-top:12px;"></div>
    </div>

    <div class="card">
      <h3 style="margin-top:0;">Auditar pagamentos contra o extrato bancário</h3>
      <p class="fonte">
        Pedido do usuário (2026-09-16): o comentário "-Pagamento Realizado" gravado no Fuctura precisa ser
        CONFIRMADO contra o extrato real (data + valor) — não o contrário. Nomes em PIX vêm truncados no extrato
        (ex: "PIX TRANSF HEROS M13/10"), então a conferência é só por data + valor, nunca por nome.
        Cole abaixo o extrato exportado como CSV (3 colunas por linha: data;descrição;valor — sem cabeçalho
        obrigatório, linhas de saldo/separador são ignoradas sozinhas).
      </p>
      <textarea id="extratoCsv-${idAluno}" rows="5" style="width:100%; font-family:monospace; font-size:0.8rem;" placeholder="21/08/2025;PIX TRANSF HEROS M21/08;R$ 377,00"></textarea>
      <div style="margin-top:8px;">
        <button class="acao" onclick="auditarPagamentos('${idAluno}')">Auditar pagamentos deste aluno</button>
      </div>
      <div id="auditoriaPagamentos-${idAluno}" style="margin-top:10px;"></div>
    </div>

    <div class="card">
      <h3 style="margin-top:0;">Comentários (${d.comentarios.length})</h3>
      ${d.comentarios.map(c => `
        <div class="comentario">
          <div class="meta">${c.data} — ${c.tipo} — ${c.autor}</div>
          <b>${c.titulo}</b><br>${c.texto}
          ${TIPO_LABEL_PARA_CODIGO[c.tipo] ? `<div style="margin-top:6px;"><button class="btn-nao" onclick="editarComentario('${c.id_acomp}')">Editar</button></div>` : ''}
        </div>
      `).join('')}
    </div>
  `;
  comentariosAtuais = d.comentarios;

  let turmaDebounceEdicao = null;
  document.getElementById('editarComentarioBuscaTurma').addEventListener('input', (e) => {
    const q = e.target.value;
    clearTimeout(turmaDebounceEdicao);
    document.getElementById('editarComentarioTurmaId').value = '';
    if (q.length < 2) { document.getElementById('editarComentarioResultadosTurma').innerHTML = ''; return; }
    turmaDebounceEdicao = setTimeout(async () => {
      const rt = await fetch('/api/turma/buscar?q=' + encodeURIComponent(q));
      const turmas = await rt.json();
      document.getElementById('editarComentarioResultadosTurma').innerHTML = turmas.map(t =>
        `<div class="item" onclick='escolherTurmaEdicaoComentario("${t.id_turma}", ${JSON.stringify(t.nome)})'>${t.nome}</div>`
      ).join('');
    }, 300);
  });

  let turmaDebounce = null;
  document.getElementById('novoComentarioBuscaTurma').addEventListener('input', (e) => {
    const q = e.target.value;
    clearTimeout(turmaDebounce);
    document.getElementById('novoComentarioTurmaId').value = '';
    if (q.length < 2) { document.getElementById('novoComentarioResultadosTurma').innerHTML = ''; return; }
    turmaDebounce = setTimeout(async () => {
      const rt = await fetch('/api/turma/buscar?q=' + encodeURIComponent(q));
      const turmas = await rt.json();
      document.getElementById('novoComentarioResultadosTurma').innerHTML = turmas.map(t =>
        `<div class="item" onclick='escolherTurmaComentario("${t.id_turma}", ${JSON.stringify(t.nome)})'>${t.nome}</div>`
      ).join('');
    }, 300);
  });
}

async function enviarFoto(idAluno) {
  const input = document.getElementById('fotoInput');
  if (!input.files.length) return;
  const status = document.getElementById('fotoStatus');
  status.textContent = 'Enviando...';
  const fd = new FormData();
  fd.append('foto', input.files[0]);
  const r = await fetch(`/api/aluno/${idAluno}/foto`, { method: 'POST', body: fd });
  const d = await r.json();
  if (d.erro) { status.textContent = 'Erro: ' + d.erro; return; }
  status.textContent = '';
  input.value = '';
  const img = document.getElementById('fotoImg');
  img.hidden = false;
  document.getElementById('fotoPlaceholder').hidden = true;
  img.src = `/api/aluno/${idAluno}/foto?t=${Date.now()}`;
}

async function removerFoto(idAluno) {
  const r = await fetch(`/api/aluno/${idAluno}/foto/remover`, { method: 'POST' });
  const d = await r.json();
  if (d.erro) { document.getElementById('fotoStatus').textContent = 'Erro: ' + d.erro; return; }
  document.getElementById('fotoImg').hidden = true;
  document.getElementById('fotoPlaceholder').hidden = false;
}

function escolherTurmaComentario(id, nome) {
  document.getElementById('novoComentarioTurmaId').value = id;
  document.getElementById('novoComentarioBuscaTurma').value = nome;
  document.getElementById('novoComentarioResultadosTurma').innerHTML = '';
  document.getElementById('novoComentarioTurmaEscolhida').textContent = 'Turma vinculada: ' + nome;
}

async function salvarComentario(idAluno) {
  const dados = {
    tipo: document.getElementById('novoComentarioTipo').value,
    assunto: document.getElementById('novoComentarioAssunto').value.trim(),
    descricao: document.getElementById('novoComentarioDescricao').value.trim(),
    turma_id: document.getElementById('novoComentarioTurmaId').value,
    turma_nome: document.getElementById('novoComentarioBuscaTurma').value.trim(),
  };
  const msg = document.getElementById('novoComentarioMsg');
  msg.textContent = 'Salvando...';
  const r = await fetch(`/api/aluno/${idAluno}/comentario`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(dados),
  });
  const d = await r.json();
  if (d.erro) { msg.innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  msg.innerHTML = `<span style="color:var(--success)">Comentário salvo!</span>`;
  setTimeout(() => selecionar(idAluno), 800);
}

function editarComentario(idAcomp) {
  const c = comentariosAtuais.find(x => x.id_acomp === idAcomp);
  if (!c) return;
  document.getElementById('editarComentarioIdAcomp').value = c.id_acomp;
  document.getElementById('editarComentarioTipo').value = TIPO_LABEL_PARA_CODIGO[c.tipo] || '3';
  document.getElementById('editarComentarioAssunto').value = c.titulo;
  document.getElementById('editarComentarioDescricao').value = c.texto;
  document.getElementById('editarComentarioTurmaId').value = '';
  document.getElementById('editarComentarioBuscaTurma').value = '';
  document.getElementById('editarComentarioTurmaEscolhida').textContent = '';
  document.getElementById('editarComentarioMsg').textContent = '';
  const card = document.getElementById('cardEditarComentario');
  card.style.display = 'block';
  card.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function escolherTurmaEdicaoComentario(id, nome) {
  document.getElementById('editarComentarioTurmaId').value = id;
  document.getElementById('editarComentarioBuscaTurma').value = nome;
  document.getElementById('editarComentarioResultadosTurma').innerHTML = '';
  document.getElementById('editarComentarioTurmaEscolhida').textContent = 'Turma vinculada: ' + nome;
}

async function salvarEdicaoComentario(idAluno) {
  const idAcomp = document.getElementById('editarComentarioIdAcomp').value;
  const dados = {
    tipo: document.getElementById('editarComentarioTipo').value,
    assunto: document.getElementById('editarComentarioAssunto').value.trim(),
    descricao: document.getElementById('editarComentarioDescricao').value.trim(),
    turma_id: document.getElementById('editarComentarioTurmaId').value,
    turma_nome: document.getElementById('editarComentarioBuscaTurma').value.trim(),
  };
  const msg = document.getElementById('editarComentarioMsg');
  msg.textContent = 'Salvando...';
  const r = await fetch(`/api/aluno/${idAluno}/comentario/${idAcomp}`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(dados),
  });
  const d = await r.json();
  if (d.erro) { msg.innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  msg.innerHTML = `<span style="color:var(--success)">Comentário alterado!</span>`;
  setTimeout(() => selecionar(idAluno), 800);
}

async function carregarResumo(idAluno, comIa) {
  const div = document.getElementById('resumoAluno');
  div.innerHTML = comIa
    ? '<div class="fonte">Gerando resumo com IA… pode levar alguns segundos.</div>'
    : '<div class="fonte">Montando…</div>';
  let d;
  try {
    const r = await fetch(`/api/aluno/${idAluno}/${comIa ? 'resumo-ia' : 'resumo'}`);
    d = await r.json();
  } catch (e) {
    div.innerHTML = `<div class="fonte" style="color:var(--danger)">Falha de rede: ${e}</div>`;
    return;
  }
  if (d.erro) { div.innerHTML = `<div class="fonte" style="color:var(--danger)">Erro: ${d.erro}</div>`; return; }

  let html = '';
  if (d.modo === 'ia') {
    html += `<div class="aviso">Resumo gerado por IA (${d.provedor}) — confira antes de usar.</div>`;
    html += `<div class="comentario" style="white-space:pre-wrap;">${d.resumo_ia || '(resposta vazia)'}</div>`;
    html += `<div class="fonte" style="margin-top:14px; margin-bottom:4px;">Base factual (sem IA):</div>`;
  }
  html += `<table><tr><th>Financeiro</th><td>${d.financeiro}</td></tr>`;
  html += `<tr><th>Histórico</th><td>${d.resumo_historico}</td></tr></table>`;
  html += `<b style="display:block; margin-top:12px;">Pagamentos / cobranças / desistência</b>`;
  html += `<div class="comentario" style="white-space:pre-wrap;">${d.pagamentos_desistencia}</div>`;
  div.innerHTML = html;
}

async function auditarPagamentos(idAluno) {
  const div = document.getElementById('auditoriaPagamentos-' + idAluno);
  const csv = document.getElementById('extratoCsv-' + idAluno).value.trim();
  if (!csv) { div.innerHTML = '<div class="fonte" style="color:var(--danger)">Cole o extrato em CSV primeiro.</div>'; return; }
  div.innerHTML = '<div class="fonte">Conferindo...</div>';
  const r = await fetch(`/api/aluno/${idAluno}/auditar-pagamentos`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ extrato_csv: csv }),
  });
  const d = await r.json();
  if (d.erro) { div.innerHTML = `<div class="fonte" style="color:var(--danger)">Erro: ${d.erro}</div>`; return; }
  if (!d.pagamentos.length) {
    div.innerHTML = '<div class="fonte">Nenhum comentário "-Pagamento Realizado" com valor identificável no histórico deste aluno.</div>';
    return;
  }
  div.innerHTML = `
    <table>
      <thead><tr><th>Data (comentário)</th><th>Valor</th><th>Data usada</th><th>Confirmado no extrato?</th></tr></thead>
      <tbody>
        ${d.pagamentos.map(p => `
          <tr>
            <td>${p.data_comentario}</td>
            <td>R$ ${p.valor_extraido.toFixed(2).replace('.', ',')}</td>
            <td>${p.data_extraida || '—'}${p.data_e_aproximada ? ' (aproximada — sem data explícita no texto)' : ''}</td>
            <td style="color:${p.confirmacao.confirmado ? 'var(--success)' : 'var(--danger)'}">
              ${p.confirmacao.confirmado ? `Sim (diferença de ${p.confirmacao.diferenca_dias} dia(s))` : '⚠ NÃO encontrado no extrato'}
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
    <div class="fonte" style="margin-top:6px;">Conferência só por data + valor (nomes em PIX vêm truncados no extrato, não são confiáveis pra casar).</div>
  `;
}

async function mudarSituacao(idAluno) {
  const dados = {
    novo_status: document.getElementById('novaSituacao').value,
    motivo: document.getElementById('motivoSituacao').value.trim(),
  };
  const msg = document.getElementById('msgSituacao');
  msg.textContent = 'Alterando...';
  const r = await fetch(`/api/aluno/${idAluno}/situacao`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(dados),
  });
  const d = await r.json();
  if (d.erro) { msg.innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  msg.innerHTML = `<span style="color:var(--success)">Situação alterada de "${d.situacao_anterior}" para "${d.situacao_nova}"!</span>`;
  setTimeout(() => selecionar(idAluno), 800);
}

function toggleEditarCadastro() {
  const card = document.getElementById('cardEditarCadastro');
  card.style.display = card.style.display === 'none' ? 'block' : 'none';
}

const CAMPOS_EDITAR_CADASTRO = [
  ['edNome', 'nome'], ['edEmail', 'email'], ['edFixo', 'fixo'], ['edCelular', 'celular'],
  ['edCpf', 'cpf'], ['edIdentidade', 'identidade'], ['edDataNascimento', 'dataNascimento'], ['edSexo', 'sexo'],
  ['edEndereco', 'endereco'], ['edNumero', 'numero'], ['edComplemento', 'complemento'],
  ['edBairro', 'bairro'], ['edCidade', 'cidade'], ['edEstado', 'estado'], ['edCep', 'cep'],
  ['edNomeResponsavel', 'nomeResponsavel'], ['edTelefoneResponsavel', 'telefoneResponsavel'],
  ['edCelularResponsavel', 'celularResponsavel'], ['edCpfResponsavel', 'cpfResponsavel'],
  ['edIdentidadeResponsavel', 'identidadeResponsavel'], ['edEmailResponsavel', 'emailResponsavel'],
];

async function salvarCadastro(idAluno) {
  const dados = {};
  for (const [elId, campo] of CAMPOS_EDITAR_CADASTRO) dados[campo] = document.getElementById(elId).value.trim();

  const msg = document.getElementById('msgEditarCadastro');
  msg.textContent = 'Salvando...';
  const r = await fetch(`/api/aluno/${idAluno}/cadastro`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(dados),
  });
  const d = await r.json();
  if (d.erro) { msg.innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  msg.innerHTML = `<span style="color:var(--success)">Cadastro salvo!</span>`;
  setTimeout(() => selecionar(idAluno), 800);
}

// deep-link direto por id (usado pela "Atividade recente" na tela inicial,
// ex: /alunos?id=32692) - abre o cadastro do aluno sem precisar buscar por nome.
const _idDireto = new URLSearchParams(window.location.search).get('id');
if (_idDireto) selecionar(_idDireto);
</script></body></html>"""


ALUNO_NOVO_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Cadastro de Aluno</title>\n<style>" + BASE_CSS + """
  .page { max-width: 680px; }
  .campo { margin-top: 14px; }
  .checkbox-linha { display:flex; align-items:center; gap:6px; font-size:0.85rem; font-weight:400; text-transform:none; letter-spacing:0; color:var(--text); margin-top:6px; }
  .checkbox-linha input { width:auto; }
  fieldset { border:1px solid var(--border); border-radius:var(--radius-sm); padding:14px; margin-top:6px; }
  legend { padding:0 6px; font-size:0.8rem; color:var(--text-muted); }
</style></head><body>
<div class="page">
<a class="voltar" href="/alunos">← voltar</a>
<h1>Cadastro de Aluno</h1>
<p class="subtitulo">Dados básicos são os mesmos do cadastro rápido do Schoolfine. Dados complementares do Fuctura
(CPF, endereço...) podem ser preenchidos agora ou depois. Profissão é obrigatória neste sistema — o Fuctura não
tem esse campo, então ela (e o resto do Bloco 3) fica registrada como um comentário estruturado no cadastro.</p>

<div class="card">
  <h3 style="margin-top:0;">Dados básicos</h3>
  <label>Nome *</label>
  <input type="text" id="nome" placeholder="Nome completo" autocomplete="off">

  <div class="campo"><label>E-mail</label>
  <input type="text" id="email" placeholder="email@exemplo.com" autocomplete="off"></div>

  <div class="campo"><label>Telefone fixo</label>
  <input type="text" id="fixo" placeholder="(81) 0000-0000" autocomplete="off"></div>

  <div class="campo"><label>Celular</label>
  <input type="text" id="celular" placeholder="(81) 90000-0000" autocomplete="off"></div>

  <div class="campo"><label>Situação *</label>
  <select id="status">
    <option value="">Selecione</option>
    <option value="19">Interessado</option>
    <option value="4">-Matriculado</option>
    <option value="7">Devedor</option>
    <option value="30">Advogado</option>
    <option value="26">Convenio</option>
    <option value="31">Provi/Pravaler</option>
    <option value="6">Pediu Pausa</option>
    <option value="29">Cliente sem Interesse</option>
    <option value="5">Cancelado</option>
    <option value="22">Ex-aluno</option>
  </select></div>
</div>

<div class="card">
  <h3 style="margin-top:0;">Dados complementares (Fuctura) — opcional agora</h3>
  <div class="campo"><label>CPF</label><input type="text" id="cpf" maxlength="14" placeholder="000.000.000-00" autocomplete="off"></div>
  <div class="campo"><label>RG / Identidade</label><input type="text" id="identidade" autocomplete="off"></div>
  <div class="campo"><label>Data de nascimento</label><input type="text" id="dataNascimento" placeholder="DD/MM/AAAA" autocomplete="off"></div>
  <div class="campo"><label>Sexo</label>
  <select id="sexo"><option value="">Selecione</option><option value="M">Masculino</option><option value="F">Feminino</option></select></div>
  <div class="campo"><label>CEP</label><input type="text" id="cep" placeholder="00000-000" autocomplete="off">
  <span id="cepStatus" class="fonte"></span></div>
  <div class="campo"><label>Endereço</label><input type="text" id="endereco" autocomplete="off"></div>
  <div class="campo"><label>Número</label><input type="text" id="numero" autocomplete="off"></div>
  <div class="campo"><label>Complemento</label><input type="text" id="complemento" autocomplete="off"></div>
  <div class="campo"><label>Bairro</label><input type="text" id="bairro" autocomplete="off"></div>
  <div class="campo"><label>Cidade</label><input type="text" id="cidade" autocomplete="off"></div>
  <div class="campo"><label>Estado (UF)</label><input type="text" id="estado" maxlength="2" autocomplete="off"></div>

  <fieldset>
    <legend>Responsável (obrigatório se menor de idade; opcional se for um terceiro que paga — pai, tio, amigo)</legend>
    <label>Nome do responsável</label><input type="text" id="nomeResponsavel" autocomplete="off">
    <div class="campo"><label>Telefone</label><input type="text" id="telefoneResponsavel" autocomplete="off"></div>
    <div class="campo"><label>Celular</label><input type="text" id="celularResponsavel" autocomplete="off"></div>
    <div class="campo"><label>CPF</label><input type="text" id="cpfResponsavel" maxlength="14" placeholder="000.000.000-00" autocomplete="off"></div>
    <div class="campo"><label>RG / Identidade</label><input type="text" id="identidadeResponsavel" autocomplete="off"></div>
    <div class="campo"><label>E-mail</label><input type="text" id="emailResponsavel" autocomplete="off"></div>
  </fieldset>
</div>

<div class="card">
  <h3 style="margin-top:0;">Dados complementares (nosso sistema)</h3>
  <label>Profissão *</label>
  <input type="text" id="profissao" placeholder="Ex: Analista de sistemas" autocomplete="off">

  <div class="campo"><label>Situação de emprego</label>
  <select id="situacao_emprego">
    <option value="">Selecione</option>
    <option value="CLT">CLT</option>
    <option value="Autônomo">Autônomo</option>
    <option value="Desempregado">Desempregado</option>
    <option value="Estudante">Estudante</option>
    <option value="Outro">Outro</option>
  </select></div>

  <div class="campo"><label>Empresa atual</label><input type="text" id="empresa_atual" autocomplete="off"></div>

  <div class="campo"><label>Experiência com programação</label>
  <select id="experiencia_programacao">
    <option value="">Selecione</option>
    <option value="Nenhuma">Nenhuma</option>
    <option value="Básica">Básica</option>
    <option value="Intermediária">Intermediária</option>
    <option value="Avançada">Avançada</option>
  </select></div>

  <div class="campo"><label>Curso de interesse</label>
  <select id="curso_interesse">
    <option value="">Selecione</option>
    <option value="Java">Java</option>
    <option value="Python">Python</option>
    <option value="Fullstack">Fullstack</option>
    <option value="Ainda não decidiu">Ainda não decidiu</option>
  </select></div>

  <div class="campo"><label>Objetivo</label>
  <select id="objetivo">
    <option value="">Selecione</option>
    <option value="Conseguir emprego">Conseguir emprego</option>
    <option value="Mudança de carreira">Mudança de carreira</option>
    <option value="Hobby">Hobby</option>
    <option value="Empreender">Empreender</option>
    <option value="Outro">Outro</option>
  </select></div>

  <div class="campo"><label>Como conheceu a Fuctura</label>
  <select id="como_conheceu" onchange="toggleOutro('como_conheceu')">
    <option value="">Selecione</option>
    <option value="Indicação">Indicação</option>
    <option value="Instagram">Instagram</option>
    <option value="Facebook">Facebook</option>
    <option value="TikTok">TikTok</option>
    <option value="Google">Google</option>
    <option value="WhatsApp">WhatsApp</option>
    <option value="Panfleto">Panfleto</option>
    <option value="Outro">Outro</option>
  </select>
  <input type="text" id="como_conheceu_outro" class="campo-outro" style="display:none; margin-top:6px;" placeholder="Especifique..." autocomplete="off">
  </div>

  <div class="campo"><label>Disponibilidade de horário</label>
    <div class="checkbox-linha"><input type="checkbox" value="Manhã" class="disp">Manhã</div>
    <div class="checkbox-linha"><input type="checkbox" value="Tarde" class="disp">Tarde</div>
    <div class="checkbox-linha"><input type="checkbox" value="Noite" class="disp">Noite</div>
    <div class="checkbox-linha"><input type="checkbox" value="Fim de semana" class="disp">Fim de semana</div>
  </div>

  <div class="campo"><label>Contato de emergência (nome)</label><input type="text" id="contato_emergencia_nome" autocomplete="off"></div>
  <div class="campo"><label>Contato de emergência (telefone)</label><input type="text" id="contato_emergencia_telefone" autocomplete="off"></div>
</div>

<div class="card">
  <button class="acao" onclick="cadastrar()">Cadastrar</button>
  <span id="status_msg"></span>
</div>

<div id="resultado"></div>
</div>

<script>""" + BASE_JS + """
const CAMPOS_TEXTO = [
  'nome', 'email', 'fixo', 'celular',
  'cpf', 'identidade', 'dataNascimento', 'endereco', 'numero', 'complemento', 'bairro', 'cidade', 'estado', 'cep',
  'nomeResponsavel', 'telefoneResponsavel', 'celularResponsavel', 'cpfResponsavel', 'identidadeResponsavel', 'emailResponsavel',
  'profissao', 'empresa_atual', 'contato_emergencia_nome', 'contato_emergencia_telefone',
];
const CAMPOS_SELECT = ['status', 'sexo', 'situacao_emprego', 'experiencia_programacao', 'curso_interesse', 'objetivo', 'como_conheceu'];

function toggleOutro(campoId) {
  const select = document.getElementById(campoId);
  const outro = document.getElementById(campoId + '_outro');
  if (!outro) return;
  outro.style.display = select.value === 'Outro' ? 'block' : 'none';
  if (select.value !== 'Outro') outro.value = '';
}

function limparFormulario() {
  for (const id of CAMPOS_TEXTO) document.getElementById(id).value = '';
  for (const id of CAMPOS_SELECT) document.getElementById(id).value = '';
  document.querySelectorAll('.campo-outro').forEach(el => { el.value = ''; el.style.display = 'none'; });
  document.querySelectorAll('.disp').forEach(cb => cb.checked = false);
  document.getElementById('cepStatus').textContent = '';
}

// Autopreenchimento por CEP (pedido do usuario, 2026-09-13) - ViaCEP e
// publico, gratuito, sem chave - so preenche endereco/bairro/cidade/
// estado; numero/complemento continuam manuais (o CEP nao carrega isso).
// So dispara com 8 digitos (CEP completo), nunca sobrescreve o que o
// funcionario ja tiver digitado a mao nesses campos.
document.getElementById('cep').addEventListener('blur', async (e) => {
  const cepStatus = document.getElementById('cepStatus');
  const digitos = e.target.value.replace(/\\D/g, '');
  if (digitos.length !== 8) { cepStatus.textContent = ''; return; }
  cepStatus.textContent = ' buscando endereço...';
  try {
    const r = await fetch(`https://viacep.com.br/ws/${digitos}/json/`);
    const d = await r.json();
    if (d.erro) { cepStatus.textContent = ' CEP não encontrado.'; return; }
    if (!document.getElementById('endereco').value) document.getElementById('endereco').value = d.logradouro || '';
    if (!document.getElementById('bairro').value) document.getElementById('bairro').value = d.bairro || '';
    if (!document.getElementById('cidade').value) document.getElementById('cidade').value = d.localidade || '';
    if (!document.getElementById('estado').value) document.getElementById('estado').value = d.uf || '';
    cepStatus.textContent = ' endereço preenchido — confira antes de salvar.';
  } catch (err) {
    cepStatus.textContent = ' não foi possível buscar o CEP agora (preencha manualmente).';
  }
});

async function cadastrar() {
  const dados = {};
  for (const id of CAMPOS_TEXTO) dados[id] = document.getElementById(id).value.trim();
  for (const id of CAMPOS_SELECT) dados[id] = document.getElementById(id).value;
  // quando "Outro" foi escolhido e o funcionario especificou, usa o texto
  // livre no lugar do literal "Outro" - fica mais claro no comentario final.
  const outroConheceu = document.getElementById('como_conheceu_outro').value.trim();
  if (dados.como_conheceu === 'Outro' && outroConheceu) dados.como_conheceu = outroConheceu;
  dados.disponibilidade = Array.from(document.querySelectorAll('.disp:checked')).map(cb => cb.value);

  const msg = document.getElementById('status_msg');
  const resultado = document.getElementById('resultado');
  resultado.innerHTML = '';

  if (!dados.nome) { alert('Informe o nome.'); return; }
  if (!dados.status) { alert('Informe a situação.'); return; }
  if (!dados.profissao) { alert('Informe a profissão.'); return; }

  msg.textContent = ' Cadastrando...';
  const r = await fetch('/api/aluno/criar', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(dados),
  });
  const d = await r.json();
  msg.textContent = '';
  if (d.erro) { alert(d.erro); return; }

  resultado.innerHTML = `
    <div class="card aluno-card ok">
      <b>✓ Aluno cadastrado com sucesso.</b><br>
      ID: ${d.id_aluno}<br>
      <a href="/alunos">Ver na Consulta de Alunos</a>
    </div>
  `;
  limparFormulario();
}
</script></body></html>"""


ATA_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Ata de Chamada</title>\n<style>" + BASE_CSS + """
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1>Ata de Chamada</h1>
<p class="subtitulo">Gera a Ata de Chamada oficial do Fuctura (Relatórios → Ata de Chamada) — lista todo aluno
vinculado à turma, ativo ou não, com situação e celular direto do cadastro. Só leitura.</p>

<div class="card">
  <label>Turma</label>
  <div class="autocomplete-box">
    <input type="text" id="buscaTurma" placeholder="Digite o nome da turma..." autocomplete="off">
    <div id="resultadosTurma" class="autocomplete-list"></div>
  </div>
  <input type="hidden" id="turmaId">
  <div id="turmaEscolhida" style="margin-top:8px; font-weight:600;"></div>
  <br>
  <button class="acao" onclick="imprimirAta()">Ata para imprimir (PDF)</button>
  &nbsp;
  <button class="btn-nao" onclick="gerarAta()">Ver lista na tela</button>
  <span id="status"></span>
</div>

<div id="resultado"></div>
</div>

<script>""" + BASE_JS + """
let turmaSelecionada = null;

document.getElementById('buscaTurma').addEventListener('input', async (e) => {
  const q = e.target.value;
  if (q.length < 2) { document.getElementById('resultadosTurma').innerHTML = ''; return; }
  const r = await fetch('/api/turma/buscar?q=' + encodeURIComponent(q));
  const turmas = await r.json();
  document.getElementById('resultadosTurma').innerHTML = turmas.map(t =>
    `<div class="item" onclick='escolherTurma("${t.id_turma}", ${JSON.stringify(t.nome)})'>${t.nome}</div>`
  ).join('');
});

function escolherTurma(id, nome) {
  turmaSelecionada = { id, nome };
  document.getElementById('turmaId').value = id;
  document.getElementById('turmaEscolhida').textContent = 'Selecionada: ' + nome;
  document.getElementById('resultadosTurma').innerHTML = '';
  document.getElementById('buscaTurma').value = nome;
}

function imprimirAta() {
  if (!turmaSelecionada) { alert('Escolha uma turma primeiro.'); return; }
  window.open(`/api/turma/${turmaSelecionada.id}/ata-pdf`, '_blank');
}

async function gerarAta() {
  if (!turmaSelecionada) { alert('Escolha uma turma primeiro.'); return; }
  document.getElementById('status').textContent = ' Gerando...';
  const r = await fetch(`/api/turma/${turmaSelecionada.id}/ata`);
  const d = await r.json();
  document.getElementById('status').textContent = '';
  const div = document.getElementById('resultado');
  if (d.erro) { div.innerHTML = `<div class="card">Erro: ${d.erro}</div>`; return; }

  div.innerHTML = `
    <div class="card">
      <h2 style="margin-top:0;">${d.turma || turmaSelecionada.nome}</h2>
      <div><b>Professor:</b> ${d.professor || '—'}</div>
      <div><b>Alunos listados:</b> ${d.alunos.length}</div>
      <table style="margin-top:12px;">
        <thead><tr><th>Nome</th><th>Situação</th><th>Celular</th></tr></thead>
        <tbody>
          ${d.alunos.map(a => `<tr><td>${a.nome}</td><td>${a.situacao}</td><td>${a.celular}</td></tr>`).join('')}
        </tbody>
      </table>
    </div>
  `;
}
</script></body></html>"""


TURMAS_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Consulta de Turmas</title>\n<style>" + BASE_CSS + """
  .contador { font-size:0.78rem; color:var(--text-muted); }
  .contador.estourou { color:var(--danger); font-weight:600; }
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1>Consulta de Turmas</h1>
<p class="subtitulo">Busca por nome, mostra o roster ativo da turma e permite editar os dados da turma.</p>

<div class="card">
  <label>Turma</label>
  <div class="autocomplete-box">
    <input type="text" id="buscaTurma" placeholder="Digite o nome da turma..." autocomplete="off">
    <div id="resultadosTurma" class="autocomplete-list"></div>
  </div>
  <input type="hidden" id="turmaId">
  <div id="turmaEscolhida" style="margin-top:8px; font-weight:600;"></div>
</div>

<div id="resultado"></div>

<div id="cardEditarTurma" class="card" hidden>
  <h2 style="margin-top:0;">Editar Turma</h2>
  <label>Abreviada * <span id="contadorAbrevEdit" class="contador">(0/5)</span></label>
  <input type="text" id="editAbreviada" maxlength="5">

  <label style="margin-top:12px;">Unidade *</label>
  <select id="editEntidade">
    <option value="">Selecione</option>
    <option value="1">Espinheiro - ESP</option>
    <option value="2">Boa Viagem - BV</option>
    <option value="5">Caruaru - Car</option>
    <option value="4">Porto Digital - POR</option>
    <option value="3">Todas - All</option>
  </select>

  <label style="margin-top:12px;">Descrição * <span id="contadorDescEdit" class="contador">(0/30)</span></label>
  <input type="text" id="editDescricao" maxlength="30">

  <label style="margin-top:12px;">Professor *</label>
  <input type="text" id="editProfessor">

  <label style="margin-top:12px;">Data início</label>
  <input type="text" id="editDataInicio" placeholder="dd/mm/aaaa">

  <label style="margin-top:12px;">Data término</label>
  <input type="text" id="editDataTermino" placeholder="dd/mm/aaaa">

  <div style="margin-top:16px;">
    <button class="acao" onclick="salvarEdicaoTurma()">Salvar</button>
  </div>
  <div id="msgEditarTurma" style="margin-top:10px;"></div>
</div>
</div>

<script>""" + BASE_JS + """
let turmaSelecionada = null;

function atualizarContadorEdit(inputId, contadorId, limite) {
  const v = document.getElementById(inputId).value;
  const c = document.getElementById(contadorId);
  c.textContent = `(${v.length}/${limite})`;
  c.classList.toggle('estourou', v.length >= limite);
}
document.getElementById('editAbreviada').addEventListener('input', () => atualizarContadorEdit('editAbreviada', 'contadorAbrevEdit', 5));
document.getElementById('editDescricao').addEventListener('input', () => atualizarContadorEdit('editDescricao', 'contadorDescEdit', 30));

document.getElementById('buscaTurma').addEventListener('input', async (e) => {
  const q = e.target.value;
  if (q.length < 2) { document.getElementById('resultadosTurma').innerHTML = ''; return; }
  const r = await fetch('/api/turma/buscar?q=' + encodeURIComponent(q));
  const turmas = await r.json();
  document.getElementById('resultadosTurma').innerHTML = turmas.map(t =>
    `<div class="item" onclick='escolherTurma("${t.id_turma}", ${JSON.stringify(t.nome)})'>${t.nome}</div>`
  ).join('');
});

async function escolherTurma(id, nome) {
  turmaSelecionada = { id, nome };
  document.getElementById('turmaId').value = id;
  document.getElementById('turmaEscolhida').textContent = 'Selecionada: ' + nome;
  document.getElementById('resultadosTurma').innerHTML = '';
  document.getElementById('buscaTurma').value = nome;
  document.getElementById('cardEditarTurma').hidden = true;

  const div = document.getElementById('resultado');
  div.innerHTML = '<div class="card">Carregando roster...</div>';
  const r = await fetch(`/api/turma/${id}/roster`);
  const d = await r.json();
  if (d.erro) { div.innerHTML = `<div class="card">Erro: ${d.erro}</div>`; return; }

  div.innerHTML = `
    <div class="card">
      <h2 style="margin-top:0;">${nome}</h2>
      <div style="margin-bottom:10px;">
        <a class="acao" style="text-decoration:none; display:inline-block;" href="/api/turma/${id}/ata-pdf" target="_blank">Ata de Chamada (PDF p/ imprimir)</a>
        &nbsp;
        <a class="acao" style="text-decoration:none; display:inline-block;" href="/api/turma/${id}/verificacao" target="_blank">Verificação de Turma (PDF)</a>
        &nbsp;
        <a class="acao" style="text-decoration:none; display:inline-block;" href="/api/turma/${id}/pagamentos" target="_blank">Pagamentos (PDF)</a>
        &nbsp;
        <button class="btn-nao" onclick="editarTurma(${JSON.stringify(id)})">Editar Turma</button>
      </div>
      <div><b>Alunos no roster ativo:</b> ${d.length}</div>
      <table style="margin-top:12px;">
        <thead><tr><th>Nome</th><th>Entrada</th><th>Status</th><th>Telefone</th></tr></thead>
        <tbody>
          ${d.map(a => `<tr><td>${a.nome}</td><td>${a.data_entrada}</td><td>${a.status}</td><td>${a.telefone}</td></tr>`).join('')}
        </tbody>
      </table>
    </div>
  `;
}

async function editarTurma(id) {
  const card = document.getElementById('cardEditarTurma');
  document.getElementById('msgEditarTurma').textContent = '';
  card.hidden = false;
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  const r = await fetch(`/api/turma/${id}/detalhe`);
  const d = await r.json();
  if (d.erro) { document.getElementById('msgEditarTurma').innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  document.getElementById('editAbreviada').value = d.abreviada;
  document.getElementById('editEntidade').value = d.entidade;
  document.getElementById('editDescricao').value = d.descricao;
  document.getElementById('editProfessor').value = d.professor;
  document.getElementById('editDataInicio').value = d.dataInicio;
  document.getElementById('editDataTermino').value = d.dataTermino;
  atualizarContadorEdit('editAbreviada', 'contadorAbrevEdit', 5);
  atualizarContadorEdit('editDescricao', 'contadorDescEdit', 30);
}

async function salvarEdicaoTurma() {
  if (!turmaSelecionada) return;
  const dados = {
    abreviada: document.getElementById('editAbreviada').value.trim(),
    entidade: document.getElementById('editEntidade').value,
    descricao: document.getElementById('editDescricao').value.trim(),
    professor: document.getElementById('editProfessor').value.trim(),
    dataInicio: document.getElementById('editDataInicio').value.trim(),
    dataTermino: document.getElementById('editDataTermino').value.trim(),
  };
  const msg = document.getElementById('msgEditarTurma');
  msg.textContent = 'Salvando...';
  const r = await fetch(`/api/turma/${turmaSelecionada.id}/editar`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(dados),
  });
  const d = await r.json();
  if (d.erro) { msg.innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  msg.innerHTML = `<span style="color:var(--success)">Turma atualizada!</span>`;
}
</script></body></html>"""


TURMA_NOVA_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Cadastro de Turma</title>\n<style>" + BASE_CSS + """
  .contador { font-size:0.78rem; color:var(--text-muted); }
  .contador.estourou { color:var(--danger); font-weight:600; }
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1>Cadastro de Turma</h1>
<p class="subtitulo">Cria uma turma nova no Fuctura. "Abreviada" e "Descrição" têm limite real do banco menor que o anunciado no Fuctura — validamos aqui antes de enviar, pra não salvar um valor cortado sem você perceber.</p>

<div class="card">
  <label>Abreviada * <span id="contadorAbrev" class="contador">(0/5)</span></label>
  <input type="text" id="abreviada" maxlength="5" placeholder="Ex: PY2, J1, BL3...">

  <label style="margin-top:12px;">Unidade *</label>
  <select id="entidade">
    <option value="">Selecione</option>
    <option value="1">Espinheiro - ESP</option>
    <option value="2">Boa Viagem - BV</option>
    <option value="5">Caruaru - Car</option>
    <option value="4">Porto Digital - POR</option>
    <option value="3">Todas - All</option>
  </select>

  <label style="margin-top:12px;">Descrição * <span id="contadorDesc" class="contador">(0/30)</span></label>
  <input type="text" id="descricao" maxlength="30" placeholder="Nome completo da turma">

  <label style="margin-top:12px;">Professor *</label>
  <input type="text" id="professor" placeholder="Nome do professor">

  <label style="margin-top:12px;">Data início</label>
  <input type="text" id="dataInicio" placeholder="dd/mm/aaaa">

  <label style="margin-top:12px;">Data término</label>
  <input type="text" id="dataTermino" placeholder="dd/mm/aaaa">

  <div style="margin-top:16px; display:flex; gap:10px;">
    <button class="acao" onclick="cadastrar()">Cadastrar</button>
    <button class="btn-nao" onclick="limparFormulario()">Limpar</button>
  </div>
  <div id="msg" style="margin-top:10px;"></div>
</div>
</div>

<script>""" + BASE_JS + """
function atualizarContador(inputId, contadorId, limite) {
  const v = document.getElementById(inputId).value;
  const c = document.getElementById(contadorId);
  c.textContent = `(${v.length}/${limite})`;
  c.classList.toggle('estourou', v.length >= limite);
}
document.getElementById('abreviada').addEventListener('input', () => atualizarContador('abreviada', 'contadorAbrev', 5));
document.getElementById('descricao').addEventListener('input', () => atualizarContador('descricao', 'contadorDesc', 30));

function limparFormulario() {
  ['abreviada','entidade','descricao','professor','dataInicio','dataTermino'].forEach(id => document.getElementById(id).value = '');
  atualizarContador('abreviada', 'contadorAbrev', 5);
  atualizarContador('descricao', 'contadorDesc', 30);
  document.getElementById('msg').textContent = '';
}

async function cadastrar() {
  const dados = {
    abreviada: document.getElementById('abreviada').value.trim(),
    entidade: document.getElementById('entidade').value,
    descricao: document.getElementById('descricao').value.trim(),
    professor: document.getElementById('professor').value.trim(),
    dataInicio: document.getElementById('dataInicio').value.trim(),
    dataTermino: document.getElementById('dataTermino').value.trim(),
  };
  const msg = document.getElementById('msg');
  msg.textContent = 'Enviando...';
  const r = await fetch('/api/turma/criar', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(dados),
  });
  const d = await r.json();
  if (d.erro) { msg.innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  msg.innerHTML = `<span style="color:var(--success)">Turma criada! id_turma=${d.id_turma}</span>`;
  limparFormulario();
}
</script></body></html>"""


MATRICULAR_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Matricular Aluno</title>\n<style>" + BASE_CSS + """
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1>Matricular Aluno</h1>
<p class="subtitulo">Matricula um aluno numa turma de curso de verdade, com valor contratado e forma de pagamento reais (isso soma no financeiro do aluno).</p>

<div class="card">
  <label>Aluno</label>
  <div class="autocomplete-box">
    <input type="text" id="buscaAluno" placeholder="Digite o nome do aluno..." autocomplete="off">
    <div id="resultadosAluno" class="autocomplete-list"></div>
  </div>
  <input type="hidden" id="alunoId">
  <div id="alunoEscolhido" style="margin-top:8px; font-weight:600;"></div>

  <label style="margin-top:16px;">Turma</label>
  <div class="autocomplete-box">
    <input type="text" id="buscaTurma" placeholder="Digite o nome da turma..." autocomplete="off">
    <div id="resultadosTurma" class="autocomplete-list"></div>
  </div>
  <input type="hidden" id="turmaId">
  <input type="hidden" id="turmaNome">
  <div id="turmaEscolhida" style="margin-top:8px; font-weight:600;"></div>

  <label style="margin-top:16px;">Valor Contratado (R$)</label>
  <input type="number" id="valorContratado" step="0.01" min="0" placeholder="0,00">

  <label style="margin-top:16px;">Forma de Pagamento</label>
  <select id="formaPagamento">
    <option value="">Selecione</option>
    <option value="cartao">Cartão de Crédito</option>
    <option value="debito">Débito Bancário</option>
    <option value="especie">Dinheiro</option>
    <option value="boleto">Boleto Bancário</option>
    <option value="cheque">Cheque</option>
  </select>

  <label style="margin-top:16px;">Observação (opcional)</label>
  <textarea id="observacao" rows="3" style="width:100%;"></textarea>

  <div style="margin-top:16px;">
    <button class="acao" onclick="matricular()">Matricular</button>
  </div>
  <div id="msg" style="margin-top:10px;"></div>
</div>
</div>

<script>""" + BASE_JS + """
let alunoDebounce = null;
document.getElementById('buscaAluno').addEventListener('input', (e) => {
  const q = e.target.value;
  clearTimeout(alunoDebounce);
  document.getElementById('alunoId').value = '';
  if (q.length < 3) { document.getElementById('resultadosAluno').innerHTML = ''; return; }
  alunoDebounce = setTimeout(async () => {
    const r = await fetch('/api/aluno/buscar?q=' + encodeURIComponent(q));
    const alunos = await r.json();
    document.getElementById('resultadosAluno').innerHTML = (alunos.erro ? [] : alunos).map(a =>
      `<div class="item" onclick='escolherAluno("${a.id_aluno}", ${JSON.stringify(a.nome)})'>${a.nome} ${a.matricula ? '— matrícula ' + a.matricula : ''}</div>`
    ).join('');
  }, 300);
});

function escolherAluno(id, nome) {
  document.getElementById('alunoId').value = id;
  document.getElementById('buscaAluno').value = nome;
  document.getElementById('resultadosAluno').innerHTML = '';
  document.getElementById('alunoEscolhido').textContent = 'Selecionado: ' + nome;
}

let turmaDebounce = null;
document.getElementById('buscaTurma').addEventListener('input', (e) => {
  const q = e.target.value;
  clearTimeout(turmaDebounce);
  document.getElementById('turmaId').value = '';
  if (q.length < 2) { document.getElementById('resultadosTurma').innerHTML = ''; return; }
  turmaDebounce = setTimeout(async () => {
    const r = await fetch('/api/turma/buscar?q=' + encodeURIComponent(q));
    const turmas = await r.json();
    document.getElementById('resultadosTurma').innerHTML = turmas.map(t =>
      `<div class="item" onclick='escolherTurma("${t.id_turma}", ${JSON.stringify(t.nome)})'>${t.nome}</div>`
    ).join('');
  }, 300);
});

function escolherTurma(id, nome) {
  document.getElementById('turmaId').value = id;
  document.getElementById('turmaNome').value = nome;
  document.getElementById('buscaTurma').value = nome;
  document.getElementById('resultadosTurma').innerHTML = '';
  document.getElementById('turmaEscolhida').textContent = 'Selecionada: ' + nome;
}

async function matricular() {
  const idAluno = document.getElementById('alunoId').value;
  const msg = document.getElementById('msg');
  if (!idAluno) { msg.innerHTML = '<span style="color:var(--danger)">Selecione um aluno.</span>'; return; }

  const dados = {
    turma_id: document.getElementById('turmaId').value,
    turma_nome: document.getElementById('turmaNome').value,
    valor_contratado: document.getElementById('valorContratado').value,
    forma_pagamento: document.getElementById('formaPagamento').value,
    observacao: document.getElementById('observacao').value.trim(),
  };
  msg.textContent = 'Matriculando...';
  const r = await fetch(`/api/aluno/${idAluno}/matricular`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(dados),
  });
  const d = await r.json();
  if (d.erro) { msg.innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  msg.innerHTML = `<span style="color:var(--success)">Matriculado!</span>`;
}
</script></body></html>"""


PAGAMENTO_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Registrar Pagamento</title>\n<style>" + BASE_CSS + """
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1>Registrar Pagamento</h1>
<p class="subtitulo">Grava um "-Pagamento Realizado" seguindo o padrão combinado com o Diógenes (2026-09-14): forma, parcela, valor e data — isso soma de verdade no "Recebido" do aluno, confirmado ao vivo.</p>

<div class="card">
  <label>Aluno</label>
  <div class="autocomplete-box">
    <input type="text" id="buscaAluno" placeholder="Digite o nome do aluno..." autocomplete="off">
    <div id="resultadosAluno" class="autocomplete-list"></div>
  </div>
  <input type="hidden" id="alunoId">
  <div id="alunoEscolhido" style="margin-top:8px; font-weight:600;"></div>

  <label style="margin-top:16px;">Forma de Pagamento</label>
  <select id="formaPagamento">
    <option value="">Selecione</option>
    <option value="cartao">Cartão de Crédito</option>
    <option value="debito">Débito Bancário</option>
    <option value="especie">Dinheiro</option>
    <option value="boleto">Boleto Bancário</option>
    <option value="cheque">Cheque</option>
  </select>

  <label style="margin-top:16px;">Parcela / mês de referência</label>
  <input type="text" id="parcela" placeholder="Ex: 3/12, ou Setembro/2026" autocomplete="off">

  <label style="margin-top:16px;">Valor pago (R$)</label>
  <input type="number" id="valor" step="0.01" min="0.01" placeholder="0,00">

  <label style="margin-top:16px;">Data do pagamento</label>
  <input type="text" id="dataPagamento" placeholder="DD/MM/AAAA" autocomplete="off">

  <label style="margin-top:16px;">Observação (opcional)</label>
  <textarea id="observacao" rows="3" style="width:100%;"></textarea>

  <div style="margin-top:16px;">
    <button class="acao" onclick="registrar()">Registrar Pagamento</button>
  </div>
  <div id="msg" style="margin-top:10px;"></div>
</div>
</div>

<script>""" + BASE_JS + """
let alunoDebounce = null;
document.getElementById('buscaAluno').addEventListener('input', (e) => {
  const q = e.target.value;
  clearTimeout(alunoDebounce);
  document.getElementById('alunoId').value = '';
  if (q.length < 3) { document.getElementById('resultadosAluno').innerHTML = ''; return; }
  alunoDebounce = setTimeout(async () => {
    const r = await fetch('/api/aluno/buscar?q=' + encodeURIComponent(q));
    const alunos = await r.json();
    document.getElementById('resultadosAluno').innerHTML = (alunos.erro ? [] : alunos).map(a =>
      `<div class="item" onclick='escolherAluno("${a.id_aluno}", ${JSON.stringify(a.nome)})'>${a.nome} ${a.matricula ? '— matrícula ' + a.matricula : ''}</div>`
    ).join('');
  }, 300);
});

function escolherAluno(id, nome) {
  document.getElementById('alunoId').value = id;
  document.getElementById('buscaAluno').value = nome;
  document.getElementById('resultadosAluno').innerHTML = '';
  document.getElementById('alunoEscolhido').textContent = 'Selecionado: ' + nome;
}

async function registrar() {
  const idAluno = document.getElementById('alunoId').value;
  const msg = document.getElementById('msg');
  if (!idAluno) { msg.innerHTML = '<span style="color:var(--danger)">Selecione um aluno.</span>'; return; }

  const dados = {
    forma_pagamento: document.getElementById('formaPagamento').value,
    parcela: document.getElementById('parcela').value.trim(),
    valor: document.getElementById('valor').value,
    data_pagamento: document.getElementById('dataPagamento').value.trim(),
    observacao: document.getElementById('observacao').value.trim(),
  };
  msg.textContent = 'Registrando...';
  const r = await fetch(`/api/aluno/${idAluno}/pagamento`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(dados),
  });
  const d = await r.json();
  if (d.erro) { msg.innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  msg.innerHTML = `<span style="color:var(--success)">Pagamento registrado!</span>`;
}
</script></body></html>"""


INTERESSADO_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Cadastrar Interessados</title>\n<style>" + BASE_CSS + """
  .page { max-width: 640px; }
  .campo { margin-top: 14px; }
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1>Cadastrar Interessados</h1>
<p class="subtitulo">Pedido do Diógenes (2026-09-12): todo interessado precisa ficar numa das turmas "-I-(curso)" — aqui isso é obrigatório, não dá pra cadastrar sem escolher a turma. A Situação já sai fixa como "Interessado".</p>

<div class="card">
  <label>Nome *</label>
  <input type="text" id="nome" placeholder="Nome completo" autocomplete="off">

  <div class="campo"><label>E-mail</label>
  <input type="text" id="email" placeholder="email@exemplo.com" autocomplete="off"></div>

  <div class="campo"><label>Telefone fixo</label>
  <input type="text" id="fixo" placeholder="(81) 0000-0000" autocomplete="off"></div>

  <div class="campo"><label>Celular</label>
  <input type="text" id="celular" placeholder="(81) 90000-0000" autocomplete="off"></div>

  <div class="campo">
    <label>Turma de interesse * <span class="fonte">(obrigatório)</span></label>
    <select id="turmaInteresse"><option value="">Carregando turmas...</option></select>
  </div>

  <div class="campo">
    <label>Resumo / observações</label>
    <textarea id="resumo" rows="4" style="width:100%;" placeholder="O que o interessado procura, como chegou até aqui, etc."></textarea>
  </div>

  <div style="margin-top:16px;">
    <button class="acao" onclick="cadastrar()">Cadastrar</button>
  </div>
  <div id="msg" style="margin-top:10px;"></div>
</div>
</div>

<script>""" + BASE_JS + """
(async function carregarTurmas() {
  const sel = document.getElementById('turmaInteresse');
  const r = await fetch('/api/turmas-interessados');
  const turmas = await r.json();
  if (turmas.erro) { sel.innerHTML = `<option value="">Erro: ${turmas.erro}</option>`; return; }
  sel.innerHTML = '<option value="">Selecione a turma de interesse</option>' +
    turmas.map(t => `<option value="${t.id_turma}" data-nome="${t.nome.replace(/"/g, '&quot;')}">${t.nome}</option>`).join('');
})();

async function cadastrar() {
  const msg = document.getElementById('msg');
  const nome = document.getElementById('nome').value.trim();
  const sel = document.getElementById('turmaInteresse');
  const turmaId = sel.value;
  const turmaNome = turmaId ? sel.options[sel.selectedIndex].dataset.nome : '';
  if (!nome) { msg.innerHTML = '<span style="color:var(--danger)">Nome é obrigatório.</span>'; return; }
  if (!turmaId) { msg.innerHTML = '<span style="color:var(--danger)">Escolha a turma de interesse — é obrigatório.</span>'; return; }

  const dados = {
    nome, turma_id: turmaId, turma_nome: turmaNome,
    email: document.getElementById('email').value.trim(),
    fixo: document.getElementById('fixo').value.trim(),
    celular: document.getElementById('celular').value.trim(),
    resumo: document.getElementById('resumo').value.trim(),
  };
  msg.textContent = 'Cadastrando...';
  const r = await fetch('/api/interessados/cadastrar', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(dados),
  });
  const d = await r.json();
  if (d.erro) { msg.innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  msg.innerHTML = `<span style="color:var(--success)">Cadastrado e colocado em "${turmaNome}" (id ${d.id_aluno})!</span>`;
}
</script></body></html>"""


INTERESSADOS_TRIAGEM_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Interessados</title>\n<style>" + BASE_CSS + """
  .telefone { font-family: monospace; font-size: 0.95rem; }
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1>Interessados</h1>
<p class="subtitulo">Quem está na turma de triagem "-IA- Interessados" (cadastrado, mas ainda sem curso definido) — ligue, confira o resumo, e depois coloque na turma "-I-(curso)" certa.</p>
<div id="lista">Carregando...</div>
</div>

<script>""" + BASE_JS + """
async function carregar() {
  const div = document.getElementById('lista');
  const r = await fetch('/api/interessados/triagem');
  const d = await r.json();
  if (d.erro) { div.innerHTML = `<div class="card">Erro: ${d.erro}</div>`; return; }
  if (!d.id_turma) { div.innerHTML = '<div class="card">A turma "-IA- Interessados" ainda não existe — ninguém foi colocado lá ainda.</div>'; return; }
  if (!d.alunos.length) { div.innerHTML = '<div class="card">Ninguém aguardando triagem no momento.</div>'; return; }

  div.innerHTML = d.alunos.map(a => `
    <div class="card aluno-card" id="item-${a.id_aluno}">
      <b>${a.nome}</b> <span class="fonte">— desde ${a.data_entrada}</span><br>
      <span class="telefone">${a.telefone || '(sem telefone cadastrado)'}</span>
      <div style="margin-top:10px;">
        <button class="btn-nao" onclick="verResumo('${a.id_aluno}')">Ver resumo do histórico</button>
        <a href="/alunos?id=${a.id_aluno}" target="_blank" class="btn-nao" style="text-decoration:none; display:inline-block;">Ver cadastro completo</a>
      </div>
      <div id="detalhe-${a.id_aluno}" style="margin-top:10px;"></div>
    </div>
  `).join('');
}

async function verResumo(idAluno) {
  const div = document.getElementById('detalhe-' + idAluno);
  div.innerHTML = 'Carregando resumo...';
  const r = await fetch(`/api/aluno/${idAluno}/resumo`);
  const d = await r.json();
  if (d.erro) { div.innerHTML = 'Erro: ' + d.erro; return; }

  div.innerHTML = `
    <div class="fonte" style="margin-bottom:8px;">${d.resumo_historico}</div>
    <label>Mover pra turma</label>
    <select id="turma-${idAluno}"><option value="">Carregando turmas...</option></select>
    <div style="margin-top:8px;">
      <button class="acao" onclick="mover('${idAluno}')">Mover</button>
    </div>
    <div id="msg-${idAluno}" style="margin-top:6px;"></div>
  `;
  const sel2 = document.getElementById('turma-' + idAluno);
  const rt = await fetch('/api/turmas-interessados');
  const turmas = await rt.json();
  if (turmas.erro) { sel2.innerHTML = `<option value="">Erro: ${turmas.erro}</option>`; return; }
  sel2.innerHTML = '<option value="">Selecione a turma</option>' +
    turmas.map(t => `<option value="${t.id_turma}" data-nome="${t.nome.replace(/"/g, '&quot;')}">${t.nome}</option>`).join('');
}

async function mover(idAluno) {
  const sel = document.getElementById('turma-' + idAluno);
  const turmaId = sel.value;
  const turmaNome = turmaId ? sel.options[sel.selectedIndex].dataset.nome : '';
  const msg = document.getElementById('msg-' + idAluno);
  if (!turmaId) { msg.innerHTML = '<span style="color:var(--danger)">Escolha uma turma.</span>'; return; }
  msg.textContent = 'Movendo...';
  const r = await fetch(`/api/interessados/triagem/${idAluno}/mover`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ turma_id: turmaId, turma_nome: turmaNome }),
  });
  const d = await r.json();
  if (d.erro) { msg.innerHTML = `<span style="color:var(--danger)">${d.erro}</span>`; return; }
  msg.innerHTML = `<span style="color:var(--success)">Movido pra "${turmaNome}"!</span>`;
}

carregar();
</script></body></html>"""


CALCULADORA_MULTA_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Calculadora de Multa</title>\n<style>" + BASE_CSS + """
  .page { max-width: 480px; }
  .resultado { margin-top:16px; padding:14px; border-radius:var(--radius-sm); background:var(--success-bg); font-size:1.1rem; }
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1>Calculadora de Multa de Cancelamento</h1>
<p class="subtitulo">Regra combinada com o Diógenes (2026-09-14): 10% ou 20% do valor dos módulos não cursados, dependendo do contrato — o sistema não decide sozinho qual percentual usar, só calcula os dois pra você escolher.</p>

<div class="card">
  <label>Valor dos módulos não cursados (R$)</label>
  <input type="number" id="valorModulos" step="0.01" min="0" placeholder="0,00">
  <div style="margin-top:16px; display:flex; gap:10px;">
    <button class="acao" onclick="calcular(0.10)">Calcular multa de 10%</button>
    <button class="acao" onclick="calcular(0.20)">Calcular multa de 20%</button>
  </div>
  <div id="resultado" class="resultado" style="display:none;"></div>
</div>
</div>

<script>""" + BASE_JS + """
function calcular(percentual) {
  const valor = parseFloat(document.getElementById('valorModulos').value);
  const div = document.getElementById('resultado');
  if (isNaN(valor) || valor < 0) {
    div.style.display = 'block';
    div.innerHTML = '<span style="color:var(--danger)">Informe um valor válido.</span>';
    return;
  }
  const multa = valor * percentual;
  const fmt = (v) => 'R$ ' + v.toFixed(2).replace('.', ',');
  div.style.display = 'block';
  div.innerHTML = `Multa de <b>${(percentual * 100).toFixed(0)}%</b> sobre ${fmt(valor)}: <b>${fmt(multa)}</b>`;
}
</script></body></html>"""


TURMAS_ENTRADA_HTML = "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Turmas de Entrada</title>\n<style>" + BASE_CSS + """
  table { width:100%; border-collapse:collapse; margin-top:6px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--border); }
  .linha-ok { background:var(--success-bg); }
  .linha-baixo { background:var(--danger-bg); }
  tr.linha-turma { cursor:pointer; }
  .lista-alunos { list-style:none; margin:0; padding:0; columns:2; }
  .lista-alunos li { padding:2px 0; }
  .tag-categoria { font-size:0.75rem; padding:1px 6px; border-radius:10px; margin-left:6px; }
  .tag-categoria.primeira-vez { background:var(--success-bg); color:var(--success); }
  .tag-categoria.refazendo, .tag-categoria.abandono { background:var(--danger-bg); color:var(--danger); }
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1>Turmas de Entrada (J1 / PY1)</h1>
<p class="subtitulo">
  J1 e PY1 são a porta de entrada dos clientes novos na academia — só compensa iniciar a turma com pelo menos
  <b id="limiarTexto">10</b> matriculados de <b>primeira vez de verdade</b> (nem refazendo, nem ex-aluno, nem continuidade).
  Mostra as turmas mais recentes de cada módulo, contando só pela marca do nome (rápido, sem consultar o Fuctura aluno por aluno).
  Clique numa linha pra conferir a lista de alunos.
</p>
<button class="btn-sim" onclick="carregar()">Carregar turmas recentes</button>
<div id="resultado" style="margin-top:14px;"></div>
</div>
<script>""" + BASE_JS + """
let turmasCarregadas = [];

function toggleAlunos(idTurma) {
  const linha = document.getElementById(`detalhe-${idTurma}`);
  if (!linha) return;
  linha.hidden = !linha.hidden;
}

async function carregar() {
  const div = document.getElementById('resultado');
  div.innerHTML = '<div class="card">Buscando turmas J1/PY1 recentes...</div>';
  const r = await fetch('/api/turmas-entrada');
  const d = await r.json();
  if (d.erro) { div.innerHTML = `<div class="card">Erro: ${d.erro}</div>`; return; }
  document.getElementById('limiarTexto').textContent = d.limiar_minimo;
  turmasCarregadas = d.turmas;
  if (!d.turmas.length) {
    div.innerHTML = '<div class="card">Nenhuma turma J1/PY1 encontrada.</div>';
    return;
  }
  div.innerHTML = `
    <div class="card">
      <table>
        <thead><tr><th>Turma</th><th>Módulo</th><th>Data</th><th>Matriculados</th><th>Primeira vez</th><th>Situação</th></tr></thead>
        <tbody>
          ${d.turmas.map(t => `
            <tr class="linha-turma ${t.atinge_minimo ? 'linha-ok' : 'linha-baixo'}" onclick="toggleAlunos('${t.id_turma}')">
              <td>${t.nome_turma}</td>
              <td>${t.modulo}</td>
              <td>${t.data || '—'}</td>
              <td>${t.total_matriculados}</td>
              <td><b>${t.primeira_vez}</b> / ${t.limiar_minimo}</td>
              <td>${t.atinge_minimo ? '✓ Atinge o mínimo' : '⚠ Abaixo do mínimo'}</td>
            </tr>
            <tr id="detalhe-${t.id_turma}" hidden>
              <td colspan="6">
                ${t.alunos.length ? `<ul class="lista-alunos">${t.alunos.map(a => `
                  <li>${a.nome} <span class="tag-categoria ${a.categoria === 'Primeira vez' ? 'primeira-vez' : a.categoria.toLowerCase()}">${a.categoria}</span></li>
                `).join('')}</ul>` : '<span class="fonte">Nenhum aluno matriculado ainda.</span>'}
              </td>
            </tr>
          `).join('')}
        </tbody>
      </table>
      <div class="fonte" style="margin-top:8px;">
        "Primeira vez" conta só pela marca do nome (mesmo método usado no Fechamento de Turma) — turmas recém-criadas,
        com poucas matrículas ainda, podem estar abaixo do mínimo só porque ainda estão enchendo, não necessariamente
        um problema.
      </div>
    </div>
  `;
}
carregar();
</script></body></html>"""


def admin_html():
    return "<!doctype html><html lang=\"pt-br\"><head><meta charset=\"utf-8\">\n<title>Configurações — Sistema Fuctura</title>\n<style>" + BASE_CSS + """
  .page { max-width: 640px; }
  .nota { font-size:0.78rem; color:var(--text-muted); margin-top:4px; }
  .checkbox-linha { display:flex; align-items:center; gap:6px; font-size:0.85rem; font-weight:400; text-transform:none; letter-spacing:0; color:var(--text); }
  .checkbox-linha input { width:auto; }
</style></head><body>
<div class="page">
<a class="voltar" href="/">← voltar</a>
<h1>Configurações</h1>
<div id="app">Carregando...</div>
</div>
<script>""" + BASE_JS + """
async function carregar() {
  const r = await fetch('/api/config');
  const cfg = await r.json();
  const app = document.getElementById('app');
  const provs = Object.keys(cfg.providers);
  app.innerHTML = `
    <div class="card">
      <label>Provedor de IA ativo (leitura de ata)</label>
      <select id="providerAtivo">${provs.map(p => `<option value="${p}" ${cfg.provider_ativo===p?'selected':''}>${p}</option>`).join('')}</select>
      <div class="nota">O uso mostrado abaixo é uma estimativa local, não o saldo real da conta no provedor.</div>
    </div>
    ${provs.map(p => `
      <div class="card">
        <b>${p}</b>
        <label>Chave de API</label>
        <input type="password" id="key_${p}" value="${cfg.providers[p].api_key}">
        <label>Modelo</label>
        <input type="text" id="modelo_${p}" value="${cfg.providers[p].modelo}">
        <label>Orçamento mensal estimado (USD)</label>
        <input type="number" step="0.01" id="orc_${p}" value="${cfg.providers[p].orcamento_mensal_usd}">
        <div class="nota">Uso estimado este mês: US$ ${(cfg.providers[p].uso_mes_atual||0).toFixed(2)}</div>
      </div>
    `).join('')}
    <div class="card">
      <b>CORA (boletos em aberto)</b>
      <div class="nota">Estrutura pronta, esperando credenciais reais — enquanto não preenchido, a tela de Reconciliação mostra "CORA não configurado" em vez de dar erro.</div>
      <label style="margin-top:10px;">Client ID</label>
      <input type="text" id="cora_client_id" value="${cfg.cora.client_id}">
      <label>Client Secret</label>
      <input type="password" id="cora_client_secret" value="${cfg.cora.client_secret}">
      <label>Caminho do certificado (se a CORA exigir mTLS)</label>
      <input type="text" id="cora_certificado_path" value="${cfg.cora.certificado_path}">
      <label>Ambiente</label>
      <select id="cora_ambiente">
        <option value="sandbox" ${cfg.cora.ambiente === 'sandbox' ? 'selected' : ''}>Sandbox (teste)</option>
        <option value="producao" ${cfg.cora.ambiente === 'producao' ? 'selected' : ''}>Produção</option>
      </select>
      <div class="checkbox-linha" style="margin-top:8px;">
        <input type="checkbox" id="cora_configurado" ${cfg.cora.configurado ? 'checked' : ''}>
        <label for="cora_configurado" style="margin:0;">Marcar como configurado (só depois de preencher e testar as credenciais acima)</label>
      </div>
    </div>
    <div class="card">
      <b>Gmail (criar rascunho COM anexo)</b>
      <div class="nota">
        Credencial do APP (compartilhada por todos) — cada funcionário ainda precisa conectar a própria conta
        individualmente, na tela de Reconciliação. Passo a passo pra criar essa credencial no Google Cloud Console
        está comentado no topo de <code>gmail_client.py</code>. URI de redirecionamento a cadastrar lá:
        <code>https://sistema-mascara.69-169-102-111.sslip.io/api/gmail/callback</code>
      </div>
      <label style="margin-top:10px;">Client ID</label>
      <input type="text" id="gmail_client_id" value="${cfg.gmail.client_id}">
      <label>Client Secret</label>
      <input type="password" id="gmail_client_secret" value="${cfg.gmail.client_secret}">
      <div class="checkbox-linha" style="margin-top:8px;">
        <input type="checkbox" id="gmail_configurado" ${cfg.gmail.configurado ? 'checked' : ''}>
        <label for="gmail_configurado" style="margin:0;">Marcar como configurado (só depois de criar a credencial acima)</label>
      </div>
    </div>
    <div class="card">
      <b>Drive (buscar contrato/ata em pastas compartilhadas)</b>
      <div class="nota">
        Reaproveita a MESMA credencial do Gmail acima (só precisa adicionar o escopo
        <code>drive.readonly</code> na Tela de Consentimento OAuth) — não precisa de Client ID/Secret próprios.
        Diferente do Gmail: essa é <b>uma conexão só</b>, de quem tem as pastas Contratos/Atas compartilhadas
        no próprio Drive — não é por funcionário. URI de redirecionamento a cadastrar no Google Cloud Console:
        <code>https://sistema-mascara.69-169-102-111.sslip.io/api/drive/callback</code>
      </div>
      <div id="drive_status" style="margin-top:10px;">Carregando status...</div>
      <div class="checkbox-linha" style="margin-top:8px;">
        <input type="checkbox" id="drive_configurado" ${cfg.drive.configurado ? 'checked' : ''}>
        <label for="drive_configurado" style="margin:0;">Marcar como configurado (só depois do escopo estar na Tela de Consentimento)</label>
      </div>
    </div>
    <div class="card">
      <label>Quem tem acesso a esta tela (um por linha) — pode ser o login numérico do Fuctura OU o nome que aparece no menu ao logar (ex: "Diogenes")</label>
      <textarea id="admins" rows="4" style="width:100%; font-family:monospace;">${cfg.admins.join('\\n')}</textarea>
      <div class="nota">Comparação por nome ignora acento/maiúscula e aceita nome parcial (ex: "Diogenes" cobre "Diógenes Souza Leão").</div>
    </div>
    <button class="acao" onclick="salvar()">Salvar</button>
    <span id="msg"></span>
  `;
  window._cfg = cfg;
  carregarStatusDrive();
}

async function carregarStatusDrive() {
  const div = document.getElementById('drive_status');
  const r = await fetch('/api/drive/status');
  const s = await r.json();
  if (!s.configurado) {
    div.innerHTML = '<span class="fonte">Marque "configurado" abaixo depois de adicionar o escopo do Drive na credencial.</span>';
  } else if (!s.conectado) {
    div.innerHTML = '<a class="btn-nao" style="text-decoration:none; display:inline-block;" href="/api/drive/autorizar">Conectar o Drive</a>';
  } else {
    div.innerHTML = `<span style="color:var(--success)">Conectado como ${s.email_conectado}</span> ` +
      '<button class="btn-nao" onclick="desconectarDrive()">Desconectar</button>';
  }
}

async function desconectarDrive() {
  await fetch('/api/drive/desconectar', {method: 'POST'});
  carregarStatusDrive();
}

async function salvar() {
  const cfg = window._cfg;
  cfg.provider_ativo = document.getElementById('providerAtivo').value;
  for (const p of Object.keys(cfg.providers)) {
    cfg.providers[p].api_key = document.getElementById('key_' + p).value;
    cfg.providers[p].modelo = document.getElementById('modelo_' + p).value;
    cfg.providers[p].orcamento_mensal_usd = parseFloat(document.getElementById('orc_' + p).value) || 0;
  }
  cfg.admins = document.getElementById('admins').value.split('\\n').map(s => s.trim()).filter(Boolean);
  cfg.cora = {
    client_id: document.getElementById('cora_client_id').value.trim(),
    client_secret: document.getElementById('cora_client_secret').value.trim(),
    certificado_path: document.getElementById('cora_certificado_path').value.trim(),
    ambiente: document.getElementById('cora_ambiente').value,
    configurado: document.getElementById('cora_configurado').checked,
  };
  cfg.gmail = {
    client_id: document.getElementById('gmail_client_id').value.trim(),
    client_secret: document.getElementById('gmail_client_secret').value.trim(),
    configurado: document.getElementById('gmail_configurado').checked,
  };
  cfg.drive = {
    configurado: document.getElementById('drive_configurado').checked,
  };
  const r = await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)});
  document.getElementById('msg').textContent = r.ok ? ' Salvo.' : ' Erro ao salvar.';
}
carregar();
</script></body></html>"""


def _ip_local():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    # 0.0.0.0 - aceita conexao de qualquer computador na mesma rede local,
    # nao so da propria maquina (e o ponto de rodar isso num PC fixo da
    # Fuctura e todo mundo acessar pelo navegador).
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    ip = _ip_local()
    print(f"Servidor rodando. Nesta máquina: http://127.0.0.1:{PORT}/")
    print(f"De outros computadores na mesma rede: http://{ip}:{PORT}/")
    print("Ctrl+C para parar.")
    if ABRIR_NAVEGADOR_AUTOMATICO:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}/")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

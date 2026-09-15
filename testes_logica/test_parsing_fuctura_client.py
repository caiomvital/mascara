"""
Testes de regressao pro parsing de HTML/texto do Fuctura (fuctura_client.py) -
sem rede nenhuma: monta uma resposta FALSA (so um objeto com .text/.content)
e chama o metodo de parsing real do cliente em cima dela, via
FucturaClient.__new__() (nunca chama __init__, entao nunca tenta logar de
verdade nem cria sessao HTTP).

Como rodar:
    cd testes_logica
    python test_parsing_fuctura_client.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fuctura_client as fuctura_client_module  # noqa: E402
from fuctura_client import FucturaClient, FucturaAuthError, parse_money  # noqa: E402

_falhas = []
_ok_count = 0


def relatar(nome, ok, detalhe=""):
    global _ok_count
    if ok:
        _ok_count += 1
        print(f"  OK     {nome}")
    else:
        _falhas.append((nome, detalhe))
        print(f"  FALHOU {nome}: {detalhe}")


class _RespostaFake:
    def __init__(self, texto="", dados_json=None):
        self.text = texto
        self.content = texto.encode("utf-8")
        self.status_code = 200
        self._dados_json = dados_json

    def json(self):
        return self._dados_json


def cliente_fake(texto_resposta):
    """FucturaClient com _get/_post falsos (sempre devolvem o mesmo texto
    fornecido) - nunca faz nenhuma chamada de rede, nunca tenta logar."""
    cliente = FucturaClient.__new__(FucturaClient)
    cliente._get = lambda *a, **k: _RespostaFake(texto_resposta)
    cliente._post = lambda *a, **k: _RespostaFake(texto_resposta)
    return cliente


def cliente_fake_get_json(paginas_json):
    """FucturaClient cujo _get devolve, em sequencia, uma resposta JSON por
    chamada (uma por pagina) - pra testar paginacao de
    buscar_comentarios_por_tipo() sem rede nenhuma."""
    cliente = FucturaClient.__new__(FucturaClient)
    chamadas = {"n": 0}

    def _get_fake(*a, **k):
        i = min(chamadas["n"], len(paginas_json) - 1)
        chamadas["n"] += 1
        return _RespostaFake(dados_json=paginas_json[i])

    cliente._get = _get_fake
    return cliente


# ---------------------------------------------------------------------------
# Caso 1 (padrao real: achado investigando buscar_alunos_por_nome, ja
# corrigido) - nome com sufixo tipo "(ADVOGADO)" logo apos </b> nao pode
# corromper a divisao matricula/celular/unidade - E o sufixo faz parte do
# nome de verdade (confirmado comparando com buscar_cadastro_completo:
# o Fuctura guarda "(ADVOGADO)" dentro do proprio campo nome), entao tem
# que aparecer no resultado, nao ser descartado.
# ---------------------------------------------------------------------------
def teste_busca_aluno_com_sufixo_advogado():
    html_fake = (
        '<a href="detalhes.php?idAluno=12345&x=1">algum texto<b>JOAO DA SILVA</b> '
        '(ADVOGADO)&nbsp;-&nbsp;123456&nbsp;-&nbsp;81999999999&nbsp;-&nbsp;ESP<br>'
    )
    resultados = cliente_fake(html_fake).buscar_alunos_por_nome("joao")
    ok = (
        len(resultados) == 1
        and resultados[0]["id_aluno"] == "12345"
        and resultados[0]["nome"] == "JOAO DA SILVA (ADVOGADO)"
        and resultados[0]["matricula"] == "123456"
        and resultados[0]["celular"] == "81999999999"
        and resultados[0]["unidade"] == "ESP"
    )
    relatar(
        "Caso 1: sufixo tipo (ADVOGADO) não corrompe a divisão de campos, e continua fazendo parte do nome",
        ok,
        f"resultado: {resultados}",
    )


# ---------------------------------------------------------------------------
# Caso 1c (padrao real: TEYLON NUNES GOMES, id 37640, achado 2026-09-08) -
# o Fuctura so deixa em NEGRITO o trecho que bateu na busca, nao o nome
# inteiro - buscar "tey" ou "teylon nunes" nao pode truncar o nome pro
# pedaco em negrito. O ponto fino: as vezes tem espaco entre </b> e o
# resto do nome (busca por palavra inteira), as vezes nao tem (busca no
# meio de uma palavra) - o parser tem que preservar isso do jeito que
# esta no HTML, nunca inserir nem remover espaco por conta propria.
# ---------------------------------------------------------------------------
def teste_busca_aluno_negrito_parcial():
    casos = [
        ("<b>TEY</b>LON NUNES GOMES (ADVOGADO)", "TEYLON NUNES GOMES (ADVOGADO)"),
        ("<b>TEYLON</b> NUNES GOMES (ADVOGADO)", "TEYLON NUNES GOMES (ADVOGADO)"),
        ("<b>TEYLON NUNES</b> GOMES (ADVOGADO)", "TEYLON NUNES GOMES (ADVOGADO)"),
    ]
    for trecho_nome, esperado in casos:
        html_fake = (
            f'<a href="detalhes.php?idAluno=37640&x=1">algum texto{trecho_nome}'
            '&nbsp;-&nbsp;913782&nbsp; - &nbsp;96732042&nbsp; - &nbsp;ADV NATALIA<br>'
        )
        resultados = cliente_fake(html_fake).buscar_alunos_por_nome("teylon")
        nome_obtido = resultados[0]["nome"] if resultados else None
        relatar(
            f"Caso 1c: negrito parcial reconstitui o nome completo ({trecho_nome!r})",
            nome_obtido == esperado,
            f"nome obtido: {nome_obtido!r} (esperado {esperado!r})",
        )


def teste_busca_aluno_sem_sufixo_continua_ok():
    html_fake = (
        '<a href="detalhes.php?idAluno=67890&x=1">algum texto<b>MARIA SOUZA</b>'
        '&nbsp;-&nbsp;654321&nbsp;-&nbsp;81888888888&nbsp;-&nbsp;BV<br>'
    )
    resultados = cliente_fake(html_fake).buscar_alunos_por_nome("maria")
    ok = (
        len(resultados) == 1
        and resultados[0]["matricula"] == "654321"
        and resultados[0]["celular"] == "81888888888"
        and resultados[0]["unidade"] == "BV"
    )
    relatar(
        "Caso 1b (controle): busca sem sufixo continua funcionando normalmente",
        ok,
        f"resultado: {resultados}",
    )


# ---------------------------------------------------------------------------
# Caso 4 (mesma CLASSE de bug do Caso 1c, encontrado proativamente
# procurando por outras ocorrencias em 2026-09-08, sem confirmar ainda se
# ja aconteceu com dado real) - regex tipo '[^<]*...</textarea>' exige que
# NENHUM '<' apareca antes do fechamento real da tag. Se acontecer, o
# regex inteiro falha (nao so trunca) e o campo volta vazio, sem erro
# nenhum - pior que truncar, e perda TOTAL e silenciosa do dado.
# ---------------------------------------------------------------------------
def teste_observacoes_com_menor_que_no_meio():
    html_fake = '<textarea name="observacoes" id="observacoes">aluno disse nota <importante> conferir depois</textarea>'
    dados = cliente_fake(html_fake).buscar_cadastro_completo("1")
    relatar(
        "Caso 4: observações com '<' no meio não pode voltar vazio",
        dados["observacoes"] == "aluno disse nota <importante> conferir depois",
        f"observacoes lidas: {dados['observacoes']!r}",
    )


# ---------------------------------------------------------------------------
# Caso 2 - o Fuctura as vezes marca "selected" TANTO no placeholder quanto
# na opcao real do mesmo <select> - tem que pegar a ULTIMA ocorrencia
# (a real), nao a primeira (o placeholder).
# ---------------------------------------------------------------------------
def teste_cadastro_completo_pega_ultima_opcao_selecionada():
    html_fake = (
        '<select name="sexo" id="sexo">'
        '<option value="" selected="selected">Selecione</option>'
        '<option value="M" selected="selected">Masculino</option>'
        '</select>'
    )
    dados = cliente_fake(html_fake).buscar_cadastro_completo("1")
    relatar(
        "Caso 2: pega a última opção 'selected' (a real), não o placeholder",
        dados["sexo"] == "M",
        f"sexo lido: {dados['sexo']!r} (esperado 'M')",
    )


# ---------------------------------------------------------------------------
# Caso 3 - parse_money em casos limite: vazio, com "R$", com "&nbsp;", texto
# nao numerico. Nenhum pode lançar exceção nem quebrar a leitura do perfil.
# ---------------------------------------------------------------------------
def teste_parse_money_casos_limite():
    casos = [
        ("", 0.0),
        ("R$ 1.234,56", 1234.56),
        ("&nbsp;500,00", 500.0),
        ("abc", 0.0),
        (None, 0.0),
        ("0,00", 0.0),
    ]
    for entrada, esperado in casos:
        resultado = parse_money(entrada)
        relatar(
            f"Caso 3: parse_money({entrada!r}) == {esperado}",
            resultado == esperado,
            f"retornou {resultado}",
        )


# ---------------------------------------------------------------------------
# Caso 5 (metodo novo, 2026-09-08, adicionado ao encapsular
# server_datatables_acomp.php pra reuso - foi essencial pra fechar a
# auditoria do flood) - buscar_comentarios_por_tipo() precisa: (a) parsear
# a linha (so 5 campos uteis, sem id_aluno - de proposito, ver docstring),
# e (b) paginar sozinho ate iTotalDisplayRecords, parando na hora certa
# mesmo quando ele vem como STRING (achado ao vivo: o Fuctura devolve
# iTotalDisplayRecords como texto, nao numero, o que quebrou a primeira
# versao com um TypeError comparando int >= str).
# ---------------------------------------------------------------------------
def teste_buscar_comentarios_por_tipo_pagina_ate_o_total():
    linha = lambda id_acomp: [
        f"<a href='lista_alunos.php?idAcomp={id_acomp}'><font color='#000000'>{id_acomp}</font></a>",
        f"<a href='lista_alunos.php?idAcomp={id_acomp}'><font color='#000000'>04/09/2026</font></a>",
        "<a href='x'><font color='#000000'>--Removido Por Flood--</font></a>",
        "<a href='x'><font color='#000000'>-Comentário</font></a>",
        "<a href='x'><font color='#000000'>--Removido Por Flood--</font></a>",
    ]
    pagina1 = {"iTotalDisplayRecords": "3", "aaData": [linha("1"), linha("2")]}
    pagina2 = {"iTotalDisplayRecords": "3", "aaData": [linha("3")]}
    resultados = cliente_fake_get_json([pagina1, pagina2]).buscar_comentarios_por_tipo(
        "3", termo="Flood", tamanho_pagina=2,
    )
    ok = (
        len(resultados) == 3
        and [r["id_acomp"] for r in resultados] == ["1", "2", "3"]
        and resultados[0]["titulo"] == "--Removido Por Flood--"
    )
    relatar(
        "Caso 5: buscar_comentarios_por_tipo pagina até o total (mesmo com iTotalDisplayRecords em string)",
        ok,
        f"resultados: {resultados}",
    )


def teste_resolver_id_aluno_por_acomp():
    html_fake = '<input type="hidden" name="idAluno" id="idAluno" value="26681">'
    id_aluno = cliente_fake(html_fake).resolver_id_aluno_por_acomp("170602")
    relatar(
        "Caso 5b: resolver_id_aluno_por_acomp lê o idAluno da página carregada via idAcomp",
        id_aluno == "26681",
        f"id_aluno obtido: {id_aluno!r}",
    )


def _cliente_com_status_login(status_code):
    """FucturaClient cujo session.post (usado por entrar(), nao _post())
    devolve um status_code fixo, sem rede nenhuma."""
    cliente = FucturaClient.__new__(FucturaClient)
    resposta = _RespostaFake("")
    resposta.status_code = status_code
    cliente.session = type("S", (), {"post": lambda self, *a, **k: resposta})()
    cliente.login = "0"
    cliente.senha = "x"
    return cliente


def teste_entrar_distingue_bloqueio_de_infraestrutura_de_senha_errada():
    # achado real 2026-09-12: um bloqueio de IP (403 "Acesso negado", pagina
    # de hospedagem) nao pode virar "Login ou senha incorretos" - confunde
    # quem le com "preciso trocar minha senha", quando o problema real e o
    # IP do servidor nao estar autorizado no Fuctura.
    for status in (403, 406, 500, 503):
        try:
            _cliente_com_status_login(status).entrar()
            ok = False
            detalhe = "esperava FucturaAuthError, nao levantou nada"
        except FucturaAuthError as e:
            ok = "Login ou senha incorretos" not in str(e) and str(status) in str(e)
            detalhe = f"mensagem: {e}"
        relatar(f"entrar() com HTTP {status} não usa a mensagem de senha errada", ok, detalhe)

    try:
        _cliente_com_status_login(200).entrar()
        ok, detalhe = False, "esperava FucturaAuthError, nao levantou nada"
    except FucturaAuthError as e:
        ok = str(e) == "Login ou senha incorretos."
        detalhe = f"mensagem: {e}"
    relatar("entrar() com HTTP 200 (login rejeitado de verdade) ainda usa a mensagem de senha errada", ok, detalhe)


class _SessaoContavel:
    """Sessao fake que conta chamadas de login (post em ctrl_acesso.php) -
    pra testar o relogin por CONTAGEM de requisicao (achado real
    2026-09-15: sessao do Fuctura degrada silenciosamente apos varias
    requisicoes acumuladas - escrita para de persistir, mesmo devolvendo
    HTTP 200) sem rede nenhuma."""

    def __init__(self):
        self.chamadas_post_login = 0
        self.headers = {}

    def post(self, url, **kwargs):
        if "ctrl_acesso.php" in url:
            self.chamadas_post_login += 1
        r = _RespostaFake("")
        r.status_code = 302
        return r

    def get(self, url, **kwargs):
        return _RespostaFake("")

    def close(self):
        pass


def teste_relogin_por_contagem_de_requisicao():
    sessao_fake = _SessaoContavel()
    sessao_original_cls = fuctura_client_module.requests.Session
    fuctura_client_module.requests.Session = lambda: sessao_fake
    try:
        cliente = FucturaClient.__new__(FucturaClient)
        cliente.session = sessao_fake
        cliente.login, cliente.senha = "0", "x"
        cliente._logged_in_at = time.time()
        cliente._requests_desde_login = 0

        limite = FucturaClient.LIMITE_REQUESTS_POR_SESSAO
        for _ in range(limite):
            cliente._get("qualquer.php")
        relatar(
            f"depois de exatamente {limite} requisições, ainda não relogou (o limite só dispara na requisição seguinte)",
            sessao_fake.chamadas_post_login == 0 and cliente._requests_desde_login == limite,
            f"chamadas_post_login={sessao_fake.chamadas_post_login}, _requests_desde_login={cliente._requests_desde_login}",
        )

        cliente._get("qualquer.php")  # a requisicao numero `limite + 1` deve disparar o relogin
        relatar(
            f"na requisição {limite + 1}, relogou automaticamente (contagem atingiu o limite)",
            sessao_fake.chamadas_post_login == 1,
            f"chamadas_post_login={sessao_fake.chamadas_post_login}",
        )
        relatar(
            "contador de requisições zera depois do relogin automático",
            cliente._requests_desde_login == 1,  # a propria requisicao que disparou o relogin ja conta como 1 na sessao nova
            f"_requests_desde_login={cliente._requests_desde_login}",
        )
    finally:
        fuctura_client_module.requests.Session = sessao_original_cls


def _cliente_capturando_post():
    """FucturaClient cujo _post so guarda o 'data' recebido (em
    chamadas['data']) em vez de fazer rede - pra inspecionar exatamente o
    que gravar_comentario/editar_comentario mandam pro Fuctura."""
    cliente = FucturaClient.__new__(FucturaClient)
    chamadas = {"data": None}
    cliente._get = lambda *a, **k: _RespostaFake("")

    def _post_fake(path, data):
        chamadas["data"] = data
        return _RespostaFake("")

    cliente._post = _post_fake
    return cliente, chamadas


def teste_gravar_comentario_manda_nome_da_turma_no_busca_auto_complete():
    """ACHADO REAL 2026-09-15: o Fuctura so vincula a turma de verdade
    (matricula tipo '15' + turma_id) se 'buscaTurmaAutoComplete' vier
    preenchido com o NOME da turma - confirmado ao vivo que 'assunto'
    sozinho nao basta (reconciliacao_devedor.aplicar_acao usa um assunto
    descritivo, nunca o nome da turma, e por isso NUNCA vinculava a
    turma de controle antes desta correcao)."""
    cliente, chamadas = _cliente_capturando_post()
    cliente.gravar_comentario(
        "123", "Matrícula em turma de controle (reconciliação)", "15", "texto",
        turma_id="1375", turma_nome="Devedor/Pendência",
    )
    relatar(
        "gravar_comentario com turma_nome explicito manda ele em buscaTurmaAutoComplete (nao o assunto)",
        chamadas["data"]["buscaTurmaAutoComplete"] == "Devedor/Pendência",
        f"buscaTurmaAutoComplete={chamadas['data'].get('buscaTurmaAutoComplete')!r}",
    )

    cliente2, chamadas2 = _cliente_capturando_post()
    cliente2.gravar_comentario("123", "-IA- Interessados", "15", "texto", turma_id="1375")
    relatar(
        "gravar_comentario sem turma_nome cai pro assunto (compatibilidade com call sites antigos)",
        chamadas2["data"]["buscaTurmaAutoComplete"] == "-IA- Interessados",
        f"buscaTurmaAutoComplete={chamadas2['data'].get('buscaTurmaAutoComplete')!r}",
    )

    cliente3, chamadas3 = _cliente_capturando_post()
    cliente3.gravar_comentario("123", "-Comentário qualquer", "3", "texto")
    relatar(
        "gravar_comentario sem turma_id manda buscaTurmaAutoComplete vazio (nao inventa turma)",
        chamadas3["data"]["buscaTurmaAutoComplete"] == "",
        f"buscaTurmaAutoComplete={chamadas3['data'].get('buscaTurmaAutoComplete')!r}",
    )


def teste_editar_comentario_manda_nome_da_turma_no_busca_auto_complete():
    cliente, chamadas = _cliente_capturando_post()
    cliente.editar_comentario(
        "123", "999", "titulo qualquer -- IA --", "15", "texto",
        turma_id="1375", turma_nome="Aguardando Advogado",
    )
    relatar(
        "editar_comentario com turma_nome explicito manda ele em buscaTurmaAutoComplete (nao o assunto)",
        chamadas["data"]["buscaTurmaAutoComplete"] == "Aguardando Advogado",
        f"buscaTurmaAutoComplete={chamadas['data'].get('buscaTurmaAutoComplete')!r}",
    )


def main():
    print("Rodando testes de parsing do fuctura_client.py (sem rede nenhuma)...\n")
    teste_busca_aluno_com_sufixo_advogado()
    teste_busca_aluno_negrito_parcial()
    teste_busca_aluno_sem_sufixo_continua_ok()
    teste_observacoes_com_menor_que_no_meio()
    teste_cadastro_completo_pega_ultima_opcao_selecionada()
    teste_parse_money_casos_limite()
    teste_buscar_comentarios_por_tipo_pagina_ate_o_total()
    teste_resolver_id_aluno_por_acomp()
    teste_entrar_distingue_bloqueio_de_infraestrutura_de_senha_errada()
    teste_relogin_por_contagem_de_requisicao()
    teste_gravar_comentario_manda_nome_da_turma_no_busca_auto_complete()
    teste_editar_comentario_manda_nome_da_turma_no_busca_auto_complete()

    print(f"\n{'=' * 70}")
    print(f"Total OK: {_ok_count} | Total FALHOU: {len(_falhas)}")
    if _falhas:
        print("\nFalhas:")
        for nome, detalhe in _falhas:
            print(f"  - {nome}: {detalhe}")
        sys.exit(1)
    print("Nenhuma regressão detectada no parsing.")


if __name__ == "__main__":
    main()

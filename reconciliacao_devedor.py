"""
Reconciliacao Situacao x Turma de Controle (Devedor/Advogado/Cancelado).

Processo descrito por Diogenes (ver RASCUNHO_reconciliacao_devedor.md): pra
todo aluno com Situacao=Devedor ha pelo menos 1 ano, conferir se o cadastro
(status + turma de controle) esta consistente com a realidade financeira, e
sinalizar/corrigir com revisao humana.

Regra de confirmacao (revista pelo usuario em 2026-09-04 - substitui a
decisao anterior de gravar comentario automaticamente): NADA e gravado sem
confirmacao explicita do usuario, nem o comentario de analise/resumo, nem
marcacao como Urgente, nem mudanca de status/turma. Alem disso, antes de
gravar um comentario de analise, o sistema verifica se ja existe uma
analise anterior pra aquele aluno e avisa o usuario ANTES de gravar, mesmo
que ele ja tenha dado a confirmacao inicial - evita duplicar analise sem o
usuario perceber.
"""
import csv
import re
from datetime import datetime

from fuctura_client import TURMA_ADVOGADO_ID, TURMA_DEVEDOR_ID

# comentarios reais do Fuctura as vezes trazem o vencimento de cada parcela
# gerada em texto livre, tipo "Vencimento em 20/01/2025 R$ 377,00" - usado
# como fallback pra elegibilidade de "1 ano" enquanto o CORA nao esta pronto
# (ver RASCUNHO_reconciliacao_devedor.md, secao 5).
VENCIMENTO_RE = re.compile(r"vencimento\s+em\s+(\d{2}/\d{2}/\d{4})", re.IGNORECASE)

# titulo do comentario de geracao de boleto varia bastante na pratica -
# "GERAR BOLETOS", "BOLETOS GERADOS", "BOLETOS GERADOS E ENVIADOS" etc.
# Um match literal so de "gerar boletos" (ordem fixa) perdia variantes reais
# (achado no caso NORMANDO NOBLAT: titulo "BOLETOS GERADOS E ENVIADOS" nao
# batia, o fallback caiu pro comentario de matricula mais antigo - que era
# uma "RESERVA META" de mais de um ano antes da matricula paga real -
# inflando a elegibilidade de forma incorreta). Agora basta "boleto" e
# alguma forma do verbo "gerar" aparecerem juntos no titulo, em qualquer ordem.
GERAR_BOLETOS_RE = re.compile(r"boleto", re.IGNORECASE)
GERAR_VERBO_RE = re.compile(r"gera", re.IGNORECASE)

CONVENIO_RE = re.compile(r"conv[eê]nio|\bbolsa\b", re.IGNORECASE)

# Achado real (investigando o Caso 1 do Vitor Fonseca Veloso, 2026-09-05):
# um comentario antigo tipo "Convite Bolsa" / "O aluno não está
# interessado" disparava CONVENIO_RE (bate em "bolsa") mesmo a bolsa tendo
# sido RECUSADA - eh_convenio() classificava erroneamente convenio=True.
# So nao mudou a decisao final do Vitor por coincidencia (ele tinha divida
# real, entao o ramo de convenio nem era consultado) - mas um aluno sem
# divida real (valor_final=0) com esse mesmo padrao de comentario acabaria
# classificado como "convenio_ok" (nenhuma acao) quando na verdade deveria
# cair em "devedor_sem_divida"/"cancelou_e_quitou". Filtra negacao/recusa
# antes de aceitar como sinal real, mesmo padrao ja usado pra cancelamento.
_CONVENIO_NEGACAO_RE = re.compile(
    r"n[ãa]o (est[áa] interessad[oa]|quer|aceitou|tem interesse)|recusou|recusad[oa]|negad[oa]|indeferid[oa]",
    re.IGNORECASE,
)


def houve_sinal_convenio(texto):
    if not CONVENIO_RE.search(texto):
        return False
    return not _CONVENIO_NEGACAO_RE.search(texto)


def listar_devedores_para_analise(client, ordem="mais_antigo_primeiro"):
    """Todo aluno com Situacao=Devedor no sistema (independente de estar ou
    nao numa turma de controle), com um flag de se ja esta em Devedor/
    Pendencia ou Aguardando Advogado.

    ordem='mais_antigo_primeiro' (default): devedor mais antigo primeiro -
    importante quando a analise roda com --limite, ja que
    listar_devedores_ao_vivo() devolve o mais recente primeiro por padrao,
    e os casos que a reconciliacao realmente pega (>=1 ano de divida) so
    aparecem entre os mais antigos - rodar um limite pequeno sem essa
    inversao so mostra devedor recente demais pra ser elegivel."""
    devedores = client.listar_devedores_ao_vivo()
    ids_controle = client.ids_turmas_controle()

    ids_devedor_turma = {a["id_aluno"] for a in client.roster_turma(TURMA_DEVEDOR_ID)}
    ids_advogado_turma = {a["id_aluno"] for a in client.roster_turma(TURMA_ADVOGADO_ID)}

    resultado = []
    for d in devedores:
        id_aluno = d["id_aluno"]
        resultado.append({
            "id_aluno": id_aluno,
            "nome": d["nome"],
            "celular": d.get("celular", ""),
            "data_registro": _parse_data_comentario(d.get("data_mais_recente")),
            "esta_em_controle": id_aluno in ids_controle,
            "em_turma_devedor": id_aluno in ids_devedor_turma,
            "em_turma_advogado": id_aluno in ids_advogado_turma,
        })

    if ordem == "mais_antigo_primeiro":
        resultado.sort(key=lambda d: d["data_registro"] or datetime.max)
    return resultado


def _parse_data_comentario(data_str):
    try:
        return datetime.strptime((data_str or "").strip(), "%d/%m/%Y")
    except ValueError:
        return None


def vencimento_mais_antigo_em_aberto(comentarios):
    """Fallback (sem CORA ainda) pra estimar desde quando a divida esta em
    aberto, em ordem de precisao decrescente (decidido com o usuario):

    1. Mencao explicita 'Vencimento em DD/MM/AAAA' no texto livre - so
       aparece em registros mais antigos que listavam cada parcela.
    2. Data do comentario 'GERAR BOLETOS' - registros mais recentes so
       descrevem o plano ('13x de R$ 377 todo dia 25'), sem vencimento por
       parcela, mas a data desse comentario e quando o plano foi criado -
       usada como proxy de quando a cobranca comecou.
    3. Data do comentario de matricula MAIS RECENTE (tipo/titulo contendo
       'matricula') - ultimo fallback, proxy mais fraca de "desde quando
       pode estar devendo", usada so se nem 1 nem 2 existirem.

       Usa o MAIS RECENTE, nao o mais antigo (achado real, caso VITOR
       FONSECA VELOSO, 2026-09-05): aluno com uma tentativa de bolsa em
       2008 que nunca virou contrato pago ("aluno nao esta interessado"),
       reativado com matricula paga de verdade so em 2026 - pegar o
       matricula mais antigo (2008) inflava a divida real (R$350, de 4
       meses) pra parecer 18 anos em aberto. O risco inverso (um aluno com
       divida antiga de verdade escondida atras de uma matricula nova
       recente) existe, mas e mais raro na pratica e o resultado e so
       "nao_elegivel por enquanto" (falso negativo, sem acao tomada) - bem
       mais seguro que inflar a antiguidade e mandar pro juridico algo
       recente demais (falso positivo, com acao real sugerida).

    Retorna (data, fonte) - fonte fica registrada no comentario de analise
    pra deixar rastreavel qual nivel de confianca foi usado."""
    datas_vencimento = []
    datas_gerar_boletos = []
    datas_matricula = []
    for c in comentarios:
        texto_full = c["titulo"] + " " + c["texto"]
        for m in VENCIMENTO_RE.finditer(texto_full):
            d = _parse_data_comentario(m.group(1))
            if d:
                datas_vencimento.append(d)
        if GERAR_BOLETOS_RE.search(c["titulo"]) and GERAR_VERBO_RE.search(c["titulo"]):
            d = _parse_data_comentario(c["data"])
            if d:
                datas_gerar_boletos.append(d)
        if "matricula" in c["titulo"].lower() or "matricula" in c["tipo"].lower():
            d = _parse_data_comentario(c["data"])
            if d:
                datas_matricula.append(d)

    if datas_vencimento:
        return min(datas_vencimento), "comentario_vencimento"
    if datas_gerar_boletos:
        return min(datas_gerar_boletos), "gerar_boletos"
    if datas_matricula:
        return max(datas_matricula), "matricula"
    return None, "sem_dado"


PRAZO_PADRAO_DIAS = 365  # ~1 ano, regra combinada com o usuario

# Pedido do usuario (2026-09-09): divida tem prazo MAXIMO de busca tambem,
# nao so minimo - a divida prescreve em 5 anos (Codigo Civil, art. 206 §5º
# I - divida liquida constante de instrumento particular), entao qualquer
# vencimento mais antigo que isso ja esta fora do prazo legal de cobranca:
# continuar sugerindo cobranca/matricula em turma de controle pra esse caso
# nao faz sentido, e so iria acumular trabalho em cima de algo que nao pode
# mais ser cobrado. 1825 = 365*5 (ajustavel pelo usuario na tela, mesmo
# padrao do PRAZO_PADRAO_DIAS/dias_minimos).
PRAZO_MAXIMO_DIAS = 1825  # 5 anos, prescricao

# Pedido do Diogenes via Caio (2026-09-14): divida real, mas pequena
# demais, nao compensa o custo de cobranca - abaixo desse valor o sistema
# nao sugere matricular em turma de controle nem enviar pro advogado (so
# sinaliza pra conferencia humana). Regra dele foi um valor fixo, nao um
# percentual - diferente de dias_minimos/dias_maximos, nao e algo que a
# tela deixa o usuario ajustar por enquanto (nao foi pedido).
PISO_VALOR_COBRANCA = 377.0


def elegivel_por_prazo(vencimento_mais_antigo, data_execucao=None, dias_minimos=PRAZO_PADRAO_DIAS):
    """Aluno e elegivel se o vencimento em aberto mais antigo for de pelo
    menos `dias_minimos` antes da data de execucao (regra combinada com o
    usuario: padrao 365 dias / ~1 ano, mas ajustavel - dar ao usuario a
    opcao de mudar esse prazo na tela em vez de fixo no codigo)."""
    if vencimento_mais_antigo is None:
        return False
    data_execucao = data_execucao or datetime.now()
    from datetime import timedelta
    limite = data_execucao - timedelta(days=dias_minimos)
    return vencimento_mais_antigo <= limite


def divida_prescrita(vencimento_mais_antigo, data_execucao=None, dias_maximos=PRAZO_MAXIMO_DIAS):
    """True se o vencimento em aberto mais antigo ja passou do prazo legal
    de cobranca (ver PRAZO_MAXIMO_DIAS) - a divida provavelmente prescreveu,
    continuar sugerindo acao de cobranca nao faz sentido."""
    if vencimento_mais_antigo is None:
        return False
    data_execucao = data_execucao or datetime.now()
    from datetime import timedelta
    limite = data_execucao - timedelta(days=dias_maximos)
    return vencimento_mais_antigo < limite


def eh_convenio(perfil, comentarios):
    """Convenio pode nao estar sempre marcado no status (26=Convenio) -
    fallback: procura mencao de convenio/bolsa nos comentarios antes de
    tratar valor=0 como inconsistencia (definido pelo usuario)."""
    if perfil.get("status", "").strip().lower() == "convenio":
        return True
    for c in comentarios:
        if houve_sinal_convenio(c["titulo"] + " " + c["texto"]):
            return True
    return False


def houve_cancelamento(comentarios):
    """Reaproveita a mesma deteccao ja usada no fechamento de turma pra achar
    mencao de cancelamento em texto livre (fechamento_logic.houve_sinal_cancelamento -
    filtra negacao e cancelamento de algo financeiro, nao so o regex cru)."""
    import fechamento_logic as logic
    return any(
        logic.houve_sinal_cancelamento(c["titulo"] + " " + c["texto"])
        for c in comentarios
    )


def ja_com_advogado(nome, turmas_atuais, comentarios):
    """Sinal de que o aluno ja esta (ou ja foi encaminhado) pro escritorio de
    advocacia externo, mesmo que roster_turma() NAO o liste no momento como
    membro atual da turma Aguardando Advogado - aprendido no projeto irmao
    fuctura-propensao-pagamento (fechamento_logic.JA_COM_ADVOGADO_RE, casos
    reais tipo ANDRE/PETHRUS: o aluno ja esta com o juridico mas sumiu do
    roster por algum motivo de cadastro). Evita sugerir matricular em
    Devedor/Pendencia um aluno que na pratica ja esta no fluxo juridico -
    pedido explicito do usuario.

    BUG REAL corrigido em 2026-09-05 (achado investigando o bucket
    'advogado_sinalizado_sem_turma_atual', 76 dos 77 casos): o proprio
    texto do comentario automatico de analise EXPLICA o caso usando a
    palavra "advogado" (ex: "...menciona advogado/escritorio... turma
    Aguardando Advogado..."), entao depois que esse comentario e gravado
    uma vez, toda analise seguinte lia o proprio comentario anterior como
    se fosse um sinal humano de advogado - um loop que se autoconfirma pra
    sempre, mascarando o caso real por baixo (visto ao vivo: um aluno que
    tinha virado corretamente "cancelou_mas_ainda_deve" com sugestao real
    de cobranca, na proxima rodada leu o proprio comentario e virou
    "advogado_sinalizado" sem nenhuma acao). Por isso ignora os proprios
    comentarios automaticos (mesmo conjunto de titulos usado na auditoria,
    ver _TITULOS_IGNORADOS_NA_AUDITORIA) antes de rodar o regex."""
    import fechamento_logic as logic
    comentarios = [c for c in comentarios if not _e_comentario_automatico(c["titulo"])]
    if logic.JA_COM_ADVOGADO_RE.search(nome or ""):
        return True
    for t in (turmas_atuais or []):
        if logic.JA_COM_ADVOGADO_RE.search(t.get("nome", "")):
            return True
    for c in comentarios:
        if logic.JA_COM_ADVOGADO_RE.search(c["titulo"] + " " + c["texto"]):
            return True
    return False


def houve_sinal_estorno_pendente(comentarios):
    """Sinal de que, apesar do cancelamento, quem tem valor a receber e a
    FAMILIA (estorno do que ja foi pago), nao a Fuctura - ver ESTORNO_RE.
    So faz sentido checar dentro do ramo "cancelou"; retorna (True, texto do
    comentario mais recente com o sinal) ou (False, None)."""
    hits = [c for c in comentarios if ESTORNO_RE.search(c["titulo"] + " " + c["texto"])]
    if not hits:
        return False, None
    ultimo = hits[-1]
    return True, f"{ultimo['titulo']} ({ultimo['data']})"


# Pedido do usuario (2026-09-16): titulo de comentario gravado pelo
# sistema sempre em MAIUSCULO - convencao real da equipe (confirmada
# olhando o historico de verdade: "FECHAMENTO...", "GERAR BOLETOS",
# "ACOMPANHAMENTO" etc., tudo em caixa alta). Comentarios ANTIGOS (antes
# desta mudanca) podem ter vindo em outra caixa - por isso toda
# comparacao contra este texto usa .upper() dos dois lados (ver
# _e_titulo_analise), nunca "==" direto, pra nao parar de reconhecer
# analise ja gravada com o titulo antigo.
ASSUNTO_ANALISE_AUTOMATICA = "RECONCILIAÇÃO DEVEDOR/TURMA DE CONTROLE"

# Pedido do usuario (2026-09-09): quando o funcionario edita o texto da
# analise sugerida antes de gravar (em vez de aceitar como veio), o assunto
# grava com esse prefixo em vez de PREFIXO_TEXTO_ORIGINAL - fica visivel no
# proprio Fuctura, sem precisar abrir o comentario pra saber se o texto e
# exatamente a sugestao ou se um humano mexeu nele antes de confirmar.
# Mesma convencao (duplicada de proposito) em fechamento_logic.py.
PREFIXO_TEXTO_ORIGINAL = "-IA- "
PREFIXO_TEXTO_EDITADO = "-MOD- "


def remover_prefixo_edicao(titulo):
    """Tira o prefixo -IA-/-MOD- (ver PREFIXO_TEXTO_ORIGINAL/EDITADO) antes
    de qualquer comparacao de titulo - esse marcador e sobre a FIDELIDADE do
    texto gravado, nao deve interferir em nada que reconhece o titulo de um
    comentario de analise (_e_titulo_analise e quem usa ela)."""
    titulo = titulo or ""
    for p in (PREFIXO_TEXTO_ORIGINAL, PREFIXO_TEXTO_EDITADO):
        if titulo.startswith(p):
            return titulo[len(p):]
    return titulo

# Formatacao dos comentarios que este modulo grava no Fuctura (pedido do
# usuario, 2026-09-08): o Fuctura renderiza <br> como quebra de linha de
# verdade na tela dele - 3x <br> ("bloco a bloco") separa visualmente as
# partes importantes de um comentario longo (ex: dados da analise x resumo
# do historico x info da matricula).
SEPARADOR_BLOCO = "<br><br><br>"


def data_ultima_analise_automatica(comentarios):
    """Data (string DD/MM/AAAA) do comentario de analise automatica MAIS
    RECENTE ja existente pra este aluno, se houver - None se nunca foi
    analisado antes. Usado pra avisar o usuario ANTES de gravar uma nova
    analise, mesmo que ele ja tenha confirmado a intencao inicialmente -
    evita duplicar analise sem o usuario perceber (pedido explicito)."""
    # de proposito NAO usa _e_comentario_automatico aqui: aquela tambem
    # reconhece os marcadores de neutralizacao tipo "--Removido Por Flood--",
    # que representam uma analise ANULADA, nao uma que ainda vale como "ja
    # analisado antes".
    anteriores = [c for c in comentarios if _e_titulo_analise(c["titulo"])]
    return anteriores[-1]["data"] if anteriores else None


def _e_titulo_analise(titulo):
    """True se o titulo e o comentario de analise automatica (exato) ou a
    MESMA analise depois de aplicar_acao() mesclar nela a matricula (titulo
    ganha o sufixo ' -- IA --' - ver aplicar_acao). Ignora o prefixo
    -IA-/-MOD- (ver remover_prefixo_edicao) antes de comparar. Compartilhado
    por data_ultima_analise_automatica (achar a ultima analise) e por
    aplicar_acao (achar o comentario pra mesclar a matricula)."""
    titulo = remover_prefixo_edicao(titulo).upper()
    return titulo == ASSUNTO_ANALISE_AUTOMATICA or titulo.startswith(ASSUNTO_ANALISE_AUTOMATICA + " ")


def resumir_comentarios(comentarios, max_chars=220):
    """Resumo curto e deterministico do historico de comentarios (pedido
    explicito do usuario) - nao e um resumo por IA, so extrai fatos: quantos
    comentarios existem, o mais recente, e o pagamento mais recente com
    Tipo='-Pagamento Realizado' (sinal primario mais confiavel, ver memoria
    fuctura-devedor-filtro-confiabilidade - nao caca "pago" em texto livre).
    comentarios vem ordenado por data ASC (fuctura_client.comentarios_aluno),
    entao o ultimo da lista e o mais recente.

    Ignora os proprios comentarios automaticos - de analise
    (ASSUNTO_ANALISE_AUTOMATICA) e os ja neutralizados (teste, flood) - ao
    procurar o "ultimo comentario" - senao, numa segunda rodada, o resumo
    fica recursivo (cita a si mesmo) ou cita um marcador de limpeza em vez
    do ultimo comentario humano real. Mesmo conjunto de titulos usado em
    detectar_inconsistencias/ja_com_advogado (ver
    _TITULOS_IGNORADOS_NA_AUDITORIA), unificado aqui pra nao voltar a
    divergir (achado real: esta funcao tinha o proprio filtro em separado,
    que nao pegava o marcador novo do flood)."""
    import fechamento_logic as logic
    comentarios_humanos = [c for c in comentarios if not _e_comentario_automatico(c["titulo"])]
    if not comentarios_humanos:
        return "Nenhum comentário (humano) no histórico deste aluno."

    ultimo = comentarios_humanos[-1]
    texto_ultimo = (ultimo["texto"] or ultimo["titulo"] or "").strip()
    if len(texto_ultimo) > max_chars:
        texto_ultimo = texto_ultimo[:max_chars].rstrip() + "..."

    partes = [f"{len(comentarios)} comentário(s) no histórico."]
    if texto_ultimo and texto_ultimo != ultimo["titulo"]:
        partes.append(f"Último em {ultimo['data']} (\"{ultimo['titulo']}\"): {texto_ultimo}")
    else:
        partes.append(f"Último em {ultimo['data']}: \"{ultimo['titulo']}\".")

    pagamentos = [c for c in comentarios if "pagamento realizado" in logic.norm(c["tipo"])]
    if pagamentos:
        ult_pag = pagamentos[-1]
        partes.append(f"Último pagamento registrado (Tipo -Pagamento Realizado) em {ult_pag['data']}.")
    else:
        partes.append("Nenhum comentário com Tipo '-Pagamento Realizado' encontrado.")

    return " ".join(partes)


def data_ultimo_pagamento(comentarios):
    """Data (string DD/MM/AAAA) do comentário '-Pagamento Realizado' mais
    recente, ou None se nunca houve nenhum - mesmo filtro usado dentro de
    resumir_comentarios(), fatorado aqui pra reaproveitar em
    relatorios_pdf.py sem precisar reprocessar o texto do resumo."""
    import fechamento_logic as logic
    pagamentos = [c for c in comentarios if "pagamento realizado" in logic.norm(c["tipo"])]
    return pagamentos[-1]["data"] if pagamentos else None


def data_ultimo_contato(comentarios):
    """Data (string DD/MM/AAAA) da 'Ocorrência de Contato' mais recente
    (ver _registrar_contato/registrarContato em app.py - grava tipo '3'
    com esse título exato), ou None se nenhum contato foi registrado
    ainda."""
    contatos = [c for c in comentarios if c["titulo"].strip().upper() == "OCORRÊNCIA DE CONTATO"]
    return contatos[-1]["data"] if contatos else None


def dias_em_aberto(vencimento_mais_antigo, data_execucao=None):
    """Dias corridos entre o vencimento em aberto mais antigo e a data de
    execução - mesma conta usada internamente por elegivel_por_prazo()/
    divida_prescrita(), exposta aqui pra exibição em relatório (não pra
    decisão). None se não há vencimento conhecido (fonte_vencimento ==
    'sem_dado')."""
    if vencimento_mais_antigo is None:
        return None
    data_execucao = data_execucao or datetime.now()
    return (data_execucao - vencimento_mais_antigo).days


_FONTE_LABEL = {
    "comentario_vencimento": "vencimento mencionado em comentário",
    "gerar_boletos": "data do comentário de geração de boletos",
    "matricula": "data do comentário de matrícula",
    "sem_dado": "não foi possível determinar",
}


def analisar_aluno(client, aluno_bruto, data_execucao=None, dias_minimos=PRAZO_PADRAO_DIAS,
                    dias_maximos=PRAZO_MAXIMO_DIAS, perfil=None, comentarios=None, matricula=""):
    """Roda a arvore de decisao completa (RASCUNHO_reconciliacao_devedor.md,
    secao 4) pra 1 aluno. So LEITURA - nao grava nem altera nada. Retorna a
    analise completa, pronta pra virar comentario e pra decidir a acao
    sugerida (que so e aplicada com confirmacao humana explicita).

    dias_minimos: prazo minimo de divida em aberto pra ser elegivel
    (padrao 365 dias / ~1 ano, ajustavel pelo usuario).
    dias_maximos: prazo maximo - alem disso a divida provavelmente
    prescreveu (padrao 1825 dias / 5 anos, ver PRAZO_MAXIMO_DIAS),
    ajustavel pelo usuario.
    perfil/comentarios: passe pre-buscados pra evitar refazer as mesmas 2
    chamadas de rede quando o chamador (ex: auditoria) ja precisa deles
    por outro motivo - se None, busca normalmente.
    matricula: pedido do usuario (2026-09-12) - a tela deve mostrar a
    matricula em vez do id_aluno interno. So exibicao, nao entra em
    NENHUMA logica de decisao - por isso e so um repasse (default "",
    NAO busca sozinha via rede, pra continuar pura/testavel sem client);
    quem chama de verdade (rodar_reconciliacao) busca e passa."""
    id_aluno = aluno_bruto["id_aluno"]
    if perfil is None:
        perfil = client.perfil_aluno(id_aluno)
    if comentarios is None:
        comentarios = client.comentarios_aluno(id_aluno)
    data_analise_anterior = data_ultima_analise_automatica(comentarios)

    vencimento, fonte_vencimento = vencimento_mais_antigo_em_aberto(comentarios)
    elegivel = elegivel_por_prazo(vencimento, data_execucao, dias_minimos)
    prescrita = divida_prescrita(vencimento, data_execucao, dias_maximos)
    valor_final = perfil["diferenca"]
    deve_de_fato = valor_final > 0
    abaixo_do_piso_cobranca = deve_de_fato and valor_final < PISO_VALOR_COBRANCA
    convenio = eh_convenio(perfil, comentarios)
    cancelou = houve_cancelamento(comentarios)
    esta_em_controle = aluno_bruto["esta_em_controle"]
    ja_advogado = ja_com_advogado(perfil.get("nome") or aluno_bruto["nome"], perfil.get("turmas_atuais"), comentarios)
    resumo_comentarios = resumir_comentarios(comentarios)
    tem_estorno, ref_estorno = houve_sinal_estorno_pendente(comentarios)

    caso = "nao_elegivel"
    acao_sugerida = None
    urgente = False

    if not elegivel:
        caso = "nao_elegivel"
    elif deve_de_fato and prescrita:
        # divida fora do prazo legal de cobranca (ver PRAZO_MAXIMO_DIAS) -
        # tem prioridade sobre QUALQUER outro caso (mesmo ja estar em
        # Devedor/Pendencia ou Aguardando Advogado): nao faz sentido
        # continuar sugerindo cobranca pra algo que provavelmente ja
        # prescreveu. Nao sugere nenhuma acao automatica - decisao de
        # baixar/cancelar essa divida e institucional, nao tecnica.
        caso = "divida_prescrita"
    elif convenio and not deve_de_fato:
        caso = "convenio_ok"
    elif not deve_de_fato:
        if cancelou:
            caso = "cancelou_e_quitou"
            acao_sugerida = {"tipo": "mudar_status", "novo_status": "5", "novo_status_nome": "Cancelado"}
        else:
            caso = "devedor_sem_divida"
            acao_sugerida = {"tipo": "mudar_status", "novo_status": "4", "novo_status_nome": "-Matriculado"}
        urgente = True
    elif esta_em_controle:
        caso = "consistente"
    elif ja_advogado:
        # ja esta (ou ja foi) encaminhado pro juridico externo por outros
        # sinais (nome/turma/comentario), mesmo fora do roster ATUAL de
        # Aguardando Advogado - nao faz sentido colocar em Devedor/Pendencia
        # nesse caso (pedido explicito do usuario). Sinaliza pra conferencia
        # do cadastro, mas nao sugere nenhuma acao automatica.
        caso = "advogado_sinalizado_sem_turma_atual"
        urgente = True
    elif cancelou and tem_estorno:
        # caso ANA LUISA SILVA RAMOS DE LIMA: cancelou antes de assistir
        # qualquer aula e o comentario pede estorno pra familia - o "valor
        # final" nao e divida real, entao NAO sugere cobranca (pedido
        # explicito do usuario apos essa investigacao, 2026-09-05).
        caso = "possivel_estorno_pendente"
        urgente = True
    elif abaixo_do_piso_cobranca:
        # pedido do Diogenes via Caio (2026-09-14): divida real mas menor
        # que PISO_VALOR_COBRANCA nao compensa cobranca formal - nao
        # sugere nem turma de controle nem advogado, so sinaliza.
        caso = "abaixo_do_piso_cobranca"
    elif cancelou:
        caso = "cancelou_mas_ainda_deve"
        acao_sugerida = {"tipo": "matricular_turma", "turma_id": TURMA_DEVEDOR_ID, "turma_nome": "Devedor/Pendência"}
        urgente = True
    else:
        caso = "devedor_sem_turma"
        acao_sugerida = {"tipo": "matricular_turma", "turma_id": TURMA_DEVEDOR_ID, "turma_nome": "Devedor/Pendência"}
        urgente = True

    return {
        "id_aluno": id_aluno, "matricula": matricula, "nome": perfil.get("nome") or aluno_bruto["nome"],
        "valor_final": valor_final, "vencimento_mais_antigo": vencimento,
        "fonte_vencimento": fonte_vencimento, "elegivel": elegivel,
        "convenio": convenio, "cancelou": cancelou, "esta_em_controle": esta_em_controle,
        "caso": caso, "acao_sugerida": acao_sugerida, "urgente": urgente,
        "dias_minimos": dias_minimos, "dias_maximos": dias_maximos, "prescrita": prescrita,
        "ja_com_advogado": ja_advogado,
        "resumo_comentarios": resumo_comentarios,
        "data_analise_anterior": data_analise_anterior,
        "possivel_estorno_pendente": tem_estorno, "referencia_estorno": ref_estorno,
        "ultimo_pagamento": data_ultimo_pagamento(comentarios),
        "ultimo_contato": data_ultimo_contato(comentarios),
    }


# ---------------------------------------------------------------------------
# Auditoria: reler os comentarios de cada caso com padroes MAIS AMPLOS que
# os usados na analise, pra achar falsos negativos (coisa que o comentario
# diz mas nossa deteccao nao pegou) - achado real que motivou isto: o caso
# NORMANDO NOBLAT, onde o titulo de um comentario de boleto fugia do padrao
# esperado e a fonte de vencimento usada foi uma matricula antiga e errada.
# So LEITURA - nunca corrige nada sozinho, so relata pra revisao humana.
# ---------------------------------------------------------------------------
ADVOGADO_AMPLO_RE = re.compile(
    r"advogad|jur[ií]dico|escrit[óo]rio|a[çc][ãa]o judicial|processo judicial|cobran[çc]a externa",
    re.IGNORECASE,
)
CANCELAMENTO_AMPLO_RE = re.compile(
    r"cancel|desistiu|n[ãa]o quer mais|n[ãa]o vai continuar|parou de vir|sumiu|abandonou|n[ãa]o vai voltar",
    re.IGNORECASE,
)
BOLETO_PAGAMENTO_RE = re.compile(r"boleto|pagamento|\bpix\b|cart[ãa]o", re.IGNORECASE)

# Achado real (caso ANA LUISA SILVA RAMOS DE LIMA, 04/09/2026): cancelamento
# em que a FUCTURA e quem deve dinheiro de volta pra familia (aluna nunca
# assistiu aula, pai cancelou, comentario pede estorno) - nesse caso o
# "valor final" (contratado - recebido) nao e uma divida real, e sugerir
# matricular em Devedor/Pendencia seria cobrar quem deveria estar recebendo
# de volta. So dispara dentro do ramo "cancelou" (estorno so faz sentido
# depois de um cancelamento) - qualquer mencao aqui ja e motivo suficiente
# pra tirar a sugestao automatica de cobranca e pedir revisao humana, entao
# usa um regex direto (nao o padrao estreito-na-decisao/amplo-na-auditoria
# usado pra cancelamento, onde falso positivo tem risco maior).
ESTORNO_RE = re.compile(
    r"estorn|devolver\s+o\s+(valor|dinheiro)|devolu[çc][ãa]o\s+do\s+(valor|dinheiro)|reembols",
    re.IGNORECASE,
)

GAP_FONTE_FRACA_DIAS = 180  # limiar pra achar "tem sinal mais recente que a fonte usada"


# Assunto usado pelo script avulso de limpeza do flood
# (limpeza_flood_04_09.py) - fica aqui, do lado da lista que filtra
# comentarios automaticos, pra nao desincronizar (quem grava e quem
# filtra precisam concordar no texto exato). Ver ASSUNTO_RESUMO_DEBITO_FLOOD
# abaixo.
ASSUNTO_RESUMO_DEBITO_FLOOD = "Resumo do Débito"

_TITULOS_IGNORADOS_NA_AUDITORIA = {
    ASSUNTO_ANALISE_AUTOMATICA, "Comentário --Teste com IA-- removido",
    # marcador usado pra neutralizar os comentarios duplicados do flood de
    # 04/09/2026 (ver memoria fuctura_mascara_confirmacao_humana_obrigatoria) -
    # precisa ficar de fora de qualquer deteccao (ja_com_advogado,
    # resumir_comentarios, etc.), senao um comentario ja neutralizado podia
    # ainda contar como "ultimo comentario" ou sinal de alguma coisa.
    "--Removido Por Flood--",
    # achado real 2026-09-11: sem isto, um "Resumo do Débito" ja gravado
    # (pelo script de limpeza do flood) virava o "ultimo comentario humano"
    # da proxima vez que resumir_comentarios rodasse pro mesmo aluno -
    # citando a si mesmo, o mesmo bug que esta lista inteira existe pra
    # evitar.
    ASSUNTO_RESUMO_DEBITO_FLOOD,
}
# normalizado em MAIUSCULO uma vez so, pra comparacao case-insensitive em
# _e_comentario_automatico (titulos antigos podem nao estar em caixa alta
# - ver nota em ASSUNTO_ANALISE_AUTOMATICA).
_TITULOS_IGNORADOS_NA_AUDITORIA_UPPER = {t.upper() for t in _TITULOS_IGNORADOS_NA_AUDITORIA}


def _e_comentario_automatico(titulo):
    """True se o titulo pertence a um comentario gerado pelo proprio sistema -
    analise automatica (ASSUNTO_ANALISE_AUTOMATICA), a MESMA analise depois
    de aplicar_acao() mesclar nela a matricula (titulo vira
    'ASSUNTO_ANALISE_AUTOMATICA -- IA --', ver aplicar_acao), ou um marcador
    de neutralizacao/revisao ja conhecido (teste, flood, resumo de debito
    do flood) - com ou sem o prefixo -IA-/-MOD- (ver remover_prefixo_edicao).

    Usar esta funcao em vez de comparar '== ASSUNTO_ANALISE_AUTOMATICA' ou
    'in _TITULOS_IGNORADOS_NA_AUDITORIA' direto: pedido do usuario
    (2026-09-08) de acrescentar ' -- IA --' ao titulo quando a analise vira
    matricula faria esses comentarios pararem de bater na comparacao exata -
    reabrindo o MESMO bug de autocontaminacao do "advogado" ja corrigido
    antes (ver ja_com_advogado): o texto do proprio comentario automatico
    menciona "advogado" pra alguns casos, e sem esse reconhecimento ele
    passaria a contar como um sinal humano de novo."""
    return _e_titulo_analise(titulo) or remover_prefixo_edicao(titulo).upper() in _TITULOS_IGNORADOS_NA_AUDITORIA_UPPER


def detectar_inconsistencias(analise, comentarios):
    """Compara a analise (feita com padroes estreitos, para nao dar falso
    positivo na hora de agir) contra os comentarios usando padroes MAIS
    AMPLOS - qualquer diferenca vira um achado de auditoria pra revisao
    humana, sem mudar a decisao/acao sugerida automaticamente.

    Ignora os proprios comentarios automaticos (de analise ou de teste ja
    neutralizados) - senao o resumo do proprio comentario de analise (que
    cita 'pagamento', 'vencimento' etc.) vira um falso positivo recursivo,
    mesmo bug ja corrigido antes em resumir_comentarios()."""
    import fechamento_logic as logic
    comentarios = [c for c in comentarios if not _e_comentario_automatico(c["titulo"])]
    achados = []

    if analise["fonte_vencimento"] == "matricula" and analise["vencimento_mais_antigo"]:
        candidatos = [c for c in comentarios if BOLETO_PAGAMENTO_RE.search(c["titulo"] + " " + c["texto"])]
        datas_candidatas = [d for d in (_parse_data_comentario(c["data"]) for c in candidatos) if d]
        if datas_candidatas:
            mais_recente = max(datas_candidatas)
            if (mais_recente - analise["vencimento_mais_antigo"]).days > GAP_FONTE_FRACA_DIAS:
                achados.append(
                    f"Fonte de vencimento é a mais fraca (comentário de matrícula, "
                    f"{analise['vencimento_mais_antigo'].strftime('%d/%m/%Y')}), mas há comentário sobre "
                    f"boleto/pagamento bem mais recente ({mais_recente.strftime('%d/%m/%Y')}) - "
                    f"conferir se a matrícula usada como referência é mesmo a certa (caso Normando: era um "
                    f"registro de reserva/lead antigo, não a matrícula paga real)."
                )

    if analise["caso"] == "cancelou_mas_ainda_deve" and not analise.get("possivel_estorno_pendente"):
        # defesa em profundidade: mesmo que analisar_aluno ja tenha essa
        # checagem embutida (ver houve_sinal_estorno_pendente), reconfere
        # aqui com o mesmo regex - se um dia a logica principal mudar e
        # parar de checar isso, a auditoria ainda pega o caso Ana Luisa.
        hits = [c for c in comentarios if ESTORNO_RE.search(c["titulo"] + " " + c["texto"])]
        if hits:
            achados.append(
                f"Sugeriu matricular em Devedor/Pendência, mas há menção de estorno/devolução de valor "
                f"nos comentários (mais recente: \"{hits[-1]['titulo']}\", {hits[-1]['data']}) - pode ser "
                f"a Fuctura quem deve dinheiro pra família, não o contrário (caso Ana Luisa). Conferir "
                f"antes de cobrar."
            )

    if not analise["ja_com_advogado"]:
        hits = [c for c in comentarios if ADVOGADO_AMPLO_RE.search(c["titulo"] + " " + c["texto"])]
        if hits:
            achados.append(
                f"Possível menção de advogado/jurídico não capturada pela detecção atual "
                f"(mais recente: \"{hits[-1]['titulo']}\", {hits[-1]['data']}) - conferir manualmente."
            )

    if not analise["convenio"]:
        hits = [c for c in comentarios if CONVENIO_RE.search(c["titulo"] + " " + c["texto"])]
        if hits:
            achados.append(
                f"Possível menção de convênio/bolsa não capturada pela detecção atual "
                f"(mais recente: \"{hits[-1]['titulo']}\", {hits[-1]['data']}) - conferir manualmente."
            )

    if not analise["cancelou"]:
        hits = [c for c in comentarios if CANCELAMENTO_AMPLO_RE.search(c["titulo"] + " " + c["texto"])]
        # o regex amplo de cancelamento e mais permissivo que
        # fechamento_logic.houve_sinal_cancelamento (ja usado na analise) -
        # so reporta se achar algo que a deteccao ja usada nao pegaria
        # tambem, senao vira ruido repetindo o que a analise ja sabe.
        hits_novos = [c for c in hits if not logic.houve_sinal_cancelamento(c["titulo"] + " " + c["texto"])]
        if hits_novos:
            achados.append(
                f"Possível menção de cancelamento/desistência (fraseado diferente do padrão já detectado) "
                f"não capturada (mais recente: \"{hits_novos[-1]['titulo']}\", {hits_novos[-1]['data']}) - "
                f"conferir manualmente."
            )

    return achados


_TEXTO_CASO = {
    "divida_prescrita": (
        "⚠ Vencimento em aberto mais antigo já passou do prazo legal de cobrança (mais de "
        "{dias_maximos} dias) - a dívida provavelmente prescreveu. Nenhuma ação de cobrança "
        "sugerida automaticamente. Decisão de baixar/cancelar essa dívida é institucional, "
        "não técnica - conferir com o responsável antes de qualquer ação."
    ),
    "convenio_ok": "Aluno em convênio/bolsa com valor final zerado - situação normal, nenhuma ação necessária.",
    "devedor_sem_divida": (
        "⚠ INCONSISTÊNCIA: aluno marcado como Devedor mas sem dívida em aberto (valor final = 0) "
        "e sem indicação de convênio/bolsa. Sugestão: mover Situação para -Matriculado. "
        "Aguardando confirmação manual - nenhuma alteração foi feita."
    ),
    "cancelou_e_quitou": (
        "⚠ Há menção de cancelamento nos comentários e a dívida já está quitada (valor final = 0). "
        "Sugestão: mover Situação de Devedor para Cancelado. "
        "Aguardando confirmação manual - nenhuma alteração foi feita."
    ),
    "consistente": "Situação consistente: aluno deve de fato e já está na turma de controle correta. Nenhuma ação necessária.",
    "cancelou_mas_ainda_deve": (
        "⚠ Há menção de cancelamento nos comentários, mas ainda há dívida em aberto - "
        "cancelamento não quita dívida, então continua elegível pra cobrança. Aluno não está em "
        "nenhuma turma de controle. Sugestão: matricular em Devedor/Pendência. "
        "Aguardando confirmação manual - nenhuma alteração foi feita."
    ),
    "devedor_sem_turma": (
        "⚠ Devedor real (valor final > 0) sem estar em nenhuma turma de controle (Devedor/Pendência "
        "nem Aguardando Advogado). Sugestão: matricular em Devedor/Pendência. "
        "Aguardando confirmação manual - nenhuma alteração foi feita."
    ),
    "advogado_sinalizado_sem_turma_atual": (
        "⚠ Aluno tem sinal de já estar (ou já ter sido encaminhado) com o escritório de advocacia "
        "externo - nome, turma ou comentário menciona advogado/escritório - mesmo não constando na "
        "turma Aguardando Advogado no momento. Não foi sugerida matrícula em Devedor/Pendência nesse "
        "caso. Recomenda-se conferir manualmente se o cadastro na turma Aguardando Advogado precisa "
        "ser corrigido."
    ),
    "possivel_estorno_pendente": (
        "⚠ Há menção de cancelamento E de estorno/devolução de valor pago nos comentários - o "
        "\"valor final\" calculado (contratado - recebido) provavelmente NÃO é uma dívida real: é "
        "comum nesse tipo de caso o aluno ter cancelado antes de usar o serviço e ser a Fuctura quem "
        "deve devolver dinheiro pra família, não o contrário. NÃO foi sugerida matrícula em "
        "Devedor/Pendência. Recomenda-se conferir manualmente se o estorno foi feito e corrigir o "
        "cadastro (não cobrar)."
    ),
    "abaixo_do_piso_cobranca": (
        "Dívida real, mas abaixo do piso de R$ {piso} definido pelo Diógenes - não compensa o custo "
        "de cobrança formal (turma de controle ou advogado). Nenhuma ação sugerida automaticamente. "
        "Fica só sinalizado pra conferência - se o valor mudar ou for reavaliado, roda a análise de novo."
    ),
}


def montar_comentario_analise(analise):
    """Monta assunto/tipo/texto do comentario de analise. Cita sempre a
    fonte do vencimento usada (definido com o usuario), pra deixar
    rastreavel o nivel de confianca de cada caso."""
    venc_str = (
        analise["vencimento_mais_antigo"].strftime("%d/%m/%Y")
        if analise["vencimento_mais_antigo"] else "desconhecido"
    )
    fonte_label = _FONTE_LABEL[analise["fonte_vencimento"]]

    valor_fmt = f"{analise['valor_final']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if analise["caso"] == "nao_elegivel":
        texto_caso = (
            f"Devedor há menos de {analise.get('dias_minimos', PRAZO_PADRAO_DIAS)} dias "
            "(ou sem dado suficiente pra confirmar) - fora do escopo desta análise por enquanto."
        )
    else:
        texto_caso = _TEXTO_CASO[analise["caso"]]
        if analise["caso"] == "divida_prescrita":
            texto_caso = texto_caso.format(dias_maximos=analise.get("dias_maximos", PRAZO_MAXIMO_DIAS))
        if analise["caso"] == "abaixo_do_piso_cobranca":
            piso_fmt = f"{PISO_VALOR_COBRANCA:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            texto_caso = texto_caso.format(piso=piso_fmt)
        if analise["caso"] == "possivel_estorno_pendente" and analise.get("referencia_estorno"):
            texto_caso += f" Comentário que menciona estorno: \"{analise['referencia_estorno']}\"."
    # Formatacao pedida pelo usuario (2026-09-08): o Fuctura renderiza <br>
    # como quebra de linha de verdade na tela dele (nao escapa HTML no
    # comentario) - usar isso deixa o comentario legivel em vez de um
    # paragrafo unico gigante. SEPARADOR_BLOCO (3x <br>) marca a fronteira
    # entre os DADOS da analise e o RESUMO do historico - as duas partes
    # mais importantes de separar visualmente.
    bloco_dados = "<br>".join([
        "Análise automática de reconciliação Situação x Turma de Controle.",
        f"Valor final (contratado - recebido): R$ {valor_fmt}.",
        f"Vencimento mais antigo em aberto usado como referência: {venc_str} (fonte: {fonte_label}).",
        texto_caso,
    ])
    bloco_resumo = f"Resumo do histórico: {analise.get('resumo_comentarios', '')}"
    texto = bloco_dados + SEPARADOR_BLOCO + bloco_resumo
    assunto = ASSUNTO_ANALISE_AUTOMATICA
    # Pedido explicito do usuario (2026-09-08), depois do flood: nada de
    # tipo="2" (Urgente) gravado automaticamente no Fuctura por padrao -
    # foi exatamente esse mecanismo (aqui, "2" sempre que analise["urgente"]
    # era True - a maioria dos casos) que fez o flood de 04/09/2026 encher
    # o Fuctura de comentarios "Urgente". O sinalizador 'urgente' continua
    # existindo e sendo usado (ver app.py) so pra destaque visual na NOSSA
    # tela - nunca mais decide o tipo gravado no Fuctura sozinho.
    tipo = "3"
    return assunto, tipo, texto


def registrar_analise(client, analise, ignorar_analise_anterior=False, texto_editado=None):
    """Grava o comentario de analise/resumo (incluindo a marcacao como
    Urgente quando aplicavel) - SO com confirmacao explicita do usuario,
    aluno por aluno (revisto em 2026-09-04: nada e gravado sozinho, nem
    o comentario de analise). Antes de gravar, confere se JA existe uma
    analise automatica anterior pra este aluno lendo os comentarios AO VIVO
    (nao usa o snapshot da analise original, que pode estar desatualizado) -
    se existir e ignorar_analise_anterior=False, NAO grava e retorna aviso
    pro usuario decidir se quer gravar mesmo assim.

    texto_editado (pedido do usuario, 2026-09-09): se o funcionario alterou
    o texto sugerido antes de confirmar (a tela sempre manda o que estiver
    na caixa, editado ou nao), grava ESSE texto em vez do original e marca
    o assunto com PREFIXO_TEXTO_EDITADO em vez de PREFIXO_TEXTO_ORIGINAL -
    fica registrado no proprio Fuctura, sem precisar abrir o comentario pra
    saber se foi mexido. Comparacao e feita aqui (autoridade e o backend,
    nao um flag mandado pela tela) contra o texto que montar_comentario_analise
    geraria de novo agora."""
    comentarios_atuais = client.comentarios_aluno(analise["id_aluno"])
    data_anterior = data_ultima_analise_automatica(comentarios_atuais)
    if data_anterior and not ignorar_analise_anterior:
        return {"gravado": False, "ja_existe_analise": True, "data_analise_anterior": data_anterior}

    assunto, tipo, texto_sugerido = montar_comentario_analise(analise)
    editado = texto_editado is not None and texto_editado.strip() != texto_sugerido.strip()
    texto_final = texto_editado if editado else texto_sugerido
    prefixo = PREFIXO_TEXTO_EDITADO if editado else PREFIXO_TEXTO_ORIGINAL
    status_code = client.gravar_comentario(analise["id_aluno"], prefixo + assunto, tipo, texto_final)
    return {
        "gravado": status_code == 200, "ja_existe_analise": False,
        "data_analise_anterior": data_anterior, "editado": editado,
    }


_TEXTO_MATRICULA_RECONCILIACAO = (
    "Matriculado após confirmação manual - reconciliação Situação x Turma de Controle."
)


def _ultimo_comentario_analise(comentarios):
    """Comentario de analise automatica mais recente ja gravado pra este
    aluno (ver _e_titulo_analise) - usado por aplicar_acao pra mesclar nele
    a matricula em vez de criar um comentario novo e duplicado. None se
    nunca foi gravada nenhuma analise pra este aluno."""
    candidatos = [c for c in comentarios if _e_titulo_analise(c["titulo"])]
    return candidatos[-1] if candidatos else None


def aplicar_acao(client, analise):
    """Aplica a acao_sugerida de uma analise (mudanca de status ou matricula
    em turma de controle). SO deve ser chamada depois de confirmacao
    explicita do usuario, aluno por aluno - nunca automaticamente. Retorna
    (sucesso, descricao).

    matricular_turma: pedido do usuario (2026-09-08) - em vez de gravar um
    SEGUNDO comentario separado so pra matricula (duplicando o registro do
    aluno com dois comentarios quase identicos no mesmo dia), procura o
    comentario de ANALISE mais recente ja gravado pra este aluno (por
    registrar_analise) e o EDITA: vira tipo '15' (-Matricula) na turma de
    controle, o titulo ganha o sufixo ' -- IA --' (marca visualmente que a
    decisao foi assistida pelo sistema, nao digitada manualmente por um
    funcionario), e a informacao da matricula e acrescentada ao texto da
    analise num bloco separado (ver SEPARADOR_BLOCO), preservando o
    historico da analise em vez de substitui-lo.

    Se nao encontrar nenhum comentario de analise pra mesclar (ex: usuario
    aplicou a acao sem ter gravado a analise antes - os dois botoes na tela
    sao independentes), cai no comportamento anterior: grava um comentario
    novo so de matricula."""
    acao = analise.get("acao_sugerida")
    if not acao:
        return False, "nenhuma ação pendente pra este aluno"

    if acao["tipo"] == "mudar_status":
        status_code = client.atualizar_status_aluno(analise["id_aluno"], acao["novo_status"])
        return status_code == 200, f"Situação alterada para {acao['novo_status_nome']}"

    if acao["tipo"] == "matricular_turma":
        comentario_analise = _ultimo_comentario_analise(client.comentarios_aluno(analise["id_aluno"]))
        if comentario_analise:
            titulo_base = comentario_analise["titulo"]
            novo_titulo = titulo_base if titulo_base.endswith(" -- IA --") else titulo_base + " -- IA --"
            novo_texto = (comentario_analise["texto"] or "") + SEPARADOR_BLOCO + _TEXTO_MATRICULA_RECONCILIACAO
            status_code = client.editar_comentario(
                analise["id_aluno"], comentario_analise["id_acomp"],
                assunto=novo_titulo, tipo="15", descricao=novo_texto,
                turma_id=acao["turma_id"], turma_nome=acao["turma_nome"],
            )
        else:
            status_code = client.gravar_comentario(
                analise["id_aluno"], "MATRÍCULA EM TURMA DE CONTROLE (RECONCILIAÇÃO)", "15",
                _TEXTO_MATRICULA_RECONCILIACAO, turma_id=acao["turma_id"], turma_nome=acao["turma_nome"],
            )
        return status_code == 200, f"Matriculado em {acao['turma_nome']}"

    return False, f"tipo de ação desconhecido: {acao['tipo']}"


_COLUNAS_RELATORIO = [
    "id_aluno", "nome", "caso", "valor_final", "vencimento_referencia", "fonte_vencimento",
    "acao_tipo", "acao_novo_status", "acao_turma_id", "acao_descricao",
]


def exportar_relatorio_csv(resultado, caminho):
    """Exporta as ACOES PENDENTES (nao o resultado inteiro - so o que
    precisa de decisao humana) pra um CSV, SO PRA LEITURA/REVISAO - nao
    existe mais um jeito de aplicar esse CSV de volta em lote (ver nota
    abaixo). Alunos 'consistente'/'convenio_ok'/'nao_elegivel' nao entram
    aqui - ja foram documentados no comentario de analise, nao precisam de
    decisao."""
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_COLUNAS_RELATORIO)
        w.writeheader()
        for a in resultado["acoes_pendentes"]:
            acao = a["acao_sugerida"]
            w.writerow({
                "id_aluno": a["id_aluno"], "nome": a["nome"], "caso": a["caso"],
                "valor_final": f"{a['valor_final']:.2f}".replace(".", ","),
                "vencimento_referencia": a["vencimento_mais_antigo"].strftime("%d/%m/%Y") if a["vencimento_mais_antigo"] else "",
                "fonte_vencimento": a["fonte_vencimento"],
                "acao_tipo": acao["tipo"],
                "acao_novo_status": acao.get("novo_status", ""),
                "acao_turma_id": acao.get("turma_id", ""),
                "acao_descricao": (
                    f"Mudar Situação para {acao.get('novo_status_nome')}" if acao["tipo"] == "mudar_status"
                    else f"Matricular em {acao.get('turma_nome')}"
                ),
            })
    return caminho


# REMOVIDO DE PROPOSITO em 2026-09-05 (pedido explicito do usuario apos o
# "flood" de comentarios automaticos gravados em 04/09/2026): existia aqui
# uma funcao aplicar_relatorio_csv() que lia de volta um CSV com uma coluna
# "aprovado" preenchida a mao e aplicava TODAS as linhas aprovadas numa
# unica rodada sem nenhuma confirmacao ao vivo no momento da gravacao - esse
# era exatamente o mecanismo que permitia um lote inteiro ser gravado de
# uma vez so. A partir de agora, TODA gravacao (comentario de analise,
# mudanca de status, matricula em turma - de qualquer processo do sistema,
# nao so reconciliacao) tem que passar por confirmacao humana individual no
# momento da gravacao, aluno por aluno - use registrar_analise()/
# aplicar_acao() diretamente (um de cada vez) ou a tela web, nunca em loop
# automatico sobre uma lista de aprovacoes pre-preenchida.


def rodar_reconciliacao(client, limite=None, data_execucao=None, progresso=None,
                         dias_minimos=PRAZO_PADRAO_DIAS, dias_maximos=PRAZO_MAXIMO_DIAS):
    """Orquestra a analise inteira: lista devedores e roda a analise de
    cada um. SO LEITURA - nunca grava nada (revisto em 2026-09-04: nem o
    comentario de analise e mais gravado automaticamente aqui). Gravar o
    comentario de analise, marcar Urgente, mudar status ou matricular em
    turma - tudo isso agora exige chamada explicita e confirmada por aluno
    (registrar_analise() / aplicar_acao()), nunca em lote sozinho.

    limite: numero maximo de alunos a processar (None = todos) - util pra
    rodar em lotes, ja que sao centenas de devedores e cada analise faz
    varias chamadas de rede. Lista vem ordenada do devedor mais ANTIGO
    primeiro, pra um limite pequeno ainda pegar os casos mais relevantes.
    dias_minimos: prazo minimo de divida em aberto pra elegibilidade -
    padrao 365 dias (~1 ano), mas o usuario pode ajustar.
    dias_maximos: prazo maximo antes da divida provavelmente ter
    prescrito (ver PRAZO_MAXIMO_DIAS) - padrao 1825 dias (~5 anos),
    tambem ajustavel.
    progresso: callback opcional chamado a cada aluno processado, recebe a
    analise - util pra imprimir status durante rodadas longas."""
    devedores = listar_devedores_para_analise(client)
    if limite:
        devedores = devedores[:limite]

    analises = []
    for aluno_bruto in devedores:
        # Pedido do usuario (2026-09-12): mostrar a matricula na tela em vez
        # do id_aluno interno - matricula so existe no cadastro completo
        # (detalhes_alunos.php), pagina que mais nada aqui precisa ler, daí a
        # chamada extra so pra isso.
        cadastro = client.buscar_cadastro_completo(aluno_bruto["id_aluno"])
        analise = analisar_aluno(
            client, aluno_bruto, data_execucao, dias_minimos, dias_maximos,
            matricula=cadastro.get("matricula", ""),
        )
        analises.append(analise)
        if progresso:
            progresso(analise)

    acoes_pendentes = [a for a in analises if a["acao_sugerida"]]
    resumo = {}
    for a in analises:
        resumo[a["caso"]] = resumo.get(a["caso"], 0) + 1

    return {
        "total_analisados": len(analises), "resumo_por_caso": resumo,
        "acoes_pendentes": acoes_pendentes, "analises": analises,
    }

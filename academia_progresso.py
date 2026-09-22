"""
Progresso do aluno pela sequência de módulos de uma "academia" (curso
multi-módulo) - pedido do Diógenes via Caio (2026-09-16), corrigindo um
erro nosso: "Ex-aluno" de verdade NÃO é só o campo Situação do Fuctura.

Regra confirmada com o Diógenes: um aluno só é "ex-aluno" de verdade se
tiver sido matriculado em TODOS os módulos da sequência da academia
pelo menos uma vez, com presença real - E não for Devedor - E não
estiver marcado agora como abandono ("." no nome, "ponto" na fala dele)
ou ainda em refazendo/continuidade ("-" no nome - continuidade é o
aluno que fez um ou mais módulos e pediu pra refazer).

Sequências confirmadas (2026-09-16):
  - Java: J1, J2, JS3, JA4
  - Python: PY1, PY2, PY3, PY4
Linux e PHP ficam de fora por pedido explícito do usuário - sem turma
ativa há muito tempo, baixíssima prioridade.

Também dá a base pro indicador de "funil" que o Diógenes pediu: por
turma, quantos alunos estão em primeira vez NESTE módulo (avançando de
verdade na sequência, não refazendo/abandono) - turma com poucos "primeira
vez" é sinal pra avaliar se vale abrir a próxima turma da sequência
(exemplo dele: J2 com 15 matriculados e só 3 prontos pra avançar não
justifica sozinho abrir uma turma de JS3/JA4).
"""
import re
from datetime import datetime

import fechamento_logic as logic

SEQUENCIAS_ACADEMIA = {
    "java": ["J1", "J2", "JS3", "JA4"],
    "python": ["PY1", "PY2", "PY3", "PY4"],
}

# Pedido do usuário (2026-09-18): J1 e PY1 são os módulos de ENTRADA da
# academia (porta de entrada dos clientes novos pra Fuctura) - só podem
# iniciar com no mínimo 10 matriculados de PRIMEIRA VEZ (nem refazendo,
# nem ex-aluno, nem continuidade). Ver analisar_turma_entrada.
MODULOS_ENTRADA = ["J1", "PY1"]
LIMIAR_MINIMO_TURMA_ENTRADA = 10

# ACHADO (2026-09-18, relatado pelo usuário: "ele mostrou aí interessados,
# tem que mostrar o que estão matriculados apenas contabilizando"): o
# roster de uma turma de curso de verdade (roster_turma) traz qualquer
# aluno cuja Situação ATUAL no Fuctura (campo 'status', ver
# fuctura_client.STATUS_ALUNO) esteja ligada àquele id_turma - inclusive
# quem nunca chegou a matricular de verdade (só "Interessado"/"Cliente sem
# Interesse") ou já desistiu ("Cancelado"). Esses nunca devem contar nem
# aparecer na lista de matriculados de uma turma de entrada.
STATUS_NAO_MATRICULADO_DE_VERDADE = {"Interessado", "Cliente sem Interesse", "Cancelado"}


def eh_status_matriculado_real(status):
    """True se o status atual do aluno (campo 'status' do roster) indica
    matrícula de verdade - qualquer coisa fora de
    STATUS_NAO_MATRICULADO_DE_VERDADE conta (inclusive Devedor/Advogado:
    já passaram pela matrícula de verdade, só estão numa situação
    financeira/administrativa à parte agora). status vazio/None conta como
    matriculado (sem dado pra afirmar o contrário)."""
    return (status or "").strip() not in STATUS_NAO_MATRICULADO_DE_VERDADE


def eh_matricula_financeira_real(contratado, recebido):
    """ACHADO (2026-09-18, terceira rodada - "tem que ver se esses
    realmente estão matriculados ou só foram matriculados na turma, sem
    ser matriculados de verdade"): status != Interessado/Cancelado NÃO
    garante matrícula de verdade - cruzando o roster de uma turma real com
    o financeiro de cada aluno (perfil_aluno), achei gente com status
    "Devedor" e até "-Matriculado" com Contratado = R$ 0,00 e nunca pagou
    nada (ex real: LUANA DE LIMA POROCA ALMEIDA - Devedor, contratado=0,
    recebido=0 - nunca assinou contrato nem pagou nada, só ficou vinculada
    à turma administrativamente).

    Decisão do usuário: quem pagou um sinal mesmo sem "Contratado" formal
    preenchido ainda conta como matrícula real - mas 'recebido' aqui
    precisa ser o valor DE VERDADE (ver recebido_real), não o bruto de
    perfil_aluno: achado real no mesmo dia, JADSON AUGUSTO PEREIRA DA
    ROSA aparecia com recebido=75 só por causa de 2 comentários de TESTE
    (sobra de teste de desenvolvimento gravada por engano nesse aluno
    real) - sem eles, ele não tem nem contrato nem pagamento nenhum de
    verdade e não deveria contar."""
    return (contratado or 0) > 0 or (recebido or 0) > 0


def eh_comentario_pagamento_teste(titulo, texto):
    """ACHADO (2026-09-18, mesma rodada): sobra de teste de
    desenvolvimento gravada por engano num aluno REAL (JADSON AUGUSTO
    PEREIRA DA ROSA) - 2 comentários "-Pagamento Realizado" com "teste"
    no título/texto (ex: "TESTE - Pagamento Realizado (convenção)",
    "Parcela: 2/1 (TESTE via endpoint HTTP)") fizeram o Fuctura contar
    R$75 como recebido de verdade, quando o aluno nem assinou contrato
    ainda. Por decisão do usuário, não mexe no histórico real do Fuctura
    (não é seguro editar em lote) - só ignora esses comentários no
    CÁLCULO de quem é matriculado de verdade (ver recebido_real)."""
    alvo = f"{titulo or ''} {texto or ''}".lower()
    return "teste" in alvo


def recebido_real(comentarios):
    """Soma os valores de pagamento REAIS do histórico de comentários de
    um aluno (mesma detecção de fechamento_logic.checagem_financeira),
    ignorando comentários de teste (ver eh_comentario_pagamento_teste).
    Usado como segunda checagem só quando Contratado=0 no perfil (a zona
    cinzenta de eh_matricula_financeira_real) - contratado>0 já basta
    sozinho, não precisa dessa checagem extra (mais cara, precisa do
    histórico completo de comentários)."""
    total = 0.0
    for c in comentarios:
        titulo = c.get("titulo", "")
        texto = c.get("texto", "")
        if eh_comentario_pagamento_teste(titulo, texto):
            continue
        tipo_n = (c.get("tipo") or "").strip().lower()
        texto_full = f"{titulo} {texto}"
        e_pagamento = "pagamento realizado" in tipo_n or bool(logic.PAGAMENTO_RE.search(texto_full))
        if not e_pagamento:
            continue
        m = logic.VALOR_RE.search(texto_full)
        if not m:
            continue
        try:
            total += float(m.group(1).replace(".", "").replace(",", "."))
        except ValueError:
            continue
    return total


def eh_observacao_monitor(observacao):
    """ACHADO (2026-09-18, mesma rodada): EDSON VINICIUS SOUZA DOS SANTOS
    aparecia como "matriculado de primeira vez" numa turma de entrada
    (J1), mas na verdade é MONITOR (ex-aluno ajudando o professor) - o
    próprio campo 'observacao' do roster já mostra isso (ex real: "AG J2
    A4 CONT MONITO" - truncado, mas reconhecível). Monitor não é aluno
    novo nem matrícula de verdade pro propósito desta tela - não conta.
    Checagem por substring (não só "MONITOR" inteiro) porque o Fuctura
    trunca esse campo em telas de listagem."""
    return "monito" in (observacao or "").lower()


_RE_OBSERVACAO_REFAZENDO = re.compile(r"\b(REF|CONT)\b", re.IGNORECASE)


def eh_observacao_refazendo(observacao):
    """ACHADO (2026-09-18, revisão comentário a comentário pedida pelo
    usuário - "seria interessante fazer o comment-by-comment de cada
    registro pra confirmar"): o nome do aluno nem sempre é atualizado com
    a marca "-" quando ele volta pra refazer um módulo - mas o campo
    'observacao' do roster (já disponível, sem custo extra) costuma trazer
    "REF" (refazendo) ou "CONT" (continuidade) mesmo quando o nome não tem
    marca nenhuma. Casos reais: DAVID ARMSTRONG SOARES SIMAO (nome sem
    marca, observacao='J1 REF', comentário confirma "pediu para refazer
    J1" - contava como Primeira vez, mas está refazendo); MARCOS AUGUSTO
    FERREIRA CAMPOS (observacao='PY1 PRES J2 CONT' - é aluno de Java
    continuando pra J2, não um aluno novo de Python). Não cobre TODO caso
    de refazendo sem marca - ver eh_status_ex_aluno pra um caso irmão
    (observação ambígua tipo 'AG J1', mas status Ex-aluno já denuncia)."""
    return bool(_RE_OBSERVACAO_REFAZENDO.search(observacao or ""))


def eh_status_ex_aluno(status):
    """ACHADO (2026-09-18, mesma revisão): Ex-aluno (já completou uma
    trilha inteira antes, ver eh_ex_aluno_de_verdade) nunca é "primeira
    vez" de verdade - aparecer numa turma de ENTRADA (J1/PY1) com esse
    status só pode significar que está voltando pra refazer, mesmo
    quando nome e observação não têm marca nenhuma reconhecível. Caso
    real: ALBERTO RICARDO MENDES DE SOUZA - status Ex-aluno, nome sem
    marca, observação ambígua ('AG J1', não 'REF') - só o status revela
    que ele não é aluno novo (confirmado pelo histórico: já completou
    J1/J2/S3/A4 antes, voltando por pedido da mãe pra refazer)."""
    return (status or "").strip() == "Ex-aluno"


def turma_ainda_aberta(data_termino_str, hoje=None):
    """True se a turma ainda não terminou (dataTermino de obter_turma() é
    hoje ou no futuro) - trata data ausente/ilegível como aberta (sem dado
    suficiente pra afirmar que já fechou). Pedido do usuário (2026-09-18:
    "não tem 30 turmas abertas, tem bem menos") - o corte anterior só
    olhava a data EMBUTIDA NO NOME da turma (data de início), sem checar
    se ela já tinha terminado."""
    if not (data_termino_str or "").strip():
        return True
    try:
        termino = datetime.strptime(data_termino_str.strip(), "%d/%m/%Y")
    except ValueError:
        return True
    return termino.date() >= (hoje or datetime.now()).date()

# Sigla do módulo no INÍCIO do nome da turma (ignora o prefixo "."/"-"
# de abandono/refazendo, que fica no nome do ALUNO, não da turma) -
# ex: "JA4 25/07/26 Sab M", "J1 08/07/25 TER N", "PY2 01/07/23 Sab T".
# \b depois da sigla evita "J1" casar com "J12" ou coisa parecida.
#
# ACHADO REAL (2026-09-22, relatado pelo usuário - "tentei acessar minha
# página como aluno... e não achei o botão de certificado"): o próprio
# usuário (Caio de Matos Vital, id 33199) completou Java em 2021
# (J1->J2->J3->J4), mas curso_completo/eh_ex_aluno_de_verdade davam False
# porque só "JS3"/"JA4" eram reconhecidos - a nomenclatura ANTIGA do
# Fuctura pro 3º/4º módulo de Java era "J3"/"J4" (achado confirmado ao
# vivo: 80 turmas de 2017-2021 usam esse padrão, buscando ".J3"/".J4").
# Python NÃO tem esse problema (nomenclatura sempre foi "PY3"/"PY4" -
# confirmado ao vivo, buscar ".P3"/".P4" não acha nada). "J3"/"J4" viram
# os códigos canônicos "JS3"/"JA4" aqui, pra tudo mais no sistema (Ex-
# aluno de verdade, restrição de quem pode marcar Ex-aluno, certificado)
# continuar funcionando sem precisar saber dessa nomenclatura antiga.
_RE_MODULO = re.compile(r"^\.?\s*(J1|J2|JS3|JA4|J3|J4|PY1|PY2|PY3|PY4)\b", re.IGNORECASE)
_ALIAS_MODULO_ANTIGO = {"J3": "JS3", "J4": "JA4"}


def identificar_modulo(nome_turma):
    """Extrai a sigla do módulo (ex: 'JA4') do nome de uma turma, ou None
    se não bater com nenhuma sequência conhecida (turma de controle,
    curso fora de Java/Python, etc.). Nomenclatura antiga "J3"/"J4" (ver
    _ALIAS_MODULO_ANTIGO) já sai traduzida pro código canônico."""
    m = _RE_MODULO.match((nome_turma or "").strip())
    if not m:
        return None
    codigo = m.group(1).upper()
    return _ALIAS_MODULO_ANTIGO.get(codigo, codigo)


def identificar_trilha(modulo):
    """'JA4' -> 'java', 'PY2' -> 'python', None se não reconhecido
    (inclusive Linux/PHP - fora do mapa de propósito, ver docstring)."""
    for trilha, modulos in SEQUENCIAS_ACADEMIA.items():
        if modulo in modulos:
            return trilha
    return None


def trilha_mais_recente(turmas_atuais):
    """Descobre a trilha (java/python) pela turma reconhecível MAIS
    RECENTE do aluno - usado quando quem chama não já sabe qual trilha
    conferir. None se nenhuma turma bater com Java/Python."""
    ordenadas = sorted(turmas_atuais or [], key=lambda t: t.get("data", ""), reverse=True)
    for t in ordenadas:
        modulo = identificar_modulo(t.get("nome", ""))
        if modulo:
            trilha = identificar_trilha(modulo)
            if trilha:
                return trilha
    return None


def modulos_cursados(turmas_atuais, trilha):
    """Conjunto de módulos (siglas) da trilha em que o aluno já foi
    matriculado pelo menos uma vez, segundo o histórico de turmas."""
    necessarios = set(SEQUENCIAS_ACADEMIA.get(trilha, []))
    if not necessarios:
        return set()
    encontrados = set()
    for t in turmas_atuais or []:
        modulo = identificar_modulo(t.get("nome", ""))
        if modulo in necessarios:
            encontrados.add(modulo)
    return encontrados


def curso_completo(turmas_atuais, trilha):
    """True se o aluno tem pelo menos 1 matrícula em CADA módulo da
    sequência dessa trilha (Java ou Python) - False pra trilha
    desconhecida/vazia (Linux/PHP, ou nome de turma não reconhecido)."""
    necessarios = set(SEQUENCIAS_ACADEMIA.get(trilha, []))
    if not necessarios:
        return False
    return necessarios.issubset(modulos_cursados(turmas_atuais, trilha))


def eh_ex_aluno_de_verdade(nome_aluno, status_atual, turmas_atuais, trilha=None):
    """Regra do Diógenes (2026-09-16): diferente do campo Situação do
    Fuctura (que pode estar desatualizado ou nunca ter sido pensado pra
    isso) - "ex-aluno" de verdade exige completar TODOS os módulos da
    trilha, não ser Devedor agora, e não estar marcado com abandono (".")
    nem refazendo/continuidade ("-") no nome agora.

    trilha: force 'java'/'python' se já souber; None tenta descobrir
    pela turma reconhecível mais recente do aluno (ver trilha_mais_recente).
    Devolve False (nunca lança erro) quando a trilha não é reconhecida -
    sem dado suficiente pra afirmar "ex-aluno de verdade", trata como não."""
    if (status_atual or "").strip().lower() == "devedor":
        return False
    if logic.eh_nome_marcado_abandono(nome_aluno) or logic.eh_nome_marcado_refazendo(nome_aluno):
        return False
    if trilha is None:
        trilha = trilha_mais_recente(turmas_atuais)
    if not trilha:
        return False
    return curso_completo(turmas_atuais, trilha)


LIMIAR_FUNIL_BAIXO = 0.3  # abaixo disso, vale avaliar se compensa abrir a próxima turma (ajustável)


def categoria_nome(nome, observacao=None, status=None):
    """Classificação rápida pela marca do nome (sem consultar o Fuctura) -
    o mesmo método manual que o Diógenes usa: "-" no nome = Refazendo, "."
    no nome = Abandono, sem marca = Primeira vez. `observacao`/`status` são
    opcionais (compatibilidade com quem só passa o nome) - quando
    disponíveis (roster_turma já traz os dois), também conta como
    Refazendo se a observação tiver "REF"/"CONT" (eh_observacao_refazendo)
    ou se o status for "Ex-aluno" (eh_status_ex_aluno) - a equipe nem
    sempre atualiza o nome quando o aluno volta pra refazer. Pra
    confirmação mais forte de "completou a academia de verdade" (exige o
    histórico real de turmas), ver eh_ex_aluno_de_verdade - mais lento
    (precisa de perfil_aluno), por isso fica separado desta função rápida
    usada no relatório inicial da turma (antes de consultar o Fuctura
    aluno por aluno)."""
    if logic.eh_nome_marcado_refazendo(nome) or eh_observacao_refazendo(observacao) or eh_status_ex_aluno(status):
        return "Refazendo"
    if logic.eh_nome_marcado_abandono(nome):
        return "Abandono"
    return "Primeira vez"


_RE_DATA_NO_NOME = re.compile(r"(\d{2})/(\d{2})/(\d{2,4})")


def extrair_data_turma(nome_turma):
    """Acha a primeira data (DD/MM/AA ou DD/MM/AAAA) dentro do nome da
    turma - ex: "J1 08/07/25 TER N" -> 08/07/2025. Usado só pra ORDENAR
    por "mais recente primeiro" (ver analisar_turmas_entrada) - nunca
    pra decisão de negócio. None se não achar nenhuma data reconhecível
    no nome."""
    m = _RE_DATA_NO_NOME.search(nome_turma or "")
    if not m:
        return None
    dia, mes, ano = m.groups()
    if len(ano) == 2:
        ano = "20" + ano
    try:
        return datetime(int(ano), int(mes), int(dia))
    except ValueError:
        return None


def analisar_turma_entrada(nome_turma, roster, limiar_minimo=LIMIAR_MINIMO_TURMA_ENTRADA):
    """Pra turmas de ENTRADA (J1/PY1 - ver MODULOS_ENTRADA): conta quantos
    matriculados são de PRIMEIRA VEZ de verdade (nem refazendo, nem
    abandono/continuidade - mesma classificação de categoria_nome) e
    compara com o mínimo pra turma poder iniciar (pedido do usuário,
    2026-09-18: "só pode iniciar com no mínimo 10 matriculados pela
    primeira vez"). roster: lista de {'nome', 'status', ...} (ex:
    roster_turma()) - filtra fora quem não matriculou de verdade
    (Interessado/Cliente sem Interesse/Cancelado, ver
    eh_status_matriculado_real) antes de contar, pedido do usuário
    (2026-09-18: "ele mostrou aí interessados, tem que mostrar o que
    estão matriculados apenas contabilizando")."""
    matriculados = [a for a in roster if eh_status_matriculado_real(a.get("status"))]
    funil = indicador_funil_turma(matriculados)
    return {
        "nome_turma": nome_turma,
        "modulo": identificar_modulo(nome_turma),
        "data": extrair_data_turma(nome_turma),
        "total_matriculados": funil["total"],
        "primeira_vez": funil["primeira_vez"],
        "limiar_minimo": limiar_minimo,
        "atinge_minimo": funil["primeira_vez"] >= limiar_minimo,
    }


def indicador_funil_turma(alunos_da_turma):
    """alunos_da_turma: lista de {'nome'} (ou qualquer dict com 'nome') -
    ex: o roster de uma turma. Conta quantos estão "primeira vez" nesse
    módulo (sem marca de abandono/refazendo no nome AGORA, sem "REF"/
    "CONT" na observação - eh_observacao_refazendo -, e status diferente
    de "Ex-aluno" - eh_status_ex_aluno -, quando presentes) vs o total -
    pedido do Diógenes: turma com poucos "primeira vez" é sinal pra
    avaliar se vale abrir a próxima turma da sequência (ex: J2 com 15
    matriculados e só 3 prontos pra avançar). 'observacao'/'status' são
    opcionais no dict de cada aluno (ausentes = só nome decide, compatível
    com quem chama sem esses campos, ex: fechamento_logic)."""
    total = len(alunos_da_turma)
    primeira_vez = sum(
        1 for a in alunos_da_turma
        if not logic.eh_nome_marcado_abandono(a["nome"])
        and not logic.eh_nome_marcado_refazendo(a["nome"])
        and not eh_observacao_refazendo(a.get("observacao"))
        and not eh_status_ex_aluno(a.get("status"))
    )
    return {
        "total": total, "primeira_vez": primeira_vez,
        "proporcao_primeira_vez": (primeira_vez / total) if total else 0.0,
    }

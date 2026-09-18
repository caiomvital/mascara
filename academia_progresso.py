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

# Sigla do módulo no INÍCIO do nome da turma (ignora o prefixo "."/"-"
# de abandono/refazendo, que fica no nome do ALUNO, não da turma) -
# ex: "JA4 25/07/26 Sab M", "J1 08/07/25 TER N", "PY2 01/07/23 Sab T".
# \b depois da sigla evita "J1" casar com "J12" ou coisa parecida.
_RE_MODULO = re.compile(r"^\.?\s*(J1|J2|JS3|JA4|PY1|PY2|PY3|PY4)\b", re.IGNORECASE)


def identificar_modulo(nome_turma):
    """Extrai a sigla do módulo (ex: 'JA4') do nome de uma turma, ou None
    se não bater com nenhuma sequência conhecida (turma de controle,
    curso fora de Java/Python, etc.)."""
    m = _RE_MODULO.match((nome_turma or "").strip())
    return m.group(1).upper() if m else None


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


def categoria_nome(nome):
    """Classificação rápida só pela marca do nome (sem consultar o
    Fuctura) - o mesmo método manual que o Diógenes usa: "-" no nome =
    Refazendo, "." no nome = Abandono, sem marca = Primeira vez. Pra
    confirmação mais forte de "completou a academia de verdade" (exige
    o histórico real de turmas), ver eh_ex_aluno_de_verdade - mais lento
    (precisa de perfil_aluno), por isso fica separado desta função
    rápida usada no relatório inicial da turma (antes de consultar o
    Fuctura aluno por aluno)."""
    if logic.eh_nome_marcado_refazendo(nome):
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
    primeira vez"). roster: lista de {'nome', ...} (ex: roster_turma())."""
    funil = indicador_funil_turma(roster)
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
    módulo (sem marca de abandono/refazendo no nome AGORA) vs o total -
    pedido do Diógenes: turma com poucos "primeira vez" é sinal pra
    avaliar se vale abrir a próxima turma da sequência (ex: J2 com 15
    matriculados e só 3 prontos pra avançar)."""
    total = len(alunos_da_turma)
    primeira_vez = sum(
        1 for a in alunos_da_turma
        if not logic.eh_nome_marcado_abandono(a["nome"]) and not logic.eh_nome_marcado_refazendo(a["nome"])
    )
    return {
        "total": total, "primeira_vez": primeira_vez,
        "proporcao_primeira_vez": (primeira_vez / total) if total else 0.0,
    }

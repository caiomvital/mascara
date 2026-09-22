"""
Testes de academia_progresso.py - sem rede nenhuma. Usa dados REAIS já
confirmados ao vivo (turma .JA4 09/07/26 Qui, professor Yago,
investigada em 2026-09-16 - Miguel Tomaz Apolonio de Souza, id 46431,
tem status "Ex-aluno" no Fuctura mas isso sozinho não confirma que ele
completou a trilha de verdade, que é exatamente o que este módulo
resolve) como fixture.

Como rodar:
    cd testes_logica
    python test_academia_progresso.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import academia_progresso as ap  # noqa: E402

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


def teste_identificar_modulo():
    relatar("identificar_modulo: 'JA4 25/07/26 Sab M' -> JA4", ap.identificar_modulo("JA4 25/07/26 Sab M") == "JA4", "")
    relatar("identificar_modulo: '.JA4 09/07/26 Qui' (com ponto de abandono) -> ainda reconhece JA4",
            ap.identificar_modulo(".JA4 09/07/26 Qui") == "JA4", "")
    relatar("identificar_modulo: 'J1 08/07/25 TER N' -> J1 (não confunde com JS3/JA4)", ap.identificar_modulo("J1 08/07/25 TER N") == "J1", "")
    relatar("identificar_modulo: 'JS3 30/04/26 QUI N' -> JS3", ap.identificar_modulo("JS3 30/04/26 QUI N") == "JS3", "")
    relatar("identificar_modulo: 'PY2 01/07/23 Sab T' -> PY2", ap.identificar_modulo("PY2 01/07/23 Sab T") == "PY2", "")
    relatar("identificar_modulo: turma de controle 'Devedor/Pendência' -> None", ap.identificar_modulo("Devedor/Pendência") is None, "")
    relatar("identificar_modulo: curso fora do mapa 'BL1 2025 8h30 Sab' (Bíblia) -> None", ap.identificar_modulo("BL1 2025 8h30 Sab") is None, "")


def teste_identificar_trilha():
    relatar("identificar_trilha: JA4 -> java", ap.identificar_trilha("JA4") == "java", "")
    relatar("identificar_trilha: PY3 -> python", ap.identificar_trilha("PY3") == "python", "")
    relatar("identificar_trilha: módulo desconhecido -> None", ap.identificar_trilha("XX9") is None, "")


def _turmas_ate(*modulos):
    """Monta turmas_atuais fake, uma por módulo passado, com datas
    crescentes (a última vem primeiro, igual perfil_aluno devolve)."""
    return [{"data": f"{10+i:02d}/01/2026", "nome": f"{m} 01/01 TER N"} for i, m in enumerate(modulos)][::-1]


def teste_curso_completo_java():
    relatar(
        "curso_completo: aluno com J1+J2+JS3+JA4 completou Java de verdade",
        ap.curso_completo(_turmas_ate("J1", "J2", "JS3", "JA4"), "java") is True,
        "",
    )
    relatar(
        "curso_completo: aluno só com J1+J2 (faltando JS3/JA4) NÃO completou Java",
        ap.curso_completo(_turmas_ate("J1", "J2"), "java") is False,
        "",
    )
    relatar(
        "curso_completo: trilha desconhecida/vazia sempre False (nunca afirma sem dado)",
        ap.curso_completo(_turmas_ate("J1", "J2", "JS3", "JA4"), "linux") is False,
        "",
    )


def teste_curso_completo_python_independente_de_java():
    turmas = _turmas_ate("J1", "PY1", "PY2", "PY3", "PY4")  # fez J1 de Java também, mas isso não conta pra Python
    relatar(
        "curso_completo: módulo de OUTRA trilha (J1) não conta pra completar Python",
        ap.curso_completo(turmas, "python") is True and ap.curso_completo(turmas, "java") is False,
        "",
    )


def teste_trilha_mais_recente():
    turmas = [
        {"data": "10/01/2025", "nome": "PY1 05/01 SEG N"},
        {"data": "20/06/2026", "nome": "JA4 10/06 QUA N"},  # mais recente
    ]
    relatar(
        "trilha_mais_recente: escolhe a trilha da turma com data mais recente (Java), não a mais antiga (Python)",
        ap.trilha_mais_recente(turmas) == "java",
        "",
    )
    relatar("trilha_mais_recente: sem turma reconhecível -> None", ap.trilha_mais_recente([{"data": "01/01/2026", "nome": "Devedor/Pendência"}]) is None, "")


def teste_ex_aluno_de_verdade_completou_tudo():
    turmas = _turmas_ate("J1", "J2", "JS3", "JA4")
    relatar(
        "eh_ex_aluno_de_verdade: completou os 4 módulos, sem marca no nome, não é Devedor -> True",
        ap.eh_ex_aluno_de_verdade("JOAO DA SILVA", "Ex-aluno", turmas) is True,
        "",
    )


def teste_ex_aluno_de_verdade_devedor_nunca_e_ex_aluno():
    turmas = _turmas_ate("J1", "J2", "JS3", "JA4")
    relatar(
        "eh_ex_aluno_de_verdade: mesmo tendo completado tudo, se o status atual é Devedor -> False (regra explícita do Diógenes)",
        ap.eh_ex_aluno_de_verdade("JOAO DA SILVA", "Devedor", turmas) is False,
        "",
    )


def teste_ex_aluno_de_verdade_marca_abandono_desqualifica():
    turmas = _turmas_ate("J1", "J2", "JS3", "JA4")
    relatar(
        "eh_ex_aluno_de_verdade: nome com '.' (abandono/'ponto') desqualifica mesmo tendo completado tudo",
        ap.eh_ex_aluno_de_verdade(".JOAO DA SILVA", "Ex-aluno", turmas) is False,
        "",
    )


def teste_ex_aluno_de_verdade_marca_refazendo_desqualifica():
    turmas = _turmas_ate("J1", "J2", "JS3", "JA4")
    relatar(
        "eh_ex_aluno_de_verdade: nome com '-' (refazendo/continuidade) desqualifica - ainda em andamento",
        ap.eh_ex_aluno_de_verdade("-JOAO DA SILVA", "Ex-aluno", turmas) is False,
        "",
    )


def teste_ex_aluno_de_verdade_incompleto_mesmo_com_status_ex_aluno():
    # ACHADO REAL (2026-09-16): Miguel Tomaz Apolonio de Souza (id 46431)
    # tem status "Ex-aluno" no Fuctura, mas o Fuctura sozinho não confere
    # se ele passou por TODOS os módulos - este é exatamente o caso que
    # motivou a correção do Diógenes.
    turmas_incompletas = _turmas_ate("J1", "J2")  # só 2 dos 4 módulos
    relatar(
        "eh_ex_aluno_de_verdade: status Fuctura diz Ex-aluno mas faltam módulos -> False (não confia só no status)",
        ap.eh_ex_aluno_de_verdade("MIGUEL TOMAZ APOLONIO DE SOUZA", "Ex-aluno", turmas_incompletas) is False,
        "",
    )


def teste_ex_aluno_de_verdade_trilha_nao_reconhecida_nunca_afirma():
    relatar(
        "eh_ex_aluno_de_verdade: sem turma de trilha reconhecível (Linux/PHP/outro) -> False, nunca afirma sem dado",
        ap.eh_ex_aluno_de_verdade("JOAO DA SILVA", "Ex-aluno", [{"data": "01/01/2026", "nome": "BL1 2025 8h30 Sab"}]) is False,
        "",
    )


def teste_indicador_funil_turma():
    # mesma proporcao encontrada ao vivo na turma .JA4 09/07/26 Qui (Yago):
    # 21 no roster, 9 com "-" (refazendo), 8 com "." (abandono), 4 sem marca (primeira vez)
    roster = (
        [{"nome": f"-REFAZENDO{i}"} for i in range(9)]
        + [{"nome": f".ABANDONO{i}"} for i in range(8)]
        + [{"nome": f"PRIMEIRAVEZ{i}"} for i in range(4)]
    )
    resultado = ap.indicador_funil_turma(roster)
    relatar(
        "indicador_funil_turma: conta certo total e primeira_vez (mesma proporção da turma real .JA4 09/07/26 Qui)",
        resultado["total"] == 21 and resultado["primeira_vez"] == 4,
        f"resultado: {resultado}",
    )
    relatar(
        "indicador_funil_turma: proporção calculada corretamente (4/21)",
        abs(resultado["proporcao_primeira_vez"] - 4 / 21) < 0.0001,
        f"resultado: {resultado}",
    )


def teste_indicador_funil_turma_vazia_nao_quebra():
    resultado = ap.indicador_funil_turma([])
    relatar(
        "indicador_funil_turma: turma vazia não quebra (proporção 0, não ZeroDivisionError)",
        resultado["total"] == 0 and resultado["proporcao_primeira_vez"] == 0.0,
        f"resultado: {resultado}",
    )


def teste_eh_observacao_refazendo():
    """Casos reais (2026-09-18, revisão comentário a comentário pedida
    pelo usuário): DAVID ARMSTRONG SOARES SIMAO (nome sem marca,
    observacao='J1 REF', comentário confirma "pediu para refazer J1") e
    MARCOS AUGUSTO FERREIRA CAMPOS (observacao='PY1 PRES J2 CONT' - aluno
    de Java continuando, não aluno novo de Python)."""
    relatar(
        "eh_observacao_refazendo: 'J1 REF' é reconhecido (caso real: DAVID ARMSTRONG)",
        ap.eh_observacao_refazendo("J1 REF") is True,
        "",
    )
    relatar(
        "eh_observacao_refazendo: 'PY1 PRES J2 CONT' é reconhecido (caso real: MARCOS AUGUSTO)",
        ap.eh_observacao_refazendo("PY1 PRES J2 CONT") is True,
        "",
    )
    relatar(
        "eh_observacao_refazendo: observação normal ('PY1 PRES', 'ADULTO', 'AG J1') não é marcada",
        ap.eh_observacao_refazendo("PY1 PRES") is False
        and ap.eh_observacao_refazendo("ADULTO") is False
        and ap.eh_observacao_refazendo("AG J1") is False,
        "",
    )
    relatar(
        "eh_observacao_refazendo: vazio/None não quebra e não é refazendo",
        ap.eh_observacao_refazendo("") is False and ap.eh_observacao_refazendo(None) is False,
        "",
    )


def teste_indicador_funil_turma_observacao_refazendo_sem_marca_no_nome():
    """Regressão direta: nome sem marca "-" mas observação com REF/CONT
    não pode contar como primeira vez - achado ao vivo (DAVID ARMSTRONG,
    ALBERTO RICARDO: nomes sem marca, mas genuinamente refazendo)."""
    roster = [
        {"nome": "DAVID ARMSTRONG SOARES SIMAO", "observacao": "J1 REF"},
        {"nome": "MARCOS AUGUSTO FERREIRA CAMPOS", "observacao": "PY1 PRES J2 CONT"},
        {"nome": "ALEF ADONAIS SEVERINO DA SILVA", "observacao": "J1 PRES"},
    ]
    resultado = ap.indicador_funil_turma(roster)
    relatar(
        "indicador_funil_turma: observação com REF/CONT desconta de primeira_vez mesmo sem marca no nome",
        resultado["total"] == 3 and resultado["primeira_vez"] == 1,
        f"resultado: {resultado}",
    )


def teste_indicador_funil_turma_sem_observacao_nao_quebra():
    """fechamento_logic passa só {'nome': ...}, sem 'observacao' - não
    pode quebrar (compatibilidade com o call site existente)."""
    roster = [{"nome": "ALUNO SEM OBSERVACAO"}]
    resultado = ap.indicador_funil_turma(roster)
    relatar(
        "indicador_funil_turma: roster sem campo 'observacao' não quebra (compatibilidade com fechamento_logic)",
        resultado["total"] == 1 and resultado["primeira_vez"] == 1,
        f"resultado: {resultado}",
    )


def teste_categoria_nome_com_observacao_refazendo():
    relatar(
        "categoria_nome: nome sem marca mas observação 'J1 REF' -> Refazendo",
        ap.categoria_nome("DAVID ARMSTRONG SOARES SIMAO", "J1 REF") == "Refazendo",
        "",
    )
    relatar(
        "categoria_nome: sem observação (compatibilidade) continua funcionando só pelo nome",
        ap.categoria_nome("-REFAZENDO1") == "Refazendo" and ap.categoria_nome("ALUNO NOVO") == "Primeira vez",
        "",
    )


def teste_eh_status_ex_aluno():
    """Caso real: ALBERTO RICARDO MENDES DE SOUZA - status Ex-aluno, nome
    sem marca, observação ambígua ('AG J1', não 'REF') - só o status
    revela que ele não é aluno novo (já completou a trilha antes)."""
    relatar(
        "eh_status_ex_aluno: 'Ex-aluno' é reconhecido (caso real: ALBERTO RICARDO)",
        ap.eh_status_ex_aluno("Ex-aluno") is True,
        "",
    )
    relatar(
        "eh_status_ex_aluno: 'Devedor'/'-Matriculado' não são Ex-aluno",
        ap.eh_status_ex_aluno("Devedor") is False and ap.eh_status_ex_aluno("-Matriculado") is False,
        "",
    )
    relatar(
        "eh_status_ex_aluno: vazio/None não quebra e não é Ex-aluno",
        ap.eh_status_ex_aluno("") is False and ap.eh_status_ex_aluno(None) is False,
        "",
    )


def teste_categoria_nome_com_status_ex_aluno():
    relatar(
        "categoria_nome: nome sem marca, observação ambígua, mas status Ex-aluno -> Refazendo (caso real: ALBERTO)",
        ap.categoria_nome("ALBERTO RICARDO MENDES DE SOUZA", "AG J1", "Ex-aluno") == "Refazendo",
        "",
    )


def teste_indicador_funil_turma_status_ex_aluno_sem_marca_no_nome():
    """Regressão direta: Ex-aluno com nome sem marca e observação ambígua
    não pode contar como primeira vez - achado ao vivo (ALBERTO RICARDO,
    status Ex-aluno, observacao='AG J1' não pegava no filtro de REF/CONT)."""
    roster = [
        {"nome": "ALBERTO RICARDO MENDES DE SOUZA", "observacao": "AG J1", "status": "Ex-aluno"},
        {"nome": "ALEF ADONAIS SEVERINO DA SILVA", "observacao": "J1 PRES", "status": "Devedor"},
    ]
    resultado = ap.indicador_funil_turma(roster)
    relatar(
        "indicador_funil_turma: status Ex-aluno desconta de primeira_vez mesmo com nome/observação sem marca",
        resultado["total"] == 2 and resultado["primeira_vez"] == 1,
        f"resultado: {resultado}",
    )


# ---------------------------------------------------------------------------
# Pedido do usuário (2026-09-18): J1/PY1 são os módulos de ENTRADA da
# academia - só podem iniciar com no mínimo 10 matriculados de primeira
# vez de verdade (nem refazendo, nem abandono/continuidade).
# ---------------------------------------------------------------------------
def teste_extrair_data_turma():
    relatar(
        "extrair_data_turma: 'J1 08/07/25 TER N' -> 08/07/2025 (ano com 2 dígitos)",
        ap.extrair_data_turma("J1 08/07/25 TER N") == datetime(2025, 7, 8),
        "",
    )
    relatar(
        "extrair_data_turma: 'PY1 02/09/2025 TER N' -> 02/09/2025 (ano com 4 dígitos)",
        ap.extrair_data_turma("PY1 02/09/2025 TER N") == datetime(2025, 9, 2),
        "",
    )
    relatar(
        "extrair_data_turma: nome sem nenhuma data reconhecível -> None, não quebra",
        ap.extrair_data_turma("Devedor/Pendência") is None,
        "",
    )


def teste_analisar_turma_entrada_atinge_minimo():
    roster = [{"nome": f"ALUNO{i}"} for i in range(12)]  # 12 sem marca = 12 primeira vez
    r = ap.analisar_turma_entrada("J1 08/07/25 TER N", roster)
    relatar(
        "analisar_turma_entrada: com 12 de primeira vez (>= 10), atinge o mínimo",
        r["primeira_vez"] == 12 and r["atinge_minimo"] is True and r["modulo"] == "J1",
        f"resultado: {r}",
    )


def teste_analisar_turma_entrada_abaixo_do_minimo():
    roster = (
        [{"nome": f"-REFAZENDO{i}"} for i in range(5)]
        + [{"nome": f"ALUNO{i}"} for i in range(3)]  # só 3 de primeira vez
    )
    r = ap.analisar_turma_entrada("PY1 02/09/25 TER N", roster)
    relatar(
        "analisar_turma_entrada: com só 3 de primeira vez (< 10), NÃO atinge o mínimo - mesmo com 8 matriculados no total",
        r["primeira_vez"] == 3 and r["total_matriculados"] == 8 and r["atinge_minimo"] is False,
        f"resultado: {r}",
    )


def teste_analisar_turma_entrada_limiar_customizado():
    roster = [{"nome": f"ALUNO{i}"} for i in range(5)]
    r = ap.analisar_turma_entrada("J1 08/07/25 TER N", roster, limiar_minimo=5)
    relatar(
        "analisar_turma_entrada: limiar customizado é respeitado (5 de primeira vez, limiar 5, atinge)",
        r["atinge_minimo"] is True,
        f"resultado: {r}",
    )


def teste_eh_status_matriculado_real():
    relatar(
        "eh_status_matriculado_real: 'Interessado' não conta como matriculado de verdade",
        ap.eh_status_matriculado_real("Interessado") is False,
        "",
    )
    relatar(
        "eh_status_matriculado_real: 'Cliente sem Interesse' não conta como matriculado de verdade",
        ap.eh_status_matriculado_real("Cliente sem Interesse") is False,
        "",
    )
    relatar(
        "eh_status_matriculado_real: 'Cancelado' não conta como matriculado de verdade",
        ap.eh_status_matriculado_real("Cancelado") is False,
        "",
    )
    relatar(
        "eh_status_matriculado_real: 'Devedor' continua contando (já matriculou de verdade)",
        ap.eh_status_matriculado_real("Devedor") is True,
        "",
    )
    relatar(
        "eh_status_matriculado_real: '-Matriculado' conta",
        ap.eh_status_matriculado_real("-Matriculado") is True,
        "",
    )
    relatar(
        "eh_status_matriculado_real: status vazio/None conta (sem dado pra afirmar o contrário)",
        ap.eh_status_matriculado_real(None) is True and ap.eh_status_matriculado_real("") is True,
        "",
    )


def teste_analisar_turma_entrada_filtra_interessado_e_cancelado():
    roster = [
        {"nome": "ANA", "status": "-Matriculado"},
        {"nome": "BRUNO", "status": "Interessado"},
        {"nome": "CARLA", "status": "Cancelado"},
        {"nome": "DAVI", "status": "Devedor"},
    ]
    r = ap.analisar_turma_entrada("J1 08/07/25 TER N", roster)
    relatar(
        "analisar_turma_entrada: Interessado/Cancelado não contam no total (só ANA e DAVI)",
        r["total_matriculados"] == 2 and r["primeira_vez"] == 2,
        f"resultado: {r}",
    )


def teste_turma_ainda_aberta_data_futura():
    amanha = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    relatar(
        "turma_ainda_aberta: dataTermino no futuro -> aberta",
        ap.turma_ainda_aberta(amanha) is True,
        "",
    )


def teste_turma_ainda_aberta_data_passada():
    ontem = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
    relatar(
        "turma_ainda_aberta: dataTermino no passado -> já fechou",
        ap.turma_ainda_aberta(ontem) is False,
        "",
    )


def teste_turma_ainda_aberta_sem_data():
    relatar(
        "turma_ainda_aberta: sem dataTermino cadastrada -> trata como aberta (sem dado pra afirmar o contrário)",
        ap.turma_ainda_aberta("") is True and ap.turma_ainda_aberta(None) is True,
        "",
    )


def teste_turma_ainda_aberta_data_ilegivel_nao_quebra():
    relatar(
        "turma_ainda_aberta: data em formato inesperado não quebra - trata como aberta",
        ap.turma_ainda_aberta("data inválida") is True,
        "",
    )


def teste_eh_matricula_financeira_real():
    """Casos reais achados ao vivo (2026-09-18, "tem que ver se esses
    realmente estão matriculados ou só foram matriculados na turma, sem
    ser matriculados de verdade"): LUANA (Devedor, contratado=0,
    recebido=0) nunca pagou nada - não é matrícula real. JADSON
    ("-Matriculado", contratado=0, recebido=75) pagou um sinal mesmo sem
    Contratado formal - decisão do usuário: ainda conta."""
    relatar(
        "eh_matricula_financeira_real: contratado=0 e recebido=0 -> não é matrícula real (caso real: LUANA)",
        ap.eh_matricula_financeira_real(0.0, 0.0) is False,
        "",
    )
    relatar(
        "eh_matricula_financeira_real: contratado=0 mas recebido>0 -> conta (caso real: JADSON, pagou sinal de R$75)",
        ap.eh_matricula_financeira_real(0.0, 75.0) is True,
        "",
    )
    relatar(
        "eh_matricula_financeira_real: contratado>0 mesmo com recebido=0 -> conta (contrato assinado, ainda não pago)",
        ap.eh_matricula_financeira_real(4901.0, 0.0) is True,
        "",
    )
    relatar(
        "eh_matricula_financeira_real: contratado>0 e recebido>0 -> conta",
        ap.eh_matricula_financeira_real(4901.0, 4901.0) is True,
        "",
    )
    relatar(
        "eh_matricula_financeira_real: None em qualquer um dos dois não quebra (trata como 0)",
        ap.eh_matricula_financeira_real(None, None) is False,
        "",
    )


def teste_eh_comentario_pagamento_teste():
    """Caso real (2026-09-18, revisão comentário a comentário pedida pelo
    usuário): JADSON tinha 2 comentários "-Pagamento Realizado" com
    "teste" no título/texto - sobra de um teste anterior da tela
    Registrar Pagamento gravada por engano nesse aluno real."""
    relatar(
        'eh_comentario_pagamento_teste: título com "TESTE" é reconhecido (caso real do JADSON)',
        ap.eh_comentario_pagamento_teste("TESTE - Pagamento Realizado (convenção)", "Valor: R$ 50,00") is True,
        "",
    )
    relatar(
        'eh_comentario_pagamento_teste: texto com "teste" também é reconhecido (case-insensitive)',
        ap.eh_comentario_pagamento_teste("Pagamento Realizado", "Parcela: 2/1 (TESTE via endpoint HTTP). Valor: R$ 25,00.") is True,
        "",
    )
    relatar(
        "eh_comentario_pagamento_teste: pagamento real (sem menção a teste) não é marcado",
        ap.eh_comentario_pagamento_teste("PAGAMENTO PIX", "Valor: R$ 377,00") is False,
        "",
    )


def teste_recebido_real_ignora_comentarios_de_teste():
    """Regressão direta do achado real: sem filtrar os 2 comentários de
    teste, JADSON somaria R$75 (50+25) como se tivesse pago de verdade -
    recebido_real deve ignorá-los e não somar nada."""
    comentarios_jadson = [
        {"tipo": "-Pagamento Realizado", "titulo": "TESTE - Pagamento Realizado (convenção)",
         "texto": "Teste de convenção: Forma: Boleto. Parcela: 1/1 (TESTE). Valor: R$ 50,00. Data: 15/09/2026."},
        {"tipo": "-Pagamento Realizado", "titulo": "Pagamento Realizado",
         "texto": "Forma: Boleto Bancário. Parcela: 2/1 (TESTE via endpoint HTTP). Valor: R$ 25,00. Observação: teste do endpoint real"},
        {"tipo": "-Matricula", "titulo": "INTERESSADO", "texto": "42 anos, hoje é técnico de manutenção."},
    ]
    relatar(
        "recebido_real: ignora os 2 comentários de teste do JADSON - soma 0, não 75",
        ap.recebido_real(comentarios_jadson) == 0.0,
        f"resultado: {ap.recebido_real(comentarios_jadson)}",
    )


def teste_recebido_real_soma_pagamento_de_verdade():
    comentarios = [
        {"tipo": "-Pagamento Realizado", "titulo": "PAGAMENTO PIX", "texto": "Valor: R$ 377,00"},
        {"tipo": "-Comentário", "titulo": "ACOMPANHAMENTO", "texto": "sem valor nenhum aqui"},
    ]
    relatar(
        "recebido_real: soma pagamento de verdade normalmente (377,00)",
        ap.recebido_real(comentarios) == 377.0,
        f"resultado: {ap.recebido_real(comentarios)}",
    )


def teste_eh_observacao_monitor():
    """Caso real: EDSON VINICIUS SOUZA DOS SANTOS aparecia como "primeira
    vez" numa turma de entrada, mas o campo observacao do roster mostrava
    "AG J2 A4 CONT MONITO" (truncado) - é monitor, não aluno novo."""
    relatar(
        'eh_observacao_monitor: "AG J2 A4 CONT MONITO" (truncado, caso real do EDSON) é reconhecido',
        ap.eh_observacao_monitor("AG J2 A4 CONT MONITO") is True,
        "",
    )
    relatar(
        'eh_observacao_monitor: "MONITOR" por extenso também é reconhecido',
        ap.eh_observacao_monitor("MONITOR") is True,
        "",
    )
    relatar(
        "eh_observacao_monitor: observação normal (ex: 'ADULTO', 'AG J1') não é marcada como monitor",
        ap.eh_observacao_monitor("ADULTO") is False and ap.eh_observacao_monitor("AG J1") is False,
        "",
    )
    relatar(
        "eh_observacao_monitor: vazio/None não quebra e não é monitor",
        ap.eh_observacao_monitor("") is False and ap.eh_observacao_monitor(None) is False,
        "",
    )


def main():
    print("Rodando testes de academia_progresso.py (sem rede)...\n")
    teste_identificar_modulo()
    teste_identificar_trilha()
    teste_curso_completo_java()
    teste_curso_completo_python_independente_de_java()
    teste_trilha_mais_recente()
    teste_ex_aluno_de_verdade_completou_tudo()
    teste_ex_aluno_de_verdade_devedor_nunca_e_ex_aluno()
    teste_ex_aluno_de_verdade_marca_abandono_desqualifica()
    teste_ex_aluno_de_verdade_marca_refazendo_desqualifica()
    teste_ex_aluno_de_verdade_incompleto_mesmo_com_status_ex_aluno()
    teste_ex_aluno_de_verdade_trilha_nao_reconhecida_nunca_afirma()
    teste_indicador_funil_turma()
    teste_indicador_funil_turma_vazia_nao_quebra()
    teste_eh_observacao_refazendo()
    teste_indicador_funil_turma_observacao_refazendo_sem_marca_no_nome()
    teste_indicador_funil_turma_sem_observacao_nao_quebra()
    teste_categoria_nome_com_observacao_refazendo()
    teste_eh_status_ex_aluno()
    teste_categoria_nome_com_status_ex_aluno()
    teste_indicador_funil_turma_status_ex_aluno_sem_marca_no_nome()
    teste_extrair_data_turma()
    teste_analisar_turma_entrada_atinge_minimo()
    teste_analisar_turma_entrada_abaixo_do_minimo()
    teste_analisar_turma_entrada_limiar_customizado()
    teste_eh_status_matriculado_real()
    teste_analisar_turma_entrada_filtra_interessado_e_cancelado()
    teste_turma_ainda_aberta_data_futura()
    teste_turma_ainda_aberta_data_passada()
    teste_turma_ainda_aberta_sem_data()
    teste_turma_ainda_aberta_data_ilegivel_nao_quebra()
    teste_eh_matricula_financeira_real()
    teste_eh_comentario_pagamento_teste()
    teste_recebido_real_ignora_comentarios_de_teste()
    teste_recebido_real_soma_pagamento_de_verdade()
    teste_eh_observacao_monitor()

    print(f"\n{'=' * 70}")
    print(f"Total OK: {_ok_count} | Total FALHOU: {len(_falhas)}")
    if _falhas:
        print("\nFalhas:")
        for nome, detalhe in _falhas:
            print(f"  - {nome}: {detalhe}")
        sys.exit(1)
    print("Nenhuma regressão detectada.")


if __name__ == "__main__":
    main()

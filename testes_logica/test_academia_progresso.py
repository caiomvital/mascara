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
from datetime import datetime

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
    teste_extrair_data_turma()
    teste_analisar_turma_entrada_atinge_minimo()
    teste_analisar_turma_entrada_abaixo_do_minimo()
    teste_analisar_turma_entrada_limiar_customizado()

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

"""
Testes de regressao pra anexos_email.py (montagem de anexos do email de
cobranca - contrato + atas preenchidas) - sem rede nenhuma, com um
FucturaClient falso.

Como rodar:
    cd testes_logica
    python test_anexos_email.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anexos_email  # noqa: E402
from fuctura_client import TURMA_ADVOGADO_ID, TURMA_DEVEDOR_ID  # noqa: E402

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


class _ClienteFake:
    """FucturaClient falso: perfil com turmas fixas, contrato PDF fake,
    buscar_turma_por_nome resolve pelo nome do dicionario abaixo."""

    def __init__(self, turmas_atuais, mapa_nome_para_id):
        self._turmas_atuais = turmas_atuais
        self._mapa = mapa_nome_para_id

    def perfil_aluno(self, id_aluno):
        return {"nome": "ALUNO TESTE", "turmas_atuais": self._turmas_atuais}

    def gerar_contrato_aluno(self, id_aluno):
        return b"%PDF-fake"

    def buscar_turma_por_nome(self, nome):
        ids = self._mapa.get(nome, [])
        return [{"id_turma": i, "nome": nome} for i in ids]


# ---------------------------------------------------------------------------
# Achado ao vivo em 2026-09-08 testando a Reconciliacao de Devedores de
# verdade: a turma de controle "Devedor/Pendencia" aparece na lista de
# turmas de TODO aluno devedor - sem filtro, gerava o aviso "nenhuma ata
# preenchida encontrada" pra ela em 100% dos casos (ela nunca tem ata
# nenhuma, e nunca vai ter - nao e aula de verdade).
# ---------------------------------------------------------------------------
def teste_turma_de_controle_devedor_nao_gera_aviso():
    cliente = _ClienteFake(
        turmas_atuais=[{"data": "14/07/2026", "nome": "Devedor/Pendencia"}],
        mapa_nome_para_id={"Devedor/Pendencia": [TURMA_DEVEDOR_ID]},
    )
    resultado = anexos_email.montar_anexos_aluno(cliente, "1")
    relatar(
        "Turma de controle 'Devedor/Pendencia' não gera aviso de ata faltando",
        resultado["avisos"] == [],
        f"avisos: {resultado['avisos']}",
    )


def teste_turma_de_controle_advogado_nao_gera_aviso():
    cliente = _ClienteFake(
        turmas_atuais=[{"data": "14/07/2026", "nome": "Aguardando Advogado"}],
        mapa_nome_para_id={"Aguardando Advogado": [TURMA_ADVOGADO_ID]},
    )
    resultado = anexos_email.montar_anexos_aluno(cliente, "1")
    relatar(
        "Turma de controle 'Aguardando Advogado' não gera aviso de ata faltando",
        resultado["avisos"] == [],
        f"avisos: {resultado['avisos']}",
    )


def teste_turma_de_verdade_sem_ata_continua_avisando():
    # controle (garante que o filtro so exclui as turmas de controle, nao
    # silencia o aviso legitimo de uma turma de aula de verdade sem ata).
    cliente = _ClienteFake(
        turmas_atuais=[{"data": "14/07/2026", "nome": "PY2 07/12/24 SAB M"}],
        mapa_nome_para_id={"PY2 07/12/24 SAB M": ["9999"]},
    )
    resultado = anexos_email.montar_anexos_aluno(cliente, "1")
    relatar(
        "Turma de aula de verdade sem ata preenchida continua gerando aviso normalmente",
        len(resultado["avisos"]) == 1 and "PY2 07/12/24 SAB M" in resultado["avisos"][0],
        f"avisos: {resultado['avisos']}",
    )


def teste_turma_ambigua_continua_avisando():
    cliente = _ClienteFake(
        turmas_atuais=[{"data": "14/07/2026", "nome": "EX LINUX I"}],
        mapa_nome_para_id={"EX LINUX I": ["1", "2", "3"]},
    )
    resultado = anexos_email.montar_anexos_aluno(cliente, "1")
    relatar(
        "Turma com nome ambíguo (mais de 1 resultado) continua avisando pra conferência manual",
        len(resultado["avisos"]) == 1 and "ambíguo" in resultado["avisos"][0],
        f"avisos: {resultado['avisos']}",
    )


def main():
    print("Rodando testes de anexos_email.py (sem rede nenhuma)...\n")
    teste_turma_de_controle_devedor_nao_gera_aviso()
    teste_turma_de_controle_advogado_nao_gera_aviso()
    teste_turma_de_verdade_sem_ata_continua_avisando()
    teste_turma_ambigua_continua_avisando()

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

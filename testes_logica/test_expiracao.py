"""
Testes da expiração de sessions/jobs (achado real via teste de estresse,
2026-09-06 — corrigido 2026-09-08): nenhum dos três dicionários em
memória (sessions, fechamento_jobs, reconciliacao_jobs) tinha limite de
vida, crescendo pra sempre enquanto o servidor roda.

Como rodar:
    cd testes_logica
    python test_expiracao.py
"""
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib.util
_spec = importlib.util.spec_from_file_location("app", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"))
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)

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


def teste_sessao_antiga_expira():
    d = {
        "antiga": {"criado_em": time.time() - (app.SESSAO_TTL_SEGUNDOS + 60)},
        "recente": {"criado_em": time.time() - 60},
    }
    app._limpar_expirados(d, app.SESSAO_TTL_SEGUNDOS)
    relatar(
        "Sessão mais velha que o TTL é removida, a recente fica",
        list(d.keys()) == ["recente"],
        f"restou: {list(d.keys())}",
    )


def teste_job_antigo_expira():
    d = {
        "antigo": {"criado_em": (datetime.now() - timedelta(seconds=app.JOB_TTL_SEGUNDOS + 60)).isoformat()},
        "recente": {"criado_em": datetime.now().isoformat()},
    }
    app._limpar_expirados(d, app.JOB_TTL_SEGUNDOS)
    relatar(
        "Job mais velho que o TTL é removido, o recente fica",
        list(d.keys()) == ["recente"],
        f"restou: {list(d.keys())}",
    )


def teste_entrada_sem_data_nao_quebra():
    d = {"sem_data": {"outra_coisa": 1}, "recente": {"criado_em": datetime.now().isoformat()}}
    app._limpar_expirados(d, app.JOB_TTL_SEGUNDOS)
    relatar(
        "Entrada sem campo de data não quebra a limpeza (fica, não é removida às cegas)",
        set(d.keys()) == {"sem_data", "recente"},
        f"restou: {list(d.keys())}",
    )


def main():
    print("Rodando testes de expiração de sessions/jobs...\n")
    teste_sessao_antiga_expira()
    teste_job_antigo_expira()
    teste_entrada_sem_data_nao_quebra()

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

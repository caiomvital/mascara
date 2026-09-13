"""
Testes da parte de cora_client.py que ja da pra testar sem credencial
nenhuma (montar_resumo_boletos e esta_configurado) - o resto
(CoraClient.listar_boletos_em_aberto) ainda nao esta implementado de
proposito, esperando credenciais reais.

Como rodar:
    cd testes_logica
    python test_cora_client.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cora_client  # noqa: E402

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


def teste_resumo_com_boletos():
    boletos = [
        {"id": "1", "vencimento": "20/09/2026", "valor": 300.0, "status": "aberto"},
        {"id": "2", "vencimento": "05/09/2026", "valor": 150.5, "status": "vencido"},
    ]
    resumo = cora_client.montar_resumo_boletos("ALUNO TESTE", boletos)
    relatar(
        "montar_resumo_boletos: total e ordenação por vencimento corretos",
        resumo["total"] == 450.5
        and resumo["quantidade"] == 2
        and resumo["boletos"][0]["id"] == "2"  # vencimento mais antigo primeiro
        and "2 boleto(s)" in resumo["resumo_texto"]
        and "450,50" in resumo["resumo_texto"],
        f"resumo: {resumo}",
    )


def teste_resumo_sem_boletos():
    resumo = cora_client.montar_resumo_boletos("ALUNO TESTE", [])
    relatar(
        "montar_resumo_boletos: sem boletos gera texto claro, não erro",
        resumo["quantidade"] == 0 and resumo["total"] == 0 and "não tem boletos" in resumo["resumo_texto"],
        f"resumo: {resumo}",
    )


def teste_nao_configurado_por_padrao():
    relatar(
        "esta_configurado(): False por padrão (sem credenciais preenchidas)",
        cora_client.esta_configurado() is False,
        "retornou True sem nenhuma credencial configurada",
    )


def teste_cliente_recusa_sem_configuracao():
    try:
        cora_client.CoraClient()
        ok = False
        detalhe = "não levantou CoraNaoConfiguradoError"
    except cora_client.CoraNaoConfiguradoError:
        ok = True
        detalhe = ""
    relatar("CoraClient() recusa se não configurado", ok, detalhe)


def main():
    print("Rodando testes de cora_client.py (parte que já dá pra testar sem credencial)...\n")
    teste_resumo_com_boletos()
    teste_resumo_sem_boletos()
    teste_nao_configurado_por_padrao()
    teste_cliente_recusa_sem_configuracao()

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

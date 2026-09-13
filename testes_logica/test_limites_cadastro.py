"""
Testes dos limites reais de caractere do cadastro de aluno, descobertos ao
vivo em 2026-09-08 (mesmo padrão do abreviada/descrição de turma: o
Fuctura trunca silenciosamente, sem o HTML anunciar limite nenhum).

Como rodar:
    cd testes_logica
    python test_limites_cadastro.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib.util
_spec = importlib.util.spec_from_file_location("app", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"))
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)

from fuctura_client import FucturaClient  # noqa: E402

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


def teste_campo_dentro_do_limite_passa():
    dados = {"bairro": "B" * 50, "cidade": "C" * 50}
    erro = app._validar_limites_cadastro(dados)
    relatar("Campo exatamente no limite (50) passa", erro is None, f"erro: {erro!r}")


def teste_campo_acima_do_limite_e_rejeitado():
    dados = {"bairro": "B" * 51}
    erro = app._validar_limites_cadastro(dados)
    relatar(
        "Campo 1 caractere acima do limite é rejeitado",
        erro is not None and "bairro" in erro and "50" in erro,
        f"erro: {erro!r}",
    )


def teste_todos_os_limites_conhecidos():
    for campo, limite in FucturaClient.LIMITES_CADASTRO_ALUNO.items():
        dados = {campo: "X" * (limite + 1)}
        erro = app._validar_limites_cadastro(dados)
        relatar(
            f"Limite de '{campo}' ({limite}) é aplicado",
            erro is not None and campo in erro,
            f"erro: {erro!r}",
        )


def teste_campo_vazio_nao_e_erro():
    erro = app._validar_limites_cadastro({})
    relatar("Dict vazio não gera erro", erro is None, f"erro: {erro!r}")


def main():
    print("Rodando testes de limites de cadastro...\n")
    teste_campo_dentro_do_limite_passa()
    teste_campo_acima_do_limite_e_rejeitado()
    teste_todos_os_limites_conhecidos()
    teste_campo_vazio_nao_e_erro()

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

"""
Testes da validação de CPF (aluno e responsável) e da obrigatoriedade
condicional de responsável quando o aluno é menor de idade - pedido
explícito do usuário (2026-09-08), depois do achado real do caso VITOR
FONSECA VELOSO (CPF do responsável com 8 dígitos, claramente um telefone
digitado no campo errado, sem nenhuma validação pra pegar isso).

Como rodar:
    cd testes_logica
    python test_validacao_cpf_responsavel.py
"""
import os
import sys

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


def validar(cpf="", cpf_responsavel="", data_nascimento="", nome_responsavel=""):
    dados = {"cpf": cpf, "cpfResponsavel": cpf_responsavel, "dataNascimento": data_nascimento, "nomeResponsavel": nome_responsavel}
    erro = app._validar_cpf_e_responsavel(dados)
    return erro, dados


def teste_cpf_com_8_digitos_e_rejeitado():
    """Padrão real: caso Vitor Fonseca Veloso, cpfResponsavel="99742511" (telefone, não CPF)."""
    erro, _ = validar(cpf_responsavel="99742511")
    relatar(
        "CPF com 8 dígitos (padrão do bug real do Vitor) é rejeitado",
        erro is not None and "CPF do responsável" in erro,
        f"erro: {erro!r}",
    )


def teste_cpf_valido_aceito_e_normalizado():
    erro, dados = validar(cpf="081.331.884-07")
    relatar(
        "CPF válido com pontuação é aceito e normalizado pra só dígitos",
        erro is None and dados["cpf"] == "08133188407",
        f"erro: {erro!r} | cpf normalizado: {dados['cpf']!r}",
    )


def teste_cpf_vazio_nao_e_erro():
    erro, _ = validar(cpf="", cpf_responsavel="")
    relatar(
        "CPF vazio não é erro por si só (obrigatoriedade é decidida à parte)",
        erro is None,
        f"erro: {erro!r}",
    )


def teste_menor_de_idade_sem_responsavel_e_rejeitado():
    erro, _ = validar(data_nascimento="15/01/2015")  # ~11 anos em 2026
    relatar(
        "Aluno menor de idade sem responsável é rejeitado",
        erro is not None and "menor de idade" in erro,
        f"erro: {erro!r}",
    )


def teste_menor_de_idade_com_responsavel_e_aceito():
    erro, _ = validar(data_nascimento="15/01/2015", cpf_responsavel="12345678901", nome_responsavel="MAE DO ALUNO")
    relatar(
        "Aluno menor de idade COM responsável completo é aceito",
        erro is None,
        f"erro: {erro!r}",
    )


def teste_adulto_sem_responsavel_e_aceito():
    erro, _ = validar(data_nascimento="15/01/1990")
    relatar(
        "Aluno adulto sem responsável é aceito (não obrigatório)",
        erro is None,
        f"erro: {erro!r}",
    )


def teste_sem_data_nascimento_nao_forca_responsavel():
    """Sem data de nascimento não dá pra confirmar menoridade - não pode
    forçar responsável às cegas."""
    erro, _ = validar()
    relatar(
        "Sem data de nascimento informada, responsável não é forçado",
        erro is None,
        f"erro: {erro!r}",
    )


def teste_responsavel_adulto_terceiro_tambem_e_valido():
    """Decisão do usuário (2026-09-08): 'responsável' cobre tanto
    responsável legal (menor) quanto adulto cujo terceiro paga (pai, tio,
    amigo) - o campo em si não distingue, então um aluno ADULTO com
    responsável preenchido (terceiro pagante) tem que passar normalmente."""
    erro, dados = validar(data_nascimento="15/01/1990", cpf_responsavel="123.456.789-01", nome_responsavel="TIO QUE PAGA")
    relatar(
        "Aluno adulto com responsável (terceiro pagante) é aceito e o CPF normalizado",
        erro is None and dados["cpfResponsavel"] == "12345678901",
        f"erro: {erro!r} | cpfResponsavel normalizado: {dados.get('cpfResponsavel')!r}",
    )


def main():
    print("Rodando testes de validação de CPF/responsável...\n")
    teste_cpf_com_8_digitos_e_rejeitado()
    teste_cpf_valido_aceito_e_normalizado()
    teste_cpf_vazio_nao_e_erro()
    teste_menor_de_idade_sem_responsavel_e_rejeitado()
    teste_menor_de_idade_com_responsavel_e_aceito()
    teste_adulto_sem_responsavel_e_aceito()
    teste_sem_data_nascimento_nao_forca_responsavel()
    teste_responsavel_adulto_terceiro_tambem_e_valido()

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

"""
Testes de regressao pra email_cobranca.py - sem rede nenhuma, so chamando
as funcoes puras com dicionarios falsos.

Como rodar:
    cd testes_logica
    python test_email_cobranca.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import email_cobranca  # noqa: E402

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


# ---------------------------------------------------------------------------
# determinar_responsavel_contrato - IMPORTANTE: isto NAO decide o
# destinatario do e-mail (correcao do usuario, 2026-09-08: o e-mail e um
# rascunho pro ADVOGADO, nunca enviado ao aluno/responsavel) - so identifica
# quem assinou o contrato, pra contexto no corpo do e-mail.
# ---------------------------------------------------------------------------
def teste_usa_responsavel_quando_cadastrado():
    cadastro = {
        "nome": "ALUNO MENOR", "cpf": "11111111111",
        "nomeResponsavel": "MAE DO ALUNO", "cpfResponsavel": "22222222222",
        "celularResponsavel": "81999990000", "emailResponsavel": "mae@example.com",
    }
    r = email_cobranca.determinar_responsavel_contrato(cadastro)
    relatar(
        "Com responsável cadastrado, aponta pro responsável (não pro aluno)",
        r["papel"] == "responsavel" and r["nome"] == "MAE DO ALUNO" and r["cpf_suspeito"] is False,
        f"resultado: {r}",
    )


def teste_usa_aluno_quando_sem_responsavel():
    cadastro = {"nome": "ALUNO ADULTO", "cpf": "33333333333", "celular": "81988887777", "email": "aluno@example.com"}
    r = email_cobranca.determinar_responsavel_contrato(cadastro)
    relatar(
        "Sem responsável cadastrado, aponta pro próprio aluno",
        r["papel"] == "aluno" and r["nome"] == "ALUNO ADULTO",
        f"resultado: {r}",
    )


def teste_cpf_suspeito_sinalizado():
    # achado real (caso Vitor Fonseca Veloso): cadastro antigo com telefone
    # no lugar do CPF - tem que sinalizar, nunca deixar passar batido numa
    # prova juridica/cobranca.
    cadastro = {"nome": "ALUNO ANTIGO", "cpf": "8113000000"}  # 10 dígitos, não 11
    r = email_cobranca.determinar_responsavel_contrato(cadastro)
    relatar(
        "CPF com número errado de dígitos é sinalizado como suspeito",
        r["cpf_suspeito"] is True,
        f"resultado: {r}",
    )


def teste_cpf_vazio_nao_e_suspeito():
    # cadastro sem CPF nenhum e um problema diferente (falta de dado, nao
    # dado errado) - nao deve acionar o mesmo alerta de "numero errado de
    # digitos".
    cadastro = {"nome": "ALUNO SEM CPF", "cpf": ""}
    r = email_cobranca.determinar_responsavel_contrato(cadastro)
    relatar(
        "CPF vazio não aciona o alerta de 'número errado de dígitos'",
        r["cpf_suspeito"] is False,
        f"resultado: {r}",
    )


def teste_preview_nao_expoe_mais_campo_destinatario_antigo():
    # trava a correcao de 2026-09-08: o campo antigo "destinatario" (que
    # dava a entender que essa pessoa recebia o e-mail) foi renomeado pra
    # "responsavel_contrato", e o preview agora sinaliza explicitamente
    # que o destinatario real (advogado) ainda esta pendente.
    relatar(
        "montar_preview_email não tem mais a função antiga 'determinar_destinatario'",
        not hasattr(email_cobranca, "determinar_destinatario"),
        "a função antiga ainda existe - renomeação incompleta",
    )


def main():
    print("Rodando testes de email_cobranca.py (sem rede nenhuma)...\n")
    teste_usa_responsavel_quando_cadastrado()
    teste_usa_aluno_quando_sem_responsavel()
    teste_cpf_suspeito_sinalizado()
    teste_cpf_vazio_nao_e_suspeito()
    teste_preview_nao_expoe_mais_campo_destinatario_antigo()

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

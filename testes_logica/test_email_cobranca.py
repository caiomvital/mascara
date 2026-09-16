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


# ---------------------------------------------------------------------------
# montar_resumo_aluno_sugerido / montar_deve_os_meses - campos do template
# real de e-mail que o Diógenes usa (exemplo trazido pelo usuário,
# 2026-09-16). NUNCA gera narrativa por IA (regra travada 2026-09-07) -
# só repassa dado já registrado, sempre editável antes de mandar.
# ---------------------------------------------------------------------------
def teste_resumo_aluno_sugerido_usa_ultimo_contato_registrado():
    comentarios = [
        # comentarios_aluno() do Fuctura vem em ordem ASC (mais antigo primeiro) - fixture segue a mesma ordem
        {"data": "20/01/2026", "titulo": "Ocorrência de Contato", "texto": "entramos em contato e ele informou que iria recomeçar o curso, mas não veio."},
        {"data": "05/09/2026", "titulo": "OCORRÊNCIA DE CONTATO", "texto": "Resultado: Não respondeu. Data: 05/09/2026."},
    ]
    r = email_cobranca.montar_resumo_aluno_sugerido(comentarios)
    relatar(
        "montar_resumo_aluno_sugerido usa o ÚLTIMO contato registrado (não o mais antigo), reconhece título maiúsculo/minúsculo",
        r.startswith("Em 05/09/2026,") and "Não respondeu" in r,
        f"resultado: {r!r}",
    )


def teste_resumo_aluno_sugerido_sem_contato_registrado():
    r = email_cobranca.montar_resumo_aluno_sugerido([{"data": "01/01/2026", "titulo": "GERAR BOLETOS", "texto": "13x de 350"}])
    relatar(
        "montar_resumo_aluno_sugerido sem nenhum contato registrado devolve placeholder pra escrever",
        r.startswith("["),
        f"resultado: {r!r}",
    )


def teste_deve_os_meses_cora_nao_configurada():
    r = email_cobranca.montar_deve_os_meses(None)
    relatar(
        "montar_deve_os_meses com CORA não configurada (None) devolve placeholder pré-pronto, não quebra",
        r.startswith("[") and "CORA" in r,
        f"resultado: {r!r}",
    )


def teste_deve_os_meses_formata_boletos_reais():
    boletos_cora = {"boletos": [
        {"valor": 350.0, "vencimento": "26/12/25"},
        {"valor": 350.0, "vencimento": "05/02/26"},
    ]}
    r = email_cobranca.montar_deve_os_meses(boletos_cora)
    relatar(
        "montar_deve_os_meses formata cada boleto real no formato do template ('R$ X,XX com Vencimento em DD/MM/AA')",
        r == "R$ 350,00 com Vencimento em 26/12/25\nR$ 350,00 com Vencimento em 05/02/26",
        f"resultado: {r!r}",
    )


def teste_deve_os_meses_sem_boletos_em_aberto():
    r = email_cobranca.montar_deve_os_meses({"boletos": []})
    relatar(
        "montar_deve_os_meses com CORA configurada mas 0 boletos em aberto diz isso claramente (não fica vazio/quebrado)",
        "Nenhum boleto" in r,
        f"resultado: {r!r}",
    )


def teste_deve_os_meses_erro_na_cora_nao_quebra():
    r = email_cobranca.montar_deve_os_meses({"erro": "timeout"})
    relatar(
        "montar_deve_os_meses com erro na consulta CORA mostra o erro, não quebra o e-mail inteiro",
        "[" in r and "timeout" in r,
        f"resultado: {r!r}",
    )


def main():
    print("Rodando testes de email_cobranca.py (sem rede nenhuma)...\n")
    teste_usa_responsavel_quando_cadastrado()
    teste_usa_aluno_quando_sem_responsavel()
    teste_cpf_suspeito_sinalizado()
    teste_cpf_vazio_nao_e_suspeito()
    teste_preview_nao_expoe_mais_campo_destinatario_antigo()
    teste_resumo_aluno_sugerido_usa_ultimo_contato_registrado()
    teste_resumo_aluno_sugerido_sem_contato_registrado()
    teste_deve_os_meses_cora_nao_configurada()
    teste_deve_os_meses_formata_boletos_reais()
    teste_deve_os_meses_sem_boletos_em_aberto()
    teste_deve_os_meses_erro_na_cora_nao_quebra()

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

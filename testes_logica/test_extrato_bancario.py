"""
Testes de extrato_bancario.py - sem rede nenhuma. Usa os formatos REAIS
de comentário '-Pagamento Realizado' já vistos no Fuctura (aluno Miguel
Tomaz, id 46431, investigado em 2026-09-16) como fixture, pra garantir
que a extração bate com o que a equipe realmente escreve, não um
formato inventado.

Como rodar:
    cd testes_logica
    python test_extrato_bancario.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import extrato_bancario as eb  # noqa: E402

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


def teste_parsear_valor_brl():
    relatar("parsear_valor_brl: 'R$ 377,00' -> 377.0", eb.parsear_valor_brl("R$ 377,00") == 377.0, "")
    relatar("parsear_valor_brl: '1.234,56' (milhar) -> 1234.56", eb.parsear_valor_brl("1.234,56") == 1234.56, "")
    relatar("parsear_valor_brl: texto inválido -> None", eb.parsear_valor_brl("abc") is None, "")


def teste_parsear_data_brl():
    relatar("parsear_data_brl: ano com 4 dígitos", eb.parsear_data_brl("21/08/2025") == datetime(2025, 8, 21), "")
    relatar("parsear_data_brl: ano com 2 dígitos (formato real visto: '21/01/26')",
            eb.parsear_data_brl("21/01/26") == datetime(2026, 1, 21), "")
    relatar("parsear_data_brl: inválida -> None", eb.parsear_data_brl("32/13/2026") is None, "")


def teste_parsear_csv_extrato_ignora_linhas_sem_valor():
    csv_texto = (
        "02/01/2026;PIX TRANSF WESLEY 02/01;R$ 453,00\n"
        "02/01/2026;;\n"  # linha de separador de dia, sem valor - deve ser ignorada
        "05/01/2026;PIX TRANSF HEROS M05/01;R$ 377,00\n"
    )
    linhas = eb.parsear_csv_extrato(csv_texto)
    relatar(
        "parsear_csv_extrato: só as linhas com data+valor válidos entram, o resto é ignorado sem quebrar",
        len(linhas) == 2 and linhas[0]["valor"] == 453.0 and linhas[1]["valor"] == 377.0,
        f"linhas: {linhas}",
    )


def teste_extrair_pagamentos_formatos_reais_do_fuctura():
    # formatos REAIS vistos no historico do aluno Miguel Tomaz (id 46431)
    comentarios = [
        {"id_acomp": "1", "data": "21/08/2025", "tipo": "-Pagamento Realizado", "titulo": "BOLETO PAGO 1/12",
         "texto": "Recebido - 21/08/2025\r\n\r\nR$ 377,00"},
        {"id_acomp": "2", "data": "28/08/2025", "tipo": "-Comentário", "titulo": "BAIXA CARTÃO",
         "texto": "Valor: R$ 95\r\ndata: 31/07"},  # tipo NAO e -Pagamento Realizado - nao deve entrar
        {"id_acomp": "3", "data": "27/01/2026", "tipo": "-Pagamento Realizado", "titulo": "BOLETO PAGO 6/12",
         "texto": "Recebido em 21/01/26\r\nR$ 377,00"},
        {"id_acomp": "4", "data": "24/04/2026", "tipo": "-Pagamento Realizado", "titulo": "BOLETO PAGO 7/12",
         "texto": "Valor recebido: R$ 377,00\r\nData - 23/02/2026"},
    ]
    pagamentos = eb.extrair_pagamentos_do_fuctura(comentarios)
    relatar(
        "extrair_pagamentos_do_fuctura: só pega comentários tipo '-Pagamento Realizado' (3 de 4)",
        len(pagamentos) == 3,
        f"pagamentos: {pagamentos}",
    )
    relatar(
        "extrair_pagamentos_do_fuctura: extrai valor e data corretos do formato 'Recebido - DD/MM/AAAA \\n R$ X'",
        pagamentos[0]["valor_extraido"] == 377.0 and pagamentos[0]["data_extraida"] == datetime(2025, 8, 21),
        f"pagamento[0]: {pagamentos[0]}",
    )
    relatar(
        "extrair_pagamentos_do_fuctura: extrai corretamente quando a data vem com ano de 2 dígitos ('21/01/26')",
        pagamentos[1]["data_extraida"] == datetime(2026, 1, 21),
        f"pagamento[1]: {pagamentos[1]}",
    )
    relatar(
        "extrair_pagamentos_do_fuctura: 'data_e_aproximada' é False quando achou data explícita no texto",
        pagamentos[0]["data_e_aproximada"] is False,
        "",
    )


def teste_extrair_pagamento_sem_data_no_texto_usa_data_do_comentario():
    comentarios = [
        {"id_acomp": "1", "data": "15/09/2026", "tipo": "-Pagamento Realizado", "titulo": "DIFERENÇA DE DESCONTO",
         "texto": "282,00 diferença de pagamento do primeiro mês"},
    ]
    pagamentos = eb.extrair_pagamentos_do_fuctura(comentarios)
    relatar(
        "sem data explícita no texto: usa a data do comentário como aproximação, marcando 'data_e_aproximada'",
        pagamentos[0]["data_extraida"] == datetime(2026, 9, 15) and pagamentos[0]["data_e_aproximada"] is True,
        f"pagamento: {pagamentos[0]}",
    )


def teste_confirmar_pagamento_bate_exato():
    extrato = [
        {"data": datetime(2025, 8, 21), "descricao": "PIX TRANSF HEROS M21/08", "valor": 377.0},
        {"data": datetime(2025, 8, 25), "descricao": "PIX TRANSF OUTRO", "valor": 500.0},
    ]
    resultado = eb.confirmar_pagamento_no_extrato(extrato, datetime(2025, 8, 21), 377.0)
    relatar(
        "confirmar_pagamento_no_extrato: confirma quando data e valor batem exatamente",
        resultado["confirmado"] is True and resultado["diferenca_dias"] == 0,
        f"resultado: {resultado}",
    )


def teste_confirmar_pagamento_dentro_da_tolerancia_de_dias():
    extrato = [{"data": datetime(2025, 8, 23), "descricao": "PIX TRANSF X", "valor": 377.0}]
    resultado = eb.confirmar_pagamento_no_extrato(extrato, datetime(2025, 8, 21), 377.0, tolerancia_dias=3)
    relatar(
        "confirmar_pagamento_no_extrato: confirma com pequena diferença de data (processamento bancário) dentro da tolerância",
        resultado["confirmado"] is True and resultado["diferenca_dias"] == 2,
        f"resultado: {resultado}",
    )


def teste_confirmar_pagamento_fora_da_tolerancia_nao_confirma():
    extrato = [{"data": datetime(2025, 9, 1), "descricao": "PIX TRANSF X", "valor": 377.0}]
    resultado = eb.confirmar_pagamento_no_extrato(extrato, datetime(2025, 8, 21), 377.0, tolerancia_dias=3)
    relatar(
        "confirmar_pagamento_no_extrato: NÃO confirma se a diferença de dias passa da tolerância, mesmo com valor igual",
        resultado["confirmado"] is False,
        f"resultado: {resultado}",
    )


def teste_confirmar_pagamento_valor_diferente_nao_confirma():
    extrato = [{"data": datetime(2025, 8, 21), "descricao": "PIX TRANSF X", "valor": 350.0}]
    resultado = eb.confirmar_pagamento_no_extrato(extrato, datetime(2025, 8, 21), 377.0)
    relatar(
        "confirmar_pagamento_no_extrato: NÃO confirma se o valor é diferente, mesmo com data exata (não casa por nome/proximidade só)",
        resultado["confirmado"] is False,
        f"resultado: {resultado}",
    )


def teste_confirmar_pagamento_escolhe_o_mais_proximo_em_data():
    extrato = [
        {"data": datetime(2025, 8, 19), "descricao": "PIX A", "valor": 377.0},
        {"data": datetime(2025, 8, 21), "descricao": "PIX B (o certo)", "valor": 377.0},
    ]
    resultado = eb.confirmar_pagamento_no_extrato(extrato, datetime(2025, 8, 21), 377.0)
    relatar(
        "confirmar_pagamento_no_extrato: com 2 candidatos de mesmo valor, escolhe o de data mais próxima",
        resultado["melhor_match"]["descricao"] == "PIX B (o certo)",
        f"resultado: {resultado}",
    )


def teste_auditar_pagamentos_aluno_integra_tudo():
    comentarios = [
        {"id_acomp": "1", "data": "21/08/2025", "tipo": "-Pagamento Realizado", "titulo": "BOLETO PAGO 1/12",
         "texto": "Recebido - 21/08/2025\r\n\r\nR$ 377,00"},
        {"id_acomp": "2", "data": "29/09/2025", "tipo": "-Pagamento Realizado", "titulo": "BOLETO PAGO 2/12",
         "texto": "Recebido - 18/09/2025\r\nValor: R$ 377,00"},  # este NAO existe no extrato de teste
    ]
    extrato = [{"data": datetime(2025, 8, 21), "descricao": "PIX TRANSF ALGUEM21/08", "valor": 377.0}]
    auditoria = eb.auditar_pagamentos_aluno(comentarios, extrato)
    relatar(
        "auditar_pagamentos_aluno: o pagamento que existe no extrato vem confirmado",
        auditoria[0]["confirmacao"]["confirmado"] is True,
        f"auditoria[0]: {auditoria[0]}",
    )
    relatar(
        "auditar_pagamentos_aluno: o pagamento que NÃO existe no extrato vem sinalizado como não confirmado (achado real - dá pra pegar erro/fraude)",
        auditoria[1]["confirmacao"]["confirmado"] is False,
        f"auditoria[1]: {auditoria[1]}",
    )


def main():
    print("Rodando testes de extrato_bancario.py (sem rede)...\n")
    teste_parsear_valor_brl()
    teste_parsear_data_brl()
    teste_parsear_csv_extrato_ignora_linhas_sem_valor()
    teste_extrair_pagamentos_formatos_reais_do_fuctura()
    teste_extrair_pagamento_sem_data_no_texto_usa_data_do_comentario()
    teste_confirmar_pagamento_bate_exato()
    teste_confirmar_pagamento_dentro_da_tolerancia_de_dias()
    teste_confirmar_pagamento_fora_da_tolerancia_nao_confirma()
    teste_confirmar_pagamento_valor_diferente_nao_confirma()
    teste_confirmar_pagamento_escolhe_o_mais_proximo_em_data()
    teste_auditar_pagamentos_aluno_integra_tudo()

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

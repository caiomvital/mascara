"""
Testes de relatorios_pdf.py - sem rede nenhuma: monta analises/resultado
FALSOS (mesmo formato que reconciliacao_devedor.analisar_aluno/
rodar_reconciliacao produzem) e confere o PDF gerado reabrindo com o
proprio PyMuPDF e extraindo o texto de verdade (nao so "nao quebrou").

Como rodar:
    cd testes_logica
    python test_relatorios_pdf.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

import relatorios_pdf as rp  # noqa: E402

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


def _texto_pdf(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texto = "\n".join(p.get_text() for p in doc)
    doc.close()
    return texto


def _analise_fake(nome, valor_final, caso="devedor_sem_turma", vencimento=None,
                   ultimo_pagamento=None, ultimo_contato=None, matricula="12345"):
    return {
        "id_aluno": "1", "matricula": matricula, "nome": nome, "valor_final": valor_final,
        "vencimento_mais_antigo": vencimento, "fonte_vencimento": "matricula" if vencimento else "sem_dado",
        "elegivel": True, "convenio": False, "cancelou": False, "esta_em_controle": False,
        "caso": caso, "acao_sugerida": None, "urgente": False,
        "dias_minimos": 365, "dias_maximos": 1825, "prescrita": False, "ja_com_advogado": False,
        "resumo_comentarios": "", "data_analise_anterior": None,
        "possivel_estorno_pendente": False, "referencia_estorno": None,
        "ultimo_pagamento": ultimo_pagamento, "ultimo_contato": ultimo_contato,
    }


def teste_formatacao_basica():
    relatar("fmt_moeda formata no padrão brasileiro (vírgula decimal, ponto de milhar)",
            rp.fmt_moeda(1234.5) == "R$ 1.234,50", f"resultado: {rp.fmt_moeda(1234.5)!r}")
    relatar("fmt_dias_em_atraso com None devolve travessão (sem vencimento conhecido)",
            rp.fmt_dias_em_atraso(None) == "—", "")
    relatar("fmt_dias_em_atraso converte pra aproximação de meses quando >= 30 dias",
            rp.fmt_dias_em_atraso(95) == "95 dia(s) (~3 mês(es))", f"resultado: {rp.fmt_dias_em_atraso(95)!r}")
    relatar("fmt_dias_em_atraso com poucos dias não inventa '~0 meses'",
            "mês" not in rp.fmt_dias_em_atraso(10), f"resultado: {rp.fmt_dias_em_atraso(10)!r}")
    relatar("situacao_curta traduz o caso técnico pra um rótulo legível",
            rp.situacao_curta("devedor_sem_turma") == "Devedor - precisa de ação", "")
    relatar("situacao_curta cai no próprio caso se não mapeado (nunca quebra por caso novo)",
            rp.situacao_curta("caso_inventado_novo") == "caso_inventado_novo", "")


def teste_pdf_devedores_conteudo():
    analises = [
        _analise_fake("MARIA DA SILVA", 500.0, caso="devedor_sem_turma",
                       vencimento=datetime(2025, 1, 10), ultimo_pagamento="05/03/2025", ultimo_contato="10/09/2026"),
        _analise_fake("JOÃO SEM DÍVIDA", 0.0, caso="consistente"),  # não deve aparecer (valor_final == 0)
        _analise_fake("PEDRO PRESCRITO", 200.0, caso="divida_prescrita", vencimento=datetime(2015, 1, 1)),
    ]
    pdf_bytes = rp.gerar_pdf_devedores(analises, data_execucao=datetime(2026, 9, 15))
    relatar("gerar_pdf_devedores produz bytes de PDF válido", pdf_bytes[:4] == b"%PDF", f"primeiros bytes: {pdf_bytes[:10]!r}")

    texto = _texto_pdf(pdf_bytes)
    relatar("PDF de devedores lista quem deve de fato", "MARIA DA SILVA" in texto, "")
    relatar("PDF de devedores NÃO lista quem tem valor_final = 0 (não é devedor de verdade)",
            "JOÃO SEM DÍVIDA" not in texto, "")
    relatar("PDF de devedores mostra o valor em aberto formatado", "R$ 500,00" in texto, "")
    relatar("PDF de devedores mostra a situação traduzida (não o código técnico cru)",
            "Devedor - precisa de ação" in texto and "devedor_sem_turma" not in texto, "")
    relatar("PDF de devedores mostra dívida prescrita também (item da lista completa)", "PEDRO PRESCRITO" in texto, "")


def teste_pdf_devedores_ordena_por_mais_antigo_primeiro():
    analises = [
        _analise_fake("RECENTE", 100.0, vencimento=datetime(2026, 8, 1)),
        _analise_fake("ANTIGO", 100.0, vencimento=datetime(2020, 1, 1)),
    ]
    pdf_bytes = rp.gerar_pdf_devedores(analises, data_execucao=datetime(2026, 9, 15))
    texto = _texto_pdf(pdf_bytes)
    relatar(
        "gerar_pdf_devedores ordena do vencimento mais antigo primeiro (prioridade igual à tela)",
        texto.index("ANTIGO") < texto.index("RECENTE"),
        f"posição ANTIGO={texto.find('ANTIGO')}, posição RECENTE={texto.find('RECENTE')}",
    )


def teste_pdf_fechamento_conteudo():
    resultado = {
        "total_analisados": 4,
        "resumo_por_caso": {"devedor_sem_turma": 2, "consistente": 1, "convenio_ok": 1},
        "acoes_pendentes": [],
        "analises": [
            _analise_fake("A", 300.0, caso="devedor_sem_turma"),
            _analise_fake("B", 450.0, caso="devedor_sem_turma"),
            _analise_fake("C", 0.0, caso="consistente"),
            _analise_fake("D", 0.0, caso="convenio_ok"),
        ],
    }
    pdf_bytes = rp.gerar_pdf_fechamento(resultado, periodo_label="Setembro/2026", data_execucao=datetime(2026, 9, 15))
    relatar("gerar_pdf_fechamento produz bytes de PDF válido", pdf_bytes[:4] == b"%PDF", "")
    texto = _texto_pdf(pdf_bytes)
    relatar("PDF de fechamento mostra o período no título", "Setembro/2026" in texto, "")
    relatar("PDF de fechamento soma o total em aberto corretamente (300 + 450 = 750)", "R$ 750,00" in texto, f"texto: {texto[:500]}")
    relatar("PDF de fechamento mostra a contagem de devedores (2)", "Devedores (valor em aberto > 0): 2" in texto, "")
    relatar("PDF de fechamento lista a distribuição por situação", "Em cobrança (turma de controle)" in texto, "")


def teste_pdf_individual_conteudo():
    analise = _analise_fake(
        "CARLOS TESTE", 750.0, caso="devedor_sem_turma", vencimento=datetime(2025, 6, 1),
        ultimo_pagamento="10/01/2025", ultimo_contato="01/09/2026", matricula="99887",
    )
    comentarios = [
        {"id_acomp": "1", "data": "01/06/2025", "titulo": "GERAR BOLETOS", "tipo": "-Comentário", "autor": "x", "texto": "13x de R$ 377"},
        {"id_acomp": "2", "data": "01/09/2026", "titulo": "Ocorrência de Contato", "tipo": "-Comentário", "autor": "x", "texto": "Não respondeu"},
    ]
    pdf_bytes = rp.gerar_pdf_individual(analise, comentarios, data_execucao=datetime(2026, 9, 15))
    relatar("gerar_pdf_individual produz bytes de PDF válido", pdf_bytes[:4] == b"%PDF", "")
    texto = _texto_pdf(pdf_bytes)
    relatar("PDF individual tem o nome do aluno no título", "CARLOS TESTE" in texto, "")
    relatar("PDF individual mostra a matrícula", "99887" in texto, "")
    relatar("PDF individual mostra o valor em aberto", "R$ 750,00" in texto, "")
    relatar("PDF individual lista os comentários do histórico completo (não resumido)",
            "GERAR BOLETOS" in texto and "Ocorrência de Contato" in texto, "")


def teste_pdf_individual_sem_comentarios_nao_quebra():
    analise = _analise_fake("SEM HISTORICO", 0.0, caso="nao_elegivel")
    pdf_bytes = rp.gerar_pdf_individual(analise, [], data_execucao=datetime(2026, 9, 15))
    relatar("gerar_pdf_individual sem nenhum comentário ainda produz PDF válido (não quebra com lista vazia)",
            pdf_bytes[:4] == b"%PDF", "")
    texto = _texto_pdf(pdf_bytes)
    relatar("PDF individual sem comentários avisa claramente, em vez de mostrar tabela vazia",
            "Nenhum comentário" in texto, "")


def teste_pdf_devedores_muitas_linhas_pagina_corretamente():
    """Achado esperado: motor de tabela precisa criar nova página quando o
    conteúdo não cabe - testa com volume real (200 devedores) que isso
    não trava nem corrompe o PDF."""
    analises = [_analise_fake(f"ALUNO {i:03d}", 100.0 + i, vencimento=datetime(2025, 1, 1)) for i in range(200)]
    pdf_bytes = rp.gerar_pdf_devedores(analises, data_execucao=datetime(2026, 9, 15))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    relatar("gerar_pdf_devedores com 200 linhas gera mais de 1 página (paginação funcionando)",
            doc.page_count > 1, f"page_count: {doc.page_count}")
    texto = "\n".join(p.get_text() for p in doc)
    doc.close()
    relatar("gerar_pdf_devedores com 200 linhas: primeiro e último aluno aparecem (nada se perde na paginação)",
            "ALUNO 000" in texto and "ALUNO 199" in texto, "")


def main():
    print("Rodando testes de relatorios_pdf.py (sem rede)...\n")
    teste_formatacao_basica()
    teste_pdf_devedores_conteudo()
    teste_pdf_devedores_ordena_por_mais_antigo_primeiro()
    teste_pdf_fechamento_conteudo()
    teste_pdf_individual_conteudo()
    teste_pdf_individual_sem_comentarios_nao_quebra()
    teste_pdf_devedores_muitas_linhas_pagina_corretamente()

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

"""
Testes de regressao pra gerador_certificado.py - sem rede nenhuma. Gera
PDFs de verdade (PyMuPDF puro) a partir dos templates reais em
certificados/ e confere bytes/propriedades básicas - não confere pixel
a pixel (isso foi feito visualmente durante o desenvolvimento, olhando
os PDFs renderizados).

Como rodar:
    cd testes_logica
    python test_gerador_certificado.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

import gerador_certificado as gc  # noqa: E402

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
    texto = doc[0].get_text()
    doc.close()
    return texto


def teste_gerar_certificado_java_produz_pdf_valido():
    pdf = gc.gerar_certificado_trilha_pdf("Ana Beatriz Souza", "java", datetime(2026, 9, 22))
    relatar(
        "gerar_certificado_trilha_pdf (java): produz bytes de PDF válido",
        pdf[:4] == b"%PDF",
        f"primeiros bytes: {pdf[:8]!r}",
    )


def teste_gerar_certificado_java_tem_nome_e_modulos_certos():
    pdf = gc.gerar_certificado_trilha_pdf("Ana Beatriz Souza", "java", datetime(2026, 9, 22))
    texto = _texto_pdf(pdf)
    relatar(
        "certificado java: tem o nome do aluno",
        "Ana Beatriz Souza" in texto,
        texto[:200],
    )
    relatar(
        "certificado java: menciona 'Academia Java' e a carga horária total (96h)",
        "Academia Java" in texto and "96h" in texto,
        texto[:200],
    )
    relatar(
        "certificado java: lista os 4 módulos de Java (não os de Python)",
        "Spring Boot" in texto and "Angular" in texto and "Django" not in texto,
        texto,
    )
    relatar(
        "certificado java: mostra a data de conclusão passada (setembro de 2026)",
        "Setembro de 2026" in texto,
        texto,
    )


def teste_gerar_certificado_python_tem_modulos_certos():
    pdf = gc.gerar_certificado_trilha_pdf("Bruno Carlos Lima", "python", datetime(2026, 9, 22))
    texto = _texto_pdf(pdf)
    relatar(
        "certificado python: tem o nome e menciona 'Academia Python'",
        "Bruno Carlos Lima" in texto and "Academia Python" in texto,
        texto[:200],
    )
    relatar(
        "certificado python: lista os módulos de Python (não os de Java) - 'Data Science' com C maiúsculo (typo do rascunho original corrigido)",
        "Django" in texto and "Data Science" in texto and "Spring Boot" not in texto,
        texto,
    )


def teste_gerar_certificado_trilha_invalida_levanta_erro():
    try:
        gc.gerar_certificado_trilha_pdf("Fulano", "linux", datetime(2026, 9, 22))
        relatar("gerar_certificado_trilha_pdf: trilha desconhecida levanta ValueError", False, "não levantou erro")
    except ValueError as e:
        relatar(
            "gerar_certificado_trilha_pdf: trilha desconhecida levanta ValueError",
            "linux" in str(e).lower() or "trilha" in str(e).lower(),
            str(e),
        )


def teste_gerar_certificado_sem_data_usa_hoje():
    pdf = gc.gerar_certificado_trilha_pdf("Carla Dias", "java", data_conclusao=None)
    texto = _texto_pdf(pdf)
    mes_ano_hoje = gc._mes_ano(None).capitalize()
    relatar(
        "gerar_certificado_trilha_pdf sem data_conclusao usa a data de hoje",
        mes_ano_hoje in texto,
        f"esperado {mes_ano_hoje!r} em: {texto}",
    )


def teste_gerar_certificado_nome_comprido_nao_quebra_nem_estoura():
    """Achado real durante o desenvolvimento (2026-09-22): sem ajuste de
    fonte, um nome comprido cortava na borda da página."""
    nome_comprido = "Maria Eduarda de Albuquerque Vasconcelos Nascimento Bezerra"
    pdf = gc.gerar_certificado_trilha_pdf(nome_comprido, "java", datetime(2026, 9, 22))
    texto = _texto_pdf(pdf)
    relatar(
        "nome comprido: continua aparecendo por inteiro no texto do PDF (fonte foi reduzida, não cortou)",
        nome_comprido in texto,
        texto,
    )


def teste_gerar_certificado_biblia3d_produz_pdf_valido():
    pdf = gc.gerar_certificado_biblia3d_pdf("Julia Ribeiro de Souza Leão", data_conclusao=datetime(2025, 8, 15))
    relatar(
        "gerar_certificado_biblia3d_pdf: produz bytes de PDF válido",
        pdf[:4] == b"%PDF",
        f"primeiros bytes: {pdf[:8]!r}",
    )
    texto = _texto_pdf(pdf)
    relatar(
        "certificado bíblia 3D: mostra o nome em maiúsculo (mesmo padrão do exemplo original)",
        "JULIA RIBEIRO DE SOUZA LEÃO" in texto,
        texto[:300],
    )
    relatar(
        "certificado bíblia 3D: mostra cidade + mês/ano de conclusão",
        "Recife, Agosto de 2025" in texto,
        texto,
    )
    # "Módulo I" faz parte da ARTE do template (imagem de fundo), não é
    # texto desenhado por cima - get_text() não alcança texto dentro de
    # imagem, então não dá pra conferir isso aqui (já conferido
    # visualmente durante o desenvolvimento).


def teste_gerar_certificado_biblia3d_cidade_customizada():
    pdf = gc.gerar_certificado_biblia3d_pdf("Pedro Silva", cidade="Olinda", data_conclusao=datetime(2025, 3, 1))
    texto = _texto_pdf(pdf)
    relatar(
        "certificado bíblia 3D: aceita cidade diferente de Recife",
        "Olinda, Março de 2025" in texto,
        texto,
    )


def teste_gerar_certificado_biblia3d_nome_comprido_nao_quebra():
    nome_comprido = "Maria Eduarda de Albuquerque Vasconcelos Nascimento Bezerra"
    pdf = gc.gerar_certificado_biblia3d_pdf(nome_comprido, data_conclusao=datetime(2025, 8, 15))
    texto = _texto_pdf(pdf)
    relatar(
        "bíblia 3D com nome comprido: continua aparecendo por inteiro (fonte reduzida)",
        nome_comprido.upper() in texto,
        texto,
    )


def teste_gerar_certificado_trilha_limpa_sufixo_administrativo_do_nome():
    """Caso real (2026-09-22, testando com aluno real de verdade): LUIS
    HENRIQUE ANDRADE DE MOURA BARBOSA tinha "(BOLETO)" cravado no nome do
    cadastro - vazava pro certificado antes da correção."""
    pdf = gc.gerar_certificado_trilha_pdf("LUIS HENRIQUE ANDRADE DE MOURA BARBOSA (BOLETO)", "python", datetime(2026, 9, 22))
    texto = _texto_pdf(pdf)
    relatar(
        'certificado: "(BOLETO)" não aparece no nome mostrado',
        "LUIS HENRIQUE ANDRADE DE MOURA BARBOSA" in texto and "(BOLETO)" not in texto,
        texto[:200],
    )


def teste_gerar_certificado_biblia3d_limpa_prefixo_asterisco_do_nome():
    """Caso real: "*JOAO CARLOS LINS DOS SANTOS" - o "*" não faz parte
    do nome de verdade da pessoa."""
    pdf = gc.gerar_certificado_biblia3d_pdf("*JOAO CARLOS LINS DOS SANTOS", data_conclusao=datetime(2026, 9, 22))
    texto = _texto_pdf(pdf)
    relatar(
        'certificado bíblia 3D: "*" não aparece no nome mostrado',
        "JOAO CARLOS LINS DOS SANTOS" in texto and "*JOAO" not in texto,
        texto[:200],
    )


def teste_elegivel_para_certificado_delega_pra_academia_progresso():
    """elegivel_para_certificado reaproveita eh_ex_aluno_de_verdade -
    confere só que a delegação funciona (a lógica em si já é testada a
    fundo em test_academia_progresso.py)."""
    turmas = [
        {"data": "01/2026", "nome": "J1 01/01 TER N"},
        {"data": "02/2026", "nome": "J2 01/02 TER N"},
        {"data": "03/2026", "nome": "JS3 01/03 TER N"},
        {"data": "04/2026", "nome": "JA4 01/04 TER N"},
    ]
    relatar(
        "elegivel_para_certificado: aluno que completou os 4 módulos de Java, sem marca, não Devedor -> True",
        gc.elegivel_para_certificado("ALUNO COMPLETO", "Ex-aluno", turmas, trilha="java") is True,
        "",
    )
    relatar(
        "elegivel_para_certificado: mesmo aluno, mas Devedor -> False (não gera certificado pra quem deve)",
        gc.elegivel_para_certificado("ALUNO COMPLETO", "Devedor", turmas, trilha="java") is False,
        "",
    )
    relatar(
        "elegivel_para_certificado: só com J1 (faltando os outros 3 módulos) -> False",
        gc.elegivel_para_certificado("ALUNO INCOMPLETO", "Ex-aluno", turmas[:1], trilha="java") is False,
        "",
    )


def main():
    print("Rodando testes de gerador_certificado.py (sem rede)...\n")
    teste_gerar_certificado_java_produz_pdf_valido()
    teste_gerar_certificado_java_tem_nome_e_modulos_certos()
    teste_gerar_certificado_python_tem_modulos_certos()
    teste_gerar_certificado_trilha_invalida_levanta_erro()
    teste_gerar_certificado_sem_data_usa_hoje()
    teste_gerar_certificado_nome_comprido_nao_quebra_nem_estoura()
    teste_gerar_certificado_biblia3d_produz_pdf_valido()
    teste_gerar_certificado_biblia3d_cidade_customizada()
    teste_gerar_certificado_biblia3d_nome_comprido_nao_quebra()
    teste_gerar_certificado_trilha_limpa_sufixo_administrativo_do_nome()
    teste_gerar_certificado_biblia3d_limpa_prefixo_asterisco_do_nome()
    teste_elegivel_para_certificado_delega_pra_academia_progresso()

    print(f"\n{'=' * 70}")
    print(f"Total OK: {_ok_count} | Total FALHOU: {len(_falhas)}")
    if _falhas:
        print("\nFalhas:")
        for nome, detalhe in _falhas:
            print(f"  - {nome}: {detalhe}")
        sys.exit(1)
    else:
        print("Nenhuma regressão detectada.")


if __name__ == "__main__":
    main()

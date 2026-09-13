"""
Divide um PDF de ata em uma imagem por pagina.

Descoberto na pratica: quando um PDF de varias paginas e mandado inteiro pra
IA de visao (mesmo com instrucao explicita no prompt pra tratar todas as
paginas como uma so leitura), o modelo as vezes "resume" o documento e trata
uma pagina como a "consolidada", descartando alunos que so aparecem em
paginas adicionais. A mesma solucao que resolveu isso entre ARQUIVOS
diferentes (processar cada um separado e mesclar no codigo) resolve aqui:
transforma cada pagina do PDF numa imagem e processa pagina por pagina, sem
depender do modelo decidir sozinho o que e "principal".

Usa PyMuPDF (fitz) - biblioteca pura (sem binario externo tipo poppler),
essencial pra empacotar como .exe com PyInstaller sem dependencia extra.
"""
import os

import fitz  # PyMuPDF


def dividir_pdf_em_imagens(caminho_pdf, pasta_destino, dpi=200):
    """Retorna lista de caminhos de imagem PNG, uma por pagina do PDF, na
    ordem original. Se o PDF tiver so 1 pagina, ainda assim converte pra
    imagem (padroniza o pipeline - so imagem daqui pra frente)."""
    nome_base = os.path.splitext(os.path.basename(caminho_pdf))[0]
    doc = fitz.open(caminho_pdf)
    zoom = dpi / 72
    matriz = fitz.Matrix(zoom, zoom)
    caminhos = []
    for i, pagina in enumerate(doc, start=1):
        pix = pagina.get_pixmap(matrix=matriz)
        caminho_imagem = os.path.join(pasta_destino, f"{nome_base}_pagina{i}.png")
        pix.save(caminho_imagem)
        caminhos.append(caminho_imagem)
    doc.close()
    return caminhos

"""
Geração de certificado em PDF (pedido do usuário, 2026-09-22) - o
certificado.php do próprio Fuctura está quebrado (ver STATUS.md), então
o sistema-máscara gera o dele, a partir dos templates PNG fornecidos
pelo usuário (pasta certificados/, prontos, com a arte/logo/assinatura já
desenhados) - só o texto dinâmico (nome, módulos, data) é desenhado por
cima, com PyMuPDF puro (mesmo padrão de relatorios_pdf.py - fontes
nativas "helv"/"hebo", sem lib externa de layout).

Coordenadas medidas visualmente nas imagens originais (2000x1414px, com
uma grade sobreposta pra conferência) e escaladas pra uma página A4
paisagem de verdade (842x595pt - mesma proporção, 1414/2000 ≈ 1/√2).

Só gera certificado pra quem realmente completou a trilha inteira -
reaproveita a mesma regra já construída e confirmada com o Diógenes em
academia_progresso.eh_ex_aluno_de_verdade (completou todos os módulos +
não está Devedor + sem marca de abandono/refazendo no nome agora) - ver
elegivel_para_certificado().
"""
import os
from datetime import datetime

import fitz

import fechamento_logic as logic

DIRETORIO_TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certificados")

TEMPLATE_JAVA_PYTHON = os.path.join(DIRETORIO_TEMPLATES, "FUC - CERTIFICADO01.png")
TEMPLATE_BIBLIA3D = os.path.join(DIRETORIO_TEMPLATES, "B3D CERTIFICADO 00.png")

LARGURA_IMG, ALTURA_IMG = 2000, 1414
LARGURA_PDF, ALTURA_PDF = 842, 595
ESCALA = LARGURA_PDF / LARGURA_IMG  # mesma proporção nas duas dimensões (1/√2, igual A4)

COR_TEXTO = (0.11, 0.11, 0.11)
COR_BRANCO = (1, 1, 1)
COR_BORDA_OURO = (246 / 255, 212 / 255, 88 / 255)

MESES = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]

# Nome de exibição + carga horária de cada módulo, na ORDEM da trilha
# (ver academia_progresso.SEQUENCIAS_ACADEMIA - mesmas chaves "java"/
# "python", mas aqui é só o texto bonito pro certificado, não tem como
# derivar isso da sigla J1/PY2 sozinho). "Data Science" (não "Sciente",
# typo do rascunho original) - corrigido aqui de propósito.
MODULOS_TRILHA = {
    "java": [
        ("Java Orientado à Objetos", 24),
        ("Banco de Dados com PostgreSQL", 24),
        ("Back-end com Spring Boot", 24),
        ("Front-end com Angular", 24),
    ],
    "python": [
        ("Python Orientado à Objetos", 24),
        ("Desenvolvimento Web com Django", 24),
        ("Data Science com IA", 24),
        ("Desenvolvimento e Agentes com IA", 24),
    ],
}
NOME_CURSO_TRILHA = {"java": "Academia Java", "python": "Academia Python"}


def _pt(x, y):
    return (x * ESCALA, y * ESCALA)


def _sz(tamanho_px):
    return tamanho_px * ESCALA


def _sx(x_px):
    return x_px * ESCALA


def _sy(y_px):
    return y_px * ESCALA


def _fontsize_que_cabe(texto, fontsize_desejado, largura_max_px, fontname="helv"):
    """Achado testando com nome comprido de verdade (2026-09-22): sem
    isso, um nome grande estourava a borda direita da página. Reduz a
    fonte proporcionalmente só o suficiente pra caber na largura
    disponível - nunca aumenta além do tamanho pedido."""
    largura = fitz.get_text_length(texto, fontname=fontname, fontsize=_sz(fontsize_desejado))
    largura_max_pt = _sx(largura_max_px)
    if largura <= largura_max_pt:
        return fontsize_desejado
    return fontsize_desejado * (largura_max_pt / largura)


def elegivel_para_certificado(nome_aluno, status_atual, turmas_atuais, trilha=None):
    """Mesma regra de eh_ex_aluno_de_verdade (academia_progresso) -
    completou todos os módulos da trilha, não é Devedor, sem marca de
    abandono/refazendo no nome agora. Reaproveitada aqui em vez de
    importar direto pra não criar dependência circular (app.py já
    importa os dois módulos separadamente)."""
    import academia_progresso as ap
    return ap.eh_ex_aluno_de_verdade(nome_aluno, status_atual, turmas_atuais, trilha=trilha)


def _mes_ano(data):
    data = data or datetime.now()
    return f"{MESES[data.month - 1]} de {data.year}"


def _nova_pagina(caminho_template):
    doc = fitz.open()
    pagina = doc.new_page(width=LARGURA_PDF, height=ALTURA_PDF)
    pagina.insert_image(fitz.Rect(0, 0, LARGURA_PDF, ALTURA_PDF), filename=caminho_template)
    return doc, pagina


def gerar_certificado_trilha_pdf(nome_aluno, trilha, data_conclusao=None):
    """trilha: 'java' ou 'python'. data_conclusao: datetime (usa hoje se
    None) - vira só "mês de ano", igual o texto original do template.
    Retorna bytes do PDF."""
    if trilha not in MODULOS_TRILHA:
        raise ValueError(f"Trilha desconhecida: {trilha!r} (use 'java' ou 'python')")

    # ACHADO REAL (2026-09-22, testando com aluno real de verdade): nome
    # do cadastro no Fuctura pode trazer sufixo administrativo tipo
    # "(BOLETO)" ou prefixo "*" - nunca faz parte do nome de verdade da
    # pessoa, mas vazava pro certificado (ex real: "LUIS HENRIQUE ANDRADE
    # DE MOURA BARBOSA (BOLETO)"). Reaproveita a mesma limpeza já usada
    # pro casamento de nome da ata (fechamento_logic.norm_nome_aluno),
    # só que preservando maiúsculas/acentos pra exibição.
    nome_aluno = logic.limpar_nome_para_exibicao(nome_aluno)

    doc, pagina = _nova_pagina(TEMPLATE_JAVA_PYTHON)

    total_horas = sum(h for _, h in MODULOS_TRILHA[trilha])
    pagina.insert_text(_pt(400, 395), "A Fuctura Tecnologia, realizadora do curso de",
                        fontsize=_sz(34), fontname="helv", color=COR_TEXTO)
    pagina.insert_text(_pt(400, 440), f"{NOME_CURSO_TRILHA[trilha]} com {total_horas}h, os módulos",
                        fontsize=_sz(34), fontname="helv", color=COR_TEXTO)

    y = 537
    for nome_modulo, horas in MODULOS_TRILHA[trilha]:
        pagina.insert_text(_pt(400, y), f"- {nome_modulo}, {horas}h",
                            fontsize=_sz(34), fontname="helv", color=COR_TEXTO)
        y += 53

    # "Confere o certificado de conclusão a" já vem no template - só o
    # nome é novo. Largura máxima: da margem esquerda (400) até perto da
    # borda direita da página, antes da decoração dourada (1900).
    tamanho_nome = _fontsize_que_cabe(nome_aluno, 60, 1900 - 400)
    pagina.insert_text(_pt(400, 897), nome_aluno, fontsize=_sz(tamanho_nome), fontname="helv", color=COR_TEXTO)

    # a data "Junho de 2026" já vem CRAVADA na imagem (não é campo em
    # branco) - pinta por cima com branco antes de escrever a data real
    # (pedido do usuário, 2026-09-22, confirmado fundo branco liso ali)
    # medido nos pixels de verdade do texto antigo ("Junho de 2026" vai de
    # y=1261 a y=1281, x=821 a x=1010) - a linha de cima (CNPJ) termina em
    # y=1255, entao o retangulo comeca so depois disso
    pagina.draw_rect(fitz.Rect(*_pt(750, 1257), *_pt(1150, 1288)), color=None, fill=COR_BRANCO)
    texto_data = _mes_ano(data_conclusao).capitalize()
    largura_data = fitz.get_text_length(texto_data, fontname="helv", fontsize=_sz(30))
    x_centro = _sx(915) - largura_data / 2
    pagina.insert_text((x_centro, _sy(1279)), texto_data,
                        fontsize=_sz(30), fontname="helv", color=COR_TEXTO)

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def gerar_certificado_biblia3d_pdf(nome_aluno, cidade="Recife", data_conclusao=None):
    """Template único disponível é o de "Módulo I" (pedido do usuário,
    2026-09-22) - mesmo depois de confirmar que existem 4 módulos de
    verdade (B3DM1-B3DM4, ver academia_progresso.SEQUENCIAS_ACADEMIA), a
    elegibilidade (ver elegivel_para_certificado, trilha="biblia3d") já
    exige completar os 4, só falta arte pros módulos II-IV quando o
    usuário mandar. Retorna bytes do PDF."""
    # ACHADO REAL (2026-09-22, testando com aluno real de verdade): nome
    # do cadastro no Fuctura pode trazer prefixo "*" ou sufixo tipo
    # "(BOLETO)" - ver mesmo achado em gerar_certificado_trilha_pdf (ex
    # real aqui: "*JOAO CARLOS LINS DOS SANTOS").
    nome_aluno = logic.limpar_nome_para_exibicao(nome_aluno)

    doc, pagina = _nova_pagina(TEMPLATE_BIBLIA3D)

    # caixa arredondada com o nome - o template em branco não tem essa
    # caixa desenhada (só o exemplo preenchido tinha), desenha aqui
    caixa = fitz.Rect(*_pt(390, 635), *_pt(1610, 695))
    pagina.draw_rect(caixa, color=COR_BORDA_OURO, fill=COR_BRANCO, width=1.5, radius=0.3)
    nome_maiusculo = nome_aluno.upper()
    tamanho_nome = _fontsize_que_cabe(nome_maiusculo, 40, 1610 - 390 - 80)
    largura_nome = fitz.get_text_length(nome_maiusculo, fontname="helv", fontsize=_sz(tamanho_nome))
    x_centro_nome = _sx(1000) - largura_nome / 2
    pagina.insert_text((x_centro_nome, _sy(678)), nome_maiusculo,
                        fontsize=_sz(tamanho_nome), fontname="helv", color=COR_TEXTO)

    texto_local_data = f"{cidade}, {_mes_ano(data_conclusao).capitalize()}"
    largura_local_data = fitz.get_text_length(texto_local_data, fontname="helv", fontsize=_sz(30))
    x_centro_data = _sx(1000) - largura_local_data / 2
    pagina.insert_text((x_centro_data, _sy(953)), texto_local_data,
                        fontsize=_sz(30), fontname="helv", color=COR_TEXTO)

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes

"""
Foto de aluno - funcionalidade nova do sistema-mascara, sem equivalente
no Fuctura (confirmado ao vivo em 2026-09-22: nem lista_alunos.php nem
detalhes_alunos.php tem campo de foto, upload de imagem ou <img> do
aluno - so icones estaticos da propria interface). Guarda localmente no
servidor, fora do Fuctura - so o id_aluno linka a pessoa certa, nada e
enviado pro Fuctura.

Sempre converte e comprime pra JPEG (redimensionado, lado maior <= 800px)
antes de salvar - pedido do usuario (2026-09-22, perguntou sobre custo de
guardar fotos no VPS): mantem o disco leve mesmo com muitas fotos (a foto
original de um celular pode ter alguns MB; comprimida fica na casa de
dezenas a ~150KB).
"""
import os
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

DIRETORIO_FOTOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fotos_alunos")
TAMANHO_MAXIMO_LADO = 800  # px
QUALIDADE_JPEG = 82
LIMITE_UPLOAD_BYTES = 8 * 1024 * 1024  # 8MB - generoso pra foto de celular, barra abuso


def _caminho(id_aluno):
    return os.path.join(DIRETORIO_FOTOS, f"{id_aluno}.jpg")


def tem_foto(id_aluno):
    return os.path.isfile(_caminho(id_aluno))


def ler_foto(id_aluno):
    """Bytes JPEG já salvos, ou None se esse aluno não tem foto."""
    caminho = _caminho(id_aluno)
    if not os.path.isfile(caminho):
        return None
    with open(caminho, "rb") as f:
        return f.read()


def salvar_foto(id_aluno, dados_brutos):
    """Recebe os bytes originais de uma imagem comum (JPEG/PNG/WEBP - HEIC
    de iPhone não é lido pelo Pillow sem plugin extra, por isso o formulário
    já avisa "JPEG ou PNG"), redimensiona (lado maior <= 800px, mantendo
    proporção) e salva sempre como JPEG comprimido. Levanta ValueError com
    mensagem pronta pra mostrar ao usuário em caso de arquivo inválido/
    grande demais - nunca deixa uma exceção crua do Pillow vazar."""
    if len(dados_brutos) > LIMITE_UPLOAD_BYTES:
        raise ValueError("Arquivo maior que 8MB - escolha uma foto menor.")
    try:
        imagem = Image.open(BytesIO(dados_brutos))
        imagem.load()
    except (UnidentifiedImageError, OSError):
        raise ValueError("Arquivo não é uma imagem reconhecível (use JPEG ou PNG).")
    # Foto de celular vem "deitada" de verdade nos pixels e a orientação
    # certa só fica na tag EXIF (achado real, 2026-09-23: usuário tirou
    # foto do próprio celular e ela apareceu de lado) - aplica a rotação
    # ANTES de converter/salvar, que descarta o EXIF.
    imagem = ImageOps.exif_transpose(imagem)
    imagem = imagem.convert("RGB")
    imagem.thumbnail((TAMANHO_MAXIMO_LADO, TAMANHO_MAXIMO_LADO))
    os.makedirs(DIRETORIO_FOTOS, exist_ok=True)
    imagem.save(_caminho(id_aluno), "JPEG", quality=QUALIDADE_JPEG, optimize=True)


def remover_foto(id_aluno):
    """True se removeu de verdade, False se esse aluno já não tinha foto."""
    caminho = _caminho(id_aluno)
    if os.path.isfile(caminho):
        os.remove(caminho)
        return True
    return False

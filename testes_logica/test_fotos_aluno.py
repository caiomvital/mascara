"""
Testes de regressao pra fotos_aluno.py - funcionalidade nova (sem
equivalente no Fuctura, confirmado ao vivo em 2026-09-22: nem
lista_alunos.php nem detalhes_alunos.php tem campo de foto). Sem rede
nenhuma - usa um diretorio temporario, nunca a pasta fotos_alunos/ real.

Como rodar:
    cd testes_logica
    python test_fotos_aluno.py
"""
import io
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import fotos_aluno as fa  # noqa: E402

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


# aponta o armazenamento pra um diretorio temporario - nunca toca na
# pasta fotos_alunos/ real (dados reais de aluno)
_tmp_dir = tempfile.mkdtemp(prefix="fotos_aluno_teste_")
fa.DIRETORIO_FOTOS = _tmp_dir


def _imagem_valida_bytes(largura=1200, altura=900, formato="JPEG"):
    buf = io.BytesIO()
    Image.new("RGB", (largura, altura), color=(120, 40, 200)).save(buf, formato)
    return buf.getvalue()


def teste_tem_foto_falso_quando_nunca_enviou():
    relatar(
        "tem_foto: aluno que nunca teve upload -> False",
        fa.tem_foto("999_nunca_teve") is False,
        "",
    )


def teste_salvar_e_ler_foto():
    fa.salvar_foto("100", _imagem_valida_bytes())
    relatar(
        "salvar_foto + tem_foto: depois de salvar, tem_foto vira True",
        fa.tem_foto("100") is True,
        "",
    )
    dados = fa.ler_foto("100")
    relatar(
        "ler_foto: devolve bytes de uma imagem JPEG válida (assinatura FF D8)",
        dados is not None and dados[:2] == b"\xff\xd8",
        f"primeiros bytes: {dados[:4] if dados else None}",
    )


def teste_salvar_foto_redimensiona_e_comprime():
    """Pedido do usuário (2026-09-22: perguntou sobre custo de guardar
    fotos no VPS) - a foto salva tem que ser bem menor que a original,
    mesmo entrando uma imagem grande (foto de celular)."""
    original = _imagem_valida_bytes(largura=4000, altura=3000)  # ~12MP, formato bem maior que o limite
    fa.salvar_foto("101", original)
    salva = fa.ler_foto("101")
    imagem_salva = Image.open(io.BytesIO(salva))
    relatar(
        f"salvar_foto: redimensiona pro lado máximo ({fa.TAMANHO_MAXIMO_LADO}px) mantendo a proporção",
        max(imagem_salva.size) <= fa.TAMANHO_MAXIMO_LADO
        and abs(imagem_salva.size[0] / imagem_salva.size[1] - 4000 / 3000) < 0.01,
        f"tamanho salvo: {imagem_salva.size}",
    )
    relatar(
        "salvar_foto: arquivo comprimido é bem menor que o original (mantém o disco leve)",
        len(salva) < len(original),
        f"original={len(original)} bytes, salvo={len(salva)} bytes",
    )


def teste_salvar_foto_respeita_orientacao_exif_do_celular():
    """Achado real (2026-09-23, usuário testou na prática): foto tirada no
    celular em pé vem gravada DEITADA nos pixels (paisagem) com a tag EXIF
    Orientation=6 dizendo "gire 90° pra exibir" - sem aplicar isso, a foto
    aparecia de lado no perfil."""
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotacionar 90° horário pra ficar em pé
    buf = io.BytesIO()
    Image.new("RGB", (1200, 800), color=(10, 200, 30)).save(buf, "JPEG", exif=exif)
    fa.salvar_foto("102", buf.getvalue())
    imagem_salva = Image.open(io.BytesIO(fa.ler_foto("102")))
    relatar(
        "salvar_foto: foto de celular com EXIF Orientation=6 sai em pé (altura > largura), não deitada",
        imagem_salva.size[1] > imagem_salva.size[0],
        f"tamanho salvo: {imagem_salva.size}",
    )


def teste_salvar_foto_arquivo_grande_demais_e_rejeitado():
    dados_falsos = b"x" * (fa.LIMITE_UPLOAD_BYTES + 1)
    try:
        fa.salvar_foto("102", dados_falsos)
        relatar("salvar_foto: arquivo maior que o limite é rejeitado com ValueError", False, "não levantou ValueError")
    except ValueError as e:
        relatar(
            "salvar_foto: arquivo maior que o limite é rejeitado com ValueError",
            "8MB" in str(e) or "maior" in str(e).lower(),
            str(e),
        )


def teste_salvar_foto_arquivo_nao_e_imagem_e_rejeitado():
    try:
        fa.salvar_foto("103", b"isso claramente nao e uma imagem de verdade")
        relatar("salvar_foto: arquivo que não é imagem é rejeitado com ValueError", False, "não levantou ValueError")
    except ValueError as e:
        relatar(
            "salvar_foto: arquivo que não é imagem é rejeitado com ValueError",
            "imagem" in str(e).lower(),
            str(e),
        )


def teste_remover_foto():
    fa.salvar_foto("104", _imagem_valida_bytes())
    relatar("remover_foto: True quando a foto existia e foi removida", fa.remover_foto("104") is True, "")
    relatar("remover_foto: tem_foto vira False depois de remover", fa.tem_foto("104") is False, "")
    relatar("remover_foto: False (não quebra) quando o aluno já não tinha foto", fa.remover_foto("104") is False, "")


def teste_ler_foto_none_quando_nao_existe():
    relatar(
        "ler_foto: devolve None (não quebra) pra aluno sem foto",
        fa.ler_foto("999_nunca_teve") is None,
        "",
    )


def teste_ids_diferentes_nao_se_misturam():
    fa.salvar_foto("200", _imagem_valida_bytes(formato="PNG"))  # PNG de entrada, mas sempre salva como JPEG
    fa.salvar_foto("201", _imagem_valida_bytes())
    relatar(
        "cada id_aluno tem seu próprio arquivo - remover um não afeta o outro",
        fa.tem_foto("200") is True and fa.tem_foto("201") is True,
        "",
    )
    fa.remover_foto("200")
    relatar(
        "depois de remover a foto do 200, a do 201 continua intacta",
        fa.tem_foto("200") is False and fa.tem_foto("201") is True,
        "",
    )


def main():
    print("Rodando testes de fotos_aluno.py (sem rede, diretório temporário)...\n")
    try:
        teste_tem_foto_falso_quando_nunca_enviou()
        teste_salvar_e_ler_foto()
        teste_salvar_foto_redimensiona_e_comprime()
        teste_salvar_foto_respeita_orientacao_exif_do_celular()
        teste_salvar_foto_arquivo_grande_demais_e_rejeitado()
        teste_salvar_foto_arquivo_nao_e_imagem_e_rejeitado()
        teste_remover_foto()
        teste_ler_foto_none_quando_nao_existe()
        teste_ids_diferentes_nao_se_misturam()
    finally:
        shutil.rmtree(_tmp_dir, ignore_errors=True)

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

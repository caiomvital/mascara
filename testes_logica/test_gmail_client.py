"""
Testes de gmail_client.py que dao pra rodar sem rede nenhuma e sem
credencial real: construcao da URL de autorizacao, montagem do MIME
(com/sem anexo) e o armazenamento local de conexao (usa um
TOKENS_PATH temporario, nunca o gmail_tokens.json de verdade).

Como rodar:
    cd testes_logica
    python test_gmail_client.py
"""
import base64
import email
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gmail_client  # noqa: E402

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


def teste_url_autorizacao_tem_os_campos_certos():
    url = gmail_client.montar_url_autorizacao("meu-client-id", "estado-123")
    relatar(
        "montar_url_autorizacao: aponta pro endpoint certo do Google",
        url.startswith(gmail_client.URL_AUTORIZACAO),
        f"url: {url}",
    )
    relatar(
        "montar_url_autorizacao: leva client_id, redirect_uri, escopo, state e prompt=consent",
        "client_id=meu-client-id" in url
        and "redirect_uri=" in url
        and "gmail.compose" in url
        and "state=estado-123" in url
        and "prompt=consent" in url,
        f"url: {url}",
    )
    relatar(
        "montar_url_autorizacao: NAO pede escopo de leitura da caixa de entrada (so gmail.compose)",
        "gmail.readonly" not in url and "gmail.modify" not in url and "mail.google.com" not in url,
        f"url: {url}",
    )


def teste_mime_sem_anexo():
    raw = gmail_client.montar_mime_base64url("", "Assunto de teste", "Corpo de teste, com acentuação.", [])
    bruto = base64.urlsafe_b64decode(raw.encode("ascii"))
    msg = email.message_from_bytes(bruto)
    relatar(
        "montar_mime_base64url sem anexo: assunto vai certo no header",
        msg["subject"] == "Assunto de teste",
        f"subject: {msg['subject']!r}",
    )
    relatar(
        "montar_mime_base64url sem anexo: nenhuma parte de anexo (só o corpo em texto)",
        not any(p.get_filename() for p in msg.walk()),
        "achou uma parte com filename mesmo sem anexo",
    )


def teste_mime_com_anexo():
    conteudo_pdf = b"%PDF-1.4 conteudo falso de teste"
    anexos = [{"nome_arquivo": "contrato_teste.pdf", "conteudo": conteudo_pdf, "tipo": "application/pdf"}]
    raw = gmail_client.montar_mime_base64url("", "Assunto", "Corpo", anexos)
    bruto = base64.urlsafe_b64decode(raw.encode("ascii"))
    msg = email.message_from_bytes(bruto)
    partes_anexo = [p for p in msg.walk() if p.get_filename() == "contrato_teste.pdf"]
    relatar(
        "montar_mime_base64url com anexo: a parte do anexo existe, com o nome de arquivo certo",
        len(partes_anexo) == 1,
        f"partes com filename: {[p.get_filename() for p in msg.walk()]}",
    )
    if partes_anexo:
        relatar(
            "montar_mime_base64url com anexo: o conteúdo do PDF sobrevive ida e volta (base64) sem corromper",
            partes_anexo[0].get_payload(decode=True) == conteudo_pdf,
            "conteúdo decodificado não bate com o original",
        )


def teste_mime_com_varios_anexos():
    anexos = [
        {"nome_arquivo": "contrato.pdf", "conteudo": b"a", "tipo": "application/pdf"},
        {"nome_arquivo": "ata_turma1.pdf", "conteudo": b"b", "tipo": "application/pdf"},
        {"nome_arquivo": "ata_turma2.pdf", "conteudo": b"c", "tipo": "application/pdf"},
    ]
    raw = gmail_client.montar_mime_base64url("", "Assunto", "Corpo", anexos)
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))
    nomes = sorted(p.get_filename() for p in msg.walk() if p.get_filename())
    relatar(
        "montar_mime_base64url: todos os anexos (contrato + várias atas) vão juntos, nenhum se perde",
        nomes == ["ata_turma1.pdf", "ata_turma2.pdf", "contrato.pdf"],
        f"nomes encontrados: {nomes}",
    )


def _com_tokens_temporarios(func):
    """Roda func() com TOKENS_PATH apontando pra um arquivo temporário -
    nunca toca no gmail_tokens.json de verdade (que teria refresh_token
    real de funcionário, se existir no servidor)."""
    original = gmail_client.TOKENS_PATH
    with tempfile.TemporaryDirectory() as tmp:
        gmail_client.TOKENS_PATH = os.path.join(tmp, "gmail_tokens_teste.json")
        try:
            func()
        finally:
            gmail_client.TOKENS_PATH = original


def teste_conexao_por_funcionario():
    def rodar():
        relatar(
            "conectado(): False antes de qualquer conexão salva",
            gmail_client.conectado("09467914492") is False,
            "",
        )
        gmail_client.salvar_conexao("09467914492", "refresh-token-fake", "caio@exemplo.com")
        relatar(
            "conectado(): True depois de salvar_conexao para aquele login",
            gmail_client.conectado("09467914492") is True,
            "",
        )
        relatar(
            "email_conectado(): devolve o email salvo",
            gmail_client.email_conectado("09467914492") == "caio@exemplo.com",
            f"email_conectado: {gmail_client.email_conectado('09467914492')!r}",
        )
        relatar(
            "conectado(): outro funcionário (login diferente) continua desconectado - tokens são por pessoa",
            gmail_client.conectado("outro-login") is False,
            "",
        )
        gmail_client.desconectar("09467914492")
        relatar(
            "desconectar(): remove a conexão salva",
            gmail_client.conectado("09467914492") is False,
            "",
        )

    _com_tokens_temporarios(rodar)


def teste_criar_rascunho_sem_conexao_da_erro_claro():
    def rodar():
        try:
            gmail_client._access_token_para("login-nunca-conectou", "cid", "csecret")
            ok = False
            detalhe = "não levantou GmailNaoConectadoError"
        except gmail_client.GmailNaoConectadoError:
            ok = True
            detalhe = ""
        relatar(
            "_access_token_para levanta GmailNaoConectadoError com mensagem clara pra quem nunca conectou",
            ok, detalhe,
        )

    _com_tokens_temporarios(rodar)


def main():
    print("Rodando testes de gmail_client.py (sem rede, sem credencial real)...\n")
    teste_url_autorizacao_tem_os_campos_certos()
    teste_mime_sem_anexo()
    teste_mime_com_anexo()
    teste_mime_com_varios_anexos()
    teste_conexao_por_funcionario()
    teste_criar_rascunho_sem_conexao_da_erro_claro()

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

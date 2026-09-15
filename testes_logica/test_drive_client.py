"""
Testes de drive_client.py que dao pra rodar sem rede nenhuma e sem
credencial real: construcao da URL de autorizacao e o armazenamento
local da conexao (usa um TOKEN_PATH temporario, nunca o
drive_token.json de verdade).

Diferente de gmail_client.py (conexao POR FUNCIONARIO): aqui e uma
conexao UNICA (de quem tem as pastas Contratos/Atas compartilhadas no
proprio Drive) - os testes de conexao refletem isso (sem parametro de
login).

Como rodar:
    cd testes_logica
    python test_drive_client.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drive_client  # noqa: E402
import google_oauth  # noqa: E402

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


def teste_url_autorizacao_pede_so_leitura():
    url = drive_client.montar_url_autorizacao("meu-client-id", "estado-abc")
    relatar(
        "montar_url_autorizacao: aponta pro endpoint certo do Google",
        url.startswith(google_oauth.URL_AUTORIZACAO),
        f"url: {url}",
    )
    relatar(
        "montar_url_autorizacao: pede drive.readonly (leitura), não modificação",
        "drive.readonly" in url and "drive.file" not in url,
        f"url: {url}",
    )
    relatar(
        "montar_url_autorizacao: leva client_id, state e prompt=consent (mesmo padrão do gmail_client)",
        "client_id=meu-client-id" in url and "state=estado-abc" in url and "prompt=consent" in url,
        f"url: {url}",
    )
    relatar(
        "montar_url_autorizacao: redirect_uri aponta pro callback do Drive, não do Gmail",
        "drive%2Fcallback" in url or "/api/drive/callback" in url,
        f"url: {url}",
    )


def _com_token_temporario(func):
    """Roda func() com TOKEN_PATH apontando pra um arquivo temporário -
    nunca toca no drive_token.json de verdade."""
    original = drive_client.TOKEN_PATH
    with tempfile.TemporaryDirectory() as tmp:
        drive_client.TOKEN_PATH = os.path.join(tmp, "drive_token_teste.json")
        try:
            func()
        finally:
            drive_client.TOKEN_PATH = original


def teste_conexao_unica_nao_por_funcionario():
    def rodar():
        relatar(
            "conectado(): False antes de qualquer conexão salva",
            drive_client.conectado() is False,
            "",
        )
        drive_client.salvar_conexao("refresh-fake", "caio@exemplo.com", autorizado_por_login="09467914492")
        relatar(
            "conectado(): True depois de salvar_conexao (sem precisar de login - conexão é única)",
            drive_client.conectado() is True,
            "",
        )
        relatar(
            "email_conectado(): devolve o email de quem autorizou",
            drive_client.email_conectado() == "caio@exemplo.com",
            f"email_conectado: {drive_client.email_conectado()!r}",
        )
        drive_client.desconectar()
        relatar(
            "desconectar(): remove a conexão salva",
            drive_client.conectado() is False,
            "",
        )

    _com_token_temporario(rodar)


def teste_operacoes_sem_conexao_dao_erro_claro():
    def rodar():
        for nome_op, chamada in [
            ("listar_pastas_por_nome", lambda: drive_client.listar_pastas_por_nome("cid", "csec", "Contratos")),
            ("listar_arquivos_da_pasta", lambda: drive_client.listar_arquivos_da_pasta("cid", "csec", "id-qualquer")),
            ("baixar_arquivo", lambda: drive_client.baixar_arquivo("cid", "csec", "id-qualquer")),
        ]:
            try:
                chamada()
                ok, detalhe = False, "não levantou DriveNaoConectadoError"
            except drive_client.DriveNaoConectadoError:
                ok, detalhe = True, ""
            relatar(f"{nome_op} sem conexão levanta DriveNaoConectadoError (nunca tenta chamar a API sem token)", ok, detalhe)

    _com_token_temporario(rodar)


def main():
    print("Rodando testes de drive_client.py (sem rede, sem credencial real)...\n")
    teste_url_autorizacao_pede_so_leitura()
    teste_conexao_unica_nao_por_funcionario()
    teste_operacoes_sem_conexao_dao_erro_claro()

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

"""
Cliente do Google Drive (OAuth 2.0 + REST puro, mesmo estilo de
gmail_client.py) - pra buscar contrato/ata de aluno em pastas
compartilhadas ("Contratos", "Atas") quando não estão no Fuctura nem em
atas_recebidas/ (pedido do usuário, 2026-09-15: "precisará também de
acesso ao drive pra puxar atas e contratos").

DIFERENTE do Gmail (uma conexão POR FUNCIONÁRIO, cada um autoriza a
própria conta): aqui é **uma conexão só, do administrador** (decisão do
usuário, 2026-09-15) - as pastas Contratos/Atas foram compartilhadas
com o Drive de uma pessoa específica (Caio), não com a conta de cada
funcionário. O sistema guarda essa ÚNICA conexão (drive_token.json,
sem chave por login) e todo mundo que cria um rascunho com anexo
reaproveita ela pra buscar no Drive - não precisa de acesso individual.

Setup necessário (acao no Google Cloud Console - mesmo projeto/
credencial ja usado pro Gmail, ver gmail_client.py; só precisa
ADICIONAR o escopo abaixo na Tela de Consentimento OAuth, não cria
credencial nova):
  1. Na Tela de Consentimento OAuth (mesmo projeto do Gmail), adicionar
     o escopo https://www.googleapis.com/auth/drive.readonly ("Ver e
     baixar todos os seus arquivos do Google Drive" - só leitura,
     nunca apaga/modifica nada)
  2. Na tela de Configurações do sistema-mascara (/admin), a pessoa
     dona do Drive com as pastas compartilhadas (Caio) clica em
     "Conectar o Drive" - só essa pessoa deveria fazer isso, não
     qualquer funcionário (a ação fica registrada em quem autorizou,
     mas é usada por todos depois)

ESTRUTURA PRONTA: conexão OAuth + funções genéricas de busca (por nome
de pasta, listagem, download) - a lógica de "qual arquivo é o contrato/
ata DESTE aluno" ainda depende de inspecionar a organização real das
pastas (nomes de arquivo, subpastas por turma/ano etc.) depois que a
conexão existir - fica pra próxima etapa, junto com quem tiver acesso
pra revisar a estrutura de verdade.
"""
import json
import os
import threading
import time

import requests

import google_oauth

URL_FILES = "https://www.googleapis.com/drive/v3/files"

# so leitura - nunca apaga, modifica ou cria nada no Drive de quem conectar.
ESCOPO = "https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/userinfo.email"

REDIRECT_URI = "https://sistema-mascara.69-169-102-111.sslip.io/api/drive/callback"

TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drive_token.json")
_lock = threading.RLock()


class DriveNaoConectadoError(Exception):
    pass


def _carregar_token():
    with _lock:
        if not os.path.exists(TOKEN_PATH):
            return None
        with open(TOKEN_PATH, encoding="utf-8") as f:
            return json.load(f)


def _salvar_token(dados):
    with _lock:
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)


def montar_url_autorizacao(client_id, state):
    return google_oauth.montar_url_autorizacao(client_id, REDIRECT_URI, ESCOPO, state)


def trocar_code_por_tokens(client_id, client_secret, code):
    return google_oauth.trocar_code_por_tokens(client_id, client_secret, code, REDIRECT_URI)


def descobrir_email(access_token):
    return google_oauth.descobrir_email(access_token)


def conectado():
    return _carregar_token() is not None


def email_conectado():
    dados = _carregar_token()
    return dados.get("email", "") if dados else ""


def salvar_conexao(refresh_token, email, autorizado_por_login):
    _salvar_token({
        "refresh_token": refresh_token, "email": email,
        "autorizado_por_login": autorizado_por_login, "conectado_em": time.time(),
    })


def desconectar():
    if os.path.exists(TOKEN_PATH):
        os.remove(TOKEN_PATH)


def _access_token(client_id, client_secret):
    dados = _carregar_token()
    if not dados:
        raise DriveNaoConectadoError("O Drive ainda não foi conectado (ver tela de Configurações).")
    return google_oauth.renovar_access_token(client_id, client_secret, dados["refresh_token"])


def listar_pastas_por_nome(client_id, client_secret, nome):
    """Acha pasta(s) com esse nome (inclusive as só COMPARTILHADAS com a
    conta conectada, não só as próprias) - retorna [{'id', 'nome'}, ...],
    pode ter mais de uma se o nome não for único (mesmo padrão de
    buscar_turma_por_nome no fuctura_client.py: quem chama decide o que
    fazer com ambiguidade, aqui só informa)."""
    access_token = _access_token(client_id, client_secret)
    query = f"name = '{nome}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    r = requests.get(
        URL_FILES,
        headers={"Authorization": f"Bearer {access_token}"},
        params={"q": query, "fields": "files(id,name)", "pageSize": 20},
        timeout=15,
    )
    r.raise_for_status()
    return [{"id": f["id"], "nome": f["name"]} for f in r.json().get("files", [])]


def listar_arquivos_da_pasta(client_id, client_secret, id_pasta):
    """Lista arquivos DIRETOS de uma pasta (não desce em subpastas) -
    retorna [{'id', 'nome', 'tipo_mime'}, ...]."""
    access_token = _access_token(client_id, client_secret)
    query = f"'{id_pasta}' in parents and trashed = false"
    arquivos = []
    page_token = None
    while True:
        params = {"q": query, "fields": "nextPageToken, files(id,name,mimeType)", "pageSize": 200}
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(URL_FILES, headers={"Authorization": f"Bearer {access_token}"}, params=params, timeout=15)
        r.raise_for_status()
        dados = r.json()
        arquivos.extend({"id": f["id"], "nome": f["name"], "tipo_mime": f["mimeType"]} for f in dados.get("files", []))
        page_token = dados.get("nextPageToken")
        if not page_token:
            break
    return arquivos


def baixar_arquivo(client_id, client_secret, id_arquivo):
    """Baixa o conteúdo bruto (bytes) de um arquivo - PDFs e outros
    binários direto; Google Docs/Sheets nativos precisariam de export
    (?alt=media não funciona neles), não tratado aqui de propósito até
    sabermos se as pastas têm algum desses (a estrutura real ainda não
    foi inspecionada)."""
    access_token = _access_token(client_id, client_secret)
    r = requests.get(
        f"{URL_FILES}/{id_arquivo}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"alt": "media"},
        timeout=30,
    )
    r.raise_for_status()
    return r.content

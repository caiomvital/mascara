"""
Cliente da API do Gmail (OAuth 2.0 + REST puro, sem lib oficial do
Google - mesmo estilo do resto do projeto, so `requests`) - cria
rascunho DE VERDADE (com anexo) na conta do proprio funcionario, sem o
sistema nunca ver a senha do Gmail dele.

Pedido do usuario (2026-09-15): a versao anterior (link tipo wa.me,
ver abrirNoGmail() em app.py) nao suporta anexo - limitacao do proprio
navegador/URL scheme, nao do nosso codigo. Pra anexo de verdade so
existe um jeito que PASSA pela autorizacao do Gmail (nao burla nada): a
API oficial, com cada funcionario autorizando o sistema uma vez via
OAuth (mesmo fluxo de "Entrar com o Google" que qualquer site usa) -
depois disso o sistema guarda so um refresh_token pra ele, nunca a
senha, e troca por um access_token novo a cada rascunho criado.

Setup necessario do lado do Diogenes/Caio (acao deles no Google Cloud
Console - nao automatizavel por aqui):
  1. console.cloud.google.com -> criar um projeto (ou usar um existente)
  2. Ativar a "Gmail API" (menu "APIs e serviços" > "Biblioteca")
  3. Configurar a "Tela de consentimento OAuth": tipo "Externo", escopo
     https://www.googleapis.com/auth/gmail.compose (cria/edita rascunho
     - NUNCA le a caixa de entrada) + escopo de email (pra mostrar qual
     conta esta conectada), e adicionar cada funcionario como "usuário
     de teste" (ate 100, sem precisar de verificacao do Google - o app
     fica em modo "Testes" pra sempre, sem problema pra uso interno)
  4. Criar uma credencial ("APIs e serviços" > "Credenciais" > "Criar
     credenciais" > "ID do cliente OAuth"), tipo "Aplicativo da Web",
     com REDIRECT_URI (abaixo) na lista de URIs de redirecionamento
     autorizados
  5. Colar o Client ID e o Client Secret na tela de Configurações do
     sistema-mascara (nunca direto no código/git - config.json já é
     ignorado pelo git, mesma convenção usada pra CORA)
"""
import base64
import json
import os
import threading
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlencode

import requests

URL_AUTORIZACAO = "https://accounts.google.com/o/oauth2/v2/auth"
URL_TOKEN = "https://oauth2.googleapis.com/token"
URL_DRAFTS = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
URL_USERINFO = "https://www.googleapis.com/oauth2/v2/userinfo"

# gmail.compose: so cria/edita/envia rascunho - NUNCA da acesso de
# leitura a caixa de entrada. userinfo.email: so pra mostrar ao
# funcionario qual conta esta conectada (evita confusao entre contas).
ESCOPO = "https://www.googleapis.com/auth/gmail.compose https://www.googleapis.com/auth/userinfo.email"

REDIRECT_URI = "https://sistema-mascara.69-169-102-111.sslip.io/api/gmail/callback"

TOKENS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gmail_tokens.json")
_lock = threading.RLock()


class GmailNaoConfiguradoError(Exception):
    pass


class GmailNaoConectadoError(Exception):
    pass


def _carregar_tokens():
    with _lock:
        if not os.path.exists(TOKENS_PATH):
            return {}
        with open(TOKENS_PATH, encoding="utf-8") as f:
            return json.load(f)


def _salvar_tokens(tokens):
    with _lock:
        with open(TOKENS_PATH, "w", encoding="utf-8") as f:
            json.dump(tokens, f, ensure_ascii=False, indent=2)


def montar_url_autorizacao(client_id, state):
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": ESCOPO,
        "access_type": "offline",
        # "consent" forca o Google a devolver refresh_token sempre -
        # sem isso, numa segunda autorizacao do mesmo funcionario o
        # Google as vezes omite o refresh_token (assume que o app ja
        # tem um salvo, o que nem sempre e verdade do nosso lado).
        "prompt": "consent",
        "state": state,
    }
    return f"{URL_AUTORIZACAO}?{urlencode(params)}"


def trocar_code_por_tokens(client_id, client_secret, code):
    r = requests.post(URL_TOKEN, data={
        "client_id": client_id, "client_secret": client_secret,
        "code": code, "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code",
    }, timeout=15)
    r.raise_for_status()
    return r.json()  # {access_token, refresh_token, expires_in, ...}


def descobrir_email(access_token):
    try:
        r = requests.get(URL_USERINFO, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        r.raise_for_status()
        return r.json().get("email", "")
    except requests.exceptions.RequestException:
        return ""


def _renovar_access_token(client_id, client_secret, refresh_token):
    r = requests.post(URL_TOKEN, data={
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token",
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def conectado(login):
    return login in _carregar_tokens()


def email_conectado(login):
    return _carregar_tokens().get(login, {}).get("email", "")


def salvar_conexao(login, refresh_token, email):
    tokens = _carregar_tokens()
    tokens[login] = {"refresh_token": refresh_token, "email": email, "conectado_em": time.time()}
    _salvar_tokens(tokens)


def desconectar(login):
    tokens = _carregar_tokens()
    if login in tokens:
        del tokens[login]
        _salvar_tokens(tokens)


def _access_token_para(login, client_id, client_secret):
    dados = _carregar_tokens().get(login)
    if not dados:
        raise GmailNaoConectadoError("Você ainda não conectou sua conta do Gmail.")
    return _renovar_access_token(client_id, client_secret, dados["refresh_token"])


def montar_mime_base64url(destinatario, assunto, corpo_texto, anexos):
    """anexos: lista de {'nome_arquivo', 'conteudo' (bytes), 'tipo' (ex:
    'application/pdf')} - mesmo formato que anexos_email.montar_anexos_aluno()
    já produz, reaproveitado direto sem reformatar."""
    msg = MIMEMultipart()
    if destinatario:
        msg["to"] = destinatario
    msg["subject"] = assunto
    msg.attach(MIMEText(corpo_texto, "plain", "utf-8"))
    for anexo in anexos or []:
        subtipo = (anexo.get("tipo") or "application/octet-stream").split("/")[-1]
        parte = MIMEApplication(anexo["conteudo"], _subtype=subtipo)
        parte.add_header("Content-Disposition", "attachment", filename=anexo["nome_arquivo"])
        msg.attach(parte)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def criar_rascunho(login, client_id, client_secret, destinatario, assunto, corpo_texto, anexos=None):
    """Cria um rascunho DE VERDADE (com anexo, se houver) na conta do
    funcionario - via API do Gmail, dentro da autorizacao OAuth que ele
    ja concedeu. Retorna o id do rascunho criado (o funcionario abre e
    confere no proprio Gmail antes de mandar - nada sai sozinho daqui)."""
    access_token = _access_token_para(login, client_id, client_secret)
    raw = montar_mime_base64url(destinatario, assunto, corpo_texto, anexos)
    r = requests.post(
        URL_DRAFTS,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"message": {"raw": raw}},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Gmail respondeu {r.status_code}: {r.text[:300]}")
    return r.json().get("id")

"""
Trocas de token OAuth 2.0 do lado do Google, genéricas pra qualquer
escopo/API (Gmail, Drive, etc.) - o endpoint de token e o fluxo de
autorizacao sao os MESMOS independente do que a credencial da acesso a.
Fatorado de gmail_client.py quando drive_client.py precisou do mesmo
mecanismo (2026-09-15, pedido do usuario: "precisará também de acesso
ao drive pra puxar atas e contratos").

Cada modulo especifico (gmail_client.py, drive_client.py) continua dono
do proprio armazenamento de token (arquivo separado, formato proprio -
por funcionario no caso do Gmail, uma conexao unica de admin no caso do
Drive) e das chamadas de API especificas de cada produto - aqui so fica
o que e identico pra qualquer escopo.
"""
from urllib.parse import urlencode

import requests

URL_AUTORIZACAO = "https://accounts.google.com/o/oauth2/v2/auth"
URL_TOKEN = "https://oauth2.googleapis.com/token"
URL_USERINFO = "https://www.googleapis.com/oauth2/v2/userinfo"


def montar_url_autorizacao(client_id, redirect_uri, escopo, state):
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": escopo,
        "access_type": "offline",
        # "consent" forca o Google a devolver refresh_token sempre -
        # sem isso, numa segunda autorizacao da mesma pessoa o Google
        # as vezes omite o refresh_token (assume que o app ja tem um
        # salvo, o que nem sempre e verdade do nosso lado).
        "prompt": "consent",
        "state": state,
    }
    return f"{URL_AUTORIZACAO}?{urlencode(params)}"


def trocar_code_por_tokens(client_id, client_secret, code, redirect_uri):
    r = requests.post(URL_TOKEN, data={
        "client_id": client_id, "client_secret": client_secret,
        "code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }, timeout=15)
    r.raise_for_status()
    return r.json()  # {access_token, refresh_token, expires_in, ...}


def renovar_access_token(client_id, client_secret, refresh_token):
    r = requests.post(URL_TOKEN, data={
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token",
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def descobrir_email(access_token):
    try:
        r = requests.get(URL_USERINFO, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        r.raise_for_status()
        return r.json().get("email", "")
    except requests.exceptions.RequestException:
        return ""

"""
Modo manutenção do sistema.

Um operador de manutenção (usuário/senha próprios do sistema, ver
OPERADORES_MANUTENCAO - não são logins do Fuctura, pra dar pra entrar
mesmo com o sistema em manutenção) pode ligar/desligar o modo manutenção.
Enquanto ativo:
  - todo request responde "em manutenção";
  - só o operador consegue entrar, e só pra acompanhar o status e desativar.

O estado fica em manutencao.json, na mesma pasta. Se o arquivo sumir ou
corromper, o sistema volta a operar normalmente (fail-open, de propósito:
manutenção travada seria pior que manutenção não aplicada).
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
from datetime import datetime

# --------------------------------------------------------------------------
# Operadores de manutenção: (usuario, hash_hex, salt_hex, iteracoes).
# A senha nunca fica aqui em texto - só o hash PBKDF2-HMAC-SHA256 com salt
# aleatório. Pra definir/trocar:  python definir_senha_manutencao.py
# Enquanto a tupla estiver vazia, não há operador (modo manutenção fica
# indisponível).
# --------------------------------------------------------------------------
_ITERACOES = 240_000

OPERADORES_MANUTENCAO = (
    ("caiomvital", "9dda3bade039a902f1ca14f8e275a21744d987291ddce614234360fda042b9f3", "70a589b0d758f5987e2b90a7d004f37f", _ITERACOES),
    # ("reserva", "<hash_hex>", "<salt_hex>", _ITERACOES),
)

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manutencao.json")
_lock = threading.RLock()

_DEFAULT = {"ativo": False, "quando": None, "por": None, "motivo": None}


def _hash_senha(senha, salt_hex, iteracoes):
    return hashlib.pbkdf2_hmac(
        "sha256", (senha or "").encode("utf-8"), bytes.fromhex(salt_hex), iteracoes
    ).hex()


def gerar_hash_senha(senha, iteracoes=_ITERACOES):
    """Usado por definir_senha_manutencao.py: devolve (salt_hex, hash_hex)
    pra uma senha nova (salt aleatório a cada chamada)."""
    salt_hex = secrets.token_hex(16)
    return salt_hex, _hash_senha(senha, salt_hex, iteracoes)


def eh_operador(usuario):
    """True se 'usuario' é um operador de manutenção configurado - checagem
    só pelo nome (pro roteamento depois do login), não valida senha."""
    return any(usuario == u for (u, _h, _s, _i) in OPERADORES_MANUTENCAO)


def verificar_operador(usuario, senha):
    """True se usuario+senha batem com um operador configurado. Comparação
    do hash em tempo constante."""
    for (u, h, s, it) in OPERADORES_MANUTENCAO:
        if usuario == u and hmac.compare_digest(_hash_senha(senha, s, it), h):
            return True
    return False


def carregar():
    with _lock:
        if not os.path.exists(_PATH):
            return dict(_DEFAULT)
        try:
            with open(_PATH, encoding="utf-8") as f:
                est = json.load(f)
        except (json.JSONDecodeError, OSError):
            return dict(_DEFAULT)
        for k, v in _DEFAULT.items():
            est.setdefault(k, v)
        return est


def em_manutencao():
    return bool(carregar().get("ativo"))


def ativar(por, motivo=""):
    with _lock:
        est = {
            "ativo": True,
            "quando": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "por": (por or "").strip() or "?",
            "motivo": (motivo or "").strip() or None,
        }
        _salvar(est)
        return est


def desativar(por):
    with _lock:
        est = {
            "ativo": False,
            "quando": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "por": (por or "").strip() or "?",
            "motivo": None,
        }
        _salvar(est)
        return est


def _salvar(est):
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(est, f, ensure_ascii=False, indent=2)

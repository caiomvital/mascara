"""
Configuracao local do sistema-mascara: quem sao os administradores, qual
provedor de IA esta ativo, chaves de API, e orcamento estimado de uso.

Tudo fica em config.json na mesma pasta - nunca e enviado pra lugar nenhum
alem das chamadas de API dos proprios provedores configurados.
"""
import json
import os
import threading

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    # login do Fuctura de quem pode acessar a tela de configuracoes.
    # adicione o login do Diogenes aqui quando tiver o dado.
    "admins": ["09467914492", "Diogenes"],
    "provider_ativo": None,  # "anthropic" | "openai" | "google" | "deepseek"
    "providers": {
        "anthropic": {"api_key": "", "modelo": "claude-sonnet-4-5", "orcamento_mensal_usd": 20.0},
        "openai": {"api_key": "", "modelo": "gpt-4o", "orcamento_mensal_usd": 20.0},
        "google": {"api_key": "", "modelo": "gemini-3.5-flash-lite", "orcamento_mensal_usd": 20.0},
        "deepseek": {"api_key": "", "modelo": "deepseek-chat", "orcamento_mensal_usd": 20.0},
    },
    # uso estimado, por mes (chave "AAAA-MM"), por provedor - preenchido pelo
    # proprio sistema a cada chamada, com base numa estimativa de custo por
    # imagem/token. Nao e o saldo real da conta - ver nota na tela de config.
    "uso_mensal_usd": {},
    # CORA (emissor de boletos) - estrutura pronta, esperando credenciais
    # reais (pedido do usuario, 2026-09-07). "configurado" fica False ate
    # alguem preencher client_id/client_secret aqui pela tela de admin -
    # ver cora_client.py pra onde essas credenciais sao usadas.
    "cora": {
        "configurado": False,
        "client_id": "", "client_secret": "", "certificado_path": "", "ambiente": "sandbox",
    },
}

_lock = threading.RLock()  # reentrante: carregar() pode chamar salvar() ainda segurando o lock


def carregar():
    with _lock:
        if not os.path.exists(CONFIG_PATH):
            salvar(DEFAULT_CONFIG)
            return json.loads(json.dumps(DEFAULT_CONFIG))
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        # garante que chaves novas de versoes futuras existam
        for k, v in DEFAULT_CONFIG.items():
            if k not in cfg:
                cfg[k] = v
        return cfg


def salvar(cfg):
    with _lock:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)


def _norm(s):
    import unicodedata
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().strip().lower()


def eh_admin(login, nome=""):
    """Admin pode ser cadastrado pelo login numerico do Fuctura OU pelo nome
    que aparece no menu do Fuctura (ex: 'Diogenes') - comparacao sem
    acento/maiuscula, e por trecho (basta o nome cadastrado estar contido no
    nome de exibicao, pra nao exigir digitar sobrenome completo certinho)."""
    cfg = carregar()
    admins = cfg.get("admins", [])
    if login in admins:
        return True
    nome_norm = _norm(nome)
    if not nome_norm:
        return False
    return any(_norm(a) and _norm(a) in nome_norm for a in admins)


def registrar_uso(provider, usd_estimado):
    from datetime import datetime
    cfg = carregar()
    chave_mes = datetime.now().strftime("%Y-%m")
    uso = cfg.setdefault("uso_mensal_usd", {})
    mes = uso.setdefault(chave_mes, {})
    mes[provider] = round(mes.get(provider, 0.0) + usd_estimado, 4)
    salvar(cfg)
    return mes[provider]


def uso_do_mes(provider):
    from datetime import datetime
    cfg = carregar()
    chave_mes = datetime.now().strftime("%Y-%m")
    return cfg.get("uso_mensal_usd", {}).get(chave_mes, {}).get(provider, 0.0)


def alerta_orcamento(provider):
    cfg = carregar()
    orcamento = cfg["providers"].get(provider, {}).get("orcamento_mensal_usd", 0)
    usado = uso_do_mes(provider)
    if orcamento <= 0:
        return None
    pct = usado / orcamento
    if pct >= 1.0:
        return f"Uso estimado de {provider} já passou do orçamento mensal (R${usado:.2f} de R${orcamento:.2f})."
    if pct >= 0.8:
        return f"Uso estimado de {provider} está em {pct*100:.0f}% do orçamento mensal (R${usado:.2f} de R${orcamento:.2f})."
    return None

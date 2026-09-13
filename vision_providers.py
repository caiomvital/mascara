"""
Camada de abstracao para leitura de ata via IA com visao.

O resto do sistema so chama extrair_ata(provider, caminhos_imagens, prompt) e
recebe texto de volta (JSON) - nao sabe nem precisa saber qual provedor esta
por tras. Trocar de provedor e so mudar a configuracao (config_store), nunca
mexer no fluxo de fechamento.

Precos por token sao ESTIMATIVAS fixas no codigo (ver PRECOS abaixo) - usadas
so pra calcular o "uso estimado" mostrado no aviso de orcamento. Nao refletem
o saldo real da conta; atualize os valores se os precos dos provedores mudarem.
"""
import base64
import mimetypes
import os

import requests

# USD por 1 milhao de tokens (entrada, saida) - valores aproximados, ajustar
# conforme tabela de precos oficial de cada provedor mudar.
PRECOS = {
    "anthropic": {"in": 3.0, "out": 15.0},
    "openai": {"in": 2.5, "out": 10.0},
    "google": {"in": 0.10, "out": 0.40},
    "deepseek": {"in": 0.27, "out": 1.10},
}


class VisionError(Exception):
    pass


def _imagem_para_base64(caminho):
    with open(caminho, "rb") as f:
        dados = f.read()
    mime = mimetypes.guess_type(caminho)[0] or "image/jpeg"
    return base64.b64encode(dados).decode("ascii"), mime


def estimar_custo_usd(provider, tokens_entrada, tokens_saida):
    preco = PRECOS.get(provider, {"in": 0, "out": 0})
    return (tokens_entrada / 1_000_000) * preco["in"] + (tokens_saida / 1_000_000) * preco["out"]


def completar_texto(provider, modelo, api_key, prompt):
    """Chamada de TEXTO PURO (sem imagem) - reaproveita os mesmos
    adaptadores de extrair_ata com a lista de imagens vazia, entao todo
    provedor ja configurado pra ler ata serve tambem pra resumir texto.
    Retorna (texto_resposta, tokens_entrada, tokens_saida)."""
    return extrair_ata(provider, modelo, api_key, [], prompt)


def extrair_ata(provider, modelo, api_key, caminhos_imagens, prompt):
    """Retorna (texto_resposta, tokens_entrada, tokens_saida)."""
    if not api_key:
        raise VisionError(f"Nenhuma chave de API configurada para {provider}. Configure em Configurações.")
    if provider == "anthropic":
        return _anthropic(modelo, api_key, caminhos_imagens, prompt)
    if provider == "openai":
        return _openai(modelo, api_key, caminhos_imagens, prompt)
    if provider == "google":
        return _google(modelo, api_key, caminhos_imagens, prompt)
    if provider == "deepseek":
        return _deepseek(modelo, api_key, caminhos_imagens, prompt)
    raise VisionError(f"Provedor desconhecido: {provider}")


def _anthropic(modelo, api_key, caminhos, prompt):
    conteudo = []
    for caminho in caminhos:
        ext = os.path.splitext(caminho)[1].lower()
        if ext == ".pdf":
            with open(caminho, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            conteudo.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}})
        else:
            b64, mime = _imagem_para_base64(caminho)
            conteudo.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
    conteudo.append({"type": "text", "text": prompt})

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": modelo, "max_tokens": 4096, "temperature": 0, "messages": [{"role": "user", "content": conteudo}]},
        timeout=120,
    )
    if r.status_code != 200:
        raise VisionError(f"Anthropic retornou erro {r.status_code}: {r.text[:300]}")
    data = r.json()
    texto = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    uso = data.get("usage", {})
    return texto, uso.get("input_tokens", 0), uso.get("output_tokens", 0)


def _openai(modelo, api_key, caminhos, prompt):
    conteudo = [{"type": "text", "text": prompt}]
    for caminho in caminhos:
        if os.path.splitext(caminho)[1].lower() == ".pdf":
            raise VisionError("OpenAI: envie a ata como imagem (PDF nativo não suportado neste adaptador). Converta antes de enviar.")
        b64, mime = _imagem_para_base64(caminho)
        conteudo.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
        json={"model": modelo, "messages": [{"role": "user", "content": conteudo}], "max_tokens": 4096, "temperature": 0},
        timeout=120,
    )
    if r.status_code != 200:
        raise VisionError(f"OpenAI retornou erro {r.status_code}: {r.text[:300]}")
    data = r.json()
    texto = data["choices"][0]["message"]["content"]
    uso = data.get("usage", {})
    return texto, uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0)


def _google(modelo, api_key, caminhos, prompt):
    partes = [{"text": prompt}]
    for caminho in caminhos:
        if os.path.splitext(caminho)[1].lower() == ".pdf":
            b64, mime = _imagem_para_base64(caminho)  # mimetypes cobre application/pdf tambem
            partes.append({"inline_data": {"mime_type": "application/pdf", "data": b64}})
        else:
            b64, mime = _imagem_para_base64(caminho)
            partes.append({"inline_data": {"mime_type": mime, "data": b64}})

    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
        params={"key": api_key},
        json={"contents": [{"parts": partes}], "generationConfig": {"temperature": 0}},
        timeout=120,
    )
    if r.status_code != 200:
        raise VisionError(f"Google retornou erro {r.status_code}: {r.text[:300]}")
    data = r.json()
    texto = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    uso = data.get("usageMetadata", {})
    return texto, uso.get("promptTokenCount", 0), uso.get("candidatesTokenCount", 0)


def _deepseek(modelo, api_key, caminhos, prompt):
    # AVISO: suporte a visao da API publica da DeepSeek nao esta confirmado
    # no momento em que isto foi escrito. Implementado no formato compativel
    # com OpenAI (que a DeepSeek usa para o restante da API) - testar antes
    # de confiar em producao; se a chamada falhar, use outro provedor.
    conteudo = [{"type": "text", "text": prompt}]
    for caminho in caminhos:
        if os.path.splitext(caminho)[1].lower() == ".pdf":
            raise VisionError("DeepSeek: envie a ata como imagem, não como PDF.")
        b64, mime = _imagem_para_base64(caminho)
        conteudo.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

    r = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
        json={"model": modelo, "messages": [{"role": "user", "content": conteudo}], "max_tokens": 4096, "temperature": 0},
        timeout=120,
    )
    if r.status_code != 200:
        raise VisionError(
            f"DeepSeek retornou erro {r.status_code}: {r.text[:300]} "
            "(suporte a imagem pode não estar disponível nesta API - considere outro provedor)"
        )
    data = r.json()
    texto = data["choices"][0]["message"]["content"]
    uso = data.get("usage", {})
    return texto, uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0)

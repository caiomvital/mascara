"""
Integracao com a CORA (emissor de boletos) - ESTRUTURA PRONTA, esperando
credenciais reais (pedido do usuario, 2026-09-07). Nada aqui faz uma
chamada de rede de verdade ainda - so define o formato que o resto do
sistema (tela de Reconciliação de Devedores) ja espera receber, pra quando
as credenciais chegarem so precisar preencher os metodos marcados com
"IMPLEMENTAR AQUI".

Motivacao (RASCUNHO_automacao_juridico.md, e pedido explicito do usuario):
o Fuctura mostra "Contratado - Recebido = Diferença" como um total unico,
que ja vimos ser pouco confiavel (ver memoria fuctura_devedor_filtro_
confiabilidade e os casos Vitor/Ana Luisa/Normando neste projeto) - a CORA
tem a lista REAL de boletos emitidos, com status individual (pago/aberto/
vencido), o que da um retrato bem mais preciso e auditavel do que o aluno
realmente deve, boleto por boleto, em vez de um total agregado que pode
estar errado.

Quando tivermos a documentacao real da API da CORA, os pontos a ajustar
sao: a URL base, o metodo de autenticacao (a CORA normalmente usa OAuth2
client_credentials + certificado mTLS, nao só client_id/secret simples -
os campos em config_store.py DEFAULT_CONFIG["cora"] sao um palpite
razoavel, ajustar conforme a doc real) e o formato de resposta de
listar_boletos_em_aberto().
"""
from datetime import datetime

import config_store


class CoraNaoConfiguradoError(Exception):
    """Levantado quando alguem tenta usar a CORA antes das credenciais
    serem preenchidas na tela de Configurações."""
    pass


def esta_configurado():
    cfg = config_store.carregar()
    cora_cfg = cfg.get("cora", {})
    return bool(cora_cfg.get("configurado") and cora_cfg.get("client_id") and cora_cfg.get("client_secret"))


class CoraClient:
    """Um client por chamada e suficiente por enquanto (sem estado de sessao
    tipo o FucturaClient) - a autenticacao real da CORA provavelmente e um
    token de curta duracao, obtido a cada uso ou cacheado com expiracao,
    mas isso so da pra desenhar direito com a documentacao real em mãos."""

    def __init__(self):
        cfg = config_store.carregar()
        self._cfg = cfg.get("cora", {})
        if not esta_configurado():
            raise CoraNaoConfiguradoError(
                "CORA ainda não foi configurado - preencha as credenciais na tela de Configurações."
            )

    def listar_boletos_em_aberto(self, identificador_aluno):
        """IMPLEMENTAR AQUI quando tivermos a documentação real da API da
        CORA. Formato de retorno ja definido pra bater com o que a tela de
        Reconciliação de Devedores espera (ver app.py, _boletos_cora_aluno):

        Retorna uma lista de dicts, um por boleto em aberto:
            {
                "id": "...",              # identificador do boleto na CORA
                "vencimento": "DD/MM/AAAA",
                "valor": 123.45,          # float, em reais
                "status": "aberto" | "vencido",
                "linha_digitavel": "...", # opcional, se a CORA devolver
            }

        identificador_aluno: ainda precisa ser decidido com o usuario -
        provavelmente CPF do aluno/responsável (o jeito mais comum de
        emissores de boleto identificarem o pagador), ja que a CORA nao
        conhece o id_aluno do Fuctura."""
        raise NotImplementedError(
            "CoraClient.listar_boletos_em_aberto ainda não foi implementado - "
            "falta a documentação real da API da CORA."
        )


def montar_resumo_boletos(nome_aluno, boletos):
    """Monta o resumo no formato pedido pelo usuario: 'ALUNO X tem Y
    boletos em aberto', mais a lista e o total. Separado de
    listar_boletos_em_aberto() pra poder ser testado sem precisar de
    credenciais/rede nenhuma - so recebe a lista ja pronta."""
    total = sum(b["valor"] for b in boletos)
    valor_fmt = f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if boletos:
        resumo_texto = f"{nome_aluno} tem {len(boletos)} boleto(s) em aberto, totalizando R$ {valor_fmt}."
    else:
        resumo_texto = f"{nome_aluno} não tem boletos em aberto na CORA."
    return {
        "aluno": nome_aluno,
        "quantidade": len(boletos),
        "boletos": sorted(boletos, key=lambda b: _data_ordenavel(b.get("vencimento"))),
        "total": round(total, 2),
        "resumo_texto": resumo_texto,
    }


def _data_ordenavel(data_str):
    try:
        return datetime.strptime(data_str or "", "%d/%m/%Y")
    except ValueError:
        return datetime.max

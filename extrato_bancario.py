"""
Confere se um pagamento REGISTRADO NO FUCTURA (comentário tipo
'-Pagamento Realizado', com data e valor escritos no texto) tem uma
transação de verdade no extrato bancário - direção confirmada pelo
usuário (2026-09-16, repassando resposta do Diógenes): "deve ser o
oposto" do que eu tinha sugerido - o comentário do Fuctura é quem
precisa ser CONFIRMADO contra o extrato (o extrato é a fonte de
verdade), não o contrário.

Nomes em transações PIX vêm TRUNCADOS pelo banco (ex: "PIX TRANSF
HEROS M13/10", visto num extrato real do Itaú compartilhado com o
usuário) - dois alunos podem ter o mesmo primeiro nome, então casar por
nome não é confiável. O casamento aqui é só por DATA + VALOR, com
tolerância de alguns dias (processamento bancário às vezes atrasa em
relação à data que o funcionário anotou no comentário).

Formato de entrada do extrato: lista de dicts {'data': datetime,
'valor': float, 'descricao': str} - agnóstico de onde veio.
parsear_csv_extrato() cobre a forma prática de hoje (exportar a
planilha/extrato como CSV) - a Drive/Sheets API real (ver
drive_client.py) pode alimentar isso no futuro sem mudar o resto.
"""
import csv
import io
import re
from datetime import datetime

import fechamento_logic as logic

_RE_VALOR = re.compile(r"r?\$?\s*([\d.]+,\d{2})", re.IGNORECASE)
_RE_DATA = re.compile(r"(\d{2}/\d{2}/\d{2,4})")


def parsear_valor_brl(valor_str):
    """'R$ 377,00' / '377,00' -> 377.0. None se não for um valor válido."""
    s = (valor_str or "").strip().replace("R$", "").replace("r$", "").strip()
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parsear_data_brl(data_str):
    """'21/08/2025' ou '21/08/25' -> datetime. None se inválida."""
    data_str = (data_str or "").strip()
    partes = data_str.split("/")
    if len(partes) == 3 and len(partes[2]) == 2:
        data_str = f"{partes[0]}/{partes[1]}/20{partes[2]}"
    try:
        return datetime.strptime(data_str, "%d/%m/%Y")
    except ValueError:
        return None


def parsear_csv_extrato(conteudo_csv, delimitador=";"):
    """conteudo_csv: texto (não bytes), 3 colunas por linha - data,
    descrição, valor (mesma ordem do extrato real já visto: 'DD/MM/AAAA',
    'PIX TRANSF ...', 'R$ 123,45'). Sem cabeçalho obrigatório - linhas
    que não têm data+valor válidos são simplesmente ignoradas, não
    quebram a leitura do resto do arquivo (extrato de banco de verdade
    tem linhas de saldo/separador de mês misturadas, sem valor nenhum -
    visto no extrato Itaú real compartilhado com o usuário)."""
    linhas = []
    leitor = csv.reader(io.StringIO(conteudo_csv), delimiter=delimitador)
    for row in leitor:
        if len(row) < 3:
            continue
        data = parsear_data_brl(row[0])
        valor = parsear_valor_brl(row[2])
        if data is None or valor is None:
            continue
        linhas.append({"data": data, "descricao": row[1].strip(), "valor": valor})
    return linhas


def confirmar_pagamento_no_extrato(extrato, data_pagamento, valor_pagamento, tolerancia_dias=3):
    """Retorna {'confirmado': bool, 'melhor_match': dict|None,
    'diferenca_dias': int|None}. "melhor_match" é a transação de MESMO
    VALOR (exato, até 1 centavo de tolerância por arredondamento) mais
    próxima em data, dentro de tolerancia_dias - NUNCA tenta casar por
    nome (ver docstring do módulo, nomes truncados não são confiáveis)."""
    if data_pagamento is None or valor_pagamento is None:
        return {"confirmado": False, "melhor_match": None, "diferenca_dias": None}

    candidatos = []
    for linha in extrato:
        if abs(linha["valor"] - valor_pagamento) > 0.01:
            continue
        diff = abs((linha["data"] - data_pagamento).days)
        if diff <= tolerancia_dias:
            candidatos.append((diff, linha))

    if not candidatos:
        return {"confirmado": False, "melhor_match": None, "diferenca_dias": None}

    candidatos.sort(key=lambda c: c[0])
    melhor_diff, melhor = candidatos[0]
    return {"confirmado": True, "melhor_match": melhor, "diferenca_dias": melhor_diff}


def extrair_pagamentos_do_fuctura(comentarios):
    """Filtra os comentários tipo '-Pagamento Realizado' e tenta extrair
    (data, valor) do TEXTO livre deles - o Fuctura não tem campo
    estruturado separado pra "data do pagamento"/"valor pago" (só o
    valorContratado, que só é usado na matrícula) - fica escrito livre
    no texto. Formatos reais já vistos (aluno Miguel Tomaz, id 46431):
    "R$ 377,00\\r\\nRecebido - 21/08/2025", "Valor: R$ 95\\r\\ndata: 31/07",
    "Recebido em 21/01/26\\r\\nR$ 377,00", "Valor recebido: R$ 377,00\\r\\nData - 23/02/2026".

    Retorna lista de {'id_acomp', 'data_comentario', 'valor_extraido',
    'data_extraida', 'data_e_aproximada', 'texto_original'} - só os que
    conseguiu extrair ALGUM valor em R$. Se não achar uma data explícita
    no texto, usa a DATA DO COMENTÁRIO como aproximação (marcada em
    'data_e_aproximada', menos confiável - o comentário pode ter sido
    gravado dias depois do pagamento de verdade)."""
    out = []
    for c in comentarios:
        if "pagamento realizado" not in logic.norm(c.get("tipo", "")):
            continue
        texto = c.get("texto") or ""
        m_valor = _RE_VALOR.search(texto)
        if not m_valor:
            continue
        valor = parsear_valor_brl(m_valor.group(1))

        data_extraida = None
        for data_bruta in _RE_DATA.findall(texto):
            d = parsear_data_brl(data_bruta)
            if d:
                data_extraida = d
                break

        out.append({
            "id_acomp": c.get("id_acomp"), "data_comentario": c.get("data"),
            "valor_extraido": valor,
            "data_extraida": data_extraida or parsear_data_brl(c.get("data")),
            "data_e_aproximada": data_extraida is None,
            "texto_original": texto,
        })
    return out


def auditar_pagamentos_aluno(comentarios, extrato, tolerancia_dias=3):
    """Junta extrair_pagamentos_do_fuctura + confirmar_pagamento_no_extrato:
    pra cada pagamento que o Fuctura diz que recebeu, confere se existe
    de verdade no extrato bancário. Retorna a mesma lista de
    extrair_pagamentos_do_fuctura, com um campo 'confirmacao' a mais em
    cada item."""
    pagamentos = extrair_pagamentos_do_fuctura(comentarios)
    for p in pagamentos:
        p["confirmacao"] = confirmar_pagamento_no_extrato(
            extrato, p["data_extraida"], p["valor_extraido"], tolerancia_dias,
        )
    return pagamentos

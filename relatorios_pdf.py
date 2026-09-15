"""
Geração de PDF "do zero" (capacidade NOVA - diferente de todo outro PDF
deste projeto, que sempre proxeia um endpoint que já devolve PDF pronto
do próprio Fuctura). Usa PyMuPDF puro (já é dependência, ver
pdf_utils.py), sem lib externa de layout/tabela.

Os 3 relatórios pedidos pelo Diógenes (item 9 da transcrição em áudio
organizada, repassada em 2026-09-14): Relatório de Devedores, Relatório
de Fechamento (resumo financeiro/quantitativo do período) e Relatório
Individual (dossiê de um devedor específico).

Duas decisões confirmadas com o usuário em 2026-09-15:
  1. O pedido original fala em "meses em atraso" - o Fuctura NÃO tem
     cronograma de parcelas estruturado (investigado antes; o próprio
     Diógenes rejeitou explicitamente a ideia de o sistema inferir
     isso: "O sistema deve comparar o que deveria pagar com o que
     pagou? Não."). Usa em vez disso "dias em atraso desde o
     vencimento mais antigo em aberto" (dado real, já calculado por
     reconciliacao_devedor.dias_em_aberto), com uma conversão
     aproximada "~X meses" do lado, deixado claro que é estimativa.
  2. O pedido (item 10) é explícito: o PDF deve ser CONSEQUÊNCIA de uma
     análise já feita (busca -> analisa -> apresenta -> gera PDF), não
     um botão solto e cego. Por isso estas funções SEMPRE recebem o
     resultado já pronto de reconciliacao_devedor.rodar_reconciliacao()
     / analisar_aluno() (o que a tela de Reconciliação de Devedores já
     roda) - nunca buscam dado nenhum sozinhas.
"""
from datetime import datetime

import fitz

LARGURA_A4, ALTURA_A4 = 595, 842
MARGEM = 40
LARGURA_UTIL = LARGURA_A4 - 2 * MARGEM

COR_TEXTO = (0.1, 0.1, 0.1)
COR_MUTED = (0.4, 0.4, 0.4)
COR_CABECALHO_FUNDO = (0.88, 0.88, 0.88)
COR_LINHA = (0.75, 0.75, 0.75)
COR_ALERTA = (0.6, 0.15, 0.1)

# Rótulo curto pra situação de cada caso - diferente de _TEXTO_CASO (em
# reconciliacao_devedor.py, frases completas pro comentário gravado no
# Fuctura), aqui precisa caber numa célula de tabela. Pedido do Diógenes
# menciona rótulos como "Em dia"/"1 mês em atraso"/"Inadimplência
# prolongada" - os nomes exatos ficam a critério do sistema (ele mesmo:
# "os nomes exatos dependem das regras de negócio de Fuctura").
SITUACAO_CURTA = {
    "nao_elegivel": "Dentro do prazo (ainda não elegível)",
    "divida_prescrita": "Dívida prescrita (>5 anos)",
    "convenio_ok": "Em dia (convênio/bolsa)",
    "devedor_sem_divida": "Inconsistência - sem dívida real",
    "cancelou_e_quitou": "Cancelado e quitado",
    "consistente": "Em cobrança (turma de controle)",
    "cancelou_mas_ainda_deve": "Cancelado, mas ainda deve",
    "devedor_sem_turma": "Devedor - precisa de ação",
    "advogado_sinalizado_sem_turma_atual": "Já sinalizado ao jurídico",
    "possivel_estorno_pendente": "Possível estorno (conferir)",
    "abaixo_do_piso_cobranca": "Abaixo do piso de cobrança",
}


def situacao_curta(caso):
    return SITUACAO_CURTA.get(caso, caso)


def fmt_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_dias_em_atraso(dias):
    """Dias em atraso + aproximação em meses, sempre deixando claro que
    é estimativa (nunca finge ser um cronograma real de parcelas) -
    versão por extenso, pro Relatório Individual (contexto de parágrafo,
    tem espaço sobrando)."""
    if dias is None:
        return "—"
    if dias < 0:
        return "—"
    meses_aprox = dias // 30
    if meses_aprox <= 0:
        return f"{dias} dia(s)"
    return f"{dias} dia(s) (~{meses_aprox} mês(es))"


def fmt_dias_em_atraso_compacto(dias):
    """Mesma informação de fmt_dias_em_atraso, formato curto pra caber
    numa célula de tabela (Relatório de Devedores)."""
    if dias is None or dias < 0:
        return "—"
    meses_aprox = dias // 30
    return f"{dias}d" if meses_aprox <= 0 else f"{dias}d (~{meses_aprox}m)"


def fmt_data(data_str):
    return data_str or "—"


class _EscritorPDF:
    """Motor mínimo de texto/tabela paginada em PDF, do zero - sem lib
    externa de layout. Cada relatório só monta título/parágrafos/tabela;
    paginação (nova página quando o conteúdo não cabe, repetindo o
    cabeçalho da tabela) fica toda aqui, uma vez só."""

    def __init__(self, titulo, subtitulo=""):
        self.doc = fitz.open()
        self.titulo = titulo
        self.subtitulo = subtitulo
        self.pagina = None
        self.y = 0
        self._tabela_em_andamento = None
        self._nova_pagina(primeira=True)

    def _nova_pagina(self, primeira=False):
        self.pagina = self.doc.new_page(width=LARGURA_A4, height=ALTURA_A4)
        self.y = MARGEM
        if primeira:
            self.pagina.insert_text((MARGEM, self.y + 16), self.titulo, fontsize=16, fontname="hebo", color=COR_TEXTO)
            self.y += 26
            if self.subtitulo:
                self.pagina.insert_text((MARGEM, self.y + 10), self.subtitulo, fontsize=9, fontname="helv", color=COR_MUTED)
                self.y += 18
            self.y += 10
        else:
            self.pagina.insert_text((MARGEM, self.y + 10), f"{self.titulo} (continuação)", fontsize=10, fontname="helv", color=COR_MUTED)
            self.y += 22
        if self._tabela_em_andamento:
            self._desenhar_cabecalho_tabela(*self._tabela_em_andamento)

    def _garantir_espaco(self, altura_necessaria):
        if self.y + altura_necessaria > ALTURA_A4 - MARGEM:
            self._nova_pagina()

    def paragrafo(self, texto, fontsize=10, negrito=False, cor=COR_TEXTO, espaco_depois=6):
        self._garantir_espaco(fontsize + espaco_depois)
        fontname = "hebo" if negrito else "helv"
        self.pagina.insert_text((MARGEM, self.y + fontsize), texto, fontsize=fontsize, fontname=fontname, color=cor)
        self.y += fontsize + espaco_depois

    def espaco(self, altura=10):
        self.y += altura

    def linha_horizontal(self):
        self._garantir_espaco(8)
        self.pagina.draw_line((MARGEM, self.y), (LARGURA_A4 - MARGEM, self.y), color=COR_LINHA, width=0.5)
        self.y += 8

    def _elidir(self, texto, largura_max, fontsize, fontname="helv"):
        texto = texto or ""
        if fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize) <= largura_max:
            return texto
        while texto and fitz.get_text_length(texto + "...", fontname=fontname, fontsize=fontsize) > largura_max:
            texto = texto[:-1]
        return (texto + "...") if texto else ""

    def _desenhar_cabecalho_tabela(self, colunas, larguras, fontsize):
        altura_linha = fontsize + 10
        self._garantir_espaco(altura_linha)
        x = MARGEM
        self.pagina.draw_rect(
            fitz.Rect(MARGEM, self.y, MARGEM + sum(larguras), self.y + altura_linha),
            color=None, fill=COR_CABECALHO_FUNDO,
        )
        for titulo_col, largura in zip(colunas, larguras):
            texto = self._elidir(titulo_col, largura - 6, fontsize, "hebo")
            self.pagina.insert_text((x + 3, self.y + altura_linha - 4), texto, fontsize=fontsize, fontname="hebo", color=COR_TEXTO)
            x += largura
        self.y += altura_linha

    def tabela(self, colunas, larguras, linhas, fontsize=8.5):
        """colunas: nomes das colunas. larguras: largura em pontos de
        cada coluna (soma deve bater com LARGURA_UTIL, não validado
        rigidamente). linhas: lista de listas de texto (1 linha por
        item, célula truncada com "..." se não couber - sem quebra de
        linha, mantém o motor simples e previsível)."""
        assert len(colunas) == len(larguras)
        self._tabela_em_andamento = (colunas, larguras, fontsize)
        self._desenhar_cabecalho_tabela(colunas, larguras, fontsize)
        altura_linha = fontsize + 8
        for i, linha in enumerate(linhas):
            self._garantir_espaco(altura_linha)
            if i % 2 == 1:
                self.pagina.draw_rect(
                    fitz.Rect(MARGEM, self.y, MARGEM + sum(larguras), self.y + altura_linha),
                    color=None, fill=(0.96, 0.96, 0.96),
                )
            x = MARGEM
            for valor, largura in zip(linha, larguras):
                texto = self._elidir(str(valor), largura - 6, fontsize)
                self.pagina.insert_text((x + 3, self.y + altura_linha - 3), texto, fontsize=fontsize, fontname="helv", color=COR_TEXTO)
                x += largura
            self.y += altura_linha
        self._tabela_em_andamento = None

    def finalizar(self):
        rodape_texto = f"Gerado pelo sistema-máscara Fuctura em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        for pagina in self.doc:
            pagina.insert_text(
                (MARGEM, ALTURA_A4 - 20), rodape_texto, fontsize=7, fontname="helv", color=COR_MUTED,
            )
        pdf_bytes = self.doc.tobytes()
        self.doc.close()
        return pdf_bytes


# ---------------------------------------------------------------------------
# 1. Relatório de Devedores - Devedor, Dias em atraso (~meses), Valor em
# aberto, Último pagamento, Último contato, Situação (item 9 do pedido).
# ---------------------------------------------------------------------------
def gerar_pdf_devedores(analises, data_execucao=None):
    """analises: lista de dicts vindos de reconciliacao_devedor.analisar_aluno
    (via rodar_reconciliacao()['analises']) - só entram os que devem de
    fato (valor_final > 0), ordenados do mais antigo pra o mais recente
    (mesma prioridade da tela de Reconciliação)."""
    data_execucao = data_execucao or datetime.now()
    import reconciliacao_devedor as rec

    devedores = [a for a in analises if a["valor_final"] > 0]
    devedores.sort(key=lambda a: a["vencimento_mais_antigo"] or datetime.max)

    escritor = _EscritorPDF(
        "Relatório de Devedores",
        f"{len(devedores)} devedor(es) - gerado em {data_execucao.strftime('%d/%m/%Y')}",
    )
    colunas = ["Matrícula", "Devedor", "Dias em atraso", "Valor em aberto", "Último pagto.", "Último contato", "Situação"]
    larguras = [45, 105, 65, 65, 55, 55, 125]
    linhas = []
    for a in devedores:
        dias = rec.dias_em_aberto(a["vencimento_mais_antigo"], data_execucao)
        linhas.append([
            a.get("matricula") or "—",
            a["nome"],
            fmt_dias_em_atraso_compacto(dias),
            fmt_moeda(a["valor_final"]),
            fmt_data(a.get("ultimo_pagamento")),
            fmt_data(a.get("ultimo_contato")),
            situacao_curta(a["caso"]),
        ])
    escritor.tabela(colunas, larguras, linhas)
    return escritor.finalizar()


# ---------------------------------------------------------------------------
# 2. Relatório de Fechamento - resumo financeiro e quantitativo do
# período (item 6/9 do pedido: totais + contagem por situação).
# ---------------------------------------------------------------------------
def gerar_pdf_fechamento(resultado_reconciliacao, periodo_label="", data_execucao=None):
    """resultado_reconciliacao: retorno de
    reconciliacao_devedor.rodar_reconciliacao() (tem 'analises' e
    'resumo_por_caso' já prontos - não recalcula nada aqui, só formata)."""
    data_execucao = data_execucao or datetime.now()
    analises = resultado_reconciliacao["analises"]

    total_contratado_recebido = [(a["valor_final"]) for a in analises]
    devedores_reais = [a for a in analises if a["valor_final"] > 0]
    total_em_aberto = sum(a["valor_final"] for a in devedores_reais)
    total_devedores = len(devedores_reais)
    total_analisados = len(analises)
    total_em_dia = total_analisados - total_devedores

    titulo = f"Relatório de Fechamento{' — ' + periodo_label if periodo_label else ''}"
    escritor = _EscritorPDF(titulo, f"Gerado em {data_execucao.strftime('%d/%m/%Y')}")

    escritor.paragrafo("Resumo financeiro", fontsize=12, negrito=True, espaco_depois=10)
    escritor.paragrafo(f"Total em aberto (soma da dívida de quem deve de fato): {fmt_moeda(total_em_aberto)}")
    escritor.paragrafo(f"Alunos analisados: {total_analisados}")
    escritor.paragrafo(f"Devedores (valor em aberto > 0): {total_devedores}")
    escritor.paragrafo(f"Sem dívida em aberto no momento: {total_em_dia}")
    escritor.espaco(8)
    escritor.linha_horizontal()

    escritor.paragrafo("Distribuição por situação", fontsize=12, negrito=True, espaco_depois=10)
    colunas = ["Situação", "Quantidade"]
    larguras = [400, 110]
    linhas = [
        [situacao_curta(caso), str(qtd)]
        for caso, qtd in sorted(resultado_reconciliacao["resumo_por_caso"].items(), key=lambda kv: -kv[1])
    ]
    escritor.tabela(colunas, larguras, linhas)

    escritor.espaco(14)
    escritor.paragrafo(
        "Nota: \"em aberto\" é a soma de Contratado - Recebido de quem tem esse valor positivo - o Fuctura não tem "
        "cronograma de parcelas estruturado, então isto não distingue meses específicos em atraso (ver Relatório de "
        "Devedores para dias em atraso por pessoa).",
        fontsize=8, cor=COR_MUTED, espaco_depois=4,
    )
    return escritor.finalizar()


# ---------------------------------------------------------------------------
# 3. Relatório Individual - histórico completo de um devedor específico
# (item 9 do pedido).
# ---------------------------------------------------------------------------
def gerar_pdf_individual(analise, comentarios, data_execucao=None):
    """analise: dict de analisar_aluno() pra ESTE aluno. comentarios:
    lista completa de comentários dele (client.comentarios_aluno) - já
    buscados por quem chama (a tela de Reconciliação/Consulta de Alunos
    já tem isso em mãos ao abrir o aluno, não busca de novo aqui)."""
    data_execucao = data_execucao or datetime.now()
    import reconciliacao_devedor as rec

    dias = rec.dias_em_aberto(analise["vencimento_mais_antigo"], data_execucao)
    escritor = _EscritorPDF(
        f"Relatório Individual — {analise['nome']}",
        f"Matrícula: {analise.get('matricula') or '—'} · Gerado em {data_execucao.strftime('%d/%m/%Y')}",
    )

    escritor.paragrafo("Situação atual", fontsize=12, negrito=True, espaco_depois=10)
    escritor.paragrafo(f"Situação: {situacao_curta(analise['caso'])}")
    escritor.paragrafo(f"Valor em aberto: {fmt_moeda(analise['valor_final'])}" if analise["valor_final"] > 0 else "Sem valor em aberto.")
    escritor.paragrafo(f"Dias em atraso: {fmt_dias_em_atraso(dias)}")
    escritor.paragrafo(f"Vencimento mais antigo em aberto: {analise['vencimento_mais_antigo'].strftime('%d/%m/%Y') if analise['vencimento_mais_antigo'] else '—'}")
    escritor.paragrafo(f"Último pagamento registrado: {fmt_data(analise.get('ultimo_pagamento'))}")
    escritor.paragrafo(f"Último contato registrado: {fmt_data(analise.get('ultimo_contato'))}")
    escritor.espaco(8)
    escritor.linha_horizontal()

    escritor.paragrafo("Histórico completo de comentários", fontsize=12, negrito=True, espaco_depois=10)
    if not comentarios:
        escritor.paragrafo("Nenhum comentário no histórico.", cor=COR_MUTED)
    colunas = ["Data", "Tipo", "Título", "Texto"]
    larguras = [55, 90, 130, 240]
    linhas = [[c["data"], c["tipo"], c["titulo"], c["texto"]] for c in comentarios]
    if linhas:
        escritor.tabela(colunas, larguras, linhas, fontsize=8)

    return escritor.finalizar()

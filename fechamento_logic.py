"""
Logica de negocio do Fechamento de Turma - separada do transporte HTTP e da
IA de visao de proposito, pra poder ser testada/ajustada sem mexer no resto.
"""
import difflib
import json
import re
import unicodedata
from datetime import datetime

PROMPT_EXTRACAO = """Você está lendo uma ATA DE CHAMADA de uma escola de tecnologia (Fuctura).
A tabela já vem IMPRESSA pelo sistema com nome, situação e celular de cada aluno -
copie esses dados exatamente como aparecem, não invente nem corrija.

O que é MANUSCRITO é: as marcações de presença (uma coluna por data de aula) e as assinaturas.
Regra de leitura das marcações, nesta ordem:
1. Célula em branco (sem nenhuma marca) = SEM REGISTRO (não é falta nem presença - a aula ainda
   não aconteceu ou não foi lançada). Marque "marcacao" como "sem_registro".
2. Letra "F" claramente escrita = FALTA.
3. Qualquer outra marca (ponto/círculo escuro preenchido, ou qualquer símbolo que não seja F
   e não esteja em branco) = PRESENÇA por padrão.
4. Só marque "online" se a marca for INEQUIVOCAMENTE a letra "O" (formato de letra bem definido,
   sem ambiguidade nenhuma com o ponto de presença). Na dúvida entre presença e online, marque
   como "presenca" - não existe categoria "incerto" para essa distinção especificamente, porque
   o que importa operacionalmente é distinguir falta de não-falta, e presença/online ambíguo
   sempre conta como presença.
A mesma folha de ata é reutilizada ao longo de várias aulas (uma coluna por data), e o campo
"Assinatura" costuma ser ÚNICO por aluno na folha, não um campo por data - não o use para
decidir a marcação de uma data especifica.

CONTAGEM DE COLUNAS (importante): antes de listar os alunos, determine N = quantas colunas de
marcação têm ALGUMA marca (de qualquer aluno, F/ponto/círculo/O). TODO aluno tem que ter
EXATAMENTE N entradas em "presencas_nesta_ata", uma por coluna, NA ORDEM das colunas (esquerda
para a direita). Se o topo da coluna tiver uma data legível, use-a no campo "data"; se NÃO
tiver data legível, use "aula_1", "aula_2", ... "aula_N" (sempre a mesma sequência para todos
os alunos). Nunca pule uma coluna nem varie a quantidade de entradas entre alunos - um aluno
com a linha inteira em branco numa coluna ainda tem que ter a entrada dessa coluna, com
marcacao "sem_registro". Devolva N no campo "colunas_de_marcacao".

Este documento pode ter mais de uma página (ex: um PDF com várias folhas). Trate TODAS as
páginas deste documento como parte da MESMA leitura - a folha é a mesma, só continua noutra
página, então as COLUNAS são as mesmas em todas as páginas: se a página 1 tem 6 colunas com
marca, a página 2 também tem 6 (mesmo que o cabeçalho de data não apareça na 2ª folha - use
"aula_1".."aula_6" na ordem). Um aluno pode aparecer em mais de uma página; junte as presenças
dele numa entrada só em "alunos", sem duplicar o aluno nem descartar nenhuma página.

Fora dessa distinção presença/online, se outra informação essencial for ilegível ou ausente
(por exemplo, não dá pra saber se é F ou está em branco), marque como "incerto" em vez de
adivinhar, e descreva o caso em "duvidas".

Nenhuma turma específica foi pré-definida para esta leitura - identifique a turma exatamente
como ela aparece escrita/impressa no documento, seja lá qual for. O exemplo de formato abaixo
usa valores fictícios genéricos só para mostrar a ESTRUTURA esperada do JSON - não são um
valor esperado nem uma turma de referência para comparar com o documento real.

Devolva SOMENTE um JSON válido, sem texto antes ou depois, neste formato exato:

{
  "turma_identificada": "<copie aqui o texto da turma exatamente como aparece no documento>",
  "professor": "<copie aqui o nome do professor exatamente como aparece no documento>",
  "datas_de_aula_nesta_ata": ["<cada data de aula encontrada, formato DD/MM/AAAA>"],
  "colunas_de_marcacao": <N - quantidade de colunas de marcação com alguma marca nesta folha>,
  "confianca_geral": "alta" | "media" | "baixa",
  "alunos": [
    {
      "nome": "<nome exatamente como impresso na ata>",
      "celular": "<como impresso>",
      "presencas_nesta_ata": [{"data": "<DD/MM/AAAA ou aula_1, aula_2...>", "marcacao": "presenca"|"falta"|"online"|"sem_registro"|"incerto"}]
    }
  ],
  "duvidas": ["descrição de qualquer campo essencial não identificado ou ambíguo"]
}
"""

MODULO_RE = re.compile(r"\b(J|PY|S|A)(\d)\b", re.IGNORECASE)
CURSO_POR_PREFIXO = {"J": "Java", "S": "Java", "A": "Java", "PY": "Python"}

PAGAMENTO_RE = re.compile(
    r"pagamento (via|realizado|confirmado|em|recorrente|feito) |boleto pago|"
    r"\bpix\b.{0,15}(recebid|confirmad)|recebid[oa].{0,15}pix|pr[eê]mio pago|"
    r"pagamento cart[aã]o|baixa boleto|boleto .{0,10}pago|pago pelo",
    re.IGNORECASE,
)
VALOR_RE = re.compile(r"r\$\s*[:\s]*([\d.,]+)", re.IGNORECASE)

# sinais em texto livre de que o aluno ja nao faz mais parte da turma - so
# pra CONTEXTO na revisao humana, nunca pra decidir/gravar nada sozinho (ver
# memoria fuctura_devedor_filtro_confiabilidade: campos/keywords do Fuctura
# mentem, tem que ler o comentario e deixar a pessoa decidir).
#
# Ampliado apos auditoria de comentarios reais (2026-09-04, reconciliacao de
# devedores) que achou varios casos de desistencia real com fraseado que a
# versao anterior nao pegava: "abandonou o curso", "aluno sumiu", "desistiu"
# sozinho (sem "do curso"), "cancelado" isolado. Ver memoria
# fuctura_mascara_formularios_padronizados - a fragilidade de depender de
# texto livre pra isso e um problema estrutural, nao so de regex.
CANCELAMENTO_RE = re.compile(
    r"\bcancelou\b|cancelamento|cancelad[oa]|n[aã]o vai (mais )?continuar|"
    r"n[aã]o quer mais vir|n[aã]o vai continuar|desistiu|\babandonou\b|\bsumiu\b",
    re.IGNORECASE,
)

# A mesma auditoria tambem achou falsos positivos reais do regex acima: (1)
# negacao explicita - "nao veio cancelar", "aluna nao vai mais cancelar" -
# que e o OPOSTO do que a palavra sozinha sugere; (2) cancelamento de algo
# FINANCEIRO (boleto/pagamento/parcela/plano/cartao cancelado ou trocado) -
# reemissao/ajuste administrativo do plano de pagamento, nao o aluno
# desistindo do curso. houve_sinal_cancelamento() filtra os dois casos.
_CANCELAMENTO_NEGACAO_RE = re.compile(
    r"n[aã]o\s+(?:veio\s+|vai\s+|foi\s+)*(?:mais\s+)?(?:cancelar|cancelou|desistir|desistiu)",
    re.IGNORECASE,
)
_CANCELAMENTO_FINANCEIRO_RE = re.compile(
    r"(boleto|pagamento|parcela|plano|recorrente|cart[aã]o|cobran[çc]a)\w*[^.]{0,25}cancelad|"
    r"cancelad[oa][^.]{0,25}(boleto|pagamento|parcela|plano|recorrente|cart[aã]o)|"
    r"cancelamento\s+d[eo]\s+(boleto|pagamento|parcela|plano|recorrente|cart[aã]o)|"
    r"troca\w*\s+d[ae]\s+forma\s+de\s+pagamento",
    re.IGNORECASE,
)
_CANCELAMENTO_SINAL_FORTE_RE = re.compile(r"\babandonou\b|\bsumiu\b|desistiu", re.IGNORECASE)


def houve_sinal_cancelamento(texto):
    """Versao mais cuidadosa de CANCELAMENTO_RE.search() - usar esta em vez
    do regex cru sempre que a decisao (ou uma acao sugerida) depender disso.
    Ainda e uma heuristica de texto livre, nao uma leitura garantida - so
    contexto pra revisao humana."""
    if not CANCELAMENTO_RE.search(texto):
        return False
    if _CANCELAMENTO_NEGACAO_RE.search(texto):
        return False
    if _CANCELAMENTO_FINANCEIRO_RE.search(texto) and not _CANCELAMENTO_SINAL_FORTE_RE.search(texto):
        return False
    return True


JA_COM_ADVOGADO_RE = re.compile(
    r"varela caon|\badvogad[ao]\b|adv natalia|\bnatalia\b|\bnathalia\b|michel monte|ag adv",
    re.IGNORECASE,
)


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def norm_nome_aluno(s):
    """Nomes de aluno no Fuctura costumam ter prefixo (.  -) e sufixo tipo
    '(BOLETO)', '(ADVOGADO)', '(LINK OK)' etc. - isso derruba a similaridade
    na hora de casar com o nome (limpo) extraido da ata, entao remove antes
    de comparar. Mesmo problema ja resolvido antes neste projeto para outra
    finalidade (analise de devedores) - reaplicado aqui."""
    s = re.sub(r"\(.*?\)", " ", s or "")
    s = re.sub(r"^[.\-*+\s]+", "", s)
    return norm(s)


def limpar_nome_para_exibicao(s):
    """Mesma limpeza de norm_nome_aluno (prefixo administrativo './-/*',
    sufixo tipo '(BOLETO)'/'(ADVOGADO)') mas SEM minusculizar/tirar acento
    - pra mostrar o nome pro próprio aluno (ex: certificado de conclusão),
    não pra comparar/casar. Achado real (2026-09-22, testando certificado
    com alunos reais): "LUIS HENRIQUE ... (BOLETO)" e "*JOAO CARLOS ..."
    vazavam a anotação administrativa pro documento formal do aluno."""
    s = re.sub(r"\(.*?\)", " ", s or "")
    s = re.sub(r"^[.\-*+\s]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


RANK_CONFIANCA = {"alta": 2, "media": 1, "baixa": 0}


def pior_confianca(a, b):
    """Ao combinar a leitura de varios arquivos, a confianca geral da ata
    combinada eh a pior confianca individual (senao um arquivo mal lido
    passaria despercebido)."""
    if not a:
        return b
    if not b:
        return a
    return a if RANK_CONFIANCA.get(a, 0) <= RANK_CONFIANCA.get(b, 0) else b


def parse_json_resposta(texto):
    """A IA pode devolver o JSON cercado de crases/markdown - limpa antes de parsear."""
    t = texto.strip()
    t = re.sub(r"^```(json)?", "", t.strip())
    t = re.sub(r"```$", "", t.strip())
    return json.loads(t)


def parte_do_curso(nome_turma):
    """J1/S1 -> (Java, 1); PY3 -> (Python, 3); etc. None se nao reconhecer."""
    m = MODULO_RE.search(nome_turma or "")
    if not m:
        return None
    prefixo, numero = m.group(1).upper(), m.group(2)
    curso = CURSO_POR_PREFIXO.get(prefixo)
    if not curso:
        return None
    return {"curso": curso, "modulo": int(numero)}


def _primeiro_nome(s):
    partes = norm_nome_aluno(s).split()
    return partes[0] if partes else ""


def casar_nome(nome_ata, roster):
    """Casa um nome extraido da ata com o roster real da turma (ja veem do
    proprio Fuctura, entao normalmente e igualdade quase exata).

    Sobrenomes brasileiros muito comuns (DA SILVA, DE OLIVEIRA, DOS SANTOS,
    RAMOS...) inflam a similaridade da string inteira mesmo entre pessoas bem
    diferentes, o que pode fazer alunos distintos serem incorretamente
    'casados' com o mesmo aluno do roster (perdendo gente da lista em vez de
    mostrar como nao-casado). Por isso exige tambem que o PRIMEIRO nome seja
    razoavelmente parecido antes de aceitar qualquer candidato - sobrenome
    sozinho nunca decide."""
    alvo = norm_nome_aluno(nome_ata)
    alvo_primeiro = _primeiro_nome(nome_ata)
    melhor, melhor_score = None, 0.0
    for aluno in roster:
        primeiro_candidato = _primeiro_nome(aluno["nome"])
        if difflib.SequenceMatcher(None, alvo_primeiro, primeiro_candidato).ratio() < 0.72:
            continue
        score = difflib.SequenceMatcher(None, alvo, norm_nome_aluno(aluno["nome"])).ratio()
        if score > melhor_score:
            melhor, melhor_score = aluno, score
    if melhor and melhor_score >= 0.82:
        return melhor, melhor_score
    return None, melhor_score


def _digitos_telefone(s):
    return re.sub(r"\D", "", s or "")


def casar_por_telefone(celular_ata, roster):
    """O celular vem IMPRESSO pelo sistema na ata (nao e manuscrito, ver
    PROMPT_EXTRACAO) - muito mais confiavel que nome pra casar com o roster,
    principalmente quando o nome tem OCR ruim ou sobrenomes comuns. Compara
    pelos ultimos 8 digitos (cobre variacao de DDI/DDD/9 na frente). So
    aceita se o numero apontar pra EXATAMENTE um aluno do roster - se for
    ambiguo ou vazio, deixa a decisao pro casar_nome (fuzzy por nome)."""
    digs_ata = _digitos_telefone(celular_ata)
    if len(digs_ata) < 8:
        return None
    chave = digs_ata[-8:]
    candidatos = [a for a in roster if _digitos_telefone(a.get("telefone", "")).endswith(chave)]
    if len(candidatos) == 1:
        return candidatos[0]
    return None


def normalizar_data(data_str, ano_padrao=None):
    """A IA pode devolver a mesma data de formas diferentes entre paginas/
    arquivos - sem ano ('29/08'), ano com 2 digitos ('29/08/26'), dia/mes sem
    zero a esquerda ('2/6/2026') etc. Se cada variante virar uma string
    diferente, a mesclagem por data (que usa a string como chave) trata a
    MESMA aula como datas diferentes e infla a contagem de aulas - entao
    canonicaliza sempre pro mesmo formato DD/MM/AAAA, com zero a esquerda e
    ano de 4 digitos, pra garantir que duas leituras da mesma data colidam na
    mesma chave nao importa como a IA formatou."""
    data_str = (data_str or "").strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$", data_str)
    if not m:
        return data_str  # deixa como esta - vai cair no tratamento de erro do parse_date
    dia, mes, ano = m.group(1), m.group(2), m.group(3)
    if not ano:
        ano = str(ano_padrao or datetime.now().year)
    elif len(ano) == 2:
        ano = "20" + ano
    return f"{int(dia):02d}/{int(mes):02d}/{ano}"


def data_valida(data_str):
    """True se a string ja esta no formato canonico DD/MM/AAAA (saida de
    normalizar_data pra uma data real). Uma data invalida/placeholder (ex:
    'sem_data', 'coluna_1', string vazia) NUNCA deve ser usada como chave de
    mesclagem por data - duas colunas reais e distintas de uma pagina sem
    cabecalho legivel podem virar o mesmo placeholder, e mesclar por essa
    chave apagaria uma marcacao real (aluno que veio em 2 aulas registrado
    como se tivesse vindo numa so)."""
    return bool(re.match(r"^\d{2}/\d{2}/\d{4}$", data_str or ""))


def mesclar_marcacao(atual, nova):
    """Combina a marcacao de uma mesma data quando ela aparece em mais de um
    arquivo/pagina da mesma ata (ex: aluno aparece nas duas folhas enviadas).
    Uma celula 'sem_registro' num arquivo nao deve apagar uma marcacao real
    lida no outro; um conflito real entre marcacoes diferentes vira 'incerto'
    em vez de escolher arbitrariamente uma das duas."""
    if atual == nova:
        return atual
    if atual == "sem_registro":
        return nova
    if nova == "sem_registro":
        return atual
    return "incerto"


def _norm_nome_turma(s):
    """Nome de turma no Fuctura as vezes vem com prefixo tipo '.' (ex:
    '.J1 26/05/26 TER N') dependendo de onde foi buscado, mas comentarios
    manuais escritos por humanos normalmente omitem esse prefixo - sem tirar
    isso antes de comparar, ja_tem_fechamento nao reconhece um fechamento
    manual ja feito e deixa duplicar."""
    return re.sub(r"^[.\-*+\s]+", "", norm(s))


# Pedido do usuario (2026-09-09): quando o funcionario edita o resumo
# academico/financeiro sugerido antes de gravar (em vez de aceitar como
# veio), o assunto grava com esse prefixo em vez de PREFIXO_TEXTO_ORIGINAL -
# fica visivel no proprio Fuctura, sem precisar abrir o comentario pra
# saber se o texto e exatamente o que a leitura da ata sugeriu ou se um
# humano mexeu nele antes de confirmar. Mesma convencao em
# reconciliacao_devedor.py (duplicada de proposito - modulos ja evitam
# import cruzado no nivel de topo, ver comentario la).
PREFIXO_TEXTO_ORIGINAL = "-IA- "
PREFIXO_TEXTO_EDITADO = "-MOD- "


def remover_prefixo_edicao(titulo):
    """Tira o prefixo -IA-/-MOD- (ver PREFIXO_TEXTO_ORIGINAL/EDITADO) antes
    de qualquer comparacao de titulo - esse marcador e sobre a FIDELIDADE do
    texto gravado (igual a sugestao ou editado por humano), nao deve
    interferir em nada que reconhece o titulo de um comentario de
    fechamento (ex: ja_tem_fechamento)."""
    titulo = titulo or ""
    for p in (PREFIXO_TEXTO_ORIGINAL, PREFIXO_TEXTO_EDITADO):
        if titulo.startswith(p):
            return titulo[len(p):]
    return titulo


def ja_tem_fechamento(comentarios, nome_turma):
    alvo = _norm_nome_turma(nome_turma)
    for c in comentarios:
        titulo = remover_prefixo_edicao(c["titulo"])
        if norm(titulo).startswith("fechamento") and alvo in _norm_nome_turma(titulo + " " + c["texto"]):
            return True
    return False


def _parse_data_segura(data_str):
    try:
        return datetime.strptime((data_str or "").strip(), "%d/%m/%Y")
    except ValueError:
        return None


# Pedido do usuario (2026-09-09), depois de conversar com Diogenes: o
# fechamento de turma infantil (Biblia 3D) e diferente do de adulto - hoje
# a diferenca exata ainda nao foi definida (nenhum dos dois tem nota/
# avaliacao na ata, so frequencia), mas o usuario quer o PONTO DE
# BIFURCACAO pronto desde ja, pra quando a diferenca real aparecer so
# mexer no lado infantil sem arriscar o lado adulto (ja testado, em uso).
# Nomes reais de turma observados ao vivo: "Academia Biblia 3D", "B3D M1"
# a "M4", "BL1 2025 8h30 Sab", "BL 2026 08h30 Sab 1" etc.
_TURMA_INFANTIL_RE = re.compile(r"b[ií]blia|^b3d\b|^bl\d*\b", re.IGNORECASE)


def eh_turma_infantil(nome_turma):
    """True se a turma pertence ao programa infantil (Biblia 3D). So
    reconhece pelo NOME da turma - nao ha outro sinal disponivel no
    Fuctura pra distinguir infantil de adulto."""
    return bool(_TURMA_INFANTIL_RE.search(norm(nome_turma or "")))


# Quantas das ultimas aulas contam como "aulas finais" pro alerta de
# abandono no fechamento (ver montar_resumo_academico -> alerta_faltas_finais).
AULAS_FINAIS_JANELA = 3


def montar_resumo_academico(presencas_agregadas):
    """presencas_agregadas: lista de dicts {data, marcacao} de todas as atas
    enviadas para este fechamento (podem ser varias fotos/sessoes).

    Data invalida/desconhecida (ex: ata sem cabecalho de data legivel numa
    pagina, so '16' truncado) e um problema DIFERENTE de marcacao ambigua
    (F ou branco?) - um aluno pode ter uma marcacao de presenca/falta bem
    lida numa coluna cuja DATA a IA nao conseguiu identificar. Tratar essas
    como "incerto" fazia a aula sumir da contagem (aluno que veio em varias
    aulas aparecendo como "0 aulas realizadas"). Agora so entra em
    "incertos" quem realmente tem a MARCACAO ambigua; data desconhecida com
    marcacao clara ainda conta pra presenca/falta/total, so fica de fora da
    ordenacao cronologica (nao da pra saber se foi uma das ultimas aulas)."""
    com_data = [p for p in presencas_agregadas if _parse_data_segura(p["data"]) is not None]
    sem_data = [p for p in presencas_agregadas if _parse_data_segura(p["data"]) is None]
    com_data = sorted(com_data, key=lambda p: _parse_data_segura(p["data"]))

    # celulas em branco (aula ainda nao lancada) nao contam como aula realizada
    lancadas = [p for p in com_data + sem_data if p["marcacao"] != "sem_registro"]
    total = len(lancadas)
    # "online" e subconjunto de presenca (bate com o exemplo da spec: 12 aulas,
    # 9 presencas, 3 faltas, 2 das presencas foram online -> 9+3=12).
    online = sum(1 for p in lancadas if p["marcacao"] == "online")
    presencas = sum(1 for p in lancadas if p["marcacao"] in ("presenca", "online"))
    faltas = sum(1 for p in lancadas if p["marcacao"] == "falta")
    incertos = sum(1 for p in lancadas if p["marcacao"] == "incerto")
    data_desconhecida = sum(1 for p in lancadas if p in sem_data)

    # Ordem das aulas pra "faltou as ultimas N" / "faltou as finais":
    # colunas COM data vem primeiro (ordenadas por data), depois as SEM
    # data na ordem em que foram lidas (append sequencial = ordem das
    # colunas da esquerda pra direita). Antes so as com data entravam
    # nessa conta - resultado: numa ata cujas colunas nao tinham data
    # legivel (ex: folha de continuacao sem cabecalho), NENHUM aluno pegava
    # o alerta de abandono mesmo faltando tudo. Agora as sem data tambem
    # contam pra ordem (achado real 2026-09-10, ata .JA4 25/07/26).
    lancadas_ordenadas = (
        [p for p in com_data if p["marcacao"] != "sem_registro"]
        + [p for p in sem_data if p["marcacao"] != "sem_registro"]
    )
    ultimas = lancadas_ordenadas[-2:] if len(lancadas_ordenadas) >= 2 else lancadas_ordenadas
    faltou_ultimas = all(p["marcacao"] == "falta" for p in ultimas) if ultimas else False

    # Sinal A MAIS pro fechamento (pedido do usuario, 2026-09-10): faltas
    # nas AULAS FINAIS. Diferente de "faltou a ultima aula" (2 aulas): aqui
    # e faltar o BLOCO final inteiro (AULAS_FINAIS_JANELA aulas), so quando
    # a turma teve pelo menos esse tanto de aula - padrao de abandono antes
    # do termino, que muda o que fazer no fechamento (nao emitir
    # certificado, conferir cancelamento/estorno).
    finais = lancadas_ordenadas[-AULAS_FINAIS_JANELA:] if len(lancadas_ordenadas) >= AULAS_FINAIS_JANELA else []
    faltas_finais = sum(1 for p in finais if p["marcacao"] == "falta")
    alerta_faltas_finais = bool(finais) and faltas_finais == AULAS_FINAIS_JANELA

    # Achado do usuario (2026-09-16, respondendo ao Diógenes): se o aluno
    # faltou a TODAS as aulas (presencas == 0), dizer "faltou as aulas
    # finais" por cima é redundante - faltar tudo já inclui as finais.
    # "faltou_tudo" vira o caso prioritario e o unico texto extra e sobre
    # possivel abandono, sem repetir "aulas finais" - alerta_faltas_finais
    # (mesma janela de sempre) so aparece como texto quando NAO faltou tudo
    # (ou seja, o aluno assistiu alguma aula mas abandonou perto do fim -
    # sinal genuinamente diferente, nao redundante).
    faltou_tudo = total > 0 and presencas == 0
    possivel_abandono = faltou_tudo or alerta_faltas_finais

    plural = lambda n: "" if n == 1 else "s"
    frase = f"{total} aula{plural(total)} realizada{plural(total)}, compareceu a {presencas} e faltou a {faltas}."
    if online:
        frase += f" Participou de {online} aula(s) online."
    if faltou_tudo:
        frase += " ⚠ Possível abandono — não compareceu a nenhuma aula."
    elif alerta_faltas_finais:
        frase += " ⚠ Possível abandono — faltou às últimas aulas seguidas."
    elif faltou_ultimas:
        n = len(ultimas)
        if n == 1:
            frase += " Faltou à última aula."
        else:
            frase += f" Faltou as últimas {n} aulas."
    if data_desconhecida:
        frase += f" ({data_desconhecida} dessas aulas com data não identificada na ata.)"
    if incertos:
        frase += f" ⚠ {incertos} marcação(ões) não puderam ser confirmadas com segurança na ata."

    return {
        "texto": frase, "total": total, "presencas": presencas, "faltas": faltas,
        "online": online, "incertos": incertos, "faltou_ultimas": faltou_ultimas,
        "faltou_tudo": faltou_tudo, "possivel_abandono": possivel_abandono,
        "alerta_faltas_finais": alerta_faltas_finais, "faltas_finais": faltas_finais,
        "janela_finais": AULAS_FINAIS_JANELA,
    }


def sugestao_academica(resumo):
    """Sugestao curta com base so no padrao de frequencia (sem dado financeiro).

    Achado do usuario (2026-09-16): esta funcao repetia o mesmo fato que
    o "texto" de montar_resumo_academico ja diz ("faltou todas as X aulas
    finais") - quando as duas aparecem juntas (ex: tabela do relatório de
    turma, uma coluna do lado da outra), lê redundante. Agora só dá a
    AÇÃO, sem repetir o fato que já está no resumo."""
    if resumo["total"] == 0:
        return ""
    if resumo.get("possivel_abandono"):
        return "Prioridade — conferir antes de fechar / emitir certificado."
    if resumo["faltou_ultimas"]:
        return "Entrar em contato — faltou às últimas aulas."
    if resumo["faltas"] > resumo["presencas"]:
        return "Acompanhar de perto — mais faltas do que presenças."
    if resumo["incertos"]:
        return "Conferir marcação na ata original — ficou incerta."
    return ""


def eh_situacao_ref(modalidade):
    """True se o campo de situação do roster (observacao de roster_turma)
    contem a marca "REF" (aluno refazendo o modulo). Pedido do usuario
    (2026-09-12): alunos REF com o alerta de faltas finais ligado NAO devem
    ser filtrados - continuam sinalizados normalmente, so com uma nota extra
    de contexto (ver nota_ref_faltas_finais). Ja "MONITOR" (ex-aluno
    ajudando o professor) nao precisa de tratamento nenhum."""
    return bool(re.search(r"\bREF\b", (modalidade or "").upper()))


def nota_ref_faltas_finais(modalidade):
    """Frase de contexto pra quando o alerta de faltas finais dispara E o
    aluno esta marcado REF no roster - complementa o alerta, nao substitui."""
    return (
        f"Atenção: aluno consta como \"{(modalidade or '').strip()}\" nesta turma — REF (refazendo "
        "o módulo). Conferir se as faltas são deste módulo antes de tratar como abandono novo."
    )


# Pedido do Diogenes via Caio (2026-09-16): a EQUIPE usa uma convencao
# informal no proprio NOME do aluno (nao um status oficial do Fuctura -
# a lista real de status nao tem "Abandono" nenhum, ver
# QUESTIONARIO_TRAMITES_FUCTURA.md): "." antes do nome = abandonou,
# "-" antes do nome = refazendo (redundante/complementar a marca REF do
# roster, ver eh_situacao_ref acima - usar OU como sinal suficiente).
# CONFIRMADO ao vivo (2026-09-16) que essa marca sozinha NAO e confiavel:
# 2 alunos reais com "." no nome tinham status "Devedor" e "Advogado" no
# cadastro, nenhum dos dois "Ex-aluno" - por isso a marca do nome nunca
# decide nada sozinha, so dispara as duas conferencias que o Diogenes
# pediu (status real + outra turma no mesmo mes/ano).
def eh_nome_marcado_abandono(nome):
    return (nome or "").strip().startswith(".")


def eh_nome_marcado_refazendo(nome):
    return (nome or "").strip().startswith("-")


def turmas_no_mesmo_mes_ano(turmas_atuais, turma_atual_nome, data_referencia):
    """turmas_atuais: lista de {'data','nome'} de perfil_aluno(). Retorna as
    turmas (exceto a que esta sendo fechada agora) cuja data de matricula
    cai no MESMO mes/ano de data_referencia (datetime) - sinal de que o
    aluno pode estar ativo em outro lugar, mesmo tendo abandonado esta
    turma especifica (2ª conferencia pedida pelo Diogenes pro "."). Usa a
    data da AULA MAIS RECENTE da ata como referencia (mes/ano em que o
    "abandono" foi observado), nao a data de hoje."""
    if not data_referencia:
        return []
    alvo_norm = norm(turma_atual_nome or "")
    encontradas = []
    for t in turmas_atuais or []:
        if norm(t.get("nome", "")) == alvo_norm:
            continue
        d = _parse_data_segura(t.get("data", ""))
        if d and d.month == data_referencia.month and d.year == data_referencia.year:
            encontradas.append(t)
    return encontradas


def nota_verificacao_abandono(nome_aluno, status_atual, turmas_no_mesmo_periodo):
    """Nota de conferência pro alerta de possível abandono no Fechamento -
    baseada no padrão real que o Diógenes gostou (comentário do aluno
    Miguel Tomaz, turma JA4, 15/09/2026: "Faltou todas as aulas Padrão de
    abandono antes do término (verique que é ex aluno) Revisa na ata") -
    só que automatizada: como perfil_aluno() e as turmas já foram
    buscados pelo Fechamento de qualquer forma, o sistema CONFERE de
    verdade em vez de só lembrar o funcionário de conferir."""
    status_norm = (status_atual or "").strip().lower()
    if status_norm == "ex-aluno":
        partes = ["Cadastro já está como Ex-aluno."]
    else:
        partes = [f"⚠ Cadastro está como \"{status_atual or '(não definido)'}\", não Ex-aluno — confira se deveria atualizar."]
    if turmas_no_mesmo_periodo:
        nomes = ", ".join(t["nome"] for t in turmas_no_mesmo_periodo)
        partes.append(f"⚠ Também está em outra turma no mesmo mês: {nomes} — abandono pode ser só desta turma.")
    partes.append("Revisar na ata antes de decidir.")
    return " ".join(partes)


# montar_resumo_academico_infantil/sugestao_academica_infantil: ponto de
# bifurcacao pro fechamento infantil (ver eh_turma_infantil). Por enquanto
# so repassam pra versao de adulto - nao ha diferenca real conhecida ainda
# (confirmado com o usuario: nenhuma ata, infantil ou adulta, tem nota/
# avaliacao, so frequencia) - mas existem como funcoes SEPARADAS de
# proposito, pra quando a diferenca aparecer so mexer aqui, sem risco pro
# fechamento de adulto (ja testado, em uso real).
def montar_resumo_academico_infantil(presencas_agregadas):
    return montar_resumo_academico(presencas_agregadas)


def sugestao_academica_infantil(resumo):
    return sugestao_academica(resumo)


def montar_relatorio_turma(turma_identificada, professor, alunos_processados, rotulo="Fechamento", infantil=False):
    """Visao geral da turma inteira, mostrada ANTES de entrar aluno por aluno.
    So usa o que ja veio da extracao da ata - nao depende de consultar cada
    aluno no Fuctura, entao fica rapido de mostrar assim que a ata e lida.

    infantil: ver eh_turma_infantil() - usa o par de funcoes _infantil pra
    montar resumo/sugestao (hoje identicas as de adulto, ponto de bifurcacao
    pronto pra quando a diferenca real for definida)."""
    montar_resumo = montar_resumo_academico_infantil if infantil else montar_resumo_academico
    montar_sugestao = sugestao_academica_infantil if infantil else sugestao_academica
    if infantil:
        rotulo = f"{rotulo} (Infantil)"

    # Pedido do Diógenes via Caio (2026-09-16): categoria rápida por aluno
    # (Refazendo/Abandono/Primeira vez, só pela marca do nome - mesmo
    # método manual que ele usa) + o indicador de "funil" da turma inteira
    # (quantos estão em primeira vez de verdade, avançando na sequência) -
    # turma com poucos "primeira vez" é sinal pra avaliar se vale abrir a
    # próxima turma do módulo seguinte (exemplo dele: J2 com 15
    # matriculados e só 3 prontos pra avançar não justifica sozinho abrir
    # JS3/JA4). So usa o nome (import local pra evitar ciclo de import,
    # ja que academia_progresso.py importa fechamento_logic).
    import academia_progresso as academia

    linhas_tabela = []
    n_precisa_atencao = 0
    for a in alunos_processados:
        resumo = montar_resumo(a["presencas"])
        sugestao = montar_sugestao(resumo)
        if sugestao:
            n_precisa_atencao += 1
        linhas_tabela.append({
            "nome": a["nome_fuctura"], "id_aluno": a["id_aluno"],
            "resumo": resumo["texto"], "sugestao": sugestao,
            "categoria": academia.categoria_nome(a["nome_fuctura"]),
        })

    n_aulas = 0
    for a in alunos_processados:
        n_aulas = max(n_aulas, sum(1 for p in a["presencas"] if p["marcacao"] != "sem_registro"))

    funil = academia.indicador_funil_turma([{"nome": a["nome_fuctura"]} for a in alunos_processados])

    observacoes = []
    if n_precisa_atencao:
        observacoes.append(f"{n_precisa_atencao} aluno(s) com sugestão de contato/atenção.")
    else:
        observacoes.append("Nenhum aluno com sinal de atenção nesta leitura da ata.")
    if funil["total"] and funil["proporcao_primeira_vez"] < academia.LIMIAR_FUNIL_BAIXO:
        observacoes.append(
            f"⚠ Só {funil['primeira_vez']} de {funil['total']} aluno(s) em primeira vez nesta turma "
            "— avalie se compensa abrir a próxima turma da sequência."
        )

    return {
        "titulo": f"{rotulo} de Turma {turma_identificada}",
        "alunos": len(alunos_processados),
        "professor": professor or "não identificado na ata",
        "quantidade_aulas": n_aulas,
        "tabela": linhas_tabela,
        "observacoes": " ".join(observacoes),
        "funil": funil,
    }


def checagem_financeira(perfil, comentarios):
    """Reconstroi pagamentos reais a partir dos comentarios (Tipo=-Pagamento
    Realizado como sinal primario) e aplica as regras da secao 6-7 da spec."""
    pagamentos = []
    vistos = set()
    for c in comentarios:
        tipo_n = norm(c["tipo"])
        texto_full = c["titulo"] + " " + c["texto"]
        e_pagamento = "pagamento realizado" in tipo_n or bool(PAGAMENTO_RE.search(texto_full))
        if not e_pagamento:
            continue
        m = VALOR_RE.search(texto_full)
        if not m:
            continue
        try:
            valor = float(m.group(1).replace(".", "").replace(",", "."))
        except ValueError:
            continue
        chave = (c["data"], round(valor, 2))
        if chave in vistos:
            continue
        vistos.add(chave)
        pagamentos.append({
            "data": c["data"], "valor": valor, "comentario": c["titulo"],
            "autor": (c.get("autor") or "").strip(),
            "via_pix": "pix" in norm(texto_full),
        })

    # duplicidade: mesmo valor em datas muito proximas (<=2 dias) pode ser o
    # mesmo pagamento lancado duas vezes com data levemente diferente.
    duplicados = []
    for i, p in enumerate(pagamentos):
        for q in pagamentos[i + 1:]:
            try:
                d1 = datetime.strptime(p["data"], "%d/%m/%Y")
                d2 = datetime.strptime(q["data"], "%d/%m/%Y")
            except ValueError:
                continue
            if abs((d1 - d2).days) <= 2 and abs(p["valor"] - q["valor"]) < 0.01:
                duplicados.append((p, q))

    diferenca = perfil["diferenca"]
    tem_debito = diferenca > 0

    def _fmt(v):
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    inconsistencias = []
    if duplicados:
        # detalha quem registrou o quê (pedido do usuário, 2026-09-10): não
        # basta dizer "1 pagamento duplicado", tem que dar pra ver qual
        # pagamento e por quem foi lançado cada um.
        def _lado(x):
            titulo = (x.get("comentario") or "").strip()
            autor = x.get("autor") or "autor não identificado"
            return f"{x['data']}" + (f' "{titulo}"' if titulo else "") + f" (registrado por {autor})"
        detalhes = "; ".join(
            f"R$ {_fmt(p['valor'])} — {_lado(p)} e {_lado(q)}"
            for p, q in duplicados
        )
        inconsistencias.append(
            f"{len(duplicados)} possível(is) pagamento(s) duplicado(s) — mesmo valor, datas próximas: {detalhes}."
        )
    if tem_debito:
        inconsistencias.append(f"Débito em aberto: R$ {_fmt(diferenca)}.")

    # Regra institucional (pedido do usuario, 2026-09-10): pagamento via pix
    # so deve ser lancado pelo Diogenes. Se aparece um pagamento com "pix"
    # no comentario registrado por outra pessoa, sinaliza pra conferencia.
    for p in pagamentos:
        if p.get("via_pix") and "diogenes" not in norm(p.get("autor")):
            inconsistencias.append(
                f"Pagamento via pix de R$ {_fmt(p['valor'])} em {p['data']} "
                f"(\"{(p.get('comentario') or '').strip()}\") registrado por "
                f"{p.get('autor') or 'autor não identificado'}, não pelo Diógenes "
                "— conferir (pix só deve ser lançado pelo Diógenes)."
            )

    return {
        "pagamentos": pagamentos, "duplicados": duplicados, "diferenca": diferenca,
        "tem_debito": tem_debito, "inconsistencias": inconsistencias,
    }


def contexto_ata_oficial(situacao):
    """Nota curta pra um nome da ata que nao bateu no roster ATIVO
    (roster_turma), mas foi encontrado na Ata de Chamada OFICIAL do painel
    (relatorio que lista todo aluno vinculado a turma, ativo ou nao). Nao tem
    id_aluno disponivel nessa fonte, entao so serve de contexto - nao da pra
    gravar comentario automaticamente pra esse aluno por aqui."""
    return (
        f"Está vinculado a esta turma no cadastro oficial do Fuctura, com situação/observação "
        f"\"{situacao}\" - mas sem id localizado no roster ativo, então não dá pra gravar comentário "
        f"automaticamente por aqui. Localizar manualmente no painel pra conferir."
    )


def contexto_aluno_fora_da_turma(perfil, comentarios, encontrado_em):
    """Monta uma nota informativa (NAO uma decisao automatica) sobre um nome
    da ata que nao bateu com o roster ATUAL da turma, mas foi encontrado numa
    busca mais ampla (turma de devedor/advogado, ou lista viva de devedores).
    So contexto pra quem for revisar decidir - ver memoria
    fuctura_devedor_filtro_confiabilidade: nao confiar em campo/keyword
    isolado, a pessoa que le precisa decidir."""
    partes = []
    if encontrado_em == "devedor":
        partes.append("Está na turma de controle Devedor/Pendência.")
    elif encontrado_em == "advogado":
        partes.append("Está na turma de Aguardando Advogado.")
    elif encontrado_em == "lista_devedores_geral":
        partes.append("Consta na lista viva de Devedores do painel (fora das turmas de controle).")

    cancelamentos = [c for c in comentarios if houve_sinal_cancelamento(c["titulo"] + " " + c["texto"])]
    if cancelamentos:
        ultimo = cancelamentos[-1]
        partes.append(f"⚠ Há menção de cancelamento em comentário de {ultimo['data']} - conferir antes de agir.")

    if encontrado_em != "advogado":
        advogados = [c for c in comentarios if JA_COM_ADVOGADO_RE.search(c["titulo"] + " " + c["texto"])]
        if advogados:
            partes.append("⚠ Comentários mencionam envolvimento de advogado/cobrança externa.")

    turmas_atuais = ", ".join(t["nome"] for t in perfil.get("turmas_atuais", [])) or "nenhuma no cadastro"
    partes.append(f"Turma(s) atual(is): {turmas_atuais}.")
    return " ".join(partes)


def regra_urgente_devedor_advogado(perfil, tem_debito, ids_turmas_controle):
    """Secao 7 da spec. Achado real (2026-09-08): a mensagem gravada de
    volta no Fuctura comecava com a palavra "URGENTE" mesmo depois da
    correcao que tirou o tipo="2" (Urgente) automatico - o texto do
    comentario continuava marcando visualmente como urgente pra quem le no
    Fuctura, so nao usando mais a categoria oficial. Pedido explicito do
    usuario: nenhuma mencao a "urgente" deve ir pro Fuctura, nem no tipo
    nem no texto - so o fato em si (debito sem estar em turma de controle),
    sem alarme. O booleano continua igual, usado so pra destaque na NOSSA
    tela (quando aplicavel)."""
    if not tem_debito:
        return False, None
    esta_em_controle = perfil["id_aluno"] in ids_turmas_controle
    if esta_em_controle:
        return False, None
    return True, "Aluno possui débito e não está na turma de devedor nem na turma de advogado."


def nota_devedor(perfil, tem_debito, ids_turma_devedor, ids_turma_advogado):
    """Cruzamento de sinais de inadimplencia pro Fechamento E o
    Acompanhamento de Turma (pedido do usuario, 2026-09-10): sinalizar que
    o aluno e devedor SEMPRE que houver sinal disso - estando ele ou nao na
    turma de controle Devedor/Pendencia.

    Sinais cruzados (nenhum decide nada sozinho - ver memoria
    fuctura_devedor_filtro_confiabilidade; aqui e so pra quem revisa o
    fechamento enxergar de imediato):
      - diferenca financeira em aberto (contratado - recebido > 0, ver
        checagem_financeira -> tem_debito)
      - Situacao do cadastro = "Devedor"
      - presenca na turma de controle Devedor/Pendencia
      - presenca na turma de controle Aguardando Advogado

    Retorna uma frase pra juntar ao comentario financeiro do fechamento,
    ou None quando nenhum sinal de devedor aparece."""
    id_aluno = perfil["id_aluno"]
    situacao_devedor = norm(perfil.get("status", "")) == "devedor"
    na_turma_devedor = id_aluno in (ids_turma_devedor or set())
    na_turma_advogado = id_aluno in (ids_turma_advogado or set())

    if not (tem_debito or situacao_devedor or na_turma_devedor or na_turma_advogado):
        return None

    if na_turma_devedor:
        onde = "já está na turma de controle Devedor/Pendência"
    elif na_turma_advogado:
        onde = "está na turma de controle Aguardando Advogado (não na de Devedor/Pendência)"
    else:
        onde = "NÃO está em nenhuma turma de controle (nem Devedor/Pendência, nem Aguardando Advogado)"

    motivos = []
    if tem_debito:
        motivos.append("diferença financeira em aberto (contratado − recebido)")
    if situacao_devedor:
        motivos.append('Situação do cadastro marcada como "Devedor"')

    if motivos:
        return f"ALUNO DEVEDOR: {'; '.join(motivos)} — {onde}."

    # entrou aqui so por estar numa turma de controle, sem diferenca
    # financeira nem Situacao de devedor no cadastro
    qual = "Devedor/Pendência" if na_turma_devedor else "Aguardando Advogado"
    return (
        f"Aluno está na turma de controle {qual}, mas sem diferença financeira em aberto "
        f"nem Situação de devedor no cadastro — conferir se ainda há pendência."
    )

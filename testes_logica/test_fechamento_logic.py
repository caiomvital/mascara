"""
Testes de regressao pras funcoes auxiliares de fechamento_logic.py
(casamento por telefone, mesclagem de marcacao, resumo academico) que
ainda nao tinham nenhum teste automatizado.

Como rodar:
    cd testes_logica
    python test_fechamento_logic.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fechamento_logic as logic  # noqa: E402

_falhas = []
_ok_count = 0


def relatar(nome, ok, detalhe=""):
    global _ok_count
    if ok:
        _ok_count += 1
        print(f"  OK     {nome}")
    else:
        _falhas.append((nome, detalhe))
        print(f"  FALHOU {nome}: {detalhe}")


# ---------------------------------------------------------------------------
# casar_por_telefone - mais confiavel que nome, mas so decide quando o
# numero aponta pra EXATAMENTE 1 aluno do roster.
# ---------------------------------------------------------------------------
def teste_telefone_casa_com_variacao_de_ddi_ddd():
    roster = [{"nome": "ALUNO A", "telefone": "5581999998888"}]  # com DDI
    aluno = logic.casar_por_telefone("(81) 99999-8888", roster)  # sem DDI, formatado
    relatar(
        "casar_por_telefone: variação de DDI/formatação ainda casa (últimos 8 dígitos)",
        aluno is not None and aluno["nome"] == "ALUNO A",
        f"resultado: {aluno}",
    )


def teste_telefone_ambiguo_nao_decide():
    roster = [{"nome": "ALUNO A", "telefone": "81999998888"}, {"nome": "ALUNO B", "telefone": "87999998888"[-11:]}]
    # forca ambiguidade: dois alunos com os mesmos ultimos 8 digitos
    roster = [{"nome": "ALUNO A", "telefone": "81999998888"}, {"nome": "ALUNO B", "telefone": "11999998888"}]
    aluno = logic.casar_por_telefone("999998888", roster)
    relatar(
        "casar_por_telefone: número ambíguo (aponta pra mais de 1 aluno) não decide sozinho",
        aluno is None,
        f"decidiu incorretamente por: {aluno}",
    )


def teste_telefone_curto_demais_nao_decide():
    roster = [{"nome": "ALUNO A", "telefone": "81999998888"}]
    aluno = logic.casar_por_telefone("888", roster)
    relatar(
        "casar_por_telefone: número curto demais (menos de 8 dígitos) não decide",
        aluno is None,
        f"decidiu incorretamente por: {aluno}",
    )


# ---------------------------------------------------------------------------
# mesclar_marcacao - "sem_registro" nunca apaga uma marcacao real vinda de
# outro arquivo/sessao; conflito real vira "incerto", nao escolhe arbitrario.
# ---------------------------------------------------------------------------
def teste_mesclar_marcacao():
    casos = [
        (("presenca", "presenca"), "presenca"),
        (("sem_registro", "presenca"), "presenca"),
        (("falta", "sem_registro"), "falta"),
        (("presenca", "falta"), "incerto"),
        (("sem_registro", "sem_registro"), "sem_registro"),
    ]
    for (atual, nova), esperado in casos:
        resultado = logic.mesclar_marcacao(atual, nova)
        relatar(
            f"mesclar_marcacao({atual!r}, {nova!r}) == {esperado!r}",
            resultado == esperado,
            f"retornou {resultado!r}",
        )


# ---------------------------------------------------------------------------
# montar_resumo_academico - data desconhecida com marcacao CLARA ainda
# conta pra presenca/falta (nao pode sumir da contagem so por causa da
# data ilegivel); so marcacao AMBIGUA vira "incerto".
# ---------------------------------------------------------------------------
def teste_data_desconhecida_com_marcacao_clara_ainda_conta():
    presencas = [
        {"data": "01/08/2026", "marcacao": "presenca"},
        {"data": "08/08/2026", "marcacao": "falta"},
        {"data": "??", "marcacao": "presenca"},  # data ilegível, marcação clara
    ]
    resumo = logic.montar_resumo_academico(presencas)
    relatar(
        "montar_resumo_academico: presença com data ilegível ainda conta no total",
        resumo["presencas"] == 2 and resumo["total"] == 3,
        f"resumo: {resumo}",
    )


def teste_marcacao_ambigua_vira_incerto_nao_conta_presenca_nem_falta():
    presencas = [
        {"data": "01/08/2026", "marcacao": "presenca"},
        {"data": "08/08/2026", "marcacao": "incerto"},
    ]
    resumo = logic.montar_resumo_academico(presencas)
    relatar(
        "montar_resumo_academico: marcação ambígua não conta como presença nem falta",
        resumo["presencas"] == 1 and resumo["faltas"] == 0 and resumo["incertos"] == 1,
        f"resumo: {resumo}",
    )


def teste_celula_em_branco_nao_conta_como_aula():
    presencas = [
        {"data": "01/08/2026", "marcacao": "presenca"},
        {"data": "08/08/2026", "marcacao": "sem_registro"},  # aula ainda não lançada
    ]
    resumo = logic.montar_resumo_academico(presencas)
    relatar(
        "montar_resumo_academico: célula em branco (aula não lançada) não conta no total",
        resumo["total"] == 1,
        f"resumo: {resumo}",
    )


# ---------------------------------------------------------------------------
# alerta_faltas_finais - sinal a mais no fechamento (pedido do usuário,
# 2026-09-10): faltar o BLOCO de aulas finais (AULAS_FINAIS_JANELA), não só
# "faltou a última". Só quando a turma teve pelo menos esse tanto de aula.
# ---------------------------------------------------------------------------
def _aula(d, m):
    return {"data": d, "marcacao": m}


def teste_alerta_faltas_finais_dispara_quando_falta_o_bloco_final():
    presencas = [
        _aula("01/08/2026", "presenca"), _aula("08/08/2026", "presenca"),
        _aula("15/08/2026", "falta"), _aula("22/08/2026", "falta"), _aula("29/08/2026", "falta"),
    ]
    r = logic.montar_resumo_academico(presencas)
    relatar(
        "alerta_faltas_finais: dispara ao faltar as 3 aulas finais de uma turma de 5",
        r["alerta_faltas_finais"] is True and r["faltas_finais"] == 3
        # achado do usuario (2026-09-16): texto simplificado, sem repetir
        # "aulas finais" com o mesmo detalhe 2x (ver sugestao_academica) -
        # so precisa dizer que e possivel abandono, uma vez.
        and "Possível abandono" in r["texto"] and "Faltou as últimas 2 aulas" not in r["texto"],
        f"resumo: {r}",
    )


def teste_alerta_faltas_finais_nao_dispara_com_poucas_aulas():
    r = logic.montar_resumo_academico([_aula("01/08/2026", "falta"), _aula("08/08/2026", "falta")])
    relatar(
        "alerta_faltas_finais: NÃO dispara quando a turma teve menos aulas que a janela (2 < 3), mesmo com tudo falta",
        r["alerta_faltas_finais"] is False and r["faltou_ultimas"] is True,
        f"resumo: {r}",
    )


def teste_alerta_faltas_finais_nao_dispara_se_veio_a_uma_das_finais():
    presencas = [
        _aula("01/08/2026", "falta"), _aula("08/08/2026", "presenca"),
        _aula("15/08/2026", "falta"), _aula("22/08/2026", "falta"),
    ]
    r = logic.montar_resumo_academico(presencas)
    relatar(
        "alerta_faltas_finais: NÃO dispara se compareceu a alguma das aulas da janela final",
        r["alerta_faltas_finais"] is False,
        f"resumo: {r}",
    )


def teste_alerta_faltas_finais_funciona_com_colunas_sem_data():
    # ata cujas colunas não têm data legível (folha de continuação): as
    # marcações vêm como placeholder. O alerta de abandono tem que sair
    # mesmo assim, pela ordem das colunas (achado real 2026-09-10).
    presencas = [
        {"data": "data_nao_identificada_1", "marcacao": "presenca"},
        {"data": "data_nao_identificada_2", "marcacao": "presenca"},
        {"data": "data_nao_identificada_3", "marcacao": "presenca"},
        {"data": "data_nao_identificada_4", "marcacao": "falta"},
        {"data": "data_nao_identificada_5", "marcacao": "falta"},
        {"data": "data_nao_identificada_6", "marcacao": "falta"},
    ]
    r = logic.montar_resumo_academico(presencas)
    relatar(
        "alerta_faltas_finais: dispara mesmo quando as colunas não têm data (pela ordem das colunas)",
        r["alerta_faltas_finais"] is True and r["faltas"] == 3 and r["presencas"] == 3,
        f"resumo: {r}",
    )


def teste_faltou_ultimas_com_colunas_sem_data_mistura_com_datadas():
    # 2 colunas com data (presente) + 2 sem data (falta) = faltou as 2 últimas
    presencas = [
        {"data": "01/08/2026", "marcacao": "presenca"},
        {"data": "08/08/2026", "marcacao": "presenca"},
        {"data": "data_nao_identificada_1", "marcacao": "falta"},
        {"data": "data_nao_identificada_2", "marcacao": "falta"},
    ]
    r = logic.montar_resumo_academico(presencas)
    relatar(
        "faltou_ultimas: colunas com data entram primeiro, sem data depois — 'faltou as últimas 2' sai certo",
        r["faltou_ultimas"] is True and "últimas 2 aulas" in r["texto"],
        f"resumo: {r}",
    )


def teste_sugestao_academica_escala_no_alerta_de_faltas_finais():
    presencas = [
        _aula("01/08/2026", "presenca"),
        _aula("15/08/2026", "falta"), _aula("22/08/2026", "falta"), _aula("29/08/2026", "falta"),
    ]
    r = logic.montar_resumo_academico(presencas)
    s = logic.sugestao_academica(r)
    relatar(
        "sugestao_academica: no alerta de faltas finais vira uma sugestão de prioridade (não a genérica)",
        # achado do usuario (2026-09-16): sugestao nao repete mais o fato
        # ("faltou X aulas finais") que o texto do resumo ja diz - so a acao.
        "Prioridade" in s and "certificado" in s,
        f"sugestão: {s!r}",
    )


# ---------------------------------------------------------------------------
# houve_sinal_cancelamento - sinal FORTE ("abandonou"/"sumiu"/"desistiu")
# tem que vencer o filtro de contexto financeiro quando os dois aparecem
# juntos no mesmo texto (aluno realmente desistiu E cancelou o pagamento).
# ---------------------------------------------------------------------------
def teste_sinal_forte_vence_contexto_financeiro():
    texto = "aluno abandonou o curso, pagamento recorrente cancelado"
    relatar(
        "houve_sinal_cancelamento: sinal forte + contexto financeiro juntos ainda conta como cancelamento",
        logic.houve_sinal_cancelamento(texto) is True,
        f"não detectou cancelamento em: {texto}",
    )


# ---------------------------------------------------------------------------
# regra_urgente_devedor_advogado - achado real (2026-09-08): mesmo depois de
# tirar o tipo="2" (Urgente) automático, a mensagem gravada de volta no
# Fuctura ainda começava com a palavra "URGENTE" - continuava marcando pra
# quem lê no Fuctura, só não pela categoria oficial. A mensagem não pode
# mais conter essa palavra, em nenhuma capitalização; o booleano (usado só
# pra destaque interno, nunca mais pro texto/tipo gravado) continua igual.
# ---------------------------------------------------------------------------
def teste_regra_urgente_nunca_menciona_a_palavra_urgente_na_mensagem():
    perfil = {"id_aluno": "1"}
    urgente, msg = logic.regra_urgente_devedor_advogado(perfil, tem_debito=True, ids_turmas_controle=set())
    relatar(
        "regra_urgente_devedor_advogado: continua detectando o caso (débito + fora de turma de controle)",
        urgente is True,
        f"urgente={urgente!r}",
    )
    relatar(
        "regra_urgente_devedor_advogado: a mensagem NUNCA contém a palavra 'urgente' (nenhuma capitalização)",
        msg is not None and "urgente" not in msg.lower(),
        f"mensagem: {msg!r}",
    )


def teste_regra_urgente_sem_debito_nao_dispara():
    perfil = {"id_aluno": "1"}
    urgente, msg = logic.regra_urgente_devedor_advogado(perfil, tem_debito=False, ids_turmas_controle=set())
    relatar(
        "regra_urgente_devedor_advogado: sem débito não dispara nada",
        urgente is False and msg is None,
        f"urgente={urgente!r} msg={msg!r}",
    )


# ---------------------------------------------------------------------------
# eh_turma_infantil / montar_relatorio_turma(infantil=...) - pedido do
# usuario (2026-09-09): ponto de bifurcacao pro fechamento infantil (Biblia
# 3D), mesmo sem saber ainda a diferenca real - hoje so muda o rotulo.
# Nomes reais observados ao vivo na Reconciliacao de Devedores.
# ---------------------------------------------------------------------------
def teste_eh_turma_infantil_reconhece_nomes_reais_biblia_3d():
    nomes_infantis = [
        "Academia Biblia 3D", "B3D M1", "B3D M2", "B3D M3", "B3D M4",
        "BL1 2025 8h30 Sab", "BL1 2025 10h30 Sab", "BL1 2025 13h30 Sab",
        "BL1 2025 14h Ter", "BL 2026 08h30 Sab 1", "BL 2026 10h30 Sab 1",
    ]
    for nome in nomes_infantis:
        relatar(
            f"eh_turma_infantil reconhece {nome!r} como infantil",
            logic.eh_turma_infantil(nome) is True,
            f"não reconheceu {nome!r}",
        )


def teste_eh_turma_infantil_nao_reconhece_turmas_de_adulto():
    nomes_adulto = [
        "Academia Java Full Stack (4M)", "J1 27/01/24 Sab M", "PY2 07/12/24 SAB M",
        "Devedor/Pendencia", "Aguardando Advogado", "Academia PHP 2018 (3M)",
    ]
    for nome in nomes_adulto:
        relatar(
            f"eh_turma_infantil NÃO marca {nome!r} como infantil",
            logic.eh_turma_infantil(nome) is False,
            f"marcou {nome!r} como infantil incorretamente",
        )


def teste_montar_relatorio_turma_infantil_marca_rotulo():
    alunos = [{"id_aluno": "1", "nome_fuctura": "ALUNO TESTE", "presencas": [
        {"data": "01/08/2026", "marcacao": "presenca"},
    ]}]
    relatorio_adulto = logic.montar_relatorio_turma("J1", "PROF TESTE", alunos, rotulo="Fechamento", infantil=False)
    relatorio_infantil = logic.montar_relatorio_turma("B3D M1", "PROF TESTE", alunos, rotulo="Fechamento", infantil=True)
    relatar(
        "montar_relatorio_turma(infantil=False) não altera o rótulo",
        relatorio_adulto["titulo"] == "Fechamento de Turma J1",
        f"título: {relatorio_adulto['titulo']!r}",
    )
    relatar(
        "montar_relatorio_turma(infantil=True) marca '(Infantil)' no rótulo",
        relatorio_infantil["titulo"] == "Fechamento (Infantil) de Turma B3D M1",
        f"título: {relatorio_infantil['titulo']!r}",
    )


def teste_montar_relatorio_turma_categoriza_por_marca_do_nome():
    # mesma proporção da turma real .JA4 09/07/26 Qui (Yago): 9 "-", 8 ".", 4 sem marca
    alunos = (
        [{"id_aluno": str(i), "nome_fuctura": f"-REFAZENDO{i}", "presencas": []} for i in range(9)]
        + [{"id_aluno": str(100 + i), "nome_fuctura": f".ABANDONO{i}", "presencas": []} for i in range(8)]
        + [{"id_aluno": str(200 + i), "nome_fuctura": f"PRIMEIRAVEZ{i}", "presencas": []} for i in range(4)]
    )
    relatorio = logic.montar_relatorio_turma("JA4 09/07/26 Qui", "YAGO", alunos)
    categorias = [linha["categoria"] for linha in relatorio["tabela"]]
    relatar(
        "montar_relatorio_turma: categoriza cada linha pela marca do nome (Refazendo/Abandono/Primeira vez)",
        categorias.count("Refazendo") == 9 and categorias.count("Abandono") == 8 and categorias.count("Primeira vez") == 4,
        f"categorias: {categorias}",
    )
    relatar(
        "montar_relatorio_turma: inclui o indicador de funil (mesma proporção 4/21 da turma real)",
        relatorio["funil"]["total"] == 21 and relatorio["funil"]["primeira_vez"] == 4,
        f"funil: {relatorio['funil']}",
    )
    relatar(
        "montar_relatorio_turma: proporção baixa de 'primeira vez' vira observação de alerta (pedido do Diógenes)",
        "avalie se compensa abrir a próxima turma" in relatorio["observacoes"],
        f"observações: {relatorio['observacoes']!r}",
    )


def teste_montar_relatorio_turma_sem_alerta_de_funil_quando_proporcao_normal():
    alunos = [{"id_aluno": str(i), "nome_fuctura": f"ALUNO{i}", "presencas": []} for i in range(10)]
    relatorio = logic.montar_relatorio_turma("J1 08/07/25 TER N", "PROF TESTE", alunos)
    relatar(
        "montar_relatorio_turma: com todos em 'primeira vez' (proporção alta), NÃO alerta sobre o funil",
        "avalie se compensa abrir a próxima turma" not in relatorio["observacoes"],
        f"observações: {relatorio['observacoes']!r}",
    )


def teste_regra_urgente_ja_em_turma_de_controle_nao_dispara():
    perfil = {"id_aluno": "1"}
    urgente, msg = logic.regra_urgente_devedor_advogado(perfil, tem_debito=True, ids_turmas_controle={"1"})
    relatar(
        "regra_urgente_devedor_advogado: já estando em turma de controle não dispara",
        urgente is False and msg is None,
        f"urgente={urgente!r} msg={msg!r}",
    )


# ---------------------------------------------------------------------------
# nota_devedor - pedido do usuario (2026-09-10): o Fechamento e o
# Acompanhamento de Turma tem que sinalizar aluno devedor SEMPRE que houver
# sinal, estando ele ou nao na turma de controle Devedor/Pendencia.
# ---------------------------------------------------------------------------
def _perfil_dev(id_aluno="1", status=""):
    return {"id_aluno": id_aluno, "status": status}


def teste_nota_devedor_sem_nenhum_sinal_retorna_none():
    nota = logic.nota_devedor(_perfil_dev(), tem_debito=False, ids_turma_devedor=set(), ids_turma_advogado=set())
    relatar(
        "nota_devedor: nenhum sinal de devedor → None (não polui o comentário financeiro)",
        nota is None,
        f"nota={nota!r}",
    )


def teste_nota_devedor_com_debito_fora_de_qualquer_turma_de_controle():
    nota = logic.nota_devedor(_perfil_dev("7"), tem_debito=True, ids_turma_devedor={"1", "2"}, ids_turma_advogado={"3"})
    relatar(
        "nota_devedor: deve E não está em turma de controle → marca ALUNO DEVEDOR + diz que não está em controle",
        nota is not None and nota.startswith("ALUNO DEVEDOR:") and "NÃO está em nenhuma turma de controle" in nota,
        f"nota={nota!r}",
    )


def teste_nota_devedor_com_debito_ja_na_turma_devedor():
    nota = logic.nota_devedor(_perfil_dev("7"), tem_debito=True, ids_turma_devedor={"7"}, ids_turma_advogado=set())
    relatar(
        "nota_devedor: deve E já está na turma Devedor/Pendência → ainda marca ALUNO DEVEDOR (não some por estar em controle)",
        nota is not None and nota.startswith("ALUNO DEVEDOR:") and "já está na turma de controle Devedor/Pendência" in nota,
        f"nota={nota!r}",
    )


def teste_nota_devedor_por_situacao_do_cadastro_sem_diferenca_financeira():
    nota = logic.nota_devedor(_perfil_dev("7", status="Devedor"), tem_debito=False, ids_turma_devedor=set(), ids_turma_advogado=set())
    relatar(
        "nota_devedor: Situação do cadastro = 'Devedor' já basta pra marcar ALUNO DEVEDOR, mesmo sem diferença financeira",
        nota is not None and nota.startswith("ALUNO DEVEDOR:") and 'Situação do cadastro marcada como "Devedor"' in nota,
        f"nota={nota!r}",
    )


def teste_nota_devedor_so_na_turma_advogado_sem_debito_nem_status_pede_conferencia():
    nota = logic.nota_devedor(_perfil_dev("7"), tem_debito=False, ids_turma_devedor=set(), ids_turma_advogado={"7"})
    relatar(
        "nota_devedor: só está na turma Aguardando Advogado (sem diferença nem status) → pede conferência, sem afirmar 'ALUNO DEVEDOR'",
        nota is not None and not nota.startswith("ALUNO DEVEDOR:") and "Aguardando Advogado" in nota and "conferir" in nota,
        f"nota={nota!r}",
    )


def teste_nota_devedor_com_debito_na_turma_advogado_diz_que_nao_esta_na_de_devedor():
    nota = logic.nota_devedor(_perfil_dev("7"), tem_debito=True, ids_turma_devedor=set(), ids_turma_advogado={"7"})
    relatar(
        "nota_devedor: deve E está na turma Aguardando Advogado → marca ALUNO DEVEDOR e diz que NÃO está na de Devedor/Pendência",
        nota is not None and nota.startswith("ALUNO DEVEDOR:") and "não na de Devedor/Pendência" in nota,
        f"nota={nota!r}",
    )


# ---------------------------------------------------------------------------
# checagem_financeira - nunca tinha teste dedicado (identificado em
# STATUS.md 2026-09-08). Reconstroi pagamentos reais a partir de
# comentarios, detecta possivel duplicidade (mesmo valor, datas proximas) e
# cruza com o debito do perfil - mas nunca decide/grava nada sozinha, so
# monta o dicionario de inconsistencias pra revisao humana.
# ---------------------------------------------------------------------------
def _perfil(diferenca=0.0):
    return {"diferenca": diferenca}


def _comentario(data, titulo="", tipo="-Comentário", texto=""):
    return {"data": data, "titulo": titulo, "tipo": tipo, "texto": texto}


def teste_checagem_financeira_detecta_pagamento_por_tipo():
    comentarios = [_comentario("01/08/2026", tipo="-Pagamento Realizado", texto="Pagamento de R$ 500,00 recebido")]
    resultado = logic.checagem_financeira(_perfil(), comentarios)
    relatar(
        "checagem_financeira: pagamento com tipo '-Pagamento Realizado' e valor em R$ é reconstruído",
        len(resultado["pagamentos"]) == 1 and resultado["pagamentos"][0]["valor"] == 500.0,
        f"pagamentos: {resultado['pagamentos']}",
    )


def teste_checagem_financeira_detecta_pagamento_por_texto_livre():
    comentarios = [_comentario("01/08/2026", tipo="-Comentário", texto="Aluno confirmou pix recebido, R$ 300,00")]
    resultado = logic.checagem_financeira(_perfil(), comentarios)
    relatar(
        "checagem_financeira: pagamento por texto livre (PAGAMENTO_RE), mesmo com tipo genérico, é reconstruído",
        len(resultado["pagamentos"]) == 1 and resultado["pagamentos"][0]["valor"] == 300.0,
        f"pagamentos: {resultado['pagamentos']}",
    )


def teste_checagem_financeira_pagamento_sem_valor_em_reais_e_ignorado():
    # achado ao ler a regra: tipo bate, mas sem "R$ <valor>" no texto o
    # VALOR_RE nao acha nada e o comentario e descartado - silenciosamente,
    # sem erro. Documentado aqui pra nao ser reintroduzido como bug sem
    # ninguem perceber.
    comentarios = [_comentario("01/08/2026", tipo="-Pagamento Realizado", texto="Pagamento confirmado por telefone")]
    resultado = logic.checagem_financeira(_perfil(), comentarios)
    relatar(
        "checagem_financeira: pagamento sem valor 'R$ ...' no texto é ignorado (não quebra, não inventa valor)",
        resultado["pagamentos"] == [],
        f"pagamentos: {resultado['pagamentos']}",
    )


def teste_checagem_financeira_comentario_nao_financeiro_e_ignorado():
    comentarios = [_comentario("01/08/2026", tipo="-Comentário", texto="Confirmou presença na aula de amanhã")]
    resultado = logic.checagem_financeira(_perfil(), comentarios)
    relatar(
        "checagem_financeira: comentário sem sinal de pagamento não vira pagamento",
        resultado["pagamentos"] == [],
        f"pagamentos: {resultado['pagamentos']}",
    )


def teste_checagem_financeira_mesmo_pagamento_lancado_duas_vezes_nao_duplica():
    # mesma data + mesmo valor = provavelmente o MESMO lancamento (ex:
    # comentario duplicado por engano) - tem que colapsar em 1 pagamento
    # so, e ESSE caso nao deve contar como "duplicado" suspeito (a
    # duplicidade suspeita e sobre LANCAMENTOS DIFERENTES do mesmo valor
    # em datas proximas, nao a mesma linha repetida).
    comentarios = [
        _comentario("01/08/2026", tipo="-Pagamento Realizado", texto="Pagamento de R$ 500,00"),
        _comentario("01/08/2026", tipo="-Pagamento Realizado", texto="Pagamento de R$ 500,00"),
    ]
    resultado = logic.checagem_financeira(_perfil(), comentarios)
    relatar(
        "checagem_financeira: mesma data+valor colapsa em 1 pagamento só, não vira 'duplicado' suspeito",
        len(resultado["pagamentos"]) == 1 and resultado["duplicados"] == [],
        f"pagamentos: {resultado['pagamentos']} | duplicados: {resultado['duplicados']}",
    )


def teste_checagem_financeira_valores_iguais_datas_proximas_e_duplicado_suspeito():
    comentarios = [
        _comentario("01/08/2026", tipo="-Pagamento Realizado", texto="Pagamento de R$ 500,00"),
        _comentario("02/08/2026", tipo="-Pagamento Realizado", texto="Pagamento de R$ 500,00"),
    ]
    resultado = logic.checagem_financeira(_perfil(), comentarios)
    relatar(
        "checagem_financeira: mesmo valor em datas diferentes mas próximas (≤2 dias) é sinalizado como duplicado suspeito",
        len(resultado["pagamentos"]) == 2 and len(resultado["duplicados"]) == 1
        and any("duplicad" in i for i in resultado["inconsistencias"]),
        f"pagamentos: {resultado['pagamentos']} | duplicados: {resultado['duplicados']} | inconsistencias: {resultado['inconsistencias']}",
    )


def teste_checagem_financeira_duplicado_detalha_quem_registrou():
    comentarios = [
        {"data": "01/08/2026", "titulo": "PGTO", "tipo": "-Pagamento Realizado", "autor": "Diogenes", "texto": "R$ 500,00"},
        {"data": "02/08/2026", "titulo": "PGTO 2", "tipo": "-Pagamento Realizado", "autor": "Surama", "texto": "R$ 500,00"},
    ]
    resultado = logic.checagem_financeira(_perfil(), comentarios)
    inc = " ".join(resultado["inconsistencias"])
    relatar(
        "checagem_financeira: o texto de pagamento duplicado detalha datas + quem registrou cada um",
        "01/08/2026" in inc and "Diogenes" in inc and "02/08/2026" in inc and "Surama" in inc,
        f"inconsistencias: {resultado['inconsistencias']}",
    )


def teste_checagem_financeira_pix_por_terceiro_e_sinalizado():
    coms = [
        {"data": "01/06/2026", "titulo": "PAGAMENTO PIX", "tipo": "-Pagamento Realizado", "autor": "Surama", "texto": "Pago R$ 377,00 via pix"},
    ]
    inc = " ".join(logic.checagem_financeira(_perfil(), coms)["inconsistencias"])
    relatar(
        "checagem_financeira: pix registrado por terceiro (não Diógenes) vira inconsistência com nome de quem lançou",
        "pix" in inc.lower() and "Surama" in inc and "Diógenes" in inc,
        f"inconsistencias: {inc!r}",
    )


def teste_checagem_financeira_pix_pelo_diogenes_nao_sinaliza():
    for autor in ("Diogenes", "Diogenes Souza Leão", "DIOGENES"):
        coms = [{"data": "01/06/2026", "titulo": "PAGAMENTO PIX", "tipo": "-Pagamento Realizado", "autor": autor, "texto": "R$ 377,00 pix"}]
        inc = logic.checagem_financeira(_perfil(), coms)["inconsistencias"]
        relatar(
            f"checagem_financeira: pix pelo Diógenes ({autor!r}) NÃO vira inconsistência",
            not any("pix só deve" in i for i in inc),
            f"inconsistencias: {inc}",
        )


def teste_checagem_financeira_pagamento_boleto_por_terceiro_nao_sinaliza_pix():
    coms = [{"data": "01/06/2026", "titulo": "BOLETO PAGO 3/13", "tipo": "-Pagamento Realizado", "autor": "Ana França", "texto": "R$ 377,00 boleto"}]
    inc = logic.checagem_financeira(_perfil(), coms)["inconsistencias"]
    relatar(
        "checagem_financeira: pagamento de boleto por terceiro não dispara a regra do pix",
        not any("pix" in i.lower() for i in inc),
        f"inconsistencias: {inc}",
    )


def teste_checagem_financeira_pagamento_sem_autor_nao_quebra():
    comentarios = [
        {"data": "01/08/2026", "titulo": "PGTO", "tipo": "-Pagamento Realizado", "texto": "R$ 500,00"},
        {"data": "02/08/2026", "titulo": "PGTO", "tipo": "-Pagamento Realizado", "texto": "R$ 500,00"},
    ]
    resultado = logic.checagem_financeira(_perfil(), comentarios)
    relatar(
        "checagem_financeira: pagamento sem campo 'autor' vira 'autor não identificado', não quebra",
        any("autor não identificado" in i for i in resultado["inconsistencias"]),
        f"inconsistencias: {resultado['inconsistencias']}",
    )


def teste_checagem_financeira_valores_iguais_datas_distantes_nao_e_duplicado():
    comentarios = [
        _comentario("01/08/2026", tipo="-Pagamento Realizado", texto="Pagamento de R$ 500,00"),
        _comentario("01/09/2026", tipo="-Pagamento Realizado", texto="Pagamento de R$ 500,00"),
    ]
    resultado = logic.checagem_financeira(_perfil(), comentarios)
    relatar(
        "checagem_financeira: mesmo valor em datas distantes (parcelas normais) não é sinalizado como duplicado",
        len(resultado["pagamentos"]) == 2 and resultado["duplicados"] == [],
        f"pagamentos: {resultado['pagamentos']} | duplicados: {resultado['duplicados']}",
    )


def teste_checagem_financeira_debito_em_aberto_vira_inconsistencia():
    resultado = logic.checagem_financeira(_perfil(diferenca=250.0), [])
    relatar(
        "checagem_financeira: diferença positiva no perfil vira 'tem_debito' + inconsistência formatada em R$",
        resultado["tem_debito"] is True and any("250,00" in i for i in resultado["inconsistencias"]),
        f"inconsistencias: {resultado['inconsistencias']}",
    )


def teste_checagem_financeira_sem_debito_nem_duplicidade_sem_inconsistencia():
    comentarios = [_comentario("01/08/2026", tipo="-Pagamento Realizado", texto="Pagamento de R$ 500,00")]
    resultado = logic.checagem_financeira(_perfil(diferenca=0.0), comentarios)
    relatar(
        "checagem_financeira: sem débito e sem duplicidade não gera inconsistência nenhuma",
        resultado["tem_debito"] is False and resultado["inconsistencias"] == [],
        f"resultado: {resultado}",
    )


def teste_checagem_financeira_data_ilegivel_nao_quebra_deteccao_de_duplicidade():
    # datetime.strptime falha pra data mal formada - tem que ser pulado
    # (continue), nunca lancar excecao pra fora da funcao.
    comentarios = [
        _comentario("??", tipo="-Pagamento Realizado", texto="Pagamento de R$ 500,00"),
        _comentario("01/08/2026", tipo="-Pagamento Realizado", texto="Pagamento de R$ 700,00"),
    ]
    try:
        resultado = logic.checagem_financeira(_perfil(), comentarios)
        ok = len(resultado["pagamentos"]) == 2
        detalhe = f"pagamentos: {resultado['pagamentos']}"
    except Exception as e:
        ok = False
        detalhe = f"lançou exceção: {e}"
    relatar(
        "checagem_financeira: data ilegível num pagamento não derruba a checagem inteira",
        ok,
        detalhe,
    )


# ---------------------------------------------------------------------------
# ja_tem_fechamento - pedido do usuario (2026-09-09): editar o resumo antes
# de gravar faz o assunto ganhar prefixo -IA-/-MOD- (ver
# fuctura-mascara/PREFIXO_TEXTO_ORIGINAL/EDITADO) - isso NAO pode quebrar a
# deteccao de "turma ja fechada" (que olha o titulo comecando com
# "fechamento").
# ---------------------------------------------------------------------------
def teste_ja_tem_fechamento_reconhece_titulo_sem_prefixo():
    comentarios = [{"titulo": "Fechamento — .J1 03/03/26 TER N", "texto": ""}]
    relatar(
        "ja_tem_fechamento reconhece o título normal (sem prefixo -IA-/-MOD-)",
        logic.ja_tem_fechamento(comentarios, ".J1 03/03/26 TER N"),
        "esperado True",
    )


def teste_ja_tem_fechamento_reconhece_titulo_com_prefixo_ia_ou_mod():
    for prefixo in (logic.PREFIXO_TEXTO_ORIGINAL, logic.PREFIXO_TEXTO_EDITADO):
        comentarios = [{"titulo": f"{prefixo}Fechamento — .J1 03/03/26 TER N", "texto": ""}]
        relatar(
            f"ja_tem_fechamento reconhece o título com prefixo {prefixo!r} (não regride pro bug de duplicar fechamento)",
            logic.ja_tem_fechamento(comentarios, ".J1 03/03/26 TER N"),
            "esperado True",
        )


def teste_ja_tem_fechamento_nao_confunde_prefixo_com_outro_comentario():
    # um comentario qualquer que por acaso comeca com "-IA-" mas nao e um
    # fechamento nao deve contar.
    comentarios = [{"titulo": "-IA- Reconciliação Devedor/Turma de Controle", "texto": ""}]
    relatar(
        "ja_tem_fechamento não confunde um comentário -IA- de OUTRO processo (reconciliação) com fechamento",
        not logic.ja_tem_fechamento(comentarios, ".J1 03/03/26 TER N"),
        "esperado False",
    )


def teste_eh_situacao_ref_reconhece_ref_isolado_e_combinado():
    for modalidade in ("REF", "J1 REF A4", "A4 REF ONL", "ref"):
        relatar(
            f"eh_situacao_ref reconhece {modalidade!r} como REF",
            logic.eh_situacao_ref(modalidade),
            "esperado True",
        )


def teste_eh_situacao_ref_nao_confunde_com_outras_palavras():
    for modalidade in ("MONITOR", "PRESENCIAL", "ONLINE", "", None, "REFERENCIA"):
        relatar(
            f"eh_situacao_ref não confunde {modalidade!r} com REF",
            not logic.eh_situacao_ref(modalidade),
            "esperado False",
        )


def teste_nota_ref_faltas_finais_cita_a_modalidade_recebida():
    nota = logic.nota_ref_faltas_finais("J1 REF A4")
    relatar(
        "nota_ref_faltas_finais cita a modalidade recebida e menciona REF",
        "J1 REF A4" in nota and "REF" in nota,
        f"nota: {nota!r}",
    )


# ---------------------------------------------------------------------------
# Achado do usuario (2026-09-16): "se faltou TODAS as aulas, obviamente
# faltou as finais tambem, entao nao precisa dizer as duas coisas" - o
# caso mais forte (faltou tudo) tem que virar um texto SO, sem repetir a
# linguagem de "aulas finais" que so faz sentido pro caso mais fraco
# (fartou so o bloco final, tendo comparecido antes).
# ---------------------------------------------------------------------------
def teste_faltou_tudo_nao_repete_linguagem_de_aulas_finais():
    presencas = [_aula(f"0{i}/08/2026", "falta") for i in range(1, 4)]  # 3 aulas, faltou todas
    r = logic.montar_resumo_academico(presencas)
    relatar(
        "montar_resumo_academico: faltou tudo (0 presenças) marca faltou_tudo e possivel_abandono",
        r["faltou_tudo"] is True and r["possivel_abandono"] is True,
        f"resumo: {r}",
    )
    relatar(
        "montar_resumo_academico: faltou tudo NÃO usa a frase de 'aulas finais' - faltar tudo já é óbvio, não repete",
        "aulas finais" not in r["texto"] and "seguidas" not in r["texto"] and "Possível abandono" in r["texto"],
        f"texto: {r['texto']!r}",
    )


def teste_faltou_tudo_com_apenas_1_aula_ainda_e_possivel_abandono():
    r = logic.montar_resumo_academico([_aula("01/08/2026", "falta")])
    relatar(
        "montar_resumo_academico: mesmo com só 1 aula (abaixo da janela de 3), faltar essa 1 já é 'faltou tudo'",
        r["faltou_tudo"] is True and r["possivel_abandono"] is True and r["alerta_faltas_finais"] is False,
        f"resumo: {r}",
    )


def teste_sugestao_academica_nao_repete_fato_do_resumo():
    presencas = [
        _aula("01/08/2026", "presenca"),
        _aula("15/08/2026", "falta"), _aula("22/08/2026", "falta"), _aula("29/08/2026", "falta"),
    ]
    r = logic.montar_resumo_academico(presencas)
    s = logic.sugestao_academica(r)
    relatar(
        "sugestao_academica não repete 'aulas finais' (o resumo já diz isso) - só a ação",
        "aulas finais" not in s,
        f"sugestão: {s!r}",
    )


# ---------------------------------------------------------------------------
# Pedido do Diogenes via Caio (2026-09-16): "." antes do nome = abandono,
# "-" antes do nome = refazendo - marca informal da equipe, NAO status
# oficial do Fuctura. Confirmado ao vivo que a marca sozinha nao e
# confiavel (2 alunos reais com "." tinham status Devedor/Advogado, nao
# Ex-aluno) - por isso a marca nunca decide nada sozinha, so dispara as
# conferencias (status real + outra turma no mesmo mes/ano).
# ---------------------------------------------------------------------------
def teste_eh_nome_marcado_abandono_e_refazendo():
    relatar(
        "eh_nome_marcado_abandono: reconhece '.' no início do nome",
        logic.eh_nome_marcado_abandono(".MIGUEL VINICIUS LOPES SOARES") is True,
        "",
    )
    relatar(
        "eh_nome_marcado_abandono: nome sem '.' não é marcado",
        logic.eh_nome_marcado_abandono("MIGUEL TOMAZ APOLONIO DE SOUZA") is False,
        "",
    )
    relatar(
        "eh_nome_marcado_refazendo: reconhece '-' no início do nome",
        logic.eh_nome_marcado_refazendo("-JOAO DA SILVA") is True,
        "",
    )
    relatar(
        "eh_nome_marcado_refazendo: nome sem '-' não é marcado",
        logic.eh_nome_marcado_refazendo("JOAO DA SILVA") is False,
        "",
    )


def teste_turmas_no_mesmo_mes_ano_acha_e_exclui_a_turma_atual():
    import datetime as dt
    turmas_atuais = [
        {"data": "10/08/2026", "nome": "J1 08/07/25 TER N"},  # a que está sendo fechada agora - não deve contar
        {"data": "15/08/2026", "nome": "JS3 30/04/26 QUI N"},  # outra turma, mesmo mês/ano - deve contar
        {"data": "20/01/2025", "nome": "J2 09/12/25 TER N"},  # outro mês/ano - não deve contar
    ]
    encontradas = logic.turmas_no_mesmo_mes_ano(turmas_atuais, "J1 08/07/25 TER N", dt.datetime(2026, 8, 20))
    relatar(
        "turmas_no_mesmo_mes_ano: acha só a turma de mesmo mês/ano, exclui a que está sendo fechada",
        len(encontradas) == 1 and encontradas[0]["nome"] == "JS3 30/04/26 QUI N",
        f"encontradas: {encontradas}",
    )


def teste_turmas_no_mesmo_mes_ano_sem_data_referencia_nao_quebra():
    relatar(
        "turmas_no_mesmo_mes_ano: sem data de referência, devolve lista vazia (não quebra)",
        logic.turmas_no_mesmo_mes_ano([{"data": "10/08/2026", "nome": "X"}], "Y", None) == [],
        "",
    )


def teste_nota_verificacao_abandono_confere_status_de_verdade():
    nota_ok = logic.nota_verificacao_abandono("MIGUEL TOMAZ", "Ex-aluno", [])
    relatar(
        "nota_verificacao_abandono: quando o cadastro já é Ex-aluno, confirma (não vira alerta)",
        "já está como Ex-aluno" in nota_ok and "⚠" not in nota_ok.split("Revisar")[0].split("Ex-aluno")[0],
        f"nota: {nota_ok!r}",
    )
    nota_alerta = logic.nota_verificacao_abandono(".MIGUEL VINICIUS", "Devedor", [])
    relatar(
        "nota_verificacao_abandono: quando NÃO é Ex-aluno, alerta com o status real (acha a inconsistência de verdade)",
        "⚠" in nota_alerta and "Devedor" in nota_alerta,
        f"nota: {nota_alerta!r}",
    )
    nota_outra_turma = logic.nota_verificacao_abandono(
        "MIGUEL", "Devedor", [{"data": "15/08/2026", "nome": "JS3 30/04/26 QUI N"}],
    )
    relatar(
        "nota_verificacao_abandono: cita a outra turma no mesmo período quando encontrada",
        "JS3 30/04/26 QUI N" in nota_outra_turma,
        f"nota: {nota_outra_turma!r}",
    )
    relatar(
        "nota_verificacao_abandono: sempre lembra de revisar na ata (mesmo padrão que o Diógenes gostou)",
        "Revisar na ata" in nota_ok and "Revisar na ata" in nota_alerta,
        "",
    )


def main():
    print("Rodando testes de fechamento_logic.py (funções sem teste anterior)...\n")
    teste_telefone_casa_com_variacao_de_ddi_ddd()
    teste_telefone_ambiguo_nao_decide()
    teste_telefone_curto_demais_nao_decide()
    teste_mesclar_marcacao()
    teste_data_desconhecida_com_marcacao_clara_ainda_conta()
    teste_marcacao_ambigua_vira_incerto_nao_conta_presenca_nem_falta()
    teste_celula_em_branco_nao_conta_como_aula()
    teste_alerta_faltas_finais_dispara_quando_falta_o_bloco_final()
    teste_alerta_faltas_finais_nao_dispara_com_poucas_aulas()
    teste_alerta_faltas_finais_nao_dispara_se_veio_a_uma_das_finais()
    teste_alerta_faltas_finais_funciona_com_colunas_sem_data()
    teste_faltou_ultimas_com_colunas_sem_data_mistura_com_datadas()
    teste_sugestao_academica_escala_no_alerta_de_faltas_finais()
    teste_sinal_forte_vence_contexto_financeiro()
    teste_regra_urgente_nunca_menciona_a_palavra_urgente_na_mensagem()
    teste_regra_urgente_sem_debito_nao_dispara()
    teste_regra_urgente_ja_em_turma_de_controle_nao_dispara()
    teste_nota_devedor_sem_nenhum_sinal_retorna_none()
    teste_nota_devedor_com_debito_fora_de_qualquer_turma_de_controle()
    teste_nota_devedor_com_debito_ja_na_turma_devedor()
    teste_nota_devedor_por_situacao_do_cadastro_sem_diferenca_financeira()
    teste_nota_devedor_so_na_turma_advogado_sem_debito_nem_status_pede_conferencia()
    teste_nota_devedor_com_debito_na_turma_advogado_diz_que_nao_esta_na_de_devedor()
    teste_eh_turma_infantil_reconhece_nomes_reais_biblia_3d()
    teste_eh_turma_infantil_nao_reconhece_turmas_de_adulto()
    teste_montar_relatorio_turma_infantil_marca_rotulo()
    teste_montar_relatorio_turma_categoriza_por_marca_do_nome()
    teste_montar_relatorio_turma_sem_alerta_de_funil_quando_proporcao_normal()
    teste_checagem_financeira_detecta_pagamento_por_tipo()
    teste_checagem_financeira_detecta_pagamento_por_texto_livre()
    teste_checagem_financeira_pagamento_sem_valor_em_reais_e_ignorado()
    teste_checagem_financeira_comentario_nao_financeiro_e_ignorado()
    teste_checagem_financeira_mesmo_pagamento_lancado_duas_vezes_nao_duplica()
    teste_checagem_financeira_valores_iguais_datas_proximas_e_duplicado_suspeito()
    teste_checagem_financeira_duplicado_detalha_quem_registrou()
    teste_checagem_financeira_pix_por_terceiro_e_sinalizado()
    teste_checagem_financeira_pix_pelo_diogenes_nao_sinaliza()
    teste_checagem_financeira_pagamento_boleto_por_terceiro_nao_sinaliza_pix()
    teste_checagem_financeira_pagamento_sem_autor_nao_quebra()
    teste_checagem_financeira_valores_iguais_datas_distantes_nao_e_duplicado()
    teste_checagem_financeira_debito_em_aberto_vira_inconsistencia()
    teste_checagem_financeira_sem_debito_nem_duplicidade_sem_inconsistencia()
    teste_checagem_financeira_data_ilegivel_nao_quebra_deteccao_de_duplicidade()
    teste_ja_tem_fechamento_reconhece_titulo_sem_prefixo()
    teste_ja_tem_fechamento_reconhece_titulo_com_prefixo_ia_ou_mod()
    teste_ja_tem_fechamento_nao_confunde_prefixo_com_outro_comentario()
    teste_eh_situacao_ref_reconhece_ref_isolado_e_combinado()
    teste_eh_situacao_ref_nao_confunde_com_outras_palavras()
    teste_nota_ref_faltas_finais_cita_a_modalidade_recebida()
    teste_faltou_tudo_nao_repete_linguagem_de_aulas_finais()
    teste_faltou_tudo_com_apenas_1_aula_ainda_e_possivel_abandono()
    teste_sugestao_academica_nao_repete_fato_do_resumo()
    teste_eh_nome_marcado_abandono_e_refazendo()
    teste_turmas_no_mesmo_mes_ano_acha_e_exclui_a_turma_atual()
    teste_turmas_no_mesmo_mes_ano_sem_data_referencia_nao_quebra()
    teste_nota_verificacao_abandono_confere_status_de_verdade()

    print(f"\n{'=' * 70}")
    print(f"Total OK: {_ok_count} | Total FALHOU: {len(_falhas)}")
    if _falhas:
        print("\nFalhas:")
        for nome, detalhe in _falhas:
            print(f"  - {nome}: {detalhe}")
        sys.exit(1)
    print("Nenhuma regressão detectada.")


if __name__ == "__main__":
    main()

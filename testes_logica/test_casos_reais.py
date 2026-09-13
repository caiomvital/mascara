"""
Testes de regressao pra bugs de INCONSISTENCIA/logica de negocio ja
encontrados ao vivo neste projeto (Normando, Vitor Fonseca Veloso, Ana
Luisa, o "flood" que contaminou a deteccao de advogado, etc.) - bem
diferente de testes_estresse/ (que testa concorrencia/robustez de rede).
Aqui testa as funcoes PURAS de decisao (reconciliacao_devedor.py,
fechamento_logic.py) contra casos fabricados que reproduzem o PADRAO
estrutural de cada bug real - nao precisa de Fuctura nenhum, nem real nem
falso.

Os nomes/dados sao FICTICIOS de proposito (ALUNO TESTE N) - o que importa
pro teste e o padrao (datas, titulos de comentario, valores), nao os dados
reais dos alunos que motivaram a descoberta original. Nunca colocar nome
real de aluno neste arquivo.

Como rodar:
    cd testes_logica
    python test_casos_reais.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fechamento_logic as logic  # noqa: E402
import reconciliacao_devedor as rec  # noqa: E402

_falhas = []
_ok_count = 0
HOJE = datetime(2026, 9, 5)  # data fixa - teste nao pode depender do dia em que roda


def relatar(nome, ok, detalhe=""):
    global _ok_count
    if ok:
        _ok_count += 1
        print(f"  OK     {nome}")
    else:
        _falhas.append((nome, detalhe))
        print(f"  FALHOU {nome}: {detalhe}")


def c(data, titulo, tipo, texto="", autor="FUNCIONARIO TESTE"):
    """Atalho pra montar um comentario no formato de comentarios_aluno()."""
    return {"id_acomp": "0", "data": data, "titulo": titulo, "tipo": tipo, "autor": autor, "texto": texto}


def perfil(nome, status, contratado, recebido, turmas_atuais=None):
    return {
        "nome": nome, "celular": "81999999999", "status": status,
        "turmas_atuais": turmas_atuais or [],
        "contratado": contratado, "recebido": recebido, "diferenca": contratado - recebido,
    }


def analisar(nome, status, contratado, recebido, comentarios, esta_em_controle=False, turmas_atuais=None, dias_maximos=None, matricula=None):
    p = perfil(nome, status, contratado, recebido, turmas_atuais)
    aluno_bruto = {"id_aluno": "0", "nome": nome, "esta_em_controle": esta_em_controle}
    kwargs = {"dias_maximos": dias_maximos} if dias_maximos is not None else {}
    if matricula is not None:
        kwargs["matricula"] = matricula
    return rec.analisar_aluno(None, aluno_bruto, data_execucao=HOJE, perfil=p, comentarios=comentarios, **kwargs)


# ---------------------------------------------------------------------------
# Caso 1 (padrao real: VITOR FONSECA VELOSO, id 8718, achado 2026-09-05) -
# uma matricula muito antiga que NUNCA virou contrato pago (so um convite
# de bolsa recusado) nao pode inflar a "antiguidade" da divida quando ha
# uma matricula paga de verdade bem mais recente. Tem que usar a mais
# RECENTE como referencia (max, nao min).
# ---------------------------------------------------------------------------
def teste_matricula_antiga_nao_confiavel():
    comentarios = [
        c("26/11/2018", "Matricula na Turma EX BOLSA I", "-Comentário", "convite de bolsa, aluno recusou"),
        c("26/06/2019", "Convite Bolsa", "-Comentário", "O aluno não está interessado."),
        c("30/04/2026", "ACADEMIA TESTE", "-Matricula", ""),
        c("30/04/2026", "PAGAMENTO CARTÃO", "-Pagamento Realizado", "Valor: 4.200,00"),
        c("13/08/2026", "TURMA TESTE 2", "-Matricula", ""),
    ]
    a = analisar("ALUNO TESTE 1", "Devedor", 4550.0, 4200.0, comentarios)
    relatar(
        "Caso 1: matrícula antiga sem contrato não deve inflar antiguidade da dívida",
        a["fonte_vencimento"] == "matricula"
        and a["vencimento_mais_antigo"] == datetime(2026, 8, 13)
        and a["caso"] == "nao_elegivel",
        f"fonte={a['fonte_vencimento']} vencimento={a['vencimento_mais_antigo']} caso={a['caso']} (esperado: matricula/13-08-2026/nao_elegivel)",
    )


# ---------------------------------------------------------------------------
# Caso 2 (padrao real: NORMANDO NOBLAT, achado ~2026-09-03) - titulo de
# comentario de geracao de boleto foge do padrao exato "gerar boletos"
# (aqui: "BOLETOS GERADOS E ENVIADOS") - o fallback nao pode cair numa
# matricula/reserva antiga so porque o titulo nao bateu no regex antigo.
# ---------------------------------------------------------------------------
def teste_titulo_boleto_fora_do_padrao():
    comentarios = [
        c("10/01/2020", "RESERVA META", "-Comentário", "reserva antiga de vaga, sem contrato"),
        c("20/08/2025", "BOLETOS GERADOS E ENVIADOS", "-Comentário", ""),
    ]
    a = analisar("ALUNO TESTE 2", "Devedor", 1000.0, 0.0, comentarios)
    relatar(
        "Caso 2: título de boleto fora do padrão exato ainda deve ser reconhecido",
        a["fonte_vencimento"] == "gerar_boletos" and a["vencimento_mais_antigo"] == datetime(2025, 8, 20),
        f"fonte={a['fonte_vencimento']} vencimento={a['vencimento_mais_antigo']} (esperado: gerar_boletos/20-08-2025)",
    )


# ---------------------------------------------------------------------------
# Caso 3 (padrao real: ANA LUISA SILVA RAMOS DE LIMA, id 46597, achado
# 2026-09-05) - cancelamento ANTES de usar o servico + mencao de estorno =
# provavelmente a escola quem deve dinheiro, nao o aluno. Nao pode sugerir
# cobranca.
# ---------------------------------------------------------------------------
def teste_estorno_pendente_nao_sugere_cobranca():
    comentarios = [
        c("20/08/2025", "GERAR BOLETOS", "Urgente", "gerar boletos todo dia 28"),
        c("29/08/2025", "TURMA TESTE", "-Matricula", ""),
        c("01/09/2025", "CANCELOU", "-Comentário",
          "pai pediu cancelamento, aluno nunca assistiu nenhuma aula, tem que fazer o estorno do valor pago"),
    ]
    a = analisar("ALUNO TESTE 3", "Devedor", 4524.0, 0.0, comentarios)
    relatar(
        "Caso 3: cancelamento + estorno pendente não deve sugerir matricular em Devedor/Pendência",
        a["caso"] == "possivel_estorno_pendente" and a["acao_sugerida"] is None,
        f"caso={a['caso']} acao_sugerida={a['acao_sugerida']} (esperado: possivel_estorno_pendente/None)",
    )


# ---------------------------------------------------------------------------
# Caso 4 (padrao real: flood de 04/09/2026 + caso ARLLAN id 28407, achado
# 2026-09-05) - o proprio comentario automatico de analise anterior
# menciona a palavra "advogado" ao explicar o caso - isso NAO pode ser lido
# como um sinal humano real de advogado numa rodada seguinte (loop de
# autocontaminacao).
# ---------------------------------------------------------------------------
def teste_nao_se_autocontamina_com_proprio_comentario():
    comentario_proprio = c(
        "04/09/2026", rec.ASSUNTO_ANALISE_AUTOMATICA, "Urgente",
        "Aluno tem sinal de já estar (ou já ter sido encaminhado) com o escritório de advocacia "
        "externo - nome, turma ou comentário menciona advogado/escritório - mesmo não constando na "
        "turma Aguardando Advogado no momento.",
        autor="Caio",
    )
    comentarios = [
        c("10/01/2024", "TURMA TESTE", "-Matricula", ""),
        c("10/01/2024", "CANCELOU", "-Comentário", "aluno abandonou o curso"),
        comentario_proprio,
    ]
    a = analisar("ALUNO TESTE 4", "Devedor", 500.0, 0.0, comentarios)
    relatar(
        "Caso 4: comentário automático próprio não pode virar sinal falso de advogado",
        a["ja_com_advogado"] is False and a["caso"] == "cancelou_mas_ainda_deve",
        f"ja_com_advogado={a['ja_com_advogado']} caso={a['caso']} (esperado: False/cancelou_mas_ainda_deve)",
    )


# ---------------------------------------------------------------------------
# Caso 5 - negacao de cancelamento nao pode contar como cancelamento.
# ---------------------------------------------------------------------------
def teste_negacao_de_cancelamento():
    casos = [
        "aluno não veio cancelar, disse que vai continuar",
        "ALUNA NÃO VAI MAIS CANCELAR, está com problema no trabalho",
        "o pai não quer mais cancelar a matrícula",
    ]
    for texto in casos:
        relatar(
            f'Caso 5: negação não deve marcar cancelamento ("{texto[:40]}...")',
            logic.houve_sinal_cancelamento(texto) is False,
            f"houve_sinal_cancelamento retornou True pra: {texto}",
        )


# ---------------------------------------------------------------------------
# Caso 6 - cancelamento de FORMA de pagamento (financeiro) nao e o aluno
# desistindo do curso.
# ---------------------------------------------------------------------------
def teste_cancelamento_financeiro_nao_e_desistencia():
    casos = [
        "pagamento recorrente cancelado, aluno vai pagar via pix agora",
        "troca da forma de pagamento, cartão anterior foi cancelado pelo banco",
    ]
    for texto in casos:
        relatar(
            f'Caso 6: cancelamento financeiro não deve marcar desistência ("{texto[:40]}...")',
            logic.houve_sinal_cancelamento(texto) is False,
            f"houve_sinal_cancelamento retornou True pra: {texto}",
        )


# ---------------------------------------------------------------------------
# Caso 7 - cancelamento real (positivo verdadeiro) tem que continuar
# detectado - o mesmo ajuste que corrigiu os falsos positivos nao pode ter
# quebrado a deteccao real.
# ---------------------------------------------------------------------------
def teste_cancelamento_real_detectado():
    casos = [
        "aluno abandonou o curso, sumiu há 2 meses",
        "aluno sumiu, não responde mais nenhum contato",
        "CANCELADO",
        "aluna desistiu do curso por motivos pessoais",
    ]
    for texto in casos:
        relatar(
            f'Caso 7: cancelamento real deve ser detectado ("{texto[:40]}...")',
            logic.houve_sinal_cancelamento(texto) is True,
            f"houve_sinal_cancelamento retornou False pra: {texto}",
        )


# ---------------------------------------------------------------------------
# Caso 8 - "Devedor" sem nenhuma divida real (diferenca = 0) e sem
# convenio - inconsistencia classica de cadastro, sugestao deve ser mover
# pra -Matriculado.
# ---------------------------------------------------------------------------
def teste_devedor_sem_divida_real():
    comentarios = [
        c("10/01/2020", "TURMA TESTE", "-Matricula", ""),
        c("10/01/2020", "BOLETO PAGO 1/1", "-Pagamento Realizado", "quitado"),
    ]
    a = analisar("ALUNO TESTE 8", "Devedor", 500.0, 500.0, comentarios)
    relatar(
        "Caso 8: Devedor sem dívida real deve sugerir mover para -Matriculado",
        a["caso"] == "devedor_sem_divida" and a["acao_sugerida"] == {"tipo": "mudar_status", "novo_status": "4", "novo_status_nome": "-Matriculado"},
        f"caso={a['caso']} acao_sugerida={a['acao_sugerida']}",
    )


# ---------------------------------------------------------------------------
# Caso 9 - cancelou E já quitou a dívida - sugestão deve ser mover pra
# Cancelado, não pra Devedor/Pendência.
# ---------------------------------------------------------------------------
def teste_cancelou_e_quitou():
    comentarios = [
        c("10/01/2020", "TURMA TESTE", "-Matricula", ""),
        c("10/01/2020", "CANCELOU", "-Comentário", "aluno abandonou o curso, já quitado"),
    ]
    a = analisar("ALUNO TESTE 9", "Devedor", 500.0, 500.0, comentarios)
    relatar(
        "Caso 9: cancelou e já quitou deve sugerir mover para Cancelado",
        a["caso"] == "cancelou_e_quitou" and a["acao_sugerida"] == {"tipo": "mudar_status", "novo_status": "5", "novo_status_nome": "Cancelado"},
        f"caso={a['caso']} acao_sugerida={a['acao_sugerida']}",
    )


# ---------------------------------------------------------------------------
# Caso 10 (controle - NÃO é bug) - nome com "(ADVOGADO)" tem que continuar
# sendo reconhecido como sinal REAL, pra garantir que a correção do Caso 4
# não suprimiu detecção legítima.
# ---------------------------------------------------------------------------
def teste_advogado_real_via_nome_continua_detectado():
    comentarios = [
        # 10/01/2024 (nao 2020!) - tem que ficar DENTRO da janela elegivel-e-
        # nao-prescrita (entre dias_minimos e dias_maximos/PRAZO_MAXIMO_DIAS
        # a partir de HOJE), senao vira "divida_prescrita" antes de chegar
        # na deteccao de advogado que este teste quer verificar.
        c("10/01/2024", "TURMA TESTE", "-Matricula", ""),
        c("10/01/2024", "CANCELOU", "-Comentário", "aluno abandonou o curso"),
    ]
    a = analisar("ALUNO TESTE 10 (ADVOGADO)", "Devedor", 500.0, 0.0, comentarios)
    relatar(
        "Caso 10 (controle): nome com (ADVOGADO) continua sendo sinal real",
        a["ja_com_advogado"] is True and a["caso"] == "advogado_sinalizado_sem_turma_atual",
        f"ja_com_advogado={a['ja_com_advogado']} caso={a['caso']} (esperado: True/advogado_sinalizado_sem_turma_atual)",
    )


# ---------------------------------------------------------------------------
# Caso 11 (padrao real: notado investigando o Caso 1/Vitor, achado de
# verdade so ao escrever este teste, 2026-09-05) - uma bolsa OFERECIDA E
# RECUSADA nao pode contar como sinal de convenio - so importa quando o
# aluno nao tem divida real (valor_final=0), senao passa despercebido.
# ---------------------------------------------------------------------------
def teste_bolsa_recusada_nao_e_convenio():
    comentarios = [
        c("10/01/2020", "TURMA TESTE", "-Matricula", ""),
        c("15/01/2020", "Convite Bolsa", "-Comentário", "O aluno não está interessado."),
    ]
    a = analisar("ALUNO TESTE 11", "Devedor", 500.0, 500.0, comentarios)
    relatar(
        "Caso 11: bolsa recusada não deve contar como convênio (deve cair em devedor_sem_divida)",
        a["caso"] == "devedor_sem_divida" and a["convenio"] is False,
        f"caso={a['caso']} convenio={a['convenio']} (esperado: devedor_sem_divida/False)",
    )


def teste_convenio_real_ainda_detectado():
    comentarios = [
        c("10/01/2020", "TURMA TESTE", "-Matricula", ""),
        c("15/01/2020", "SOBRE CONVENIO", "-Comentário", "aluno está em convênio com a empresa X, sem custo"),
    ]
    a = analisar("ALUNO TESTE 11B", "Devedor", 500.0, 500.0, comentarios)
    relatar(
        "Caso 11b (controle): convênio real continua detectado",
        a["caso"] == "convenio_ok" and a["convenio"] is True,
        f"caso={a['caso']} convenio={a['convenio']} (esperado: convenio_ok/True)",
    )


# ---------------------------------------------------------------------------
# Caso 12 (padrao real: "ANA CLARA", mencionado no historico do projeto) -
# resumir_comentarios nao pode citar o proprio comentario automatico de
# analise anterior como "ultimo comentario" - vira uma citacao recursiva
# de si mesmo em vez do ultimo comentario humano de verdade.
# ---------------------------------------------------------------------------
def teste_resumo_nao_cita_proprio_comentario_automatico():
    comentarios = [
        c("10/01/2024", "SOBRE O ALUNO", "-Comentário", "aluno sumiu, não responde mais contato"),
        c("04/09/2026", rec.ASSUNTO_ANALISE_AUTOMATICA, "Urgente", "Análise automática de reconciliação...", autor="Caio"),
    ]
    resumo = rec.resumir_comentarios(comentarios)
    relatar(
        "Caso 12: resumo não deve citar o próprio comentário automático como 'último'",
        rec.ASSUNTO_ANALISE_AUTOMATICA not in resumo and "sumiu" in resumo,
        f"resumo gerado: {resumo}",
    )


# ---------------------------------------------------------------------------
# Caso 13 - sobrenome comum nao pode "casar" dois alunos diferentes so
# porque o sobrenome bate - o primeiro nome tem que ser parecido tambem
# (protecao ja existente, testada aqui pra nunca regredir).
# ---------------------------------------------------------------------------
def teste_casar_nome_exige_primeiro_nome_parecido():
    roster = [{"nome": "PEDRO SILVA DOS SANTOS"}, {"nome": "MARIA SILVA DOS SANTOS"}]
    aluno, score = logic.casar_nome("JOAO SILVA DOS SANTOS", roster)
    relatar(
        "Caso 13: sobrenome comum não deve casar aluno errado sem o primeiro nome bater",
        aluno is None,
        f"casou incorretamente com: {aluno} (score={score})",
    )


def teste_casar_nome_funciona_com_pequena_variacao():
    roster = [{"nome": "JOAO SILVA DOS SANTOS"}, {"nome": "MARIA SILVA DOS SANTOS"}]
    aluno, score = logic.casar_nome("JOÃO SILVA DOS SANTOS", roster)
    relatar(
        "Caso 13b (controle): variação de acento ainda deve casar corretamente",
        aluno is not None and aluno["nome"] == "JOAO SILVA DOS SANTOS",
        f"aluno casado: {aluno} (score={score})",
    )


# ---------------------------------------------------------------------------
# Caso 14 - nome de turma com prefixo de pontuacao (".TURMA X", vindo de
# busca) tem que ser reconhecido como a mesma turma de um comentario manual
# sem o prefixo ("TURMA X") - senao ja_tem_fechamento nao reconhece um
# fechamento manual ja feito e deixa duplicar.
# ---------------------------------------------------------------------------
def teste_fechamento_reconhece_turma_com_ou_sem_prefixo():
    comentarios = [c("18/08/2026", "FECHAMENTO TURMA X 20/08/26", "-Comentário", "fechado manualmente")]
    relatar(
        "Caso 14: fechamento manual é reconhecido mesmo com prefixo de pontuação na turma",
        logic.ja_tem_fechamento(comentarios, ".TURMA X 20/08/26") is True,
        "ja_tem_fechamento não reconheceu a turma com prefixo '.'",
    )


# ---------------------------------------------------------------------------
# Caso 15 - variantes de formato de data (sem ano, ano com 2 digitos, sem
# zero a esquerda) tem que canonicalizar pra mesma string, senao a mesma
# aula conta como datas diferentes e infla a contagem de aulas assistidas.
# ---------------------------------------------------------------------------
def teste_normalizar_data_variantes():
    casos = [
        ("29/08", 2026, "29/08/2026"),
        ("29/08/26", None, "29/08/2026"),
        ("2/6/2026", None, "02/06/2026"),
        ("29/08/2026", None, "29/08/2026"),
    ]
    for entrada, ano_padrao, esperado in casos:
        resultado = logic.normalizar_data(entrada, ano_padrao=ano_padrao)
        relatar(
            f"Caso 15: normalizar_data({entrada!r}) == {esperado!r}",
            resultado == esperado,
            f"retornou {resultado!r}",
        )


# ---------------------------------------------------------------------------
# Caso 16 - fronteira exata de elegibilidade (dias_minimos) - classico erro
# de off-by-one. Vencimento EXATAMENTE nos dias_minimos deve ser elegivel
# (<=), um dia depois (mais recente) nao deve.
# ---------------------------------------------------------------------------
def teste_elegibilidade_fronteira_exata():
    hoje = datetime(2026, 9, 5)
    limite_exato = datetime(2025, 9, 5)  # exatamente 365 dias antes
    um_dia_depois = datetime(2025, 9, 6)  # 364 dias antes - nao deveria ser elegivel ainda
    relatar(
        "Caso 16: vencimento exatamente no limite de dias_minimos é elegível",
        rec.elegivel_por_prazo(limite_exato, hoje, 365) is True,
        "não considerou elegível no limite exato",
    )
    relatar(
        "Caso 16b: vencimento um dia mais recente que o limite NÃO é elegível ainda",
        rec.elegivel_por_prazo(um_dia_depois, hoje, 365) is False,
        "considerou elegível um dia antes da hora",
    )


# ---------------------------------------------------------------------------
# Caso 17 - prioridade de fontes: uma mencao EXPLICITA de "Vencimento em
# DD/MM/AAAA" tem que vencer mesmo contra um comentario de boleto/matricula
# mais recente - e a fonte mais confiavel e nao pode ser atropelada so por
# ser mais antiga cronologicamente.
# ---------------------------------------------------------------------------
def teste_prioridade_fonte_vencimento_explicito():
    comentarios = [
        c("10/01/2020", "PLANO DE PAGAMENTO", "-Comentário", "Vencimento em 20/01/2020 R$ 100,00"),
        c("15/08/2026", "GERAR BOLETOS", "Urgente", "gerar boletos todo dia 25"),
    ]
    vencimento, fonte = rec.vencimento_mais_antigo_em_aberto(comentarios)
    relatar(
        "Caso 17: menção explícita de vencimento vence mesmo contra sinal mais recente",
        fonte == "comentario_vencimento" and vencimento == datetime(2020, 1, 20),
        f"fonte={fonte} vencimento={vencimento} (esperado: comentario_vencimento/20-01-2020)",
    )


# ---------------------------------------------------------------------------
# Caso 18 - detectar_inconsistencias (a propria funcao de auditoria) tem
# que achar o padrao "fonte fraca" quando a analise caiu pra matricula mas
# ha um comentario de boleto/pagamento com titulo fora do padrao (nao bate
# em GERAR_BOLETOS_RE+GERAR_VERBO_RE) bem mais recente.
# ---------------------------------------------------------------------------
def teste_auditoria_acha_fonte_fraca():
    comentarios = [
        c("10/01/2020", "TURMA TESTE", "-Matricula", ""),
        c("15/08/2026", "BOLETO ATRASADO", "-Comentário", "cobrar por whatsapp"),  # nao bate GERAR_VERBO_RE
    ]
    a = analisar("ALUNO TESTE 18", "Devedor", 500.0, 0.0, comentarios)
    achados = rec.detectar_inconsistencias(a, comentarios)
    relatar(
        "Caso 18: auditoria detecta fonte de vencimento fraca com sinal mais recente não capturado",
        any("mais fraca" in ach for ach in achados),
        f"fonte={a['fonte_vencimento']} achados={achados}",
    )


def teste_auditoria_nao_alarma_caso_correto():
    """Controle: o Caso 1 (matrícula antiga sem contrato) já resolve
    corretamente pra fonte mais recente - a auditoria não pode gerar
    nenhum achado de 'fonte fraca' aqui (falso alarme)."""
    comentarios = [
        c("26/11/2018", "Matricula Antiga", "-Comentário", "tentativa antiga sem contrato, aluno não teve interesse"),
        c("30/04/2026", "ACADEMIA TESTE", "-Matricula", ""),
        c("30/04/2026", "PAGAMENTO CARTÃO", "-Pagamento Realizado", "Valor: 4.200,00"),
        c("13/08/2026", "TURMA TESTE 2", "-Matricula", ""),
    ]
    a = analisar("ALUNO TESTE 18B", "Devedor", 4550.0, 4200.0, comentarios)
    achados = rec.detectar_inconsistencias(a, comentarios)
    relatar(
        "Caso 18b (controle): caso já corrigido não gera falso alarme de auditoria",
        achados == [],
        f"achados inesperados: {achados}",
    )


# ---------------------------------------------------------------------------
# Caso 19 (pedido explicito do usuario, 2026-09-08, depois do flood) - o
# comentario automatico da reconciliacao NUNCA pode gravar tipo="2"
# (Urgente) no Fuctura, mesmo quando analise["urgente"] e True (a maioria
# dos casos) - foi exatamente esse mecanismo que causou o flood de
# 04/09/2026. O sinalizador 'urgente' continua existindo pra destaque
# visual na tela, so nao decide mais o tipo gravado.
# ---------------------------------------------------------------------------
def teste_comentario_nunca_grava_urgente_mesmo_quando_marcado_urgente():
    comentarios = [
        # 10/01/2024 (nao 2020!) - precisa ficar dentro da janela elegivel-e-
        # nao-prescrita, senao vira "divida_prescrita" (urgente=False nesse
        # caso) antes de chegar no caso que este teste quer verificar.
        c("10/01/2024", "TURMA TESTE", "-Matricula", ""),
        c("10/01/2024", "CANCELOU", "-Comentário", "aluno abandonou o curso"),
    ]
    a = analisar("ALUNO TESTE 19", "Devedor", 500.0, 0.0, comentarios)
    assunto, tipo, texto = rec.montar_comentario_analise(a)
    relatar(
        "Caso 19: comentário de análise nunca grava tipo Urgente, mesmo com analise['urgente']=True",
        a["urgente"] is True and tipo == "3",
        f"analise['urgente']={a['urgente']} tipo_gravado={tipo!r} (esperado: True/'3')",
    )


# ---------------------------------------------------------------------------
# Casos 20-24 (pedido explicito do usuario, 2026-09-09) - divida com
# vencimento mais antigo alem do prazo legal de cobranca (5 anos /
# PRAZO_MAXIMO_DIAS) vira um caso proprio "divida_prescrita", com
# prioridade sobre QUALQUER outro caso (mesmo ja em turma de controle) e
# sem sugerir nenhuma acao - nao faz sentido continuar cobrando algo que
# provavelmente ja prescreveu.
# ---------------------------------------------------------------------------
def teste_divida_muito_antiga_vira_prescrita():
    comentarios = [c("10/01/2018", "TURMA ANTIGA", "-Matricula", "")]  # ~8,5 anos antes de HOJE
    a = analisar("ALUNO TESTE 20", "Devedor", 500.0, 0.0, comentarios)
    relatar(
        "Caso 20: dívida com vencimento além do prazo legal (5 anos) vira 'divida_prescrita', sem ação sugerida",
        a["caso"] == "divida_prescrita" and a["acao_sugerida"] is None and a["prescrita"] is True,
        f"caso={a['caso']!r} acao_sugerida={a['acao_sugerida']} prescrita={a.get('prescrita')}",
    )


def teste_divida_prescrita_tem_prioridade_sobre_ja_em_controle():
    comentarios = [c("10/01/2018", "TURMA ANTIGA", "-Matricula", "")]
    a = analisar("ALUNO TESTE 21", "Devedor", 500.0, 0.0, comentarios, esta_em_controle=True)
    relatar(
        "Caso 21: dívida prescrita tem prioridade mesmo se o aluno já está em turma de controle",
        a["caso"] == "divida_prescrita",
        f"caso={a['caso']!r} (esperado: divida_prescrita, mesmo com esta_em_controle=True)",
    )


def teste_divida_dentro_do_prazo_nao_vira_prescrita():
    comentarios = [c("10/08/2024", "TURMA RECENTE", "-Matricula", "")]  # ~1 ano antes de HOJE
    a = analisar("ALUNO TESTE 22", "Devedor", 500.0, 0.0, comentarios)
    relatar(
        "Caso 22: dívida dentro do prazo legal continua no fluxo normal (não vira prescrita)",
        a["caso"] == "devedor_sem_turma" and a["prescrita"] is False,
        f"caso={a['caso']!r} prescrita={a.get('prescrita')} (esperado: devedor_sem_turma/False)",
    )


def teste_prazo_maximo_ajustavel_pelo_usuario():
    comentarios = [c("10/08/2024", "TURMA RECENTE", "-Matricula", "")]  # ~1 ano antes de HOJE
    # com um prazo maximo bem curto (30 dias), essa mesma divida de ~1 ano
    # ja conta como "prescrita" - confirma que o parametro e respeitado,
    # nao so o padrao fixo de 1825 dias.
    a = analisar("ALUNO TESTE 23", "Devedor", 500.0, 0.0, comentarios, dias_maximos=30)
    relatar(
        "Caso 23: dias_maximos é ajustável - um prazo mais curto pega dívida que o padrão não pegaria",
        a["caso"] == "divida_prescrita",
        f"caso={a['caso']!r} (esperado: divida_prescrita com dias_maximos=30)",
    )


def teste_texto_divida_prescrita_formata_dias_maximos_corretamente():
    comentarios = [c("10/01/2018", "TURMA ANTIGA", "-Matricula", "")]
    a = analisar("ALUNO TESTE 24", "Devedor", 500.0, 0.0, comentarios)
    assunto, tipo, texto = rec.montar_comentario_analise(a)
    relatar(
        "Caso 24: texto do comentário de análise formata dias_maximos corretamente (sem sobrar '{dias_maximos}' literal)",
        "{dias_maximos}" not in texto and "1825" in texto and tipo == "3",
        f"texto: {texto!r}",
    )


def teste_matricula_passa_direto_pra_analise_sem_afetar_decisao():
    # pedido do usuario (2026-09-12): a tela deve mostrar a matricula, nao o
    # id_aluno interno - so exibicao, entao repassar (ou nao passar) a
    # matricula nao pode mudar NADA na decisao.
    comentarios = [c("10/01/2018", "COMENTARIO", "-Comentário", "")]
    sem_matricula = analisar("ALUNO TESTE 25", "Devedor", 500.0, 0.0, comentarios)
    com_matricula = analisar("ALUNO TESTE 25", "Devedor", 500.0, 0.0, comentarios, matricula="12345")
    relatar(
        "matricula default vem vazia quando não informada",
        sem_matricula["matricula"] == "",
        f"matricula: {sem_matricula['matricula']!r}",
    )
    relatar(
        "matricula informada é repassada tal qual, sem mudar o caso/ação decidida",
        com_matricula["matricula"] == "12345" and com_matricula["caso"] == sem_matricula["caso"],
        f"matricula: {com_matricula['matricula']!r}, caso: {com_matricula['caso']!r}",
    )


def main():
    print("Rodando testes de regressão de lógica de negócio (sem Fuctura, nem real nem falso)...\n")
    teste_matricula_antiga_nao_confiavel()
    teste_titulo_boleto_fora_do_padrao()
    teste_estorno_pendente_nao_sugere_cobranca()
    teste_nao_se_autocontamina_com_proprio_comentario()
    teste_negacao_de_cancelamento()
    teste_cancelamento_financeiro_nao_e_desistencia()
    teste_cancelamento_real_detectado()
    teste_devedor_sem_divida_real()
    teste_cancelou_e_quitou()
    teste_advogado_real_via_nome_continua_detectado()
    teste_bolsa_recusada_nao_e_convenio()
    teste_convenio_real_ainda_detectado()
    teste_resumo_nao_cita_proprio_comentario_automatico()
    teste_casar_nome_exige_primeiro_nome_parecido()
    teste_casar_nome_funciona_com_pequena_variacao()
    teste_fechamento_reconhece_turma_com_ou_sem_prefixo()
    teste_normalizar_data_variantes()
    teste_elegibilidade_fronteira_exata()
    teste_prioridade_fonte_vencimento_explicito()
    teste_auditoria_acha_fonte_fraca()
    teste_auditoria_nao_alarma_caso_correto()
    teste_comentario_nunca_grava_urgente_mesmo_quando_marcado_urgente()
    teste_divida_muito_antiga_vira_prescrita()
    teste_divida_prescrita_tem_prioridade_sobre_ja_em_controle()
    teste_divida_dentro_do_prazo_nao_vira_prescrita()
    teste_prazo_maximo_ajustavel_pelo_usuario()
    teste_texto_divida_prescrita_formata_dias_maximos_corretamente()
    teste_matricula_passa_direto_pra_analise_sem_afetar_decisao()

    print(f"\n{'=' * 70}")
    print(f"Total OK: {_ok_count} | Total FALHOU: {len(_falhas)}")
    if _falhas:
        print("\nFalhas:")
        for nome, detalhe in _falhas:
            print(f"  - {nome}: {detalhe}")
        sys.exit(1)
    print("Nenhuma regressão detectada nos casos conhecidos.")


if __name__ == "__main__":
    main()

"""
Testes de regressao pra reconciliacao_devedor.py (montagem de comentario,
mesclagem da matricula com a analise em aplicar_acao, formatacao com <br>) -
sem rede nenhuma, com um FucturaClient falso.

Como rodar:
    cd testes_logica
    python test_reconciliacao_devedor.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reconciliacao_devedor as rec  # noqa: E402

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


def c(id_acomp, data, titulo, tipo, texto=""):
    return {"id_acomp": id_acomp, "data": data, "titulo": titulo, "tipo": tipo, "texto": texto}


class _ClienteFake:
    """FucturaClient falso: comentarios_aluno devolve uma lista fixa;
    editar_comentario/gravar_comentario/atualizar_status_aluno so registram
    a chamada (num dict) pra o teste inspecionar depois - nunca tocam rede."""

    def __init__(self, comentarios):
        self._comentarios = comentarios
        self.chamadas_editar = []
        self.chamadas_gravar = []
        self.chamadas_status = []

    def comentarios_aluno(self, id_aluno):
        return self._comentarios

    def editar_comentario(self, id_aluno, id_acomp, assunto, tipo, descricao, turma_id=None):
        self.chamadas_editar.append({
            "id_aluno": id_aluno, "id_acomp": id_acomp, "assunto": assunto,
            "tipo": tipo, "descricao": descricao, "turma_id": turma_id,
        })
        return 200

    def gravar_comentario(self, id_aluno, assunto, tipo, descricao, turma_id=None,
                           valor_contratado="0,00", forma_pagamento="---"):
        self.chamadas_gravar.append({
            "id_aluno": id_aluno, "assunto": assunto, "tipo": tipo,
            "descricao": descricao, "turma_id": turma_id,
        })
        return 200

    def atualizar_status_aluno(self, id_aluno, novo_status):
        self.chamadas_status.append({"id_aluno": id_aluno, "novo_status": novo_status})
        return 200


def _analise_matricular(id_aluno="1", turma_id="1138", turma_nome="Devedor/Pendência"):
    return {
        "id_aluno": id_aluno,
        "acao_sugerida": {"tipo": "matricular_turma", "turma_id": turma_id, "turma_nome": turma_nome},
    }


# ---------------------------------------------------------------------------
# montar_comentario_analise - formatacao pedida pelo usuario (2026-09-08):
# <br> pra quebra de linha (o Fuctura renderiza de verdade, nao escapa
# HTML), 3x <br> (SEPARADOR_BLOCO) separando os DADOS da analise do RESUMO
# do historico.
# ---------------------------------------------------------------------------
def teste_montar_comentario_usa_br_e_separador_de_bloco():
    analise = {
        "vencimento_mais_antigo": None, "fonte_vencimento": "matricula",
        "valor_final": 500.0, "caso": "consistente",
        "resumo_comentarios": "3 comentário(s) no histórico.",
    }
    assunto, tipo, texto = rec.montar_comentario_analise(analise)
    relatar(
        "montar_comentario_analise usa <br> como quebra de linha (não espaço)",
        "<br>" in texto and " Valor final" not in texto,
        f"texto: {texto!r}",
    )
    relatar(
        "montar_comentario_analise separa dados x resumo com 3x <br> (SEPARADOR_BLOCO)",
        rec.SEPARADOR_BLOCO in texto and texto.count(rec.SEPARADOR_BLOCO) == 1,
        f"texto: {texto!r}",
    )
    relatar(
        "montar_comentario_analise nunca grava tipo Urgente (regra já travada, aqui só confere que não regrediu)",
        tipo == "3",
        f"tipo: {tipo!r}",
    )


# ---------------------------------------------------------------------------
# aplicar_acao (matricular_turma) - pedido do usuario (2026-09-08): mesclar
# a matricula no comentario de analise já gravado (título ganha ' -- IA --',
# vira tipo '15', texto da análise + SEPARADOR_BLOCO + info da matrícula) em
# vez de gravar um segundo comentário duplicado.
# ---------------------------------------------------------------------------
def teste_aplicar_acao_mescla_na_analise_existente():
    comentarios = [
        c("100", "10/01/2026", rec.ASSUNTO_ANALISE_AUTOMATICA, "-Comentário", "Análise automática...<br>Resumo: nada."),
    ]
    client = _ClienteFake(comentarios)
    analise = _analise_matricular()
    sucesso, descricao = rec.aplicar_acao(client, analise)
    relatar(
        "aplicar_acao(matricular_turma) com análise prévia: sucesso e edita (não cria comentário novo)",
        sucesso and len(client.chamadas_editar) == 1 and not client.chamadas_gravar,
        f"sucesso={sucesso} editar={client.chamadas_editar} gravar={client.chamadas_gravar}",
    )
    chamada = client.chamadas_editar[0]
    relatar(
        "aplicar_acao mesclado: edita o MESMO id_acomp da análise, tipo vira '15' (-Matricula), turma_id correto",
        chamada["id_acomp"] == "100" and chamada["tipo"] == "15" and chamada["turma_id"] == "1138",
        f"chamada: {chamada}",
    )
    relatar(
        "aplicar_acao mesclado: título ganha o sufixo ' -- IA --' ao final",
        chamada["assunto"] == rec.ASSUNTO_ANALISE_AUTOMATICA + " -- IA --",
        f"assunto: {chamada['assunto']!r}",
    )
    relatar(
        "aplicar_acao mesclado: texto preserva a análise original + SEPARADOR_BLOCO + info da matrícula",
        chamada["descricao"].startswith("Análise automática...<br>Resumo: nada." + rec.SEPARADOR_BLOCO)
        and "Matriculado após confirmação manual" in chamada["descricao"],
        f"descricao: {chamada['descricao']!r}",
    )


def teste_aplicar_acao_sem_analise_previa_cai_no_comportamento_antigo():
    client = _ClienteFake(comentarios=[])  # nenhum comentário de análise gravado ainda
    analise = _analise_matricular()
    sucesso, descricao = rec.aplicar_acao(client, analise)
    relatar(
        "aplicar_acao sem análise prévia: sucesso e grava um comentário NOVO (fallback), não edita nada",
        sucesso and len(client.chamadas_gravar) == 1 and not client.chamadas_editar,
        f"sucesso={sucesso} gravar={client.chamadas_gravar} editar={client.chamadas_editar}",
    )
    relatar(
        "aplicar_acao fallback: continua indo pra turma certa com tipo '15'",
        client.chamadas_gravar[0]["turma_id"] == "1138" and client.chamadas_gravar[0]["tipo"] == "15",
        f"gravar: {client.chamadas_gravar}",
    )


def teste_aplicar_acao_mudar_status_nao_mexe_em_comentario():
    client = _ClienteFake(comentarios=[c("1", "01/01/2026", rec.ASSUNTO_ANALISE_AUTOMATICA, "-Comentário", "x")])
    analise = {"id_aluno": "1", "acao_sugerida": {"tipo": "mudar_status", "novo_status": "4", "novo_status_nome": "-Matriculado"}}
    sucesso, descricao = rec.aplicar_acao(client, analise)
    relatar(
        "aplicar_acao(mudar_status) não toca em comentário nenhum (nem editar, nem gravar)",
        sucesso and not client.chamadas_editar and not client.chamadas_gravar and len(client.chamadas_status) == 1,
        f"editar={client.chamadas_editar} gravar={client.chamadas_gravar} status={client.chamadas_status}",
    )


# ---------------------------------------------------------------------------
# Nao reabrir o bug do "advogado" (corrigido em 2026-09-05, ver
# ja_com_advogado): depois que aplicar_acao mescla o titulo pra
# 'ASSUNTO_ANALISE_AUTOMATICA -- IA --', esse comentario continua sendo
# reconhecido como AUTOMATICO e ignorado pelos detectores - mesmo que o
# texto dele mencione "advogado" (o texto_caso de alguns casos menciona).
# ---------------------------------------------------------------------------
def teste_resumo_debito_flood_e_reconhecido_como_automatico_mesmo_com_prefixo():
    # achado real 2026-09-11: sem isto, um "Resumo do Débito" já gravado
    # virava "último comentário humano" na próxima vez que
    # resumir_comentarios rodasse pro mesmo aluno - citando a si mesmo.
    for titulo in (
        rec.ASSUNTO_RESUMO_DEBITO_FLOOD,
        rec.PREFIXO_TEXTO_ORIGINAL + rec.ASSUNTO_RESUMO_DEBITO_FLOOD,
        rec.PREFIXO_TEXTO_EDITADO + rec.ASSUNTO_RESUMO_DEBITO_FLOOD,
    ):
        relatar(
            f"_e_comentario_automatico reconhece {titulo!r} (resumo do débito do flood)",
            rec._e_comentario_automatico(titulo),
            f"_e_comentario_automatico({titulo!r}) == False",
        )


def teste_titulo_mesclado_ainda_e_reconhecido_como_automatico():
    titulo_mesclado = rec.ASSUNTO_ANALISE_AUTOMATICA + " -- IA --"
    relatar(
        "título com sufixo ' -- IA --' ainda é reconhecido como comentário de análise (_e_titulo_analise)",
        rec._e_titulo_analise(titulo_mesclado),
        f"_e_titulo_analise({titulo_mesclado!r}) == False",
    )
    relatar(
        "título com sufixo ' -- IA --' ainda é ignorado nos detectores (_e_comentario_automatico)",
        rec._e_comentario_automatico(titulo_mesclado),
        f"_e_comentario_automatico({titulo_mesclado!r}) == False",
    )

    comentarios = [
        c("1", "01/01/2026", titulo_mesclado, "-Matricula", "menciona advogado no texto da análise, mas é nosso próprio comentário"),
    ]
    resultado = rec.ja_com_advogado("ALUNO TESTE", turmas_atuais=[], comentarios=comentarios)
    relatar(
        "ja_com_advogado ignora o próprio comentário mesclado (mesmo citando 'advogado') - não reabre o bug do flood",
        resultado is False,
        f"ja_com_advogado retornou {resultado!r} (esperado False)",
    )


def teste_flood_neutralizado_nao_conta_como_analise_existente():
    # achado real: um comentario neutralizado (--Removido Por Flood--) NAO
    # deve contar como "ja existe analise pra este aluno" - ele representa
    # uma analise ANULADA, nao uma que ainda vale.
    comentarios = [c("1", "04/09/2026", "--Removido Por Flood--", "-Comentário", "--Removido Por Flood--")]
    data = rec.data_ultima_analise_automatica(comentarios)
    relatar(
        "comentário neutralizado (--Removido Por Flood--) não conta como análise existente",
        data is None,
        f"data_ultima_analise_automatica retornou {data!r} (esperado None)",
    )


def teste_titulo_mesclado_conta_como_analise_existente():
    titulo_mesclado = rec.ASSUNTO_ANALISE_AUTOMATICA + " -- IA --"
    comentarios = [c("1", "05/09/2026", titulo_mesclado, "-Matricula", "x")]
    data = rec.data_ultima_analise_automatica(comentarios)
    relatar(
        "comentário de análise já mesclado (' -- IA --') CONTA como análise existente pra este aluno",
        data == "05/09/2026",
        f"data_ultima_analise_automatica retornou {data!r} (esperado '05/09/2026')",
    )


# ---------------------------------------------------------------------------
# Pedido do usuario (2026-09-09): funcionario pode editar o texto sugerido
# antes de gravar - registrar_analise() marca o assunto com -IA-/-MOD-
# conforme o texto gravado for exatamente a sugestao ou tenha sido alterado.
# ---------------------------------------------------------------------------
def _analise_simples(id_aluno="1"):
    return {
        "id_aluno": id_aluno, "acao_sugerida": None,
        "vencimento_mais_antigo": None, "fonte_vencimento": "matricula",
        "valor_final": 500.0, "caso": "consistente",
        "resumo_comentarios": "3 comentário(s) no histórico.",
    }


def teste_registrar_analise_sem_texto_editado_marca_prefixo_ia():
    client = _ClienteFake(comentarios=[])
    resultado = rec.registrar_analise(client, _analise_simples())
    chamada = client.chamadas_gravar[0]
    relatar(
        "registrar_analise sem texto_editado: assunto ganha o prefixo '-IA- '",
        chamada["assunto"].startswith(rec.PREFIXO_TEXTO_ORIGINAL + rec.ASSUNTO_ANALISE_AUTOMATICA),
        f"assunto: {chamada['assunto']!r}",
    )
    relatar(
        "registrar_analise sem texto_editado: resultado reporta editado=False",
        resultado["editado"] is False,
        f"resultado: {resultado}",
    )


def teste_registrar_analise_com_texto_igual_ao_sugerido_continua_ia():
    client = _ClienteFake(comentarios=[])
    analise = _analise_simples()
    _, _, texto_sugerido = rec.montar_comentario_analise(analise)
    # o mesmo texto, so com espaco a mais nas pontas - nao conta como editado
    resultado = rec.registrar_analise(client, analise, texto_editado="  " + texto_sugerido + "  ")
    chamada = client.chamadas_gravar[0]
    relatar(
        "registrar_analise com texto_editado IGUAL ao sugerido (só espaço a mais): continua '-IA-', não '-MOD-'",
        chamada["assunto"].startswith(rec.PREFIXO_TEXTO_ORIGINAL) and resultado["editado"] is False,
        f"assunto: {chamada['assunto']!r} | resultado: {resultado}",
    )


def teste_registrar_analise_com_texto_diferente_marca_prefixo_mod():
    client = _ClienteFake(comentarios=[])
    analise = _analise_simples()
    texto_editado = "Texto reescrito à mão pelo funcionário, com um detalhe que a análise automática não capturou."
    resultado = rec.registrar_analise(client, analise, texto_editado=texto_editado)
    chamada = client.chamadas_gravar[0]
    relatar(
        "registrar_analise com texto_editado DIFERENTE: assunto ganha o prefixo '-MOD- '",
        chamada["assunto"].startswith(rec.PREFIXO_TEXTO_EDITADO + rec.ASSUNTO_ANALISE_AUTOMATICA),
        f"assunto: {chamada['assunto']!r}",
    )
    relatar(
        "registrar_analise com texto_editado DIFERENTE: grava o texto editado (não o sugerido)",
        chamada["descricao"] == texto_editado,
        f"descricao gravada: {chamada['descricao']!r}",
    )
    relatar(
        "registrar_analise com texto_editado DIFERENTE: resultado reporta editado=True",
        resultado["editado"] is True,
        f"resultado: {resultado}",
    )


def teste_titulo_com_prefixo_ia_ou_mod_ainda_e_reconhecido_como_analise():
    for prefixo in (rec.PREFIXO_TEXTO_ORIGINAL, rec.PREFIXO_TEXTO_EDITADO):
        titulo = prefixo + rec.ASSUNTO_ANALISE_AUTOMATICA
        relatar(
            f"título com prefixo {prefixo!r} ainda é reconhecido como comentário de análise (_e_titulo_analise)",
            rec._e_titulo_analise(titulo),
            f"_e_titulo_analise({titulo!r}) == False",
        )
        comentarios = [c("1", "06/09/2026", titulo, "-Comentário", "x")]
        data = rec.data_ultima_analise_automatica(comentarios)
        relatar(
            f"título com prefixo {prefixo!r} continua contando como análise existente pra avisar antes de duplicar",
            data == "06/09/2026",
            f"data_ultima_analise_automatica retornou {data!r} (esperado '06/09/2026')",
        )


def main():
    print("Rodando testes de reconciliacao_devedor.py (sem rede nenhuma)...\n")
    teste_montar_comentario_usa_br_e_separador_de_bloco()
    teste_aplicar_acao_mescla_na_analise_existente()
    teste_aplicar_acao_sem_analise_previa_cai_no_comportamento_antigo()
    teste_aplicar_acao_mudar_status_nao_mexe_em_comentario()
    teste_resumo_debito_flood_e_reconhecido_como_automatico_mesmo_com_prefixo()
    teste_titulo_mesclado_ainda_e_reconhecido_como_automatico()
    teste_flood_neutralizado_nao_conta_como_analise_existente()
    teste_titulo_mesclado_conta_como_analise_existente()
    teste_registrar_analise_sem_texto_editado_marca_prefixo_ia()
    teste_registrar_analise_com_texto_igual_ao_sugerido_continua_ia()
    teste_registrar_analise_com_texto_diferente_marca_prefixo_mod()
    teste_titulo_com_prefixo_ia_ou_mod_ainda_e_reconhecido_como_analise()

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

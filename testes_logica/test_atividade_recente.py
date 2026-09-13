"""
Testes de regressao pra _montar_atividade_recente() (app.py) - painel novo
na tela inicial (comentarios Urgentes recentes + matriculas do dia, em todo
o sistema). Sem rede nenhuma, com um FucturaClient falso.

Como rodar:
    cd testes_logica
    python test_atividade_recente.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
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


def c(id_acomp, data, titulo, texto=""):
    return {"id_acomp": id_acomp, "data": data, "titulo": titulo, "tipo": "Urgente", "texto": texto}


class _ClienteFake:
    """FucturaClient falso: buscar_comentarios_por_tipo devolve listas fixas
    por tipo (dict tipo -> lista); resolver_id_aluno_por_acomp resolve por
    um dicionario id_acomp -> id_aluno.

    Reproduz o comportamento real (achado 2026-09-10): o endpoint devolve
    do MAIS ANTIGO pro mais recente e ignora o sSortDir - entao aqui a
    lista fixa e sempre entregue em ordem crescente de data, e
    recentes_primeiro=True devolve invertida (mais recente primeiro), que e
    exatamente o que _montar_atividade_recente pede."""

    def __init__(self, por_tipo, id_aluno_por_acomp):
        self._por_tipo = por_tipo
        self._id_aluno_por_acomp = id_aluno_por_acomp

    def buscar_comentarios_por_tipo(self, tipo, termo="", max_registros=5000,
                                     tamanho_pagina=500, recentes_primeiro=False):
        def _d(x):
            try:
                return datetime.strptime(x["data"], "%d/%m/%Y")
            except (ValueError, TypeError):
                return datetime.min
        lista = sorted(self._por_tipo.get(tipo, []), key=_d)  # crescente, como o painel real
        if recentes_primeiro:
            lista = list(reversed(lista[-max_registros:]))
        return lista

    def resolver_id_aluno_por_acomp(self, id_acomp):
        return self._id_aluno_por_acomp.get(id_acomp)


HOJE_DT = datetime.now()
HOJE = HOJE_DT.strftime("%d/%m/%Y")
RECENTE = (HOJE_DT - timedelta(days=10)).strftime("%d/%m/%Y")
RECENTE_2 = (HOJE_DT - timedelta(days=3)).strftime("%d/%m/%Y")
MUITO_ANTIGO = (HOJE_DT - timedelta(days=400)).strftime("%d/%m/%Y")
ALEM_DA_JANELA = (HOJE_DT - timedelta(days=120)).strftime("%d/%m/%Y")  # > 90 dias


# ---------------------------------------------------------------------------
# Urgentes: so sinal humano de verdade - filtra fora qualquer comentario com
# titulo do proprio sistema (achado real 2026-09-09: confirmamos ao vivo que
# hoje sao 0 no Fuctura real, mas o filtro fica por seguranca).
# ---------------------------------------------------------------------------
def teste_urgentes_filtra_titulo_automatico_do_proprio_sistema():
    urgentes = [
        c("1", RECENTE, "ENVIAR CERTIFICADO", "texto humano"),
        c("2", RECENTE, rec.ASSUNTO_ANALISE_AUTOMATICA, "sobra de antes da correção"),
        c("3", RECENTE, rec.ASSUNTO_ANALISE_AUTOMATICA + " -- IA --", "idem, já mesclado"),
    ]
    client = _ClienteFake({"2": urgentes}, {"1": "100"})
    resultado = app._montar_atividade_recente(client)
    relatar(
        "urgentes exclui comentários automáticos do próprio sistema (título de análise)",
        len(resultado["urgentes"]) == 1 and resultado["urgentes"][0]["id_acomp"] == "1",
        f"urgentes: {resultado['urgentes']}",
    )


def teste_urgentes_resolve_id_aluno():
    urgentes = [c("1", RECENTE, "ENVIAR CERTIFICADO")]
    client = _ClienteFake({"2": urgentes}, {"1": "12345"})
    resultado = app._montar_atividade_recente(client)
    relatar(
        "urgentes resolve o id_aluno de cada comentário via resolver_id_aluno_por_acomp",
        resultado["urgentes"][0]["id_aluno"] == "12345",
        f"resultado: {resultado['urgentes']}",
    )


def teste_urgentes_respeita_limite():
    urgentes = [c(str(i), RECENTE, f"COMENTARIO {i}") for i in range(30)]
    client = _ClienteFake({"2": urgentes}, {})
    resultado = app._montar_atividade_recente(client, limite_urgentes=5)
    relatar(
        "urgentes respeita o limite pedido, mesmo com mais resultados disponíveis",
        len(resultado["urgentes"]) == 5,
        f"quantidade: {len(resultado['urgentes'])}",
    )


# ---------------------------------------------------------------------------
# Urgentes: "atividade RECENTE" - achado real 2026-09-10 (a tela mostrou
# comentario Urgente de 2017). Corta o que e mais velho que a janela
# (padrao 90 dias) e ordena por data de verdade, mais recente primeiro.
# ---------------------------------------------------------------------------
def teste_urgentes_ignora_comentario_antigo_demais():
    urgentes = [
        c("velho", MUITO_ANTIGO, "ENVIAR CERTIFICADO 2017"),
        c("fora_janela", ALEM_DA_JANELA, "ACOMPANHAR ALUNO"),
        c("novo", RECENTE, "CANCELAMENTO"),
    ]
    client = _ClienteFake({"2": urgentes}, {})
    resultado = app._montar_atividade_recente(client)
    ids = [u["id_acomp"] for u in resultado["urgentes"]]
    relatar(
        "urgentes corta o que é mais velho que a janela de dias (só 'novo' sobra)",
        ids == ["novo"],
        f"ids retornados: {ids}",
    )


def teste_urgentes_janela_ajustavel():
    urgentes = [c("fora_janela", ALEM_DA_JANELA, "ACOMPANHAR ALUNO")]  # ~120 dias
    client = _ClienteFake({"2": urgentes}, {})
    fechado = app._montar_atividade_recente(client, dias_urgente=90)
    aberto = app._montar_atividade_recente(client, dias_urgente=365)
    relatar(
        "janela de dias é ajustável: 90 dias esconde um comentário de ~120 dias, 365 dias mostra",
        len(fechado["urgentes"]) == 0 and len(aberto["urgentes"]) == 1,
        f"90d={len(fechado['urgentes'])} 365d={len(aberto['urgentes'])}",
    )


def teste_urgentes_ordenado_do_mais_recente_pro_mais_antigo():
    urgentes = [
        c("a", (HOJE_DT - timedelta(days=40)).strftime("%d/%m/%Y"), "A"),
        c("b", (HOJE_DT - timedelta(days=5)).strftime("%d/%m/%Y"), "B"),
        c("d", (HOJE_DT - timedelta(days=20)).strftime("%d/%m/%Y"), "D"),
    ]
    client = _ClienteFake({"2": urgentes}, {})
    resultado = app._montar_atividade_recente(client)
    ids = [u["id_acomp"] for u in resultado["urgentes"]]
    relatar(
        "urgentes vêm ordenados do mais recente pro mais antigo (não confia na ordem do painel)",
        ids == ["b", "d", "a"],
        f"ordem retornada: {ids}",
    )


# ---------------------------------------------------------------------------
# Matriculas: so as de HOJE, mesmo que a busca traga registros antigos junto
# (matricula acontece o tempo todo, não é raro como "urgente").
# ---------------------------------------------------------------------------
def teste_matriculas_filtra_so_hoje():
    matriculas = [
        c("10", HOJE, "Matrícula em turma de controle (reconciliação)"),
        c("11", MUITO_ANTIGO, "Matrícula em turma de controle (reconciliação)"),
    ]
    client = _ClienteFake({"15": matriculas}, {"10": "500", "11": "501"})
    resultado = app._montar_atividade_recente(client)
    relatar(
        "matriculas_hoje só inclui as com data de hoje, ignora as antigas",
        len(resultado["matriculas_hoje"]) == 1 and resultado["matriculas_hoje"][0]["id_acomp"] == "10",
        f"matriculas_hoje: {resultado['matriculas_hoje']}",
    )


def teste_matriculas_vazio_quando_nenhuma_hoje():
    matriculas = [c("11", MUITO_ANTIGO, "Matrícula em turma de controle (reconciliação)")]
    client = _ClienteFake({"15": matriculas}, {"11": "501"})
    resultado = app._montar_atividade_recente(client)
    relatar(
        "matriculas_hoje fica vazio quando nenhuma matrícula é de hoje",
        resultado["matriculas_hoje"] == [],
        f"matriculas_hoje: {resultado['matriculas_hoje']}",
    )


def teste_resultado_inclui_data_de_hoje():
    client = _ClienteFake({}, {})
    resultado = app._montar_atividade_recente(client)
    relatar(
        "resultado inclui a data de hoje usada no filtro (pra mostrar na tela)",
        resultado["data"] == HOJE,
        f"data retornada: {resultado['data']!r} (esperado {HOJE!r})",
    )


def main():
    print("Rodando testes de _montar_atividade_recente (app.py, sem rede nenhuma)...\n")
    teste_urgentes_filtra_titulo_automatico_do_proprio_sistema()
    teste_urgentes_resolve_id_aluno()
    teste_urgentes_respeita_limite()
    teste_urgentes_ignora_comentario_antigo_demais()
    teste_urgentes_janela_ajustavel()
    teste_urgentes_ordenado_do_mais_recente_pro_mais_antigo()
    teste_matriculas_filtra_so_hoje()
    teste_matriculas_vazio_quando_nenhuma_hoje()
    teste_resultado_inclui_data_de_hoje()

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

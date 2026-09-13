"""
Testes de regressao pro Resumo do aluno (app.py): o resumo deterministico
(so fatos) e o prompt que vai pra IA. Sem rede nenhuma - a chamada de IA
em si nao e testada aqui (depende de provedor externo), so a montagem.

Como rodar:
    cd testes_logica
    python test_resumo_aluno.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402

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


def c(data, titulo, tipo="-Comentário", texto=""):
    return {"id_acomp": "1", "data": data, "titulo": titulo, "tipo": tipo, "autor": "Fulano", "texto": texto}


_PERFIL = {"nome": "MARIA DE TESTE", "contratado": 3000.0, "recebido": 1200.0, "diferenca": 1800.0}
_COMENTARIOS = [
    c("01/02/2025", "MATRICULA", "-Matricula", ""),
    c("10/02/2025", "LIGACAO", "-Comentário", "Aluna não atendeu, deixei recado."),
    c("20/02/2025", "PAGAMENTO", "-Pagamento Realizado", "Pago R$ 1.200,00 no pix."),
    c("05/03/2025", "PEDIDO", "-Comentário", "Aluna pediu cancelamento e falou em advogado."),
]


# ---------------------------------------------------------------------------
# resumo deterministico
# ---------------------------------------------------------------------------
def teste_resumo_deterministico_traz_os_tres_blocos():
    r = app._montar_resumo_deterministico(_COMENTARIOS, _PERFIL)
    relatar(
        "resumo determinístico marca modo='deterministico' e traz financeiro + histórico + pagamentos/desistência",
        r["modo"] == "deterministico" and "R$" in r["financeiro"]
        and "comentário(s) no histórico" in r["resumo_historico"]
        and r["pagamentos_desistencia"],
        f"resultado: {r}",
    )


def teste_resumo_deterministico_financeiro_formatado_em_reais_br():
    r = app._montar_resumo_deterministico(_COMENTARIOS, _PERFIL)
    relatar(
        "financeiro sai formatado no padrão BR (R$ 1.800,00), não 1800.0",
        "1.800,00" in r["financeiro"] and "Diferença R$ 1.800,00" in r["financeiro"],
        f"financeiro: {r['financeiro']!r}",
    )


def teste_resumo_deterministico_pagamentos_lista_so_o_que_e_de_cobranca():
    r = app._montar_resumo_deterministico(_COMENTARIOS, _PERFIL)
    pd = r["pagamentos_desistencia"]
    relatar(
        "pagamentos/desistência pega pagamento e pedido de cancelamento, ignora a ligação genérica",
        "PAGAMENTO" in pd and "PEDIDO" in pd and "LIGACAO" not in pd,
        f"pagamentos_desistencia: {pd!r}",
    )


def teste_resumo_deterministico_sem_comentario_humano():
    r = app._montar_resumo_deterministico([], {"contratado": 0, "recebido": 0, "diferenca": 0})
    relatar(
        "sem comentário nenhum: histórico diz que não há, pagamentos idem, sem quebrar",
        "Nenhum comentário" in r["resumo_historico"] and "Nenhum registro" in r["pagamentos_desistencia"],
        f"resultado: {r}",
    )


# ---------------------------------------------------------------------------
# prompt da IA
# ---------------------------------------------------------------------------
def teste_prompt_ia_inclui_cada_comentario_e_o_nome_e_financeiro():
    p = app._prompt_resumo_ia("MARIA DE TESTE", "Contratado R$ 3.000,00 | Recebido R$ 1.200,00 | Diferença R$ 1.800,00", _COMENTARIOS)
    ok = (
        "MARIA DE TESTE" in p
        and "Diferença R$ 1.800,00" in p
        and "10/02/2025" in p and "Aluna não atendeu, deixei recado." in p
        and "05/03/2025" in p and "falou em advogado" in p
    )
    relatar("prompt da IA inclui nome, financeiro e cada comentário (data + texto)", ok, f"prompt:\n{p}")


def teste_prompt_ia_pede_pra_nao_inventar():
    p = app._prompt_resumo_ia("X", "—", _COMENTARIOS)
    relatar(
        "prompt instrui explicitamente a NÃO inventar o que não está nos comentários",
        "NÃO invente" in p or "não invente" in p.lower(),
        "prompt não tem a instrução anti-invenção",
    )


def teste_prompt_ia_sem_comentarios():
    p = app._prompt_resumo_ia("X", "—", [])
    relatar(
        "prompt da IA com histórico vazio diz '(nenhum comentário no histórico)'",
        "(nenhum comentário no histórico)" in p,
        f"prompt:\n{p}",
    )


def teste_prompt_ia_achata_quebras_de_linha_do_comentario():
    coms = [c("01/01/2025", "T", "-Comentário", "linha 1\nlinha 2\r\nlinha 3")]
    p = app._prompt_resumo_ia("X", "—", coms)
    # a linha do comentário no prompt não pode ter quebra no meio (cada
    # comentário é uma linha só, começando com "- ")
    linha_do_com = [l for l in p.splitlines() if l.startswith("- 01/01/2025")]
    relatar(
        "comentário com \\n vira uma linha só no prompt (não quebra o formato de lista)",
        len(linha_do_com) == 1 and "linha 1 linha 2 linha 3" in linha_do_com[0],
        f"linhas encontradas: {linha_do_com}",
    )


def main():
    print("Rodando testes do Resumo do aluno (sem rede)...\n")
    teste_resumo_deterministico_traz_os_tres_blocos()
    teste_resumo_deterministico_financeiro_formatado_em_reais_br()
    teste_resumo_deterministico_pagamentos_lista_so_o_que_e_de_cobranca()
    teste_resumo_deterministico_sem_comentario_humano()
    teste_prompt_ia_inclui_cada_comentario_e_o_nome_e_financeiro()
    teste_prompt_ia_pede_pra_nao_inventar()
    teste_prompt_ia_sem_comentarios()
    teste_prompt_ia_achata_quebras_de_linha_do_comentario()

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

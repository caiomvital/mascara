"""
Testes de regressao pra _montar_turmas_entrada() (app.py) - card "Turmas de
Entrada" (J1/PY1) na tela inicial. Sem rede nenhuma, com um FucturaClient
falso.

Cobre dois bugs reais achados ao vivo em 2026-09-18 (relatado pelo usuario:
"So apareceram turmas de python. E nao consigo clicar pra ver os alunos
listados."):
  1. O autocomplete de turma do Fuctura exige >=3 caracteres na busca -
     "J1"/"J2" (2 caracteres) sempre devolviam 0 resultados ao vivo, entao
     nenhuma turma de Java aparecia. Corrigido buscando "." + modulo (toda
     turma de curso comeca com "." no nome).
  2. O corte pros N mais recentes era feito no total combinado (J1+PY1) -
     se um modulo tivesse muito mais turmas cadastradas que o outro, ele
     sozinho preenchia todas as vagas. Corrigido cortando por modulo.

Como rodar:
    cd testes_logica
    python test_turmas_entrada.py
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


class _ClienteFake:
    """FucturaClient falso: buscar_turma_por_nome so devolve resultado se o
    termo pedido bater EXATAMENTE com uma chave de `turmas_por_termo` -
    reproduz de proposito o comportamento real do autocomplete do Fuctura
    (que exige >=3 caracteres e nao devolve nada pra termos mais curtos),
    pra pegar de volta o bug se `_montar_turmas_entrada` voltar a buscar o
    modulo cru (ex: "J1") em vez de "." + modulo (ex: ".J1")."""

    def __init__(self, turmas_por_termo, roster_por_id):
        self._turmas_por_termo = turmas_por_termo
        self._roster_por_id = roster_por_id

    def buscar_turma_por_nome(self, termo):
        return self._turmas_por_termo.get(termo, [])

    def roster_turma(self, id_turma):
        return self._roster_por_id.get(id_turma, [])


def _turma(id_turma, nome):
    return {"id_turma": id_turma, "nome": nome}


def _aluno(nome):
    return {"nome": nome}


def teste_busca_com_ponto_prefixado_nao_com_modulo_cru():
    """Se o cliente só responde pra ".J1"/".PY1" (como o Fuctura real, que
    não acha nada pra "J1" cru - só 2 caracteres), _montar_turmas_entrada
    ainda assim tem que achar as turmas de Java - regressão direta do bug
    relatado ("só apareceram turmas de python")."""
    client = _ClienteFake(
        turmas_por_termo={
            ".J1": [_turma("1", ".J1 08/07/25 TER N")],
            ".PY1": [_turma("2", ".PY1 02/09/25 TER N")],
        },
        roster_por_id={"1": [_aluno("FULANO")], "2": [_aluno("CICLANO")]},
    )
    resultado = app._montar_turmas_entrada(client)
    modulos = {t["modulo"] for t in resultado["turmas"]}
    relatar(
        'busca "." + módulo acha turma de J1 mesmo quando o termo cru ("J1") não devolveria nada',
        modulos == {"J1", "PY1"},
        f"módulos encontrados: {modulos}",
    )


def teste_corte_e_por_modulo_nao_no_total_combinado():
    """Com 20 turmas de PY1 e só 1 de J1, J1 não pode sumir do resultado -
    regressão do corte que antes era feito no total combinado das duas."""
    py1 = [_turma(f"py{i}", f".PY1 0{i % 9 + 1}/01/2{i} SAB M") for i in range(20)]
    client = _ClienteFake(
        turmas_por_termo={
            ".J1": [_turma("j1", ".J1 08/07/25 TER N")],
            ".PY1": py1,
        },
        roster_por_id={t["id_turma"]: [] for t in py1 + [_turma("j1", "")]},
    )
    resultado = app._montar_turmas_entrada(client)
    modulos = {t["modulo"] for t in resultado["turmas"]}
    relatar(
        "corte por módulo: J1 aparece mesmo com só 1 turma contra 20 de PY1 (não é engolido pelo corte combinado)",
        "J1" in modulos,
        f"módulos encontrados: {modulos}",
    )


def teste_alunos_incluidos_com_categoria():
    """Pedido do usuário (2026-09-18: "não consigo clicar pra ver os
    alunos listados... é importante poder conferir") - a resposta tem que
    trazer a lista de alunos (nome + categoria), não só a contagem."""
    client = _ClienteFake(
        turmas_por_termo={
            ".J1": [_turma("1", ".J1 08/07/25 TER N")],
            ".PY1": [],
        },
        roster_por_id={"1": [_aluno("ANA"), _aluno("-BRUNO"), _aluno(".CARLOS")]},
    )
    resultado = app._montar_turmas_entrada(client)
    turma = resultado["turmas"][0]
    categorias = {a["nome"]: a["categoria"] for a in turma["alunos"]}
    relatar(
        "resultado inclui a lista de alunos com nome + categoria (primeira vez / refazendo / abandono)",
        categorias == {"ANA": "Primeira vez", "-BRUNO": "Refazendo", ".CARLOS": "Abandono"},
        f"categorias: {categorias}",
    )


def teste_turma_sem_ninguem_nao_quebra():
    client = _ClienteFake(
        turmas_por_termo={".J1": [_turma("1", ".J1 08/07/25 TER N")], ".PY1": []},
        roster_por_id={"1": []},
    )
    resultado = app._montar_turmas_entrada(client)
    relatar(
        "turma sem nenhum matriculado não quebra - alunos vazio, total 0",
        resultado["turmas"][0]["alunos"] == [] and resultado["turmas"][0]["total_matriculados"] == 0,
        f"resultado: {resultado['turmas'][0]}",
    )


def main():
    print('Rodando testes de _montar_turmas_entrada (app.py, sem rede nenhuma)...\n')
    teste_busca_com_ponto_prefixado_nao_com_modulo_cru()
    teste_corte_e_por_modulo_nao_no_total_combinado()
    teste_alunos_incluidos_com_categoria()
    teste_turma_sem_ninguem_nao_quebra()

    print(f"\n{'=' * 70}")
    print(f"Total OK: {_ok_count} | Total FALHOU: {len(_falhas)}")
    if _falhas:
        print("\nFalhas:")
        for nome, detalhe in _falhas:
            print(f"  - {nome}: {detalhe}")
        sys.exit(1)
    else:
        print("Nenhuma regressão detectada.")


if __name__ == "__main__":
    main()

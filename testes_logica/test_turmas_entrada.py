"""
Testes de regressao pra _montar_turmas_entrada() (app.py) - card "Turmas de
Entrada" (J1/PY1) na tela inicial. Sem rede nenhuma, com um FucturaClient
falso.

Cobre bugs reais achados ao vivo em 2026-09-18, em duas rodadas (relatado
pelo usuario):

Rodada 1 - "So apareceram turmas de python. E nao consigo clicar pra ver
os alunos listados.":
  1. O autocomplete de turma do Fuctura exige >=3 caracteres na busca -
     "J1"/"J2" (2 caracteres) sempre devolviam 0 resultados ao vivo, entao
     nenhuma turma de Java aparecia. Corrigido buscando "." + modulo (toda
     turma de curso comeca com "." no nome).
  2. O corte pros N mais recentes era feito no total combinado (J1+PY1) -
     se um modulo tivesse muito mais turmas cadastradas que o outro, ele
     sozinho preenchia todas as vagas. Corrigido cortando por modulo.

Rodada 2 - "ele mostrou ai interessados, tem que mostrar o que estao
matriculados apenas contabilizando" e "nao tem 30 turmas abertas, tem bem
menos":
  3. O roster de uma turma trazia qualquer aluno cuja Situacao ATUAL no
     Fuctura estivesse ligada aquele id_turma - inclusive Interessado/
     Cliente sem Interesse/Cancelado, que nunca matricularam de verdade.
     Corrigido: só conta/lista quem tem status de matrícula real.
  4. O corte pras N mais recentes só olhava a data embutida no NOME da
     turma (início) - não checava se ela já tinha TERMINADO (dataTermino
     de obter_turma()). Corrigido: turma já terminada não entra na lista.

Como rodar:
    cd testes_logica
    python test_turmas_entrada.py
"""
import os
import sys
from datetime import datetime, timedelta

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
    modulo cru (ex: "J1") em vez de "." + modulo (ex: ".J1"). obter_turma
    devolve {"dataTermino": ...} por id_turma - turma sem entrada em
    `detalhe_por_id` conta como sem dataTermino (aberta por padrão)."""

    def __init__(self, turmas_por_termo, roster_por_id, detalhe_por_id=None):
        self._turmas_por_termo = turmas_por_termo
        self._roster_por_id = roster_por_id
        self._detalhe_por_id = detalhe_por_id or {}

    def buscar_turma_por_nome(self, termo):
        return self._turmas_por_termo.get(termo, [])

    def roster_turma(self, id_turma):
        return self._roster_por_id.get(id_turma, [])

    def obter_turma(self, id_turma):
        return self._detalhe_por_id.get(id_turma, {"dataTermino": ""})


def _turma(id_turma, nome):
    return {"id_turma": id_turma, "nome": nome}


def _aluno(nome, status=None):
    return {"nome": nome, "status": status}


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


def teste_interessado_e_cancelado_nao_contam():
    """Regressão direta do relatado: "ele mostrou aí interessados, tem que
    mostrar o que estão matriculados apenas contabilizando" - Interessado/
    Cliente sem Interesse/Cancelado nunca matricularam de verdade, não
    podem contar nem aparecer na lista de alunos."""
    client = _ClienteFake(
        turmas_por_termo={".J1": [_turma("1", ".J1 08/07/25 TER N")], ".PY1": []},
        roster_por_id={"1": [
            _aluno("ANA", "-Matriculado"),
            _aluno("BRUNO", "Interessado"),
            _aluno("CARLA", "Cliente sem Interesse"),
            _aluno("DAVI", "Cancelado"),
            _aluno("EVA", "Devedor"),
        ]},
    )
    resultado = app._montar_turmas_entrada(client)
    turma = resultado["turmas"][0]
    nomes_listados = {a["nome"] for a in turma["alunos"]}
    relatar(
        "Interessado/Cliente sem Interesse/Cancelado ficam de fora da contagem (só ANA e EVA contam)",
        turma["total_matriculados"] == 2 and nomes_listados == {"ANA", "EVA"},
        f"total={turma['total_matriculados']} alunos listados={nomes_listados}",
    )


def teste_devedor_e_advogado_continuam_contando():
    """Devedor/Advogado já passaram pela matrícula de verdade - só estão
    numa situação financeira à parte agora, não podem sumir da contagem
    (diferente de Interessado, que nunca matriculou)."""
    client = _ClienteFake(
        turmas_por_termo={".J1": [_turma("1", ".J1 08/07/25 TER N")], ".PY1": []},
        roster_por_id={"1": [_aluno("ANA", "Devedor"), _aluno("BRUNO", "Advogado")]},
    )
    resultado = app._montar_turmas_entrada(client)
    relatar(
        "Devedor/Advogado continuam contando como matriculados de verdade",
        resultado["turmas"][0]["total_matriculados"] == 2,
        f"resultado: {resultado['turmas'][0]}",
    )


def teste_turma_ja_terminada_e_excluida():
    """Regressão direta do relatado: "não tem 30 turmas abertas, tem bem
    menos" - turma com dataTermino no passado não pode aparecer, mesmo
    sendo uma das "mais recentes" pela data do nome."""
    ontem = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
    amanha = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    client = _ClienteFake(
        turmas_por_termo={
            ".J1": [_turma("velha", ".J1 08/07/25 TER N"), _turma("nova", ".J1 17/09/26 Ter N")],
            ".PY1": [],
        },
        roster_por_id={"velha": [], "nova": []},
        detalhe_por_id={
            "velha": {"dataTermino": ontem},
            "nova": {"dataTermino": amanha},
        },
    )
    resultado = app._montar_turmas_entrada(client)
    ids = {t["id_turma"] for t in resultado["turmas"]}
    relatar(
        "turma com dataTermino no passado é excluída; a que ainda não terminou continua aparecendo",
        ids == {"nova"},
        f"ids retornados: {ids}",
    )


def teste_turma_sem_data_termino_conta_como_aberta():
    """dataTermino ausente/ilegível não pode derrubar a turma da lista -
    sem dado suficiente pra afirmar que já fechou, trata como aberta."""
    client = _ClienteFake(
        turmas_por_termo={".J1": [_turma("1", ".J1 08/07/25 TER N")], ".PY1": []},
        roster_por_id={"1": []},
        detalhe_por_id={"1": {"dataTermino": ""}},
    )
    resultado = app._montar_turmas_entrada(client)
    relatar(
        "turma sem dataTermino cadastrada continua aparecendo (trata como aberta, não como fechada)",
        len(resultado["turmas"]) == 1,
        f"resultado: {resultado['turmas']}",
    )


def main():
    print('Rodando testes de _montar_turmas_entrada (app.py, sem rede nenhuma)...\n')
    teste_busca_com_ponto_prefixado_nao_com_modulo_cru()
    teste_corte_e_por_modulo_nao_no_total_combinado()
    teste_alunos_incluidos_com_categoria()
    teste_turma_sem_ninguem_nao_quebra()
    teste_interessado_e_cancelado_nao_contam()
    teste_devedor_e_advogado_continuam_contando()
    teste_turma_ja_terminada_e_excluida()
    teste_turma_sem_data_termino_conta_como_aberta()

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

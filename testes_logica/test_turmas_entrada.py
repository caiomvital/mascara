"""
Testes de regressao pra _montar_turmas_entrada() (app.py) - card "Turmas de
Entrada" (J1/PY1) na tela inicial. Sem rede nenhuma, com um FucturaClient
falso.

Cobre bugs reais achados ao vivo em 2026-09-18, em três rodadas (relatado
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

Rodada 3 - "Tem que ver se esses realmente estão matriculados ou só
foram matriculados na turma, sem ser matriculados de verdade":
  5. Status != Interessado/Cancelado não garante matrícula de verdade -
     cruzando o roster com o financeiro (perfil_aluno) achei gente com
     status "Devedor"/"-Matriculado" com Contratado = R$0,00 e nunca
     pagou nada (caso real: LUANA DE LIMA POROCA ALMEIDA). Corrigido:
     exige contratado > 0 OU já pagou algo (decisão do usuário: quem
     pagou um sinal mesmo sem Contratado formal ainda conta - caso real:
     JADSON, contratado=0 mas recebido=75).

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

    def __init__(self, turmas_por_termo, roster_por_id, detalhe_por_id=None, perfil_por_id=None, comentarios_por_id=None):
        self._turmas_por_termo = turmas_por_termo
        self._roster_por_id = roster_por_id
        self._detalhe_por_id = detalhe_por_id or {}
        self._perfil_por_id = perfil_por_id or {}
        self._comentarios_por_id = comentarios_por_id or {}

    def buscar_turma_por_nome(self, termo):
        return self._turmas_por_termo.get(termo, [])

    def roster_turma(self, id_turma):
        return self._roster_por_id.get(id_turma, [])

    def comentarios_aluno(self, id_aluno):
        return self._comentarios_por_id.get(id_aluno, [])

    def obter_turma(self, id_turma):
        return self._detalhe_por_id.get(id_turma, {"dataTermino": ""})

    def perfil_aluno(self, id_aluno):
        # default = financeiro real (contratado>0), pra nao quebrar testes
        # que nao se importam com o filtro financeiro.
        return self._perfil_por_id.get(id_aluno, {"contratado": 9999.0, "recebido": 9999.0})


def _turma(id_turma, nome):
    return {"id_turma": id_turma, "nome": nome}


def _aluno(nome, status=None, id_aluno=None, observacao=None):
    return {"nome": nome, "status": status, "id_aluno": id_aluno or nome, "observacao": observacao}


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


def teste_status_ok_mas_sem_contrato_nem_pagamento_nao_conta():
    """Regressão direta do relatado (terceira rodada): "tem que ver se
    esses realmente estão matriculados ou só foram matriculados na turma,
    sem ser matriculados de verdade" - status "Devedor"/"-Matriculado" não
    garante matrícula real; cruza com o financeiro (perfil_aluno). Casos:
    LUANA (Devedor, contratado=0, recebido=0 - fora), JADSON
    ("-Matriculado", contratado=0, mas pagou de verdade - conta, ver
    comentarios_por_id) - contratado=0 sempre recalcula pelo histórico de
    comentários (recebido_real), não confia no agregado bruto sozinho."""
    client = _ClienteFake(
        turmas_por_termo={".J1": [_turma("1", ".J1 08/07/25 TER N")], ".PY1": []},
        roster_por_id={"1": [
            _aluno("LUANA", "Devedor", id_aluno="luana"),
            _aluno("JADSON", "-Matriculado", id_aluno="jadson"),
            _aluno("ALEF", "Devedor", id_aluno="alef"),
        ]},
        perfil_por_id={
            "luana": {"contratado": 0.0, "recebido": 0.0},
            "jadson": {"contratado": 0.0, "recebido": 75.0},
            "alef": {"contratado": 4901.0, "recebido": 100.0},
        },
        comentarios_por_id={
            "jadson": [{"tipo": "-Pagamento Realizado", "titulo": "PAGAMENTO PIX", "texto": "Valor: R$ 75,00"}],
        },
    )
    resultado = app._montar_turmas_entrada(client)
    turma = resultado["turmas"][0]
    nomes_listados = {a["nome"] for a in turma["alunos"]}
    relatar(
        "LUANA (contratado=0, recebido=0) fica de fora; JADSON (pagou de verdade) e ALEF (contrato assinado) contam",
        turma["total_matriculados"] == 2 and nomes_listados == {"JADSON", "ALEF"},
        f"total={turma['total_matriculados']} alunos listados={nomes_listados}",
    )


def teste_pagamento_de_teste_nao_conta_como_matricula_real():
    """Regressão direta do achado real (2026-09-18, revisão comentário a
    comentário pedida pelo usuário: "17 no mesmo bolo"): JADSON só
    aparecia com recebido=75 no perfil_aluno por causa de 2 comentários de
    TESTE gravados por engano nesse aluno real (sobra de teste da tela
    Registrar Pagamento) - sem contrato formal (contratado=0), o sistema
    tem que ignorar esse "recebido" e recalcular pelo histórico de
    comentários de verdade, que não confirma nenhum pagamento real."""
    client = _ClienteFake(
        turmas_por_termo={".J1": [_turma("1", ".J1 08/07/25 TER N")], ".PY1": []},
        roster_por_id={"1": [_aluno("JADSON", "-Matriculado", id_aluno="jadson")]},
        perfil_por_id={"jadson": {"contratado": 0.0, "recebido": 75.0}},
        comentarios_por_id={
            "jadson": [
                {"tipo": "-Pagamento Realizado", "titulo": "TESTE - Pagamento Realizado (convenção)",
                 "texto": "Valor: R$ 50,00"},
                {"tipo": "-Pagamento Realizado", "titulo": "Pagamento Realizado",
                 "texto": "Parcela: 2/1 (TESTE via endpoint HTTP). Valor: R$ 25,00."},
            ],
        },
    )
    resultado = app._montar_turmas_entrada(client)
    turma = resultado["turmas"][0]
    relatar(
        "JADSON não conta - o \"recebido=75\" do perfil vinha só de comentários de teste, sem pagamento real nenhum",
        turma["total_matriculados"] == 0,
        f"resultado: {turma}",
    )


def teste_monitor_nao_conta_como_matricula_de_primeira_vez():
    """Regressão direta do achado real: EDSON VINICIUS SOUZA DOS SANTOS
    aparecia como "primeira vez" numa turma de entrada, mas o campo
    observacao do roster mostrava "AG J2 A4 CONT MONITO" (truncado) - é
    monitor (ex-aluno ajudando o professor), não aluno novo."""
    client = _ClienteFake(
        turmas_por_termo={".J1": [_turma("1", ".J1 08/07/25 TER N")], ".PY1": []},
        roster_por_id={"1": [
            _aluno("EDSON", "Devedor", id_aluno="edson", observacao="AG J2 A4 CONT MONITO"),
            _aluno("ANA", "Devedor", id_aluno="ana", observacao="ADULTO"),
        ]},
        perfil_por_id={
            "edson": {"contratado": 4130.0, "recebido": 2065.0},
            "ana": {"contratado": 4901.0, "recebido": 100.0},
        },
    )
    resultado = app._montar_turmas_entrada(client)
    turma = resultado["turmas"][0]
    nomes_listados = {a["nome"] for a in turma["alunos"]}
    relatar(
        "EDSON (monitor, pela observação) não conta; ANA (observação normal) conta",
        turma["total_matriculados"] == 1 and nomes_listados == {"ANA"},
        f"total={turma['total_matriculados']} alunos listados={nomes_listados}",
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
    teste_status_ok_mas_sem_contrato_nem_pagamento_nao_conta()
    teste_pagamento_de_teste_nao_conta_como_matricula_real()
    teste_monitor_nao_conta_como_matricula_de_primeira_vez()
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

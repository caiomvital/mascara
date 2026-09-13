"""
Teste de estresse automatizado do sistema-mascara.

Roda o app.py DE VERDADE (mesmo codigo, mesmo ThreadingHTTPServer) numa
porta de teste, contra um Fuctura FALSO (fuctura_stub.py) - NUNCA contra o
Fuctura real. So testa os caminhos de ESCRITA (comentario, matricula,
editar cadastro, mudar situacao, criar aluno, criar turma) sob concorrencia
pesada e input adversarial, coisa que seria irresponsavel testar em
producao (recriaria o proprio "flood" que motivou travar tudo atras de
confirmacao humana).

Como rodar:
    cd testes_estresse
    python stress_test.py

Sai com codigo 0 se tudo passou, 1 se algum cenario falhou (uteis pra CI).
"""
import concurrent.futures
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fuctura_stub  # noqa: E402

PORTA_STUB = 18767
PORTA_APP = 18766

import fuctura_client  # noqa: E402
fuctura_client.BASE = f"http://127.0.0.1:{PORTA_STUB}"
fuctura_client.AUTH = ("stub", "stub")

import app as sistema  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402
import requests  # noqa: E402

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


def subir_tudo():
    fuctura_stub.subir_stub(PORTA_STUB)
    servidor_app = ThreadingHTTPServer(("127.0.0.1", PORTA_APP), sistema.Handler)
    threading.Thread(target=servidor_app.serve_forever, daemon=True).start()
    time.sleep(0.3)


def _url(caminho):
    return f"http://127.0.0.1:{PORTA_APP}{caminho}"


def login(numero_login):
    """Retorna o token de sessao (string), ou None se falhou - usado sem
    requests.Session compartilhada entre threads (cada worker manda o
    cookie explicitamente), pra nao depender de Session ser thread-safe."""
    r = requests.post(_url("/api/login"), json={"login": numero_login, "senha": "qualquercoisa"})
    if not r.json().get("ok"):
        return None
    return r.cookies.get("sessao")


def criar_aluno_teste(token, nome="ALUNO STRESS"):
    r = requests.post(_url("/api/aluno/criar"), cookies={"sessao": token},
                       json={"nome": nome, "profissao": "TESTE", "status": "19"})
    return r.json().get("id_aluno")


def criar_turma_teste(token, abreviada="STR"):
    r = requests.post(_url("/api/turma/criar"), cookies={"sessao": token}, json={
        "abreviada": abreviada, "entidade": "1", "descricao": "TURMA STRESS", "professor": "PROF TESTE",
    })
    return r.json().get("id_turma")


# ---------------- cenario 1: comentarios concorrentes no mesmo aluno ----------------
def cenario_comentarios_concorrentes(n=30):
    fuctura_stub.resetar()
    token = login("09000000001")
    id_aluno = criar_aluno_teste(token)

    def worker(i):
        return requests.post(_url(f"/api/aluno/{id_aluno}/comentario"), cookies={"sessao": token}, json={
            "tipo": "3", "assunto": f"COMENTARIO {i}", "descricao": f"teste concorrente {i}",
        })

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        respostas = list(ex.map(worker, range(n)))

    erros = [r.status_code for r in respostas if r.status_code != 200]
    # criar_aluno_teste ja grava 1 comentario proprio (bloco 3, "Profissão:
    # ..."), entao o total esperado eh n + 1, nao n.
    n_comentarios = len(fuctura_stub._alunos[id_aluno]["comentarios"])
    esperado = n + 1
    relatar(
        f"Cenário 1: {n} comentários concorrentes no mesmo aluno",
        not erros and n_comentarios == esperado,
        f"status !=200: {erros} | comentários gravados: {n_comentarios}/{esperado} (nenhum pode se perder)",
    )


# ---------------- cenario 2: matriculas concorrentes (soma financeira) ----------------
def cenario_matriculas_concorrentes(n=30, valor=100.0):
    fuctura_stub.resetar()
    token = login("09000000002")
    id_aluno = criar_aluno_teste(token)
    id_turma = criar_turma_teste(token)

    def worker(i):
        return requests.post(_url(f"/api/aluno/{id_aluno}/matricular"), cookies={"sessao": token}, json={
            "turma_id": id_turma, "turma_nome": "TURMA STRESS",
            "valor_contratado": str(valor), "forma_pagamento": "boleto",
        })

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        respostas = list(ex.map(worker, range(n)))

    erros = [r.status_code for r in respostas if r.status_code != 200]
    contratado_final = fuctura_stub._alunos[id_aluno]["contratado"]
    esperado = n * valor
    relatar(
        f"Cenário 2: {n} matrículas concorrentes de R$ {valor:.2f} cada",
        not erros and abs(contratado_final - esperado) < 0.01,
        f"status !=200: {erros} | contratado final: {contratado_final} (esperado {esperado} — nenhuma pode 'sumir')",
    )


# ---------------- cenario 3: logins + criacoes concorrentes (isolamento) ----------------
def cenario_logins_concorrentes(n=20):
    fuctura_stub.resetar()

    def worker(i):
        token = login(f"09{i:09d}")
        if not token:
            return False
        id_aluno = criar_aluno_teste(token, nome=f"ALUNO SESSAO {i}")
        return id_aluno is not None

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        resultados = list(ex.map(worker, range(n)))

    relatar(
        f"Cenário 3: {n} logins + criações concorrentes (isolamento de sessão)",
        all(resultados),
        f"falharam: {resultados.count(False)}/{n}",
    )


# ---------------- cenario 4: input adversarial em rajada ----------------
def cenario_input_adversarial():
    fuctura_stub.resetar()
    token = login("09000000004")
    id_aluno = criar_aluno_teste(token)

    payloads_ruins = [
        {},
        {"tipo": "15"},  # tipo proibido no formulario manual de comentario
        {"tipo": "3", "assunto": "x" * 100000, "descricao": "y"},
        {"tipo": "3", "assunto": "emoji 🎉💥", "descricao": "unicode com zero-width ​ e acentos ção éí"},
        {"tipo": None, "assunto": None, "descricao": None},
        {"tipo": 123, "assunto": ["lista", "nao", "string"], "descricao": {"obj": 1}},
        {"tipo": "3", "assunto": "normal", "descricao": "normal", "turma_id": "id-que-nao-existe"},
    ]

    quebrou = []
    for i, payload in enumerate(payloads_ruins):
        try:
            r = requests.post(_url(f"/api/aluno/{id_aluno}/comentario"), cookies={"sessao": token},
                               json=payload, timeout=5)
            r.json()  # se a resposta nao for JSON valido, cai na excecao
            if r.status_code >= 500:
                quebrou.append((i, r.status_code, "500 sem tratamento"))
        except Exception as e:
            quebrou.append((i, "exceção", str(e)))

    ainda_vivo = False
    detalhe_vivo = ""
    try:
        r = requests.get(_url(f"/api/aluno/{id_aluno}/detalhe"), cookies={"sessao": token}, timeout=5)
        ainda_vivo = r.status_code == 200
        detalhe_vivo = f"status={r.status_code} corpo={r.text[:200]}"
    except Exception as e:
        detalhe_vivo = f"exceção: {e}"

    relatar(
        "Cenário 4: rajada de input adversarial (campos faltando/tipo errado/texto gigante/unicode)",
        not quebrou and ainda_vivo,
        f"payloads problemáticos: {quebrou} | servidor respondeu depois: {ainda_vivo} ({detalhe_vivo})",
    )


# ---------------- cenario 5: vazamento de sessao (diagnostico, nao pass/fail) ----------------
def cenario_vazamento_sessao(n=200):
    antes = len(sistema.sessions)
    for i in range(n):
        login(f"08{i:09d}")
    depois = len(sistema.sessions)
    cresceu_tudo = (depois - antes) == n
    print(f"  DIAGNÓSTICO Cenário 5: sessions antes={antes} depois={depois} (+{depois - antes} de {n} logins, nenhum fez logout)")
    if cresceu_tudo:
        print("  ACHADO (não corrigido automaticamente): sessions/reconciliacao_jobs não têm expiração —")
        print("  cresce sem limite enquanto o servidor roda. Ver resumo no memory/relatório.")


def main():
    print("Subindo Fuctura falso + sistema-mascara real (portas de teste isoladas, nada toca produção)...")
    subir_tudo()
    print(f"Fuctura falso em :{PORTA_STUB} | sistema-mascara em :{PORTA_APP}\n")

    cenario_comentarios_concorrentes()
    cenario_matriculas_concorrentes()
    cenario_logins_concorrentes()
    cenario_input_adversarial()
    cenario_vazamento_sessao()

    print(f"\n{'=' * 70}")
    print(f"Total OK: {_ok_count} | Total FALHOU: {len(_falhas)}")
    if _falhas:
        print("\nFalhas:")
        for nome, detalhe in _falhas:
            print(f"  - {nome}: {detalhe}")
        sys.exit(1)
    print("Nenhuma falha nos cenários automatizados (Cenário 5 é diagnóstico, não pass/fail).")


if __name__ == "__main__":
    main()

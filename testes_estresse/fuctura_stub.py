"""
Fuctura FALSO (stub), pra teste de estresse.

Imita, com fidelidade suficiente pros parsers de fuctura_client.py, so os
endpoints que os 6 caminhos de escrita do sistema-mascara usam de verdade
(criar aluno, criar turma, comentario, matricular, editar cadastro, mudar
situacao). NUNCA aponte fuctura_client.BASE pra isto contra dado real - e
so pra rodar stress_test.py localmente contra um servidor de mentira.

Guarda tudo em memoria (dict + Lock), sem persistir em disco - cada rodada
de teste comeca do zero.
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# Mesma lista de campos que fuctura_client.FucturaClient usa pra ler/montar
# o cadastro completo - importado de la pra nao duplicar e desalinhar.
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fuctura_client import FucturaClient

_CAMPOS_TEXTO = FucturaClient._CAMPOS_CADASTRO_TEXTO
_CAMPOS_SELECT = FucturaClient._CAMPOS_CADASTRO_SELECT

_TIPO_LABEL = {
    "3": "-Comentário", "15": "-Matricula", "11": "-Pagamento Realizado",
    "13": "Abatimento/Devolução", "14": "Aguardando Turma", "1": "Importante", "2": "Urgente",
}

_lock = threading.Lock()
_alunos = {}
_turmas = {}
_contador = {"aluno": 1000, "turma": 1000, "acomp": 1, "matricula": 900000}


def resetar():
    """Zera todo o estado - chame no inicio de cada cenario de teste, pra
    um cenario nao contaminar o proximo."""
    with _lock:
        _alunos.clear()
        _turmas.clear()
        _contador.update(aluno=1000, turma=1000, acomp=1, matricula=900000)


def _novo_aluno_vazio():
    d = {c: "" for c in _CAMPOS_TEXTO}
    d.update({c: "" for c in _CAMPOS_SELECT})
    d["observacoes"] = ""
    d["comentarios"] = []
    d["contratado"] = 0.0
    d["recebido"] = 0.0
    d["turmas_atuais"] = []
    return d


def _html_form_cadastro(a):
    """Bate com buscar_cadastro_completo(): 1 input/select por campo, com
    o valor atual - inclusive o(s) <select> com 'selected' na opcao certa."""
    partes = []
    for campo in _CAMPOS_TEXTO:
        partes.append(f'<input name="{campo}" id="{campo}" value="{a.get(campo, "")}" />')
    for campo in _CAMPOS_SELECT:
        valor = a.get(campo, "")
        partes.append(
            f'<select name="{campo}" id="{campo}">'
            f'<option value="">Selecione</option>'
            f'<option value="{valor}" selected="selected">{valor}</option>'
            f'</select>'
        )
    partes.append(f'<textarea name="observacoes">{a.get("observacoes", "")}</textarea>')
    return "<html><body><form>" + "".join(partes) + "</form></body></html>"


def _html_perfil(a):
    """Bate o suficiente com perfil_aluno() (nome/celular/status/turmas/financeiro)."""
    turmas_html = "".join(
        f"- {t['data']} - .{t['nome']}<br>" for t in a.get("turmas_atuais", [])
    )
    return f"""<html><body>
<input name="nome" id="nome" value="{a.get('nome', '')}" />
<input name="celular" id="celular" value="{a.get('celular', '')}" />
<option value='1' selected='selected'>{a.get('status', '')}</option>
Turmas:</td><td>{turmas_html}</td><td>
Contratado</strong></td><td>{a.get('contratado', 0):.2f}
Recebido</strong></td><td>{a.get('recebido', 0):.2f}
Diferença</strong></td><td>{a.get('contratado', 0) - a.get('recebido', 0):.2f}
</body></html>"""


def _parse_money(s):
    if not s:
        return 0.0
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _ler_body(self):
        tamanho = int(self.headers.get("Content-Length", 0))
        cru = self.rfile.read(tamanho).decode("utf-8", "replace") if tamanho else ""
        return {k: v[0] for k, v in parse_qs(cru).items()}

    def _enviar(self, conteudo, status=200, tipo="text/html; charset=utf-8"):
        corpo = conteudo.encode("utf-8") if isinstance(conteudo, str) else conteudo
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    # ---------------- GET ----------------
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        caminho = parsed.path

        if caminho == "/principal.php":
            self._enviar("<a href='ctrl_navegacao.php?p=dados_usuario'>FUNCIONARIO TESTE</a>")
            return

        if caminho == "/lista_alunos.php":
            id_aluno = (qs.get("idAluno") or [""])[0]
            with _lock:
                if id_aluno and id_aluno in _alunos:
                    self._enviar(_html_perfil(_alunos[id_aluno]))
                    return
                # modo criacao - reserva uma matricula nova a cada GET,
                # igual o Fuctura real faz.
                matricula = str(_contador["matricula"])
                _contador["matricula"] += 1
            self._enviar(f'<html><body><input name="matricula" id="matricula" value="{matricula}" /></body></html>')
            return

        if caminho == "/detalhes_alunos.php":
            id_aluno = (qs.get("id") or [""])[0]
            with _lock:
                a = _alunos.get(id_aluno)
            if a is None:
                self._enviar("aluno nao encontrado", 404)
                return
            self._enviar(_html_form_cadastro(a))
            return

        if caminho == "/server_datatables_aluno.php":
            # bate com comentarios_aluno(): {"aaData": [[id_acomp, data,
            # titulo, tipo_label, autor, texto], ...]}
            id_aluno = (qs.get("idA") or [""])[0]
            with _lock:
                a = _alunos.get(id_aluno)
                comentarios = list(a["comentarios"]) if a else []
            aa_data = [
                [c["id_acomp"], c["data"], c["titulo"], _TIPO_LABEL.get(c["tipo"], c["tipo"]), c["autor"], c["texto"]]
                for c in comentarios
            ]
            import json as _json
            self._enviar(_json.dumps({"aaData": aa_data}), tipo="application/json")
            return

        self._enviar("not found", 404)

    # ---------------- POST ----------------
    def do_POST(self):
        caminho = urlparse(self.path).path
        body = self._ler_body()

        if caminho == "/ctrl_acesso.php":
            self.send_response(302)
            self.send_header("Location", "/principal.php")
            self.end_headers()
            return

        if caminho == "/lista_alunos.php":
            self._post_lista_alunos(body)
            return

        if caminho == "/detalhes_alunos.php":
            self._post_detalhes_alunos(body)
            return

        if caminho == "/lista_turmas.php":
            self._post_lista_turmas(body)
            return

        self._enviar("not found", 404)

    def _post_lista_alunos(self, body):
        if body.get("cadAluno") == "1":
            with _lock:
                novo_id = str(_contador["aluno"])
                _contador["aluno"] += 1
                a = _novo_aluno_vazio()
                a.update({
                    "nome": body.get("nome", ""), "email": body.get("email", ""),
                    "fixo": body.get("fixo", ""), "celular": body.get("celular", ""),
                    "status": body.get("status", ""), "matricula": body.get("matricula", ""),
                })
                _alunos[novo_id] = a
            self._enviar(f'<html><body><input name="idAluno" id="idAluno" value="{novo_id}" /></body></html>')
            return

        if body.get("cadAcompanhamento") == "1":
            id_aluno = body.get("idAluno", "")
            with _lock:
                a = _alunos.get(id_aluno)
                if a is None:
                    self._enviar("aluno nao encontrado", 404)
                    return
                tipo = body.get("tipo", "")
                if tipo == "15":
                    a["contratado"] += _parse_money(body.get("valorContratado", "0,00"))
                    turma_id = body.get("turma", "")
                    if turma_id and turma_id in _turmas:
                        a["turmas_atuais"].append({"data": "05/09/2026", "nome": _turmas[turma_id]["descricao"]})
                acomp_id = str(_contador["acomp"])
                _contador["acomp"] += 1
                a["comentarios"].append({
                    "id_acomp": acomp_id, "data": "05/09/2026",
                    "titulo": body.get("assunto", ""), "tipo": tipo,
                    "autor": "FUNCIONARIO TESTE", "texto": body.get("descricao", ""),
                })
            self._enviar("ok")
            return

        self._enviar("bad request", 400)

    def _post_detalhes_alunos(self, body):
        id_aluno = body.get("idAluno", "")
        with _lock:
            a = _alunos.get(id_aluno)
            if a is None:
                self._enviar("aluno nao encontrado", 404)
                return
            for campo in _CAMPOS_TEXTO + _CAMPOS_SELECT + ["observacoes"]:
                if campo in body:
                    a[campo] = body[campo]
        self._enviar("ok")

    def _post_lista_turmas(self, body):
        with _lock:
            novo_id = str(_contador["turma"])
            _contador["turma"] += 1
            _turmas[novo_id] = {
                "abreviada": body.get("abreviada", "")[:5],
                "descricao": body.get("descricao", "")[:30],
                "professor": body.get("professor", ""),
                "entidade": body.get("entidade", ""),
            }
        self._enviar(f'<html><body><input name="idTurma" id="idTurma" value="{novo_id}" /></body></html>')


def subir_stub(porta):
    servidor = ThreadingHTTPServer(("127.0.0.1", porta), StubHandler)
    t = threading.Thread(target=servidor.serve_forever, daemon=True)
    t.start()
    return servidor

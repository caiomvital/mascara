"""
Testes de regressao pro modo manutencao (manutencao.py):
  - credencial de operador (usuario + hash de senha, com salt, nunca texto);
  - o ativar/desativar persistente.
Sem rede nenhuma.

Como rodar:
    cd testes_logica
    python test_manutencao.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import manutencao as m  # noqa: E402

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


# aponta o estado pra um arquivo temporario - nao toca no manutencao.json real
_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
_tmp.close()
os.unlink(_tmp.name)
m._PATH = _tmp.name

# operador de teste (nao mexe na tupla real)
_SALT, _HASH = m.gerar_hash_senha("senha-de-teste-123")
m.OPERADORES_MANUTENCAO = (
    ("op-teste", _HASH, _SALT, m._ITERACOES),
)


# ---------------------------------------------------------------------------
# credencial de operador (usuario + senha)
# ---------------------------------------------------------------------------
def teste_gerar_hash_senha_usa_salt_aleatorio():
    s1, h1 = m.gerar_hash_senha("abc")
    s2, h2 = m.gerar_hash_senha("abc")
    relatar(
        "gerar_hash_senha usa salt aleatório (mesma senha → salt e hash diferentes a cada chamada)",
        s1 != s2 and h1 != h2,
        f"s1={s1[:8]} s2={s2[:8]}",
    )


def teste_verificar_operador_aceita_credencial_certa():
    relatar(
        "verificar_operador aceita usuário + senha corretos",
        m.verificar_operador("op-teste", "senha-de-teste-123") is True,
        "não aceitou a credencial correta",
    )


def teste_verificar_operador_recusa_senha_errada():
    relatar(
        "verificar_operador recusa senha errada pro usuário certo",
        m.verificar_operador("op-teste", "outra-senha") is False,
        "aceitou senha errada",
    )


def teste_verificar_operador_recusa_usuario_desconhecido():
    relatar(
        "verificar_operador recusa usuário não configurado (ex: um login do Fuctura)",
        m.verificar_operador("09467914492", "senha-de-teste-123") is False,
        "aceitou usuário desconhecido",
    )


def teste_eh_operador_so_pelo_nome():
    relatar(
        "eh_operador reconhece o usuário configurado (sem validar senha)",
        m.eh_operador("op-teste") and not m.eh_operador("qualquer-outro"),
        "reconhecimento por usuário falhou",
    )


def teste_sem_operador_configurado_ninguem_passa():
    salvo = m.OPERADORES_MANUTENCAO
    m.OPERADORES_MANUTENCAO = ()
    try:
        ok = (not m.eh_operador("op-teste")
              and m.verificar_operador("op-teste", "senha-de-teste-123") is False)
    finally:
        m.OPERADORES_MANUTENCAO = salvo
    relatar(
        "com OPERADORES_MANUTENCAO vazio, ninguém é operador (modo manutenção indisponível)",
        ok,
        "alguém passou como operador sem credencial configurada",
    )


# ---------------------------------------------------------------------------
# ativar/desativar persistente
# ---------------------------------------------------------------------------
def teste_por_padrao_operacao_normal():
    if os.path.exists(m._PATH):
        os.unlink(m._PATH)
    relatar(
        "sem arquivo de estado, o sistema está em operação normal (não em manutenção)",
        m.em_manutencao() is False,
        f"em_manutencao() == {m.em_manutencao()!r}",
    )


def teste_ativar_persiste_quem_quando_motivo():
    r = m.ativar(por="op-teste", motivo="  atualização  ")
    lido = m.carregar()
    relatar(
        "ativar() marca ativo=True e registra por/quando/motivo (motivo com trim)",
        r["ativo"] is True and lido["ativo"] is True
        and lido["por"] == "op-teste" and lido["motivo"] == "atualização" and lido["quando"],
        f"estado lido: {lido}",
    )
    relatar(
        "em_manutencao() reflete o estado persistido (sobrevive a um novo carregar)",
        m.em_manutencao() is True,
        "em_manutencao() == False depois de ativar()",
    )


def teste_motivo_vazio_vira_none():
    m.ativar(por="op-teste", motivo="   ")
    relatar(
        "motivo só com espaço vira None",
        m.carregar()["motivo"] is None,
        f"motivo: {m.carregar()['motivo']!r}",
    )


def teste_desativar_volta_operacao_normal():
    m.ativar(por="op-teste", motivo="x")
    r = m.desativar(por="op-teste")
    relatar(
        "desativar() volta ativo=False e limpa o motivo, mantendo o rastro de quem/quando",
        r["ativo"] is False and m.em_manutencao() is False
        and m.carregar()["motivo"] is None and m.carregar()["por"] == "op-teste",
        f"estado: {m.carregar()}",
    )


def teste_arquivo_corrompido_nao_prende_o_sistema():
    with open(m._PATH, "w", encoding="utf-8") as f:
        f.write("{ isto nao e json valido")
    relatar(
        "arquivo de estado corrompido NÃO deixa o sistema preso em manutenção (fail-open)",
        m.em_manutencao() is False,
        f"em_manutencao() com arquivo corrompido == {m.em_manutencao()!r}",
    )


def main():
    print("Rodando testes de manutencao.py (modo manutenção, sem rede)...\n")
    teste_gerar_hash_senha_usa_salt_aleatorio()
    teste_verificar_operador_aceita_credencial_certa()
    teste_verificar_operador_recusa_senha_errada()
    teste_verificar_operador_recusa_usuario_desconhecido()
    teste_eh_operador_so_pelo_nome()
    teste_sem_operador_configurado_ninguem_passa()
    teste_por_padrao_operacao_normal()
    teste_ativar_persiste_quem_quando_motivo()
    teste_motivo_vazio_vira_none()
    teste_desativar_volta_operacao_normal()
    teste_arquivo_corrompido_nao_prende_o_sistema()

    try:
        if os.path.exists(m._PATH):
            os.unlink(m._PATH)
    except OSError:
        pass

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

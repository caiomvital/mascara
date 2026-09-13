"""
Script AVULSO, de uso unico, pra corrigir os 434 comentarios que o
incidente de flood de 04/09/2026 deixou com o texto generico
"--Removido Por Flood--" (tipo -Comentário). Pedido do Diogenes via Caio
(2026-09-11): trocar esse marcador por um resumo de debito de verdade, um
aluno de cada vez, sempre com confirmacao individual no momento de
gravar - NUNCA em lote (ver memoria
fuctura_mascara_confirmacao_humana_obrigatoria).

Isto NAO e uma funcionalidade do sistema-mascara. E uma correcao pontual
de um problema causado por um erro de execucao anterior (o proprio
flood). Depois que os 434 comentarios estiverem tratados, este arquivo
pode ser apagado - nao faz sentido ficar rodando de novo, ja que so
existe flood pra corrigir uma vez.

Cada aluno pode ter mais de um comentario de flood (o incidente bateu
nele mais de uma vez) - so o mais recente vira o alvo da reescrita; os
demais ficam registrados como "extras", intocados, pra nao duplicar o
mesmo resumo varias vezes no mesmo aluno.

Como rodar (sempre precisa disso primeiro):
    set FUCTURA_LOGIN=seu_login
    set FUCTURA_SENHA=sua_senha
    python limpeza_flood_04_09.py

Um checkpoint (limpeza_flood_04_09_progresso.json) guarda quais alunos ja
foram tratados (gravado, pulado ou marcado como erro), pra poder
interromper e continuar depois sem perder o lugar - ele so serve de
bookkeeping local, nao afeta o Fuctura nem grava nada sozinho.
"""
import json
import os
import sys
import time

import reconciliacao_devedor as reconciliacao
import fechamento_logic as logic
from fuctura_client import FucturaClient, FucturaAuthError

ARQUIVO_PROGRESSO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "limpeza_flood_04_09_progresso.json")
PAUSA_ANTES_DE_LER_APOS_GRAVAR_SEGUNDOS = 5  # achado real 2026-09-11: leitura logo apos gravar pode pegar valor velho


def login_fuctura():
    login = os.environ.get("FUCTURA_LOGIN")
    senha = os.environ.get("FUCTURA_SENHA")
    if not login or not senha:
        print("Defina FUCTURA_LOGIN e FUCTURA_SENHA como variável de ambiente antes de rodar.")
        sys.exit(1)
    print(f"Logando como {login}...")
    client = FucturaClient(login, senha)
    try:
        client.entrar()
    except FucturaAuthError as e:
        print(f"Falha no login: {e}")
        sys.exit(1)
    print(f"Login OK - {client.nome_usuario}\n")
    return client


def carregar_progresso():
    if os.path.exists(ARQUIVO_PROGRESSO):
        with open(ARQUIVO_PROGRESSO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def salvar_progresso(progresso):
    with open(ARQUIVO_PROGRESSO, "w", encoding="utf-8") as f:
        json.dump(progresso, f, ensure_ascii=False, indent=2)


def montar_resumo_debito(client, perfil, comentarios):
    """Mesma logica de cruzamento ja usada no fechamento (nota_devedor +
    checagem_financeira) e no resumo de historico da Reconciliação - nao
    e um mecanismo novo, sao os mesmos dados ja cruzados em outros
    lugares do sistema, aplicados aqui pra montar o texto de reposicao."""
    financeiro = logic.checagem_financeira(perfil, comentarios)
    controle = client.ids_turmas_controle_detalhado()
    nota_dev = logic.nota_devedor(perfil, financeiro["tem_debito"], controle["devedor"], controle["advogado"])
    partes = []
    if nota_dev:
        partes.append(nota_dev)
    if financeiro["inconsistencias"]:
        partes.append("; ".join(financeiro["inconsistencias"]))
    if not partes:
        partes.append("Sem débito em aberto identificado nesta revisão (situação pode já estar regularizada).")
    partes.append("Resumo do histórico: " + reconciliacao.resumir_comentarios(comentarios))
    return " ".join(partes)


def levantar_alvos(client):
    print("Levantando comentários do flood (04/09, '--Removido Por Flood--')...")
    brutos = client.buscar_comentarios_por_tipo(
        "3", termo="Removido Por Flood", max_registros=1000, recentes_primeiro=True
    )
    vistos = {}
    for c in brutos:
        id_aluno, nome = client.resolver_aluno_por_acomp(c["id_acomp"])
        if not id_aluno:
            continue
        if id_aluno in vistos:
            vistos[id_aluno]["extras"] += 1
        else:
            vistos[id_aluno] = {"nome": nome, "id_acomp_alvo": c["id_acomp"], "extras": 0}
    print(f"{len(brutos)} comentários de flood, {len(vistos)} alunos únicos.\n")
    return vistos


def perguntar(pergunta):
    resp = input(pergunta).strip().lower()
    return resp


def processar_aluno(client, id_aluno, alvo, progresso):
    nome = alvo["nome"] or "(nome não resolvido)"
    print(f"\n{'-' * 70}")
    print(f"Aluno: {nome} (id {id_aluno}) — comentário alvo id_acomp {alvo['id_acomp_alvo']}")
    if alvo["extras"]:
        print(f"  (+{alvo['extras']} outro(s) comentário(s) de flood neste aluno, não mexidos)")

    perfil = client.perfil_aluno(id_aluno)
    comentarios = client.comentarios_aluno(id_aluno)
    resumo = montar_resumo_debito(client, perfil, comentarios)
    print(f"\nResumo sugerido:\n  {resumo}\n")

    resp = perguntar("Gravar este resumo? [s]im / [n]ão (pular) / [e]ditar antes de gravar: ")
    if resp == "n" or resp == "":
        print("Pulado.")
        progresso[id_aluno] = {"status": "pulado", "nome": nome}
        return

    texto_final = resumo
    editado = False
    if resp == "e":
        print("Cole o texto final e pressione Enter (uma linha só):")
        texto_editado = input("> ").strip()
        if texto_editado and texto_editado != resumo.strip():
            texto_final = texto_editado
            editado = True
    elif resp != "s":
        print("Resposta não reconhecida, pulando por segurança.")
        progresso[id_aluno] = {"status": "pulado", "nome": nome}
        return

    prefixo = logic.PREFIXO_TEXTO_EDITADO if editado else logic.PREFIXO_TEXTO_ORIGINAL
    assunto = f"{prefixo}{reconciliacao.ASSUNTO_RESUMO_DEBITO_FLOOD}"
    status = client.editar_comentario(id_aluno, alvo["id_acomp_alvo"], assunto=assunto, tipo="3", descricao=texto_final)
    if status != 200:
        print(f"ERRO: status HTTP {status} ao gravar.")
        progresso[id_aluno] = {"status": "erro", "nome": nome, "http_status": status}
        return

    print(f"Gravado (HTTP {status}). Conferindo...")
    time.sleep(PAUSA_ANTES_DE_LER_APOS_GRAVAR_SEGUNDOS)
    comentarios_pos = client.comentarios_aluno(id_aluno)
    conferido = next((c for c in comentarios_pos if c["id_acomp"] == alvo["id_acomp_alvo"]), None)
    if conferido and conferido["texto"].strip() == texto_final.strip():
        print("Confirmado: a leitura bate com o que foi gravado.")
        progresso[id_aluno] = {"status": "gravado", "nome": nome, "editado": editado}
    else:
        print("ATENÇÃO: a leitura de conferência NÃO bateu com o texto gravado - revise manualmente este aluno.")
        progresso[id_aluno] = {"status": "gravado_sem_confirmar_leitura", "nome": nome, "editado": editado}


def main():
    client = login_fuctura()
    progresso = carregar_progresso()
    alvos = levantar_alvos(client)

    pendentes = {id_aluno: alvo for id_aluno, alvo in alvos.items() if id_aluno not in progresso}
    ja_tratados = len(alvos) - len(pendentes)
    if ja_tratados:
        print(f"{ja_tratados} aluno(s) já tratados em execuções anteriores (ver {ARQUIVO_PROGRESSO}), pulando esses.\n")

    if not pendentes:
        print("Nada pendente. Todos os alunos do flood já foram tratados.")
        return

    print(f"{len(pendentes)} aluno(s) pendente(s). Confirmação individual a cada um - digite Ctrl+C a qualquer momento pra parar (o progresso já feito fica salvo).\n")

    try:
        for id_aluno, alvo in pendentes.items():
            processar_aluno(client, id_aluno, alvo, progresso)
            salvar_progresso(progresso)
    except KeyboardInterrupt:
        print("\n\nInterrompido pelo usuário. Progresso salvo - rode de novo pra continuar de onde parou.")
        salvar_progresso(progresso)
        return

    print(f"\n{'=' * 70}")
    print("Concluído. Resumo desta execução + anteriores:")
    contagens = {}
    for v in progresso.values():
        contagens[v["status"]] = contagens.get(v["status"], 0) + 1
    for status, qtd in contagens.items():
        print(f"  {status}: {qtd}")


if __name__ == "__main__":
    main()

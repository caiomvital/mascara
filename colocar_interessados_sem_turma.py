"""
Script AVULSO, de uso unico, pra colocar na turma de triagem "-IA-
Interessados" os alunos com Situação=Interessado que foram cadastrados
(resumo registrado) mas nunca ficaram em turma nenhuma - achado real
levantado em 2026-09-12/13 (pedido do Diógenes via Caio) rodando uma
auditoria contra o painel de configuração do Fuctura, ano por ano
(2026: 33 encontrados: 2025: 59 encontrados).

NAO substitui a decisão de qual curso o interessado quer - "-IA-
Interessados" é só uma turma de TRIAGEM (deixa de estar invisível
enquanto ninguém decide em qual "-I-(curso)" específico ele deveria
entrar). Grava, na matrícula, um resumo determinístico (sem IA) do que já
estava registrado no histórico do aluno - pedido explícito do usuário
(2026-09-13).

Confirmação individual em cada aluno antes de gravar - NUNCA em lote (ver
memória fuctura_mascara_confirmacao_humana_obrigatoria).

Como rodar:
    set FUCTURA_LOGIN=seu_login
    set FUCTURA_SENHA=sua_senha
    python colocar_interessados_sem_turma.py

Um checkpoint (colocar_interessados_sem_turma_progresso.json) guarda quem
já foi tratado, pra poder interromper e continuar sem perder o lugar.
"""
import json
import os
import sys

import reconciliacao_devedor as reconciliacao
from fuctura_client import FucturaClient, FucturaAuthError

ARQUIVO_PROGRESSO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "colocar_interessados_sem_turma_progresso.json")
NOME_TURMA_TRIAGEM = "-IA- Interessados"

# Resultado da auditoria (2026-09-12/13) - "sem turma alguma" cruzado
# individualmente via perfil_aluno(), depois de tirar RESERVA/DUPLICADO/
# TESTE/lixo. Ver STATUS.md pra detalhes de como foi levantado.
ALVOS = {
    # 2026 (33)
    "49935": "CLAUDIO JOSÉ NEVES BAPTISTA NETO", "49911": "ALLANA LIZ SANTOS DA SILVA",
    "49910": "DAVI PHILLIP BATISTA CESARIO", "49909": "GABRIELLY DE MOURA ATIADI",
    "49907": "NARA", "49894": "ROSANE BASTOS", "49889": "WYLLARI VITÓRIA DA SILVA SANTOS",
    "49883": "HIGOR CÉZAR LEITE DA CRUZ", "49858": "SHERLITON MICHEL FERREIRA DE LIMA",
    "49810": "DIEGO VENANCIO GONÇALVES", "49757": "FELIPE AUGUSTO GERMANO RIBEIRO",
    "49756": "CHARLES RIBEIRO", "49752": "RODRIGO BENTO", "49749": "CAUÃ HENRY",
    "49736": "KAYO PHILIPS SOARES DE BARROS", "49733": "RICHARD OZIEL", "49732": "MARIA CLARA",
    "49716": "RENATO FLÁVIO FARIAS PEREIRA SOARES", "49710": "AIYMARA EWE DE SANTANA CAVALCANTI",
    "49705": "DIEGO JOSÉ DA SILVA GOMES", "49703": "CARLOS ALBERTO CABRAL",
    "49701": "KAMILA DUTRA GAMA", "49699": "EDGILSON MAIA LINS", "49681": "HORTENCIO SILVA",
    "49680": "HEMILY MOURA", "49672": "VICTOR MOURA", "49656": "JEOSAFA OLIVEIRA DE MOURA",
    "49597": "HENRIQUE SAMPAIO CAVALCANTI", "49540": "GIOVANE SERGIO DE OLIVEIRA DA SILVA",
    "49468": "CHARLES", "49194": "CARLOS NASCIMENTO", "49185": "LUCAS FONSECA DE DANTAS ARAUJO",
    "49090": "RAPHAEL HENRIQUE DA SILVEIRA DE SOUZA",
    # 2025 (59)
    "49005": "ERIVANIA", "48947": "PAULETE GONÇALVES DA COSTA(DUPLICATO)",
    "48942": "CHRESTIAN TAVARES GONÇALVES", "48920": "EFRAIN", "48918": "EDUARTE FABRÍCIO DOS SANTOS",
    "48760": "JANE CLEIDE", "48727": "POLIANA MARIA", "48697": "KARINA", "48541": "ELISSANDRA",
    "48470": "RAFAEL DOUGLAS", "48394": "FELIX LEVY MELINA", "47979": "IGOR", "47948": "ROSE",
    "47577": "ANTONIO FALCAO", "47440": "FAMILIA PROJETO DE DEUS", "47412": "SAMUEL(ERRADO)",
    "47362": "DAVID", "47191": "FLAVIA", "47190": "FERNANDO MENDONÇA", "47189": "KELLY",
    "47151": "LUCIANA", "47138": "RUBENITA FERREIRA", "47100": "WALDOMIRO QUEIROZ",
    "47095": "WILMA RAMOS", "47078": "RYAN", "47077": "ELIZABETHY", "47071": "RAFA PIRES",
    "47001": "RAFAEL", "46839": "X", "46805": "VIVIANE",
    "46448": "JOÃO FILIPE DOMINGOS CAETANO DA SILVA", "45843": "FERNANDA SILVA", "45676": "L",
    "45603": "MERCIA BATISTA", "45441": "SOARES", "45425": "ÍTALO", "45393": "FABIANA",
    "45108": "SENHORA", "44977": "JOSÉ TEÓFILO BATISTA DE QUEIROZ JÚNIOE",
    "44976": "HUMBERTO JORGE ALVES PEREIRA", "44954": "JURACY MAGALHÃES", "44953": "RENNATA",
    "44746": "RAFAELE", "44695": "SUANE MARTINS", "44651": "LUCAS EMANUEL",
    "44650": "LUCAS EMANUEL", "44644": "UMBERTO", "44643": "UMBERTO", "44642": "LUZIANA COSTA",
    "44597": "NEUDY ALMEIDA", "44571": "MARGARETE", "44489": "ADRIANA PAULA", "44487": "WANIERY",
    "44484": "NATHUZA", "44380": "SHEILA FREITAS", "44316": "CABRAL", "44149": "GEISIANE KEYLA",
    "44141": "ALUGUEL DE SALA", "43365": "JANDERSON FEITOSA",
}


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


def garantir_turma_triagem(client):
    """Acha a turma "-IA- Interessados" pelo nome, ou cria se ainda não
    existir - só faz isso uma vez, não recria a cada execução."""
    encontradas = client.buscar_turma_por_nome(NOME_TURMA_TRIAGEM)
    for t in encontradas:
        if t["nome"].strip() == NOME_TURMA_TRIAGEM:
            print(f'Turma "{NOME_TURMA_TRIAGEM}" já existe (id {t["id_turma"]}).\n')
            return t["id_turma"]
    print(f'Turma "{NOME_TURMA_TRIAGEM}" não existe ainda - criando...')
    id_turma = client.criar_turma(
        abreviada="IAINT", entidade="3", descricao=NOME_TURMA_TRIAGEM, professor="",
    )
    if not id_turma:
        print("Falha ao criar a turma de triagem. Abortando.")
        sys.exit(1)
    print(f"Turma criada, id {id_turma}.\n")
    return id_turma


def perguntar(pergunta):
    return input(pergunta).strip().lower()


def processar_aluno(client, id_turma, id_aluno, nome, progresso):
    print(f"\n{'-' * 70}")
    print(f"Aluno: {nome} (id {id_aluno})")

    perfil = client.perfil_aluno(id_aluno)
    comentarios = client.comentarios_aluno(id_aluno)
    resumo = reconciliacao.resumir_comentarios(comentarios)
    print(f"\nResumo do histórico:\n  {resumo}\n")

    resp = perguntar('Colocar em "-IA- Interessados" com esse resumo? [s]im / [n]ão (pular) / [e]ditar antes: ')
    if resp == "n" or resp == "":
        print("Pulado.")
        progresso[id_aluno] = {"status": "pulado", "nome": nome}
        return

    texto_final = resumo
    if resp == "e":
        print("Cole o texto final e pressione Enter (uma linha só):")
        texto_editado = input("> ").strip()
        if texto_editado:
            texto_final = texto_editado
    elif resp != "s":
        print("Resposta não reconhecida, pulando por segurança.")
        progresso[id_aluno] = {"status": "pulado", "nome": nome}
        return

    status = client.gravar_comentario(
        id_aluno, NOME_TURMA_TRIAGEM.upper(), "15", texto_final,
        turma_id=id_turma, valor_contratado="0,00", forma_pagamento="---", turma_nome=NOME_TURMA_TRIAGEM,
    )
    if status != 200:
        print(f"ERRO: status HTTP {status} ao gravar.")
        progresso[id_aluno] = {"status": "erro", "nome": nome, "http_status": status}
        return
    print(f"Gravado (HTTP {status}).")
    progresso[id_aluno] = {"status": "gravado", "nome": nome}


def main():
    client = login_fuctura()
    id_turma = garantir_turma_triagem(client)
    progresso = carregar_progresso()

    pendentes = {i: n for i, n in ALVOS.items() if i not in progresso}
    ja_tratados = len(ALVOS) - len(pendentes)
    if ja_tratados:
        print(f"{ja_tratados} já tratados em execuções anteriores (ver {ARQUIVO_PROGRESSO}), pulando esses.\n")

    if not pendentes:
        print("Nada pendente. Todos já foram tratados.")
        return

    print(f"{len(pendentes)} pendente(s). Confirmação individual a cada um - Ctrl+C a qualquer momento pra parar (progresso salvo).\n")

    try:
        for id_aluno, nome in pendentes.items():
            processar_aluno(client, id_turma, id_aluno, nome, progresso)
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

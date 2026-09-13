"""
Reune os anexos pro email de cobrança/jurídico de um aluno - cópia do
contrato e cópia da(s) ata(s) PREENCHIDA(S) de cada turma que ele
participou (pedido do usuário, 2026-09-07). ESTRUTURA PRONTA - o
conteúdo/template do próprio email ainda vai ser definido depois; isso só
junta os arquivos, pra quando o template chegar bastar plugar aqui.

IMPORTANTE (correção do usuário, 2026-09-07): a ata que interessa aqui é a
ata PREENCHIDA de verdade - o arquivo que o funcionário fotografa/escaneia
e sobe no Fechamento de Turma, com presença real marcada - NÃO a "Ata de
Chamada" que o Fuctura gera pelo relatório oficial (essa vem sempre EM
BRANCO, é só um roster de quem está vinculado à turma, não prova
frequência nenhuma). Os uploads reais ficam em atas_recebidas/<job_id>/.

Decisão do usuário (2026-09-07): quando uma turma tem várias atas
preenchidas ao longo do tempo, anexar TODAS (registro mais completo como
prova), não só a mais recente.

So LEITURA - nunca grava nada no Fuctura, so le PDF que ja existem la e
arquivos que ja estao no disco local.
"""
import json
import os

from fuctura_client import TURMA_ADVOGADO_ID, TURMA_DEVEDOR_ID

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atas_recebidas")

# Turmas de CONTROLE administrativo (Devedor/Pendencia, Aguardando Advogado)
# nunca tem ata de chamada de verdade - nao sao aula nenhuma, so um marcador
# de status. Achado ao vivo em 2026-09-08 testando a Reconciliacao de
# Devedores de verdade: TODO aluno devedor esta nessa turma de controle, entao
# sem esse filtro o aviso "nenhuma ata preenchida encontrada" aparecia pra
# ELE em 100% dos alunos analisados - ruido garantido que escondia os avisos
# que realmente importam (turma ambigua, ata faltando de verdade etc.).
_TURMAS_CONTROLE_SEM_ATA = {TURMA_DEVEDOR_ID, TURMA_ADVOGADO_ID}


def _norm_para_comparar_turma(s):
    """Mesma normalização de fechamento_logic.norm(), mas tratando '/',
    '_' e '-' como equivalentes - necessário pro fallback por nome de
    arquivo, já que "/" na turma vira "_" no nome do arquivo salvo em
    disco (ex: turma "J1 26/05/26 TER N" -> arquivo "J1 26_05_26 TER
    N.pdf")."""
    import re as _re
    import fechamento_logic as logic
    s = _re.sub(r"[/_-]", " ", s or "")
    return logic.norm(s)


def localizar_atas_preenchidas(turma_id, turma_nome):
    """Acha TODOS os arquivos de ata PREENCHIDA (upload real de Fechamento
    de Turma) que pertencem a uma turma.

    Prioriza o metadata.json (gravado a partir de 2026-09-07 em cada
    upload, ver app.py:_iniciar_fechamento_impl) comparando por
    turma_id - exato e confiável. Pastas de uploads ANTERIORES a essa data
    não têm metadata.json; pra essas, cai no nome do arquivo dentro da
    pasta comparado com turma_nome (normalização tolerante a acentuação e
    a "/" vs "_") - menos confiável, então cada resultado indica a origem
    ('metadata' ou 'nome_arquivo') pra quem revisar saber o nível de
    confiança.

    Retorna lista ordenada por data de upload, cada item:
        {"caminho", "nome_arquivo", "origem": "metadata"|"nome_arquivo", "data_upload"}"""
    if not os.path.isdir(UPLOAD_DIR):
        return []

    alvo_normalizado = _norm_para_comparar_turma(turma_nome) if turma_nome else None
    encontradas = []

    for job_id in os.listdir(UPLOAD_DIR):
        pasta_job = os.path.join(UPLOAD_DIR, job_id)
        if not os.path.isdir(pasta_job):
            continue

        meta = None
        caminho_meta = os.path.join(pasta_job, "metadata.json")
        if os.path.isfile(caminho_meta):
            try:
                with open(caminho_meta, encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, json.JSONDecodeError):
                meta = None

        if meta is not None:
            if turma_id and str(meta.get("turma_id")) == str(turma_id):
                for nome_arquivo in meta.get("arquivos", []):
                    if nome_arquivo.lower().endswith(".pdf"):
                        encontradas.append({
                            "caminho": os.path.join(pasta_job, nome_arquivo), "nome_arquivo": nome_arquivo,
                            "origem": "metadata", "data_upload": meta.get("criado_em", ""),
                        })
            # tem metadata mas e de outra turma - nunca cai no fallback de
            # nome de arquivo pra essa pasta (o metadata ja e a resposta certa)
            continue

        # pasta antiga sem metadata.json - fallback por nome de arquivo
        if not alvo_normalizado:
            continue
        try:
            arquivos_pasta = os.listdir(pasta_job)
        except OSError:
            continue
        for nome_arquivo in arquivos_pasta:
            if not nome_arquivo.lower().endswith(".pdf"):
                continue
            nome_sem_ext = os.path.splitext(nome_arquivo)[0]
            if _norm_para_comparar_turma(nome_sem_ext) == alvo_normalizado:
                encontradas.append({
                    "caminho": os.path.join(pasta_job, nome_arquivo), "nome_arquivo": nome_arquivo,
                    "origem": "nome_arquivo", "data_upload": "",
                })

    encontradas.sort(key=lambda e: e["data_upload"] or "")
    return encontradas


def montar_anexos_aluno(client, id_aluno):
    """Retorna:
        {
            "aluno": "NOME DO ALUNO",
            "anexos": [{"nome_arquivo": "...", "conteudo": bytes, "tipo": "application/pdf"}, ...],
            "avisos": ["turma X não encontrada, conferir manualmente", ...],
        }

    Sempre tenta incluir o contrato do aluno e a ata oficial de CADA turma
    que aparece no cadastro atual (perfil_aluno()['turmas_atuais']).

    Resolver nome de turma -> id usa buscar_turma_por_nome() (autocomplete
    fuzzy) - só anexa quando o nome bate de forma INEQUÍVOCA (exatamente 1
    resultado). Turma ambígua ou não encontrada fica de fora do anexo e
    vira um aviso pra conferência manual - melhor faltar um anexo (o
    humano percebe e resolve na hora) do que anexar a ata da turma
    errada por engano."""
    perfil = client.perfil_aluno(id_aluno)
    nome_aluno = perfil.get("nome") or f"aluno_{id_aluno}"

    anexos = []
    avisos = []

    contrato_pdf = client.gerar_contrato_aluno(id_aluno)
    if contrato_pdf:
        anexos.append({"nome_arquivo": f"contrato_{nome_aluno}.pdf", "conteudo": contrato_pdf, "tipo": "application/pdf"})
    else:
        avisos.append("Não foi possível gerar o contrato (Fuctura não retornou um PDF).")

    nomes_vistos = set()
    for turma in perfil.get("turmas_atuais", []):
        nome_turma = (turma.get("nome") or "").strip()
        if not nome_turma or nome_turma in nomes_vistos:
            continue
        nomes_vistos.add(nome_turma)

        candidatos = client.buscar_turma_por_nome(nome_turma)
        if len(candidatos) != 1:
            motivo = "não encontrada" if not candidatos else f"nome ambíguo ({len(candidatos)} turmas parecidas)"
            avisos.append(f'Turma "{nome_turma}": {motivo} - ata não anexada automaticamente, conferir manualmente.')
            continue

        id_turma = candidatos[0]["id_turma"]
        if id_turma in _TURMAS_CONTROLE_SEM_ATA:
            continue  # turma de controle administrativo - nunca tem ata, nem avisa

        atas = localizar_atas_preenchidas(id_turma, nome_turma)
        if not atas:
            avisos.append(
                f'Turma "{nome_turma}": nenhuma ata preenchida encontrada em atas_recebidas/ '
                f"(fechamento feito antes do sistema-máscara existir, ou upload não localizado) - conferir manualmente."
            )
            continue

        for i, ata in enumerate(atas, start=1):
            try:
                with open(ata["caminho"], "rb") as f:
                    conteudo = f.read()
            except OSError as e:
                avisos.append(f'Turma "{nome_turma}": falha lendo {ata["nome_arquivo"]} ({e}).')
                continue
            sufixo = f"_{i}" if len(atas) > 1 else ""
            anexos.append({"nome_arquivo": f"ata_{nome_turma}{sufixo}.pdf", "conteudo": conteudo, "tipo": "application/pdf"})
            if ata["origem"] == "nome_arquivo":
                avisos.append(
                    f'Turma "{nome_turma}": ata "{ata["nome_arquivo"]}" encontrada por correspondência de nome de '
                    f"arquivo (upload anterior a 07/09/2026, sem índice exato) - vale conferir se é mesmo dessa turma."
                )

    return {"aluno": nome_aluno, "anexos": anexos, "avisos": avisos}

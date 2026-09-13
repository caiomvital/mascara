"""
Monta o CONTEÚDO do email de cobrança/jurídico de um aluno - corpo com
dados pessoais, resumo de pagamentos/desistência, valores em aberto
(CORA) e turmas, mais a lista de anexos que seriam usados (ver
anexos_email.py). Pedido do usuário, 2026-09-07.

So LEITURA - nunca envia nada, nunca cria rascunho nenhum. Criar o
rascunho de verdade no Gmail é um passo SEPARADO (mexe na conta real do
usuário, precisa de confirmação explícita) - isto aqui só junta os dados
pra revisão antes desse passo final.

Resumo de pagamentos/desistência é DETERMINÍSTICO por decisão explícita
do usuário (2026-09-07): nunca IA livre num documento que pode virar
prova jurídica - só filtra e apresenta comentários reais, em ordem
cronológica, sem inventar ou interpretar nada.
"""
import re

import cora_client
import fechamento_logic as logic
import reconciliacao_devedor as reconciliacao
from anexos_email import montar_anexos_aluno

_CAMPOS_CORPO = [
    ("nome", "Nome"), ("cpf", "CPF"), ("celular", "Telefone"),
    ("endereco", "Endereço"), ("bairro", "Bairro"), ("cidade", "Cidade"),
    ("cep", "CEP"), ("email", "Email"),
]


def determinar_responsavel_contrato(cadastro):
    """ATENÇÃO: isto NÃO é o destinatário do e-mail. Correção do usuário
    (2026-09-08): o e-mail é um RASCUNHO pra ser enviado ao ADVOGADO -
    serve de controle de informações pro caso, mas nunca é enviado ao
    aluno/responsável diretamente. O que esta função identifica é só QUEM
    ASSINOU o contrato (pra contexto do advogado no corpo do e-mail):
    quando existe responsável cadastrado (legal, de menor de idade, ou um
    terceiro adulto que paga - ver app._validar_cpf_e_responsavel, o campo
    cobre os dois casos), é ele quem assinou/é o contato de cobrança de
    verdade, não o aluno sozinho. Só usa o aluno quando não há nenhum
    responsável registrado.

    O destinatário real (endereço do advogado/escritório) ainda está
    pendente - ver STATUS.md ("bloqueado por algo externo").

    NÃO confundir com a consulta na CORA (email_cobranca.montar_preview_email)
    - essa continua sempre pelo CPF do PRÓPRIO ALUNO, porque é nele que o
    boleto é emitido (confirmado pelo usuário, 2026-09-07)."""
    if (cadastro.get("nomeResponsavel") or "").strip():
        responsavel = {
            "papel": "responsavel",
            "nome": cadastro.get("nomeResponsavel") or "—",
            "cpf": cadastro.get("cpfResponsavel") or "—",
            "telefone": cadastro.get("celularResponsavel") or cadastro.get("telefoneResponsavel") or "—",
            "email": cadastro.get("emailResponsavel") or "—",
        }
    else:
        responsavel = {
            "papel": "aluno",
            "nome": cadastro.get("nome") or "—",
            "cpf": cadastro.get("cpf") or "—",
            "telefone": cadastro.get("celular") or cadastro.get("fixo") or "—",
            "email": cadastro.get("email") or "—",
        }
    # achado real (caso Vitor Fonseca Veloso): cadastro antigo, anterior a
    # validacao de CPF, pode ter um CPF com numero errado de digitos (ex:
    # um telefone digitado no campo errado) - sinaliza pra revisao manual
    # em vez de deixar passar batido numa prova juridica/cobranca.
    cpf_digitos = re.sub(r"\D", "", responsavel["cpf"] or "")
    responsavel["cpf_suspeito"] = bool(cpf_digitos) and len(cpf_digitos) != 11
    return responsavel


def _relevante_pagamento_desistencia(comentario):
    texto_full = comentario["titulo"] + " " + comentario["texto"]
    return bool(
        reconciliacao.BOLETO_PAGAMENTO_RE.search(texto_full)
        or logic.houve_sinal_cancelamento(texto_full)
        or reconciliacao.ESTORNO_RE.search(texto_full)
    )


def montar_resumo_pagamentos_desistencia(comentarios):
    """Junta, em ordem cronológica, os comentários reais que mencionam
    pagamento/boleto/cancelamento/estorno - nada além disso. Ignora o
    próprio comentário automático de reconciliação (mesmo filtro já usado
    em resumir_comentarios/ja_com_advogado, ver
    reconciliacao_devedor.ASSUNTO_ANALISE_AUTOMATICA)."""
    relevantes = [
        c for c in comentarios
        if c["titulo"] != reconciliacao.ASSUNTO_ANALISE_AUTOMATICA and _relevante_pagamento_desistencia(c)
    ]
    if not relevantes:
        return "Nenhum registro de pagamento, boleto, cancelamento ou estorno encontrado no histórico deste aluno."
    linhas = []
    for c in relevantes:
        if c["texto"]:
            linhas.append(f"{c['data']} — {c['titulo']}: {c['texto']}")
        else:
            linhas.append(f"{c['data']} — {c['titulo']}")
    return "\n".join(linhas)


def montar_dados_corpo(cadastro):
    return [{"rotulo": rotulo, "valor": cadastro.get(campo) or "—"} for campo, rotulo in _CAMPOS_CORPO]


def montar_preview_email(client, id_aluno):
    """Junta tudo que o corpo/anexos do email vão precisar, sem enviar nem
    criar rascunho nenhum - pra revisão antes desse passo final."""
    cadastro = client.buscar_cadastro_completo(id_aluno)
    perfil = client.perfil_aluno(id_aluno)
    comentarios = client.comentarios_aluno(id_aluno)

    boletos_cora = None  # None = CORA ainda não configurado
    if cora_client.esta_configurado():
        try:
            lista = cora_client.CoraClient().listar_boletos_em_aberto(cadastro.get("cpf", ""))
            boletos_cora = cora_client.montar_resumo_boletos(cadastro.get("nome", ""), lista)
        except Exception as e:
            boletos_cora = {"erro": str(e)}

    resultado_anexos = montar_anexos_aluno(client, id_aluno)
    responsavel_contrato = determinar_responsavel_contrato(cadastro)

    return {
        "dados_pessoais": montar_dados_corpo(cadastro),
        # rascunho vai pro ADVOGADO, nunca pro aluno/responsavel direto -
        # isto e so informacao de quem assinou o contrato, pro contexto do
        # advogado. Endereco real do advogado (o "Para:" de verdade)
        # continua pendente (ver STATUS.md).
        "responsavel_contrato": responsavel_contrato,
        "destinatario_email_pendente": True,
        "resumo_pagamentos_desistencia": montar_resumo_pagamentos_desistencia(comentarios),
        "boletos_cora": boletos_cora,
        "turmas": perfil.get("turmas_atuais", []),
        "anexos": [{"nome_arquivo": a["nome_arquivo"], "tamanho": len(a["conteudo"])} for a in resultado_anexos["anexos"]],
        "avisos_anexos": resultado_anexos["avisos"],
    }

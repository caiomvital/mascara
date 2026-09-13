# Rascunho Técnico — Automação de Análise Pré-Jurídico (Aluno → CORA → Drive → E-mail)

Investigação feita em 2026-09-03, código existente + testes ao vivo contra Fuctura e contra os conectores de Google Drive/Gmail desta sessão. Nenhum arquivo de produção foi alterado; nada foi implementado ainda. Aguardando aprovação das decisões da seção 11 antes de programar.

## 1. Fluxo geral

```
Listar candidatos (Devedor/Pendência + Aguardando Advogado, ordenado por data de entrada ASC)
  → filtrar: ≥1 ano desde 1ª matrícula na escola
  → ordenar prioridade: tem registro em "Academia Java/Python Full Stack"? primeiro
  → [DRY RUN mostra a lista aqui e para — nada abaixo roda sem confirmação por aluno]
  → selecionar 1 aluno
  → buscar dados cadastrais (CPF, email, endereço...) via detalhes_alunos.php
  → buscar turmas participadas (já vem do perfil)
  → consultar CORA por CPF (boletos LATE/OPEN)
  → buscar atas no Drive (por nome/CPF do aluno)
  → buscar contrato no Drive
  → gerar resumo textual (fatos apenas, sem inferir causa)
  → montar corpo do e-mail (dados + resumo + tabela de boletos + turmas + docs)
  → criar RASCUNHO no Gmail com os PDFs anexados (nunca enviar)
  → registrar decisão/resultado pra auditoria
```

## 2. O que já existe (reaproveitável)

Do `fuctura-mascara` (este projeto):
- `FucturaClient` — sessão autenticada, `roster_turma(id)` (já usado pra Devedor=1138 e Advogado=1136), `perfil_aluno(id)` (nome, celular, status, **turmas_atuais com datas** — já é a fonte de "turmas participadas" e da data da "Academia Java/Python Full Stack"), `comentarios_aluno(id)`, `gravar_comentario(...)`.
- `casar_nome()` / `casar_por_telefone()` — já lidam com nomes ruidosos do Fuctura (parênteses, sobrenomes comuns) — mesma lógica serve pra casar aluno no CORA/Drive.
- `contexto_aluno_fora_da_turma()` e o padrão de "contexto informativo, nunca decisão automática" — mesmo princípio se aplica aqui.
- `gerar_ata_chamada_oficial()` — já sabe extrair texto de PDF/relatório do painel (usa PyMuPDF) — mesma técnica serve pra ler PDFs baixados do Drive se precisar.
- A memória `fuctura-devedor-filtro-confiabilidade` já registrada é **diretamente aplicável aqui**: campos de status/saldo do Fuctura mentem, cancelamento aparece só em texto livre, tem que ler comentário por comentário antes de indicar pro jurídico.

Do `fuctura-propensao-pagamento` (projeto irmão):
- `devedor_review_app.py` já tem: `listar_devedores_ao_vivo()`, `buscar_ids_turmas_controle()`, detecção de cancelamento/abandono por regex em comentários, e — importante — **já existe uma planilha no Drive** (`alunos_fuctura_priorizados_cobranca`) com um scoring de "chance de recuperação"/"prioridade de cobrança" por aluno, feita numa rodada anterior. Vale conversar se essa planilha deve alimentar a priorização em vez de recalcular do zero.
- `fetch_cpf()` — já resolvia CPF via um endpoint diferente (`detalhes_alunos.php?id=`), que investiguei agora e é muito mais rico do que parecia (ver seção 4).

Do ambiente desta sessão (conectores já disponíveis, não fazem parte do código do projeto):
- **Gmail** (`mcp__claude_ai_Gmail__*`) — `create_draft` cria rascunho com anexos, sem enviar. Testei: está conectado a `caiomvital@gmail.com`.
- **Google Drive** (`mcp__claude_ai_Google_Drive__*`) — `search_files`, `read_file_content`, `download_file_content`. Testei: está conectado à conta pessoal, e **não encontrei atas nem contratos de aluno nela** (ver seção 6 — pendência real).

## 3. O que precisará ser criado

- Serviço de seleção/priorização de candidatos (novo).
- Cálculo de "tempo de matrícula" e "tem registro Full Stack" a partir de `turmas_atuais`.
- Cliente CORA (novo módulo, `cora_client.py` no mesmo estilo de `fuctura_client.py`).
- Busca e download de atas/contrato no Drive certo (depende de decisão, seção 6).
- Gerador de resumo textual (regras determinísticas, extrai fatos de comentários/CORA e monta frases — não é a IA "inventando").
- Montagem do corpo do e-mail + anexos + criação do rascunho via Gmail.
- Modo DRY RUN.
- Log de auditoria (quem rodou, quando, o que decidiu, o que foi pulado).

## 4. Dados necessários

| Informação | Onde está | Como será obtida |
|---|---|---|
| Nome, status | Fuctura, `lista_alunos.php` | já existe: `perfil_aluno()` |
| CPF, email, endereço, bairro, cidade, CEP | Fuctura, `detalhes_alunos.php?id=` | novo endpoint testado ao vivo — retorna tudo num único GET |
| Data de 1ª matrícula (regra "≥1 ano") | Fuctura, dentro de `turmas_atuais` (perfil) | pegar a data mais antiga da lista já retornada por `perfil_aluno()` |
| Registro Academia Fullstack | Fuctura, `turmas_atuais` | já vem: entrada tipo `{"data": "02/07/2026", "nome": "Academia Java Full Stack (4M)"}` |
| Turmas participadas | Fuctura, `turmas_atuais` | mesma fonte acima |
| Pagamentos em aberto/atrasados | CORA | `GET /v2/invoices/?search=<CPF>&state=LATE` (e `OPEN`) |
| Atas | Google Drive | pendente — Drive certo não identificado ainda (seção 6) |
| Contrato | Google Drive | mesma pendência |
| E-mail (rascunho) | Gmail | `create_draft` — conectado, mas é conta pessoal (confirmar se serve) |

## 5. CORA

**API:** existe, documentada em `developers.cora.com.br`.
- Endpoint: `GET /v2/invoices/` — parâmetros `search` (filtra por CPF/CNPJ do cliente), `state` (LATE/OPEN/PAID/...), `start`/`end` (datas).
- Retorna por boleto: `customer_name`, `customer_document`, `total_amount`, `due_date`, `status`, `paid_at`, `total_paid`.
- Identificação segura do cliente: parâmetro `search` filtra por CPF exato.
- **Autenticação — pendência**: a documentação descreve OAuth2 "authorization code" (apps parceiros acessando contas de terceiros) e um fluxo `client_credentials` mais direto (dono da própria conta). Fuctura é dona da conta CORA, então o fluxo certo é provavelmente `client_credentials` — mas precisa confirmação no painel/suporte do CORA, incluindo se exige certificado (mTLS).

**Acesso visual (navegador):** não deveria ser necessário — a API cobre tudo que foi pedido. Só cairia nisso se a conta CORA da Fuctura não tiver API habilitada.

## 6. Google Drive — pendência real

- Conector desta sessão está em `caiomvital@gmail.com`. Tem bastante conteúdo Fuctura (inclusive uma planilha de credenciais de infraestrutura, não reproduzida aqui) mas **nenhuma ata de chamada nem contrato de aluno** — nem por texto nem por título.
- Provavelmente atas/contratos vivem numa Drive/pasta separada (talvez sob `botfuctura@gmail.com` ou uma unidade compartilhada do Workspace da escola) não conectada aqui.
- Precisa decisão: onde ficam de fato, e se dá pra conectar aqui ou se a versão final roda com Service Account do Google.

## 7. E-mail

- Serviço: Gmail via `create_draft` — aceita corpo, anexos (base64, até 25MB combinados), não envia sozinho.
- Anexos: baixar do Drive (`download_file_content`) e passar pro `create_draft`.
- Destinatário: decisão pendente (fixo pra alguém, ou rascunho solto pra você preencher).
- Conta pessoal vs institucional (`botfuctura@gmail.com`?): decisão pendente.

## 8. Segurança e pontos de parada obrigatória

- CPF ausente/inválido → não consulta CORA, sinaliza.
- CORA não retorna nada pro CPF → não assume "sem dívida", sinaliza "não encontrado, conferir manualmente".
- Mais de um documento no Drive batendo por nome mas CPF não confirmável → não anexa automaticamente, lista como candidato pra revisão.
- Contrato não encontrado → segue sem, mas avisa explicitamente.
- Sem menção clara de cancelamento/desistência nos comentários → resumo não inventa motivo, só relata cronologia de fatos.
- Rascunho nunca enviado sozinho; nada é gravado no Fuctura neste processo.

## 9. DRY RUN — exemplo

```
[DRY RUN] Selecionado: MARCOS AUGUSTO FERREIRA CAMPOS (id 49745)
[DRY RUN] Matrícula desde: 02/07/2026 → NÃO elegível (menos de 1 ano) — pulado

[DRY RUN] Selecionado: <próximo aluno elegível>
[DRY RUN] Academia Full Stack desde: 13/05/2025 (prioridade alta)
[DRY RUN] CPF: 123.456.789-00
[DRY RUN] CORA: 3 boletos em aberto, R$ 1.131,00 total, vencimento mais antigo 10/03/2026
[DRY RUN] Drive: 2 atas encontradas (score de confiança), 1 contrato encontrado
[DRY RUN] Rascunho seria criado com: [prévia do corpo do e-mail]
[DRY RUN] Anexos que seriam incluídos: ata_2026-03.pdf, contrato_assinado.pdf
[DRY RUN] --- NADA foi criado, gravado ou enviado ---
```

## 10. Etapas de implementação

1. **Seleção e priorização** — novo módulo, reusa `roster_turma(1138/1136)` + `perfil_aluno()`. Sem dependências novas.
2. **Dados cadastrais completos** — `FucturaClient.detalhes_aluno(id)` novo, endpoint já validado.
3. **Cliente CORA** — `cora_client.py`. **Bloqueado por credenciais CORA.**
4. **Turmas participadas** — extrair de `turmas_atuais`, sem dependência nova.
5. **Busca no Drive** — **bloqueado por decisão de qual conta/Drive é a correta.**
6. **Resumo textual** — regras determinísticas sobre dados das etapas 2-4.
7. **Montagem do e-mail** — depende de 2, 3, 4, 6.
8. **Anexos** — depende de 5 e da decisão de destinatário.
9. **DRY RUN** — flag que desliga as ações de escrita.
10. **Rascunho real (1 aluno, supervisionado)** — só após validar DRY RUN várias vezes.

## 11. Decisões pendentes (precisam de aprovação antes de programar)

1. Credenciais CORA (client_id/secret, certificado?) e confirmar fluxo `client_credentials` vs OAuth authorization code.
2. Qual Drive/conta contém as atas e contratos reais.
3. Conta de e-mail do rascunho: pessoal (`caiomvital@gmail.com`, já conectada) ou institucional (`botfuctura@gmail.com`, precisaria conectar)?
4. Destinatário do rascunho: fixo (quem?) ou em branco?
5. Definição exata de "1 ano de matrícula": desde a primeira turma cursada (qualquer curso) ou especificamente desde a Academia Full Stack?
6. Reaproveitar a planilha `alunos_fuctura_priorizados_cobranca` já existente no Drive, ou recalcular do zero?

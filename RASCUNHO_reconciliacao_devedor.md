# Rascunho Técnico — Reconciliação Situação × Turma de Controle (Devedor/Advogado/Cancelado)

Investigação feita em 2026-09-03. Instruções vieram de Diógenes, repassadas por você. Nenhum arquivo de produção foi alterado; nada foi implementado. Este documento cobre só este processo — a "análise de turma" mencionada fica para depois, como combinado.

## 1. O processo, como entendi

Objetivo: para todo aluno com **Situação = Devedor** há pelo menos 1 ano, conferir se o cadastro (status + turma de controle) está consistente com a realidade financeira, e corrigir com revisão humana.

## 2. O que já existe (reaproveitável)

- `listar_devedores_ao_vivo()` (`fuctura_client.py`) — já lista **todos** os alunos com situação Devedor no sistema (via `lista_alunos_geral.php?tp=devedores`), independente de estarem ou não na turma de controle. É exatamente a fonte de "todos os devedores" pedida.
- `roster_turma(TURMA_DEVEDOR_ID)` / `roster_turma(TURMA_ADVOGADO_ID)` (já usados no fechamento de turma) — dizem quem está em cada turma de controle, com `data_entrada` (serve pra saber há quanto tempo está lá).
- `perfil_aluno(id)` — já traz `diferenca` (= "valor final": contratado − recebido) e `turmas_atuais`.
- `ids_turmas_controle()` — já calcula o conjunto de quem está em Devedor OU Advogado, usado hoje na regra de urgência do fechamento de turma (`regra_urgente_devedor_advogado`) — mesma lógica de checagem serve aqui.
- `comentarios_aluno(id)` + `CANCELAMENTO_RE` (em `fechamento_logic.py`) — já detecta menção de cancelamento em texto livre.
- `gravar_comentario(id, assunto, tipo, descricao, turma_id=None)` — já grava comentário e, com `tipo="15"` (-Matricula) + `turma_id`, **já matricula numa turma** — é o mecanismo pra "colocar na turma Devedor".
- `ja_tem_fechamento()` — pode ser reaproveitado pra checar se alguma turma do aluno nunca foi fechada (o item que você pediu pra aproveitar do fechamento de turma).

## 3. O que precisará ser criado

- **Endpoint de alteração de status** — investiguei agora: `detalhes_alunos.php` é POST (`action="/detalhes_alunos.php"`), reenvia o cadastro inteiro com o campo `status` novo. Precisa de um método novo `FucturaClient.atualizar_status_aluno(id, novo_status)` que busca o cadastro atual e reenvia com `status` alterado (sem tocar nos outros campos, pra não perder dado).
- Serviço de reconciliação (`reconciliacao_devedor.py`, novo) — junta as fontes acima e aplica a árvore de decisão da seção 4.
- Cálculo de "há quanto tempo é Devedor" — ver observação na seção 5 (não achei uma data direta de "virou Devedor" pra quem NÃO está na turma; provavelmente definido de outro jeito).
- DRY RUN + resumo por aluno.
- Registro da ação como comentário — tipo `"3"` (-Comentário) normal, ou `"2"` (Urgente) quando o caso pedir atenção imediata (ex: devedor sem turma nenhuma, ou inconsistência tipo "situação Devedor mas valor = 0" há muito tempo).

## 4. Árvore de decisão (o que entendi das suas regras)

```
Para cada aluno com Situação = Devedor, há ≥1 ano:

  valor_final = perfil["diferenca"]

  SE valor_final == 0:
    → INCONSISTENTE: está marcado Devedor mas não deve nada.
    → Convênio/bolsa: valor 0 é NORMAL pra quem é convênio - checar isso primeiro
      (senão vira falso positivo constante).
    → Se não é convênio: candidato a mover Situação → Matriculado (ou Ex-aluno,
      se também não estiver mais ativo em nenhuma turma - regra: só vira
      Ex-aluno se NÃO tiver dívida).
    → SINALIZA, não move sozinho.

  SE valor_final != 0 (realmente deve):
    esta_em_controle = está na turma Devedor/Pendência OU Aguardando Advogado?

    SE esta_em_controle == False:
      → CASO MAIS COMUM: colocar na turma Devedor/Pendência
        (gravar_comentario com tipo="15", turma_id=1138).
      → Antes de colocar, checar comentários por menção de cancelamento:
        SE há cancelamento mencionado E ainda deve:
          → ninguém paga depois de cancelar - mantém em Devedor
            (ou coloca em Devedor, já que cancelamento sozinho não quita a dívida).
        SE há cancelamento mencionado E valor_final == 0 (pagou o que devia):
          → candidato a mover Situação: Devedor → Cancelado.
      → SINALIZA "sem turma de controle, sugestão: colocar em Devedor" pra
        confirmação humana antes de gravar.

    SE esta_em_controle == True:
      → já está consistente (na turma certa, devendo de fato) - nada a fazer,
        só relatar no resumo.
```

## 5. Pontos resolvidos com Diógenes/você

- **"Devedor há pelo menos 1 ano"**: calculado a partir da **data de execução do sistema** contra a **data de vencimento** da dívida em aberto mais antiga — ex: rodando em 04/09/2026, o aluno é elegível se o vencimento mais antigo em aberto for de 04/09/2025 ou antes. **Dependência real**: isso precisa de vencimento por parcela, que o Fuctura não expõe de forma estruturada (só `contratado`/`recebido`/`diferenca` agregados). Duas fontes possíveis:
  1. **CORA** (rascunho `RASCUNHO_automacao_juridico.md`) — tem `due_date` por boleto via `GET /v2/invoices/`. É a fonte mais confiável, mas depende das credenciais CORA (ainda pendente).
  2. **Fallback nos comentários do Fuctura** — já vi comentários reais no formato `"Vencimento em 20/01/2025 R$ 377,00"` em sequência (ex: aluno Teylon, comentário de matrícula/geração de boleto). Dá pra extrair essas datas com regex como fallback quando o CORA não estiver disponível ainda, mas é menos confiável (só pega o que foi escrito na geração inicial do boleto, não reflete renegociações).
  - **Consequência prática**: esta automação fica mais forte/precisa depois que o CORA estiver integrado. Posso implementar o fallback via comentário primeiro pra não bloquear, e trocar pela fonte CORA assim que as credenciais chegarem.
- **Convênio**: não há garantia de que sempre está registrado como status `26: Convenio` — verificar primeiro o status; se não for Convenio mas valor==0, cair pro fallback de procurar "convênio"/"bolsa" no texto dos comentários antes de marcar como inconsistência.
- **Ex-aluno**: a automação PODE sinalizar/propor Ex-aluno, mas nunca aplica sozinha — sempre com confirmação manual, igual toda mudança de status/turma.

## 6. Regra de confirmação — importante, muda o padrão usado no Fechamento de Turma

Você definiu o princípio central: **o funcionário roda o sistema e confirma manualmente toda alteração real** (mudança de status, matrícula em turma) — **exceto a gravação de comentários de observação/análise/resumo**, que pode ser feita automaticamente, sem confirmação por item.

Ou seja, diferente do Fechamento de Turma (onde nem o comentário é gravado sem "posso salvar isso?"), aqui:
- Comentário de análise/resumo (tipo `"3"`) → grava direto, sem perguntar.
- Mudança de `status` (Situação) → sempre pede confirmação explícita antes de gravar.
- Matrícula em turma de controle (`tipo="15"`) → sempre pede confirmação explícita antes de gravar.

## 7. Segurança e pontos de parada

- Convênio: nunca sinaliza "inconsistente" alunos com status Convênio (ou menção de convênio/bolsa nos comentários) e valor 0 — tratado como normal, não como bug.
- Cancelamento: só sugere mover pra "Cancelado" se houver *tanto* menção de cancelamento no texto livre *quanto* valor_final == 0 — nunca só por um dos dois.
- "Devedor sem turma nenhuma" é sempre um caso de alerta explícito (⚠) na sugestão de ação, mesmo grava o comentário de análise sozinho.
- Se o aluno tiver múltiplas turmas de controle simultâneas (Devedor E Advogado ao mesmo tempo) — sinaliza como inconsistência separada, não decide qual manter.
- Se não há vencimento confiável disponível (nem CORA nem comentário) pra calcular "1 ano" → não estima, marca como "não foi possível determinar elegibilidade" e deixa de fora da lista de ação até ter dado melhor.

## 8. DRY RUN — exemplo

```
[DRY RUN] JOAO GUILHERME DA SILVA MATIAS CABRAL (id 46788)
  Situação: Devedor | valor_final: R$ 7.310,00 | vencimento mais antigo em aberto: 12/08/2025
  (≥ 1 ano antes de 04/09/2026 → elegível) | está em turma de controle? NÃO
  → Comentário de análise GRAVADO automaticamente (tipo=3): resumo da situação.
  → AÇÃO SUGERIDA (aguardando confirmação): matricular em Devedor/Pendência
    (tipo=15, turma=1138).

[DRY RUN] .VIRGINIA PEREIRA LIMA (ADVOGADO) (id 39804)
  Situação: Devedor | valor_final: R$ 4.806,00 | está em turma de controle? SIM (Advogado)
  → Comentário de análise GRAVADO automaticamente: "consistente, nada a fazer."

[DRY RUN] <aluno hipotético> (id X)
  Situação: Devedor | valor_final: R$ 0,00 | não é convênio (nem por status nem por comentário)
  → Comentário de análise GRAVADO automaticamente, sinalizando inconsistência.
  → AÇÃO SUGERIDA (aguardando confirmação): mover Situação Devedor → Matriculado.

--- Em modo DRY RUN de verdade, nem os comentários de análise seriam gravados -
    a flag desliga TODAS as escritas, inclusive a exceção da seção 6, só pra
    permitir testar sem tocar em nada. Fora do DRY RUN, os comentários de
    análise gravam direto (conforme regra da seção 6); só status/turma
    esperam confirmação. ---
```

## 9. Etapas de implementação

1. **`atualizar_status_aluno()`** — novo método no `FucturaClient`, testar contra 1 aluno de teste (ida e volta, sem mudar status de verdade num aluno real até confirmar que preserva os outros campos do cadastro).
2. **Coletar todos os devedores** — reusa `listar_devedores_ao_vivo()`, cruza com `roster_turma()` das duas turmas de controle pra saber quem está/não está nelas.
3. **Fonte de vencimento pra elegibilidade** — implementar primeiro o fallback via comentário (regex tipo `VALOR_RE`, mas pra "Vencimento em DD/MM/AAAA"); trocar pelo CORA assim que as credenciais chegarem (rascunho irmão).
4. **Árvore de decisão + resumo por aluno** — inclui a gravação automática do comentário de análise (sem confirmação, conforme seção 6) e a lista de ações pendentes de confirmação.
5. **DRY RUN completo** — desliga toda escrita, inclusive comentário — pra você revisar a lista antes de eu tocar em qualquer coisa real.
6. **Execução real** — comentário de análise grava direto; mudança de status e matrícula em turma sempre perguntam "posso gravar isso?" antes.

## 10. Decisões — aprovado em 2026-09-03

1. Comentário de análise cita a fonte do vencimento usada ("fonte: comentário" ou "fonte: CORA") — **aprovado**.
2. Começa só com o fallback via comentários, sem esperar o CORA — **aprovado**.

**Rascunho aprovado. Implementação inicia pela Etapa 1 (etapa por vez, conforme seção 9).**

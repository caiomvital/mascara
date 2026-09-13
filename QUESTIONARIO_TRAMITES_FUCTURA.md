# Questionário — trâmites reais do Fuctura

Reescrito em 2026-09-09 (versão anterior, de 05/09, ficou perdida na compactação
do histórico — esta é mais informada, porque desde então investigamos muito mais
o sistema ao vivo: comportamento real de turmas de controle, o endpoint "Suas
Postagens", como o Fuctura rotula tipo/matrícula às vezes de forma inconsistente,
limites reais de campo, o flood de 04/09 e sua causa raiz, entre outras coisas).

Perguntas que dava pra responder só investigando o sistema (engenharia reversa)
já foram respondidas e não estão aqui. O que sobra é conhecimento **institucional**
— como a Fuctura realmente opera — que só você ou o Diógenes sabem.

---

## 1. Situação do aluno (`status`)

O Fuctura tem 10 valores possíveis:

| Código | Situação |
|---|---|
| 19 | Interessado |
| 4 | -Matriculado |
| 7 | Devedor |
| 30 | Advogado |
| 5 | Cancelado |
| 29 | Cliente sem Interesse |
| 22 | Ex-aluno |
| 6 | Pediu Pausa |
| 26 | Convenio |
| 31 | Provi/Pravaler |

1. Existe uma ordem/fluxo esperado entre esses status (ex: Interessado →
   -Matriculado → Devedor → Advogado), ou qualquer um pode virar qualquer
   outro a qualquer momento?
2. "Cliente sem Interesse" e "Ex-aluno" — qual a diferença de critério pra
   escolher um ou outro?
   > **Respondido (09/09):** "Cliente sem Interesse" = pessoa registrada
   > que **não chegou a se matricular** no curso (não é ex-aluno, nunca foi
   > aluno de fato). A conclusão exige checar os comentários caso a caso —
   > **não é pra virar automação/gravação no sistema-máscara por enquanto**,
   > só conhecimento pra revisão humana.
3. "Pediu Pausa" tem prazo? Depois de quanto tempo sem retornar isso deveria
   virar outra coisa (Cancelado? Ex-aluno?)?
4. "Convenio" — é sempre financeiro (bolsa/desconto de parceria) ou existe
   convênio que não mexe em pagamento?
5. Quando um aluno paga tudo e "some" (não vem mais, não cancela
   formalmente) — qual status é o certo?

## 2. Turmas de controle (Devedor/Pendência, Aguardando Advogado)

6. Existe uma regra formal de quanto tempo em atraso pra matricular em
   Devedor/Pendência, ou é sempre critério humano caso a caso? (Nosso
   sistema hoje usa 365 dias como padrão configurável — é o número certo?)
7. Quem decide mover de Devedor/Pendência pra Aguardando Advogado — existe
   um segundo prazo, ou depende de outra coisa (tentativas de contato sem
   resposta, valor da dívida)?
8. Um aluno pode estar matriculado numa turma de curso normal *e* em
   Devedor/Pendência ao mesmo tempo (o que a gente já vê acontecer na
   prática), ou isso é sempre um erro que deveria ser corrigido?
9. Existe uma turma de controle pra "aluno que a empresa dele paga e a
   empresa está devendo" (achamos casos assim nos comentários reais, tipo
   Miguel Mattos), ou entra na mesma Devedor/Pendência normal?

## 3. Financeiro / cobrança

10. O cálculo de multa de cancelamento que aparece nos comentários reais
    varia bastante (10%, 20% dos módulos restantes, às vezes sem multa
    nenhuma) — existe uma tabela oficial, ou é negociação caso a caso
    autorizada pelo Diógenes?
11. Quando tem "acordo" formal (vimos comentários tipo "Email Mergulhão
    recebido" com parcelamento de dívida antiga) — isso vem de fora
    (escritório terceirizado, "Mergulhão"?) ou é interno?
    > **Respondido (09/09):** "Mergulhão" era a empresa de advocacia que
    > cobrava/processava inadimplentes **antes** da mudança pro escritório
    > atual — explica os comentários antigos ("Email Mergulhão recebido"),
    > não é o fluxo em uso hoje.
12. Existe um valor mínimo de dívida abaixo do qual não vale a pena cobrar
    (custo de cobrança > valor)?
13. "Estorno" (aluno cancelou antes de usar o serviço, Fuctura deve
    devolver) — qual o processo real? Quem autoriza, em quanto tempo?
    > **Respondido (09/09):** Diógenes autoriza.
14. Pagamento via boleto Fuctura vs. boleto CORA — os comentários mostram os
    dois em uso; tem regra de quando usar cada um, ou é só histórico
    (mudaram de sistema em algum momento)?

## 4. CORA

15. Quem administra a conta CORA da Fuctura — você, o Diógenes, ou outra
    pessoa? Essa é a pessoa certa pra pedir as credenciais de API
    (`client_id`/`client_secret`, ou certificado mTLS)?
    > **Respondido (09/09):** Diógenes autoriza — é com ele que a
    > liberação das credenciais precisa ser pedida.
16. A conta CORA tem API habilitada, ou o acesso é só pelo painel visual
    deles?

## 5. Jurídico / advogado

17. Qual o critério real pra mandar um caso pro advogado — só "está em
    Aguardando Advogado" já é suficiente, ou tem um passo humano de revisão
    antes de mandar o e-mail?
    > **Respondido (09/09):** Revisão humana — confirma que o desenho
    > atual do sistema (rascunho, nunca envia sozinho) está certo.
18. O e-mail vai sempre pro mesmo advogado/escritório, ou depende do caso
    (valor, tipo de dívida)?
    > **Respondido (09/09):** Sempre o mesmo, até segunda ordem.
19. Depois que manda pro advogado, o sistema-máscara deveria registrar isso
    de alguma forma automática no Fuctura (comentário "enviado pro
    jurídico em DD/MM"), ou isso já é feito manualmente por vocês?
20. Existe prazo de resposta esperado do advogado, ou acompanhamento
    nenhum depois que o e-mail sai?

## 6. Comentários / convenções da equipe

21. Vimos MUITAS variações de título pro mesmo tipo de evento ("BOLETO
    PAGO", "PAGAMENTO REALIZADO", "PAGAMENTO VIA PIX", "DAR BAIXA...") —
    existe um padrão que a equipe deveria seguir e simplesmente não segue,
    ou é aceito ser livre?
22. O tipo "-Pagamento Realizado" (dropdown oficial) aparece bem menos que
    o texto livre mencionando pagamento — a equipe evita usar esse tipo de
    propósito, ou é falta de hábito?
23. "Comentário invalidado... solicitar a Diógenes a exclusão" — aparece em
    pelo menos um comentário real. Existe de fato um jeito de excluir
    comentário que a gente não descobriu, ou o pedido nunca é atendido (o
    Fuctura genuinamente não tem exclusão, confirmamos isso)?

## 7. Permissões / operação

24. Além de admin (você, Diógenes) existem outros níveis de permissão reais
    dentro do Fuctura (o que cada funcionário pode/não pode fazer), ou é
    tudo igual pra quem tem login?
25. Quantas pessoas usam o Fuctura no dia a dia hoje? Isso muda a urgência
    de algumas coisas (ex: trava de concorrência já implementada).

## 8. Turmas / matrícula

26. As turmas "-I-(Nome do Curso)" (ex: `-I-(Academia Java)`) — o que
    exatamente esse prefixo "-I-" significa? Interessado? Pré-matrícula?
    > **Respondido (09/09):** Interessado.
27. Turmas tipo "Rev Ferias", "Rev1 J2..." (revisão) — contam como turma de
    verdade pra fins de cobrança, ou são só reforço sem custo adicional?
28. Existe uma lista oficial de nomes de curso (Java, Python, Linux, PHP,
    Bíblia 3D...) ou qualquer curso novo pode ser criado livremente com
    qualquer nome de turma?

## 9. Relatórios do próprio Fuctura

29. Os relatórios "Sem Turma" e "Verificação de Turma" (menu Relatórios) —
    você já usou algum dos dois recentemente e funcionou? Isso ajudaria a
    confirmar se é bug real ou só "zero resultados hoje".
30. Existem outros relatórios do menu que vocês usam no dia a dia que vale
    a pena eu mapear pro sistema-máscara também?

## 10. Sobre o novo sistema (perguntas nossas, não do Fuctura)

31. Quando o Diógenes falou em "mandar mensagem pro aluno com aquela
    rotina que eu te mostrei do Claude" — isso é a Evolution API (WhatsApp)
    que já está rodando na VPS? Vale eu conectar o sistema-máscara nisso, ou
    continua sendo um processo separado?
32. Confirma o entendimento: o e-mail pro advogado é sempre rascunho
    (nunca envia sozinho) — isso vale pra sempre, ou em algum momento
    futuro, com processo mais maduro, faz sentido considerar envio
    automático?
33. Quantos alunos aproximadamente estão em situação "Devedor" hoje, na
    prática? Ajuda a dimensionar se o sistema aguenta o volume real (já
    testamos com 355, mas se crescer muito pode precisar rodar em lotes).

---

*Perguntas concentradas nas áreas onde o comportamento real do Fuctura diverge
do que dá pra inferir só olhando o HTML/banco — cálculo de multa, critério de
"quando é urgente de verdade", papel de terceiros (advogado, "Mergulhão"),
convenções da equipe. Não precisa responder tudo de uma vez — o que já foi
resolvido no meio da conversa (ex: prompts, IP da VPS) eu já tirei daqui.*

---

## Anexo — onde ficam os textos "mutáveis" (resposta à pergunta do Diógenes sobre "prompts")

Ver `textos_editaveis.txt` (mesma pasta deste documento) — lista os arquivos
`.py` e as linhas exatas onde ficam os textos gravados no Fuctura e no e-mail
de cobrança. Nenhum deles usa IA; são strings Python, editáveis a qualquer
momento sob pedido.

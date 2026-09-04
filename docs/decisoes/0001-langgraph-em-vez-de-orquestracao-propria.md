# 0001 — LangGraph em vez de orquestração própria

**Estado:** aceita · **Data:** 2026-09

## Contexto

O pipeline de avaliação é uma sequência com bifurcações: coletar, triar,
pontuar, justificar, revisar, notificar. Um laço `for` com `if` faria isso.
A pergunta é se o framework paga o próprio custo.

## Decisão

Usar LangGraph a partir do M3.

## Por quê

Três necessidades concretas, nenhuma delas cosmética:

1. **Checkpoint durável.** São centenas de contratações por execução, e a camada
   de LLM custa dinheiro. Se o processo cair na contratação 300, retomar do zero
   significa pagar de novo pelo que já foi julgado. O `AsyncPostgresSaver`
   persiste o estado a cada nó.

2. **Pausa humana com retomada.** O alerta só sai depois de alguém aprovar. Com
   `interrupt()`, o processo pode terminar e a thread continua de onde parou
   quando a aprovação chegar — sem inventar uma máquina de estados própria.

3. **Descarte como decisão registrada.** Uma aresta condicional para `arquivar`
   grava o motivo. Um `continue` escondido dentro de um laço não deixa rastro, e
   "por que este edital não me foi mostrado?" é uma pergunta que o usuário vai
   fazer.

## Consequências

- Uma dependência pesada a mais, e a curva de aprendizado do modelo de grafo.
- Em troca: o estado do pipeline vira um objeto explícito e inspecionável.
- Efeito colateral bem-vindo — LangChain/LangGraph aparece em ~40% das vagas de
  engenharia de IA, o que não é motivo para escolher um framework, mas também
  não é motivo para evitá-lo quando ele já é a escolha certa.

## Alternativas consideradas

**CrewAI** — bom para agentes conversando entre si; aqui o fluxo é determinístico
com pontos de decisão, não uma conversa. Estado implícito e sem checkpoint
durável equivalente.

**Orquestração própria** — perfeitamente viável, e provavelmente 200 linhas.
O que ela não dá de graça é o checkpoint transacional e a retomada.

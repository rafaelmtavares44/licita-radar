# 0006 — O analista de editais muda o produto

**Estado:** aceita · **Data:** 2026-09

## Contexto

O projeto nasceu como radar: avisar quando aparece licitação compatível.
Esse mercado já existe e é servido — Effecti, ConLicitação, Alerta
Licitação. Todos entregam a mesma coisa: o aviso.

A observação que muda o desenho veio de fora do código: **a dor das
empresas não é descobrir que o edital existe, é entender o edital.** Ler
oitenta páginas de juridiquês e concluir se vale disputar exige um
profissional caro e escasso, e é aí que o processo trava.

## Decisão

O produto passa a ter duas metades:

1. **Radar** — encontra o que combina com o perfil da empresa (M1–M3)
2. **Analista** — lê o edital e devolve um resumo executivo: objeto real,
   exigências de habilitação, garantia, prazos, penalidades e riscos

A proposta de valor deixa de ser *"te aviso quando aparecer"* e passa a ser
*"te aviso, e te digo em uma página se vale a pena disputar"*.

## Consequências

- O ADR 0002 ("a v0.1 não lê o edital") **fica revogado a partir do M4**. A
  decisão continua correta para a v0.1, que precisava existir antes de ser
  ambiciosa; o que muda é o depois.
- O `EditalState` já reserva `documentos`, `texto_edital` e `analise` desde
  o M3, vazios. Custa nada agora e evita invalidar todo checkpoint gravado
  quando o M4 chegar — mudança de formato de estado é migração silenciosa.
- O grafo do M3 é a espinha dorsal dos dois: o analista entra como nós
  novos entre `pontuar` e `revisar`, não como projeto paralelo.
- Entra um risco novo e sério: **um resumo que inventa exigência é pior que
  resumo nenhum**. A pessoa deixa de disputar por causa de uma alucinação.
  O M4 precisa de citação de trecho e de um caminho de verificação.

## Roadmap revisado

| | Marco | Estado |
|---|---|---|
| M3 | Grafo LangGraph com checkpoint | ✅ |
| M4 | Analista de editais: PDF, extração e resumo executivo | ⬜ |
| M5 | Painel web | ⬜ |
| M6 | Alerta e acabamento para o release | ⬜ |

O alerta desceu na fila de propósito: sem o resumo do edital, ele avisa a
mesma coisa que os concorrentes já avisam.

# 0008 — O alerta vem antes da aprovação, não depois

**Estado:** aceita · **Data:** 2026-09

## Contexto

O grafo termina em `notificar`, e o caminho até lá passa por `revisar` —
a pausa em que uma pessoa decide. O lugar óbvio para o alerta é o último
nó: a licitação foi aprovada, o edital foi lido, o resumo está pronto.

Óbvio e errado para o caso que mais importa.

O radar existe para rodar sozinho. Agendado às 7h da manhã, ele coleta,
pontua e para em `revisar` — porque é ali que ele deve parar. **Ninguém
aprovou nada.** Um alerta que só dispara depois da aprovação ficaria mudo
exatamente na execução em que o aviso é a única forma de a pessoa ficar
sabendo que existe algo para decidir.

## Decisão

Dois alertas, em dois momentos, com propósitos distintos:

| quando | pergunta que responde | conteúdo |
|---|---|---|
| o `radar` termina | *vale parar o que estou fazendo?* | objeto, órgão, prazo, valor, score e a justificativa |
| a pessoa aprova | *o que o edital exige?* | o resumo com ✓ / ≈ / ? |

O primeiro sai do comando, não do grafo — ele é sobre o conjunto do que
ficou pendente, e o grafo só enxerga uma contratação por vez. O segundo é
o nó `notificar`, que passa a ter conteúdo de verdade.

## Consequências

- **Falhar no alerta nunca desfaz trabalho.** A licitação foi aprovada, o
  edital foi baixado e a análise está gravada no banco antes de o canal
  ser chamado. Alerta que não sai é aborrecimento; execução que morre no
  último nó jogaria fora o funil inteiro. A trilha registra
  `notificar:falhou` para o caso não sumir.
- **O canal é opcional, como o LLM.** Sem token, o `CanalDesligado`
  assume e o alerta vai para o log. Quem clona o repositório não precisa
  de um bot para ver o radar funcionando.
- **O formato é HTML, não MarkdownV2.** O MarkdownV2 do Telegram exige
  escapar dezoito caracteres — `.`, `-`, `(`, `)`, `!`, `=` — e objeto de
  licitação vem cheio de todos. Uma mensagem que falha por causa de um
  parêntese no nome do órgão é o mesmo defeito do `[web]` comido pelo
  Rich: conteúdo tratado como marcação. Em HTML são três caracteres, e a
  regra cabe numa função de duas linhas.
- **A lista de triagem é cortada em cinco.** Um alerta que precisa de
  rolagem para ser compreendido já falhou como alerta.
- **O `doctor` confere o bot e o destino**, não se as variáveis estão
  preenchidas. O erro mais comum não é token errado: é faltar o `/start`,
  e aí a configuração *parece* completa e a mensagem simplesmente não
  chega — o pior tipo de falha para um canal de aviso.

# 0005 — Freio adaptativo no cliente do PNCP

**Estado:** aceita · **Data:** 2026-09

## Contexto

O manual das APIs de consulta do PNCP não menciona limite de requisições, e
o cliente foi escrito com essa suposição: concorrência 3 e backoff
exponencial por tentativa, com teto de 30 s e cinco tentativas.

A primeira varredura nacional derrubou a suposição. A partir da página 16 da
modalidade 6, o servidor passou a responder `429 Too Many Requests` em
série; as cinco tentativas se esgotaram em segundos e a modalidade foi
pulada. Pior: a modalidade 8, iniciada logo depois, levou 429 já na
**primeira** página.

Esse detalhe é o que decide o desenho: **o limite é por cliente, não por
requisição nem por rota**. Backoff isolado por chamada não resolve, porque
cada nova chamada recomeça ignorando o que a anterior aprendeu.

## Decisão

Um `Freio` compartilhado por toda a varredura, que mantém um intervalo
mínimo entre chamadas, dobra esse intervalo a cada 429 (respeitando
`Retry-After` quando presente) e o reduz pela metade a cada sequência de
sucessos.

A concorrência padrão cai de 3 para 2.

## Por quê

- Controle de fluxo do lado do cliente não depende da boa vontade nem da
  documentação do servidor.
- O estado compartilhado é o ponto: a modalidade seguinte herda o ritmo que
  a anterior descobriu, em vez de bater na parede de novo.
- Afrouxar sozinho evita o outro extremo — ficar lento para sempre por
  causa de um pico de dez minutos atrás.

## Consequências

- A varredura nacional fica mais lenta. É o preço de terminar em vez de ser
  cortada no meio.
- Um serviço público gratuito não deve ser martelado; ser educado aqui é
  também o comportamento correto.
- `LR_PNCP_INTERVALO_MIN_S` e `LR_PNCP_INTERVALO_MAX_S` permitem ajustar sem
  tocar no código, caso o PNCP mude a política.

## Nota

O README dizia "não há limite de requisições documentado. O cliente trata
como se houvesse". A intuição estava certa; a implementação é que era fraca
demais para o limite real.

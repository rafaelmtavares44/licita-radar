# 0003 — Postgres com pgvector, e não um banco vetorial dedicado

**Estado:** aceita · **Data:** 2026-09

## Contexto

O matching semântico precisa de busca por similaridade de vetores. As opções
usuais são um banco vetorial dedicado (Qdrant, Chroma, Weaviate) ou a extensão
`pgvector` no Postgres que o projeto já usa para os dados relacionais.

## Decisão

Postgres com `pgvector`, num serviço só.

## Por quê

- O volume é pequeno: dezenas de milhares de contratações por ano, não milhões.
  A vantagem de desempenho de um banco dedicado não aparece nessa escala.
- Contratação, embedding, avaliação e checkpoint do LangGraph ficam na mesma
  transação. Consistência de graça.
- Um serviço a menos no `docker compose` é um obstáculo a menos para quem clona
  o repositório. Adoção de projeto opensource morre no atrito de instalação.

## Consequências

- A busca vetorial fica presa ao que o `pgvector` oferece. Suficiente para
  similaridade de cosseno com índice HNSW.
- Se o projeto um dia precisar de filtro híbrido sofisticado ou de bilhões de
  vetores, essa decisão terá que ser revisitada. Não é o caso da v0.1, nem da v1.

## Nota de implementação

A migração `001` **não** usa a extensão `vector` — ela entra só na `002`, junto
com o matching. Assim o M1 roda em qualquer Postgres 14+ sem instalar extensão
nenhuma, e quem só quer ver a ingestão funcionando não precisa da imagem
`pgvector/pgvector`.

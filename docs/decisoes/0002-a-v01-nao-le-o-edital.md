# 0002 — A v0.1 não lê o edital

**Estado:** aceita · **Data:** 2026-09

## Contexto

O instinto ao montar um radar de licitações é baixar o edital em PDF, extrair o
texto e analisar as exigências. É também o caminho mais rápido para o projeto
nunca ser publicado: download de arquivos, OCR de PDF escaneado, chunking,
extração de requisitos e avaliação de habilitação são um projeto inteiro.

## Decisão

A v0.1 trabalha exclusivamente com o campo `objetoCompra`, que já vem na própria
listagem da API de consultas. Nenhum arquivo é baixado.

## Por quê

- O objeto da compra é justamente o que uma pessoa lê para decidir se abre o
  edital. Se ele não interessa, o PDF não muda nada.
- Elimina toda a cadeia de processamento de documento — a maior parte do esforço
  total do projeto — sem eliminar a maior parte do valor.
- Mantém a ingestão rápida e o custo por contratação praticamente nulo.

## Consequências

- Compras com objeto genérico ("aquisição de solução tecnológica") vão pontuar
  mal ou gerar ruído. Aceitável: o alerta manda o link, a pessoa abre e decide.
- Exigências de habilitação — atestado de capacidade técnica, garantia, índices
  contábeis — ficam fora do escopo. Elas decidem se vale mesmo participar, e
  entram na fase 2.
- Os nós do grafo ficam desenhados de forma que um `extrair_requisitos` possa ser
  inserido depois sem reescrever o fluxo.

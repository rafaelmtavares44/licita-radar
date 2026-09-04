# 0004 — fastembed em vez de sentence-transformers

**Estado:** aceita · **Data:** 2026-09

## Contexto

A camada semântica precisa transformar texto em vetor. O caminho padrão em
Python é `sentence-transformers`, que arrasta o PyTorch: cerca de 2,5 GB de
instalação, mesmo quando só se quer inferência em CPU com um modelo de 200 MB.

## Decisão

Usar `fastembed`, que roda os mesmos modelos em ONNX, e deixá-lo num extra
opcional (`pip install -e ".[semantico]"`).

## Por quê

- **Instalação de ~200 MB contra ~2,5 GB.** Num projeto opensource, o tamanho
  do `pip install` decide quantas pessoas terminam a instalação.
- **Sem GPU envolvida.** O volume é de centenas de textos por dia; ONNX em CPU
  resolve com folga, e o PyTorch só entraria para ficar ocioso.
- **O extra é opcional de propósito.** `licita-radar ingest` e
  `licita-radar match --sem-semantica` funcionam sem baixar modelo nenhum —
  dá para calibrar o perfil inteiro antes de decidir se vale a pena.

## Consequências

- O catálogo do `fastembed` é menor que o do `sentence-transformers`. O modelo
  padrão é o `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensões, ~220 MB);
  trocar para o `multilingual-e5-large` (1024 dimensões, 2,2 GB) melhora a
  qualidade, mas exige mudar `vector(384)` na migração 002 e recodificar tudo.
- O encoder fica atrás de um `Protocol` (`matching/encoder.py`), então a troca
  é de uma linha de configuração, não uma refatoração.

## Nota sobre os testes

Nenhum teste baixa modelo. A suíte usa um `EncoderFalso` determinístico, de
bag-of-words, que basta para exercitar cosseno, cache, lotes e persistência.
Teste que depende de download de 200 MB não é teste — é sorte.

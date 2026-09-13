# Texto para o LinkedIn

---

## Versão principal

Uma fábrica de software vende desenvolvimento de sistemas web, APIs de integração, sustentação de sistemas legados e alocação de squad para órgãos públicos.

Isso está sendo licitado no PNCP todo dia — no meio de milhares de contratações de toner, cabo de rede e obra civil. Quem tem estrutura paga assinatura de portal. Quem não tem, descobre tarde.

Então construí o **licita-radar**: ele varre o PNCP, pontua cada contratação contra o que a empresa realmente vende e — quando eu aprovo — baixa o edital, lê e resume.

A parte que me interessava resolver: **cada afirmação do resumo carrega o trecho do edital que a sustenta**, e o sistema procura esse trecho de volta no documento original antes de mostrar na tela. Marca ✓ quando bate, ≈ quando o trecho existe mas o número não, ? quando não achou.

Um resumo que inventa uma exigência de habilitação faz a empresa desistir de uma licitação que venceria — e quem pediu o resumo é justamente quem não vai reler as oitenta páginas para perceber.

**A pilha:**
🐍 Python · FastAPI · asyncio
🧠 LangGraph — a máquina de estados pausa e espera a decisão humana, com checkpoint no Postgres
🔎 PostgreSQL + pgvector (HNSW) · fastembed em ONNX, sem arrastar PyTorch
📄 Extração de PDF, DOCX e ZIP · LLM pelo protocolo da OpenAI (Gemini, Groq ou Ollama local)
⚛️ React + TypeScript + Vite
🐳 Docker · mypy strict · ruff · CI no GitHub Actions

Opensource sob licença Apache 2.0 — código, decisões de arquitetura e os erros documentados:

👉 [SEU LINK DO GITHUB AQUI]

Aceito issues, críticas e PRs.

#opensource #python #ia #langgraph #postgresql #licitacoes #devbrasil

---

## Variação sem emojis

Uma fábrica de software vende desenvolvimento de sistemas web, APIs de integração, sustentação de sistemas legados e alocação de squad para órgãos públicos.

Isso está sendo licitado no PNCP todo dia — no meio de milhares de contratações de toner, cabo de rede e obra civil. Quem tem estrutura paga assinatura de portal. Quem não tem, descobre tarde.

Então construí o licita-radar: ele varre o PNCP, pontua cada contratação contra o que a empresa realmente vende e — quando eu aprovo — baixa o edital, lê e resume.

A parte que me interessava resolver: cada afirmação do resumo carrega o trecho do edital que a sustenta, e o sistema procura esse trecho de volta no documento original antes de mostrar na tela. Marca ✓ quando bate, ≈ quando o trecho existe mas o número não, ? quando não achou.

Um resumo que inventa uma exigência de habilitação faz a empresa desistir de uma licitação que venceria — e quem pediu o resumo é justamente quem não vai reler as oitenta páginas para perceber.

A pilha: Python, FastAPI e asyncio na base. LangGraph para o grafo de decisão — a máquina de estados pausa e espera o humano aprovar, com checkpoint no Postgres, então a espera sobrevive a reiniciar o servidor. PostgreSQL com pgvector e índice HNSW para a busca semântica, com embeddings em ONNX via fastembed (sem arrastar PyTorch). Extração de PDF, DOCX e ZIP. A camada de LLM fala o protocolo da OpenAI, então roda com Gemini, Groq ou Ollama local trocando duas linhas. React com TypeScript e Vite no painel. Docker, mypy strict, ruff e CI no GitHub Actions.

Opensource sob licença Apache 2.0 — código, decisões de arquitetura e os erros documentados:

[SEU LINK DO GITHUB AQUI]

Aceito issues, críticas e PRs.

#opensource #python #ia #langgraph #postgresql #licitacoes #devbrasil

---

## Observações

**Onde entra o link:** o LinkedIn reduz o alcance de posts com link no corpo. Duas
saídas comuns — escolha uma:

1. Deixe o link no texto mesmo (mais simples, alcance um pouco menor)
2. Ponha "link nos comentários" no lugar e cole o endereço no primeiro comentário

**Ordem das imagens:** o painel primeiro. Ele mostra o produto funcionando, que é o
que segura o scroll. A arquitetura vem em segundo, para quem parou.

**As três primeiras linhas** são as únicas que aparecem antes do "ver mais". Elas
estão carregando o peso de propósito: o que a empresa vende, onde isso é licitado,
e o ruído que esconde. A promessa técnica vem logo depois, para quem abriu.

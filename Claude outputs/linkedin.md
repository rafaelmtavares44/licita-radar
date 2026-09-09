# Post para o LinkedIn

> Troque `SEU-USUARIO` pelo link real do repositório antes de publicar.
> As três primeiras linhas são o que aparece antes do "ver mais" — elas
> carregam o post sozinhas.

---

Empresas perdem licitações públicas que ganhariam — por não saberem que elas existem.

E desistem de outras que ganhariam — por não entenderem o edital.

Passei os últimos dias construindo um projeto open source que ataca as duas pontas.

**A primeira metade é um funil de custo crescente.**

O licita-radar lê o PNCP todos os dias e passa cada contratação por camadas cada vez mais caras:

filtro léxico (custo zero) → similaridade semântica (embeddings) → LLM (menos de 1% do que entra) → você decide

A ordem é a tese do projeto. Se cada uma das ~1.300 contratações publicadas por dia passasse por um modelo de linguagem, seria lento e caro. Do jeito que está, o modelo só vê o que sobreviveu a duas camadas baratas.

**A segunda metade lê o edital. E é aqui que ficou interessante.**

Um LLM resumindo 80 páginas de juridiquês acerta quase sempre. O "quase" é o problema: uma exigência de habilitação inventada é indistinguível de uma real justamente para quem não leu o documento — que é exatamente quem pediu o resumo.

Então toda afirmação carrega o trecho literal do edital, e todo trecho volta ao documento original para ser procurado lá.

Um caso real, com dado real:

O modelo afirmou "multa de 15%" citando uma frase que **estava mesmo no edital**. A citação passou na verificação. O número era invenção — a extração do PDF tinha jogado o "20 vinte" para o fim da linha e deixado uma lacuna, e o modelo preencheu com um valor plausível.

A multa real era 20%.

Isso virou a regra mais importante do projeto:

**conferir que a evidência existe não é o mesmo que conferir que ela prova a alegação.**

Hoje cada linha do resumo chega marcada:

✓ a citação está no edital e contém os números da frase
≈ a citação é do edital, mas o número afirmado não está nela
? a citação não foi encontrada

---

**A stack:**

→ **Python 3.11+** com asyncio de ponta a ponta
→ **LangGraph** — grafo com checkpoint em Postgres e human-in-the-loop de verdade (`interrupt()` / `Command(resume=...)`): a execução dorme esperando a decisão e retoma exatamente onde parou, mesmo se o processo morrer
→ **PostgreSQL + pgvector** — busca por similaridade com índice HNSW
→ **fastembed (ONNX)** — embeddings multilíngues sem arrastar PyTorch para a árvore de dependências
→ **FastAPI + Pydantic v2** — API com OpenAPI gerado dos tipos
→ **React + TypeScript + Vite** — painel para decidir no navegador
→ **httpx + tenacity** — cliente com backoff e freio adaptativo (o PNCP tem limite de requisições que não está documentado; descobri levando 429)
→ **pypdf** — extração de edital, em modo layout
→ **pytest, mypy strict, ruff** — 251 testes, nenhum toca a rede
→ **Docker Compose, GitHub Actions, Apache 2.0**

---

O que eu mais levo desse projeto não é a stack. É que **quase toda decisão de arquitetura veio de dado real me contrariando**:

→ em dispensa não existe "edital" — quem faz esse papel é o Aviso de Contratação Direta, e a minha ordenação deixava ele de fora
→ `valorTotalEstimado = 0` significa "não informado", não "vale zero"
→ um endpoint que demora 58 segundos não está travado, e eu "consertei" o timeout que estava certo
→ o uvicorn passa `loop_factory` explícito, e isso ignora a política de event loop — a correção do Windows estava instalada e sendo pulada

Cada uma virou commit, teste e ADR.

Código, decisões documentadas e roadmap:
github.com/SEU-USUARIO/licita-radar

#Python #IA #LangGraph #FastAPI #React #PostgreSQL #OpenSource #DadosAbertos #LLM #GovTech

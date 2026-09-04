# Roadmap

Este arquivo tem duas funções. A de cima é dizer o que vem. A de baixo — a mais
importante — é **conter o apetite**: toda ideia boa que aparecer no meio do
caminho vem parar aqui em vez de entrar no escopo da v0.1.

## Marcos até a v0.1.0

| | Marco | Estado | Pronto quando |
|---|---|---|---|
| M0 | Fundação: repositório, CI, banco | ✅ | `pytest` passa no GitHub Actions e o compose sobe o banco |
| M1 | Ingestão do PNCP | ✅ | Rodar duas vezes seguidas não duplica linha |
| M2 | Funil de matching (léxico + semântico) | ✅ | O top 20 do dia sai no terminal e você concorda com o ranking |
| M3 | Grafo LangGraph com checkpoint | ✅ | Matar o processo no meio e retomar sem reavaliar o que já foi julgado |
| M4 | Analista de editais | ⬜ | O resumo do edital responde "vale a pena?" sem abrir o PDF |
| M5 | Painel web | ⬜ | Dá para revisar e decidir sem terminal |
| M6 | Alerta e acabamento | ⬜ | Alguém clona e chega no primeiro resultado em 5 minutos pelo README |

O escopo mudou no M3: ler o edital deixou de ser fase 2 e virou o coração
do produto. O [ADR 0006](decisoes/0006-o-analista-de-editais-muda-o-produto.md)
explica por quê.

### O que o M2 entregou

- Camada léxica com veto por palavra negativa e filtros de elegibilidade
- Limpeza da casca burocrática do objeto, calibrada em dados reais
- Encoder desacoplado (`fastembed`, sem PyTorch), com dublê para os testes
- Migração `002`: extensão `vector`, embeddings e avaliações
- Score combinado pelos pesos do perfil, com veredito explicável
- `licita-radar match`, com `--sem-semantica` para calibrar sem baixar modelo

Ficou de fora, para quando houver volume: casamento por CNAE (hoje o campo é
informativo) e busca vetorial no banco via `<=>` em vez de cosseno em Python —
com poucas centenas de linhas por dia, a diferença não se nota.

### O que o M3 entregou

- `EditalState` com reducer na trilha, e campos já reservados para o M4
- Nós de triagem, pontuação, justificativa, revisão, notificação e arquivo
- Arestas condicionais: o descarte é decisão registrada, não `continue`
- `AsyncPostgresSaver`, com `thread_id` = `numeroControlePNCP`
- `interrupt()` na revisão: o processo pode morrer e a thread continua
- Camada de LLM trocável (OpenAI, Groq, Ollama) e **opcional** — sem chave,
  a justificativa é heurística e o projeto roda igual
- `licita-radar radar` e `licita-radar revisar`

### O que falta no M4 — o analista de editais

- Download dos arquivos da contratação (API de integração do PNCP)
- Extração de texto, incluindo PDF escaneado
- Resumo executivo: objeto real, habilitação, garantia, prazos, penalidades
- **Citação do trecho de origem em cada afirmação** — um resumo que inventa
  exigência é pior que resumo nenhum

---

## Congelado — fase 2

Nada daqui entra antes da `v0.1.0` estar publicada com release no GitHub.

- **Leitura do edital em PDF.** Download dos arquivos da contratação, extração de
  texto, chunking. Elimina-se assim ~70% do esforço da v0.1. As rotas ficam na API
  de integração, padrão `/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos` —
  confirmar no Swagger antes de contar com elas.
- **Itens da contratação.** Mesma família de rotas. Melhoraria muito o matching em
  compras com objeto genérico.
- **Extração de exigências de habilitação.** Atestado de capacidade técnica,
  garantia, índices contábeis — o que decide se vale mesmo participar.
- **Múltiplos perfis / multi-tenant.** Hoje é um YAML, um usuário.
- **Painel web e API HTTP.** A CLI é a interface da v0.1, de propósito.
- **Histórico de preços e atas de registro.** O endpoint `/v1/atas` existe e é rico.
- **Aprendizado a partir do feedback.** A tabela já guarda as decisões humanas;
  usar isso para recalibrar pesos é trabalho de fase 2.
- **Agendamento próprio.** Um `cron` no container resolve uma execução por dia.
  Celery e Airflow aqui seriam vaidade arquitetural.

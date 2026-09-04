# Roadmap

Este arquivo tem duas funções. A de cima é dizer o que vem. A de baixo — a mais
importante — é **conter o apetite**: toda ideia boa que aparecer no meio do
caminho vem parar aqui em vez de entrar no escopo da v0.1.

## Marcos até a v0.1.0

| | Marco | Estado | Pronto quando |
|---|---|---|---|
| M0 | Fundação: repositório, CI, banco | ✅ | `pytest` passa no GitHub Actions e o compose sobe o banco |
| M1 | Ingestão do PNCP | ✅ | Rodar duas vezes seguidas não duplica linha |
| M2 | Funil de matching (léxico + semântico) | ⬜ | O top 20 do dia sai no terminal e você concorda com o ranking |
| M3 | Grafo LangGraph com checkpoint | ⬜ | Matar o processo no meio e retomar sem reavaliar o que já foi julgado |
| M4 | Aprovação humana e alerta | ⬜ | Chega no celular um alerta com objeto, órgão, valor, prazo e link |
| M5 | Acabamento e release | ⬜ | Alguém clona e chega no primeiro resultado em 5 minutos pelo README |

### O que falta no M2

- Embedding do `objetoCompra` e da descrição do perfil (`bge-m3`, local)
- Migração `002`: extensão `vector`, tabela `contratacao_embedding`, índice HNSW
- Camada léxica: palavras positivas e negativas, CNAE, filtros de UF, valor e prazo
- Score combinado pelos pesos do perfil
- Comando `licita-radar match`

### O que falta no M3

- `EditalState` e os nós em `graph/nodes/`
- Arestas condicionais para `arquivar`
- `AsyncPostgresSaver` como checkpointer, com `thread_id` = `numeroControlePNCP`
- Nó de justificativa por LLM, respeitando `top_n_para_llm`
- `tokens_gastos` gravado em toda avaliação

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

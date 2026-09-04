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
| M3 | Grafo LangGraph com checkpoint | ⬜ | Matar o processo no meio e retomar sem reavaliar o que já foi julgado |
| M4 | Aprovação humana e alerta | ⬜ | Chega no celular um alerta com objeto, órgão, valor, prazo e link |
| M5 | Acabamento e release | ⬜ | Alguém clona e chega no primeiro resultado em 5 minutos pelo README |

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

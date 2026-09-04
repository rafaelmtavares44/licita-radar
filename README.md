# licita-radar

**Um radar de licitações públicas.** Ele lê o [PNCP](https://pncp.gov.br) todo dia, entende o que a sua empresa vende e avisa só quando aparece algo que vale a pena disputar.

> [!WARNING]
> Em construção. A v0.1 ainda não está publicada — hoje o projeto coleta do PNCP (M1) e pontua contra o seu perfil (M2). O roadmap abaixo diz o que falta.

---

## O problema

Centenas de órgãos públicos publicam contratações no PNCP todos os dias. Uma empresa pequena de software não tem ninguém para vasculhar isso — então perde por desconhecimento contratos que ganharia por mérito. Quem tem estrutura paga assinatura de portal de licitações. Quem não tem, descobre tarde.

## A proposta

Você descreve o que a sua empresa vende **uma vez**, num arquivo YAML. O licita-radar faz o resto:

```
PNCP ──► filtro léxico ──► similaridade semântica ──► LLM ──► você aprova ──► alerta
         (custo zero)      (custo marginal)          (só o topo)
```

A ordem importa: as camadas caras vêm **depois** de duas camadas baratas. Se cada contratação passasse por um modelo de linguagem, o projeto seria lento e caro. Do jeito que está, o LLM vê menos de 1% do que entra.

---

## Começando

Você precisa de Python 3.11+ e um Postgres 14+.

```bash
git clone https://github.com/SEU-USUARIO/licita-radar.git
cd licita-radar

# banco (ou aponte LR_DATABASE_URL para um Postgres que você já tenha)
docker compose up -d db

# dependências
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# configuração
cp .env.exemplo .env
cp perfil.exemplo.yaml perfil.yaml   # <- edite este arquivo

licita-radar migrar
licita-radar perfil                  # confere como o perfil foi interpretado
licita-radar ingest --uf GO
licita-radar listar
```

> [!TIP]
> **Já tem um Postgres instalado na máquina?** Não precisa desinstalar nem
> lembrar a senha dele — o `docker compose` sobe um container próprio, com
> usuário e senha `licita`. Se o compose reclamar que *a porta já está em
> uso*, ponha `LR_DB_PORT=5433` no `.env`, troque a porta também na
> `LR_DATABASE_URL`, e suba de novo.

### Travou? `licita-radar doctor`

```
Ambiente
  ✓ Python 3.12.3  win32
  ✓ event loop  WindowsSelectorEventLoopPolicy
  ✓ perfil (perfil.yaml)  Acme Software
  ! camada semântica  fastembed não instalado
      → opcional: pip install -e ".[semantico]"

Banco
  ✓ resolver 127.0.0.1  127.0.0.1
  ✗ porta 5432 aberta  Connection refused
      → suba o banco: docker compose up -d db
```

Ele testa cada camada separadamente — Python, event loop, perfil, DNS, porta
TCP, autenticação, extensão, migrações e a API do PNCP — e para no primeiro
problema com a instrução do conserto.

### Os comandos

| Comando | O que faz |
|---|---|
| `licita-radar migrar` | Cria ou atualiza o esquema do banco |
| `licita-radar perfil` | Valida o `perfil.yaml` e mostra como ele foi lido |
| `licita-radar ingest` | Busca contratações no PNCP e grava. `--seco` mostra sem gravar |
| `licita-radar listar` | Mostra o que está guardado, com proposta ainda aberta |
| `licita-radar match` | Pontua tudo contra o seu perfil e mostra o funil |
| `licita-radar doctor` | Diagnostica o ambiente quando algo não funciona |

### Onde procurar

```bash
licita-radar ingest --uf GO          # uma UF
licita-radar ingest --brasil         # o país inteiro (leva alguns minutos)
licita-radar ingest --ate 90         # propostas que encerram nos próximos 90 dias
licita-radar ingest --historico --dias 30   # backfill por data de publicação
```

Para filtrar por esfera de governo, use o perfil:

```yaml
restricoes:
  ufs: []              # vazio = Brasil inteiro
  esferas: ["F"]       # só o governo federal — E estadual, M municipal
```

### Calibrando sem baixar modelo

```bash
licita-radar match --sem-semantica
```

Roda só a camada léxica. Não baixa nada, responde em milissegundos, e é o
jeito certo de ajustar as palavras-chave do perfil antes de ligar a
semântica. A saída mostra o funil inteiro:

```
O funil
  não passou nos filtros           9  ███████████████
  vetada por palavra negativa      0
  abaixo do limiar                 3  █████
  candidata — merece o seu olho    0
```

Para ligar a camada semântica:

```bash
pip install -e ".[semantico]"    # ~200 MB, sem PyTorch
licita-radar match
```

O modelo (`paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensões) é baixado
na primeira execução e fica em cache.

---

## O perfil é a interface

Todo o conhecimento de negócio mora em um arquivo. Veja o [`perfil.exemplo.yaml`](perfil.exemplo.yaml) completo; o trecho que mais importa é este:

```yaml
palavras_chave:
  positivas:
    - desenvolvimento de software
    - sustentação de sistemas
  negativas:            # o filtro que mais aumenta a precisão
    - toner
    - cabeamento estruturado
    - link de internet
```

**Sobre as palavras negativas:** buscar "TI" no PNCP devolve toner, cartucho, cabeamento estruturado e nobreak. Essa lista vale mais para a qualidade do resultado do que qualquer sofisticação de modelo. Se o seu alerta estiver ruidoso, é quase sempre aqui que se conserta.

---

## Como o PNCP funciona (o que descobrimos apanhando)

A [API de consultas](https://pncp.gov.br/api/consulta/swagger-ui/index.html) é pública e não pede chave. Duas coisas surpreendem quem chega:

**A modalidade é obrigatória e única por chamada.** Não existe "me dê tudo": você itera sobre os códigos que interessam. Os relevantes para software:

| Código | Modalidade |
|---:|---|
| 6 | Pregão — Eletrônico |
| 8 | Dispensa de Licitação |
| 4 | Concorrência — Eletrônica |
| 9 | Inexigibilidade |
| 12 | Credenciamento |

**O `dataFinal` do endpoint de propostas abertas é o fim do prazo, não "até quando olhar".** Passar a data de hoje devolve o que encerra hoje — ou seja, quase tudo já fechado. Numa coleta real, 31 de 37 contratações vieram com prazo vencido por causa disso. A data precisa estar no futuro: é o que a opção `--ate` controla.

**A dispensa domina o volume, e ela é barata.** Numa amostra real de Goiás, 36 das 37 contratações abertas eram dispensa (modalidade 8), com valores entre R$ 900 e R$ 13 mil. Um `valor_minimo` de 50 mil no perfil elimina praticamente toda a modalidade — justamente aquela em que a empresa pequena tem chance.

**O objeto vem coberto de casca burocrática.** `DESPESA REFERENTE A`, `SOLICITAÇÃO DE`, `1 -TERMO DE SOLICITAÇÃO`, `[Portal de Compras Públicas] - DISPENSA -`. Esse prefixo é idêntico em licitação de software e de picolé; o projeto remove antes de comparar, senão a busca semântica aproxima coisas que não têm nada a ver.

**No Windows, o psycopg exige trocar o event loop.** O `asyncio.run()` de lá usa o `ProactorEventLoop`, incompatível com o psycopg assíncrono: a conexão falha com `InterfaceError` antes de tocar a rede, e o sintoma parece problema de rede. A CLI troca a política na importação; se você usar o pacote como biblioteca, faça o mesmo.

**Não há limite de requisições documentado.** O cliente trata como se houvesse: concorrência baixa, backoff exponencial e cache local opcional durante o desenvolvimento (`LR_PNCP_CACHE_LOCAL=true`).

---

## Roadmap

| | Marco | Estado |
|---|---|---|
| M0 | Fundação: repositório, CI, banco | ✅ |
| M1 | Ingestão do PNCP | ✅ |
| M2 | Funil de matching (léxico + semântico) | ✅ |
| M3 | Grafo LangGraph com checkpoint | ⬜ |
| M4 | Aprovação humana e alerta no Telegram | ⬜ |
| M5 | Acabamento e release `v0.1.0` | ⬜ |

Fora do escopo da v0.1, de propósito: leitura do edital em PDF, múltiplos perfis, painel web e API HTTP. Estão em [`docs/roadmap.md`](docs/roadmap.md) esperando a vez.

---

## Desenvolvimento

```bash
pytest              # nenhum teste toca a rede: tudo roda contra fixtures
ruff check .
mypy
```

Os testes usam [respx](https://lundberg.github.io/respx/) para interceptar o HTTP. Um CI que depende de um serviço externo estar no ar é um CI que quebra sozinho.

> `tests/fixtures/pncp/amostra_real_go.json` traz **12 contratações reais** de Goiás, capturadas em 04/09/2026 — é contra elas que o matching é testado. Para capturar as suas: `LR_PNCP_CACHE_LOCAL=true licita-radar ingest --seco` grava o que a API devolveu em `.cache_pncp/`.

## Licença

[Apache 2.0](LICENSE).

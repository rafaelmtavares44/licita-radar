# licita-radar

**Um radar de licitações públicas que também lê o edital.** Ele varre o [PNCP](https://pncp.gov.br) todo dia, entende o que a sua empresa vende, avisa só quando aparece algo que vale a pena disputar — e, do que você aprovar, baixa o edital e devolve um resumo executivo **com o trecho do documento ao lado de cada afirmação**.

> [!WARNING]
> Em construção. A v0.1 ainda não está publicada. Hoje o projeto coleta do PNCP (M1), pontua contra o seu perfil (M2), roda o grafo com revisão humana (M3) e analisa o edital (M4). O roadmap abaixo diz o que falta.

---

## O problema

Centenas de órgãos públicos publicam contratações no PNCP todos os dias. Uma empresa pequena de software não tem ninguém para vasculhar isso — então perde por desconhecimento contratos que ganharia por mérito. Quem tem estrutura paga assinatura de portal de licitações. Quem não tem, descobre tarde.

## A proposta

Você descreve o que a sua empresa vende **uma vez**, num arquivo YAML. O licita-radar faz o resto:

```
PNCP ─► filtro léxico ─► similaridade ─► LLM ─► você aprova ─► lê o edital ─► alerta
        (custo zero)     (marginal)      (o topo)              (só o aprovado)
```

A ordem importa: as camadas caras vêm **depois** das baratas. Se cada contratação passasse por um modelo de linguagem, o projeto seria lento e caro. Do jeito que está, o LLM vê menos de 1% do que entra — e o edital em PDF, menos ainda: só é baixado e lido depois que uma pessoa disse que aquela licitação interessa.

### O analista de editais

A dor de quem disputa licitação não é descobrir que o edital existe: é entender o edital. Oitenta páginas de juridiquês decidem se vale participar, e ler isso exige um profissional caro e escasso.

O `licita-radar analisar` baixa os anexos, extrai o texto e devolve o que importa — objeto real, exigências de habilitação, garantia, prazos, pagamento, multas e riscos. **Cada afirmação vem com o trecho literal do edital que a sustenta, e cada trecho é procurado de volta no documento original antes de aparecer na tela:**

```
✓ Pede atestado de capacidade técnica compatível com o objeto.
  "apresentar atestado de capacidade técnica, fornecido por pessoa jurídica"
? Exige certificação ISO 27001 válida.
  "a licitante deverá comprovar certificação ISO 27001 vigente"
  o trecho citado não existe no edital
```

O `?` é o ponto. Um resumo que inventa uma exigência faz a empresa desistir de uma licitação que venceria — e quem pediu o resumo é justamente quem não vai reler as oitenta páginas para perceber. A conferência é determinística, não custa uma chamada a mais de modelo, e está descrita em [`docs/decisoes/0007`](docs/decisoes/0007-toda-afirmacao-carrega-o-trecho-que-a-prova.md).

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
| `licita-radar radar` | Passa as contratações pelo grafo, até a revisão humana |
| `licita-radar revisar` | Mostra o que espera decisão; ao aprovar, já lê o edital |
| `licita-radar documentos` | Lista (e baixa, com `--baixar`) os anexos de uma contratação |
| `licita-radar analisar` | Resume o edital com o trecho de origem em cada afirmação |
| `licita-radar atualizar` | Coleta, pontua e roda o grafo — o ciclo inteiro, para agendar |
| `licita-radar servir` | Sobe a API e o painel web |
| `licita-radar alertar` | Confere o canal do Telegram e ajuda a configurá-lo |
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

### Mantendo o radar atualizado

Nada roda sozinho: o painel é uma janela sobre o que estes passos produziram.

```bash
licita-radar atualizar        # ingest + match + radar, de uma vez
```

O cabeçalho do painel mostra **quando foi a última coleta** e fica âmbar quando passa de um dia — porque uma tela com dado de quatro dias atrás é indistinguível de uma tela atualizada agora, e quem olha conclui que o PNCP é que parou de publicar.

Para rodar todo dia de manhã, agende o comando no seu sistema. No Windows, pelo **Agendador de Tarefas**; no Linux ou macOS, no `cron`:

```cron
0 7 * * 1-5  cd /caminho/do/licita-radar && .venv/bin/licita-radar atualizar
```

Licitação com prazo vencido some da lista por padrão — ela ocupa espaço e não há nada a fazer a respeito. O contador `+N encerradas` no topo da lista traz de volta quando você quiser.

### O painel

```bash
pip install -e ".[web]"
licita-radar servir            # http://127.0.0.1:8000
```

A tela lista as candidatas por score, mostra por que cada uma apareceu e deixa aprovar ou rejeitar ali mesmo — aprovar baixa o edital e o resume, com o trecho de origem embaixo de cada afirmação e a marca ✓ / ≈ / ? ao lado. A decisão retoma o mesmo grafo do `licita-radar revisar`: as duas interfaces conversam com o mesmo checkpoint.

Para mexer no front:

```bash
cd web && npm install
npm run dev                    # http://localhost:5173, recarrega ao salvar
npm run build                  # gera web/dist, que o `servir` passa a entregar
```

Sem `web/dist`, o `servir` entrega só a API — e ela sozinha já é útil, com documentação interativa em `/docs`.

### O alerta

```bash
licita-radar alertar --descobrir   # depois de mandar /start para o seu bot
licita-radar alertar --teste
```

Dois avisos chegam, em momentos diferentes e por razões diferentes:

- **quando o `radar` termina** — *"3 esperando a sua decisão"*, com objeto, órgão, prazo, score e a frase que explica por que apareceu
- **quando você aprova** — o resumo do edital, com as marcas ✓ / ≈ / ?

A separação não é detalhe. Aprovar é o que dispara a leitura do edital, então num `radar` agendado às 7h ninguém aprovou nada — se o alerta só existisse depois da aprovação, o canal ficaria mudo justamente na hora em que ele é mais útil.

Sem token configurado, o canal fica desligado e o alerta vai para o log.

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

**Há limite de requisições, e ele não está documentado.** Uma varredura nacional levou `429 Too Many Requests` a partir da página 16 — e, pior, a modalidade seguinte já começou bloqueada: o limite é por cliente, não por rota. O projeto responde com um freio adaptativo que espaça todas as chamadas, dobra o espaçamento a cada 429 e o reduz conforme as respostas voltam a passar. Também honra o cabeçalho `Retry-After` quando o servidor manda um.

---

## Roadmap

| | Marco | Estado |
|---|---|---|
| M0 | Fundação: repositório, CI, banco | ✅ |
| M1 | Ingestão do PNCP | ✅ |
| M2 | Funil de matching (léxico + semântico) | ✅ |
| M3 | Grafo LangGraph com checkpoint e revisão humana | ✅ |
| M4 | Analista de editais: download, extração e resumo com citação | ✅ |
| M5 | Painel web: API HTTP e a tela de decisão | ✅ |
| M6 | Alerta no Telegram | ✅ |
| M7 | Acabamento e release `v0.1.0` | ⬜ |

O alerta desceu na fila de propósito: sem o resumo do edital, ele avisaria a mesma coisa que os portais de licitação já avisam. Fora do escopo, ainda: múltiplos perfis, API HTTP e OCR de edital digitalizado — estão em [`docs/roadmap.md`](docs/roadmap.md) esperando a vez.

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

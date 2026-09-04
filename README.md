# licita-radar

**Um radar de licitações públicas.** Ele lê o [PNCP](https://pncp.gov.br) todo dia, entende o que a sua empresa vende e avisa só quando aparece algo que vale a pena disputar.

> [!WARNING]
> Em construção. A v0.1 ainda não está publicada — hoje o projeto tem a ingestão do PNCP funcionando (M1). O roadmap abaixo diz o que falta.

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

### Os comandos

| Comando | O que faz |
|---|---|
| `licita-radar migrar` | Cria ou atualiza o esquema do banco |
| `licita-radar perfil` | Valida o `perfil.yaml` e mostra como ele foi lido |
| `licita-radar ingest` | Busca contratações no PNCP e grava. `--seco` mostra sem gravar |
| `licita-radar listar` | Mostra o que ainda tem proposta aberta |

`licita-radar ingest --historico --dias 30` faz backfill por data de publicação, em vez de buscar só o que está com proposta aberta.

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

**Não há limite de requisições documentado.** O cliente trata como se houvesse: concorrência baixa, backoff exponencial e cache local opcional durante o desenvolvimento (`LR_PNCP_CACHE_LOCAL=true`).

---

## Roadmap

| | Marco | Estado |
|---|---|---|
| M0 | Fundação: repositório, CI, banco | ✅ |
| M1 | Ingestão do PNCP | ✅ |
| M2 | Funil de matching (léxico + semântico) | ⬜ |
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

> As fixtures em `tests/fixtures/pncp/` são **sintéticas** — seguem a estrutura documentada, mas foram escritas à mão. Para substituir por respostas reais: `LR_PNCP_CACHE_LOCAL=true licita-radar ingest --seco` grava o que a API devolveu em `.cache_pncp/`.

## Licença

[Apache 2.0](LICENSE).

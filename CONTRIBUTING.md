# Contribuindo

Obrigado pelo interesse. O projeto é pequeno e as regras são poucas.

## Ambiente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d db
cp .env.exemplo .env
cp perfil.exemplo.yaml perfil.yaml
```

## Antes de abrir PR

```bash
ruff check .
ruff format .
mypy
pytest
```

Os quatro precisam passar — é o que o CI roda.

## Testes

**Nenhum teste pode tocar a rede.** O HTTP é interceptado pelo
[respx](https://lundberg.github.io/respx/) contra fixtures em
`tests/fixtures/pncp/`. Um CI que depende do PNCP estar no ar é um CI que
quebra sozinho, num horário que ninguém escolheu.

Testes que precisam de Postgres de verdade ficam marcados com
`@pytest.mark.integracao` e são pulados por padrão:

```bash
export LR_TEST_DATABASE_URL=postgresql://licita:licita@localhost:5432/licita_radar
pytest -m integracao
```

## Convenções

- **O código é escrito em português.** Nomes de função, variável, tabela e
  mensagem de erro. O domínio é brasileiro e o vocabulário — edital, modalidade,
  órgão, contratação — não tem tradução boa. Manter isso consistente vale mais
  que seguir o costume de codar em inglês.
- **Mensagem de erro é interface.** Quem usa o licita-radar edita um YAML, não o
  código. Todo erro de configuração precisa dizer o que fazer para consertar.
- **Ingestão não falha por causa de campo opcional.** Registro torto vira `None`
  e um log; nunca uma exceção que derruba o lote inteiro.
- **Decisão de arquitetura vira ADR.** Meia página em `docs/decisoes/`, no
  formato dos que já estão lá.

## Escopo

O roadmap está em [`docs/roadmap.md`](docs/roadmap.md). A seção "Congelado —
fase 2" existe para segurar o apetite: nada dali entra antes da `v0.1.0` estar
publicada. Se sua ideia cai lá, abra uma issue — ela será bem-vinda depois do
release.

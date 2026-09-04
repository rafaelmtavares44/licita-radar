FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# As dependências mudam menos que o código: camada separada para
# aproveitar o cache do Docker entre builds.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Usuário sem privilégio: o container não tem motivo para rodar como root.
RUN useradd --create-home --uid 10001 radar
USER radar

ENTRYPOINT ["licita-radar"]
CMD ["--help"]

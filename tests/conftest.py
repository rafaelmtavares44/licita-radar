from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from licita_radar.config.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures" / "pncp"


def carregar_fixture(nome: str) -> dict[str, Any]:
    dados: dict[str, Any] = json.loads((FIXTURES / nome).read_text(encoding="utf-8"))
    return dados


@pytest.fixture
def pagina1() -> dict[str, Any]:
    return carregar_fixture("contratacoes_proposta_pagina1.json")


@pytest.fixture
def pagina2() -> dict[str, Any]:
    return carregar_fixture("contratacoes_proposta_pagina2.json")


@pytest.fixture
def settings_teste(tmp_path: Path) -> Settings:
    """Configuração isolada: sem cache em disco, sem esperar entre tentativas."""
    return Settings(
        pncp_base_url="https://pncp.exemplo.test/api/consulta",
        pncp_timeout_s=2.0,
        pncp_max_tentativas=3,
        pncp_concorrencia=2,
        pncp_tamanho_pagina=50,
        pncp_cache_local=False,
        pncp_cache_dir=tmp_path / "cache",
        database_url="postgresql://ninguem@localhost:1/inexistente",
    )


@pytest.fixture
def perfil_valido() -> dict[str, Any]:
    return {
        "id": "acme-software",
        "nome": "Acme Software",
        "descricao": (
            "Fábrica de software sob demanda: desenvolvimento de sistemas web, "
            "APIs de integração e sustentação evolutiva de sistemas legados."
        ),
        "cnaes": ["6201-5/01"],
        "palavras_chave": {
            "positivas": ["desenvolvimento de software", "sustentação de sistemas"],
            "negativas": ["toner", "cabeamento estruturado"],
        },
        "restricoes": {"ufs": ["go"], "modalidades": [6, 8], "valor_minimo": 50000},
        "pontuacao": {"peso_lexical": 0.35, "peso_semantico": 0.65, "limiar_alerta": 0.72},
        "canais": [{"tipo": "telegram", "chat_id": "${TELEGRAM_CHAT_ID}"}],
    }

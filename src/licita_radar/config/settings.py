"""Configuração de infraestrutura, lida do ambiente.

Regra de fronteira deste módulo: aqui mora só o que muda entre máquinas
(URL do banco, timeouts, nível de log). Conhecimento de negócio — o que a
empresa vende, o que ela quer receber — mora no perfil YAML, nunca aqui.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- banco ---
    database_url: str = "postgresql://licita:licita@localhost:5432/licita_radar"

    # --- PNCP ---
    pncp_base_url: str = "https://pncp.gov.br/api/consulta"
    pncp_timeout_s: float = 30.0
    pncp_max_tentativas: int = Field(default=5, ge=1, le=10)
    pncp_concorrencia: int = Field(default=3, ge=1, le=10)
    pncp_tamanho_pagina: int = Field(default=50, ge=10, le=500)
    pncp_cache_local: bool = False
    pncp_cache_dir: Path = Path(".cache_pncp")

    # --- perfil ---
    perfil_path: Path = Path("perfil.yaml")

    # --- observabilidade ---
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instância única, para não reler o .env a cada chamada."""
    return Settings()

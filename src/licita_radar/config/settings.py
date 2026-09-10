"""Configuração de infraestrutura, lida do ambiente.

Regra de fronteira deste módulo: aqui mora só o que muda entre máquinas
(URL do banco, timeouts, nível de log). Conhecimento de negócio — o que a
empresa vende, o que ela quer receber — mora no perfil YAML, nunca aqui.
"""

from __future__ import annotations

import re
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
    database_url: str = "postgresql://licita:licita@127.0.0.1:5432/licita_radar"

    #: Curto de propósito. Banco fora do ar é a falha mais comum de quem
    #: está começando, e ela deve aparecer em segundos, não em meio minuto.
    database_timeout_s: float = Field(default=5.0, ge=1.0, le=120.0)

    # --- PNCP ---
    pncp_base_url: str = "https://pncp.gov.br/api/consulta"
    #: 30s parecia folgado até o Pregão Eletrônico nacional: página funda,
    #: servidor lento, e o `ReadTimeout` derrubava a modalidade inteira
    #: depois de esgotar as tentativas. O PNCP é devagar, não é mudo — a
    #: mesma lição que a rota de arquivos já tinha dado.
    pncp_timeout_s: float = 60.0
    pncp_max_tentativas: int = Field(default=5, ge=1, le=10)
    #: Duas chamadas simultâneas já bastam, e o PNCP agradece. Com três, uma
    #: varredura nacional levou 429 a partir da página 16.
    pncp_concorrencia: int = Field(default=2, ge=1, le=10)

    #: Intervalo mínimo entre chamadas. Dobra a cada 429 e volta devagar.
    pncp_intervalo_min_s: float = Field(default=0.2, ge=0.0, le=10.0)
    pncp_intervalo_max_s: float = Field(default=8.0, ge=0.5, le=60.0)
    #: Páginas grandes são o remédio contra o 429, não contra a lentidão.
    #: Com 50, o Pregão Eletrônico nacional passa de 111 páginas e o PNCP
    #: começa a barrar por volta dali — e cada 429 dobra o intervalo do
    #: freio, então a coleta desacelera exatamente onde tinha mais a fazer.
    #: Com 500, o mesmo conteúdo cabe em ~12 chamadas. O limite é do PNCP.
    pncp_tamanho_pagina: int = Field(default=500, ge=10, le=500)
    pncp_cache_local: bool = False
    pncp_cache_dir: Path = Path(".cache_pncp")

    # --- documentos do edital ---
    # Outra API, outra base: `/api/consulta` devolve metadados da
    # contratação; os arquivos do edital só existem em `/api/pncp`.
    pncp_integracao_base_url: str = "https://pncp.gov.br/api/pncp"

    #: Baixar um edital não é baixar um JSON. Anexo de 30 MB com planta e
    #: memorial descritivo é rotina, e 30 s de timeout reprova quase todos.
    pncp_download_timeout_s: float = Field(default=120.0, ge=10.0, le=600.0)

    #: Listar anexos devolve dez linhas de JSON e mesmo assim é lento: uma
    #: medição real levou 58 s. O timeout curto do resto do PNCP corta essa
    #: rota no meio — ela precisa do seu próprio, e de um aviso na tela.
    pncp_arquivos_timeout_s: float = Field(default=120.0, ge=10.0, le=600.0)

    #: Onde os PDFs ficam. Fora do controle de versão, e reaproveitados
    #: entre execuções: edital publicado não muda.
    documentos_dir: Path = Path(".editais")
    documento_tamanho_max_mb: int = Field(default=40, ge=1, le=500)

    #: Quantos anexos entram na análise, em ordem de relevância. Três cobre
    #: edital + termo de referência + um anexo na esmagadora maioria.
    analise_max_documentos: int = Field(default=3, ge=1, le=20)

    #: Quanto texto de edital vai para o modelo por análise. Acima disso o
    #: recorte por assunto entra e escolhe o que enviar.
    analise_orcamento_caracteres: int = Field(default=24_000, ge=2_000, le=200_000)

    # --- LLM (opcional) ---
    # Protocolo da OpenAI: serve OpenAI, Groq, Ollama, OpenRouter, LM Studio.
    # Sem base_url e modelo, o projeto usa a justificativa heurística e roda
    # de ponta a ponta sem nenhuma chave.
    llm_base_url: str | None = None
    llm_modelo: str | None = None
    llm_api_key: str | None = None
    llm_timeout_s: float = 60.0

    # --- alerta (opcional) ---
    # Sem token e chat_id, os alertas ficam só no log e o projeto roda
    # igual — mesma regra do LLM. O token vem do @BotFather; o chat_id,
    # de `licita-radar alertar --descobrir`.
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_timeout_s: float = 20.0

    #: Endereço do painel, para o alerta virar um link clicável em vez de
    #: um comando para digitar no computador que a pessoa não está usando.
    painel_url: str | None = None

    # --- perfil ---
    perfil_path: Path = Path("perfil.yaml")

    # --- observabilidade ---
    log_level: str = "INFO"

    @property
    def database_url_segura(self) -> str:
        """A URL do banco sem a senha, para poder aparecer em mensagem de erro."""
        return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", self.database_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instância única, para não reler o .env a cada chamada."""
    return Settings()

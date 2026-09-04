"""O formato normalizado de uma contratação, depois de sair da API.

Tudo abaixo desta camada fala PNCP. Tudo acima fala `Contratacao`. É essa
fronteira que permite trocar a fonte de dados sem tocar em matching, grafo
ou notificação.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Contratacao(BaseModel):
    """Uma contratação pública, no vocabulário do projeto."""

    model_config = ConfigDict(frozen=True)

    numero_controle_pncp: str
    modalidade_codigo: int
    objeto: str
    orgao_cnpj: str | None = None
    orgao_nome: str | None = None
    uf: str | None = None
    municipio: str | None = None
    valor_estimado: Decimal | None = None
    data_publicacao: date | None = None
    abertura_proposta: datetime | None = None
    encerramento_proposta: datetime | None = None

    #: Resposta crua da API, preservada inteira. Custa pouco em disco e
    #: evita ter que rebuscar a API quando descobrirmos, daqui a um mês,
    #: que precisávamos de um campo que descartamos hoje.
    payload: dict[str, Any] = Field(repr=False)

    @property
    def url_pncp(self) -> str | None:
        """Link para a contratação no portal, para colar no alerta.

        O `numeroControlePNCP` tem o formato `<cnpj>-<tipo>-<sequencial>/<ano>`.
        A rota pública do portal remonta essas partes.
        """
        try:
            identificacao, ano = self.numero_controle_pncp.split("/")
            cnpj, _tipo, sequencial = identificacao.split("-")
        except ValueError:
            return None
        return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{int(sequencial)}"


class PaginaPNCP(BaseModel):
    """Envelope de paginação devolvido pela API de consultas."""

    data: list[dict[str, Any]] = Field(default_factory=list)
    total_registros: int = 0
    total_paginas: int = 0
    numero_pagina: int = 1
    paginas_restantes: int = 0
    empty: bool = False

    @classmethod
    def de_resposta(cls, bruto: dict[str, Any]) -> PaginaPNCP:
        return cls(
            data=bruto.get("data") or [],
            total_registros=bruto.get("totalRegistros", 0),
            total_paginas=bruto.get("totalPaginas", 0),
            numero_pagina=bruto.get("numeroPagina", 1),
            paginas_restantes=bruto.get("paginasRestantes", 0),
            empty=bruto.get("empty", False),
        )

    @property
    def tem_proxima(self) -> bool:
        return self.paginas_restantes > 0

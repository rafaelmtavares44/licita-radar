"""Do JSON do PNCP para o modelo do projeto.

Este módulo é deliberadamente paranoico. A API é pública, versionada com
pouca cerimônia, e nem todo campo documentado aparece em toda resposta.
A regra aqui: **campo ausente vira `None`, nunca exceção**. Perder um
valor opcional é aceitável; derrubar a ingestão inteira por causa de um
registro torto não é.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from dateutil import parser as dateparser

from licita_radar.ingest.modelos import Contratacao

logger = logging.getLogger(__name__)

# O nome exato do campo de encerramento ainda não foi confirmado numa
# resposta real da API — a documentação e os exemplos divergem. Em vez de
# apostar em um nome, aceitamos os candidatos conhecidos, na ordem.
_CAMPOS_ENCERRAMENTO = (
    "dataEncerramentoProposta",
    "dataFechamentoProposta",
    "dataEncerramentoPropostaPncp",
)
_CAMPOS_ABERTURA = ("dataAberturaProposta", "dataAberturaPropostaPncp")

_ESPACOS = re.compile(r"\s+")


def sem_acento(texto: str) -> str:
    """Normaliza para comparação: minúsculas, sem acento, espaço único.

    Usada pelo matching léxico. `LICITAÇÃO` e `licitacao` precisam ser a
    mesma coisa quando o assunto é casar palavra-chave.
    """
    decomposto = unicodedata.normalize("NFKD", texto)
    sem_marcas = "".join(c for c in decomposto if not unicodedata.combining(c))
    return _ESPACOS.sub(" ", sem_marcas).strip().lower()


def limpar_texto(bruto: str | None) -> str:
    """Colapsa quebras de linha e espaços múltiplos, preservando acento.

    O `objetoCompra` costuma vir com quebras de linha do sistema de origem;
    sem isso o texto polui log, terminal e prompt.
    """
    if not bruto:
        return ""
    return _ESPACOS.sub(" ", bruto).strip()


def _primeiro_presente(dados: dict[str, Any], chaves: tuple[str, ...]) -> Any | None:
    for chave in chaves:
        valor = dados.get(chave)
        if valor not in (None, ""):
            return valor
    return None


def para_datetime(valor: Any) -> datetime | None:
    if valor in (None, ""):
        return None
    if isinstance(valor, datetime):
        return valor
    try:
        return dateparser.isoparse(str(valor))
    except (ValueError, TypeError):
        logger.debug("data não interpretável descartada: %r", valor)
        return None


def para_date(valor: Any) -> date | None:
    momento = para_datetime(valor)
    return momento.date() if momento else None


def para_decimal(valor: Any) -> Decimal | None:
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        logger.debug("valor não numérico descartado: %r", valor)
        return None


def normalizar_contratacao(bruto: dict[str, Any]) -> Contratacao | None:
    """Converte um item da API. Devolve `None` se o item for inutilizável.

    Só dois campos são realmente obrigatórios: a chave natural e o objeto.
    Sem chave não há como deduplicar; sem objeto não há o que casar.
    """
    numero_controle = bruto.get("numeroControlePNCP")
    objeto = limpar_texto(bruto.get("objetoCompra"))

    if not numero_controle or not objeto:
        logger.warning(
            "contratação descartada na normalização (chave=%r, objeto vazio=%s)",
            numero_controle,
            not objeto,
        )
        return None

    orgao = bruto.get("orgaoEntidade") or {}
    unidade = bruto.get("unidadeOrgao") or {}

    return Contratacao(
        numero_controle_pncp=str(numero_controle),
        modalidade_codigo=int(bruto.get("modalidadeId") or bruto.get("modalidadeCodigo") or 0),
        objeto=objeto,
        orgao_cnpj=orgao.get("cnpj"),
        orgao_nome=limpar_texto(orgao.get("razaoSocial")) or None,
        uf=unidade.get("ufSigla"),
        municipio=unidade.get("municipioNome"),
        valor_estimado=para_decimal(bruto.get("valorTotalEstimado")),
        data_publicacao=para_date(bruto.get("dataPublicacaoPncp")),
        abertura_proposta=para_datetime(_primeiro_presente(bruto, _CAMPOS_ABERTURA)),
        encerramento_proposta=para_datetime(_primeiro_presente(bruto, _CAMPOS_ENCERRAMENTO)),
        payload=bruto,
    )


def normalizar_pagina(itens: list[dict[str, Any]]) -> list[Contratacao]:
    """Normaliza uma página inteira, pulando o que não dá para aproveitar."""
    resultado = [normalizar_contratacao(item) for item in itens]
    return [c for c in resultado if c is not None]

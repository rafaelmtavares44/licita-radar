"""O perfil da empresa — o único lugar com conhecimento de negócio.

Na v0.1 este arquivo YAML *é* a interface do produto: quem usa o
licita-radar edita ele, não o código. Por isso a validação aqui falha com
mensagem em português legível, e não com stack trace do Pydantic.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from licita_radar.ingest.modalidades import MODALIDADES_TI
from licita_radar.ingest.normalizar import sem_acento

_VAR_AMBIENTE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class ErroDePerfil(ValueError):
    """Perfil ausente, mal formado ou inválido — sempre com dica de conserto."""


class PalavrasChave(BaseModel):
    model_config = ConfigDict(frozen=True)

    positivas: list[str] = Field(default_factory=list)
    negativas: list[str] = Field(default_factory=list)

    @property
    def positivas_normalizadas(self) -> tuple[str, ...]:
        return tuple(sem_acento(p) for p in self.positivas)

    @property
    def negativas_normalizadas(self) -> tuple[str, ...]:
        return tuple(sem_acento(p) for p in self.negativas)


#: Como o PNCP codifica a esfera do órgão em orgaoEntidade.esferaId.
ESFERAS = {"F": "Federal", "E": "Estadual", "M": "Municipal", "D": "Distrital"}


class Restricoes(BaseModel):
    model_config = ConfigDict(frozen=True)

    ufs: list[str] = Field(default_factory=list)
    #: Vazio = todas. ["F"] traz só o governo federal.
    esferas: list[str] = Field(default_factory=list)
    modalidades: list[int] = Field(default_factory=lambda: [int(m) for m in MODALIDADES_TI])
    valor_minimo: Decimal | None = None
    dias_minimos_ate_encerramento: int = Field(default=0, ge=0)

    @field_validator("ufs")
    @classmethod
    def _ufs_maiusculas(cls, valor: list[str]) -> list[str]:
        for uf in valor:
            if len(uf) != 2:
                raise ValueError(f"UF inválida: {uf!r}. Use a sigla de duas letras, como 'GO'.")
        return [uf.upper() for uf in valor]

    @field_validator("esferas")
    @classmethod
    def _esferas_conhecidas(cls, valor: list[str]) -> list[str]:
        for esfera in valor:
            if esfera.upper() not in ESFERAS:
                validas = ", ".join(f"{k} ({v})" for k, v in ESFERAS.items())
                raise ValueError(f"Esfera {esfera!r} não existe. As válidas são: {validas}.")
        return [e.upper() for e in valor]

    @field_validator("modalidades")
    @classmethod
    def _modalidades_conhecidas(cls, valor: list[int]) -> list[int]:
        for codigo in valor:
            if not 1 <= codigo <= 13:
                raise ValueError(
                    f"Modalidade {codigo} não existe no PNCP. Os códigos válidos vão de 1 a 13."
                )
        return valor


class Pontuacao(BaseModel):
    model_config = ConfigDict(frozen=True)

    peso_lexical: float = Field(default=0.35, ge=0.0, le=1.0)
    peso_semantico: float = Field(default=0.65, ge=0.0, le=1.0)
    limiar_alerta: float = Field(default=0.72, ge=0.0, le=1.0)
    top_n_para_llm: int = Field(default=15, ge=0, le=200)

    @field_validator("peso_semantico")
    @classmethod
    def _pesos_somam_um(cls, valor: float, info: Any) -> float:
        lexical = info.data.get("peso_lexical")
        if lexical is not None and abs((lexical + valor) - 1.0) > 1e-6:
            raise ValueError(
                f"peso_lexical + peso_semantico deve dar 1.0 "
                f"(hoje dá {lexical + valor:.2f}). Ajuste um dos dois."
            )
        return valor


class Canal(BaseModel):
    model_config = ConfigDict(frozen=True)

    tipo: str
    chat_id: str | None = None
    email: str | None = None


class Perfil(BaseModel):
    """O que a empresa vende, e o que ela quer receber."""

    model_config = ConfigDict(frozen=True)

    id: str
    nome: str
    #: O assunto da busca, em duas ou três palavras — "TI", "obras",
    #: "saúde". Aparece no cabeçalho do painel. Não entra em nenhum
    #: filtro: quem decide o que casa são as palavras-chave e o
    #: embedding. Isto existe para quem olha a tela saber, sem abrir o
    #: YAML, que radar está vendo.
    foco: str = ""
    descricao: str = Field(min_length=30)
    cnaes: list[str] = Field(default_factory=list)
    palavras_chave: PalavrasChave = Field(default_factory=PalavrasChave)
    restricoes: Restricoes = Field(default_factory=Restricoes)
    pontuacao: Pontuacao = Field(default_factory=Pontuacao)
    canais: list[Canal] = Field(default_factory=list)

    @field_validator("descricao")
    @classmethod
    def _descricao_util(cls, valor: str) -> str:
        # A descrição vira embedding. Uma linha vaga produz matching vago:
        # "empresa de tecnologia e inovação" casa com qualquer coisa e com
        # nada. O piso de 12 palavras força a dizer o que vocês de fato vendem.
        if len(valor.split()) < 12:
            raise ValueError(
                "A descrição precisa de pelo menos 12 palavras — ela vira o embedding "
                "que representa a empresa. Escreva os serviços concretos que vocês "
                "prestam, não o posicionamento institucional."
            )
        return valor.strip()


def _expandir_variaveis(no: Any) -> Any:
    """Resolve ``${VAR}`` no YAML com o ambiente — segredo não vai para o git."""
    if isinstance(no, str):
        return _VAR_AMBIENTE.sub(lambda m: os.environ.get(m.group(1), ""), no)
    if isinstance(no, dict):
        return {chave: _expandir_variaveis(valor) for chave, valor in no.items()}
    if isinstance(no, list):
        return [_expandir_variaveis(item) for item in no]
    return no


def _formatar_erro(erro: ValidationError, caminho: Path) -> str:
    linhas = [f"O perfil em {caminho} tem problema:"]
    for detalhe in erro.errors():
        campo = " → ".join(str(parte) for parte in detalhe["loc"]) or "(raiz)"
        linhas.append(f"  • {campo}: {detalhe['msg']}")
    return "\n".join(linhas)


def carregar_perfil(caminho: Path | str) -> Perfil:
    """Lê e valida o YAML. Todo erro sai como `ErroDePerfil` explicável."""
    caminho = Path(caminho)

    if not caminho.exists():
        raise ErroDePerfil(
            f"Não encontrei o perfil em {caminho}. "
            f"Copie o perfil.exemplo.yaml para {caminho} e ajuste com os dados da empresa."
        )

    try:
        cru = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    except yaml.YAMLError as erro:
        raise ErroDePerfil(f"O arquivo {caminho} não é um YAML válido:\n{erro}") from erro

    if not isinstance(cru, dict):
        raise ErroDePerfil(f"O arquivo {caminho} está vazio ou não descreve um perfil.")

    try:
        return Perfil.model_validate(_expandir_variaveis(cru))
    except ValidationError as erro:
        raise ErroDePerfil(_formatar_erro(erro, caminho)) from erro

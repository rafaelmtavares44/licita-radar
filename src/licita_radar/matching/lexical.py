"""Camada 1 do funil: filtro léxico. Custo zero, e é onde mora o domínio.

Duas coisas acontecem aqui, e elas são diferentes:

**Elegibilidade** — UF, modalidade, valor, prazo. É sim ou não, não é nota.
Uma licitação em São Paulo para quem só atende Goiás não é "pouco aderente";
ela simplesmente não conta.

**Score léxico** — quantas palavras-chave do perfil aparecem no objeto, com
veto imediato se alguma palavra negativa aparecer.

O veto é a peça mais valiosa do projeto inteiro. Buscar "TI" no PNCP devolve
toner, cartucho, cabeamento e nobreak; nenhum modelo de linguagem conserta
isso melhor do que uma lista de nove palavras escrita por quem conhece o
próprio negócio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from licita_radar.config.perfil import Perfil
from licita_radar.ingest.modelos import Contratacao
from licita_radar.ingest.normalizar import sem_acento

#: Com quantas palavras-chave positivas o score léxico já satura. Três
#: menções ao vocabulário da empresa é sinal forte; a quarta não acrescenta.
SATURACAO = 3


@dataclass(frozen=True)
class ResultadoLexical:
    score: float
    encontradas: tuple[str, ...] = field(default_factory=tuple)
    vetada_por: str | None = None

    @property
    def vetada(self) -> bool:
        return self.vetada_por is not None


def avaliar_elegibilidade(
    contratacao: Contratacao, perfil: Perfil, *, agora: datetime | None = None
) -> str | None:
    """Devolve o motivo da inelegibilidade, ou `None` se a licitação conta.

    Motivo em texto, não booleano: quando o usuário perguntar "por que este
    edital não me foi mostrado?", a resposta precisa existir.
    """
    r = perfil.restricoes

    if r.ufs and contratacao.uf and contratacao.uf.upper() not in r.ufs:
        return f"fora das UFs do perfil (é {contratacao.uf})"

    if r.modalidades and contratacao.modalidade_codigo not in r.modalidades:
        return f"modalidade {contratacao.modalidade_codigo} não está no perfil"

    if (
        r.valor_minimo is not None
        and contratacao.valor_estimado is not None
        and contratacao.valor_estimado < r.valor_minimo
    ):
        return f"valor abaixo do mínimo (R$ {contratacao.valor_estimado:,.2f})"

    if contratacao.encerramento_proposta:
        momento = agora or datetime.now(UTC)
        encerramento = contratacao.encerramento_proposta
        if encerramento.tzinfo is None:
            encerramento = encerramento.replace(tzinfo=UTC)
        dias = (encerramento - momento).days

        # O endpoint /contratacoes/proposta devolve muita coisa encerrando
        # hoje mesmo — visto na amostra real de 04/09/2026. "Já encerrou" e
        # "não dá tempo" são situações diferentes e merecem motivos diferentes.
        if dias < 0:
            return f"prazo já encerrado em {encerramento.date().strftime('%d/%m/%Y')}"
        if r.dias_minimos_ate_encerramento and dias < r.dias_minimos_ate_encerramento:
            return f"prazo curto demais (encerra em {dias} dia(s))"

    return None


def pontuar_lexicalmente(objeto: str, perfil: Perfil) -> ResultadoLexical:
    """Nota de 0 a 1 pelas palavras-chave, com veto pelas negativas.

    A comparação é feita sem acento e em minúsculas, dos dois lados: no
    PNCP o mesmo serviço aparece como "MANUTENÇÃO EVOLUTIVA" e
    "manutencao evolutiva" dependendo do sistema que publicou.
    """
    alvo = sem_acento(objeto)

    for negativa in perfil.palavras_chave.negativas_normalizadas:
        if negativa and negativa in alvo:
            return ResultadoLexical(score=0.0, vetada_por=negativa)

    positivas = perfil.palavras_chave.positivas_normalizadas
    encontradas = tuple(p for p in positivas if p and p in alvo)

    if not positivas:
        # Perfil sem palavras positivas não deve zerar tudo — deixa a
        # decisão inteira para a camada semântica.
        return ResultadoLexical(score=0.0)

    teto = min(SATURACAO, len(positivas))
    score = min(1.0, len(encontradas) / teto)
    return ResultadoLexical(score=score, encontradas=encontradas)

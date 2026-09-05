"""Monta o grafo e o compila com checkpoint.

```
            triar ──(vetada)──────────────┐
              │                            │
         (segue)                           │
              ↓                            │
           pontuar ──(abaixo do limiar)────┤
              │                            │
         (candidata)                       ↓
              ↓                        arquivar → END
          justificar
              │
              ↓
      revisar  ← interrupt(): o grafo dorme aqui
              │
      ┌───(aprovada)───┐
      ↓                ↓
  analisar         arquivar
      │                │
      ↓                │
  notificar            │
      │                │
      └───→ END ←──────┘
```

`analisar` fica depois da pessoa, não antes: baixar e resumir um edital de
80 páginas é o passo mais caro do projeto, e pagá-lo por uma licitação que
será descartada em dois segundos é gastar para não usar.

O `thread_id` de cada execução é o `numeroControlePNCP`. É isso que torna
o reprocessamento barato: retomar do checkpoint em vez de pagar de novo
pela avaliação — e que faz a pausa humana funcionar mesmo se o processo
morrer no meio.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from licita_radar.graph import nodes
from licita_radar.graph.nodes import Dependencias
from licita_radar.graph.state import EditalState

logger = logging.getLogger(__name__)


def _apos_triagem(estado: EditalState) -> Literal["pontuar", "arquivar"]:
    return "arquivar" if estado.get("situacao") == "triada_fora" else "pontuar"


def _apos_pontuacao(estado: EditalState) -> Literal["justificar", "arquivar"]:
    return "arquivar" if estado.get("situacao") == "abaixo_limiar" else "justificar"


def _apos_revisao(estado: EditalState) -> Literal["analisar", "arquivar"]:
    return "analisar" if estado.get("decisao_humana") == "aprovada" else "arquivar"


def construir_grafo(deps: Dependencias) -> Any:
    """Monta o grafo. As dependências entram aqui, não dentro dos nós."""
    grafo = StateGraph(EditalState)

    def _sincrono(funcao: Callable[..., EditalState]) -> Callable[[EditalState], EditalState]:
        def envelope(estado: EditalState) -> EditalState:
            return funcao(estado, deps)

        return envelope

    async def _justificar(estado: EditalState) -> EditalState:
        return await nodes.justificar(estado, deps)

    async def _analisar(estado: EditalState) -> EditalState:
        return await nodes.analisar(estado, deps)

    async def _notificar(estado: EditalState) -> EditalState:
        return await nodes.notificar(estado, deps)

    grafo.add_node("triar", _sincrono(nodes.triar))
    grafo.add_node("pontuar", _sincrono(nodes.pontuar))
    grafo.add_node("justificar", _justificar)
    grafo.add_node("revisar", nodes.revisar)
    grafo.add_node("analisar", _analisar)
    grafo.add_node("notificar", _notificar)
    grafo.add_node("arquivar", nodes.arquivar)

    grafo.add_edge(START, "triar")

    # As bifurcações são explícitas: o descarte é uma decisão registrada do
    # grafo, não um `continue` escondido dentro de um laço.
    grafo.add_conditional_edges("triar", _apos_triagem, ["pontuar", "arquivar"])
    grafo.add_conditional_edges("pontuar", _apos_pontuacao, ["justificar", "arquivar"])
    grafo.add_edge("justificar", "revisar")
    grafo.add_conditional_edges("revisar", _apos_revisao, ["analisar", "arquivar"])
    grafo.add_edge("analisar", "notificar")

    grafo.add_edge("notificar", END)
    grafo.add_edge("arquivar", END)

    return grafo


def compilar(deps: Dependencias, checkpointer: Any = None) -> Any:
    """Compila o grafo. Sem checkpointer, não há retomada — só para teste."""
    return construir_grafo(deps).compile(checkpointer=checkpointer)


def configuracao(numero_controle: str) -> dict[str, Any]:
    """O `thread_id` é a chave natural da contratação.

    Consequência prática: rodar o mesmo edital duas vezes não recomeça do
    zero nem duplica gasto — o LangGraph reconhece a thread e retoma.
    """
    return {"configurable": {"thread_id": numero_controle}}

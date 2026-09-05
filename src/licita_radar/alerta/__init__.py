"""O aviso que chega sem você abrir nada.

O alerta fecha o ciclo do projeto, e o desenho dele tem uma sutileza que
não é óbvia: **ele não pode vir só depois de aprovar.** Aprovar é o que
dispara a leitura do edital, e se o radar roda sozinho às 7h da manhã
ninguém aprovou coisa alguma — o canal ficaria mudo justamente quando é
mais útil.

Por isso são dois momentos, com propósitos diferentes:

*triagem*, quando o `radar` termina
    "3 esperando a sua decisão", com objeto, órgão, prazo, score e a frase
    que explica por que aquilo apareceu. É o que faz alguém pegar o
    celular.

*análise*, quando você aprova
    o resumo do edital com ✓ / ≈ / ?. É o que faz ter valido a pena.

Como o LLM, o canal é opcional: sem token configurado o `CanalDesligado`
assume, registra em log e o projeto roda igual.
"""

from licita_radar.alerta.canal import Canal, CanalDesligado, construir_canal
from licita_radar.alerta.mensagem import mensagem_da_analise, mensagem_de_triagem

__all__ = [
    "Canal",
    "CanalDesligado",
    "construir_canal",
    "mensagem_da_analise",
    "mensagem_de_triagem",
]

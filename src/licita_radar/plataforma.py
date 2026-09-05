"""O ajuste de event loop que o Windows exige, num lugar só.

No Windows, o `asyncio` usa o `ProactorEventLoop` por padrão e o psycopg
**recusa** rodar em modo assíncrono nele: a conexão morre com
`InterfaceError` antes de tocar a rede. O sintoma engana — o container
está de pé, a porta publicada, e o erro fala de conexão.

Havia duas cópias do conserto (na importação de `cli.py` e de
`conftest.py`) e elas não cobriram o servidor. O uvicorn faz:

    asyncio.run(self.serve(), loop_factory=self.config.get_loop_factory())

e `asyncio.run` com `loop_factory` explícito **ignora a política** — a
defesa continuava instalada e era simplesmente pulada. Por isso agora há
as duas metades aqui:

`ajustar_politica()`
    para quem chama `asyncio.run()` e obedece à política — a CLI e os
    testes.

`fabrica_de_loop()`
    para quem exige uma fábrica e não olha a política — o uvicorn a
    recebe por nome, em `loop=`.
"""

from __future__ import annotations

import asyncio
import sys

#: O caminho que o uvicorn importa para montar o loop. Ele aceita uma
#: fábrica própria por string em `loop=`, e é assim que a correção alcança
#: um processo que a CLI nem chega a tocar — o filho do `--recarregar`.
CAMINHO_DA_FABRICA = "licita_radar.plataforma:fabrica_de_loop"


def ajustar_politica() -> None:
    """Troca a política padrão do Windows. Não custa nada nos outros."""
    if sys.platform == "win32":  # pragma: no cover — só roda no Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def fabrica_de_loop() -> asyncio.AbstractEventLoop:
    """Um event loop em que o psycopg assíncrono funciona."""
    if sys.platform == "win32":  # pragma: no cover — só roda no Windows
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()

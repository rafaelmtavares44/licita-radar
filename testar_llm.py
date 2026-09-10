"""Descobre qual modelo do provedor ainda tem cota para hoje.

Rode com `python testar_llm.py`. Ele lê a configuração do `.env` — a
chave não aparece na saída.

Existe porque nenhuma página de documentação sabe a resposta. A cota do
plano gratuito é por modelo, muda sem aviso, e os blogs que a publicam
erram: um deles dizia 1.500 pedidos por dia para um modelo que a API
respondeu ter 20. O provedor é a única fonte confiável sobre o provedor,
e ele conta a verdade no corpo do 429 — de graça, em menos de um
segundo.

Cada modelo custa exatamente um pedido, com a mensagem mais curta
possível.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from licita_radar.config.settings import get_settings
from licita_radar.llm import cota_diaria, erro_do_corpo, listar_modelos, provaveis_de_chat
from licita_radar.plataforma import ajustar_politica

#: Testar dezenas de modelos gastaria a cota de todos eles.
QUANTOS = 6


def _mesmo(a: str, b: str) -> bool:
    """`gemini-flash-latest` e `models/gemini-flash-latest` são um só."""
    return a.removeprefix("models/") == b.removeprefix("models/")


async def principal() -> None:
    s = get_settings()
    base = (s.llm_base_url or "").rstrip("/")

    print(f"provedor : {base}")
    print(f"chave    : {'presente' if s.llm_api_key else 'AUSENTE'}")
    if not s.llm_api_key:
        print("\nsem chave não há o que testar — confira LR_LLM_API_KEY no .env")
        return

    todos = await listar_modelos(base_url=base, api_key=s.llm_api_key)
    candidatos = provaveis_de_chat(todos)[:QUANTOS]
    # O que está no .env vai junto, mesmo que não esteja entre os favoritos:
    # a pergunta que trouxe você aqui foi sobre ele. Mas o Google lista
    # `models/gemini-flash-latest` e o .env costuma trazer o nome curto —
    # a mesma coisa escrita de dois jeitos. Num script cujo propósito é
    # gastar pouca cota, testar o mesmo modelo duas vezes é o pior erro
    # possível.
    if s.llm_modelo and not any(_mesmo(s.llm_modelo, c) for c in candidatos):
        candidatos.insert(0, s.llm_modelo)

    print(f"testando : {len(candidatos)} de {len(todos)} modelos\n")

    cabecalhos = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {s.llm_api_key}",
    }
    rota = f"{base}/chat/completions"
    aprovados: list[tuple[str, float]] = []

    async with httpx.AsyncClient(timeout=60.0) as cliente:
        for modelo in candidatos:
            corpo = {
                "model": modelo,
                "messages": [{"role": "user", "content": "oi"}],
                "max_tokens": 16,
            }
            inicio = time.monotonic()
            try:
                r = await cliente.post(rota, headers=cabecalhos, json=corpo)
            except Exception as erro:
                ms = (time.monotonic() - inicio) * 1000
                print(f"  FALHA  {modelo:38} {ms:6.0f} ms · {type(erro).__name__}")
                continue

            ms = (time.monotonic() - inicio) * 1000
            if r.status_code == 200:
                print(f"  LIVRE  {modelo:38} {ms:6.0f} ms")
                aprovados.append((modelo, ms))
            elif (diaria := cota_diaria(r)) is not None:
                print(f"  CHEIO  {modelo:38} {ms:6.0f} ms · {diaria}")
            else:
                recado = str(erro_do_corpo(r).get("message", "")) or r.text
                print(f"  {r.status_code}    {modelo:38} {ms:6.0f} ms · {recado[:90]}")

    print()
    if aprovados:
        # A ordem de teste vem de um palpite sobre o NOME. A recomendação
        # pode fazer melhor: agora existem dois fatos medidos. "Lite" é
        # modelo mais fraco, e ler edital citando o trecho de origem é
        # justamente onde isso aparece — então ele fica atrás, mesmo
        # quando é o mais rápido. Entre iguais, decide o relógio.
        def _preferencia(item: tuple[str, float]) -> tuple[int, float]:
            modelo, ms = item
            return (1 if "lite" in modelo.lower() else 0, ms)

        escolhido = min(aprovados, key=_preferencia)[0].removeprefix("models/")
        print(f"Ponha no .env:  LR_LLM_MODELO={escolhido}")
        if len(aprovados) > 1:
            outros = ", ".join(
                f"{m.removeprefix('models/')} ({ms / 1000:.1f}s)"
                for m, ms in sorted(aprovados, key=_preferencia)[1:]
            )
            print(f"Também livres:  {outros}")
    else:
        print("Nenhum modelo respondeu. Se todos estão CHEIO, a cota volta amanhã —")
        print("ou crie a chave em outro projeto do Google, já que a cota é por projeto.")


if __name__ == "__main__":
    ajustar_politica()
    asyncio.run(principal())

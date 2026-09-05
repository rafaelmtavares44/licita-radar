"""O texto que chega no celular.

Duas mensagens, dois propósitos. A de triagem responde *"vale eu parar o
que estou fazendo?"* — e para isso precisa do prazo, do valor e da frase
que explica por que aquilo apareceu, nesta ordem. A de análise responde
*"o que o edital exige?"*, e leva as marcas ✓ / ≈ / ? junto, porque um
resumo sem a distinção seria pior no celular do que no terminal: ninguém
confere edital de oitenta páginas no ônibus.
"""

from __future__ import annotations

from typing import Any

from licita_radar.alerta.canal import escapar

#: A mesma marca do terminal e do painel. Três superfícies, um vocabulário.
MARCAS = {"sustentada": "✓", "numero_sem_apoio": "≈", "nao_encontrada": "?"}

ROTULOS = {
    "objeto": "O que é",
    "habilitacao": "Para participar",
    "garantia": "Garantia",
    "prazos": "Prazos",
    "pagamento": "Pagamento",
    "penalidades": "Multas",
    "riscos": "Atenção",
}


def _moeda(valor: Any) -> str:
    if valor in (None, ""):
        return "valor não informado"
    texto = f"{float(valor):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {texto}"


def _prazo(iso: str | None) -> str:
    if not iso:
        return "sem prazo informado"
    return f"encerra {escapar(str(iso)[:10])}"


def _url(numero: str) -> str | None:
    try:
        identificacao, ano = numero.split("/")
        cnpj, _, sequencial = identificacao.split("-")
    except ValueError:
        return None
    return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{int(sequencial)}"


def mensagem_de_triagem(
    candidatas: list[dict[str, Any]], *, painel: str | None = None, mostrar: int = 5
) -> str:
    """ "Tem coisa esperando você" — com o suficiente para decidir se vale abrir.

    A lista é cortada de propósito: cinco cabem numa tela de celular sem
    rolar, e um alerta que precisa de rolagem para ser compreendido já
    falhou como alerta.
    """
    if not candidatas:
        return "<b>licita-radar</b>\nNenhuma licitação nova passou no funil desta vez."

    quantas = len(candidatas)
    plural = "licitação espera" if quantas == 1 else "licitações esperam"
    partes = [f"<b>🔔 {quantas} {plural} a sua decisão</b>"]

    for item in candidatas[:mostrar]:
        numero = str(item.get("numero_controle") or item.get("numero_controle_pncp") or "")
        objeto = escapar(str(item.get("objeto") or "")[:220])
        orgao = escapar(str(item.get("orgao") or item.get("orgao_nome") or "órgão não informado"))
        score = float(item.get("score") or item.get("score_final") or 0.0)

        linhas = [
            "",
            f"<b>{objeto}</b>",
            f"<i>{orgao}</i>",
            f"{_moeda(item.get('valor_estimado'))} · "
            f"{_prazo(item.get('encerramento') or item.get('encerramento_proposta'))} · "
            f"score {score:.2f}",
        ]
        if item.get("justificativa"):
            linhas.append(escapar(str(item["justificativa"])[:300]))
        if url := _url(numero):
            linhas.append(f'<a href="{url}">ver no PNCP</a>')
        partes.append("\n".join(linhas))

    if quantas > mostrar:
        partes.append(f"\n<i>… e mais {quantas - mostrar}.</i>")
    if painel:
        partes.append(f'\n<a href="{escapar(painel)}">decidir no painel</a>')
    else:
        partes.append("\nDecida com <code>licita-radar revisar</code>.")

    return "\n".join(partes)


def mensagem_da_analise(analise: dict[str, Any], *, objeto: str, numero_controle: str = "") -> str:
    """O resumo do edital, com a marca de cada afirmação.

    As não sustentadas vão para o fim, juntas, com o motivo. No terminal
    elas aparecem no meio do assunto a que pertencem, porque lá dá para
    conferir na hora; no celular, o que importa é separar o que se pode
    repetir para outra pessoa do que precisa ser checado antes.
    """
    afirmacoes = [a for a in (analise.get("afirmacoes") or []) if isinstance(a, dict)]
    partes = [f"<b>{escapar(objeto[:200])}</b>"]

    if analise.get("resumo"):
        partes.append(f"\n{escapar(str(analise['resumo']))}")

    confirmadas = [a for a in afirmacoes if a.get("estado") == "sustentada"]
    duvidosas = [a for a in afirmacoes if a.get("estado") != "sustentada"]

    if confirmadas:
        partes.append("")
        for a in confirmadas:
            rotulo = ROTULOS.get(str(a.get("assunto")), str(a.get("assunto")))
            partes.append(f"✓ <b>{escapar(rotulo)}</b> — {escapar(str(a.get('texto') or ''))}")

    if duvidosas:
        partes.append("\n<b>Confira no documento antes de usar:</b>")
        for a in duvidosas:
            marca = MARCAS.get(str(a.get("estado")), "?")
            texto = escapar(str(a.get("texto") or ""))
            motivo = escapar(str(a.get("observacao") or ""))
            partes.append(f"{marca} {texto}\n<i>{motivo}</i>")

    if analise.get("alertas"):
        partes.append("")
        partes.extend(f"⚠️ {escapar(str(x))}" for x in analise["alertas"])

    if url := _url(numero_controle):
        partes.append(f'\n<a href="{url}">edital no PNCP</a>')

    rodape = (
        f"{escapar(str(analise.get('modelo') or 'sem modelo'))} · "
        f"{analise.get('tokens', 0)} tokens · "
        f"{len(confirmadas)}/{len(afirmacoes)} conferidas"
    )
    partes.append(f"\n<i>{rodape}</i>")

    return "\n".join(partes)

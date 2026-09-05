"""A API HTTP que sustenta o painel.

Ela não tem regra de negócio própria: tudo o que decide alguma coisa —
matching, grafo, análise de edital — já existe e é chamado daqui. O papel
desta camada é traduzir aquilo para JSON e cuidar de duas coisas que só
aparecem quando existe uma tela:

**A chave da contratação tem barra.** ``10825373000155-1-000157/2026`` não
cabe num caminho de URL sem virar `%2F`, e `%2F` é decodificado antes do
roteamento — a rota quebra em duas. Por isso a API usa `~` no lugar da
barra (`chave` e `numero_de`), o que mantém a URL legível e sem armadilha.

**Aprovar demora.** A decisão humana dispara o nó mais caro do grafo:
baixar o edital, extrair e resumir leva um minuto ou mais. Segurar a
resposta HTTP até o fim seria um botão que parece travado — o mesmo erro
que a listagem de anexos já ensinou no terminal. A decisão volta na hora,
o trabalho corre em segundo plano e a tela pergunta como ele está.
"""

from licita_radar.api.app import criar_app

__all__ = ["criar_app"]

"""Leitura de editais: extrair o texto, recortar o que importa, resumir.

O funil do matching decide *se* vale olhar. Este pacote decide *o que o
edital diz* — e é o único lugar do projeto onde um erro é caro de verdade:
um resumo que inventa uma exigência de habilitação faz a empresa desistir
de uma licitação que ela venceria, e um que omite faz gastar dias montando
proposta para um edital que ela nunca poderia atender.

Daí a regra que atravessa os três módulos: **nada é afirmado sem o trecho
que sustenta**. O `analista` exige a citação, e `citacoes.confirmar` volta
ao texto original para conferir se ela existe mesmo.
"""

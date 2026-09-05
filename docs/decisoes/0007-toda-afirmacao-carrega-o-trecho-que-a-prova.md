# 0007 — Toda afirmação do resumo carrega o trecho que a prova

**Estado:** aceita · **Data:** 2026-09

## Contexto

O ADR 0006 abriu o analista de editais e fechou com o risco que ele cria:
**um resumo que inventa exigência é pior que resumo nenhum.** A pessoa que
pede o resumo é exatamente a que não vai ler as oitenta páginas — ela não
tem como perceber o erro.

E o erro é assimétrico nos dois sentidos, os dois caros:

- exigência inventada → a empresa desiste de uma licitação que venceria;
- exigência omitida → a empresa monta proposta para um edital que nunca
  poderia atender.

Pedir "cite o edital" no prompt reduz o problema e não o resolve, porque o
modelo consegue inventar a citação junto com a frase. Um resumo com
citações falsas é *mais* convincente que um sem citação nenhuma.

## Decisão

O modelo não escreve um relatório. Ele preenche uma lista de afirmações
curtas, e **cada uma carrega o trecho literal do edital que a sustenta**.
Depois da resposta, cada trecho volta ao texto original e é procurado lá.
A afirmação cuja citação não é encontrada **não é apagada: é marcada**.

O resultado carrega a taxa de confirmação, e a interface distingue as duas
linhas com um símbolo:

```
✓ Pede atestado de capacidade técnica.
  "apresentar atestado de capacidade técnica, fornecido por pessoa jurídica"
? Exige certificação ISO 27001.
  "a licitante deverá comprovar certificação ISO 27001 vigente"
  o trecho citado não existe no edital
```

## Por quê

- Inventar a frase é fácil; inventar a frase **e** uma citação que existe
  no documento, não. A exigência da citação encarece a alucinação.
- Marcar é melhor que apagar. "O modelo afirmou isto e eu não confirmei" é
  informação útil — às vezes é um erro nosso de extração, e a pessoa quer
  ver o que foi dito para decidir.
- A verificação é determinística e barata: nenhuma chamada extra ao modelo,
  nenhum julgamento de um LLM sobre outro LLM.

## Como a comparação funciona

Comparar literalmente não serve: o modelo normaliza espaço, conserta o
hífen de quebra de linha do PDF e corta o meio da frase. Comparar posição a
posição também não — o corte no meio desloca todo o alinhamento e reprova
transcrição honesta.

O que se compara é **cobertura por sequências**: quanto do trecho aparece
no edital em sequências de pelo menos três palavras, lidas em ordem e sem
voltar atrás. Setenta por cento confirma.

- `"multa de 0,5% […] por dia de atraso"` → confirmado, mesmo com o corte
- `"exige-se experiência anterior comprovada"` → recusado: é paráfrase
- `"certificação ISO 27001 vigente"` → recusado: não está lá

Trechos com menos de seis palavras não passam por isso: para eles exige-se
literalidade, porque cinco palavras soltas coincidem por acaso.

## A citação existir não é a citação provar

Isto foi descoberto no primeiro edital real, e é a razão de existirem três
estados e não dois. O modelo devolveu:

> **Multas podem chegar a 15%** do valor da contratação
> citando `"11.1.15. Multa de % ( por cento) sobre o valor estimado…"`

A citação **passou na conferência, corretamente**: aquela frase está no
edital, exatamente assim. O que estava errado era o resto da cadeia.

A extração do PDF, no modo padrão do pypdf, tinha jogado o número para o
fim da linha — `"…pela conduta do20 vinte"` — deixando uma lacuna no lugar
dele. O modelo leu `"Multa de __%"`, precisou de um valor e escreveu 15%:
plausível, redondo, e errado. **O edital dizia 20%.** Extração ruim não
produz resumo incompleto; produz resumo inventado.

Duas mudanças saíram daí:

1. **A extração passa a usar `extraction_mode="layout"`**, que respeita a
   posição do texto na página. O mesmo trecho volta como
   `"Multa de 20% (vinte por cento)"`.
2. **Os números da afirmação são conferidos contra a citação**, um a um.
   Se a frase diz 15% e o trecho não contém 15, ela não é sustentada —
   mesmo com a citação confirmada.

O segundo item é o que sobrevive ao primeiro. Nenhuma extração é perfeita,
e o número é justamente a parte em que alguém age: 15% ou 20% de multa, 3
ou 6 anos de impedimento, 30 ou 60 dias de prazo. Conferir que a evidência
existe sem conferir que ela prova a alegação deixa passar exatamente o
erro que mais custa.

A comparação aceita as duas grafias: `"prazo máximo de até dez dias úteis"`
sustenta `"em até 10 dias úteis"`. Edital escreve por extenso o tempo
todo, e reprovar isso seria trocar alucinação por falso alarme. E o token
numérico carrega a pontuação, senão o `15` de `11.1.15` — a numeração da
cláusula — provaria a multa de 15%.

Daí os três estados:

| | significado |
|---|---|
| ✓ | a citação está no edital e contém os números da frase |
| ≈ | a citação é do edital, mas o número afirmado não está nela |
| ? | não achei essa citação no edital |

## Consequências

- A conferência usa o texto **completo**, não o recorte enviado ao modelo.
  Reprovar uma citação verdadeira porque o nosso próprio corte a deixou de
  fora seria injusto com o modelo e enganoso com quem lê.
- A taxa de confirmação vira coluna no banco (`analise_edital.confiabilidade`):
  dá para ordenar por ela e para perceber que um modelo está piorando. Ela
  conta só o que está em ✓ — é a fração que dá para repetir para outra
  pessoa sem ressalva.
- Um edital digitalizado sem OCR não produz resumo — produz o aviso de que
  é digitalizado. É o comportamento correto: análise de texto vazio seria
  alucinação pura.
- Paráfrase correta é recusada junto com invenção. É o preço, e é o lado
  certo do erro: o rótulo diz "não confirmei", não "está errado".

import type { Afirmacao, Analise } from "../tipos";
import { ORDEM_DOS_ASSUNTOS, ROTULOS } from "../tipos";

/** A marca de cada estado. É o núcleo da tela, não decoração. */
const MARCAS = { sustentada: "✓", numero_sem_apoio: "≈", nao_encontrada: "?" } as const;

function Linha({ afirmacao }: { afirmacao: Afirmacao }) {
  return (
    <div className={`afirmacao ${afirmacao.estado}`}>
      <span className={`veredito ${afirmacao.estado}`} aria-hidden="true">
        {MARCAS[afirmacao.estado]}
      </span>
      <p className="afirmacao-texto">{afirmacao.texto}</p>
      {/* O trecho é transcrição do edital: fonte monoespaçada e nenhum
          realce por cima. Estilizar palavras dentro de uma citação faz
          parecer que ela foi editada, justo onde a promessa é o contrário. */}
      <blockquote className="trecho">{afirmacao.trecho}</blockquote>
      {afirmacao.estado !== "sustentada" && (
        <p className={`observacao ${afirmacao.estado}`}>{afirmacao.observacao}</p>
      )}
    </div>
  );
}

export function PainelDeAnalise({ analise }: { analise: Analise }) {
  const sustentadas = analise.afirmacoes.filter((a) => a.estado === "sustentada").length;
  const total = analise.afirmacoes.length;

  const porAssunto = ORDEM_DOS_ASSUNTOS.map((assunto) => ({
    assunto,
    itens: analise.afirmacoes.filter((a) => a.assunto === assunto),
  })).filter((grupo) => grupo.itens.length > 0);

  return (
    <section>
      <div className="analise-cabecalho">
        <h2>Análise do edital</h2>
        {total > 0 && (
          <span className="confianca">
            {sustentadas}/{total} conferidas
          </span>
        )}
      </div>

      {analise.resumo && <p className="resumo">{analise.resumo}</p>}

      {porAssunto.map(({ assunto, itens }) => (
        <div className="assunto" key={assunto}>
          <h3>{ROTULOS[assunto] ?? assunto}</h3>
          {itens.map((afirmacao, i) => (
            <Linha afirmacao={afirmacao} key={`${assunto}-${i}`} />
          ))}
        </div>
      ))}

      {analise.alertas.map((alerta, i) => (
        <p className="alerta" key={i}>
          {alerta}
        </p>
      ))}

      {total > 0 && (
        <p className="legenda">
          <span>
            <code>✓</code> a citação está no edital e contém os números da frase
          </span>
          <span>
            <code>≈</code> a citação é do edital, mas o número afirmado não está nela
          </span>
          <span>
            <code>?</code> a citação não foi encontrada no edital
          </span>
        </p>
      )}

      <p className="rodape-analise">
        {analise.modelo ?? "sem modelo"} · {analise.tokens} tokens ·{" "}
        {analise.caracteres_lidos.toLocaleString("pt-BR")} caracteres lidos
        {analise.documentos.length > 0 &&
          ` · ${analise.documentos.length} anexo(s): ${analise.documentos
            .map((d) => d.titulo ?? "documento")
            .join(", ")}`}
      </p>
    </section>
  );
}

import { useState } from "react";
import { moeda, prazo } from "../api";
import type { Detalhe } from "../tipos";
import { PainelDeAnalise } from "./Analise";

export function PainelDeDetalhe({
  detalhe,
  analisando,
  aoDecidir,
}: {
  detalhe: Detalhe;
  analisando: boolean;
  aoDecidir: (decisao: "aprovar" | "rejeitar", comentario: string) => void;
}) {
  const [comentario, setComentario] = useState("");
  const { texto: quando } = prazo(detalhe.encerramento);

  return (
    <article className="leitura">
      <div className="leitura-limite">
        <h1 className="objeto">{detalhe.objeto}</h1>

        <div className="ficha">
          <span>{detalhe.orgao ?? "órgão não informado"}</span>
          {detalhe.uf && <span>{detalhe.uf}</span>}
          <span>{moeda(detalhe.valor_estimado)}</span>
          <span>{quando}</span>
          {detalhe.url_pncp && (
            <a href={detalhe.url_pncp} target="_blank" rel="noreferrer">
              ver no PNCP ↗
            </a>
          )}
        </div>

        {detalhe.justificativa && (
          <p className="justificativa">
            {detalhe.justificativa}
            <small>por que esta apareceu · score {detalhe.score.toFixed(2)}</small>
          </p>
        )}

        {detalhe.aguardando_decisao && (
          <div className="decisao">
            <p>
              {analisando
                ? "lendo o edital — isto leva cerca de um minuto"
                : "Vale a pena disputar? Aprovar baixa o edital e o resume."}
            </p>
            {analisando ? (
              <span className="trabalhando">
                <span className="girando" aria-hidden="true" />
                analisando
              </span>
            ) : (
              <>
                <input
                  className="comentario"
                  placeholder="comentário (opcional) — fica gravado com a decisão"
                  value={comentario}
                  onChange={(e) => setComentario(e.target.value)}
                />
                <button className="botao" onClick={() => aoDecidir("rejeitar", comentario)}>
                  Rejeitar
                </button>
                <button
                  className="botao principal"
                  onClick={() => aoDecidir("aprovar", comentario)}
                >
                  Aprovar e ler o edital
                </button>
              </>
            )}
          </div>
        )}

        {detalhe.comentario_humano && (
          <p className="ficha">
            <span>seu comentário: “{detalhe.comentario_humano}”</span>
          </p>
        )}

        {detalhe.analise ? (
          <PainelDeAnalise analise={detalhe.analise} />
        ) : (
          !detalhe.aguardando_decisao && (
            <p className="alerta">
              Esta contratação ainda não foi analisada. Rode <code>licita-radar radar</code> para
              levá-la até a revisão, ou aprove-a aqui quando ela chegar.
            </p>
          )
        )}
      </div>
    </article>
  );
}

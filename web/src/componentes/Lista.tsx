import { moeda, prazo } from "../api";
import type { ItemLista } from "../tipos";

function Etiqueta({ item }: { item: ItemLista }) {
  if (item.aguardando_decisao) return <span className="etiqueta espera">decidir</span>;
  if (item.situacao === "notificada" || item.tem_analise)
    return <span className="etiqueta pronta">analisada</span>;
  if (item.situacao === "rejeitada") return <span className="etiqueta recusada">rejeitada</span>;
  return null;
}

export function Lista({
  itens,
  selecionada,
  aoEscolher,
}: {
  itens: ItemLista[];
  selecionada: string | null;
  aoEscolher: (chave: string) => void;
}) {
  return (
    <nav className="lista" aria-label="Candidatas">
      <div className="lista-titulo">
        {itens.length} candidata{itens.length === 1 ? "" : "s"}
      </div>

      {itens.map((item) => {
        const { texto, dias } = prazo(item.encerramento);
        return (
          <button
            key={item.chave}
            className="linha"
            aria-current={item.chave === selecionada}
            onClick={() => aoEscolher(item.chave)}
          >
            <span className="linha-objeto">{item.objeto}</span>
            <span className="linha-meta">
              <span>{item.orgao ?? "órgão não informado"}</span>
            </span>
            <span className="linha-meta">
              {item.uf && <span>{item.uf}</span>}
              <span>{moeda(item.valor_estimado)}</span>
              {/* prazo curto é a informação que muda o que fazer hoje */}
              <span className={dias !== null && dias <= 3 ? "urgente" : undefined}>{texto}</span>
            </span>
            <span className="score">
              <span className="score-trilho">
                <span
                  className="score-preenchido"
                  style={{ width: `${Math.min(100, item.score * 200)}%` }}
                />
              </span>
              <b>{item.score.toFixed(2)}</b>
            </span>
            <Etiqueta item={item} />
          </button>
        );
      })}
    </nav>
  );
}

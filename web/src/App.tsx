import { useCallback, useEffect, useRef, useState } from "react";
import { ErroDaApi, api } from "./api";
import { PainelDeDetalhe } from "./componentes/Detalhe";
import { Lista } from "./componentes/Lista";
import type { Detalhe, ItemLista, ResumoFunil } from "./tipos";

/** De quanto em quanto tempo perguntar se o edital já foi lido. */
const INTERVALO_DA_ESPERA = 2000;

export default function App() {
  const [resumo, setResumo] = useState<ResumoFunil | null>(null);
  const [itens, setItens] = useState<ItemLista[]>([]);
  const [selecionada, setSelecionada] = useState<string | null>(null);
  const [detalhe, setDetalhe] = useState<Detalhe | null>(null);
  const [analisando, setAnalisando] = useState(false);
  const [falha, setFalha] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(true);

  const relogio = useRef<number | null>(null);

  const carregar = useCallback(async () => {
    try {
      const [novoResumo, novosItens] = await Promise.all([api.resumo(), api.candidatas()]);
      setResumo(novoResumo);
      setItens(novosItens);
      setFalha(null);
      setSelecionada((atual) => atual ?? novosItens[0]?.chave ?? null);
    } catch (erro) {
      setFalha(erro instanceof ErroDaApi ? erro.message : String(erro));
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  useEffect(() => {
    if (!selecionada) return;
    let cancelado = false;

    api
      .detalhe(selecionada)
      .then((d) => {
        if (!cancelado) setDetalhe(d);
      })
      .catch((erro) => {
        if (!cancelado) setFalha(erro instanceof ErroDaApi ? erro.message : String(erro));
      });

    return () => {
      cancelado = true;
    };
  }, [selecionada]);

  // A espera pela análise é ativa: o POST volta na hora e o trabalho corre
  // no servidor, então quem pergunta "já?" é a tela. Sem isso o botão
  // pareceria travado — e um botão que parece travado é clicado de novo.
  const acompanhar = useCallback(
    (chave: string) => {
      setAnalisando(true);
      const parar = () => {
        if (relogio.current) window.clearInterval(relogio.current);
        relogio.current = null;
      };

      relogio.current = window.setInterval(async () => {
        try {
          const estado = await api.estadoDaDecisao(chave);
          if (estado.estado === "analisando") return;

          parar();
          setAnalisando(false);
          if (estado.estado === "erro") {
            setFalha(estado.mensagem ?? "a análise falhou");
          }
          setDetalhe(await api.detalhe(chave));
          void carregar();
        } catch (erro) {
          parar();
          setAnalisando(false);
          setFalha(erro instanceof ErroDaApi ? erro.message : String(erro));
        }
      }, INTERVALO_DA_ESPERA);
    },
    [carregar],
  );

  useEffect(() => () => {
    if (relogio.current) window.clearInterval(relogio.current);
  }, []);

  async function decidir(decisao: "aprovar" | "rejeitar", comentario: string) {
    if (!selecionada) return;
    try {
      const trabalho = await api.decidir(selecionada, decisao, comentario);
      if (trabalho.estado === "analisando") {
        acompanhar(selecionada);
      } else {
        setDetalhe(await api.detalhe(selecionada));
        void carregar();
      }
    } catch (erro) {
      setFalha(erro instanceof ErroDaApi ? erro.message : String(erro));
    }
  }

  return (
    <div className="aplicativo">
      <header className="cabecalho">
        <div className="marca">
          licita<span>·</span>radar
        </div>
        {resumo && (
          <div className="medidores">
            <div className="medidor">
              <b>{resumo.contratacoes.toLocaleString("pt-BR")}</b>
              <small>coletadas</small>
            </div>
            <div className="medidor">
              <b>{resumo.candidatas}</b>
              <small>candidatas</small>
            </div>
            <div className="medidor">
              <b>{resumo.analisadas}</b>
              <small>editais lidos</small>
            </div>
          </div>
        )}
      </header>

      {falha && <p className="falha">{falha}</p>}

      <div className="corpo">
        {itens.length > 0 ? (
          <Lista itens={itens} selecionada={selecionada} aoEscolher={setSelecionada} />
        ) : (
          <nav className="lista">
            <div className="vazio">
              <h2>{carregando ? "carregando…" : "nenhuma candidata ainda"}</h2>
              {!carregando && (
                <p>
                  Rode <code>licita-radar ingest</code>, depois <code>licita-radar radar</code>.
                </p>
              )}
            </div>
          </nav>
        )}

        {detalhe ? (
          <PainelDeDetalhe detalhe={detalhe} analisando={analisando} aoDecidir={decidir} />
        ) : (
          <div className="vazio">
            <h2>Escolha uma contratação</h2>
            <p>O resumo do edital aparece aqui, com o trecho de origem em cada afirmação.</p>
          </div>
        )}
      </div>
    </div>
  );
}

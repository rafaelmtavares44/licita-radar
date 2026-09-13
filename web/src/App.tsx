import { useCallback, useEffect, useRef, useState } from "react";
import { ErroDaApi, api, desdeQuando } from "./api";
import { PainelDeDetalhe } from "./componentes/Detalhe";
import { Lista } from "./componentes/Lista";
import { UFS } from "./tipos";
import type { Atualizacao, Detalhe, Escopo, ItemLista, ResumoFunil } from "./tipos";

/** O que este radar procura, embaixo do nome.
 *
 * Os números do cabeçalho são incompletos sem isto: "6 abertas agora" não
 * diz seis abertas de quê. O recorte mora no `perfil.yaml`, longe de quem
 * está olhando a tela.
 */
function Recorte({ escopo }: { escopo: Escopo | undefined }) {
  if (!escopo) return null;
  const partes = [
    escopo.foco,
    ...(escopo.modalidades ?? []),
    ...(escopo.esferas ?? []),
  ].filter(Boolean);
  if (partes.length === 0) return null;

  return (
    <p className="recorte" title={partes.join(" · ")}>
      {/* O assunto vem primeiro e em destaque: é a pergunta que o radar
          responde. A modalidade é como a compra acontece — detalhe, não
          manchete. */}
      {escopo.foco && <span className="foco">{escopo.foco}</span>}
      <span className="modalidades">{(escopo.modalidades ?? []).join(" · ")}</span>
    </p>
  );
}

/** Onde procurar. Muda a lista e o alvo da próxima coleta.
 *
 * Os 27 estados aparecem sempre. Os que já têm candidata vêm primeiro,
 * com a contagem, porque é neles que há algo para ver agora — mas os
 * outros continuam escolhíveis, senão coletar um estado novo exigiria
 * que ele já tivesse sido coletado.
 */
function SeletorDeUf({
  contagem: bruta,
  atual,
  aoEscolher,
}: {
  contagem: Record<string, number> | undefined;
  atual: string | null;
  aoEscolher: (uf: string | null) => void;
}) {
  // O painel e a API são versionados juntos, mas nem sempre chegam
  // juntos: um `dist` velho contra uma API nova recebe campos que não
  // conhece e falta os que espera. Isso não pode derrubar a tela.
  const contagem = bruta ?? {};
  const comResultado = UFS.filter((uf) => (contagem[uf] ?? 0) > 0);
  const restantes = UFS.filter((uf) => !(contagem[uf] ?? 0));

  return (
    <label className="seletor-uf">
      <span className="rotulo">onde</span>
      <select value={atual ?? ""} onChange={(e) => aoEscolher(e.target.value || null)}>
        <option value="">Brasil inteiro</option>
        {comResultado.length > 0 && (
          <optgroup label="com candidatas abertas">
            {comResultado.map((uf) => (
              <option key={uf} value={uf}>
                {uf} ({contagem[uf]})
              </option>
            ))}
          </optgroup>
        )}
        <optgroup label={comResultado.length > 0 ? "demais estados" : "estados"}>
          {restantes.map((uf) => (
            <option key={uf} value={uf}>
              {uf}
            </option>
          ))}
        </optgroup>
      </select>
    </label>
  );
}

/** De quanto em quanto tempo perguntar se o edital já foi lido. */
const INTERVALO_DA_ESPERA = 2000;

/** O cabeçalho, com a idade do dado em destaque.
 *
 * A coleta não roda sozinha, e sem esta linha uma tela com dado de quatro
 * dias atrás é idêntica a uma tela atualizada agora. O erro que ela evita
 * não é de leitura: é concluir que o PNCP parou de publicar.
 */
function Medidores({ resumo }: { resumo: ResumoFunil }) {
  const { texto, horas } = desdeQuando(resumo.ultima_coleta);
  const velho = horas === null || horas > 24;

  return (
    <div className="medidores">
      <div className="medidor">
        <b>{resumo.contratacoes.toLocaleString("pt-BR")}</b>
        <small>coletadas</small>
      </div>
      <div className="medidor">
        <b>{resumo.candidatas}</b>
        <small>abertas agora</small>
      </div>
      <div className="medidor">
        <b>{resumo.analisadas}</b>
        <small>editais lidos</small>
      </div>
      <div className={`medidor coleta${velho ? " vencida" : ""}`} title="licita-radar atualizar">
        <b>{texto}</b>
        <small>{velho ? "rode `licita-radar atualizar`" : "última coleta"}</small>
      </div>
    </div>
  );
}


function duracao(segundos: number): string {
  if (segundos < 60) return `${segundos}s`;
  const minutos = Math.floor(segundos / 60);
  return `${minutos}min ${segundos % 60}s`;
}

/** O botão que dispara o ciclo, e vira relatório de progresso enquanto roda. */
function BotaoDeAtualizar({
  estado,
  aoClicar,
}: {
  estado: Atualizacao | null;
  aoClicar: () => void;
}) {
  const rodando = estado?.em_andamento ?? false;

  return (
    <button className="atualizar" onClick={aoClicar} disabled={rodando}>
      {rodando ? (
        <>
          <span className="girando" aria-hidden="true" />
          <span>
            {estado?.mensagem}
            {/* O relógio é o que separa "demorando" de "travado". A
                varredura nacional leva minutos, e sem ele a pessoa
                desiste antes de a primeira etapa terminar. */}
            <em>{duracao(estado?.segundos ?? 0)}</em>
          </span>
        </>
      ) : (
        "Atualizar editais"
      )}
    </button>
  );
}


export default function App() {
  const [resumo, setResumo] = useState<ResumoFunil | null>(null);
  const [itens, setItens] = useState<ItemLista[]>([]);
  const [selecionada, setSelecionada] = useState<string | null>(null);
  const [detalhe, setDetalhe] = useState<Detalhe | null>(null);
  const [analisando, setAnalisando] = useState(false);
  const [comEncerradas, setComEncerradas] = useState(false);
  // A UF escolhida vale para as duas coisas: o que a lista mostra e o que
  // a próxima coleta vai buscar. Separar as duas daria uma tela que exibe
  // Goiás e atualiza o Brasil.
  const [uf, setUf] = useState<string | null>(null);
  const [atualizacao, setAtualizacao] = useState<Atualizacao | null>(null);
  const [falha, setFalha] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(true);

  const relogio = useRef<number | null>(null);

  const carregar = useCallback(async () => {
    try {
      const [novoResumo, novosItens] = await Promise.all([
        api.resumo(),
        api.candidatas(40, comEncerradas, uf),
      ]);
      setResumo(novoResumo);
      setItens(novosItens);
      setFalha(null);
      setSelecionada((atual) =>
        novosItens.some((i) => i.chave === atual) ? atual : (novosItens[0]?.chave ?? null),
      );
    } catch (erro) {
      setFalha(erro instanceof ErroDaApi ? erro.message : String(erro));
    } finally {
      setCarregando(false);
    }
  }, [comEncerradas, uf]);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  useEffect(() => {
    // Trocar de estado troca a lista inteira. Sem limpar aqui, o painel
    // da direita continuava exibindo a contratação do estado anterior ao
    // lado de uma lista vazia — duas telas contando histórias
    // diferentes, e a da direita parecendo a mais confiável.
    if (!selecionada) {
      setDetalhe(null);
      return;
    }
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

  // A atualização leva minutos e roda no servidor: quem pergunta "já?" é a
  // tela. O intervalo é maior que o da análise porque as etapas são longas
  // — perguntar de dois em dois segundos só geraria tráfego.
  const acompanharAtualizacao = useCallback(() => {
    const relogio = window.setInterval(async () => {
      try {
        const estado = await api.estadoDaAtualizacao();
        setAtualizacao(estado);
        if (!estado.em_andamento) {
          window.clearInterval(relogio);
          void carregar();
        }
      } catch {
        // Parar de perguntar em silêncio deixa o botão girando para
        // sempre — e um botão que gira é uma promessa de que algo está
        // acontecendo. Se o servidor caiu, a tela precisa dizer isso.
        window.clearInterval(relogio);
        setAtualizacao(null);
        setFalha("perdi contato com o servidor. Recarregue a página para ver como a coleta terminou.");
      }
    }, 3000);
  }, [carregar]);

  async function atualizarAgora() {
    try {
      setAtualizacao(await api.atualizar(uf));
      acompanharAtualizacao();
    } catch (erro) {
      setFalha(erro instanceof ErroDaApi ? erro.message : String(erro));
    }
  }

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
        <div className="identidade">
          <div className="marca">
            licita<span>·</span>radar
          </div>
          {resumo && <Recorte escopo={resumo.escopo} />}
        </div>
        {resumo && <Medidores resumo={resumo} />}
        {resumo && (
          <SeletorDeUf contagem={resumo.candidatas_por_uf} atual={uf} aoEscolher={setUf} />
        )}
        <BotaoDeAtualizar estado={atualizacao} aoClicar={atualizarAgora} />
      </header>

      {atualizacao?.etapa === "pronto" && (
        <p className="aviso-topo">
          {atualizacao.novas} novas de {atualizacao.coletadas} coletadas ·{" "}
          {atualizacao.aguardando} esperando a sua decisão
        </p>
      )}

      {/* Uma coleta que perdeu uma modalidade continua sendo uma coleta —
          mas dizer só "atualizado" transforma o buraco em silêncio, e
          quem olha conclui que o PNCP não tinha nada. */}
      {(atualizacao?.avisos?.length ?? 0) > 0 && (
        <p className="aviso-topo parcial">
          coleta incompleta · {atualizacao?.avisos.join(" · ")}
        </p>
      )}

      {falha && <p className="falha">{falha}</p>}

      <div className="corpo">
        {itens.length > 0 ? (
          <Lista
            itens={itens}
            selecionada={selecionada}
            aoEscolher={setSelecionada}
            encerradasEscondidas={resumo?.encerradas_escondidas ?? 0}
            mostrandoEncerradas={comEncerradas}
            aoAlternarEncerradas={() => setComEncerradas((v) => !v)}
          />
        ) : (
          <nav className="lista">
            <div className="vazio">
              <h2>{carregando ? "carregando…" : "nenhuma candidata ainda"}</h2>
              {!carregando && (
                <p>
                  Rode <code>licita-radar atualizar</code>.
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

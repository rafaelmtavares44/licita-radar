import type { Detalhe, EstadoDoTrabalho, ItemLista, ResumoFunil } from "./tipos";

/** Erro que já sabe se explicar — a tela mostra a mensagem, não o objeto. */
export class ErroDaApi extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
  }
}

async function pedir<T>(caminho: string, opcoes?: RequestInit): Promise<T> {
  let resposta: Response;
  try {
    resposta = await fetch(caminho, {
      headers: { "Content-Type": "application/json" },
      ...opcoes,
    });
  } catch {
    // A causa esmagadoramente mais comum, e a que a mensagem genérica do
    // navegador ("Failed to fetch") esconde.
    throw new ErroDaApi("o servidor não respondeu — o `licita-radar servir` está rodando?");
  }

  if (!resposta.ok) {
    const corpo = await resposta.json().catch(() => null);
    throw new ErroDaApi(corpo?.detail ?? `o servidor respondeu ${resposta.status}`, resposta.status);
  }
  return (await resposta.json()) as T;
}

export const api = {
  resumo: () => pedir<ResumoFunil>("/api/resumo"),

  candidatas: (limite = 40, encerradas = false) =>
    pedir<ItemLista[]>(`/api/candidatas?limite=${limite}&encerradas=${encerradas}`),

  detalhe: (chave: string) => pedir<Detalhe>(`/api/candidatas/${chave}`),

  decidir: (chave: string, decisao: "aprovar" | "rejeitar", comentario?: string) =>
    pedir<EstadoDoTrabalho>(`/api/candidatas/${chave}/decisao`, {
      method: "POST",
      body: JSON.stringify({ decisao, comentario: comentario || null }),
    }),

  estadoDaDecisao: (chave: string) =>
    pedir<EstadoDoTrabalho>(`/api/candidatas/${chave}/decisao`),
};

export const moeda = (valor: number | null) =>
  valor == null
    ? "—"
    : valor.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

/** Há quanto tempo isto aconteceu, em português de gente. */
export function desdeQuando(iso: string | null): { texto: string; horas: number | null } {
  if (!iso) return { texto: "nunca coletado", horas: null };
  const quando = new Date(iso);
  if (Number.isNaN(quando.getTime())) return { texto: iso, horas: null };

  const horas = (Date.now() - quando.getTime()) / 3_600_000;
  if (horas < 1) return { texto: "agora há pouco", horas };
  if (horas < 24) return { texto: `há ${Math.floor(horas)}h`, horas };

  const dias = Math.floor(horas / 24);
  return { texto: dias === 1 ? "ontem" : `há ${dias} dias`, horas };
}

export function prazo(iso: string | null): { texto: string; dias: number | null } {
  if (!iso) return { texto: "sem prazo informado", dias: null };
  const fim = new Date(iso);
  if (Number.isNaN(fim.getTime())) return { texto: iso, dias: null };

  const hoje = new Date();
  const dias = Math.ceil((fim.getTime() - hoje.getTime()) / 86_400_000);
  const data = fim.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" });

  if (dias < 0) return { texto: `encerrou em ${data}`, dias };
  if (dias === 0) return { texto: "encerra hoje", dias };
  if (dias === 1) return { texto: "encerra amanhã", dias };
  return { texto: `${dias} dias · ${data}`, dias };
}

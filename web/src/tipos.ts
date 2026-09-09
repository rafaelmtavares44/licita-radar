/** O contrato com a API. Espelha `licita_radar/api/esquemas.py`. */

export type EstadoDaAfirmacao = "sustentada" | "numero_sem_apoio" | "nao_encontrada";

export interface Afirmacao {
  assunto: string;
  texto: string;
  trecho: string;
  estado: EstadoDaAfirmacao;
  observacao: string;
  numeros_sem_apoio: string[];
}

export interface Analise {
  resumo: string;
  afirmacoes: Afirmacao[];
  alertas: string[];
  confiabilidade: number;
  modelo: string | null;
  tokens: number;
  caracteres_lidos: number;
  documentos: { titulo?: string; caminho?: string; bytes?: number }[];
}

export interface ItemLista {
  chave: string;
  numero_controle: string;
  objeto: string;
  orgao: string | null;
  uf: string | null;
  valor_estimado: number | null;
  encerramento: string | null;
  url_pncp: string | null;
  score: number;
  score_lexical: number;
  score_semantico: number;
  palavras_encontradas: string[];
  justificativa: string | null;
  situacao: string;
  aguardando_decisao: boolean;
  tem_analise: boolean;
}

export interface Detalhe extends ItemLista {
  analise: Analise | null;
  comentario_humano: string | null;
  trilha: string[];
  tokens_gastos: number;
}

export interface ResumoFunil {
  contratacoes: number;
  avaliadas: number;
  candidatas: number;
  analisadas: number;
  por_veredito: Record<string, number>;
  ultima_coleta: string | null;
  novas_na_ultima: number;
  encerradas_escondidas: number;
}

export interface Atualizacao {
  etapa: "parado" | "coletando" | "pontuando" | "avaliando" | "pronto" | "erro";
  mensagem: string;
  em_andamento: boolean;
  coletadas: number;
  novas: number;
  avaliadas: number;
  candidatas: number;
  aguardando: number;
  erro: string | null;
}

export interface EstadoDoTrabalho {
  chave: string;
  estado: "analisando" | "pronta" | "erro" | "desconhecido";
  mensagem: string | null;
}

/** Os assuntos na ordem em que uma pessoa os lê, com o rótulo da tela. */
export const ROTULOS: Record<string, string> = {
  objeto: "O que está sendo comprado",
  habilitacao: "O que exigem para participar",
  garantia: "Garantias exigidas",
  prazos: "Prazos",
  pagamento: "Pagamento",
  penalidades: "Multas e sanções",
  riscos: "Pontos de atenção",
};

export const ORDEM_DOS_ASSUNTOS = Object.keys(ROTULOS);

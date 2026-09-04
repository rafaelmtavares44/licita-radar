-- 002 — embeddings e avaliações (M2, o funil de matching).
--
-- A dimensão 384 corresponde ao modelo padrão
-- (paraphrase-multilingual-MiniLM-L12-v2). Trocar para o
-- multilingual-e5-large exige mudar aqui para vector(1024) e recodificar
-- tudo: o índice não converte dimensão sozinho.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS contratacao_embedding (
    numero_controle_pncp text PRIMARY KEY
        REFERENCES contratacao (numero_controle_pncp) ON DELETE CASCADE,
    modelo      text         NOT NULL,
    texto       text         NOT NULL,   -- o que de fato foi codificado, já sem a casca burocrática
    embedding   vector(384)  NOT NULL,
    criado_em   timestamptz  NOT NULL DEFAULT now()
);

COMMENT ON COLUMN contratacao_embedding.texto IS
    'Objeto após limpar_objeto(): guardar isto é o que permite auditar por que uma licitação pontuou como pontuou.';

CREATE INDEX IF NOT EXISTS idx_embedding_hnsw
    ON contratacao_embedding USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS avaliacao (
    id                   bigserial PRIMARY KEY,
    numero_controle_pncp text NOT NULL
        REFERENCES contratacao (numero_controle_pncp) ON DELETE CASCADE,
    perfil_id            text        NOT NULL,
    score_lexical        real,
    score_semantico      real,
    score_final          real,
    veredito             text        NOT NULL,
    palavras_encontradas text[]      NOT NULL DEFAULT '{}',
    motivo               text,
    justificativa        text,                       -- preenchida pelo LLM no M3
    modelo               text,
    tokens_gastos        integer     NOT NULL DEFAULT 0,
    criado_em            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (numero_controle_pncp, perfil_id)
);

-- O ranking do dia: candidatas primeiro, melhor score na frente.
CREATE INDEX IF NOT EXISTS idx_avaliacao_ranking
    ON avaliacao (perfil_id, veredito, score_final DESC);

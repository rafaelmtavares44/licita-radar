-- 004 — a análise do edital: o resumo e a evidência de cada afirmação.
--
-- O checkpoint do LangGraph já guarda o resultado, mas ele é um blob de
-- retomada de execução, não uma tabela de consulta: não dá para perguntar
-- "quais editais exigem atestado de acervo técnico?" nem para o painel web
-- listar as análises sem desserializar thread por thread.
--
-- As afirmações ficam em JSONB, e não em tabela filha, por uma razão
-- prática: elas são sempre lidas junto com a análise, nunca isoladas, e o
-- formato ainda vai mudar enquanto o resumo evolui. JSONB dá o índice GIN
-- para busca quando for preciso, sem migração a cada campo novo.

CREATE TABLE IF NOT EXISTS analise_edital (
    numero_controle_pncp text PRIMARY KEY
        REFERENCES contratacao (numero_controle_pncp) ON DELETE CASCADE,
    resumo            text,
    afirmacoes        jsonb   NOT NULL DEFAULT '[]'::jsonb,
    documentos        jsonb   NOT NULL DEFAULT '[]'::jsonb,
    alertas           jsonb   NOT NULL DEFAULT '[]'::jsonb,

    -- A fração de citações que foram encontradas no edital original.
    -- É a medida de quanto se pode confiar neste resumo, e por isso é
    -- coluna de verdade: dá para filtrar e ordenar por ela.
    confiabilidade    real    NOT NULL DEFAULT 0,

    modelo            text,
    tokens            integer NOT NULL DEFAULT 0,
    caracteres_lidos  integer NOT NULL DEFAULT 0,
    criado_em         timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN analise_edital.confiabilidade IS
    'fração das citações do modelo que foram localizadas no texto do edital';

CREATE INDEX IF NOT EXISTS idx_analise_confiabilidade
    ON analise_edital (confiabilidade DESC);

-- Busca por conteúdo das afirmações: "quais editais falam em acervo?"
CREATE INDEX IF NOT EXISTS idx_analise_afirmacoes
    ON analise_edital USING gin (afirmacoes jsonb_path_ops);

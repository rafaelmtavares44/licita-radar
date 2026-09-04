-- 001 — a tabela de contratações e o controle de execuções de ingestão.
--
-- Nota de escopo: a extensão `vector` e a tabela de embeddings entram na
-- migração 002, junto com o matching semântico (M2). Manter o M1 livre de
-- pgvector significa que qualquer Postgres 14+ roda a ingestão sem
-- instalar extensão nenhuma — menos atrito para quem clona o repositório.

CREATE TABLE IF NOT EXISTS contratacao (
    numero_controle_pncp   text PRIMARY KEY,
    modalidade_codigo      smallint    NOT NULL,
    objeto                 text        NOT NULL,
    orgao_cnpj             text,
    orgao_nome             text,
    uf                     char(2),
    municipio              text,
    valor_estimado         numeric(16, 2),
    data_publicacao        date,
    abertura_proposta      timestamptz,
    encerramento_proposta  timestamptz,
    payload                jsonb       NOT NULL,
    ingerido_em            timestamptz NOT NULL DEFAULT now(),
    atualizado_em          timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN contratacao.payload IS
    'Resposta crua da API do PNCP, preservada inteira: seguro barato contra campo descartado cedo demais.';

-- Os alertas do dia são ordenados por quem encerra primeiro.
CREATE INDEX IF NOT EXISTS idx_contratacao_encerramento
    ON contratacao (encerramento_proposta)
    WHERE encerramento_proposta IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_contratacao_uf_modalidade
    ON contratacao (uf, modalidade_codigo);

-- Histórico das execuções: quantas vieram, quantas eram novas, quanto demorou.
-- Sem isto, "a ingestão está funcionando?" vira uma pergunta sem resposta.
CREATE TABLE IF NOT EXISTS execucao_ingestao (
    id            bigserial PRIMARY KEY,
    iniciada_em   timestamptz NOT NULL DEFAULT now(),
    concluida_em  timestamptz,
    uf            char(2),
    modalidades   smallint[]  NOT NULL,
    janela_inicio date,
    janela_fim    date,
    total_vistas  integer     NOT NULL DEFAULT 0,
    total_novas   integer     NOT NULL DEFAULT 0,
    erro          text
);

-- 003 — a esfera do órgão, para quem quer só o governo federal.
--
-- O dado sempre esteve no payload (orgaoEntidade.esferaId); virou coluna
-- porque filtrar por ela é caso de uso comum: uma empresa pequena costuma
-- disputar municipal, e uma maior mira o federal.

ALTER TABLE contratacao ADD COLUMN IF NOT EXISTS esfera char(1);

COMMENT ON COLUMN contratacao.esfera IS 'F federal, E estadual, M municipal, D distrital';

CREATE INDEX IF NOT EXISTS idx_contratacao_esfera ON contratacao (esfera)
    WHERE esfera IS NOT NULL;

-- preenche o que já foi ingerido, direto do payload
UPDATE contratacao
   SET esfera = payload -> 'orgaoEntidade' ->> 'esferaId'
 WHERE esfera IS NULL
   AND payload -> 'orgaoEntidade' ->> 'esferaId' IS NOT NULL;

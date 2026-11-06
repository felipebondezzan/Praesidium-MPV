DO $$
DECLARE
    empresa_id_type text;
BEGIN
    SELECT format_type(attribute.atttypid, attribute.atttypmod)
    INTO empresa_id_type
    FROM pg_attribute AS attribute
    JOIN pg_class AS relation ON relation.oid = attribute.attrelid
    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND relation.relname = 'empresas_monitoradas'
      AND attribute.attname = 'id'
      AND attribute.attnum > 0
      AND NOT attribute.attisdropped;

    IF empresa_id_type IS NULL THEN
        RAISE EXCEPTION 'Não foi possível determinar o tipo de empresas_monitoradas.id';
    END IF;

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS public.split_payment_events (
            empresa_id %s NOT NULL
                REFERENCES public.empresas_monitoradas(id) ON DELETE CASCADE,
            event_id text NOT NULL,
            transaction_id text NOT NULL,
            event_type text NOT NULL,
            gross_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (gross_amount >= 0),
            retained_amount numeric(18,2) NOT NULL CHECK (retained_amount > 0),
            currency char(3) NOT NULL DEFAULT ''BRL'',
            occurred_at timestamptz NOT NULL,
            payload_sha256 char(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT split_payment_events_pkey PRIMARY KEY (empresa_id, event_id)
        )',
        empresa_id_type
    );
END
$$;

DO $$
DECLARE
    empresa_id_type text;
BEGIN
    SELECT format_type(attribute.atttypid, attribute.atttypmod)
    INTO empresa_id_type
    FROM pg_attribute AS attribute
    JOIN pg_class AS relation ON relation.oid = attribute.attrelid
    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'public'
      AND relation.relname = 'empresas_monitoradas'
      AND attribute.attname = 'id'
      AND attribute.attnum > 0
      AND NOT attribute.attisdropped;

    IF empresa_id_type IS NULL THEN
        RAISE EXCEPTION 'Não foi possível determinar o tipo de empresas_monitoradas.id';
    END IF;

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS public.empresa_cnpjs_autorizados (
            empresa_id %s NOT NULL
                REFERENCES public.empresas_monitoradas(id) ON DELETE CASCADE,
            cnpj text NOT NULL CHECK (cnpj ~ ''^[0-9]{14}$''),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT empresa_cnpjs_autorizados_pkey PRIMARY KEY (empresa_id, cnpj),
            CONSTRAINT empresa_cnpjs_autorizados_cnpj_key UNIQUE (cnpj)
        )',
        empresa_id_type
    );
END
$$;

INSERT INTO public.empresa_cnpjs_autorizados (empresa_id, cnpj)
SELECT DISTINCT ON (normalized.cnpj) normalized.id, normalized.cnpj
FROM (
    SELECT
        id,
        regexp_replace(cnpj::text, '[^0-9]', '', 'g') AS cnpj
    FROM public.empresas_monitoradas
) AS normalized
WHERE length(normalized.cnpj) = 14
ORDER BY normalized.cnpj, normalized.id
ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION public.registrar_cnpj_principal_autorizado()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    cnpj_anterior text;
    cnpj_novo text;
BEGIN
    cnpj_novo := regexp_replace(COALESCE(NEW.cnpj::text, ''), '[^0-9]', '', 'g');
    IF TG_OP = 'UPDATE' THEN
        cnpj_anterior := regexp_replace(COALESCE(OLD.cnpj::text, ''), '[^0-9]', '', 'g');
        IF length(cnpj_anterior) = 14 AND cnpj_anterior <> cnpj_novo THEN
            DELETE FROM public.empresa_cnpjs_autorizados
            WHERE empresa_id = OLD.id
              AND cnpj = cnpj_anterior;
        END IF;
    END IF;
    IF length(cnpj_novo) = 14 THEN
        INSERT INTO public.empresa_cnpjs_autorizados (empresa_id, cnpj)
        VALUES (NEW.id, cnpj_novo)
        ON CONFLICT (empresa_id, cnpj) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS empresas_monitoradas_cnpj_autorizado
    ON public.empresas_monitoradas;
CREATE TRIGGER empresas_monitoradas_cnpj_autorizado
AFTER INSERT OR UPDATE OF cnpj ON public.empresas_monitoradas
FOR EACH ROW EXECUTE FUNCTION public.registrar_cnpj_principal_autorizado();

ALTER TABLE public.conciliacao_devolucoes
    ADD COLUMN IF NOT EXISTS data_devolucao timestamptz;

CREATE INDEX IF NOT EXISTS split_payment_events_empresa_occurred_idx
    ON public.split_payment_events (empresa_id, occurred_at DESC);

ALTER TABLE public.split_payment_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.split_payment_events FROM anon, authenticated;
GRANT ALL ON TABLE public.split_payment_events TO service_role;
ALTER TABLE public.empresa_cnpjs_autorizados ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.empresa_cnpjs_autorizados FROM anon, authenticated;
GRANT ALL ON TABLE public.empresa_cnpjs_autorizados TO service_role;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.conciliacao_devolucoes
        WHERE chave_nota_devolucao IS NOT NULL
          AND COALESCE(chave_nota_devolucao, '') <> ''
        GROUP BY empresa_id, chave_nota_devolucao
        HAVING count(*) > 1
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS conciliacao_devolucoes_empresa_chave_uq
            ON public.conciliacao_devolucoes (empresa_id, chave_nota_devolucao)
            WHERE chave_nota_devolucao IS NOT NULL
              AND chave_nota_devolucao <> '';
    ELSE
        RAISE NOTICE 'Índice de idempotência de devoluções não criado: existem chaves duplicadas legadas.';
    END IF;
END
$$;

ALTER TABLE public.itens_nota_fiscal
    ADD COLUMN IF NOT EXISTS item_uid text,
    ADD COLUMN IF NOT EXISTS nitem integer;

UPDATE public.itens_nota_fiscal
SET item_uid = 'legacy:' || id::text
WHERE item_uid IS NULL;

ALTER TABLE public.itens_nota_fiscal
    ALTER COLUMN item_uid SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS itens_nota_fiscal_item_uid_uq
    ON public.itens_nota_fiscal (item_uid);

CREATE INDEX IF NOT EXISTS itens_nota_fiscal_empresa_nota_idx
    ON public.itens_nota_fiscal (empresa_id, nota_id);

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
        'CREATE TABLE IF NOT EXISTS public.praesidium_empresa_mirror (
            empresa_id %s NOT NULL
                REFERENCES public.empresas_monitoradas(id) ON DELETE CASCADE,
            record_type text NOT NULL
                CHECK (record_type IN (''item'', ''devolucao'', ''split_payment'')),
            record_key text NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT praesidium_empresa_mirror_pkey
                PRIMARY KEY (empresa_id, record_type, record_key)
        )',
        empresa_id_type
    );
END
$$;

CREATE INDEX IF NOT EXISTS praesidium_empresa_mirror_type_idx
    ON public.praesidium_empresa_mirror (empresa_id, record_type);

ALTER TABLE public.praesidium_empresa_mirror ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.praesidium_empresa_mirror FROM anon, authenticated;
GRANT ALL ON TABLE public.praesidium_empresa_mirror TO service_role;

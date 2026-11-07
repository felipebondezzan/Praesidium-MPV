ALTER TABLE public.notas_fiscais_recebidas
    ADD COLUMN IF NOT EXISTS xml_reconstruido text,
    ADD COLUMN IF NOT EXISTS numero text,
    ADD COLUMN IF NOT EXISTS serie text;

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
        'CREATE TABLE IF NOT EXISTS public.praesidium_ledger_fiscal (
            empresa_id %s NOT NULL
                REFERENCES public.empresas_monitoradas(id) ON DELETE CASCADE,
            item_uid text NOT NULL,
            payload jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT praesidium_ledger_fiscal_pkey PRIMARY KEY (empresa_id, item_uid)
        )',
        empresa_id_type
    );
END
$$;

CREATE INDEX IF NOT EXISTS praesidium_ledger_fiscal_empresa_idx
    ON public.praesidium_ledger_fiscal (empresa_id);

ALTER TABLE public.praesidium_ledger_fiscal DISABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.praesidium_ledger_fiscal FROM anon, authenticated;
GRANT ALL ON TABLE public.praesidium_ledger_fiscal TO service_role;

CREATE OR REPLACE FUNCTION public.decidir_itens_quarentena_lote(
    p_empresa_id text,
    p_decisoes jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
    v_decisao record;
    v_item record;
    v_atualizados integer := 0;
BEGIN
    IF p_empresa_id IS NULL OR btrim(p_empresa_id) = ''
       OR COALESCE(jsonb_typeof(p_decisoes), '') <> 'array'
       OR COALESCE(jsonb_array_length(p_decisoes), 0) = 0 THEN
        RAISE EXCEPTION 'Empresa ou decisões inválidas';
    END IF;

    FOR v_decisao IN
        SELECT value AS decisao
          FROM jsonb_array_elements(p_decisoes)
    LOOP
        IF COALESCE(v_decisao.decisao ->> 'item_uid', '') = ''
           OR COALESCE(v_decisao.decisao ->> 'classificacao_item', '')
                NOT IN ('Aprovado Manualmente Contador', 'Rejeitado pelo Contador')
           OR COALESCE(v_decisao.decisao ->> 'status_credito', '')
                NOT IN ('Validado', 'Rejeitado') THEN
            RAISE EXCEPTION 'Decisão de auditoria inválida';
        END IF;

        UPDATE public.itens_nota_fiscal AS item
           SET classificacao_item = v_decisao.decisao ->> 'classificacao_item',
               status_credito = v_decisao.decisao ->> 'status_credito',
               valor_credito_item = (v_decisao.decisao ->> 'valor_credito_item')::numeric,
               justificativa_motor = v_decisao.decisao ->> 'justificativa_motor'
         WHERE item.empresa_id::text = p_empresa_id
           AND item.item_uid = v_decisao.decisao ->> 'item_uid'
           AND item.classificacao_item = 'Quarentena Uso/Consumo'
        RETURNING item.* INTO v_item;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Item pendente não encontrado: %',
                v_decisao.decisao ->> 'item_uid';
        END IF;

        INSERT INTO public.praesidium_ledger_fiscal (
            empresa_id,
            item_uid,
            payload,
            updated_at
        )
        VALUES (
            v_item.empresa_id,
            v_item.item_uid,
            to_jsonb(v_item),
            now()
        )
        ON CONFLICT (empresa_id, item_uid)
        DO UPDATE SET
            payload = EXCLUDED.payload,
            updated_at = EXCLUDED.updated_at;

        INSERT INTO public.praesidium_empresa_mirror (
            empresa_id,
            record_type,
            record_key,
            payload,
            updated_at
        )
        VALUES (
            v_item.empresa_id,
            'item',
            v_item.item_uid,
            to_jsonb(v_item),
            now()
        )
        ON CONFLICT (empresa_id, record_type, record_key)
        DO UPDATE SET
            payload = EXCLUDED.payload,
            updated_at = EXCLUDED.updated_at;

        v_atualizados := v_atualizados + 1;
    END LOOP;

    RETURN jsonb_build_object(
        'empresa_id', p_empresa_id,
        'updated', v_atualizados
    );
END;
$$;

REVOKE ALL ON FUNCTION public.decidir_itens_quarentena_lote(text, jsonb)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.decidir_itens_quarentena_lote(text, jsonb)
    TO service_role;

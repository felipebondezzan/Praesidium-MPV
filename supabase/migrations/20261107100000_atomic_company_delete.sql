CREATE OR REPLACE FUNCTION public.excluir_cliente_cascade(p_empresa_id text)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
    v_empresa_id text;
    v_tabela text;
    v_excluidos bigint;
    v_contagens jsonb := '{}'::jsonb;
BEGIN
    IF p_empresa_id IS NULL OR btrim(p_empresa_id) = '' THEN
        RAISE EXCEPTION 'ID de cliente inválido';
    END IF;

    SELECT id::text
      INTO v_empresa_id
      FROM public.empresas_monitoradas
     WHERE id::text = p_empresa_id
     FOR UPDATE;

    IF v_empresa_id IS NULL THEN
        RAISE EXCEPTION 'Cliente não encontrado';
    END IF;

    IF to_regclass('public.itens_nota_fiscal') IS NOT NULL
       AND EXISTS (
           SELECT 1
             FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'itens_nota_fiscal'
              AND column_name = 'empresa_id'
       ) THEN
        EXECUTE
            'DELETE FROM public.itens_nota_fiscal WHERE empresa_id::text = $1'
            USING p_empresa_id;
        GET DIAGNOSTICS v_excluidos = ROW_COUNT;
        v_contagens := v_contagens
            || jsonb_build_object('itens_nota_fiscal', v_excluidos);
    END IF;

    IF to_regclass('public.itens_nota_fiscal') IS NOT NULL
       AND to_regclass('public.notas_fiscais_recebidas') IS NOT NULL
       AND EXISTS (
           SELECT 1
             FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'itens_nota_fiscal'
              AND column_name = 'nota_id'
       )
       AND EXISTS (
           SELECT 1
             FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'notas_fiscais_recebidas'
              AND column_name = 'empresa_id'
       ) THEN
        EXECUTE
            'DELETE FROM public.itens_nota_fiscal AS item
              USING public.notas_fiscais_recebidas AS nota
              WHERE item.nota_id::text = nota.id::text
                AND nota.empresa_id::text = $1'
            USING p_empresa_id;
        GET DIAGNOSTICS v_excluidos = ROW_COUNT;
        v_contagens := jsonb_set(
            v_contagens,
            '{itens_nota_fiscal}',
            to_jsonb(
                COALESCE((v_contagens ->> 'itens_nota_fiscal')::bigint, 0)
                + v_excluidos
            ),
            true
        );
    END IF;

    FOREACH v_tabela IN ARRAY ARRAY[
        'praesidium_ledger_fiscal',
        'conciliacao_devolucoes',
        'praesidium_empresa_mirror',
        'split_payment_events',
        'empresa_cnpjs_autorizados'
    ] LOOP
        IF to_regclass(format('public.%I', v_tabela)) IS NOT NULL
           AND EXISTS (
               SELECT 1
                 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = v_tabela
                  AND column_name = 'empresa_id'
           ) THEN
            EXECUTE format(
                'DELETE FROM public.%I WHERE empresa_id::text = $1',
                v_tabela
            )
            USING p_empresa_id;
            GET DIAGNOSTICS v_excluidos = ROW_COUNT;
            v_contagens := v_contagens
                || jsonb_build_object(v_tabela, v_excluidos);
        END IF;
    END LOOP;

    IF to_regclass('public.notas_fiscais_recebidas') IS NOT NULL
       AND EXISTS (
           SELECT 1
             FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'notas_fiscais_recebidas'
              AND column_name = 'empresa_id'
       ) THEN
        EXECUTE
            'DELETE FROM public.notas_fiscais_recebidas WHERE empresa_id::text = $1'
            USING p_empresa_id;
        GET DIAGNOSTICS v_excluidos = ROW_COUNT;
        v_contagens := v_contagens
            || jsonb_build_object('notas_fiscais_recebidas', v_excluidos);
    END IF;

    DELETE FROM public.empresas_monitoradas
     WHERE id::text = p_empresa_id;
    GET DIAGNOSTICS v_excluidos = ROW_COUNT;

    IF v_excluidos <> 1 THEN
        RAISE EXCEPTION 'A exclusão do cliente não foi confirmada';
    END IF;

    RETURN jsonb_build_object(
        'deleted', true,
        'empresa_id', v_empresa_id,
        'deleted_records', v_contagens
    );
END;
$$;

REVOKE ALL ON FUNCTION public.excluir_cliente_cascade(text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.excluir_cliente_cascade(text)
    TO service_role;

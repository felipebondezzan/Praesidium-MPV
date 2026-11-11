from types import SimpleNamespace

import xml_engine


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeRpc:
    def __init__(self, name, params, response):
        self.name = name
        self.params = params
        self.response = response

    def execute(self):
        return FakeResponse(self.response)


class FakeSupabase:
    def __init__(self, response):
        self.response = response
        self.rpc_call = None
        self.delete_order = []
        self.delete_filters = {}

    def rpc(self, name, params):
        self.rpc_call = (name, params)
        return FakeRpc(name, params, self.response)

    def table(self, name):
        return FakeDeleteQuery(self, name)


class FakeDeleteQuery:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.filters = []

    def delete(self):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def neq(self, column, value):
        self.filters.append(("neq", column, value))
        return self

    def select(self, _):
        return self

    def execute(self):
        self.database.delete_order.append(self.table)
        self.database.delete_filters[self.table] = list(self.filters)
        data = [{"id": company_id}] if self.table == "empresas_monitoradas" else []
        return FakeResponse(data)


class FakeDirectSupabase:
    def __init__(self):
        self.delete_order = []
        self.delete_filters = {}

    def rpc(self, *args, **kwargs):  # type: ignore[unused-ignore]
        raise RuntimeError("RPC indisponível")

    def table(self, name):
        return FakeDeleteQuery(self, name)


company_id = "88"
supabase = FakeSupabase({
    "deleted": True,
    "empresa_id": company_id,
    "deleted_records": {"itens_nota_fiscal": 100},
})
resultado = xml_engine.excluir_empresa_supabase(supabase, company_id)
assert resultado["deleted"] is True
assert supabase.rpc_call == (
    "excluir_cliente_cascade",
    {"p_empresa_id": company_id},
)

supabase_rpc_fallback = FakeSupabase({
    "deleted": False,
    "empresa_id": company_id,
})
resultado_rpc_fallback = xml_engine.excluir_empresa_supabase(
    supabase_rpc_fallback,
    company_id,
)
assert resultado_rpc_fallback["fallback"] is True
assert supabase_rpc_fallback.delete_order[-1] == "empresas_monitoradas"

supabase_direto = FakeDirectSupabase()
resultado_direto = xml_engine.excluir_empresa_supabase(
    supabase_direto,
    company_id,
)
assert resultado_direto["fallback"] is True
assert supabase_direto.delete_order == [
    "itens_nota_fiscal",
    "conciliacao_devolucoes",
    "notas_fiscais_recebidas",
    "split_payment_events",
    "praesidium_empresa_mirror",
    "praesidium_ledger_fiscal",
    "empresa_cnpjs_autorizados",
    "empresas_monitoradas",
]

supabase_reset = FakeDirectSupabase()
estado_sessao = {
    "_dados_hidratados_por_empresa": {
        company_id: {"notas": [{"id": "n1"}]},
        "99": {"notas": [{"id": "n2"}]},
    },
    f"fallback_notas_{company_id}": [{"id": "n1"}],
    f"fallback_split_payment_{company_id}": [{"event_id": "e1"}],
    f"perfil_tributario_{company_id}": {"fator_reducao": 0.7},
    f"perfil_tributario_select_{company_id}": "reduction",
    f"form_liquidacao_{company_id}_op": "value",
}
streamlit_original = xml_engine.st
limpador_original = xml_engine._limpador_cache_dados
limpezas_cache = []
xml_engine.st = SimpleNamespace(session_state=estado_sessao)
xml_engine.registrar_limpador_cache_dados(
    lambda: limpezas_cache.append("limpo")
)
try:
    xml_engine.limpar_dados_empresa_supabase(supabase_reset, company_id)
finally:
    xml_engine.st = streamlit_original
    xml_engine.registrar_limpador_cache_dados(limpador_original)
assert supabase_reset.delete_order == [
    "itens_nota_fiscal",
    "conciliacao_devolucoes",
    "notas_fiscais_recebidas",
    "split_payment_events",
    "praesidium_empresa_mirror",
    "praesidium_ledger_fiscal",
]
assert (
    "neq",
    "item_uid",
    "config:aliquota-ibs-cbs:v1",
) in supabase_reset.delete_filters["praesidium_ledger_fiscal"]
assert f"fallback_notas_{company_id}" not in estado_sessao
assert f"fallback_split_payment_{company_id}" not in estado_sessao
assert f"perfil_tributario_{company_id}" in estado_sessao
assert f"perfil_tributario_select_{company_id}" in estado_sessao
assert f"form_liquidacao_{company_id}_op" not in estado_sessao
assert company_id not in estado_sessao["_dados_hidratados_por_empresa"]
assert "99" in estado_sessao["_dados_hidratados_por_empresa"]
assert limpezas_cache == ["limpo"]

empresa = {"id": 88}
cache = {}
snapshot = {
    "notas": [{"id": "n1"}],
    "itens": [{"item_uid": "uid1"}],
    "devolucoes": [],
    "eventos_split": [],
}
hidrator_original = xml_engine.hidratar_dados_empresa_supabase
try:
    xml_engine.hidratar_dados_empresa_supabase = (
        lambda *_: snapshot
    )
    assert xml_engine.hidratar_dados_empresa_com_cache(
        object(),
        company_id,
        empresa,
        cache,
    ) == snapshot

    def falha_temporaria(*_):
        raise RuntimeError("erro de rede simulado")

    xml_engine.hidratar_dados_empresa_supabase = falha_temporaria
    assert xml_engine.hidratar_dados_empresa_com_cache(
        object(),
        company_id,
        empresa,
        cache,
    ) == snapshot

    cache.clear()
    assert xml_engine.hidratar_dados_empresa_com_cache(
        object(),
        company_id,
        empresa,
        cache,
    ) == {
        "notas": [],
        "itens": [],
        "devolucoes": [],
        "eventos_split": [],
    }
finally:
    xml_engine.hidratar_dados_empresa_supabase = hidrator_original

print("PASS: atomic-delete RPC contract and silent hydration cache fallback.")

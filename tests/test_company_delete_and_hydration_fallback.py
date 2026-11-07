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

    def rpc(self, name, params):
        self.rpc_call = (name, params)
        return FakeRpc(name, params, self.response)


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

    def select(self, columns):
        return self

    def execute(self):
        self.database.delete_order.append(self.table)
        data = [{"id": company_id}] if self.table == "empresas_monitoradas" else []
        return FakeResponse(data)


class FakeDirectSupabase:
    def __init__(self):
        self.delete_order = []

    def rpc(self, name, params):
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

try:
    xml_engine.excluir_empresa_supabase(
        FakeSupabase({"deleted": False, "empresa_id": company_id}),
        company_id,
    )
except RuntimeError:
    pass
else:
    raise AssertionError("A exclusão não confirmada deveria falhar.")

supabase_direto = FakeDirectSupabase()
resultado_direto = xml_engine.excluir_empresa_supabase(
    supabase_direto,
    company_id,
)
assert resultado_direto["fallback"] is True
assert supabase_direto.delete_order[:3] == [
    "itens_nota_fiscal",
    "notas_fiscais_recebidas",
    "praesidium_ledger_fiscal",
]
assert supabase_direto.delete_order[-1] == "empresas_monitoradas"

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
        lambda *_args: snapshot
    )
    assert xml_engine.hidratar_dados_empresa_com_cache(
        object(),
        company_id,
        empresa,
        cache,
    ) == snapshot

    def falha_temporaria(*_args):
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

import logging
from types import SimpleNamespace

import xml_engine


EMPRESA = {
    "id": 88,
    "cnpj": "12345678000195",
    "cnae_principal": "9602501",
}
CHAVE = "3" * 44


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, database, table):
        self.database = database
        self.table_name = table
        self.action = "select"
        self.payload = None
        self.filters = []

    def select(self, columns=None, **options):
        self.selected_columns = columns
        self.options = options
        return self

    def insert(self, payload):
        self.action = "insert"
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict=None, **options):
        self.action = "upsert"
        self.payload = payload
        self.on_conflict = on_conflict
        self.options = options
        return self

    def update(self, payload):
        self.action = "update"
        self.payload = payload
        return self

    def delete(self):
        self.action = "delete"
        return self

    def eq(self, column, value):
        self.filters.append((column, "eq", value))
        return self

    def in_(self, column, values):
        self.filters.append((column, "in", values))
        return self

    def range(self, start, end):
        self.range_values = (start, end)
        return self

    def limit(self, limit):
        self.limit_value = limit
        return self

    def _matches(self, record):
        for column, operator, value in self.filters:
            if operator == "eq" and str(record.get(column)) != str(value):
                return False
            if operator == "in" and str(record.get(column)) not in {
                str(item) for item in value
            }:
                return False
        return True

    def execute(self):
        if self.table_name == "itens_nota_fiscal":
            if self.action in {"delete", "upsert", "insert"}:
                if self.action == "upsert":
                    incoming = self.payload if isinstance(self.payload, list) else [self.payload]
                    self.database.item_upsert_sizes.append(len(incoming))
                raise RuntimeError("simulated items table write failure")
            return FakeResponse([])

        if self.table_name == "praesidium_empresa_mirror":
            rows = self.database.mirror
            if self.action == "select":
                return FakeResponse([row for row in rows if self._matches(row)])
            if self.action == "delete":
                self.database.mirror = [
                    row for row in rows if not self._matches(row)
                ]
                return FakeResponse([])
            if self.action == "upsert":
                incoming = self.payload if isinstance(self.payload, list) else [self.payload]
                self.database.mirror_upsert_sizes.append(len(incoming))
                indexed = {
                    (
                        str(row.get("empresa_id")),
                        str(row.get("record_type")),
                        str(row.get("record_key")),
                    ): row
                    for row in rows
                }
                for row in incoming:
                    indexed[(
                        str(row.get("empresa_id")),
                        str(row.get("record_type")),
                        str(row.get("record_key")),
                    )] = row
                self.database.mirror = list(indexed.values())
                return FakeResponse(incoming)

        if self.table_name == "notas_fiscais_recebidas":
            rows = self.database.notes
            if self.action == "select":
                return FakeResponse([row.copy() for row in rows if self._matches(row)])
            if self.action == "update":
                updated = []
                for row in rows:
                    if self._matches(row):
                        row.update(self.payload)
                        updated.append(row.copy())
                return FakeResponse(updated)
            if self.action == "insert":
                incoming = self.payload if isinstance(self.payload, list) else [self.payload]
                created = []
                for payload in incoming:
                    row = {**payload, "id": f"note_{len(rows) + 1}"}
                    rows.append(row)
                    created.append(row.copy())
                return FakeResponse(created)
        raise AssertionError(f"Unexpected operation: {self.table_name}/{self.action}")


class FakeSupabase:
    def __init__(self):
        self.notes = [{
            "id": "existing_note_1",
            "empresa_id": EMPRESA["id"],
            "chave_acesso": CHAVE,
        }]
        self.item_upsert_sizes = []
        self.mirror_upsert_sizes = []
        self.mirror = [{
            "empresa_id": EMPRESA["id"],
            "record_type": "item",
            "record_key": f"{CHAVE}_ITEM_0100",
            "payload": {
                "empresa_id": EMPRESA["id"],
                "nota_id": "existing_note_1",
                "item_uid": f"{CHAVE}_ITEM_0100",
                "nitem": 100,
                "descricao": "Smart TV antiga e truncada",
            },
        }]

    def table(self, name):
        return FakeQuery(self, name)


def _xml_100_items():
    dets = []
    for index in range(1, 101):
        if index <= 60:
            ncm = "33051000" if index % 2 else "33059000"
            valor = 220.62 if index <= 54 else 220.58
        else:
            ncm = "40111000" if index % 2 else "85287200"
            valor = 1214.36 if index <= 71 else 1214.35
        dets.append(
            f'<det nItem="{index}"><prod><xProd>Produto {index}</xProd>'
            f"<NCM>{ncm}</NCM><CFOP>5102</CFOP><qCom>1</qCom>"
            f"<vProd>{valor:.2f}</vProd></prod></det>"
        )
    return (
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe>'
        f'<infNFe Id="NFe{CHAVE}"><ide><dhEmi>2026-11-06T10:00:00-03:00</dhEmi></ide>'
        f'<emit><CNPJ>{EMPRESA["cnpj"]}</CNPJ><xNome>Fornecedor</xNome><CRT>3</CRT></emit>'
        f'<dest><CNPJ>{EMPRESA["cnpj"]}</CNPJ></dest>{"".join(dets)}'
        "<total><ICMSTot><vNF>61811.07</vNF></ICMSTot></total>"
        "</infNFe></NFe></nfeProc>"
    ).encode("utf-8")


logging.disable(logging.CRITICAL)
empresa_state_key = f"fallback_itens_{EMPRESA['id']}"
cache_clear_calls = []
xml_engine.st = SimpleNamespace(
    session_state={},
    toast=lambda *args, **kwargs: (args, kwargs),
)
xml_engine.registrar_limpador_cache_dados(
    lambda: cache_clear_calls.append("cleared")
)
supabase = FakeSupabase()
xml_engine.st.session_state[empresa_state_key] = [{
    "id": "old_local_item",
    "empresa_id": EMPRESA["id"],
    "nota_id": "existing_note_1",
    "item_uid": f"{CHAVE}_ITEM_0100",
    "nitem": 100,
    "descricao": "Smart TV antiga e truncada",
}]
resultado = xml_engine.processar_xml_lote_bytes(
    [("nfe_teste_salao_100_itens.xml", _xml_100_items())],
    supabase,
    EMPRESA["id"],
    EMPRESA,
)

assert len(resultado) == 1
assert resultado[0][0], resultado[0][1]
assert cache_clear_calls
abort_message = "Não foi possível preparar a sincronização " + "dos itens existentes."
assert abort_message not in resultado[0][1]
itens = xml_engine.st.session_state[empresa_state_key]
assert len(itens) == 100, (
    len(itens),
    resultado,
    [item.get("item_uid") for item in itens[:5]],
    len(supabase.mirror),
)
assert len({item["item_uid"] for item in itens}) == 100
assert all(
    item["item_uid"] == f"{CHAVE}_ITEM_{item['nitem']:04d}"
    for item in itens
)
resumo = xml_engine.calcular_resumo_creditos(itens)
assert sum(
    item["classificacao_item"] == "Aprovado Revenda"
    for item in itens
) == 60
assert sum(
    item["classificacao_item"] == "Quarentena Uso/Consumo"
    for item in itens
) == 40
assert round(resumo["validado"], 2) == 3507.54, resumo
assert round(resumo["quarentena"], 2) == 12872.11, resumo
assert round(resumo["bruto"], 2) == 16379.65, resumo
assert len(supabase.mirror) == 100
assert supabase.item_upsert_sizes
assert max(supabase.item_upsert_sizes) == 25
assert supabase.mirror_upsert_sizes
assert max(supabase.mirror_upsert_sizes) <= 25
assert sum(
    row.get("record_type") == "item"
    and str(row.get("empresa_id")) == str(EMPRESA["id"])
    for row in supabase.mirror
) == 100

print(
    "PASS: same import path; reprocessing and failed item-table delete/upsert tolerated; "
    "100 items in session and mirror; 60 validated / 40 quarantine; "
    "credits R$ 3,507.54 / R$ 12,872.11 / gross R$ 16,379.65"
)

import io
import logging
import sys
from types import SimpleNamespace

import xml_engine
import relatorio_engine
from reportlab.pdfgen import canvas


EMPRESA = {
    "id": 88,
    "cnpj": "12345678000195",
    "cnae_principal": "9602501",
}
CHAVE = "3" * 44


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeRpc:
    def __init__(self, database, name, params):
        self.database = database
        self.name = name
        self.params = params

    def execute(self):
        if self.name != "decidir_itens_quarentena_lote":
            raise AssertionError(f"Unexpected RPC: {self.name}")
        empresa_id = str(self.params["p_empresa_id"])
        ledger = self.database.tables.setdefault("praesidium_ledger_fiscal", [])
        espelho = self.database.tables["praesidium_empresa_mirror"]
        decisoes = self.params["p_decisoes"]
        pendentes = {}
        for decisao in decisoes:
            uid = decisao["item_uid"]
            for registro in ledger:
                if (
                    str(registro.get("empresa_id")) == empresa_id
                    and registro.get("item_uid") == uid
                ):
                    pendentes[uid] = dict(registro["payload"])
            for registro in espelho:
                if (
                    str(registro.get("empresa_id")) == empresa_id
                    and registro.get("record_type") == "item"
                    and registro.get("record_key") == uid
                ):
                    pendentes[uid] = dict(registro["payload"])
        if any(
            uid not in pendentes
            or pendentes[uid].get("classificacao_item") != "Quarentena Uso/Consumo"
            for uid in (decisao["item_uid"] for decisao in decisoes)
        ):
            raise RuntimeError("Item pendente não encontrado no teste.")

        decisoes_por_uid = {
            decisao["item_uid"]: decisao
            for decisao in decisoes
        }
        for registro in ledger:
            decisao = decisoes_por_uid.get(registro.get("item_uid"))
            if str(registro.get("empresa_id")) == empresa_id and decisao:
                registro["payload"] = {**registro["payload"], **decisao}
        for registro in espelho:
            decisao = decisoes_por_uid.get(registro.get("record_key"))
            if (
                str(registro.get("empresa_id")) == empresa_id
                and registro.get("record_type") == "item"
                and decisao
            ):
                registro["payload"] = {**registro["payload"], **decisao}
        return FakeResponse({"empresa_id": empresa_id, "updated": len(decisoes)})


class FakeQuery:
    def __init__(self, database, table):
        self.database = database
        self.table_name = table
        self.action = "select"
        self.payload = None
        self.filters = []
        self.start = 0
        self.end = None
        self.limit_value = None

    def select(self, columns=None, **options):
        return self

    def insert(self, payload):
        self.action = "insert"
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict=None, **options):
        self.action = "upsert"
        self.payload = payload
        self.on_conflict = on_conflict
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
        self.start = start
        self.end = end
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
        rows = self.database.tables.setdefault(self.table_name, [])
        if self.table_name == "itens_nota_fiscal" and self.action in {
            "delete", "insert", "upsert", "update",
        }:
            self.database.item_write_sizes.append(
                len(self.payload)
                if isinstance(self.payload, list)
                else 1
            )
            raise RuntimeError("simulated items table write failure")

        correspondentes = [row for row in rows if self._matches(row)]
        if self.action == "select":
            fim = self.end + 1 if self.end is not None else None
            selecionados = correspondentes[self.start:fim]
            if self.limit_value is not None:
                selecionados = selecionados[:self.limit_value]
            return FakeResponse([dict(row) for row in selecionados])

        if self.action == "delete":
            self.database.tables[self.table_name] = [
                row for row in rows if not self._matches(row)
            ]
            return FakeResponse([])

        if self.action == "update":
            atualizados = []
            for row in rows:
                if self._matches(row):
                    row.update(self.payload)
                    atualizados.append(dict(row))
            return FakeResponse(atualizados)

        incoming = self.payload if isinstance(self.payload, list) else [self.payload]
        if self.table_name == "praesidium_empresa_mirror":
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
                )] = dict(row)
            self.database.tables[self.table_name] = list(indexed.values())
            return FakeResponse([dict(row) for row in incoming])

        criados = []
        for payload in incoming:
            registro = dict(payload)
            if self.table_name == "notas_fiscais_recebidas":
                registro.setdefault("id", f"note_{len(rows) + 1}")
            elif self.table_name == "split_payment_events":
                registro.setdefault("id", f"event_{len(rows) + 1}")
            rows.append(registro)
            criados.append(dict(registro))
        return FakeResponse(criados)


class FakeSupabase:
    def __init__(self):
        self.tables = {
            "notas_fiscais_recebidas": [{
                "id": "existing_note_1",
                "empresa_id": EMPRESA["id"],
                "chave_acesso": CHAVE,
                "cnpj_emitente": "12345678000195",
                "valor_total_nf": 61810.00,
                "data_emissao": "2026-11-06T10:00:00-03:00",
            }],
            "itens_nota_fiscal": [],
            "praesidium_empresa_mirror": [{
                "empresa_id": EMPRESA["id"],
                "record_type": "item",
                "record_key": f"{CHAVE}::ITEM::0100",
                "payload": {
                    "empresa_id": EMPRESA["id"],
                    "nota_id": "existing_note_1",
                    "item_uid": f"{CHAVE}::ITEM::0100",
                    "nitem": 100,
                    "descricao": "Smart TV antiga e truncada",
                },
                "updated_at": "2026-11-06T10:00:00+00:00",
            }],
            "conciliacao_devolucoes": [],
            "split_payment_events": [],
        }
        self.item_write_sizes = []
        self.mirror_upsert_sizes = []

    @property
    def mirror(self):
        return self.tables["praesidium_empresa_mirror"]

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, name, params):
        return FakeRpc(self, name, params)


def _xml_100_items():
    nomes_fora_cnae = [
        ("Pneus", "40110000"),
        ("Smart TV", "85280000"),
        ("Cerveja", "22030000"),
        ("Ração", "23090000"),
        ("PS5", "95045000"),
        ("Cimento", "25230000"),
        ("Whisky", "22083000"),
        ("Bicicleta", "87120000"),
        ("Churrasqueira", "73211100"),
        ("Óleo Motor", "27101900"),
        ("Picanha", "02013000"),
        ("Carvão", "44020000"),
        ("Joias", "71131900"),
        ("Colchão", "94040000"),
        ("Martelete", "84672100"),
        ("Sapato", "64030000"),
        ("Vinho", "22042100"),
        ("Esteira", "95069100"),
        ("Caixa de Som", "85182100"),
        ("Whey", "21069000"),
    ]
    detalhes = []
    for indice in range(1, 101):
        if indice <= 60:
            descricao = "Shampoo profissional para salão"
            ncm = "33051000"
            valor = 220.00 if indice <= 42 else 222.00
        else:
            descricao, ncm = nomes_fora_cnae[(indice - 61) % len(nomes_fora_cnae)]
            valor = 1214.00 if indice < 100 else 1228.00
        detalhes.append(
            f'<det nItem="{indice}"><prod><xProd>{descricao} {indice}</xProd>'
            f"<NCM>{ncm}</NCM><CFOP>5102</CFOP><qCom>1</qCom>"
            f"<vProd>{valor:.2f}</vProd></prod></det>"
        )
    return (
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe>'
        f'<infNFe Id="NFe{CHAVE}"><ide><dhEmi>2026-11-06T10:00:00-03:00</dhEmi></ide>'
        f'<emit><CNPJ>{EMPRESA["cnpj"]}</CNPJ><xNome>Fornecedor</xNome><CRT>3</CRT></emit>'
        f'<dest><CNPJ>{EMPRESA["cnpj"]}</CNPJ></dest>{"".join(detalhes)}'
        "<total><ICMSTot><vNF>61810.00</vNF></ICMSTot></total>"
        "</infNFe></NFe></nfeProc>"
    ).encode("utf-8")


def _xml_20_items():
    detalhes = "".join(
        f'<det nItem="{indice}"><prod><xProd>Produto {indice}</xProd>'
        f"<NCM>33051000</NCM><CFOP>5102</CFOP><qCom>1</qCom>"
        "<vProd>10.00</vProd></prod></det>"
        for indice in range(1, 21)
    )
    return (
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe>'
        f'<infNFe Id="NFe{CHAVE}"><ide><dhEmi>2026-11-06T10:00:00-03:00</dhEmi></ide>'
        f'<emit><CNPJ>{EMPRESA["cnpj"]}</CNPJ><xNome>Fornecedor</xNome><CRT>3</CRT></emit>'
        f'<dest><CNPJ>{EMPRESA["cnpj"]}</CNPJ></dest>{detalhes}'
        "<total><ICMSTot><vNF>200.00</vNF></ICMSTot></total>"
        "</infNFe></NFe></nfeProc>"
    ).encode("utf-8")


logging.disable(logging.CRITICAL)
xml_engine.st = SimpleNamespace(
    session_state={},
    toast=lambda *args, **kwargs: (args, kwargs),
)
xml_engine.registrar_limpador_cache_dados(lambda: None)
supabase = FakeSupabase()
resultado = xml_engine.processar_xml_lote_bytes(
    [("nfe_teste_salao_100_itens.xml", _xml_100_items())],
    supabase,
    EMPRESA["id"],
    EMPRESA,
)

assert len(resultado) == 1
assert resultado[0][0], resultado[0][1]
assert "Não foi possível preparar a sincronização dos itens existentes." not in resultado[0][1]
assert supabase.item_write_sizes
assert max(supabase.item_write_sizes) == 25
assert supabase.mirror_upsert_sizes
assert max(supabase.mirror_upsert_sizes) <= 25

xml_engine.st.session_state.clear()
dados = xml_engine.hidratar_dados_empresa_supabase(
    supabase,
    EMPRESA["id"],
    EMPRESA,
)
itens = dados["itens"]
resumo = xml_engine.calcular_resumo_creditos(itens)
assert len(itens) == 100
assert len({item["item_uid"] for item in itens}) == 100
assert all(
    item["item_uid"] == f"{CHAVE}::ITEM::{item['nitem']:04d}"
    for item in itens
)
assert sum(item["classificacao_item"] == "Aprovado Revenda" for item in itens) == 60
assert sum(item["classificacao_item"] == "Quarentena Uso/Consumo" for item in itens) == 40
assert resumo["bruto"] == 16379.65, resumo
assert resumo["validado"] == 3507.54, resumo
assert resumo["quarentena"] == 12872.11, resumo

supabase_20 = FakeSupabase()
xml_engine.st.session_state.clear()
resultado_20 = xml_engine.processar_xml_lote_bytes(
    [("nfe_teste_salao_20_itens.xml", _xml_20_items())],
    supabase_20,
    EMPRESA["id"],
    EMPRESA,
)
assert resultado_20[0][0], resultado_20[0][1]
itens_espelho_20 = [
    linha
    for linha in supabase_20.mirror
    if linha.get("record_type") == "item"
]
ledger_20 = supabase_20.tables["praesidium_ledger_fiscal"]
assert len(itens_espelho_20) == 20
assert len(ledger_20) == 20
assert len({linha["record_key"] for linha in itens_espelho_20}) == 20
assert all(
    isinstance(linha["payload"].get(campo), float)
    for linha in ledger_20
    for campo in (
        "valor_item",
        "credito_ibs_gerado",
        "credito_cbs_gerado",
        "valor_credito_item",
    )
)
payload_limpo = xml_engine.sanitizar_payload_supabase(
    "itens_nota_fiscal",
    {
        "empresa_id": EMPRESA["id"],
        "nota_id": "note_1",
        "item_uid": f"{CHAVE}::ITEM::0001",
        "nitem": 1,
        "descricao": "Produto",
        "ncm": "33051000",
        "cfop": "5102",
        "vProd": "10.5",
        "vIBS": "1.25",
        "vCBS": None,
        "credito_calculado": "2.75",
        "id": "not-a-uuid",
        "coluna_inexistente": "remover",
    },
)
assert payload_limpo["valor_item"] == 10.5
assert payload_limpo["credito_ibs_gerado"] == 1.25
assert payload_limpo["credito_cbs_gerado"] == 0.0
assert payload_limpo["valor_credito_item"] == 2.75
assert "id" not in payload_limpo
assert "coluna_inexistente" not in payload_limpo
print("PASS: 20-item note uploaded and ledger persisted with numeric values.")
xml_engine.st.session_state.clear()

item_aprovado = next(
    item for item in itens
    if item["classificacao_item"] == "Quarentena Uso/Consumo"
)
credito_liberado = xml_engine.obter_credito_quarentena(item_aprovado)
ok, mensagem, _ = xml_engine.executar_update_seguro(
    supabase,
    "itens_nota_fiscal",
    {
        "classificacao_item": "Aprovado Manualmente Contador",
        "status_credito": "Validado",
        "valor_credito_item": credito_liberado,
        "justificativa_motor": "Decisão manual confirmada no teste de reboot.",
    },
    {
        "id": item_aprovado["id"],
        "nota_id": item_aprovado["nota_id"],
        "empresa_id": EMPRESA["id"],
        "classificacao_item": "Quarentena Uso/Consumo",
    },
    fallback_record=item_aprovado,
)
assert ok, mensagem

xml_engine.st.session_state.clear()
dados_pos_aprovacao = xml_engine.hidratar_dados_empresa_supabase(
    supabase,
    EMPRESA["id"],
    EMPRESA,
)
resumo_pos_aprovacao = xml_engine.calcular_resumo_creditos(
    dados_pos_aprovacao["itens"]
)
assert sum(
    item["classificacao_item"] == "Aprovado Manualmente Contador"
    for item in dados_pos_aprovacao["itens"]
) == 1
assert resumo_pos_aprovacao["bruto"] == 16379.65
assert resumo_pos_aprovacao["validado"] == 3829.25
assert resumo_pos_aprovacao["quarentena"] == 12550.40

resultado_reenvio = xml_engine.processar_xml_lote_bytes(
    [("nfe_teste_salao_100_itens.xml", _xml_100_items())],
    supabase,
    EMPRESA["id"],
    EMPRESA,
)
assert resultado_reenvio[0][0], resultado_reenvio[0][1]
xml_engine.st.session_state.clear()
dados_pos_reenvio = xml_engine.hidratar_dados_empresa_supabase(
    supabase,
    EMPRESA["id"],
    EMPRESA,
)
assert len(dados_pos_reenvio["itens"]) == 100
assert sum(
    item["classificacao_item"] == "Aprovado Manualmente Contador"
    for item in dados_pos_reenvio["itens"]
) == 1
assert xml_engine.calcular_resumo_creditos(
    dados_pos_reenvio["itens"]
)["validado"] == 3829.25

evento = {
    "empresa_id": EMPRESA["id"],
    "event_id": "reboot-test-split-payment-1",
    "transaction_id": "reboot-test-transaction-1",
    "event_type": "split_payment.retained",
    "gross_amount": 500.00,
    "retained_amount": 100.00,
    "currency": "BRL",
    "occurred_at": "2026-11-07T12:00:00+00:00",
    "payload_sha256": "a" * 64,
}
ok, mensagem, _ = xml_engine.inserir_seguro_supabase(
    supabase,
    "split_payment_events",
    evento,
)
assert ok, mensagem

xml_engine.st.session_state.clear()
dados_pos_split = xml_engine.hidratar_dados_empresa_supabase(
    supabase,
    EMPRESA["id"],
    EMPRESA,
)
resumo_pos_split = xml_engine.calcular_resumo_creditos(dados_pos_split["itens"])
retido = sum(
    evento["retained_amount"]
    for evento in dados_pos_split["eventos_split"]
)
assert len(dados_pos_split["itens"]) == 100
assert retido == 100.00
assert round(resumo_pos_split["validado"] - retido, 2) == 3729.25

print(
    "PASS: reboot hydration recovered 100 items; 60 validated / 40 quarantine; "
    "gross R$ 16,379.65, validated R$ 3,507.54, quarantine R$ 12,872.11."
)
print(
    "PASS: approval and R$ 100.00 split-payment retention survived subsequent "
    "session_state clears and Supabase re-hydration."
)

chave_pdf = "2" * 44
nota_pdf = {
    "id": "pdf_note_1",
    "chave_acesso": chave_pdf,
    "numero": "1050",
    "serie": "1",
    "cnpj_emitente": "60746948000112",
    "razao_social_emitente": "Fornecedor DANFE Teste",
    "valor_total_nf": 5000.00,
    "data_emissao": "2026-11-07T10:00:00-03:00",
}
itens_pdf = []
for indice in range(1, 51):
    salao = indice <= 25
    itens_pdf.append({
        "nitem": indice,
        "codigo": f"PROD-{indice:03d}",
        "descricao": "Shampoo profissional" if salao else "Pneu automotivo",
        "ncm": "33051000" if salao else "40111000",
        "cfop": "5102",
        "quantidade_comercial": 1.0,
        "valor_unitario": 100.00,
        "valor_item": 100.00,
        "credito_ibs_gerado": 17.70,
        "credito_cbs_gerado": 8.80,
        "valor_credito_item": 26.50,
        "classificacao_item": "Aprovado Revenda" if salao else "Quarentena Uso/Consumo",
    })
pdf_danfe = relatorio_engine.gerar_danfe_pdf(
    nota_pdf,
    itens_pdf,
    {**EMPRESA, "razao_social": "Salão Teste"},
)
assert pdf_danfe.startswith(b"%PDF")
xml_reconstruido = xml_engine.converter_danfe_pdf_para_xml(
    pdf_danfe,
    EMPRESA["cnpj"],
    "Salão Teste",
)
raiz_pdf = xml_engine.ET.fromstring(xml_reconstruido)
assert len([
    elemento for elemento in raiz_pdf.iter()
    if elemento.tag.split("}")[-1] == "det"
]) == 50
resultado_pdf = xml_engine.processar_xml_bytes(
    xml_reconstruido,
    supabase,
    EMPRESA["id"],
    EMPRESA,
)
assert resultado_pdf[0], resultado_pdf[1]
xml_engine.st.session_state.clear()
dados_pdf_reboot = xml_engine.hidratar_dados_empresa_supabase(
    supabase,
    EMPRESA["id"],
    EMPRESA,
)
nota_persistida_pdf = next(
    nota for nota in dados_pdf_reboot["notas"]
    if nota.get("chave_acesso") == chave_pdf
)
itens_pdf_persistidos = [
    item for item in dados_pdf_reboot["itens"]
    if str(item.get("nota_id")) == str(nota_persistida_pdf["id"])
]
assert len(itens_pdf_persistidos) == 50
assert sum(
    item["classificacao_item"] == "Aprovado Revenda"
    for item in itens_pdf_persistidos
) == 25
assert sum(
    item["classificacao_item"] == "Quarentena Uso/Consumo"
    for item in itens_pdf_persistidos
) == 25
assert xml_engine.calcular_resumo_creditos(itens_pdf_persistidos) == {
    "bruto": 1325.0,
    "validado": 662.5,
    "quarentena": 662.5,
    "rejeitado": 0.0,
}
assert nota_persistida_pdf["xml_reconstruido"].encode("utf-8") == xml_reconstruido

pendentes_lote = [
    item for item in itens_pdf_persistidos
    if item["classificacao_item"] == "Quarentena Uso/Consumo"
]
selecionados_aprovar = pendentes_lote[:2]
selecionados_rejeitar = pendentes_lote[2:5]
assert xml_engine.decidir_itens_quarentena_lote(
    supabase,
    EMPRESA["id"],
    selecionados_aprovar,
    "aprovar",
) == 2
assert xml_engine.decidir_itens_quarentena_lote(
    supabase,
    EMPRESA["id"],
    selecionados_rejeitar,
    "rejeitar",
) == 3
try:
    xml_engine.decidir_itens_quarentena_lote(
        supabase,
        EMPRESA["id"],
        [
            pendentes_lote[5],
            {"empresa_id": EMPRESA["id"], "item_uid": "uid-ausente"},
        ],
        "aprovar",
    )
except RuntimeError:
    pass
else:
    raise AssertionError("A decisão parcial deveria ser revertida.")
xml_engine.st.session_state.clear()
dados_lote_reboot = xml_engine.hidratar_dados_empresa_supabase(
    supabase,
    EMPRESA["id"],
    EMPRESA,
)
itens_pdf_pos_lote = [
    item for item in dados_lote_reboot["itens"]
    if str(item.get("nota_id")) == str(nota_persistida_pdf["id"])
]
assert sum(
    item["classificacao_item"] == "Aprovado Manualmente Contador"
    for item in itens_pdf_pos_lote
) == 2
assert sum(
    item["classificacao_item"] == "Rejeitado pelo Contador"
    for item in itens_pdf_pos_lote
) == 3
assert sum(
    item["classificacao_item"] == "Quarentena Uso/Consumo"
    for item in itens_pdf_pos_lote
) == 20
assert xml_engine.calcular_resumo_creditos(itens_pdf_pos_lote) == {
    "bruto": 1245.5,
    "validado": 715.5,
    "quarentena": 530.0,
    "rejeitado": 79.5,
}
pdf_nativo = relatorio_engine._pdf_nativo_danfe(
    relatorio_engine._payload_danfe(
        nota_pdf,
        itens_pdf[:2],
        {**EMPRESA, "razao_social": "Salão Teste"},
    )
)
xml_nativo = xml_engine.converter_danfe_pdf_para_xml(
    pdf_nativo,
    EMPRESA["cnpj"],
    "Salão Teste",
)
assert sum(
    elemento.tag.split("}")[-1] == "det"
    for elemento in xml_engine.ET.fromstring(xml_nativo).iter()
) == 2
modulo_pypdf_original = sys.modules.get("pypdf")
sys.modules["pypdf"] = None
try:
    xml_nativo_sem_pypdf = xml_engine.converter_danfe_pdf_para_xml(
        pdf_nativo,
        EMPRESA["cnpj"],
        "Salão Teste",
    )
finally:
    if modulo_pypdf_original is None:
        sys.modules.pop("pypdf", None)
    else:
        sys.modules["pypdf"] = modulo_pypdf_original
assert xml_nativo_sem_pypdf == xml_nativo

pdf_externo_buffer = io.BytesIO()
pdf_externo_canvas = canvas.Canvas(pdf_externo_buffer)
linhas_pdf_externo = [
    "DANFE - Documento Auxiliar da Nota Fiscal Eletronica",
    "Razao Social: Fornecedor Externo Ltda",
    "CNPJ Emitente: 60.746.948/0001-12",
    "CNPJ Destinatario: 12.345.678/0001-95",
    f"Chave de acesso: {'3' * 44}",
    "NF-e Nº: 2091 Serie: 1",
    "05/11/2026",
    "CODIGO DESCRICAO NCM CST CFOP UN QTD V.UNIT V.TOTAL",
    "SH001 Shampoo profissional 33051000 000 5102 UN 1,0000 100,00 100,00",
    "PN001 Pneu automotivo 40111000 000 5102 UN 1,0000 200,00 200,00",
]
for indice, linha in enumerate(linhas_pdf_externo):
    pdf_externo_canvas.drawString(30, 800 - indice * 18, linha)
pdf_externo_canvas.save()
xml_externo = xml_engine.converter_danfe_pdf_para_xml(
    pdf_externo_buffer.getvalue(),
    EMPRESA["cnpj"],
    "Salão Teste",
)
itens_externos = [
    elemento for elemento in xml_engine.ET.fromstring(xml_externo).iter()
    if elemento.tag.split("}")[-1] == "det"
]
assert len(itens_externos) == 2
assert [
    next(
        filho.text for filho in detalhe.iter()
        if filho.tag.split("}")[-1] == "NCM"
    )
    for detalhe in itens_externos
] == ["33051000", "40111000"]
nota_externa, itens_externos_preparados, erro_externo = xml_engine._preparar_xml_nfe(
    xml_externo,
    EMPRESA["id"],
    EMPRESA,
    set(),
    set(),
    {EMPRESA["cnpj"]},
)
assert nota_externa is not None, erro_externo
assert len(itens_externos_preparados) == 2
assert [
    xml_engine.classificar_item_inteligente(
        item["ncm"],
        item["cfop"],
        item["quantidade_comercial"],
        item["valor_item"],
        nota_externa["crt_emitente"],
        EMPRESA["cnae_principal"],
        False,
        item["descricao"],
    )[0]
    for item in itens_externos_preparados
] == ["Aprovado Revenda", "Quarentena Uso/Consumo"]
print(
    "PASS: 50-item DANFE PDF round-trip recovered every item, ran CNAE audit, "
    "persisted XML and passed reboot checks."
)
print(
    "PASS: mass approval and rejection were atomic in the RPC contract and "
    "survived Supabase hydration."
)

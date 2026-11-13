"""Resiliência da importação XML/DANFE com um Supabase falso funcional.

O fake respeita UUIDs, conflitos de upsert (itens e espelho) e tabelas ausentes
(PGRST205), como o banco real. Falhas na tabela de itens são injetadas por
`banco.falhar_itens`.
"""
import io
import logging
import sys
import unittest
import uuid
from types import SimpleNamespace

from postgrest.exceptions import APIError
from reportlab.pdfgen import canvas

import praesidium_persistence_fallback as ppf
import relatorio_engine
import xml_engine

EMPRESA = {
    "id": str(uuid.uuid4()),
    "cnpj": "12345678000195",
    "cnae_principal": "9602501",
}
CHAVE = "3" * 44
TABELAS_EXISTENTES = {
    "notas_fiscais_recebidas",
    "itens_nota_fiscal",
    "praesidium_empresa_mirror",
    "conciliacao_devolucoes",
    "split_payment_events",
}
CONFLITOS = {
    "itens_nota_fiscal": ("empresa_id", "item_uid"),
    "praesidium_empresa_mirror": ("empresa_id", "record_type", "record_key"),
}


def _erro(codigo, mensagem):
    return APIError({"message": mensagem, "code": codigo, "hint": None, "details": None})


class Consulta:
    def __init__(self, banco, tabela):
        self.banco, self.tabela = banco, tabela
        self.acao, self.payload, self.filtros = "select", None, []
        self.conflito, self.inicio, self.fim, self.limite = None, 0, None, None

    def select(self, *_, **__):
        return self

    def insert(self, payload):
        self.acao, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None, **_):
        self.acao, self.payload, self.conflito = "upsert", payload, on_conflict
        return self

    def update(self, payload):
        self.acao, self.payload = "update", payload
        return self

    def delete(self):
        self.acao = "delete"
        return self

    def eq(self, coluna, valor):
        self.filtros.append((coluna, "eq", valor))
        return self

    def neq(self, coluna, valor):
        self.filtros.append((coluna, "neq", valor))
        return self

    def in_(self, coluna, valores):
        self.filtros.append((coluna, "in", valores))
        return self

    def like(self, coluna, padrao):
        self.filtros.append((coluna, "like", padrao))
        return self

    def range(self, inicio, fim):
        self.inicio, self.fim = inicio, fim
        return self

    def limit(self, limite):
        self.limite = limite
        return self

    def _casa(self, linha):
        for coluna, operador, valor in self.filtros:
            atual = str(linha.get(coluna))
            if operador == "eq" and atual != str(valor):
                return False
            if operador == "neq" and atual == str(valor):
                return False
            if operador == "in" and atual not in {str(v) for v in valor}:
                return False
            if operador == "like" and not atual.startswith(str(valor).rstrip("%")):
                return False
        return True

    def execute(self):
        banco = self.banco
        if self.tabela not in TABELAS_EXISTENTES:
            raise _erro("PGRST205", f"Could not find the table 'public.{self.tabela}'")
        linhas = banco.tabelas.setdefault(self.tabela, [])
        if self.tabela == "itens_nota_fiscal" and self.acao != "select":
            tamanho = len(self.payload) if isinstance(self.payload, list) else 1
            banco.escritas_itens.append(tamanho)
            if banco.falhar_itens:
                raise RuntimeError("falha simulada na tabela de itens")
        if self.acao == "select":
            achadas = [dict(l) for l in linhas if self._casa(l)]
            fim = self.fim + 1 if self.fim is not None else None
            achadas = achadas[self.inicio:fim]
            return SimpleNamespace(data=achadas[: self.limite] if self.limite else achadas)
        if self.acao == "delete":
            removidas = [l for l in linhas if self._casa(l)]
            banco.tabelas[self.tabela] = [l for l in linhas if l not in removidas]
            return SimpleNamespace(data=[dict(l) for l in removidas])
        if self.acao == "update":
            alteradas = []
            for linha in linhas:
                if self._casa(linha):
                    linha.update(self.payload)
                    alteradas.append(dict(linha))
            return SimpleNamespace(data=alteradas)
        entradas = self.payload if isinstance(self.payload, list) else [self.payload]
        if self.tabela == "praesidium_empresa_mirror":
            banco.escritas_espelho.append(len(entradas))
        colunas = (
            tuple(self.conflito.split(",")) if self.conflito else CONFLITOS.get(self.tabela)
        )
        gravadas = []
        for entrada in entradas:
            nova = dict(entrada)
            existente = None
            if self.acao == "upsert" and colunas and all(c in nova for c in colunas):
                existente = next(
                    (l for l in linhas if all(str(l.get(c)) == str(nova[c]) for c in colunas)),
                    None,
                )
            if existente is not None:
                existente.update(nova)
                gravadas.append(dict(existente))
                continue
            if self.tabela != "praesidium_empresa_mirror":
                nova.setdefault("id", str(uuid.uuid4()))
            linhas.append(nova)
            gravadas.append(dict(nova))
        return SimpleNamespace(data=gravadas)


class Banco:
    def __init__(self):
        self.tabelas = {nome: [] for nome in TABELAS_EXISTENTES}
        self.escritas_itens, self.escritas_espelho = [], []
        self.falhar_itens = False

    def table(self, nome):
        return Consulta(self, nome)

    def rpc(self, nome, _):
        raise _erro("PGRST202", f"Could not find the function public.{nome}")

    @property
    def espelho(self):
        return self.tabelas["praesidium_empresa_mirror"]


def _xml(itens: list[tuple[str, str, float]]) -> bytes:
    detalhes = "".join(
        f'<det nItem="{i}"><prod><xProd>{desc} {i}</xProd><NCM>{ncm}</NCM>'
        f"<CFOP>5102</CFOP><qCom>1</qCom><vProd>{valor:.2f}</vProd></prod></det>"
        for i, (desc, ncm, valor) in enumerate(itens, start=1)
    )
    total = sum(valor for _, _, valor in itens)
    return (
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe>'
        f'<infNFe Id="NFe{CHAVE}"><ide><dhEmi>2026-11-06T10:00:00-03:00</dhEmi></ide>'
        f'<emit><CNPJ>{EMPRESA["cnpj"]}</CNPJ><xNome>Fornecedor</xNome><CRT>3</CRT></emit>'
        f'<dest><CNPJ>{EMPRESA["cnpj"]}</CNPJ></dest>{detalhes}'
        f"<total><ICMSTot><vNF>{total:.2f}</vNF></ICMSTot></total>"
        "</infNFe></NFe></nfeProc>"
    ).encode("utf-8")


FORA_DO_CNAE = [
    ("Pneus", "40110000"), ("Smart TV", "85280000"), ("Cerveja", "22030000"),
    ("Ração", "23090000"), ("PS5", "95045000"), ("Cimento", "25230000"),
    ("Whisky", "22083000"), ("Bicicleta", "87120000"), ("Churrasqueira", "73211100"),
    ("Óleo Motor", "27101900"), ("Picanha", "02013000"), ("Carvão", "44020000"),
    ("Joias", "71131900"), ("Colchão", "94040000"), ("Martelete", "84672100"),
    ("Sapato", "64030000"), ("Vinho", "22042100"), ("Esteira", "95069100"),
    ("Caixa de Som", "85182100"), ("Whey", "21069000"),
]


def _itens_100():
    itens = []
    for i in range(1, 101):
        if i <= 60:
            itens.append(("Shampoo profissional para salão", "33051000", 220.0 if i <= 42 else 222.0))
        else:
            desc, ncm = FORA_DO_CNAE[(i - 61) % len(FORA_DO_CNAE)]
            itens.append((desc, ncm, 1214.0 if i < 100 else 1228.0))
    return itens


class ImportacaoResilienteTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.estado = {}
        xml_engine.st = SimpleNamespace(
            session_state=self.estado,
            toast=lambda *args, **kwargs: None,
        )
        xml_engine.registrar_limpador_cache_dados(lambda: None)
        xml_engine.limpar_cache_recursos_ausentes()
        self.banco = Banco()

    def _importar(self, arquivo=("nfe.xml", None), banco=None):
        return xml_engine.processar_xml_lote_bytes(
            [arquivo], banco or self.banco, EMPRESA["id"], EMPRESA
        )

    def _hidratar(self):
        self.estado.clear()
        return xml_engine.hidratar_dados_empresa_supabase(
            self.banco, EMPRESA["id"], EMPRESA
        )

    def test_nota_de_100_itens_grava_em_lotes_de_25_e_hidrata_tudo(self):
        resultado = self._importar(("nfe_100.xml", _xml(_itens_100())))
        self.assertTrue(resultado[0][0], resultado[0][1])
        self.assertEqual(max(self.banco.escritas_itens), 25)
        self.assertLessEqual(max(self.banco.escritas_espelho), 25)
        itens = self._hidratar()["itens"]
        self.assertEqual(len({i["item_uid"] for i in itens}), 100)
        self.assertTrue(all(
            i["item_uid"] == f"{CHAVE}::ITEM::{i['nitem']:04d}" for i in itens
        ))
        por_status = {}
        for item in itens:
            por_status[item["classificacao_item"]] = por_status.get(item["classificacao_item"], 0) + 1
        self.assertEqual(por_status, {"Aprovado Revenda": 60, "Quarentena Uso/Consumo": 40})
        self.assertEqual(
            xml_engine.calcular_resumo_creditos(itens),
            {"bruto": 16379.65, "validado": 3507.54, "quarentena": 12872.11, "rejeitado": 0.0},
        )

    def test_decisao_manual_sobrevive_ao_reenvio_e_a_retencao_split(self):
        xml = _xml(_itens_100())
        self._importar(("nfe_100.xml", xml))
        itens = self._hidratar()["itens"]
        alvo = next(i for i in itens if i["classificacao_item"] == "Quarentena Uso/Consumo")
        self.assertEqual(
            xml_engine.decidir_itens_quarentena_lote(self.banco, EMPRESA["id"], [alvo], "aprovar"),
            1,
        )
        resumo = xml_engine.calcular_resumo_creditos(self._hidratar()["itens"])
        self.assertEqual(
            (resumo["bruto"], resumo["validado"], resumo["quarentena"]),
            (16379.65, 3829.25, 12550.40),
        )
        self.assertTrue(self._importar(("nfe_100.xml", xml))[0][0])
        itens = self._hidratar()["itens"]
        self.assertEqual(len(itens), 100)
        self.assertEqual(
            sum(i["classificacao_item"] == "Aprovado Manualmente Contador" for i in itens), 1
        )
        evento = {
            "empresa_id": EMPRESA["id"], "event_id": "evt-1", "transaction_id": "trx-1",
            "event_type": "split_payment.retained", "gross_amount": 500.0,
            "retained_amount": 100.0, "currency": "BRL",
            "occurred_at": "2026-11-07T12:00:00+00:00", "payload_sha256": "a" * 64,
        }
        ok, mensagem, _ = xml_engine.inserir_seguro_supabase(self.banco, "split_payment_events", evento)
        self.assertTrue(ok, mensagem)
        dados = self._hidratar()
        retido = sum(e["retained_amount"] for e in dados["eventos_split"])
        validado = xml_engine.calcular_resumo_creditos(dados["itens"])["validado"]
        self.assertEqual(retido, 100.0)
        self.assertEqual(round(validado - retido, 2), 3729.25)

    def test_perfil_30_gravado_no_espelho_controla_as_aliquotas(self):
        derivacao = list(xml_engine.ALIQUOTAS_REFORMA_TRIBUTARIA)[1]
        xml_engine.persistir_perfil_tributario_empresa(self.banco, EMPRESA["id"], derivacao)
        registro = next(l for l in self.banco.espelho if l["record_key"] == ppf.CHAVE_LEDGER_PERFIL_TRIBUTARIO)
        self.assertEqual(registro["record_type"], "split_payment")
        self.assertEqual(registro["payload"]["record_type"], "company_tax_profile")
        self.assertTrue(self._importar(("nfe_20.xml", _xml([("Produto", "33051000", 10.0)] * 20)))[0][0])
        itens = self._hidratar()["itens"]
        self.assertEqual(len(itens), 20)
        self.assertTrue(all(i["aliquota_aplicada"] == 0.1855 for i in itens))
        self.assertTrue(all(i["valor_credito_item"] == 1.86 for i in itens))
        self.assertTrue(all(round(i["aliquota_ibs_aplicada"], 4) == 0.1239 for i in itens))

    def test_eventos_de_liquidacao_no_espelho_nao_viram_eventos_split(self):
        evento = {
            "event_id": str(uuid.uuid4()), "id_op": "OP-1", "empresa_id": EMPRESA["id"],
            "event_type": "Liquidação Parcial/Integral", "valor_liquidado": 100.0,
            "occurred_at": "2026-11-07T12:00:00+00:00",
        }
        xml_engine.registrar_evento_liquidacao_supabase(self.banco, EMPRESA["id"], evento)
        dados = self._hidratar()
        self.assertEqual(len(dados["eventos_liquidacao"]), 1)
        self.assertEqual(dados["eventos_split"], [])
        self.assertEqual(xml_engine.carregar_espelho_empresa(self.banco, EMPRESA["id"])["split_payment"], [])

    def test_falha_na_tabela_de_itens_preserva_os_itens_no_espelho(self):
        self.banco.falhar_itens = True
        resultado = self._importar(("nfe_20.xml", _xml([("Produto", "33051000", 10.0)] * 20)))
        self.assertTrue(resultado[0][0], resultado[0][1])
        self.assertEqual(self.banco.tabelas["itens_nota_fiscal"], [])
        itens = self._hidratar()["itens"]
        self.assertEqual(len(itens), 20)
        self.assertEqual(len({i["item_uid"] for i in itens}), 20)

    def test_sanitizacao_de_item_converte_aliases_e_descarta_colunas_invalidas(self):
        limpo = xml_engine.sanitizar_payload_supabase("itens_nota_fiscal", {
            "empresa_id": EMPRESA["id"], "nota_id": str(uuid.uuid4()),
            "item_uid": f"{CHAVE}::ITEM::0001", "nitem": 1, "descricao": "Produto",
            "ncm": "33051000", "cfop": "5102", "vProd": "10.5", "vIBS": "1.25",
            "vCBS": None, "credito_calculado": "2.75", "id": "not-a-uuid",
            "coluna_inexistente": "remover",
        })
        self.assertEqual(limpo["valor_item"], 10.5)
        self.assertEqual(limpo["credito_ibs_gerado"], 1.25)
        self.assertEqual(limpo["valor_ibs"], 1.25)
        self.assertEqual(limpo["credito_cbs_gerado"], 0.0)
        self.assertEqual(limpo["valor_credito_item"], 2.75)
        self.assertNotIn("id", limpo)
        self.assertNotIn("coluna_inexistente", limpo)

    def test_campos_ausentes_no_banco_real_sobrevivem_no_espelho(self):
        original = Consulta.execute

        def sem_colunas_extras(consulta):
            if consulta.tabela == "itens_nota_fiscal" and consulta.acao != "select":
                for linha in consulta.payload if isinstance(consulta.payload, list) else [consulta.payload]:
                    for coluna in ("quantidade_comercial", "qcom", "credito_ibs_gerado", "credito_cbs_gerado"):
                        linha.pop(coluna, None)
            return original(consulta)

        Consulta.execute = sem_colunas_extras
        self.addCleanup(setattr, Consulta, "execute", original)
        self.assertTrue(self._importar(("nfe_20.xml", _xml([("Shampoo", "33051000", 100.0)] * 20)))[0][0])
        itens = self._hidratar()["itens"]
        self.assertEqual(len(itens), 20)
        self.assertTrue(all(i["quantidade_comercial"] == 1.0 for i in itens))
        self.assertTrue(all(i["credito_ibs_gerado"] > 0 and i["credito_cbs_gerado"] > 0 for i in itens))


class DanfePdfTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.estado = {}
        xml_engine.st = SimpleNamespace(session_state=self.estado, toast=lambda *a, **k: None)
        xml_engine.registrar_limpador_cache_dados(lambda: None)
        xml_engine.limpar_cache_recursos_ausentes()
        self.banco = Banco()
        self.empresa = {**EMPRESA, "razao_social": "Salão Teste"}

    def _danfe_50_itens(self):
        nota = {
            "id": str(uuid.uuid4()), "chave_acesso": "2" * 44, "numero": "1050", "serie": "1",
            "cnpj_emitente": "60746948000112", "razao_social_emitente": "Fornecedor DANFE Teste",
            "valor_total_nf": 5000.0, "data_emissao": "2026-11-07T10:00:00-03:00",
        }
        itens = [{
            "nitem": i, "codigo": f"PROD-{i:03d}",
            "descricao": "Shampoo profissional" if i <= 25 else "Pneu automotivo",
            "ncm": "33051000" if i <= 25 else "40111000", "cfop": "5102",
            "quantidade_comercial": 1.0, "valor_unitario": 100.0, "valor_item": 100.0,
            "credito_ibs_gerado": 17.70, "credito_cbs_gerado": 8.80, "valor_credito_item": 26.50,
            "classificacao_item": "Aprovado Revenda" if i <= 25 else "Quarentena Uso/Consumo",
        } for i in range(1, 51)]
        return nota, itens, relatorio_engine.gerar_danfe_pdf(nota, itens, self.empresa)

    def test_danfe_de_50_itens_volta_integralmente_e_aceita_decisoes_em_lote(self):
        nota, itens, pdf = self._danfe_50_itens()
        self.assertTrue(pdf.startswith(b"%PDF"))
        xml = xml_engine.converter_danfe_pdf_para_xml(pdf, EMPRESA["cnpj"], "Salão Teste")
        self.assertEqual(sum(e.tag.split("}")[-1] == "det" for e in xml_engine.ET.fromstring(xml).iter()), 50)
        ok, mensagem = xml_engine.processar_xml_bytes(xml, self.banco, EMPRESA["id"], EMPRESA)
        self.assertTrue(ok, mensagem)

        def hidratar():
            self.estado.clear()
            dados = xml_engine.hidratar_dados_empresa_supabase(self.banco, EMPRESA["id"], EMPRESA)
            nota_db = next(n for n in dados["notas"] if n["chave_acesso"] == nota["chave_acesso"])
            return nota_db, [i for i in dados["itens"] if str(i["nota_id"]) == str(nota_db["id"])]

        nota_db, itens_db = hidratar()
        self.assertEqual(len(itens_db), 50)
        self.assertEqual(
            xml_engine.calcular_resumo_creditos(itens_db),
            {"bruto": 1325.0, "validado": 662.5, "quarentena": 662.5, "rejeitado": 0.0},
        )
        self.assertEqual(nota_db["xml_reconstruido"].encode("utf-8"), xml)

        pendentes = [i for i in itens_db if i["classificacao_item"] == "Quarentena Uso/Consumo"]
        self.assertEqual(xml_engine.decidir_itens_quarentena_lote(self.banco, EMPRESA["id"], pendentes[:2], "aprovar"), 2)
        self.assertEqual(xml_engine.decidir_itens_quarentena_lote(self.banco, EMPRESA["id"], pendentes[2:5], "rejeitar"), 3)
        with self.assertRaises(RuntimeError):
            xml_engine.decidir_itens_quarentena_lote(
                self.banco, EMPRESA["id"],
                [pendentes[5], {"empresa_id": EMPRESA["id"], "item_uid": "uid-ausente"}],
                "aprovar",
            )
        _, itens_pos = hidratar()
        contagem = {}
        for item in itens_pos:
            contagem[item["classificacao_item"]] = contagem.get(item["classificacao_item"], 0) + 1
        self.assertEqual(contagem["Aprovado Manualmente Contador"], 2)
        self.assertEqual(contagem["Rejeitado pelo Contador"], 3)
        self.assertEqual(contagem["Quarentena Uso/Consumo"], 20)
        self.assertEqual(
            xml_engine.calcular_resumo_creditos(itens_pos),
            {"bruto": 1245.5, "validado": 715.5, "quarentena": 530.0, "rejeitado": 79.5},
        )

    def test_pdf_nativo_converte_com_e_sem_pypdf(self):
        nota, itens, _ = self._danfe_50_itens()
        pdf = relatorio_engine._pdf_nativo_danfe(
            relatorio_engine._payload_danfe(nota, itens[:2], self.empresa)
        )
        com_pypdf = xml_engine.converter_danfe_pdf_para_xml(pdf, EMPRESA["cnpj"], "Salão Teste")
        original = sys.modules.get("pypdf")
        sys.modules["pypdf"] = None
        try:
            sem_pypdf = xml_engine.converter_danfe_pdf_para_xml(pdf, EMPRESA["cnpj"], "Salão Teste")
        finally:
            if original is None:
                sys.modules.pop("pypdf", None)
            else:
                sys.modules["pypdf"] = original
        self.assertEqual(com_pypdf, sem_pypdf)
        self.assertEqual(sum(e.tag.split("}")[-1] == "det" for e in xml_engine.ET.fromstring(com_pypdf).iter()), 2)

    def test_danfe_externo_em_texto_e_classificado_pelo_cnae(self):
        buffer = io.BytesIO()
        pagina = canvas.Canvas(buffer)
        linhas = [
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
        for indice, linha in enumerate(linhas):
            pagina.drawString(30, 800 - indice * 18, linha)
        pagina.save()
        xml = xml_engine.converter_danfe_pdf_para_xml(buffer.getvalue(), EMPRESA["cnpj"], "Salão Teste")
        detalhes = [e for e in xml_engine.ET.fromstring(xml).iter() if e.tag.split("}")[-1] == "det"]
        self.assertEqual(len(detalhes), 2)
        nota, itens, erro = xml_engine._preparar_xml_nfe(
            xml, EMPRESA["id"], EMPRESA, set(), set(), {EMPRESA["cnpj"]}
        )
        self.assertIsNotNone(nota, erro)
        classes = [
            xml_engine.classificar_item_inteligente(
                i["ncm"], i["cfop"], i["quantidade_comercial"], i["valor_item"],
                nota["crt_emitente"], EMPRESA["cnae_principal"], False, i["descricao"],
            )[0]
            for i in itens
        ]
        self.assertEqual(classes, ["Aprovado Revenda", "Quarentena Uso/Consumo"])


if __name__ == "__main__":
    unittest.main()

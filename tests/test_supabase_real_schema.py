"""Regressões contra um Supabase falso que replica as regras do Postgres real.

O fake valida tipos (uuid/integer/numeric/varchar(n)), colunas inexistentes e
tabelas ausentes com os mesmos códigos de erro vistos nos logs de produção
(22P02, 22001, 42703, PGRST204, PGRST205).
"""
import logging
import re
import unittest
import uuid
from types import SimpleNamespace

from postgrest.exceptions import APIError

import praesidium_persistence_fallback as ppf
import xml_engine

CNPJ_ESPLENDORE = "21270553000134"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

SCHEMA = {
    "empresas_monitoradas": {
        "id": "uuid", "cnpj": ("varchar", 14), "razao_social": "text",
        "regime_tributario": "text", "cnae_principal": ("varchar", 20),
        "certificado_a1_status": "text",
    },
    "notas_fiscais_recebidas": {
        "id": "uuid", "empresa_id": "uuid", "chave_acesso": "text", "numero": "integer",
        "cnpj_emitente": "text", "razao_social_emitente": "text", "nome_emitente": "text",
        "crt_emitente": "text", "valor_total_nf": "numeric", "manifestacao_status": "text",
        "status_manifestacao": "text", "status_processamento": "text",
        "hash_sha256_xml": "text", "regra_tributaria_version": "text",
        "versao_regra": "text", "data_emissao": "text",
    },
    "itens_nota_fiscal": {
        "id": "uuid", "empresa_id": "uuid", "nota_id": "uuid", "nitem": "integer",
        "codigo": "text", "descricao": "text", "descricao_produto": "text",
        "ncm": "text", "cfop": "integer", "quantidade_comercial": "numeric",
        "valor_unitario": "numeric", "valor_item": "numeric",
        "valor_total_bruto": "numeric", "aliquota_aplicada": "numeric",
        "aliquota_ibs_aplicada": "numeric", "aliquota_cbs_aplicada": "numeric",
        "valor_credito_item": "numeric", "credito_ibs_gerado": "numeric",
        "credito_cbs_gerado": "numeric", "classificacao_item": "text",
        "status_credito": "text", "justificativa_motor": "text",
    },
    # Igual ao banco real: sem as colunas payload/item_uid.
    "praesidium_ledger_fiscal": {"id": "uuid", "empresa_id": "uuid", "updated_at": "text"},
    "conciliacao_devolucoes": {"id": "uuid", "empresa_id": "uuid"},
    "split_payment_events": {"id": "uuid", "empresa_id": "uuid"},
}


def _erro(codigo: str, mensagem: str) -> APIError:
    return APIError({"message": mensagem, "code": codigo, "hint": None, "details": None})


class FakeQuery:
    def __init__(self, db, tabela):
        self.db, self.tabela, self.acao = db, tabela, "select"
        self.payload, self.filtros, self.on_conflict = None, [], None
        self.colunas, self.limite = "*", None

    def select(self, colunas="*"):
        self.colunas = colunas
        return self

    def insert(self, payload):
        self.acao, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None, **_):
        self.acao, self.payload, self.on_conflict = "upsert", payload, on_conflict
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

    def _colunas(self):
        return self.db.schema.get(self.tabela)

    def _checar_coluna_leitura(self, coluna):
        if coluna not in self._colunas():
            raise _erro("42703", f"column {self.tabela}.{coluna} does not exist")

    def _validar_valor(self, coluna, valor):
        tipo = self._colunas()[coluna]
        if valor is None:
            return
        texto = str(valor)
        if tipo == "uuid" and not UUID_RE.match(texto):
            raise _erro("22P02", f'invalid input syntax for type uuid: "{texto}"')
        if tipo == "integer" and (
            isinstance(valor, bool) or not re.fullmatch(r"-?\d+", texto)
        ):
            raise _erro("22P02", f'invalid input syntax for type integer: "{texto}"')
        if tipo == "numeric":
            try:
                float(texto)
            except ValueError:
                raise _erro("22P02", f'invalid input syntax for type numeric: "{texto}"')
        if isinstance(tipo, tuple) and len(texto) > tipo[1]:
            raise _erro("22001", f"value too long for type character varying({tipo[1]})")

    def _validar_linha(self, linha):
        for coluna, valor in linha.items():
            if coluna not in self._colunas():
                raise _erro(
                    "PGRST204",
                    f"Could not find the '{coluna}' column of '{self.tabela}' in the schema cache",
                )
            self._validar_valor(coluna, valor)

    def _casa(self, linha):
        for coluna, op, valor in self.filtros:
            self._checar_coluna_leitura(coluna)
            atual = linha.get(coluna)
            if op != "like" and self._colunas()[coluna] == "uuid" and valor is not None:
                self._validar_valor(coluna, valor)
            if op == "eq" and str(atual) != str(valor):
                return False
            if op == "neq" and str(atual) == str(valor):
                return False
            if op == "in" and str(atual) not in {str(v) for v in valor}:
                return False
            if op == "like" and not re.fullmatch(
                re.escape(valor).replace("%", ".*"), str(atual)
            ):
                return False
        return True

    def execute(self):
        try:
            return self._executar()
        except APIError as erro:
            self.db.erros.append(erro.code)
            raise

    def _executar(self):
        self.db.chamadas.append((self.tabela, self.acao))
        if self._colunas() is None:
            raise _erro("PGRST205", f"Could not find the table 'public.{self.tabela}'")
        if self.colunas != "*":
            for coluna in self.colunas.split(","):
                self._checar_coluna_leitura(coluna.strip())
        linhas = self.db.tabelas.setdefault(self.tabela, [])
        if self.acao == "select":
            resultado = [dict(l) for l in linhas if self._casa(l)]
            return SimpleNamespace(data=resultado[: self.limite] if self.limite else resultado)
        if self.acao == "delete":
            removidas = [l for l in linhas if self._casa(l)]
            self.db.tabelas[self.tabela] = [l for l in linhas if l not in removidas]
            return SimpleNamespace(data=[dict(l) for l in removidas])
        if self.acao == "update":
            self._validar_linha(self.payload)
            alteradas = []
            for linha in linhas:
                if self._casa(linha):
                    linha.update(self.payload)
                    alteradas.append(dict(linha))
            return SimpleNamespace(data=alteradas)
        entradas = self.payload if isinstance(self.payload, list) else [self.payload]
        if self.on_conflict:
            for coluna in self.on_conflict.split(","):
                self._checar_coluna_leitura(coluna)
        for entrada in entradas:
            self._validar_linha(entrada)
        criadas = []
        for entrada in entradas:
            linha = dict(entrada)
            if "id" in self._colunas():
                linha.setdefault("id", str(uuid.uuid4()))
            linhas.append(linha)
            criadas.append(dict(linha))
        return SimpleNamespace(data=criadas)


class FakeSupabase:
    def __init__(self):
        self.schema = SCHEMA
        self.tabelas = {nome: [] for nome in SCHEMA}
        self.chamadas = []
        self.erros = []

    def table(self, nome):
        return FakeQuery(self, nome)

    def rpc(self, nome, params):
        raise _erro("PGRST202", f"Could not find the function public.{nome}")

    def contar(self, tabela):
        return sum(1 for t, _ in self.chamadas if t == tabela)


def _xml_nfe(cnpj_dest: str, itens: int = 20, uso_interno: int = 5) -> bytes:
    chave = "3" * 44
    detalhes = "".join(
        f'<det nItem="{i}"><prod><xProd>'
        f'{"Material de consumo interno" if i <= uso_interno else "Shampoo"} {i}'
        f"</xProd><NCM>33051000</NCM><CFOP>5102</CFOP><qCom>1</qCom>"
        f"<vProd>10.00</vProd></prod></det>"
        for i in range(1, itens + 1)
    )
    return (
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe>'
        f'<infNFe Id="NFe{chave}"><ide><dhEmi>2026-11-06T10:00:00-03:00</dhEmi></ide>'
        f"<emit><CNPJ>60746948000112</CNPJ><xNome>Fornecedor</xNome><CRT>3</CRT></emit>"
        f"<dest><CNPJ>{cnpj_dest}</CNPJ></dest>{detalhes}"
        f"<total><ICMSTot><vNF>{itens * 10:.2f}</vNF></ICMSTot></total>"
        "</infNFe></NFe></nfeProc>"
    ).encode("utf-8")


class SupabaseRealSchemaTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        xml_engine.st = SimpleNamespace(session_state={}, toast=lambda *a, **k: None)
        xml_engine.registrar_limpador_cache_dados(lambda: None)
        xml_engine.limpar_cache_recursos_ausentes()
        self.db = FakeSupabase()

    def _criar_empresa(self, cnpj=CNPJ_ESPLENDORE):
        ok, mensagem, criadas = xml_engine.executar_insert_seguro(
            "empresas_monitoradas",
            {
                "razao_social": "ESPLENDORE [T:felipebondezzan]",
                "cnpj": cnpj,
                "regime_tributario": "Lucro Presumido",
                "cnae_principal": "9602501",
                "certificado_a1_status": "Ativo",
            },
            supabase=self.db,
        )
        self.assertTrue(ok, mensagem)
        return criadas[0]

    def _importar(self, empresa):
        ativa = {**empresa, "razao_social": "ESPLENDORE", "cnae_principal": "9602501"}
        return ativa, xml_engine.processar_xml_lote_bytes(
            [("nfe_teste_20_itens_esplendore.xml", _xml_nfe(CNPJ_ESPLENDORE))],
            self.db,
            empresa["id"],
            ativa,
        )

    def test_empresa_respeita_varchar_14_e_nao_envia_empresa_id(self):
        ok, _, criadas = xml_engine.executar_insert_seguro(
            "empresas_monitoradas",
            {
                "cnpj": "21.270.553/0001-34",
                "empresa_id": "local_abc",
                "razao_social": "ESPLENDORE",
                "regime_tributario": "Lucro Presumido",
                "cnae_principal": "9602-5/01",
            },
            supabase=self.db,
        )
        self.assertTrue(ok)
        self.assertEqual(criadas[0]["cnpj"], CNPJ_ESPLENDORE)
        self.assertEqual(criadas[0]["cnae_principal"], "9602501")
        self.assertTrue(xml_engine.eh_uuid_valido(criadas[0]["id"]))

    def test_registros_de_sistema_cabem_em_14_caracteres(self):
        self.assertLessEqual(len(ppf.cnpj_usuario_sistema("felipebondezzan")), 14)
        registro = ppf.codificar_registro_ledger(str(uuid.uuid4()), "config:x", {"a": 1})
        self.assertLessEqual(len(registro["cnpj"]), 14)
        ok, _, _ = xml_engine.executar_insert_seguro(
            "empresas_monitoradas", registro, supabase=self.db
        )
        self.assertTrue(ok)

    def test_upload_grava_nota_e_20_itens_sem_22p02(self):
        empresa = self._criar_empresa()
        _, resultados = self._importar(empresa)
        self.assertTrue(resultados[0][0], resultados[0][1])
        notas = self.db.tabelas["notas_fiscais_recebidas"]
        itens = self.db.tabelas["itens_nota_fiscal"]
        self.assertEqual(len(notas), 1)
        self.assertEqual(len(itens), 20)
        for linha in notas + itens:
            self.assertNotIn("", linha.values())
            self.assertTrue(xml_engine.eh_uuid_valido(linha["id"]))
        self.assertTrue(all(i["nota_id"] == notas[0]["id"] for i in itens))
        self.assertEqual(sorted(i["nitem"] for i in itens), list(range(1, 21)))
        self.assertNotIn("22P02", self.db.erros)
        self.assertNotIn("22001", self.db.erros)

    def test_22p02_inesperado_e_corrigido_e_regravado(self):
        empresa = self._criar_empresa()
        nota = {
            "empresa_id": empresa["id"],
            "chave_acesso": "4" * 44,
            "numero": "ABC",
            "valor_total_nf": "12,50",
        }
        ok, _, gravadas = xml_engine.executar_insert_seguro(
            "notas_fiscais_recebidas", nota, supabase=self.db
        )
        self.assertTrue(ok)
        self.assertNotIn("numero", gravadas[0])
        self.assertEqual(gravadas[0]["valor_total_nf"], 12.5)

    def test_hidratacao_nunca_consulta_com_id_local(self):
        banco = FakeSupabase()
        dados = xml_engine.hidratar_dados_empresa_supabase(
            banco, "local_f7696143146f4e73a960782752b20982",
            {"id": "local_f7696143146f4e73a960782752b20982"},
        )
        self.assertTrue(dados["_falha_hidratacao"])
        self.assertEqual(dados["notas"], [])
        self.assertEqual(banco.chamadas, [])

    def test_fallback_local_nao_gera_ids_local(self):
        ok, _, _ = xml_engine.executar_insert_seguro(
            "empresas_monitoradas",
            {"razao_social": "X", "cnpj": "1", "regime_tributario": "LP", "cnae_principal": "1"},
            supabase=None,
        )
        self.assertFalse(ok)
        self.assertFalse(any(
            str(r.get("id", "")).startswith("local_")
            for chave, valor in xml_engine.st.session_state.items()
            if isinstance(valor, list)
            for r in valor
        ))

    def test_ledger_sem_colunas_usa_sys_ledger_e_cacheia_ausencia(self):
        empresa = self._criar_empresa()
        derivacao = list(xml_engine.ALIQUOTAS_REFORMA_TRIBUTARIA)[1]
        xml_engine.persistir_perfil_tributario_empresa(self.db, empresa["id"], derivacao)
        chamadas_ledger = self.db.contar("praesidium_ledger_fiscal")
        perfil = xml_engine.carregar_perfil_tributario_empresa(self.db, empresa["id"])
        self.assertEqual(perfil["derivacao_setorial"], derivacao)
        xml_engine.carregar_perfil_tributario_empresa(self.db, empresa["id"])
        self.assertEqual(self.db.contar("praesidium_ledger_fiscal"), chamadas_ledger)
        self.assertFalse(xml_engine._ledger_fiscal_disponivel())

    def test_decisao_em_lote_atualiza_itens_reais(self):
        empresa = self._criar_empresa()
        ativa, resultados = self._importar(empresa)
        self.assertTrue(resultados[0][0], resultados[0][1])
        dados = xml_engine.hidratar_dados_empresa_supabase(self.db, empresa["id"], ativa)
        pendentes = [
            i for i in dados["itens"]
            if i["classificacao_item"] == "Quarentena Uso/Consumo"
        ]
        self.assertEqual(len(pendentes), 5)
        atualizados = xml_engine.decidir_itens_quarentena_lote(
            self.db, empresa["id"], pendentes[:3], "aprovar"
        )
        self.assertEqual(atualizados, 3)
        atualizados = xml_engine.decidir_itens_quarentena_lote(
            self.db, empresa["id"], pendentes[3:], "rejeitar"
        )
        self.assertEqual(atualizados, 2)
        por_status = {}
        for linha in self.db.tabelas["itens_nota_fiscal"]:
            por_status[linha["classificacao_item"]] = por_status.get(linha["classificacao_item"], 0) + 1
        self.assertEqual(por_status["Aprovado Manualmente Contador"], 3)
        self.assertEqual(por_status["Rejeitado pelo Contador"], 2)
        with self.assertRaises(RuntimeError):
            xml_engine.decidir_itens_quarentena_lote(
                self.db, empresa["id"], pendentes[:1], "aprovar"
            )

    def test_zerar_notas_e_excluir_cliente(self):
        empresa = self._criar_empresa()
        self._importar(empresa)
        xml_engine.persistir_perfil_tributario_empresa(
            self.db, empresa["id"], list(xml_engine.ALIQUOTAS_REFORMA_TRIBUTARIA)[2]
        )
        xml_engine.limpar_dados_empresa_supabase(self.db, empresa["id"])
        self.assertEqual(self.db.tabelas["notas_fiscais_recebidas"], [])
        self.assertEqual(self.db.tabelas["itens_nota_fiscal"], [])
        perfil = xml_engine.carregar_perfil_tributario_empresa(self.db, empresa["id"])
        self.assertEqual(
            perfil["derivacao_setorial"], list(xml_engine.ALIQUOTAS_REFORMA_TRIBUTARIA)[2]
        )
        resultado = xml_engine.excluir_empresa_supabase(self.db, empresa["id"])
        self.assertTrue(resultado["deleted"])
        self.assertEqual(self.db.tabelas["empresas_monitoradas"], [])

    def test_excluir_cliente_com_id_invalido_nao_consulta_banco(self):
        with self.assertRaises(ValueError):
            xml_engine.excluir_empresa_supabase(self.db, "local_123")
        self.assertEqual(self.db.chamadas, [])


if __name__ == "__main__":
    unittest.main()

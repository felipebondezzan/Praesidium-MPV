"""Exclusão de cliente, "Zerar Notas" e snapshot de hidratação (UUIDs válidos)."""
import unittest
import uuid
from types import SimpleNamespace

import praesidium_persistence_fallback as ppf
import xml_engine

EMPRESA_ID = str(uuid.uuid4())
OUTRA_EMPRESA_ID = str(uuid.uuid4())
TABELAS_LIMPEZA = [
    "itens_nota_fiscal",
    "conciliacao_devolucoes",
    "notas_fiscais_recebidas",
    "split_payment_events",
    "praesidium_empresa_mirror",
]


class Consulta:
    def __init__(self, banco, tabela):
        self.banco, self.tabela = banco, tabela
        self.acao, self.filtros = "select", []

    def delete(self):
        self.acao = "delete"
        return self

    def select(self, *_):
        return self

    def eq(self, coluna, valor):
        self.filtros.append(("eq", coluna, valor))
        return self

    def neq(self, coluna, valor):
        self.filtros.append(("neq", coluna, valor))
        return self

    def like(self, coluna, padrao):
        self.filtros.append(("like", coluna, padrao))
        return self

    def limit(self, _):
        return self

    def execute(self):
        if self.acao == "delete":
            self.banco.exclusoes.append(self.tabela)
            self.banco.filtros[self.tabela] = list(self.filtros)
            removido = [{"id": EMPRESA_ID}] if self.tabela == "empresas_monitoradas" else []
            return SimpleNamespace(data=removido)
        return SimpleNamespace(data=[])


class FakeSupabase:
    """Registra a ordem dos deletes; `rpc` devolve `resposta_rpc` ou levanta erro."""

    def __init__(self, resposta_rpc=None):
        self.resposta_rpc = resposta_rpc
        self.chamada_rpc = None
        self.exclusoes = []
        self.filtros = {}

    def rpc(self, nome, parametros):
        self.chamada_rpc = (nome, parametros)
        resposta = self.resposta_rpc
        if resposta is None:
            raise RuntimeError("RPC indisponível")
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=resposta))

    def table(self, nome):
        return Consulta(self, nome)


class ExclusaoDeClienteTests(unittest.TestCase):
    def setUp(self):
        self.estado = {}
        self.limpezas = []
        self.original = (xml_engine.st, xml_engine._limpador_cache_dados)
        xml_engine.st = SimpleNamespace(session_state=self.estado)
        xml_engine.registrar_limpador_cache_dados(lambda: self.limpezas.append(1))
        xml_engine.limpar_cache_recursos_ausentes()
        self.addCleanup(self._restaurar)

    def _restaurar(self):
        xml_engine.st = self.original[0]
        xml_engine.registrar_limpador_cache_dados(self.original[1])
        xml_engine.limpar_cache_recursos_ausentes()

    def test_rpc_atomica_exclui_o_cliente(self):
        banco = FakeSupabase({"deleted": True, "empresa_id": EMPRESA_ID})
        resultado = xml_engine.excluir_empresa_supabase(banco, EMPRESA_ID)
        self.assertTrue(resultado["deleted"])
        self.assertTrue(resultado["via_rpc"])
        self.assertEqual(
            banco.chamada_rpc,
            ("excluir_cliente_cascade", {"p_empresa_id": EMPRESA_ID}),
        )

    def test_rpc_sem_exclusao_cai_no_fallback_e_remove_o_cadastro_por_ultimo(self):
        banco = FakeSupabase({"deleted": False, "empresa_id": EMPRESA_ID})
        resultado = xml_engine.excluir_empresa_supabase(banco, EMPRESA_ID)
        self.assertTrue(resultado["via_fallback"])
        self.assertEqual(banco.exclusoes[-1], "empresas_monitoradas")

    def test_fallback_direto_limpa_dependentes_antes_do_cliente(self):
        banco = FakeSupabase()
        resultado = xml_engine.excluir_empresa_supabase(banco, EMPRESA_ID)
        self.assertTrue(resultado["via_fallback"])
        dependentes = [t for t in banco.exclusoes if t != "empresas_monitoradas"]
        self.assertEqual(dependentes[:5], TABELAS_LIMPEZA)
        self.assertIn("empresa_cnpjs_autorizados", dependentes)
        self.assertEqual(banco.exclusoes[-1], "empresas_monitoradas")

    def test_id_que_nao_e_uuid_nunca_chega_ao_banco(self):
        banco = FakeSupabase()
        with self.assertRaises(ValueError):
            xml_engine.excluir_empresa_supabase(banco, "88")
        with self.assertRaises(ValueError):
            xml_engine.limpar_dados_empresa_supabase(banco, "88")
        self.assertEqual(banco.exclusoes, [])
        self.assertIsNone(banco.chamada_rpc)

    def test_zerar_notas_preserva_o_perfil_tributario_e_outras_empresas(self):
        banco = FakeSupabase()
        self.estado.update({
            "_dados_hidratados_por_empresa": {
                EMPRESA_ID: {"notas": [{"id": "n1"}]},
                OUTRA_EMPRESA_ID: {"notas": [{"id": "n2"}]},
            },
            f"fallback_notas_{EMPRESA_ID}": [{"id": "n1"}],
            f"perfil_tributario_{EMPRESA_ID}": {"fator_reducao": 0.7},
            f"perfil_tributario_select_{EMPRESA_ID}": "reducao",
            f"form_liquidacao_{EMPRESA_ID}_op": "valor",
        })
        xml_engine.limpar_dados_empresa_supabase(banco, EMPRESA_ID)
        self.assertEqual(banco.exclusoes[:5], TABELAS_LIMPEZA)
        self.assertNotIn("empresas_monitoradas", banco.exclusoes)
        self.assertIn(
            ("neq", "record_key", ppf.CHAVE_LEDGER_PERFIL_TRIBUTARIO),
            banco.filtros["praesidium_empresa_mirror"],
        )
        self.assertNotIn(f"fallback_notas_{EMPRESA_ID}", self.estado)
        self.assertNotIn(f"form_liquidacao_{EMPRESA_ID}_op", self.estado)
        self.assertIn(f"perfil_tributario_{EMPRESA_ID}", self.estado)
        self.assertIn(f"perfil_tributario_select_{EMPRESA_ID}", self.estado)
        cache = self.estado["_dados_hidratados_por_empresa"]
        self.assertNotIn(EMPRESA_ID, cache)
        self.assertIn(OUTRA_EMPRESA_ID, cache)
        self.assertTrue(self.limpezas)


class SnapshotDeHidratacaoTests(unittest.TestCase):
    def setUp(self):
        self.original = xml_engine.hidratar_dados_empresa_supabase
        self.addCleanup(
            setattr, xml_engine, "hidratar_dados_empresa_supabase", self.original
        )

    def test_falha_temporaria_reutiliza_o_ultimo_snapshot_valido(self):
        snapshot = {"notas": [{"id": "n1"}], "itens": [], "devolucoes": [], "eventos_split": []}
        empresa = {"id": EMPRESA_ID}
        cache = {}

        xml_engine.hidratar_dados_empresa_supabase = lambda *_: snapshot
        self.assertEqual(
            xml_engine.hidratar_dados_empresa_com_cache(object(), EMPRESA_ID, empresa, cache),
            snapshot,
        )

        def falha_temporaria(*_):
            raise RuntimeError("erro de rede simulado")

        xml_engine.hidratar_dados_empresa_supabase = falha_temporaria
        self.assertEqual(
            xml_engine.hidratar_dados_empresa_com_cache(object(), EMPRESA_ID, empresa, cache),
            snapshot,
        )

        cache.clear()
        vazio = xml_engine.hidratar_dados_empresa_com_cache(object(), EMPRESA_ID, empresa, cache)
        self.assertEqual(vazio, {"notas": [], "itens": [], "devolucoes": [], "eventos_split": []})


if __name__ == "__main__":
    unittest.main()

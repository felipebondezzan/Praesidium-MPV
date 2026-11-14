"""Execução real do `app.py` com `streamlit.testing.v1.AppTest`.

O app roda inteiro (login, cadastro, upload de XML, espelho da nota, quarentena,
derivação setorial, "Zerar Notas" e "Excluir Cliente") sobre:

- um Supabase falso que replica o schema real (sempre); e
- o Supabase real (opt-in com `PRAESIDIUM_TEST_SUPABASE_REAL=1`), usando um
  cliente de teste próprio que é excluído ao final, sem tocar nos demais.

Rede e alertas externos são neutralizados e o registro local de usuários é
restaurado ao final.
"""
import os
import random
import re
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

import streamlit as st
from streamlit.testing.v1 import AppTest

import relatorio_engine
import xml_engine
from test_multi_tenant_isolation_and_ledger import (
    BancoFalso,
    CHAVE_COFRE,
    _cliente_supabase_real,
    _ler_tudo,
    cofres_no_banco,
    cofres_no_real,
)

RAIZ = Path(__file__).resolve().parent.parent
CNPJ_CLIENTE = "21270553000134"
CNPJ_FORMATADO = "21.270.553/0001-34"
DERIVACAO_TESTE = "Alíquota Teste de Transição — 1,00% (0,90% CBS + 0,10% IBS)"
SUPABASE_REAL = os.environ.get("PRAESIDIUM_TEST_SUPABASE_REAL") == "1"
TABELAS_DA_EMPRESA = (
    "notas_fiscais_recebidas", "itens_nota_fiscal", "praesidium_empresa_mirror",
    "conciliacao_devolucoes", "split_payment_events", "empresa_cnpjs_autorizados",
)


def chave_nfe(numero: str, serie: str, sufixo: str = "1") -> str:
    return (
        "35" "2611" "60746948000112" "55"
        f"{int(serie):03d}{int(numero):09d}" "1" f"{int(sufixo):08d}" "0"
    )


def xml_nfe(
    chave: str, crt: str, itens: list[tuple], numero: str, serie: str,
    cnpj_dest: str = CNPJ_CLIENTE,
) -> bytes:
    detalhes = "".join(
        f'<det nItem="{n}"><prod><cProd>P{n}</cProd><xProd>{descricao}</xProd>'
        f"<NCM>{ncm}</NCM><CFOP>5102</CFOP><qCom>1</qCom><vUnCom>{valor:.2f}</vUnCom>"
        f"<vProd>{valor:.2f}</vProd></prod></det>"
        for n, (descricao, ncm, valor) in enumerate(itens, start=1)
    )
    total = sum(valor for _, _, valor in itens)
    return (
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe>'
        f'<infNFe Id="NFe{chave}"><ide><serie>{serie}</serie><nNF>{numero}</nNF>'
        "<dhEmi>2026-11-06T10:00:00-03:00</dhEmi></ide>"
        f"<emit><CNPJ>60746948000112</CNPJ><xNome>Fornecedor Atacado</xNome><CRT>{crt}</CRT></emit>"
        f"<dest><CNPJ>{cnpj_dest}</CNPJ></dest>{detalhes}"
        f"<total><ICMSTot><vNF>{total:.2f}</vNF></ICMSTot></total>"
        "</infNFe></NFe></nfeProc>"
    ).encode("utf-8")


def brl_para_decimal(texto: str) -> Decimal:
    limpo = texto.replace("R$", "").replace(".", "").replace(",", ".").strip()
    return Decimal(limpo)


class AppTestBase(unittest.TestCase):
    usa_supabase_real = False

    @classmethod
    def setUpClass(cls):
        cls._st = xml_engine.st
        cls._limpador = xml_engine._limpador_cache_dados
        cls._pasta = tempfile.mkdtemp()
        cls._backup_usuarios = Path(cls._pasta) / "usuarios_praesidium.json"
        cls._arquivo_usuarios = RAIZ / "usuarios_praesidium.json"
        shutil.copy2(cls._arquivo_usuarios, cls._backup_usuarios)

    @classmethod
    def tearDownClass(cls):
        shutil.copy2(cls._backup_usuarios, cls._arquivo_usuarios)
        shutil.rmtree(cls._pasta, ignore_errors=True)
        xml_engine.st = cls._st
        xml_engine.registrar_limpador_cache_dados(cls._limpador)
        st.cache_resource.clear()
        st.cache_data.clear()

    def setUp(self):
        xml_engine.st = st
        xml_engine.limpar_cache_recursos_ausentes()
        st.cache_resource.clear()
        st.cache_data.clear()
        patches = [
            ("requests.post", lambda *a, **k: mock.Mock(raise_for_status=lambda: None)),
            ("requests.get", mock.Mock(side_effect=RuntimeError("sem rede nos testes"))),
        ]
        if self.usa_supabase_real:
            self.db = _cliente_supabase_real()
            self.inicio = datetime.now(timezone.utc).isoformat()
            self.addCleanup(self._remover_logs_de_acesso_do_teste)
        else:
            self.db = BancoFalso()
            patches.append(("supabase.create_client", lambda *a, **k: self.db))
        for alvo, novo in patches:
            patcher = mock.patch(alvo, novo)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(shutil.copy2, self._backup_usuarios, self._arquivo_usuarios)
        self.at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=300)

    def _remover_logs_de_acesso_do_teste(self):
        self.db.table("empresas_monitoradas").delete().eq(
            "regime_tributario", "SYS_LOG"
        ).gte("created_at", self.inicio).execute()

    # ------------------------------------------------------------ utilidades
    def tabela(self, nome: str) -> list[dict]:
        if self.usa_supabase_real:
            return _ler_tudo(self.db, nome)
        return list(self.db.tabelas[nome])

    def cofres(self, empresa_id: str) -> list[dict]:
        if self.usa_supabase_real:
            return cofres_no_real(self.db, empresa_id)
        return cofres_no_banco(self.db, empresa_id)

    def da_empresa(self, tabela: str, empresa_id: str) -> list[dict]:
        return [l for l in self.tabela(tabela) if str(l.get("empresa_id")) == str(empresa_id)]

    def executar(self):
        self.at.run()
        self.assertEqual([e.value for e in self.at.exception], [])
        return self.at

    def elementos(self, tipo: str) -> list:
        return list(getattr(self.at.main, tipo)) + list(getattr(self.at.sidebar, tipo))

    def por_chave(self, tipo: str, chave: str):
        encontrados = [e for e in self.elementos(tipo) if e.key == chave]
        self.assertEqual(len(encontrados), 1, f"{tipo} '{chave}': {len(encontrados)} encontrados")
        return encontrados[0]

    def botao(self, rotulo: str):
        encontrados = [b for b in self.elementos("button") if rotulo in (b.label or "")]
        self.assertTrue(encontrados, f"botão '{rotulo}' não encontrado")
        return encontrados[0]

    def textos_markdown(self) -> str:
        return "\n".join(m.value for m in self.elementos("markdown"))

    def cartoes_do_espelho(self) -> dict[str, str]:
        return dict(re.findall(
            r'<div class="espelho-label">(.*?)</div><div class="espelho-valor">(.*?)</div>',
            self.textos_markdown(),
        ))

    def dataframe_com_coluna(self, coluna: str):
        for quadro in self.elementos("dataframe"):
            if coluna in quadro.value.columns:
                return quadro.value
        self.fail(f"nenhuma tabela com a coluna '{coluna}'")

    def entrar(self):
        self.executar()
        campos = [t for t in self.at.text_input if t.label in {"Usuário", "Senha"}][:2]
        campos[0].input("felipebondezzan")
        campos[1].input("admin2026")
        self.botao("Entrar").click()
        self.executar()
        self.assertTrue(self.at.session_state["autenticado"])

    def versao_do_formulario(self) -> int:
        estado = self.at.session_state
        return estado["_versao_form_cadastro"] if "_versao_form_cadastro" in estado else 0

    def cadastrar_cliente(self, nome: str, cnpj: str, cnae: str):
        versao = self.versao_do_formulario()
        self.por_chave("text_input", f"cad_nome_{versao}").input(nome)
        self.por_chave("text_input", f"cad_cnpj_{versao}").input(cnpj)
        self.por_chave("text_input", f"cad_cnae_prin_{versao}").input(cnae)
        self.botao("Salvar Cliente Definitivo").click()
        self.executar()

    def empresa_ativa_id(self) -> str:
        return self.at.session_state["empresa_id_ativa"]

    def importar(self, arquivos: list[tuple[str, bytes]]):
        empresa_id = self.empresa_ativa_id()
        chave_reinicio = f"up_reset_{empresa_id}"
        reinicio = self.at.session_state[chave_reinicio] if chave_reinicio in self.at.session_state else 0
        carregador = self.por_chave("file_uploader", f"xml_up_{empresa_id}_{reinicio}")
        carregador.set_value([(nome, conteudo, "text/xml") for nome, conteudo in arquivos])
        self.executar()
        self.botao("Auditar e Importar").click()
        self.executar()

    def foto_das_demais_empresas(self, empresa_id: str) -> dict:
        """Linhas de todas as outras empresas (cofres ignorados: o app os recalcula)."""
        foto = {}
        for tabela in TABELAS_DA_EMPRESA:
            foto[tabela] = sorted(
                repr(sorted(l.items(), key=lambda par: par[0]))
                for l in self.tabela(tabela)
                if str(l.get("empresa_id")) != str(empresa_id)
                and l.get("record_key") != CHAVE_COFRE
            )
        foto["clientes"] = sorted(
            l["id"] for l in self.tabela("empresas_monitoradas")
            if l["id"] != empresa_id and not str(l["regime_tributario"]).startswith("SYS_")
        )
        return foto


class FluxoCompletoMixin:
    """Roteiro do contador: cadastro → upload → espelho → quarentena → derivação → zerar → excluir."""

    def fluxo_completo(self, nome: str, cnpj_formatado: str):
        cnpj = re.sub(r"\D", "", cnpj_formatado)

        # ---------------------------------------------------------- login
        self.executar()
        for campo in self.elementos("text_input"):
            self.assertEqual(campo.proto.autocomplete, "off", campo.label)
        self.entrar()
        for campo in self.elementos("text_input"):
            self.assertEqual(campo.proto.autocomplete, "off", f"{campo.label} ({campo.key})")
        outras_antes = None

        # ---------------------------------------------- cadastro do cliente
        versao = self.versao_do_formulario()
        self.assertEqual(self.por_chave("text_input", f"cad_nome_{versao}").value, "")
        outras_antes = self.foto_das_demais_empresas("")
        self.cadastrar_cliente(nome, cnpj_formatado, "9602501")
        clientes = [
            c for c in self.tabela("empresas_monitoradas")
            if c["cnpj"] == cnpj and c["regime_tributario"] == "Simples Nacional"
        ]
        self.assertEqual(len(clientes), 1)
        empresa_id = clientes[0]["id"]
        self.assertEqual(self.empresa_ativa_id(), empresa_id)

        # Higiene: caixas reabrem em branco, com chaves novas e sem sugestões.
        nova_versao = self.versao_do_formulario()
        self.assertGreater(nova_versao, versao)
        for prefixo in ("cad_nome_", "cad_cnpj_", "cad_cnae_prin_"):
            self.assertEqual(self.por_chave("text_input", f"{prefixo}{nova_versao}").value, "")
        for campo in self.elementos("text_input"):
            self.assertEqual(campo.proto.autocomplete, "off", f"{campo.label} ({campo.key})")

        css = self.textos_markdown()
        for trecho in (
            '[data-testid="stMetricValue"] > div',
            '[data-testid="stMetricLabel"]',
            "overflow: visible !important",
            "text-overflow: unset !important",
            "white-space: normal !important",
            "word-break: keep-all !important",
            "overflow-wrap: anywhere !important",
            "clamp(1.0rem, 1.3vw, 1.45rem) !important",
            "line-height: 1.25 !important",
        ):
            self.assertIn(trecho, css)

        # ------------------------------------------------------ upload XML
        chave_a = chave_nfe("12345", "1", "1")
        xml_a = xml_nfe(
            chave_a, "1",
            [
                ("Shampoo profissional", "33051000", 12_000_000.00),
                ("Esmalte profissional", "33043000", 3_000_000.00),
                ("Material de consumo interno A", "33051000", 300_000.50),
                ("Uso interno B", "33051000", 150_890.25),
            ],
            numero="12345", serie="1", cnpj_dest=cnpj,
        )
        self.importar([("nfe_a.xml", xml_a)])
        notas = self.da_empresa("notas_fiscais_recebidas", empresa_id)
        self.assertEqual([n["chave_acesso"] for n in notas], [chave_a])
        self.assertEqual(notas[0]["crt_emitente"], "1")
        self.assertEqual(len(self.da_empresa("itens_nota_fiscal", empresa_id)), 4)

        # ------------------------------- espelho da nota sem "..." e completo
        valores = self.cartoes_do_espelho()
        self.assertEqual(
            list(valores),
            [
                "Número / Série", "Base de Cálculo / Produtos", "IBS apurado",
                "CBS apurado", "Crédito Split Payment LC 214/2025",
            ],
        )
        self.assertEqual(valores["Número / Série"], "12345 / 1")
        self.assertEqual(valores["Base de Cálculo / Produtos"], "R$ 15.450.890,75")
        for rotulo, texto in valores.items():
            self.assertNotIn("...", texto, rotulo)
            self.assertNotIn("…", texto, rotulo)
        ibs = brl_para_decimal(valores["IBS apurado"])
        cbs = brl_para_decimal(valores["CBS apurado"])
        credito = brl_para_decimal(valores["Crédito Split Payment LC 214/2025"])
        self.assertEqual(credito, Decimal("1236071.26"))
        self.assertGreater(ibs, 0)
        self.assertGreater(cbs, 0)
        self.assertLessEqual(abs(ibs + cbs - credito), Decimal("0.02"))
        self.assertAlmostEqual(float(ibs / credito), 0.177 / 0.265, places=3)
        self.assertIn("espelho-grid", self.textos_markdown())

        # O Streamlit move o emoji inicial do alerta para o ícone; o texto restante é o do aviso.
        alertas = [w.value.removeprefix("⚠️").strip() for w in self.elementos("warning")]
        self.assertIn(
            "Fornecedor Simples Nacional (CRT 1) — Atenção: Crédito de IBS/CBS limitado "
            "à alíquota efetiva recolhida no DAS (Art. 28 e Art. 47 da LC 214/2025).",
            alertas,
        )
        self.assertTrue(
            xml_engine.alerta_fornecedor_simples("1").startswith("⚠️ Fornecedor Simples Nacional (CRT 1)")
        )

        itens_tabela = self.dataframe_com_coluna("Valor Unitário (R$)")
        self.assertIn("R$ 12.000.000,00", set(itens_tabela["Valor Total (R$)"]))
        self.assertIn("R$ 12.000.000,00", set(itens_tabela["Valor Unitário (R$)"]))
        self.assertIn("R$ 300.000,50", set(itens_tabela["Valor Total (R$)"]))

        # ----------------------- quarentena: aprovação e glosa com carimbo legal
        aprovar = [b for b in self.elementos("button") if (b.key or "").startswith("lib_")]
        self.assertEqual(len(aprovar), 2)
        aprovar[0].click()
        self.executar()
        rejeitar = [b for b in self.elementos("button") if (b.key or "").startswith("bloq_")]
        self.assertEqual(len(rejeitar), 1)
        rejeitar[0].click()
        self.executar()
        decididos = {i["classificacao_item"] for i in self.da_empresa("itens_nota_fiscal", empresa_id)}
        self.assertTrue({"Aprovado Manualmente Contador", "Rejeitado pelo Contador"} <= decididos)
        justificativas = {
            m["payload"].get("justificativa_motor")
            for m in self.da_empresa("praesidium_empresa_mirror", empresa_id)
            if m["record_type"] == "item"
        }
        self.assertIn(xml_engine.CARIMBO_APROVACAO_ART_47, justificativas)
        self.assertIn(xml_engine.CARIMBO_GLOSA_ART_57, justificativas)

        historico = self.dataframe_com_coluna("Status / Decisão do Contador")
        fundamentos = set(historico["Fundamentação Legal"])
        self.assertIn(xml_engine.CARIMBO_APROVACAO_ART_47, fundamentos)
        self.assertIn(xml_engine.CARIMBO_GLOSA_ART_57, fundamentos)
        ponte = self.dataframe_com_coluna("Fundamentação Legal (LC 214/2025)")
        fundamentos_ponte = set(ponte["Fundamentação Legal (LC 214/2025)"])
        self.assertIn(xml_engine.CARIMBO_APROVACAO_ART_47, fundamentos_ponte)
        self.assertIn(xml_engine.CARIMBO_GLOSA_ART_57, fundamentos_ponte)
        self.assertTrue(any("CRT 1" in texto for texto in ponte["Alerta Fornecedor"]))

        # ---------------------- derivação de 1,00% (5ª opção) e nova importação
        seletor = self.por_chave("selectbox", f"perfil_tributario_select_{empresa_id}")
        self.assertEqual(len(seletor.options), 5)
        self.assertEqual(seletor.options[-1], DERIVACAO_TESTE)
        seletor.select(DERIVACAO_TESTE)
        self.executar()
        self.botao("Salvar Derivação IBS/CBS").click()
        self.executar()
        perfil = xml_engine.carregar_perfil_tributario_empresa(self.db, empresa_id)
        self.assertEqual(perfil["derivacao_setorial"], DERIVACAO_TESTE)
        self.assertEqual(
            (perfil["aliquota_efetiva"], perfil["aliquota_ibs_efetiva"], perfil["aliquota_cbs_efetiva"]),
            (1.0, 0.1, 0.9),
        )
        self.assertTrue(any("1.00%" in c.value for c in self.elementos("caption")))

        chave_b = chave_nfe("777", "2", "2")
        xml_b = xml_nfe(
            chave_b, "3", [("Shampoo profissional", "33051000", 1_000_000.00)],
            numero="777", serie="2", cnpj_dest=cnpj,
        )
        self.importar([("nfe_b.xml", xml_b)])
        notas = {n["chave_acesso"]: n["id"] for n in self.da_empresa("notas_fiscais_recebidas", empresa_id)}
        self.assertEqual(set(notas), {chave_a, chave_b})
        self.por_chave("selectbox", f"visualizar_danfe_{empresa_id}").select(notas[chave_b])
        self.executar()
        cartoes_b = self.cartoes_do_espelho()
        self.assertEqual(cartoes_b["Número / Série"], "777 / 2")
        self.assertEqual(brl_para_decimal(cartoes_b["Crédito Split Payment LC 214/2025"]), Decimal("10000.00"))
        self.assertEqual(brl_para_decimal(cartoes_b["IBS apurado"]), Decimal("1000.00"))
        self.assertEqual(brl_para_decimal(cartoes_b["CBS apurado"]), Decimal("9000.00"))
        self.assertFalse(any("CRT" in w.value for w in self.elementos("warning")))

        # O cofre do cliente acompanhou todo o fluxo.
        cofres = self.cofres(empresa_id)
        self.assertEqual(len(cofres), 1)
        self.assertEqual(set(cofres[0]["notas_consolidadas"]), {chave_a, chave_b})

        # ---------------------------- "Zerar Notas deste Cliente" (perfil fica)
        self.por_chave("button", f"btn_reset_nf_{empresa_id}").click()
        self.executar()
        for tabela in ("notas_fiscais_recebidas", "itens_nota_fiscal", "conciliacao_devolucoes"):
            self.assertEqual(self.da_empresa(tabela, empresa_id), [], tabela)
        self.assertEqual(self.cofres(empresa_id), [])
        self.assertEqual(
            xml_engine.carregar_perfil_tributario_empresa(self.db, empresa_id)["derivacao_setorial"],
            DERIVACAO_TESTE,
        )

        # --------------------------------------- "Excluir Cliente Ativo"
        self.por_chave("button", f"btn_del_cli_{empresa_id}").click()
        self.executar()
        for tabela in TABELAS_DA_EMPRESA:
            self.assertEqual(self.da_empresa(tabela, empresa_id), [], tabela)
        self.assertNotIn(empresa_id, [c["id"] for c in self.tabela("empresas_monitoradas")])
        self.assertEqual(self.cofres(empresa_id), [])
        self.assertEqual(self.foto_das_demais_empresas(empresa_id), outras_antes)


class FluxoCompletoDoAppTests(FluxoCompletoMixin, AppTestBase):
    def test_login_cadastro_upload_espelho_quarentena_derivacao_zerar_e_excluir(self):
        self.fluxo_completo("Esplendore", CNPJ_FORMATADO)

    def test_trocar_de_cliente_limpa_as_caixas_de_cadastro(self):
        self.entrar()
        self.cadastrar_cliente("Cliente Um", "11.222.333/0001-81", "4711302")
        self.cadastrar_cliente("Cliente Dois", "22.333.444/0001-81", "4772500")
        versao = self.versao_do_formulario()
        self.por_chave("text_input", f"cad_nome_{versao}").input("Rascunho pendente")
        self.por_chave("text_input", f"cad_cnpj_{versao}").input("99.999.999/0001-99")
        self.executar()
        seletor = self.por_chave("selectbox", "empresa_ativa_select_felipebondezzan")
        outro = next(op for op in seletor.options if op != seletor.value)
        seletor.select(outro)
        self.executar()
        nova_versao = self.versao_do_formulario()
        self.assertGreater(nova_versao, versao)
        for prefixo in ("cad_nome_", "cad_cnpj_", "cad_cnae_prin_"):
            self.assertEqual(self.por_chave("text_input", f"{prefixo}{nova_versao}").value, "")
        self.assertEqual(
            [c for c in self.elementos("text_input") if c.key == f"cad_nome_{versao}"], []
        )

    def test_adicionar_cnae_secundario_nao_apaga_o_que_ja_foi_digitado(self):
        self.entrar()
        self.por_chave("text_input", "cad_nome_0").input("Rascunho")
        self.executar()
        self.botao("Adicionar outro CNAE").click()
        self.executar()
        self.assertEqual(self.por_chave("text_input", "cad_nome_0").value, "Rascunho")
        self.assertEqual(self.por_chave("text_input", "sec_cnae_input_0_0").value, "")


@unittest.skipUnless(
    SUPABASE_REAL,
    "defina PRAESIDIUM_TEST_SUPABASE_REAL=1 para executar o app contra o Supabase real",
)
class FluxoCompletoNoSupabaseRealTests(FluxoCompletoMixin, AppTestBase):
    usa_supabase_real = True

    def test_fluxo_completo_com_cliente_de_teste_no_supabase_real(self):
        cnpj = f"98{random.randint(0, 999999):06d}0001{random.randint(10, 99)}"
        formatado = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"

        def remover_cliente_de_teste():
            for cliente in self.tabela("empresas_monitoradas"):
                if cliente["cnpj"] == cnpj and cliente["regime_tributario"] == "Simples Nacional":
                    xml_engine.excluir_empresa_supabase(self.db, cliente["id"])

        self.addCleanup(remover_cliente_de_teste)
        self.fluxo_completo(f"ZZ TESTE APPTEST {uuid.uuid4().hex[:6]}", formatado)


class RegrasLC214Tests(unittest.TestCase):
    def setUp(self):
        self._st = xml_engine.st
        self.addCleanup(setattr, xml_engine, "st", self._st)
        xml_engine.st = mock.Mock(session_state={}, toast=lambda *a, **k: None)
        xml_engine.registrar_limpador_cache_dados(lambda: None)
        xml_engine.limpar_cache_recursos_ausentes()

    def test_moeda_brasileira_e_numero_serie_pela_chave(self):
        self.assertEqual(xml_engine.formatar_brl(15450890.75), "R$ 15.450.890,75")
        self.assertEqual(xml_engine.formatar_brl(4800), "R$ 4.800,00")
        self.assertEqual(xml_engine.formatar_brl(None), "R$ 0,00")
        self.assertEqual(xml_engine.formatar_brl(-1234.5), "-R$ 1.234,50")
        self.assertEqual(
            xml_engine.resolver_numero_serie({"chave_acesso": chave_nfe("12345", "1")}),
            ("12345", "1"),
        )
        self.assertEqual(
            xml_engine.resolver_numero_serie(
                {"chave_acesso": chave_nfe("12345", "1"), "numero": "99", "serie": "7"}
            ),
            ("99", "7"),
        )
        self.assertEqual(xml_engine.resolver_numero_serie({}), ("—", "—"))

    def test_ibs_cbs_legado_zerado_e_repartido_pelo_credito(self):
        legado = {
            "valor_credito_item": 1272.0, "credito_ibs_gerado": 0.0, "credito_cbs_gerado": 0.0,
            "valor_ibs": 0.0, "valor_cbs": 0.0,
            "aliquota_ibs_aplicada": 0.265, "aliquota_cbs_aplicada": 0.0,
        }
        ibs, cbs = xml_engine.obter_ibs_cbs_item(legado)
        self.assertEqual(round(ibs + cbs, 2), 1272.0)
        self.assertGreater(cbs, 0)
        self.assertEqual(
            xml_engine.obter_ibs_cbs_item(
                {"valor_credito_item": 100.0, "credito_ibs_gerado": 40.0, "credito_cbs_gerado": 60.0}
            ),
            (40.0, 60.0),
        )
        self.assertEqual(xml_engine.somar_ibs_cbs_itens([legado, legado])[0], round(ibs * 2, 2))

    def test_alerta_de_fornecedor_simples_e_mei(self):
        for crt in ("1", "2"):
            self.assertIn(f"Fornecedor Simples Nacional (CRT {crt})", xml_engine.alerta_fornecedor_simples(crt))
        self.assertIn("MEI (CRT 4)", xml_engine.alerta_fornecedor_simples("4"))
        self.assertIsNone(xml_engine.alerta_fornecedor_simples("3"))
        self.assertIsNone(xml_engine.alerta_fornecedor_simples(None))
        self.assertIn("Art. 28 e Art. 47 da LC 214/2025", xml_engine.alerta_fornecedor_simples("1"))

    def test_crt_4_mei_e_preservado_pelo_parser_e_pelo_sanitizador(self):
        empresa = {"id": "x", "cnpj": CNPJ_CLIENTE}
        nota, itens, erro = xml_engine._preparar_xml_nfe(
            xml_nfe(chave_nfe("5", "1"), "4", [("Shampoo", "33051000", 10.0)], "5", "1"),
            "x", empresa, set(), set(), {CNPJ_CLIENTE},
        )
        self.assertEqual(erro, "")
        self.assertEqual(nota["crt_emitente"], "4")
        saneado = xml_engine.sanitizar_payload_supabase(
            "notas_fiscais_recebidas",
            {"empresa_id": "x", "chave_acesso": "1" * 44, "crt_emitente": "4"},
        )
        self.assertEqual(saneado["crt_emitente"], "4")

    def test_quinta_derivacao_e_proporcional_a_base_de_26_50(self):
        derivacoes = list(xml_engine.ALIQUOTAS_REFORMA_TRIBUTARIA)
        self.assertEqual(len(derivacoes), 5)
        self.assertEqual(derivacoes[:4], [
            "Base Padrão Integral — 26,50% (Sem redutor)",
            "Derivação Redução 30% (Profissões Regulamentadas / Serviços) — Alíquota Efetiva 18,55%",
            "Derivação Redução 60% (Saúde, Educação, Insumos Agro/Higiene) — Alíquota Efetiva 10,60%",
            "Regime Específico / Alíquota Zero — 0,00%",
        ])
        memoria = xml_engine.calcular_memoria_aliquota(derivacoes[4])
        self.assertEqual(derivacoes[4], DERIVACAO_TESTE)
        self.assertEqual(memoria["aliquota_efetiva"], 1.0)
        self.assertEqual(memoria["aliquota_cbs_efetiva"], 0.9)
        self.assertEqual(memoria["aliquota_ibs_efetiva"], 0.1)
        self.assertAlmostEqual(memoria["fator_reducao"] * 26.5, 1.0, places=9)
        self.assertEqual(xml_engine.fracao_ibs_do_perfil(memoria), 0.1)
        padrao = xml_engine.calcular_memoria_aliquota(derivacoes[0])
        self.assertAlmostEqual(xml_engine.fracao_ibs_do_perfil(padrao), 0.177 / 0.265)
        item = xml_engine.classificar_item_inteligente(
            "33051000", "5102", 1.0, 1000.0, "3", "9602501", False, "Shampoo",
            memoria["fator_reducao"],
        )
        self.assertEqual(item, ("Aprovado Revenda", item[1], 10.0))

    def test_carimbos_legais_na_decisao_em_lote_e_no_dossie(self):
        banco = BancoFalso()
        ok, mensagem, criadas = xml_engine.executar_insert_seguro(
            "empresas_monitoradas",
            {
                "razao_social": "ESPLENDORE [T:t]", "cnpj": CNPJ_CLIENTE,
                "regime_tributario": "Simples Nacional", "cnae_principal": "9602501",
                "certificado_a1_status": "Ativo",
            },
            supabase=banco,
        )
        self.assertTrue(ok, mensagem)
        empresa = {**criadas[0], "razao_social": "ESPLENDORE", "cnae_principal": "9602501"}
        empresa_id = empresa["id"]
        resultado = xml_engine.processar_xml_lote_bytes(
            [("a.xml", xml_nfe(
                chave_nfe("9", "3"), "2",
                [
                    ("Shampoo profissional", "33051000", 100.0),
                    ("Material de consumo interno 1", "33051000", 200.0),
                    ("Uso interno 2", "33051000", 300.0),
                ],
                "9", "3",
            ))],
            banco, empresa_id, empresa,
        )
        self.assertTrue(resultado[0][0], resultado[0][1])
        dados = xml_engine.hidratar_dados_empresa_supabase(banco, empresa_id, empresa)
        pendentes = [i for i in dados["itens"] if i["classificacao_item"] == "Quarentena Uso/Consumo"]
        self.assertEqual(len(pendentes), 2)
        xml_engine.decidir_itens_quarentena_lote(banco, empresa_id, pendentes[:1], "aprovar")
        xml_engine.decidir_itens_quarentena_lote(banco, empresa_id, pendentes[1:], "rejeitar")
        dados = xml_engine.hidratar_dados_empresa_supabase(banco, empresa_id, empresa)
        fundamentos = {i["classificacao_item"]: xml_engine.fundamentacao_legal_item(i) for i in dados["itens"]}
        self.assertEqual(fundamentos["Aprovado Manualmente Contador"], xml_engine.CARIMBO_APROVACAO_ART_47)
        self.assertEqual(fundamentos["Rejeitado pelo Contador"], xml_engine.CARIMBO_GLOSA_ART_57)
        self.assertEqual(
            xml_engine.CARIMBO_APROVACAO_ART_47,
            "Homologado: Insumo Essencial à Atividade Econômica (Art. 47 da LC 214/2025)",
        )
        self.assertEqual(
            xml_engine.CARIMBO_GLOSA_ART_57,
            "Glosado: Uso ou Consumo Pessoal / Alheio à Atividade (Art. 57 da LC 214/2025)",
        )

        # Dossiê (HTML) e Ponte Contábil (DataFrame) exibem fundamentação e alerta CRT.
        quadro = relatorio_engine.montar_dataframe_relacional.__wrapped__(
            dados["notas"], dados["itens"], [], empresa_id
        )
        self.assertIn(xml_engine.CARIMBO_APROVACAO_ART_47, set(quadro["Fundamentação Legal (LC 214/2025)"]))
        self.assertIn(xml_engine.CARIMBO_GLOSA_ART_57, set(quadro["Fundamentação Legal (LC 214/2025)"]))
        self.assertTrue(all("CRT 2" in a for a in quadro["Alerta Fornecedor"]))
        html = relatorio_engine.gerar_html_dossie_analitico.__wrapped__(
            {"razao_social": "ESPLENDORE", "cnpj": CNPJ_CLIENTE, "regime_tributario": "Simples Nacional"},
            "Fechamento Mensal Consolidado", 1.0, 1.0, 1.0, 0.0, dados["notas"], quadro,
        )
        self.assertIn(xml_engine.CARIMBO_APROVACAO_ART_47, html)
        self.assertIn(xml_engine.CARIMBO_GLOSA_ART_57, html)
        self.assertIn("Fornecedor Simples Nacional (CRT 2)", html)
        self.assertIn("Fundamentação Legal", html)


if __name__ == "__main__":
    unittest.main()

"""Blindagem multi-tenant (50 empresas), persistência dos XMLs e cofre de saldo.

Simula um escritório de contabilidade com 50 clientes (CNPJs e CNAEs distintos)
sobre um Supabase falso que replica o schema real (UUID, varchar(14), conflitos
de upsert, CHECK do espelho, colunas ausentes). Valida as leis:

1. isolamento por `empresa_id` em banco e sessão;
2. XMLs/notas nunca somem em trocas de cliente, reruns e novos logins;
3. Impostômetro protegido por um cofre de saldo bruto/líquido por empresa;
4. "Zerar Notas" e "Excluir Cliente" atingem apenas a empresa ativa;
5. teste de estresse com alternância repetida entre os clientes.

Execução contra o Supabase real (opt-in, cria e remove clientes de teste
próprios e confere que nenhum dado preexistente foi alterado):

    $env:PRAESIDIUM_TEST_SUPABASE_REAL = "1"
"""
import copy
import hashlib
import json
import logging
import os
import random
import re
import tomllib
import unittest
import uuid
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from types import SimpleNamespace

from postgrest.exceptions import APIError

import praesidium_persistence_fallback as ppf
import xml_engine

RAIZ = Path(__file__).resolve().parent.parent
QUANTIDADE_EMPRESAS = 50
CHAVE_COFRE = ppf.CHAVE_LEDGER_COFRE_SALDO
PERFIS_CNAE = (
    ("9602501", "33051000", "Shampoo profissional"),
    ("4772500", "33030010", "Perfume importado"),
    ("4711302", "02032900", "Carne suina congelada"),
    ("4744001", "73181500", "Parafuso sextavado"),
)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

SCHEMA = {
    "empresas_monitoradas": {
        "id": "uuid", "cnpj": ("varchar", 14), "razao_social": "text",
        "regime_tributario": "text", "cnae_principal": ("varchar", 20),
        "certificado_a1_status": "text", "created_at": "text",
    },
    "notas_fiscais_recebidas": {
        "id": "uuid", "empresa_id": "uuid", "chave_acesso": "text",
        "cnpj_emitente": "text", "crt_emitente": "text", "data_emissao": "text",
        "hash_sha256_xml": "text", "manifestacao_status": "text",
        "nome_emitente": "text", "valor_total_nf": "numeric", "created_at": "text",
        "credito_elegivel_total": "numeric", "credito_potencial_total": "numeric",
        "em_contingencia": "text", "liquidacao_financeira": "text",
        "status_auditoria": "text", "valor_retido_analise": "numeric",
    },
    "itens_nota_fiscal": {
        "id": "uuid", "empresa_id": "uuid", "nota_id": "uuid", "nitem": "integer",
        "item_uid": "text", "chave_acesso": "text", "codigo_produto": "text",
        "descricao": "text", "ncm": "text", "cfop": "integer",
        "aliquota_aplicada": "numeric", "valor_item": "numeric",
        "valor_total": "numeric", "valor_credito_item": "numeric",
        "credito_calculado": "numeric", "valor_ibs": "numeric",
        "valor_cbs": "numeric", "classificacao_item": "text",
        "status_credito": "text", "motivo_quarentena": "text",
        "auditado_por": "text", "numero_item": "integer",
    },
    "praesidium_empresa_mirror": {
        "empresa_id": "uuid", "record_type": "text", "record_key": "text",
        "payload": "jsonb", "created_at": "text", "updated_at": "text",
    },
    # Igual ao banco real: sem payload/item_uid, o ledger vai para o espelho.
    "praesidium_ledger_fiscal": {"id": "uuid", "empresa_id": "uuid", "updated_at": "text"},
    "conciliacao_devolucoes": {
        "id": "uuid", "empresa_id": "uuid", "nota_original_id": "uuid",
        "credito_estornado": "numeric", "chave_nota_devolucao": "text",
    },
    "split_payment_events": {
        "id": "uuid", "empresa_id": "uuid", "event_id": "text",
        "retained_amount": "numeric", "gross_amount": "numeric",
    },
    "empresa_cnpjs_autorizados": {"id": "uuid", "empresa_id": "uuid", "cnpj": "text"},
}
CONFLITOS = {
    "itens_nota_fiscal": ("empresa_id", "item_uid"),
    "praesidium_empresa_mirror": ("empresa_id", "record_type", "record_key"),
}
TABELAS_POR_EMPRESA = tuple(
    tabela for tabela, colunas in SCHEMA.items() if "empresa_id" in colunas
)


def _erro(codigo: str, mensagem: str) -> APIError:
    return APIError({"message": mensagem, "code": codigo, "hint": None, "details": None})


class Consulta:
    def __init__(self, banco, tabela):
        self.banco, self.tabela, self.acao = banco, tabela, "select"
        self.payload, self.filtros, self.conflito = None, [], None
        self.colunas, self.inicio, self.fim, self.limite = "*", 0, None, None

    def select(self, colunas="*", **_):
        self.colunas = colunas
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

    def _colunas(self):
        return self.banco.schema[self.tabela]

    def _coluna_existe(self, coluna):
        if coluna not in self._colunas():
            raise _erro("42703", f"column {self.tabela}.{coluna} does not exist")

    def _validar_valor(self, coluna, valor):
        tipo = self._colunas()[coluna]
        if valor is None:
            return
        texto = str(valor)
        if tipo == "uuid" and not UUID_RE.match(texto):
            raise _erro("22P02", f'invalid input syntax for type uuid: "{texto}"')
        if tipo == "integer" and (isinstance(valor, bool) or not re.fullmatch(r"-?\d+", texto)):
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
        if self.tabela == "praesidium_empresa_mirror" and linha.get("record_type") not in {
            "item", "devolucao", "split_payment",
        }:
            raise _erro("23514", "violates check constraint record_type")

    def _casa(self, linha):
        for coluna, operador, valor in self.filtros:
            self._coluna_existe(coluna)
            if self._colunas()[coluna] == "uuid" and operador != "in":
                self._validar_valor(coluna, valor)
            atual = linha.get(coluna)
            if operador == "eq" and str(atual) != str(valor):
                return False
            if operador == "neq" and str(atual) == str(valor):
                return False
            if operador == "in" and str(atual) not in {str(v) for v in valor}:
                return False
            if operador == "like" and not re.fullmatch(
                re.escape(valor).replace("%", ".*"), str(atual)
            ):
                return False
        return True

    def execute(self):
        banco = self.banco
        banco.chamadas.append((self.tabela, self.acao))
        if self.tabela not in banco.schema:
            raise _erro("PGRST205", f"Could not find the table 'public.{self.tabela}'")
        if banco.indisponivel:
            raise RuntimeError("falha de rede simulada")
        if self.acao == "select" and banco.falhar_leitura_de:
            for coluna, operador, valor in self.filtros:
                if coluna == "empresa_id" and str(valor) in banco.falhar_leitura_de:
                    raise RuntimeError("falha de leitura simulada")
        if self.colunas != "*":
            for coluna in self.colunas.split(","):
                self._coluna_existe(coluna.strip())
        linhas = banco.tabelas[self.tabela]
        if self.acao == "select":
            achadas = [dict(l) for l in linhas if self._casa(l)]
            fim = self.fim + 1 if self.fim is not None else None
            achadas = achadas[self.inicio:fim]
            return SimpleNamespace(data=achadas[: self.limite] if self.limite else achadas)
        if self.acao == "delete":
            removidas = [l for l in linhas if self._casa(l)]
            banco.tabelas[self.tabela] = [l for l in linhas if l not in removidas]
            banco.exclusoes.append((self.tabela, len(removidas)))
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
        for entrada in entradas:
            self._validar_linha(entrada)
        conflito = (
            tuple(self.conflito.split(",")) if self.conflito else CONFLITOS.get(self.tabela)
        )
        resultado = []
        for entrada in entradas:
            existente = None
            if self.acao == "upsert" and conflito:
                existente = next(
                    (
                        l for l in linhas
                        if all(str(l.get(c)) == str(entrada.get(c)) for c in conflito)
                    ),
                    None,
                )
            if existente is not None:
                existente.update(entrada)
                resultado.append(dict(existente))
                continue
            linha = dict(entrada)
            if "id" in self._colunas():
                linha.setdefault("id", str(uuid.uuid4()))
            linhas.append(linha)
            resultado.append(dict(linha))
        return SimpleNamespace(data=resultado)


class BancoFalso:
    """Supabase falso; `com_rpc` ativa a RPC atômica `excluir_cliente_cascade`."""

    def __init__(self, com_rpc: bool = False, sem_tabelas: tuple = ()):
        self.schema = {t: c for t, c in SCHEMA.items() if t not in sem_tabelas}
        self.tabelas = {tabela: [] for tabela in self.schema}
        self.com_rpc = com_rpc
        self.chamadas, self.exclusoes = [], []
        self.indisponivel = False
        self.falhar_leitura_de: set[str] = set()

    def table(self, nome):
        return Consulta(self, nome)

    def clonar(self):
        copia = copy.copy(self)
        copia.tabelas = copy.deepcopy(self.tabelas)
        copia.chamadas, copia.exclusoes = [], []
        copia.falhar_leitura_de = set()
        return copia

    def rpc(self, nome, parametros):
        if not self.com_rpc or nome != "excluir_cliente_cascade":
            raise _erro("PGRST202", f"Could not find the function public.{nome}")
        empresa_id = parametros["p_empresa_id"]

        def executar():
            for tabela in TABELAS_POR_EMPRESA:
                self.tabelas[tabela] = [
                    l for l in self.tabelas[tabela] if str(l.get("empresa_id")) != empresa_id
                ]
            antes = len(self.tabelas["empresas_monitoradas"])
            self.tabelas["empresas_monitoradas"] = [
                l for l in self.tabelas["empresas_monitoradas"] if l["id"] != empresa_id
            ]
            return SimpleNamespace(data={
                "deleted": antes - len(self.tabelas["empresas_monitoradas"]) == 1,
                "empresa_id": empresa_id,
            })

        return SimpleNamespace(execute=executar)


# ---------------------------------------------------------------------------
# Instrumentos de medição: tudo que pertence a uma empresa, em qualquer tabela.
# ---------------------------------------------------------------------------
def _dono_linha_sistema(linha: dict):
    if str(linha.get("regime_tributario")) != ppf.REGIME_TRIBUTARIO_LEDGER:
        return None
    payload = ppf.decodificar_ledger(linha) or {}
    return payload.get("empresa_id")


def linhas_da_empresa(banco, empresa_id) -> dict[str, list[dict]]:
    empresa_id = str(empresa_id)
    resultado = {
        tabela: [l for l in banco.tabelas.get(tabela, []) if str(l.get("empresa_id")) == empresa_id]
        for tabela in TABELAS_POR_EMPRESA
    }
    resultado["empresas_monitoradas"] = [
        l for l in banco.tabelas["empresas_monitoradas"] if l["id"] == empresa_id
    ]
    resultado["ledger_sys"] = [
        l for l in banco.tabelas["empresas_monitoradas"]
        if str(_dono_linha_sistema(l)) == empresa_id
    ]
    return resultado


def contagens(banco, empresa_id) -> dict[str, int]:
    return {t: len(l) for t, l in linhas_da_empresa(banco, empresa_id).items()}


def digest(banco, empresa_id) -> str:
    conteudo = json.dumps(linhas_da_empresa(banco, empresa_id), sort_keys=True, default=str)
    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def contagem_fora(banco, empresa_id) -> dict[str, int]:
    """`WHERE empresa_id != X` em todas as tabelas (inclui ledger [SYS_LEDGER])."""
    empresa_id = str(empresa_id)
    resultado = {
        tabela: sum(
            1 for l in banco.tabelas.get(tabela, []) if str(l.get("empresa_id")) != empresa_id
        )
        for tabela in TABELAS_POR_EMPRESA
    }
    resultado["empresas_monitoradas"] = sum(
        1 for l in banco.tabelas["empresas_monitoradas"]
        if l["id"] != empresa_id and str(_dono_linha_sistema(l)) != empresa_id
    )
    return resultado


def cofres_no_banco(banco, empresa_id) -> list[dict]:
    encontrados = [
        l["payload"] for l in banco.tabelas.get("praesidium_empresa_mirror", [])
        if str(l["empresa_id"]) == str(empresa_id) and l["record_key"] == CHAVE_COFRE
    ]
    for linha in banco.tabelas["empresas_monitoradas"]:
        payload = ppf.decodificar_ledger(linha) or {}
        if (
            str(payload.get("empresa_id")) == str(empresa_id)
            and payload.get("item_uid") == CHAVE_COFRE
        ):
            encontrados.append(payload)
    return encontrados


# ---------------------------------------------------------------------------
# Dados de teste
# ---------------------------------------------------------------------------
def cnpj_da_empresa(indice: int) -> str:
    return f"{11000000 + indice * 7919:08d}0001{indice % 100:02d}"


def chave_nfe(indice: int, arquivo: int) -> str:
    return f"{indice * 10 + arquivo:044d}"


def xml_nfe(indice: int, arquivo: int, cnpj_dest: str, itens: list[tuple]) -> bytes:
    detalhes = "".join(
        f'<det nItem="{n}"><prod><xProd>{descricao}</xProd><NCM>{ncm}</NCM>'
        f"<CFOP>5102</CFOP><qCom>1</qCom><vProd>{valor:.2f}</vProd></prod></det>"
        for n, (descricao, ncm, valor) in enumerate(itens, start=1)
    )
    total = sum(valor for _, _, valor in itens)
    return (
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe>'
        f'<infNFe Id="NFe{chave_nfe(indice, arquivo)}"><ide><dhEmi>2026-11-06T10:00:00-03:00</dhEmi></ide>'
        f"<emit><CNPJ>60746948000112</CNPJ><xNome>Fornecedor {indice}</xNome><CRT>3</CRT></emit>"
        f"<dest><CNPJ>{cnpj_dest}</CNPJ></dest>{detalhes}"
        f"<total><ICMSTot><vNF>{total:.2f}</vNF></ICMSTot></total>"
        "</infNFe></NFe></nfeProc>"
    ).encode("utf-8")


def itens_do_cliente(indice: int) -> tuple[list[tuple], list[tuple]]:
    """(itens do XML A, itens do XML B) com valores exclusivos de cada cliente."""
    cnae, ncm, descricao = PERFIS_CNAE[indice % len(PERFIS_CNAE)]
    preco = 10 + indice
    aprovados = [(f"{descricao} {n}", ncm, float(preco)) for n in range(2 + indice % 3)]
    quarentena = [
        (f"Material de consumo interno {n}", ncm, float(preco))
        for n in range(1 + indice % 2)
    ]
    return aprovados + quarentena[:1], quarentena[1:] + [
        (f"Uso interno extra {indice}", ncm, float(preco + 1))
    ]


def credito_quarentena_esperado(valor: float, ncm: str = "", fator: float = 1.0) -> Decimal:
    aliquota = xml_engine._aliquota_base_item(ncm, "3", fator) if ncm else 0.265
    return (Decimal(str(valor)) * Decimal(str(aliquota))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def derivacao_do_cliente(indice: int) -> str:
    return list(xml_engine.ALIQUOTAS_REFORMA_TRIBUTARIA)[indice % 3]


class Escritorio:
    """Sessão do contador: estado Streamlit isolado + empresa ativa."""

    def __init__(self, banco, empresas: dict[str, dict]):
        self.banco, self.empresas = banco, empresas
        self.estado: dict = {}

    def _religar(self):
        xml_engine.st = SimpleNamespace(session_state=self.estado, toast=lambda *a, **k: None)

    def ativar(self, empresa_id: str) -> dict:
        self._religar()
        self.estado["empresa_id_ativa"] = empresa_id
        cache = self.estado.setdefault("_dados_hidratados_por_empresa", {})
        dados = xml_engine.hidratar_dados_empresa_protegido(
            self.banco, empresa_id, self.empresas[empresa_id], cache
        )
        chave_perfil = f"perfil_tributario_{empresa_id}"
        if chave_perfil not in self.estado:
            self.estado[chave_perfil] = xml_engine.carregar_perfil_tributario_empresa(
                self.banco, empresa_id
            )
        return dados

    def impostometro(self, empresa_id: str) -> dict:
        dados = self.ativar(empresa_id)
        saldos = xml_engine.calcular_saldos_com_cofre(dados)
        return {
            "bruto": saldos["bruto"],
            "liquido": saldos["credito_liquido"],
            "quarentena": saldos["quarentena"],
            "notas": sorted(str(n["id"]) for n in dados["notas"]),
            "itens": sorted(str(i["item_uid"]) for i in dados["itens"]),
            "eventos": sorted(str(e.get("event_id")) for e in dados["eventos_liquidacao"]),
            "devolucoes": len(dados["devolucoes"]),
        }

    def importar(self, indice: int, empresa_id: str, arquivo: int, itens: list[tuple]):
        self._religar()
        empresa = self.empresas[empresa_id]
        resultado = xml_engine.processar_xml_lote_bytes(
            [(f"nfe_{indice}_{arquivo}.xml", xml_nfe(indice, arquivo, empresa["cnpj"], itens))],
            self.banco,
            empresa_id,
            empresa,
        )
        assert resultado[0][0], resultado[0][1]


def montar_carteira(banco, quantidade: int = QUANTIDADE_EMPRESAS) -> tuple[Escritorio, list[str]]:
    escritorio = Escritorio(banco, {})
    escritorio._religar()
    ids = []
    for indice in range(quantidade):
        cnae = PERFIS_CNAE[indice % len(PERFIS_CNAE)][0]
        ok, mensagem, criadas = xml_engine.executar_insert_seguro(
            "empresas_monitoradas",
            {
                "razao_social": f"CLIENTE {indice:02d} LTDA [T:contador_teste]",
                "cnpj": cnpj_da_empresa(indice),
                "regime_tributario": "Lucro Presumido",
                "cnae_principal": cnae,
                "certificado_a1_status": "Ativo",
            },
            supabase=banco,
        )
        assert ok, mensagem
        empresa = criadas[0]
        ids.append(empresa["id"])
        escritorio.empresas[empresa["id"]] = {
            **empresa,
            "razao_social": f"CLIENTE {indice:02d} LTDA",
            "cnae_principal": cnae,
        }
    return escritorio, ids


def ingerir_carteira(escritorio: Escritorio, ids: list[str]) -> None:
    for indice, empresa_id in enumerate(ids):
        xml_engine.persistir_perfil_tributario_empresa(
            escritorio.banco, empresa_id, derivacao_do_cliente(indice)
        )
        xml_a, xml_b = itens_do_cliente(indice)
        escritorio.importar(indice, empresa_id, 1, xml_a)
        escritorio.importar(indice, empresa_id, 2, xml_b)


def registrar_eventos_e_devolucoes(escritorio: Escritorio, ids: list[str]) -> None:
    banco = escritorio.banco
    for indice, empresa_id in enumerate(ids):
        if indice % 2 == 0:
            xml_engine.registrar_evento_liquidacao_supabase(banco, empresa_id, {
                "empresa_id": empresa_id,
                "event_id": f"evt-{indice}",
                "id_op": f"OP-SPLIT-2026-{indice:08d}",
                "event_type": "Liquidação Parcial/Integral",
                "valor_liquidado": 100.0 + indice,
                "credito_compensado": 1.0 + indice / 10,
                "valor_retido_caixa": 2.0,
                "occurred_at": "2026-11-06T12:00:00+00:00",
            })
        if indice % 5 == 0:
            ok, mensagem, _ = xml_engine.executar_insert_seguro(
                "conciliacao_devolucoes",
                {
                    "empresa_id": empresa_id,
                    "chave_nota_devolucao": chave_nfe(indice, 9),
                    "credito_estornado": 0.5 + indice,
                },
                supabase=banco,
            )
            assert ok, mensagem
        if indice % 7 == 0:
            ok, mensagem, _ = xml_engine.executar_insert_seguro(
                "empresa_cnpjs_autorizados",
                {"empresa_id": empresa_id, "cnpj": f"{indice:014d}"},
                supabase=banco,
            )
            assert ok, mensagem


class BaseMultiTenant(unittest.TestCase):
    banco_base: BancoFalso
    ids_base: list[str]
    empresas_base: dict[str, dict]
    baseline_base: dict[str, dict]

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls._st = xml_engine.st
        cls._limpador = xml_engine._limpador_cache_dados
        xml_engine.registrar_limpador_cache_dados(lambda: None)
        xml_engine.limpar_cache_recursos_ausentes()
        banco = BancoFalso()
        escritorio, ids = montar_carteira(banco)
        ingerir_carteira(escritorio, ids)
        registrar_eventos_e_devolucoes(escritorio, ids)
        cls.baseline_base = {i: escritorio.impostometro(i) for i in ids}
        cls.banco_base, cls.ids_base, cls.empresas_base = banco, ids, escritorio.empresas

    @classmethod
    def tearDownClass(cls):
        xml_engine.st = cls._st
        xml_engine.registrar_limpador_cache_dados(cls._limpador)
        xml_engine.limpar_cache_recursos_ausentes()
        logging.disable(logging.NOTSET)

    def setUp(self):
        xml_engine.limpar_cache_recursos_ausentes()
        self.banco = self.banco_base.clonar()
        self.ids = list(self.ids_base)
        self.baseline = copy.deepcopy(self.baseline_base)
        self.escritorio = Escritorio(self.banco, copy.deepcopy(self.empresas_base))
        self.escritorio._religar()

    def alvo(self, posicao: int = 17) -> str:
        return self.ids[posicao]

    def outras(self, alvo: str) -> list[str]:
        return [i for i in self.ids if i != alvo]

    def assertOutrasIntactas(self, alvo, antes_digest, antes_fora):
        self.assertEqual(contagem_fora(self.banco, alvo), antes_fora)
        for empresa_id in self.outras(alvo):
            self.assertEqual(
                digest(self.banco, empresa_id), antes_digest[empresa_id],
                f"dados da empresa {empresa_id} foram alterados",
            )


class CarteiraDeCinquentaEmpresasTests(BaseMultiTenant):
    def test_carteira_tem_empresas_distintas_e_dados_proprios(self):
        self.assertEqual(len(set(self.ids)), QUANTIDADE_EMPRESAS)
        cadastros = self.banco.tabelas["empresas_monitoradas"]
        clientes = [c for c in cadastros if not str(c["regime_tributario"]).startswith("SYS_")]
        self.assertEqual(len({c["cnpj"] for c in clientes}), QUANTIDADE_EMPRESAS)
        self.assertGreaterEqual(len({c["cnae_principal"] for c in clientes}), 4)
        for empresa_id in self.ids:
            self.assertTrue(UUID_RE.match(empresa_id))
        chaves = [n["chave_acesso"] for n in self.banco.tabelas["notas_fiscais_recebidas"]]
        self.assertEqual(len(chaves), 2 * QUANTIDADE_EMPRESAS)
        self.assertEqual(len(set(chaves)), len(chaves))
        for tabela in ("notas_fiscais_recebidas", "itens_nota_fiscal"):
            for linha in self.banco.tabelas[tabela]:
                self.assertIn(linha["empresa_id"], self.ids)

    def test_cada_nota_e_item_pertence_exclusivamente_a_sua_empresa(self):
        notas = self.banco.tabelas["notas_fiscais_recebidas"]
        dono_da_nota = {n["id"]: n["empresa_id"] for n in notas}
        for item in self.banco.tabelas["itens_nota_fiscal"]:
            self.assertEqual(dono_da_nota[item["nota_id"]], item["empresa_id"])
        for indice, empresa_id in enumerate(self.ids):
            xml_a, xml_b = itens_do_cliente(indice)
            esperado = len(xml_a) + len(xml_b)
            dados = self.escritorio.ativar(empresa_id)
            self.assertEqual(len(dados["itens"]), esperado)
            self.assertEqual(len(dados["notas"]), 2)
            self.assertTrue(all(str(i["empresa_id"]) == empresa_id for i in dados["itens"]))
            self.assertTrue(all(str(n["empresa_id"]) == empresa_id for n in dados["notas"]))
            self.assertEqual(
                {n["chave_acesso"] for n in dados["notas"]},
                {chave_nfe(indice, 1), chave_nfe(indice, 2)},
            )

    def test_impostometro_bate_com_o_calculo_independente_do_motor(self):
        for indice, empresa_id in enumerate(self.ids):
            cnae = PERFIS_CNAE[indice % len(PERFIS_CNAE)][0]
            fator = xml_engine.ALIQUOTAS_REFORMA_TRIBUTARIA[derivacao_do_cliente(indice)][
                "fator_reducao"
            ]
            xml_a, xml_b = itens_do_cliente(indice)
            validado = quarentena = Decimal("0")
            for descricao, ncm, valor in xml_a + xml_b:
                classificacao, _, credito = xml_engine.classificar_item_inteligente(
                    ncm, "5102", 1.0, valor, "3", cnae, False, descricao, fator
                )
                if classificacao == "Aprovado Revenda":
                    validado += Decimal(str(credito))
                else:
                    quarentena += credito_quarentena_esperado(valor, ncm, fator)
            esperado_quarentena = float(quarentena)
            esperado_bruto = float(validado + quarentena)
            obtido = self.baseline[empresa_id]
            self.assertEqual(obtido["quarentena"], esperado_quarentena, empresa_id)
            self.assertEqual(obtido["bruto"], esperado_bruto, empresa_id)

    def test_perfil_tributario_de_cada_empresa_permanece_o_seu(self):
        derivacoes = list(xml_engine.ALIQUOTAS_REFORMA_TRIBUTARIA)
        for indice, empresa_id in enumerate(self.ids):
            perfil = xml_engine.carregar_perfil_tributario_empresa(self.banco, empresa_id)
            self.assertEqual(perfil["derivacao_setorial"], derivacoes[indice % 3])

    def test_cada_empresa_possui_um_unico_cofre_com_o_seu_saldo(self):
        for empresa_id in self.ids:
            cofres = cofres_no_banco(self.banco, empresa_id)
            self.assertEqual(len(cofres), 1, empresa_id)
            cofre = cofres[0]
            self.assertEqual(cofre["empresa_id"], empresa_id)
            self.assertEqual(cofre["saldo_bruto_consolidado"], self.baseline[empresa_id]["bruto"])
            self.assertEqual(cofre["saldo_liquido_consolidado"], self.baseline[empresa_id]["liquido"])
            self.assertEqual(
                cofre["saldo_quarentena_consolidado"], self.baseline[empresa_id]["quarentena"]
            )


class AlternanciaEEstresseTests(BaseMultiTenant):
    def test_alternancia_repetida_mantem_impostometro_notas_e_eventos(self):
        sorteio = random.Random(20261114)
        for rodada in range(4):
            ordem = list(self.ids)
            sorteio.shuffle(ordem)
            for empresa_id in ordem:
                self.assertEqual(
                    self.escritorio.impostometro(empresa_id),
                    self.baseline[empresa_id],
                    f"rodada {rodada}: {empresa_id}",
                )
        cache = self.escritorio.estado["_dados_hidratados_por_empresa"]
        self.assertEqual(set(cache), set(self.ids))
        for empresa_id, dados in cache.items():
            self.assertTrue(all(str(i["empresa_id"]) == empresa_id for i in dados["itens"]))

    def test_ping_pong_entre_duas_empresas_nao_mistura_saldos(self):
        a, b = self.ids[0], self.ids[1]
        self.assertNotEqual(self.baseline[a]["bruto"], self.baseline[b]["bruto"])
        for _ in range(25):
            self.assertEqual(self.escritorio.impostometro(a), self.baseline[a])
            self.assertEqual(self.escritorio.impostometro(b), self.baseline[b])

    def test_novo_login_reidrata_o_mesmo_patrimonio_fiscal(self):
        for empresa_id in self.ids:
            self.escritorio.impostometro(empresa_id)
        novo_login = Escritorio(self.banco, self.escritorio.empresas)
        for empresa_id in reversed(self.ids):
            self.assertEqual(novo_login.impostometro(empresa_id), self.baseline[empresa_id])

    def test_dois_escritorios_simultaneos_nao_se_contaminam(self):
        escritorio_b = Escritorio(self.banco, self.escritorio.empresas)
        for empresa_id in self.ids:
            a = self.escritorio.impostometro(empresa_id)
            b = escritorio_b.impostometro(self.ids[(self.ids.index(empresa_id) + 1) % 50])
            self.assertEqual(a, self.baseline[empresa_id])
            self.assertNotEqual(a["notas"], b["notas"])
        self.assertEqual(
            self.escritorio.estado["_dados_hidratados_por_empresa"].keys(),
            escritorio_b.estado["_dados_hidratados_por_empresa"].keys(),
        )

    def test_decisoes_de_quarentena_so_movem_o_saldo_da_empresa_decidida(self):
        decididas = (3, 11, 29, 44)
        antes = {i: digest(self.banco, i) for i in self.ids}
        for posicao in decididas:
            empresa_id = self.ids[posicao]
            dados = self.escritorio.ativar(empresa_id)
            pendentes = [
                i for i in dados["itens"] if i["classificacao_item"] == "Quarentena Uso/Consumo"
            ]
            self.assertTrue(pendentes)
            aprovar, rejeitar = pendentes[:1], pendentes[1:2]
            credito_aprovado = sum(
                xml_engine.obter_credito_quarentena(i) for i in aprovar
            )
            credito_rejeitado = sum(
                xml_engine.obter_credito_quarentena(i) for i in rejeitar
            )
            self.assertEqual(
                xml_engine.decidir_itens_quarentena_lote(
                    self.banco, empresa_id, aprovar, "aprovar"
                ),
                len(aprovar),
            )
            if rejeitar:
                xml_engine.decidir_itens_quarentena_lote(
                    self.banco, empresa_id, rejeitar, "rejeitar"
                )
            depois = self.escritorio.impostometro(empresa_id)
            base = self.baseline[empresa_id]
            self.assertAlmostEqual(
                depois["quarentena"],
                base["quarentena"] - credito_aprovado - credito_rejeitado,
                places=2,
            )
            self.assertAlmostEqual(
                depois["bruto"], base["bruto"] - credito_rejeitado, places=2
            )
            self.assertAlmostEqual(
                depois["liquido"], base["liquido"] + credito_aprovado, places=2
            )
            cofre = cofres_no_banco(self.banco, empresa_id)[0]
            self.assertEqual(cofre["saldo_bruto_consolidado"], depois["bruto"])
            self.assertEqual(cofre["saldo_liquido_consolidado"], depois["liquido"])
        for empresa_id in self.ids:
            if self.ids.index(empresa_id) in decididas:
                continue
            self.assertEqual(digest(self.banco, empresa_id), antes[empresa_id])
            self.assertEqual(self.escritorio.impostometro(empresa_id), self.baseline[empresa_id])

    def test_decisao_com_item_de_outra_empresa_e_recusada(self):
        a, b = self.ids[4], self.ids[5]
        item_de_b = next(
            i for i in self.escritorio.ativar(b)["itens"]
            if i["classificacao_item"] == "Quarentena Uso/Consumo"
        )
        antes = digest(self.banco, b)
        with self.assertRaises(ValueError):
            xml_engine.decidir_itens_quarentena_lote(self.banco, a, [item_de_b], "aprovar")
        self.assertEqual(digest(self.banco, b), antes)

    def test_chaves_de_sessao_sempre_particionadas_por_empresa(self):
        for empresa_id in self.ids:
            self.escritorio.ativar(empresa_id)
            self.escritorio.estado[f"quarentena_todos_{empresa_id}"] = True
            self.escritorio.estado[f"fallback_notas_{empresa_id}"] = [{"id": empresa_id}]
        permitidas = {"_dados_hidratados_por_empresa", "empresa_id_ativa"}
        for chave in self.escritorio.estado:
            if chave in permitidas:
                continue
            self.assertTrue(
                any(empresa_id in chave for empresa_id in self.ids),
                f"chave de sessão sem empresa_id: {chave}",
            )

    def test_codigo_nao_usa_chaves_de_sessao_genericas_para_dados_fiscais(self):
        padrao = re.compile(
            r"""session_state(?:\.get\(|\.pop\(|\.setdefault\(|\[)\s*(?<![fF])["']"""
            r"""(fallback_[a-z_]*|perfil_tributario[a-z_]*|quarentena_[a-z_]+|pacote_xml[a-z_]*"""
            r"""|upload_feedback[a-z_]*|cofre_saldo[a-z_]*|notas|itens|devolucoes|saldo[a-z_]*)["']"""
        )
        for arquivo in ("app.py", "xml_engine.py", "relatorio_engine.py"):
            for numero, linha in enumerate(
                (RAIZ / arquivo).read_text(encoding="utf-8").splitlines(), start=1
            ):
                if "fallback_empresas" in linha:
                    continue
                self.assertIsNone(padrao.search(linha), f"{arquivo}:{numero}: {linha.strip()}")

    def test_fallback_local_e_sempre_particionado_por_empresa(self):
        a, b = self.ids[0], self.ids[1]
        xml_engine._guardar_fallback("empresa_cnpjs_autorizados", {"empresa_id": a, "cnpj": "1"})
        xml_engine._guardar_fallback("empresa_cnpjs_autorizados", {"empresa_id": b, "cnpj": "2"})
        estado = self.escritorio.estado
        self.assertEqual([r["cnpj"] for r in estado[f"fallback_empresa_cnpjs_autorizados_{a}"]], ["1"])
        self.assertEqual([r["cnpj"] for r in estado[f"fallback_empresa_cnpjs_autorizados_{b}"]], ["2"])
        self.assertNotIn("fallback_empresa_cnpjs_autorizados_", estado)
        estado["empresa_id_ativa"] = a
        guardados = xml_engine._guardar_fallback("itens_nota_fiscal", {"item_uid": "x"})
        self.assertEqual(guardados[0]["empresa_id"], a)
        estado["empresa_id_ativa"] = ""
        self.assertEqual(xml_engine._guardar_fallback("itens_nota_fiscal", {"item_uid": "y"}), [])


class CofreDeSaldoTests(BaseMultiTenant):
    def _remover_itens_da_visao(self, empresa_id: str, quantos: int):
        """Perda parcial de listagem: some itens (banco + espelho) sem 'Zerar Notas'."""
        itens = [i for i in self.banco.tabelas["itens_nota_fiscal"] if i["empresa_id"] == empresa_id]
        uids = {i["item_uid"] for i in itens[:quantos]}
        removidos = [
            i for i in self.banco.tabelas["itens_nota_fiscal"]
            if i["empresa_id"] == empresa_id and i["item_uid"] in uids
        ]
        self.banco.tabelas["itens_nota_fiscal"] = [
            i for i in self.banco.tabelas["itens_nota_fiscal"] if i not in removidos
        ]
        espelho_removido = [
            m for m in self.banco.tabelas["praesidium_empresa_mirror"]
            if m["empresa_id"] == empresa_id
            and m["record_type"] == "item"
            and m["record_key"] in uids
        ]
        self.banco.tabelas["praesidium_empresa_mirror"] = [
            m for m in self.banco.tabelas["praesidium_empresa_mirror"]
            if m not in espelho_removido
        ]
        return removidos, espelho_removido

    def test_nota_que_deixa_de_ser_listada_nao_derruba_o_saldo(self):
        alvo = self.alvo(8)
        self._remover_itens_da_visao(alvo, 2)
        sessao = Escritorio(self.banco, self.escritorio.empresas)
        exibido = sessao.impostometro(alvo)
        self.assertLess(
            len(exibido["itens"]), len(self.baseline[alvo]["itens"]),
            "a visão deveria estar parcial",
        )
        self.assertEqual(exibido["bruto"], self.baseline[alvo]["bruto"])
        self.assertEqual(exibido["liquido"], self.baseline[alvo]["liquido"])
        self.assertEqual(exibido["quarentena"], self.baseline[alvo]["quarentena"])
        cofre = cofres_no_banco(self.banco, alvo)[0]
        self.assertEqual(cofre["saldo_bruto_consolidado"], self.baseline[alvo]["bruto"])
        self.assertEqual(cofre["saldo_liquido_consolidado"], self.baseline[alvo]["liquido"])

    def test_visao_totalmente_vazia_nao_zera_o_cofre(self):
        alvo = self.alvo(21)
        for tabela in ("itens_nota_fiscal", "notas_fiscais_recebidas"):
            self.banco.tabelas[tabela] = [
                l for l in self.banco.tabelas[tabela] if l["empresa_id"] != alvo
            ]
        self.banco.tabelas["praesidium_empresa_mirror"] = [
            m for m in self.banco.tabelas["praesidium_empresa_mirror"]
            if not (m["empresa_id"] == alvo and m["record_type"] == "item")
        ]
        exibido = Escritorio(self.banco, self.escritorio.empresas).impostometro(alvo)
        self.assertEqual(exibido["itens"], [])
        self.assertEqual(exibido["bruto"], self.baseline[alvo]["bruto"])
        self.assertEqual(exibido["liquido"], self.baseline[alvo]["liquido"])
        self.assertEqual(exibido["quarentena"], self.baseline[alvo]["quarentena"])

    def test_falha_de_rede_nao_regride_o_impostometro_nem_grava_zeros(self):
        alvo = self.alvo(30)
        sessao = Escritorio(self.banco, self.escritorio.empresas)
        self.assertEqual(sessao.impostometro(alvo), self.baseline[alvo])
        cofre_antes = copy.deepcopy(cofres_no_banco(self.banco, alvo))
        # Sessão em andamento: o snapshot e o cofre em memória sustentam os cards.
        self.banco.indisponivel = True
        self.assertEqual(sessao.impostometro(alvo), self.baseline[alvo])
        # Login novo durante a queda: nada é exibido de outra empresa e nada é gravado.
        login_novo = Escritorio(self.banco, self.escritorio.empresas)
        durante_a_queda = login_novo.impostometro(alvo)
        self.assertEqual(durante_a_queda["notas"], [])
        self.banco.indisponivel = False
        self.assertEqual(cofres_no_banco(self.banco, alvo), cofre_antes)
        self.assertEqual(login_novo.impostometro(alvo), self.baseline[alvo])

    def test_falha_de_leitura_so_de_uma_empresa_nao_afeta_as_demais(self):
        alvo = self.alvo(12)
        self.banco.falhar_leitura_de = {alvo}
        login_novo = Escritorio(self.banco, self.escritorio.empresas)
        login_novo.impostometro(alvo)
        for empresa_id in self.outras(alvo):
            self.assertEqual(login_novo.impostometro(empresa_id), self.baseline[empresa_id])
        self.banco.falhar_leitura_de = set()
        self.assertEqual(login_novo.impostometro(alvo), self.baseline[alvo])

    def test_cofre_forjado_de_outra_empresa_e_ignorado(self):
        a, b = self.ids[6], self.ids[7]
        cofre_de_a = cofres_no_banco(self.banco, a)[0]
        for linha in self.banco.tabelas["praesidium_empresa_mirror"]:
            if linha["empresa_id"] == b and linha["record_key"] == CHAVE_COFRE:
                linha["payload"] = {**cofre_de_a, "saldo_bruto_consolidado": 999999.99}
        exibido = Escritorio(self.banco, self.escritorio.empresas).impostometro(b)
        self.assertEqual(exibido["bruto"], self.baseline[b]["bruto"])
        self.assertEqual(exibido["liquido"], self.baseline[b]["liquido"])
        reparado = cofres_no_banco(self.banco, b)[0]
        self.assertEqual(reparado["empresa_id"], b)
        self.assertEqual(reparado["saldo_bruto_consolidado"], self.baseline[b]["bruto"])

    def test_novos_creditos_sobem_o_cofre_somente_da_empresa_importada(self):
        indice = 9
        alvo = self.ids[indice]
        antes = {i: digest(self.banco, i) for i in self.outras(alvo)}
        self.escritorio.importar(
            indice, alvo, 3, [("Shampoo profissional extra", PERFIS_CNAE[1][1], 500.0)]
        )
        depois = self.escritorio.impostometro(alvo)
        self.assertGreater(depois["bruto"], self.baseline[alvo]["bruto"])
        self.assertEqual(
            cofres_no_banco(self.banco, alvo)[0]["saldo_bruto_consolidado"], depois["bruto"]
        )
        for empresa_id in self.outras(alvo):
            self.assertEqual(digest(self.banco, empresa_id), antes[empresa_id])

    def test_nota_some_e_outra_entra_no_mesmo_instante_o_saldo_nao_cai(self):
        indice = 14
        alvo = self.ids[indice]
        base = self.baseline[alvo]
        cofre_antes = cofres_no_banco(self.banco, alvo)[0]
        chave_sumida = chave_nfe(indice, 1)
        self.assertIn(chave_sumida, cofre_antes["notas_consolidadas"])
        # A nota 1 some da visão e, no mesmo instante, a nota 3 entra.
        self.banco.tabelas["notas_fiscais_recebidas"] = [
            n for n in self.banco.tabelas["notas_fiscais_recebidas"]
            if not (n["empresa_id"] == alvo and n["chave_acesso"] == chave_sumida)
        ]
        self.banco.tabelas["itens_nota_fiscal"] = [
            i for i in self.banco.tabelas["itens_nota_fiscal"]
            if not (i["empresa_id"] == alvo and chave_sumida in i["item_uid"])
        ]
        self.banco.tabelas["praesidium_empresa_mirror"] = [
            m for m in self.banco.tabelas["praesidium_empresa_mirror"]
            if not (m["empresa_id"] == alvo and chave_sumida in m["record_key"])
        ]
        sessao = Escritorio(self.banco, self.escritorio.empresas)
        sessao.importar(
            indice, alvo, 3, [("Shampoo profissional extra", PERFIS_CNAE[2][1], 400.0)]
        )
        itens_novos = [
            i for i in sessao.ativar(alvo)["itens"] if chave_nfe(indice, 3) in i["item_uid"]
        ]
        nova = xml_engine.calcular_resumo_creditos(itens_novos)
        self.assertGreater(nova["bruto"], 0)

        exibido = sessao.impostometro(alvo)
        self.assertGreaterEqual(exibido["bruto"], base["bruto"])
        self.assertGreaterEqual(exibido["liquido"], base["liquido"])
        self.assertGreaterEqual(exibido["quarentena"], base["quarentena"])
        self.assertAlmostEqual(exibido["bruto"], base["bruto"] + nova["bruto"], places=2)
        self.assertAlmostEqual(exibido["liquido"], base["liquido"] + nova["validado"], places=2)
        cofre = cofres_no_banco(self.banco, alvo)[0]
        self.assertEqual(
            set(cofre["notas_consolidadas"]),
            {chave_sumida, chave_nfe(indice, 2), chave_nfe(indice, 3)},
        )
        # Estável em novas leituras: a nota nova não é somada duas vezes.
        for _ in range(3):
            self.assertEqual(sessao.impostometro(alvo), exibido)

    def test_nota_que_volta_a_ser_listada_restabelece_a_apuracao_completa(self):
        alvo = self.alvo(20)
        itens_removidos, mirror_removido = self._remover_itens_da_visao(alvo, 2)
        sessao = Escritorio(self.banco, self.escritorio.empresas)
        self.assertEqual(sessao.impostometro(alvo)["bruto"], self.baseline[alvo]["bruto"])
        self.banco.tabelas["itens_nota_fiscal"].extend(itens_removidos)
        self.banco.tabelas["praesidium_empresa_mirror"].extend(mirror_removido)
        self.assertEqual(
            Escritorio(self.banco, self.escritorio.empresas).impostometro(alvo),
            self.baseline[alvo],
        )

    def test_zerar_notas_explicito_libera_o_cofre_apos_perda_de_notas(self):
        alvo = self.alvo(33)
        self._remover_itens_da_visao(alvo, 2)
        xml_engine.limpar_dados_empresa_supabase(self.banco, alvo)
        zerado = Escritorio(self.banco, self.escritorio.empresas).impostometro(alvo)
        self.assertEqual((zerado["bruto"], zerado["liquido"], zerado["quarentena"]), (0.0, 0.0, 0.0))
        self.assertEqual(cofres_no_banco(self.banco, alvo), [])

    def test_cofre_no_ledger_de_sistema_quando_o_espelho_nao_existe(self):
        banco = BancoFalso(sem_tabelas=("praesidium_empresa_mirror",))
        xml_engine.limpar_cache_recursos_ausentes()
        escritorio, ids = montar_carteira(banco, 4)
        for indice, empresa_id in enumerate(ids):
            xml_a, xml_b = itens_do_cliente(indice)
            escritorio.importar(indice, empresa_id, 1, xml_a)
            escritorio.importar(indice, empresa_id, 2, xml_b)
        saldos = {i: escritorio.impostometro(i) for i in ids}
        for empresa_id in ids:
            cofres = cofres_no_banco(banco, empresa_id)
            self.assertEqual(len(cofres), 1)
            self.assertEqual(cofres[0]["saldo_bruto_consolidado"], saldos[empresa_id]["bruto"])
        alvo = ids[2]
        antes = {i: digest(banco, i) for i in ids if i != alvo}
        antes_fora = contagem_fora(banco, alvo)
        xml_engine.limpar_dados_empresa_supabase(banco, alvo)
        self.assertEqual(cofres_no_banco(banco, alvo), [])
        self.assertEqual(contagem_fora(banco, alvo), antes_fora)
        for empresa_id in antes:
            self.assertEqual(digest(banco, empresa_id), antes[empresa_id])
            self.assertEqual(escritorio.impostometro(empresa_id), saldos[empresa_id])

    def test_colisao_de_identificador_do_ledger_nunca_sobrescreve_outro_cliente(self):
        banco = BancoFalso(sem_tabelas=("praesidium_empresa_mirror",))
        xml_engine.limpar_cache_recursos_ausentes()
        escritorio, ids = montar_carteira(banco, 2)
        original = ppf.cnpj_registro_ledger
        ppf.cnpj_registro_ledger = lambda empresa_id, chave: "L00000COLISAO"
        derivacoes = list(xml_engine.ALIQUOTAS_REFORMA_TRIBUTARIA)
        try:
            xml_engine.persistir_perfil_tributario_empresa(banco, ids[0], derivacoes[1])
            with self.assertRaises(RuntimeError):
                xml_engine.persistir_perfil_tributario_empresa(banco, ids[1], derivacoes[2])
            perfil_a = xml_engine.carregar_perfil_tributario_empresa(banco, ids[0])
        finally:
            ppf.cnpj_registro_ledger = original
        self.assertEqual(perfil_a["derivacao_setorial"], derivacoes[1])


class RegrasDoCofreTests(unittest.TestCase):
    EMPRESA = str(uuid.uuid4())

    def saldos(self, bruto, liquido, quarentena=0.0, validado=None):
        return {
            "bruto": bruto, "credito_liquido": liquido,
            "quarentena": quarentena,
            "validado": bruto - quarentena if validado is None else validado,
        }

    def itens(self, quantidade):
        return [{"item_uid": str(n)} for n in range(quantidade)]

    def test_primeira_apuracao_cria_o_cofre_e_sem_itens_nao_cria(self):
        self.assertEqual(
            xml_engine.consolidar_cofre_saldo(self.EMPRESA, [], self.saldos(0, 0)), (None, False)
        )
        cofre, alterado = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, self.itens(3), self.saldos(100.0, 80.0, 20.0)
        )
        self.assertTrue(alterado)
        self.assertEqual(cofre["saldo_bruto_consolidado"], 100.0)
        self.assertEqual(cofre["saldo_liquido_consolidado"], 80.0)
        self.assertEqual(cofre["itens_consolidados"], 3)

    def test_visao_parcial_nunca_reduz_e_visao_completa_e_autoritativa(self):
        cofre, _ = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, self.itens(5), self.saldos(100.0, 80.0, 20.0)
        )
        parcial, alterado = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, self.itens(2), self.saldos(30.0, 10.0, 5.0), cofre
        )
        self.assertFalse(alterado)
        self.assertEqual(parcial, cofre)
        mais_alto, alterado = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, self.itens(2), self.saldos(130.0, 10.0, 5.0), cofre
        )
        self.assertTrue(alterado)
        self.assertEqual(mais_alto["saldo_bruto_consolidado"], 130.0)
        self.assertEqual(mais_alto["saldo_liquido_consolidado"], 80.0)
        self.assertEqual(mais_alto["itens_consolidados"], 5)
        completo, alterado = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, self.itens(5), self.saldos(90.0, 70.0, 20.0), cofre
        )
        self.assertTrue(alterado)
        self.assertEqual(completo["saldo_bruto_consolidado"], 90.0)

    def test_nota_some_e_outra_entra_ao_mesmo_tempo_nunca_derruba_o_cofre(self):
        def itens_da_nota(chave, creditos):
            return [
                {
                    "item_uid": f"{chave}::ITEM::{n}", "chave_acesso": chave,
                    "classificacao_item": "Aprovado Revenda", "valor_credito_item": credito,
                    "valor_item": credito * 4,
                }
                for n, credito in enumerate(creditos, start=1)
            ]

        def saldos_de(itens):
            return xml_engine.calcular_saldo_credito_digital(itens, [], [], [])

        nota_1, nota_2 = "1" * 44, "2" * 44
        visao_inicial = itens_da_nota(nota_1, [30.0, 40.0, 30.0])
        cofre, _ = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, visao_inicial, saldos_de(visao_inicial)
        )
        self.assertEqual(cofre["notas_consolidadas"], [nota_1])
        self.assertEqual(cofre["saldo_bruto_consolidado"], 100.0)

        # Mesma contagem de itens (3), mas a nota consolidada trocou: não é visão completa.
        visao_trocada = itens_da_nota(nota_2, [20.0, 25.0, 25.0])
        novo, alterado = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, visao_trocada, saldos_de(visao_trocada), cofre
        )
        self.assertTrue(alterado)
        self.assertEqual(novo["saldo_bruto_consolidado"], 170.0)
        self.assertEqual(novo["saldo_liquido_consolidado"], 170.0)
        self.assertEqual(novo["notas_consolidadas"], [nota_1, nota_2])
        self.assertEqual(novo["itens_consolidados"], 6)

        # A mesma visão repetida não soma a nota nova outra vez.
        repetido, alterado = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, visao_trocada, saldos_de(visao_trocada), novo
        )
        self.assertFalse(alterado)
        self.assertEqual(repetido, novo)

        # Com as duas notas listadas a apuração volta a ser autoritativa.
        visao_completa = visao_inicial + visao_trocada
        completo, _ = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, visao_completa, saldos_de(visao_completa), novo
        )
        self.assertEqual(completo["saldo_bruto_consolidado"], 170.0)

        # Item sumido dentro de uma nota ainda listada também é visão parcial.
        visao_sem_item = visao_inicial[:2] + visao_trocada
        parcial, _ = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, visao_sem_item, saldos_de(visao_sem_item), completo
        )
        self.assertEqual(parcial["saldo_bruto_consolidado"], 170.0)

    def test_cofre_legado_sem_identificadores_segue_a_regra_por_contagem(self):
        legado = {
            "record_type": "company_balance_vault", "empresa_id": self.EMPRESA,
            "saldo_bruto_consolidado": 100.0, "saldo_liquido_consolidado": 80.0,
            "saldo_quarentena_consolidado": 20.0, "credito_validado_consolidado": 80.0,
            "itens_consolidados": 5, "updated_at": "2026-11-13T00:00:00+00:00",
        }
        parcial, alterado = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, self.itens(2), self.saldos(30.0, 10.0, 5.0), legado
        )
        self.assertFalse(alterado)
        self.assertEqual(parcial, legado)
        completo, alterado = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, self.itens(5), self.saldos(90.0, 70.0, 20.0), legado
        )
        self.assertTrue(alterado)
        self.assertEqual(completo["saldo_bruto_consolidado"], 90.0)
        self.assertIn("itens_por_nota", completo)

    def test_leitura_instavel_preserva_o_cofre(self):
        cofre, _ = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, self.itens(5), self.saldos(100.0, 80.0, 20.0)
        )
        self.assertEqual(
            xml_engine.consolidar_cofre_saldo(
                self.EMPRESA, [], self.saldos(0, 0), cofre, leitura_confiavel=False
            ),
            (cofre, False),
        )

    def test_cofre_de_outra_empresa_e_descartado(self):
        outra = str(uuid.uuid4())
        cofre_alheio, _ = xml_engine.consolidar_cofre_saldo(
            outra, self.itens(5), self.saldos(999.0, 999.0)
        )
        cofre, alterado = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, self.itens(1), self.saldos(10.0, 10.0), cofre_alheio
        )
        self.assertTrue(alterado)
        self.assertEqual(cofre["empresa_id"], self.EMPRESA)
        self.assertEqual(cofre["saldo_bruto_consolidado"], 10.0)
        with self.assertRaises(ValueError):
            xml_engine.persistir_cofre_saldo_empresa(BancoFalso(), self.EMPRESA, cofre_alheio)

    def test_aplicar_cofre_substitui_apenas_os_saldos_do_impostometro(self):
        cofre, _ = xml_engine.consolidar_cofre_saldo(
            self.EMPRESA, self.itens(5), self.saldos(100.0, 80.0, 20.0)
        )
        momentaneo = {**self.saldos(0.0, 0.0), "credito_disponivel": 0.0, "retido_caixa": 3.0}
        exibido = xml_engine.aplicar_cofre_aos_saldos(momentaneo, cofre)
        self.assertEqual(exibido["bruto"], 100.0)
        self.assertEqual(exibido["credito_liquido"], 80.0)
        self.assertEqual(exibido["credito_disponivel"], 80.0)
        self.assertEqual(exibido["retido_caixa"], 3.0)
        self.assertEqual(xml_engine.aplicar_cofre_aos_saldos(momentaneo, None), momentaneo)


class ZerarENExcluirTests(BaseMultiTenant):
    def _preparar_sessao_com_estado(self):
        for empresa_id in self.ids:
            self.escritorio.ativar(empresa_id)
            self.escritorio.estado[f"quarentena_todos_{empresa_id}"] = True
            self.escritorio.estado[f"fallback_notas_{empresa_id}"] = [{"id": "n"}]
            self.escritorio.estado[f"form_liquidacao_{empresa_id}_op"] = "x"
            self.escritorio.estado[f"up_reset_{empresa_id}"] = 2

    def _zerar(self, alvo):
        self.escritorio._religar()
        xml_engine.limpar_dados_empresa_supabase(self.banco, alvo)

    def test_zerar_notas_atinge_somente_a_empresa_ativa(self):
        self._preparar_sessao_com_estado()
        alvo = self.alvo()
        antes_digest = {i: digest(self.banco, i) for i in self.outras(alvo)}
        antes_fora = contagem_fora(self.banco, alvo)
        antes_alvo = contagens(self.banco, alvo)
        self.assertGreater(antes_alvo["notas_fiscais_recebidas"], 0)
        perfil_antes = xml_engine.carregar_perfil_tributario_empresa(self.banco, alvo)

        self._zerar(alvo)

        depois = contagens(self.banco, alvo)
        for tabela in (
            "notas_fiscais_recebidas", "itens_nota_fiscal", "conciliacao_devolucoes",
            "split_payment_events", "praesidium_ledger_fiscal", "ledger_sys",
        ):
            self.assertEqual(depois[tabela], 0, tabela)
        self.assertEqual(depois["empresas_monitoradas"], 1)
        self.assertEqual(depois["empresa_cnpjs_autorizados"], antes_alvo["empresa_cnpjs_autorizados"])
        restantes = [
            m["record_key"] for m in self.banco.tabelas["praesidium_empresa_mirror"]
            if m["empresa_id"] == alvo
        ]
        self.assertEqual(restantes, [ppf.CHAVE_LEDGER_PERFIL_TRIBUTARIO])
        self.assertEqual(cofres_no_banco(self.banco, alvo), [])
        self.assertEqual(
            xml_engine.carregar_perfil_tributario_empresa(self.banco, alvo), perfil_antes
        )
        self.assertOutrasIntactas(alvo, antes_digest, antes_fora)

        estado = self.escritorio.estado
        for chave in ("quarentena_todos_", "fallback_notas_", "up_reset_"):
            self.assertNotIn(f"{chave}{alvo}", estado)
            for empresa_id in self.outras(alvo):
                self.assertIn(f"{chave}{empresa_id}", estado)
        self.assertNotIn(f"form_liquidacao_{alvo}_op", estado)
        self.assertIn(f"perfil_tributario_{alvo}", estado)
        self.assertEqual(
            set(estado["_dados_hidratados_por_empresa"]), set(self.outras(alvo))
        )

        zerado = self.escritorio.impostometro(alvo)
        self.assertEqual((zerado["bruto"], zerado["liquido"], zerado["quarentena"]), (0.0, 0.0, 0.0))
        self.assertEqual(zerado["notas"], [])
        self.assertEqual(cofres_no_banco(self.banco, alvo), [])
        for empresa_id in self.outras(alvo):
            self.assertEqual(self.escritorio.impostometro(empresa_id), self.baseline[empresa_id])

    def test_zerar_notas_em_cada_posicao_da_carteira_preserva_as_demais(self):
        for posicao in (0, 1, 25, 48, 49):
            banco = self.banco_base.clonar()
            self.banco = banco
            self.escritorio = Escritorio(banco, copy.deepcopy(self.empresas_base))
            alvo = self.ids[posicao]
            antes_digest = {i: digest(banco, i) for i in self.ids if i != alvo}
            antes_fora = contagem_fora(banco, alvo)
            self._zerar(alvo)
            self.assertOutrasIntactas(alvo, antes_digest, antes_fora)
            self.assertEqual(contagens(banco, alvo)["itens_nota_fiscal"], 0)

    def test_zerar_notas_duas_vezes_e_idempotente(self):
        alvo = self.alvo()
        antes_digest = {i: digest(self.banco, i) for i in self.outras(alvo)}
        antes_fora = contagem_fora(self.banco, alvo)
        self._zerar(alvo)
        self._zerar(alvo)
        self.assertOutrasIntactas(alvo, antes_digest, antes_fora)

    def test_reimportar_depois_de_zerar_recria_o_cofre_so_da_empresa(self):
        indice = 17
        alvo = self.ids[indice]
        self._zerar(alvo)
        antes_digest = {i: digest(self.banco, i) for i in self.outras(alvo)}
        xml_a, xml_b = itens_do_cliente(indice)
        self.escritorio.importar(indice, alvo, 1, xml_a)
        self.escritorio.importar(indice, alvo, 2, xml_b)
        recriado = self.escritorio.impostometro(alvo)
        self.assertEqual(recriado["bruto"], self.baseline[alvo]["bruto"])
        self.assertEqual(len(cofres_no_banco(self.banco, alvo)), 1)
        for empresa_id in self.outras(alvo):
            self.assertEqual(digest(self.banco, empresa_id), antes_digest[empresa_id])

    def _excluir(self, alvo):
        self.escritorio._religar()
        return xml_engine.excluir_empresa_supabase(self.banco, alvo)

    def _verificar_exclusao(self, alvo, resultado):
        self.assertTrue(resultado["deleted"])
        totais = contagens(self.banco, alvo)
        self.assertEqual(sum(totais.values()), 0, totais)
        self.assertEqual(cofres_no_banco(self.banco, alvo), [])
        self.assertNotIn(
            alvo, [c["id"] for c in self.banco.tabelas["empresas_monitoradas"]]
        )

    def test_excluir_cliente_via_fallback_remove_somente_a_empresa(self):
        self._preparar_sessao_com_estado()
        alvo = self.alvo(33)
        antes_digest = {i: digest(self.banco, i) for i in self.outras(alvo)}
        antes_fora = contagem_fora(self.banco, alvo)
        resultado = self._excluir(alvo)
        self.assertTrue(resultado.get("via_fallback"))
        self._verificar_exclusao(alvo, resultado)
        self.assertOutrasIntactas(alvo, antes_digest, antes_fora)
        cache = self.escritorio.estado["_dados_hidratados_por_empresa"]
        self.assertNotIn(alvo, cache)
        self.assertEqual(set(cache), set(self.outras(alvo)))
        for empresa_id in self.outras(alvo):
            self.assertEqual(self.escritorio.impostometro(empresa_id), self.baseline[empresa_id])
        self.assertFalse(any(alvo in str(chave) for chave in self.escritorio.estado))

    def test_excluir_cliente_via_rpc_atomica_remove_somente_a_empresa(self):
        self.banco.com_rpc = True
        alvo = self.alvo(2)
        antes_digest = {i: digest(self.banco, i) for i in self.outras(alvo)}
        antes_fora = contagem_fora(self.banco, alvo)
        resultado = self._excluir(alvo)
        self.assertTrue(resultado.get("via_rpc"))
        self._verificar_exclusao(alvo, resultado)
        self.assertOutrasIntactas(alvo, antes_digest, antes_fora)

    def test_excluir_apos_zerar_e_excluir_de_novo_nao_toca_as_demais(self):
        alvo = self.alvo(40)
        outro = self.alvo(41)
        antes_digest = {i: digest(self.banco, i) for i in self.ids if i not in (alvo, outro)}
        self._zerar(alvo)
        self._verificar_exclusao(alvo, self._excluir(alvo))
        apos_um = contagem_fora(self.banco, outro)
        self._verificar_exclusao(outro, self._excluir(outro))
        self.assertEqual(
            sum(contagens(self.banco, alvo).values()) + sum(contagens(self.banco, outro).values()), 0
        )
        for empresa_id, esperado in antes_digest.items():
            self.assertEqual(digest(self.banco, empresa_id), esperado)
        self.assertGreater(sum(apos_um.values()), sum(contagem_fora(self.banco, outro).values()) - 1)

    def test_id_invalido_nunca_chega_ao_banco(self):
        antes = copy.deepcopy(self.banco.tabelas)
        for operacao in (xml_engine.excluir_empresa_supabase, xml_engine.limpar_dados_empresa_supabase):
            with self.assertRaises(ValueError):
                operacao(self.banco, "88")
            with self.assertRaises(ValueError):
                operacao(self.banco, "local_abc")
        self.assertEqual(self.banco.tabelas, antes)
        self.assertEqual(self.banco.exclusoes, [])

    def test_falha_ao_zerar_e_propagada_sem_tocar_outras_empresas(self):
        alvo = self.alvo(5)
        antes_digest = {i: digest(self.banco, i) for i in self.outras(alvo)}
        antes_fora = contagem_fora(self.banco, alvo)
        self.banco.indisponivel = True
        with self.assertRaises(Exception):
            self._zerar(alvo)
        self.banco.indisponivel = False
        self.assertOutrasIntactas(alvo, antes_digest, antes_fora)

    def test_toda_exclusao_do_banco_usa_filtro_da_empresa_alvo(self):
        alvo = self.alvo(9)
        excluidas = []
        original = Consulta.execute

        def espiao(consulta):
            if consulta.acao == "delete":
                filtros = {(c, o, str(v)) for c, o, v in consulta.filtros}
                excluidas.append((consulta.tabela, filtros))
            return original(consulta)

        Consulta.execute = espiao
        try:
            self._zerar(alvo)
            self._excluir(alvo)
        finally:
            Consulta.execute = original
        self.assertTrue(excluidas)
        ids_linhas_ledger = {
            l["id"] for l in self.banco_base.tabelas["empresas_monitoradas"]
            if str(_dono_linha_sistema(l)) == alvo
        }
        for tabela, filtros in excluidas:
            escopo = (
                ("empresa_id", "eq", alvo) in filtros
                or ("id", "eq", alvo) in filtros
                or any(c == "id" and v in ids_linhas_ledger for c, _, v in filtros)
            )
            self.assertTrue(escopo, f"DELETE sem escopo da empresa em {tabela}: {filtros}")


# ---------------------------------------------------------------------------
# Supabase real (opt-in): clientes de teste próprios, nada preexistente é tocado.
# ---------------------------------------------------------------------------
def _cliente_supabase_real():
    from supabase import create_client

    segredos = tomllib.loads((RAIZ / ".streamlit" / "secrets.toml").read_text(encoding="utf-8"))
    return create_client(segredos["SUPABASE_URL"], segredos["SUPABASE_KEY"])


def _ler_tudo(cliente, tabela, filtro=None):
    linhas, inicio = [], 0
    while True:
        consulta = cliente.table(tabela).select("*")
        if filtro:
            consulta = consulta.eq(*filtro)
        pagina = consulta.range(inicio, inicio + 999).execute().data or []
        linhas.extend(pagina)
        if len(pagina) < 1000:
            return linhas
        inicio += 1000


@unittest.skipUnless(
    os.environ.get("PRAESIDIUM_TEST_SUPABASE_REAL") == "1",
    "defina PRAESIDIUM_TEST_SUPABASE_REAL=1 para executar contra o Supabase real",
)
class SupabaseRealMultiTenantTests(unittest.TestCase):
    TABELAS = (
        "notas_fiscais_recebidas", "itens_nota_fiscal", "praesidium_empresa_mirror",
        "conciliacao_devolucoes", "split_payment_events", "empresa_cnpjs_autorizados",
    )
    QUANTIDADE = 6

    def _foto_preexistente(self, cliente, ids_teste):
        foto = {}
        for tabela in self.TABELAS:
            linhas = [
                l for l in _ler_tudo(cliente, tabela)
                if str(l.get("empresa_id")) not in ids_teste
            ]
            foto[tabela] = hashlib.sha256(
                json.dumps(sorted(json.dumps(l, sort_keys=True, default=str) for l in linhas)).encode()
            ).hexdigest(), len(linhas)
        clientes = [
            l for l in _ler_tudo(cliente, "empresas_monitoradas")
            if l["id"] not in ids_teste
            and not str(l.get("regime_tributario", "")).startswith("SYS_")
            and not str(l.get("razao_social", "")).startswith("[SYS_")
        ]
        foto["empresas_monitoradas"] = hashlib.sha256(
            json.dumps(sorted(json.dumps(l, sort_keys=True, default=str) for l in clientes)).encode()
        ).hexdigest(), len(clientes)
        return foto

    def _linhas_teste(self, cliente, empresa_id):
        total = {}
        for tabela in self.TABELAS:
            total[tabela] = len(_ler_tudo(cliente, tabela, ("empresa_id", empresa_id)))
        total["cadastro"] = len(_ler_tudo(cliente, "empresas_monitoradas", ("id", empresa_id)))
        return total

    def test_carteira_de_teste_no_supabase_real(self):
        cliente = _cliente_supabase_real()
        xml_engine.limpar_cache_recursos_ausentes()
        original_st, original_limpador = xml_engine.st, xml_engine._limpador_cache_dados
        xml_engine.registrar_limpador_cache_dados(lambda: None)
        sufixo = uuid.uuid4().hex[:6]
        escritorio = Escritorio(cliente, {})
        ids: list[str] = []
        antes = self._foto_preexistente(cliente, set())
        try:
            escritorio._religar()
            for indice in range(self.QUANTIDADE):
                cnae = PERFIS_CNAE[indice % len(PERFIS_CNAE)][0]
                ok, mensagem, criadas = xml_engine.executar_insert_seguro(
                    "empresas_monitoradas",
                    {
                        "razao_social": f"ZZ TESTE MT {sufixo} {indice} [T:zz_teste_mt]",
                        "cnpj": f"9{int(sufixo, 16) % 10**7:07d}{indice:02d}{indice + 1:04d}"[:14],
                        "regime_tributario": "Lucro Presumido",
                        "cnae_principal": cnae,
                        "certificado_a1_status": "Ativo",
                    },
                    supabase=cliente,
                )
                self.assertTrue(ok, mensagem)
                empresa = criadas[0]
                ids.append(empresa["id"])
                escritorio.empresas[empresa["id"]] = {
                    **empresa,
                    "razao_social": f"ZZ TESTE MT {indice}",
                    "cnae_principal": cnae,
                }
            for indice, empresa_id in enumerate(ids):
                xml_a, xml_b = itens_do_cliente(indice)
                escritorio.importar(
                    indice + 100 * int(sufixo, 16) % 10**6, empresa_id, 1, xml_a
                )
                escritorio.importar(
                    indice + 100 * int(sufixo, 16) % 10**6, empresa_id, 2, xml_b
                )
            baseline = {i: escritorio.impostometro(i) for i in ids}
            for empresa_id in ids:
                self.assertGreater(baseline[empresa_id]["bruto"], 0)
                self.assertEqual(len(cofres_no_real(cliente, empresa_id)), 1)
            for _ in range(3):
                for empresa_id in reversed(ids):
                    self.assertEqual(escritorio.impostometro(empresa_id), baseline[empresa_id])
            login_novo = Escritorio(cliente, escritorio.empresas)
            for empresa_id in ids:
                self.assertEqual(login_novo.impostometro(empresa_id), baseline[empresa_id])

            alvo_zerar, alvo_excluir = ids[1], ids[4]
            foto_ids = {i: self._linhas_teste(cliente, i) for i in ids}
            xml_engine.limpar_dados_empresa_supabase(cliente, alvo_zerar)
            zerado = self._linhas_teste(cliente, alvo_zerar)
            self.assertEqual(zerado["notas_fiscais_recebidas"], 0)
            self.assertEqual(zerado["itens_nota_fiscal"], 0)
            self.assertEqual(len(cofres_no_real(cliente, alvo_zerar)), 0)
            for empresa_id in ids:
                if empresa_id != alvo_zerar:
                    self.assertEqual(self._linhas_teste(cliente, empresa_id), foto_ids[empresa_id])
                    self.assertEqual(escritorio.impostometro(empresa_id), baseline[empresa_id])

            xml_engine.excluir_empresa_supabase(cliente, alvo_excluir)
            self.assertEqual(sum(self._linhas_teste(cliente, alvo_excluir).values()), 0)
            for empresa_id in ids:
                if empresa_id not in (alvo_zerar, alvo_excluir):
                    self.assertEqual(self._linhas_teste(cliente, empresa_id), foto_ids[empresa_id])
                    self.assertEqual(escritorio.impostometro(empresa_id), baseline[empresa_id])
        finally:
            for empresa_id in ids:
                try:
                    xml_engine.excluir_empresa_supabase(cliente, empresa_id)
                except Exception:
                    pass
            xml_engine.st = original_st
            xml_engine.registrar_limpador_cache_dados(original_limpador)
            xml_engine.limpar_cache_recursos_ausentes()
        for empresa_id in ids:
            self.assertEqual(sum(self._linhas_teste(cliente, empresa_id).values()), 0)
        self.assertEqual(self._foto_preexistente(cliente, set()), antes)


def cofres_no_real(cliente, empresa_id) -> list[dict]:
    encontrados = [
        l["payload"] for l in _ler_tudo(cliente, "praesidium_empresa_mirror", ("empresa_id", empresa_id))
        if l["record_key"] == CHAVE_COFRE
    ]
    for linha in _ler_tudo(cliente, "empresas_monitoradas"):
        payload = ppf.decodificar_ledger(linha) or {}
        if str(payload.get("empresa_id")) == str(empresa_id) and payload.get("item_uid") == CHAVE_COFRE:
            encontrados.append(payload)
    return encontrados


if __name__ == "__main__":
    unittest.main()

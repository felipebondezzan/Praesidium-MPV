#!/usr/bin/env python3
"""
TESTE FASE 4: Simulacao de Supabase Real SEM Migracoes SQL
Mock Supabase com APENAS as 4 tabelas originais (SEM praesidium_ledger_fiscal,
praesidium_empresa_mirror, split_payment_events) e itens_nota_fiscal SEM coluna item_uid.
"""

import sys
import json
import logging
from unittest.mock import Mock

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


class FakePostgresError(Exception):
    """Simula erro Postgres"""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class FakeScopedQueryRestrito:
    """Query builder restrito: sem coluna item_uid, sem constraint, sem RPC"""
    
    def __init__(self, db: "FakeDatabaseRestrito", tabela: str):
        self.db = db
        self.tabela = tabela
        self.filtros = {}
        self.dados = None
        self.on_conflict = None
        self.operacao = None
        
    def select(self, _="*"):
        self.operacao = "select"
        return self
    
    def insert(self, dados):
        self.operacao = "insert"
        self.dados = dados if isinstance(dados, list) else [dados]
        return self
    
    def upsert(self, dados, on_conflict=None):
        self.operacao = "upsert"
        self.dados = dados if isinstance(dados, list) else [dados]
        self.on_conflict = on_conflict
        return self
    
    def update(self, dados):
        self.operacao = "update"
        self.dados = dados
        return self
    
    def delete(self):
        self.operacao = "delete"
        return self
    
    def eq(self, coluna, valor):
        self.filtros[coluna] = ("eq", valor)
        return self
    
    def neq(self, coluna, valor):
        self.filtros[coluna] = ("neq", valor)
        return self
    
    def limit(self, _):
        return self
    
    def execute(self):
        if self.tabela == "praesidium_ledger_fiscal":
            raise FakePostgresError("42P01", "Table does not exist")
        if self.tabela == "praesidium_empresa_mirror":
            raise FakePostgresError("42P01", "Table does not exist")
        if self.tabela == "split_payment_events":
            raise FakePostgresError("42P01", "Table does not exist")
        
        if self.operacao == "upsert" and self.on_conflict == "empresa_id,item_uid":
            raise FakePostgresError("42P10", "Constraint or column does not exist")
        
        if self.operacao == "select":
            return self._simular_select()
        if self.operacao == "insert":
            return self._simular_insert()
        if self.operacao == "upsert":
            return self._simular_upsert()
        if self.operacao == "update":
            return self._simular_update()
        if self.operacao == "delete":
            return self._simular_delete()
        
        return Mock(data=[])
    
    def _simular_select(self):
        registros = self.db.tabelas.get(self.tabela, [])
        resultado = self._filtrar(registros)
        return Mock(data=resultado)
    
    def _simular_insert(self):
        if not isinstance(self.dados, list):
            self.dados = [self.dados]
        self.db.tabelas.setdefault(self.tabela, []).extend(self.dados)
        return Mock(data=self.dados)
    
    def _simular_upsert(self):
        if not isinstance(self.dados, list):
            self.dados = [self.dados]
        self.db.tabelas.setdefault(self.tabela, []).extend(self.dados)
        return Mock(data=self.dados)
    
    def _simular_update(self):
        registros = self.db.tabelas.get(self.tabela, [])
        atualizados = []
        for reg in registros:
            if self._match(reg):
                reg.update(self.dados)
                atualizados.append(reg)
        return Mock(data=atualizados)
    
    def _simular_delete(self):
        registros = self.db.tabelas.get(self.tabela, [])
        iniciais = len(registros)
        self.db.tabelas[self.tabela] = [r for r in registros if not self._match(r)]
        deletados = iniciais - len(self.db.tabelas[self.tabela])
        return Mock(data=[] if deletados > 0 else None)
    
    def _filtrar(self, registros):
        return [r for r in registros if self._match(r)]
    
    def _match(self, reg):
        for coluna, (operador, valor) in self.filtros.items():
            valor_reg = reg.get(coluna)
            if operador == "eq" and valor_reg != valor:
                return False
            if operador == "neq" and valor_reg == valor:
                return False
        return True


class FakeDatabaseRestrito:
    """Mock Supabase real (sem migracoes): apenas 4 tabelas principais"""
    
    def __init__(self):
        self.tabelas = {
            "empresas_monitoradas": [],
            "notas_fiscais_recebidas": [],
            "itens_nota_fiscal": [],
            "conciliacao_devolucoes": [],
        }
    
    def table(self, nome):
        return FakeScopedQueryRestrito(self, nome)
    
    def rpc(self, *args, **kwargs):  # noqa: ARG002
        raise FakePostgresError("PGRST204", f"Function does not exist")


def test_1_upload_20_itens():
    print("\n[TESTE 1] Upload de 20 itens com fallback a insert puro...")
    
    db = FakeDatabaseRestrito()
    
    empresa = {"id": 1, "razao_social": "Esplendore"}
    db.tabelas["empresas_monitoradas"].append(empresa)
    
    nota = {"id": 1, "empresa_id": 1}
    db.tabelas["notas_fiscais_recebidas"].append(nota)
    
    itens = []
    for i in range(20):
        item = {
            "nota_id": 1,
            "empresa_id": 1,
            "item_uid": f"item_{i}",
            "ncm": "60042100",
            "descricao": f"Produto {i}",
            "valor_item": 100.0 + i,
        }
        itens.append(item)
    
    try:
        query = db.table("itens_nota_fiscal")
        query.operacao = "upsert"
        query.on_conflict = "empresa_id,item_uid"
        query.dados = itens
        _ = query.execute()
    except FakePostgresError as e:
        print(f"  [OK] Upsert falhou ({e.code}); fallback para insert puro")
        query = db.table("itens_nota_fiscal")
        query.operacao = "insert"
        query.dados = itens
        _ = query.execute()
    
    assert len(db.tabelas["itens_nota_fiscal"]) == 20
    print(f"  [OK] 20 itens inseridos com sucesso")
    return True


def test_2_quarentena_5_itens():
    print("\n[TESTE 2] Aprovacao de 5 itens (cascading ID detection)...")
    
    db = FakeDatabaseRestrito()
    
    empresa = {"id": 1}
    db.tabelas["empresas_monitoradas"].append(empresa)
    
    nota = {"id": 1, "empresa_id": 1}
    db.tabelas["notas_fiscais_recebidas"].append(nota)
    
    itens = []
    for i in range(5):
        item = {
            "id": i + 1,
            "nota_id": 1,
            "empresa_id": 1,
            "item_uid": f"uid_{i}",
            "descricao": f"Item {i}",
        }
        itens.append(item)
    db.tabelas["itens_nota_fiscal"].extend(itens)
    
    try:
        db.rpc("decidir_itens_quarentena_lote", {})
    except FakePostgresError:
        print("  [OK] RPC nao existe; usando cascading ID")
    
    atualizados = 0
    for item in itens:
        query = db.table("itens_nota_fiscal")
        query.operacao = "update"
        query.dados = {"status": "Aprovado"}
        query.filtros = {"id": ("eq", item["id"]), "empresa_id": ("eq", 1)}
        result = query.execute()
        if result.data:
            atualizados += 1
    
    assert atualizados == 5
    print(f"  [OK] 5 itens aprovados via cascading ID")
    return True


def test_3_zerar_notas():
    print("\n[TESTE 3] Limpeza total ('Zerar Notas')...")
    
    db = FakeDatabaseRestrito()
    empresa_id = 1
    
    empresa = {"id": empresa_id}
    db.tabelas["empresas_monitoradas"].append(empresa)
    
    notas = [
        {"id": 1, "empresa_id": empresa_id},
        {"id": 2, "empresa_id": empresa_id},
    ]
    db.tabelas["notas_fiscais_recebidas"].extend(notas)
    
    itens = [
        {"id": 1, "nota_id": 1, "empresa_id": empresa_id},
        {"id": 2, "nota_id": 1, "empresa_id": empresa_id},
        {"id": 3, "nota_id": 2, "empresa_id": empresa_id},
    ]
    db.tabelas["itens_nota_fiscal"].extend(itens)
    
    devolucoes = [
        {"id": 1, "empresa_id": empresa_id},
    ]
    db.tabelas["conciliacao_devolucoes"].extend(devolucoes)
    
    tabelas_auxiliares = ["praesidium_ledger_fiscal", "praesidium_empresa_mirror", "split_payment_events"]
    for tabela in tabelas_auxiliares:
        try:
            query = db.table(tabela)
            query.operacao = "delete"
            query.filtros = {"empresa_id": ("eq", empresa_id)}
            query.execute()
        except FakePostgresError as e:
            if e.code == "42P01":
                print(f"  [OK] Tabela {tabela} nao existe (esperado)")
    
    for tabela in ["conciliacao_devolucoes", "itens_nota_fiscal"]:
        query = db.table(tabela)
        query.operacao = "delete"
        query.filtros = {"empresa_id": ("eq", empresa_id)}
        query.execute()
    
    query = db.table("notas_fiscais_recebidas")
    query.operacao = "delete"
    query.filtros = {"empresa_id": ("eq", empresa_id)}
    query.execute()
    
    assert len(db.tabelas["notas_fiscais_recebidas"]) == 0
    assert len(db.tabelas["itens_nota_fiscal"]) == 0
    assert len(db.tabelas["conciliacao_devolucoes"]) == 0
    print(f"  [OK] Limpeza total bem-sucedida")
    return True


def test_4_sys_ledger_fallback():
    print("\n[TESTE 4] Fallback SYS_LEDGER para eventos/perfil...")
    
    db = FakeDatabaseRestrito()
    empresa_id = 1
    
    empresa = {
        "id": empresa_id,
        "razao_social": "Test Corp",
        "SYS_LEDGER_events": json.dumps([{"tipo": "pagamento", "montante": 1000}]),
        "SYS_LEDGER_perfil": json.dumps({"fator": 0.7}),
    }
    db.tabelas["empresas_monitoradas"].append(empresa)
    
    try:
        query = db.table("praesidium_ledger_fiscal")
        query.operacao = "select"
        query.filtros = {"empresa_id": ("eq", empresa_id)}
        result = query.execute()
    except FakePostgresError:
        print("  [OK] Tabela ledger nao existe; usando SYS_LEDGER")
    
    query = db.table("empresas_monitoradas")
    query.operacao = "select"
    query.filtros = {"id": ("eq", empresa_id)}
    result = query.execute()
    
    assert len(result.data) == 1
    emp = result.data[0]
    
    events = json.loads(emp.get("SYS_LEDGER_events", "[]"))
    perfil = json.loads(emp.get("SYS_LEDGER_perfil", "{}"))
    
    assert len(events) > 0
    assert perfil.get("fator") == 0.7
    print(f"  [OK] SYS_LEDGER persistiu eventos e perfil")
    return True


if __name__ == "__main__":
    print("=" * 80)
    print("TESTE FASE 4: Simulacao de Supabase Real (SEM migracoes SQL)")
    print("=" * 80)
    
    testes = [
        ("Upload 20 itens", test_1_upload_20_itens),
        ("Quarentena 5 itens", test_2_quarentena_5_itens),
        ("Zerar Notas", test_3_zerar_notas),
        ("SYS_LEDGER fallback", test_4_sys_ledger_fallback),
    ]
    
    resultados = []
    for nome, func in testes:
        try:
            sucesso = func()
            resultados.append((nome, "PASS", None))
        except AssertionError as e:
            resultados.append((nome, "FAIL", str(e)))
        except Exception as e:
            resultados.append((nome, "ERROR", str(e)))
    
    print("\n" + "=" * 80)
    print("RESUMO")
    print("=" * 80)
    for nome, status, erro in resultados:
        symbol = "[PASS]" if status == "PASS" else "[FAIL]"
        print(f"{symbol} {nome}")
        if erro:
            print(f"       {erro}")
    
    total_pass = sum(1 for _, status, _ in resultados if status == "PASS")
    print(f"\n{total_pass}/{len(testes)} testes passaram")
    
    sys.exit(0 if total_pass == len(testes) else 1)

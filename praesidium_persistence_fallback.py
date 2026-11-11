#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
praesidium_persistence_fallback.py
==================================
Camada de persistência para Praesidium que funciona APENAS com empresas_monitoradas.

Armazena notas, itens e eventos como registros especiais em empresas_monitoradas:
- [SYS_NOTA]<json>  -> notas fiscais
- [SYS_ITEM]<json>  -> itens das notas
- [SYS_EVENT]<json> -> eventos financeiros
- [SYS_LEDGER]<json> -> perfil tributário da empresa

Este é um fallback robusto que permite o sistema funcionar 100% sem migrações SQL!
"""

import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Prefixos dos registros de sistema
PREFIXO_NOTA_SISTEMA = "[SYS_NOTA]"
PREFIXO_ITEM_SISTEMA = "[SYS_ITEM]"
PREFIXO_EVENT_SISTEMA = "[SYS_EVENT]"
PREFIXO_LEDGER_SISTEMA = "[SYS_LEDGER]"

# Constantes
REGIME_TRIBUTARIO_NOTA = "SYS_NOTA"
REGIME_TRIBUTARIO_ITEM = "SYS_ITEM"
REGIME_TRIBUTARIO_EVENT = "SYS_EVENT"
REGIME_TRIBUTARIO_LEDGER = "SYS_LEDGER"


def eh_registro_sistema(razao_social: str, regime_tributario: str) -> bool:
    """Verifica se um registro é do sistema (não é cliente real)."""
    razao = str(razao_social or "").strip()
    regime = str(regime_tributario or "").strip()
    
    # Registros de sistema
    return (
        razao.startswith(("[SYS_", "[DELETADO]"))
        or regime.startswith("SYS_")
        or razao.startswith(("SYS_", "DELETADO"))
    )


def eh_cliente_real(razao_social: str, regime_tributario: str, cnae: str) -> bool:
    """Verifica se um registro é uma empresa cliente real."""
    if eh_registro_sistema(razao_social, regime_tributario):
        return False
    cnae_str = str(cnae or "").strip()
    if cnae_str == "0000000":
        return False
    return True


def codificar_nota(
    empresa_id: str,
    nota_id: str,
    chave_acesso: str,
    numero_nf: str,
    serie_nf: str,
    valor_total: float,
    cnpj_fornecedor: str,
    razao_fornecedor: str,
    status: str = "Processada",
    data_emissao: Optional[str] = None,
    data_recebimento: Optional[str] = None,
) -> dict:
    """Cria um registro de nota fiscal no formato de empresa_monitoradas."""
    if data_emissao is None:
        data_emissao = datetime.now(timezone.utc).isoformat()
    if data_recebimento is None:
        data_recebimento = datetime.now(timezone.utc).isoformat()
    
    payload = {
        "nota_id": nota_id,
        "empresa_id": empresa_id,
        "chave_acesso": chave_acesso,
        "numero_nf": numero_nf,
        "serie_nf": serie_nf,
        "valor_total": float(valor_total),
        "cnpj_fornecedor": cnpj_fornecedor,
        "razao_fornecedor": razao_fornecedor,
        "status": status,
        "data_emissao": data_emissao,
        "data_recebimento": data_recebimento,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    return {
        "razao_social": f"{PREFIXO_NOTA_SISTEMA}{json.dumps(payload)}",
        "regime_tributario": REGIME_TRIBUTARIO_NOTA,
        "cnae_principal": empresa_id,  # Referência para enterprise pai
        "cnpj": chave_acesso,  # Identificador único da nota
    }


def decodificar_nota(row: dict) -> Optional[dict]:
    """Extrai uma nota fiscal de um registro de empresas_monitoradas."""
    razao_social = str(row.get("razao_social", "")).strip()
    regime = str(row.get("regime_tributario", "")).strip()
    
    if regime != REGIME_TRIBUTARIO_NOTA or not razao_social.startswith(PREFIXO_NOTA_SISTEMA):
        return None
    
    try:
        conteudo = razao_social[len(PREFIXO_NOTA_SISTEMA):]
        payload = json.loads(conteudo)
        payload["_sys_id"] = row.get("id")  # ID real do registro no Supabase
        return payload
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(f"Erro ao decodificar nota: {e}")
        return None


def codificar_item(
    nota_id: str,
    empresa_id: str,
    item_uid: str,
    ncm: str,
    descricao: str,
    valor_item: float,
    aliquota_ibs: float = 0.0,
    aliquota_cbs: float = 0.0,
    status_credito: str = "Crédito Validado",
    classificacao_item: str = "Operacional",
    credito_ibs_gerado: float = 0.0,
    credito_cbs_gerado: float = 0.0,
) -> dict:
    """Cria um registro de item da nota no formato de empresa_monitoradas."""
    payload = {
        "item_uid": item_uid,
        "nota_id": nota_id,
        "empresa_id": empresa_id,
        "ncm": ncm,
        "descricao": descricao,
        "valor_item": float(valor_item),
        "aliquota_ibs": float(aliquota_ibs),
        "aliquota_cbs": float(aliquota_cbs),
        "status_credito": status_credito,
        "classificacao_item": classificacao_item,
        "credito_ibs_gerado": float(credito_ibs_gerado),
        "credito_cbs_gerado": float(credito_cbs_gerado),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    return {
        "razao_social": f"{PREFIXO_ITEM_SISTEMA}{json.dumps(payload)}",
        "regime_tributario": REGIME_TRIBUTARIO_ITEM,
        "cnae_principal": nota_id,  # Referência para nota pai
        "cnpj": item_uid,  # Identificador único do item
    }


def decodificar_item(row: dict) -> Optional[dict]:
    """Extrai um item da nota de um registro de empresas_monitoradas."""
    razao_social = str(row.get("razao_social", "")).strip()
    regime = str(row.get("regime_tributario", "")).strip()
    
    if regime != REGIME_TRIBUTARIO_ITEM or not razao_social.startswith(PREFIXO_ITEM_SISTEMA):
        return None
    
    try:
        conteudo = razao_social[len(PREFIXO_ITEM_SISTEMA):]
        payload = json.loads(conteudo)
        payload["_sys_id"] = row.get("id")  # ID real do registro no Supabase
        return payload
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(f"Erro ao decodificar item: {e}")
        return None


def codificar_ledger(
    empresa_id: str,
    aliquota_ibs: float = 0.17,
    aliquota_cbs: float = 0.088,
    redutor_percentual: float = 1.0,
    regime_tributario: str = "LP",
) -> dict:
    """Cria um registro de perfil tributário (ledger) da empresa."""
    payload = {
        "empresa_id": empresa_id,
        "aliquota_ibs": float(aliquota_ibs),
        "aliquota_cbs": float(aliquota_cbs),
        "redutor_percentual": float(redutor_percentual),
        "regime_tributario": regime_tributario,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    return {
        "razao_social": f"{PREFIXO_LEDGER_SISTEMA}{json.dumps(payload)}",
        "regime_tributario": REGIME_TRIBUTARIO_LEDGER,
        "cnae_principal": empresa_id,  # Referência da empresa
        "cnpj": f"SYS_LEDGER_{empresa_id[:8]}",  # Identificador único
    }


def decodificar_ledger(row: dict) -> Optional[dict]:
    """Extrai o perfil tributário de um registro de empresas_monitoradas."""
    razao_social = str(row.get("razao_social", "")).strip()
    regime = str(row.get("regime_tributario", "")).strip()
    
    if regime != REGIME_TRIBUTARIO_LEDGER or not razao_social.startswith(PREFIXO_LEDGER_SISTEMA):
        return None
    
    try:
        conteudo = razao_social[len(PREFIXO_LEDGER_SISTEMA):]
        payload = json.loads(conteudo)
        payload["_sys_id"] = row.get("id")  # ID real do registro no Supabase
        return payload
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(f"Erro ao decodificar ledger: {e}")
        return None


def codificar_evento_financeiro(
    empresa_id: str,
    nota_id: str,
    tipo_evento: str,  # "pagamento_parcial", "renegociacao", "antecipacao", "devolucao"
    valor_operacao: float,
    valor_liquido: float,
    percentual_liquidado: float,
    descricao: str = "",
) -> dict:
    """Cria um registro de evento financeiro (liquidação, renegociação, etc)."""
    event_id = str(uuid.uuid4())
    payload = {
        "event_id": event_id,
        "empresa_id": empresa_id,
        "nota_id": nota_id,
        "tipo_evento": tipo_evento,
        "valor_operacao": float(valor_operacao),
        "valor_liquido": float(valor_liquido),
        "percentual_liquidado": float(percentual_liquidado),
        "descricao": descricao,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    return {
        "razao_social": f"{PREFIXO_EVENT_SISTEMA}{json.dumps(payload)}",
        "regime_tributario": REGIME_TRIBUTARIO_EVENT,
        "cnae_principal": empresa_id,  # Referência da empresa
        "cnpj": event_id,  # Identificador único do evento
    }


def decodificar_evento_financeiro(row: dict) -> Optional[dict]:
    """Extrai um evento financeiro de um registro de empresas_monitoradas."""
    razao_social = str(row.get("razao_social", "")).strip()
    regime = str(row.get("regime_tributario", "")).strip()
    
    if regime != REGIME_TRIBUTARIO_EVENT or not razao_social.startswith(PREFIXO_EVENT_SISTEMA):
        return None
    
    try:
        conteudo = razao_social[len(PREFIXO_EVENT_SISTEMA):]
        payload = json.loads(conteudo)
        payload["_sys_id"] = row.get("id")  # ID real do registro no Supabase
        return payload
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(f"Erro ao decodificar evento financeiro: {e}")
        return None


if __name__ == "__main__":
    # Teste rápido de codificação/decodificação
    print("Teste de Codificação/Decodificação")
    print("="*50)
    
    # Teste de nota
    nota_reg = codificar_nota(
        empresa_id="emp-123",
        nota_id="nota-456",
        chave_acesso="35240201234567000100550010000000011234567890",
        numero_nf="00000001",
        serie_nf="1",
        valor_total=1000.00,
        cnpj_fornecedor="12345678000190",
        razao_fornecedor="Fornecedor Teste LTDA",
    )
    print(f"\nNota Codificada:")
    print(f"  razao_social: {nota_reg['razao_social'][:100]}...")
    print(f"  regime_tributario: {nota_reg['regime_tributario']}")
    
    nota_dec = decodificar_nota(nota_reg)
    print(f"\nNota Decodificada:")
    print(f"  nota_id: {nota_dec.get('nota_id')}")
    print(f"  valor_total: {nota_dec.get('valor_total')}")
    
    # Teste de item
    item_reg = codificar_item(
        nota_id="nota-456",
        empresa_id="emp-123",
        item_uid=str(uuid.uuid4()),
        ncm="12345678",
        descricao="Produto Teste",
        valor_item=500.00,
        status_credito="Crédito Validado",
    )
    print(f"\nItem Codificado:")
    print(f"  razao_social: {item_reg['razao_social'][:100]}...")
    print(f"  regime_tributario: {item_reg['regime_tributario']}")
    
    item_dec = decodificar_item(item_reg)
    print(f"\nItem Decodificado:")
    print(f"  item_uid: {item_dec.get('item_uid')[:8]}...")
    print(f"  descricao: {item_dec.get('descricao')}")
    
    # Teste de ledger
    ledger_reg = codificar_ledger(
        empresa_id="emp-123",
        aliquota_ibs=0.177,
        aliquota_cbs=0.088,
    )
    print(f"\nLedger Codificado:")
    print(f"  razao_social: {ledger_reg['razao_social'][:100]}...")
    
    ledger_dec = decodificar_ledger(ledger_reg)
    print(f"\nLedger Decodificado:")
    print(f"  aliquota_ibs: {ledger_dec.get('aliquota_ibs')}")
    
    print("\n" + "="*50)
    print("[OK] Codificacao/Decodificacao OK!")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
praesidium_persistence_fallback.py
==================================
Registros de sistema armazenados em `empresas_monitoradas` para dados que não
possuem tabela própria no Supabase real:

- [SYS_AUTH]   -> contas de usuário (criadas em app.py)
- [SYS_LOG]    -> log de acessos (criado em app.py)
- [SYS_LEDGER] -> chave/valor por empresa (perfil tributário, eventos de liquidação)

Restrições do schema real respeitadas aqui:
- `empresas_monitoradas.cnpj` é VARCHAR(14): todo identificador de sistema tem
  no máximo 14 caracteres e é derivado de um hash determinístico.
- `empresas_monitoradas` não possui a coluna `empresa_id`: a referência à empresa
  fica dentro do JSON de `razao_social`.
- `cnae_principal` recebe sempre "0000000" nos registros de sistema.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

TAMANHO_MAXIMO_CNPJ = 14
CNAE_REGISTRO_SISTEMA = "0000000"

PREFIXO_LEDGER_SISTEMA = "[SYS_LEDGER]"
REGIME_TRIBUTARIO_LEDGER = "SYS_LEDGER"
REGIME_TRIBUTARIO_AUTH = "SYS_AUTH"
REGIME_TRIBUTARIO_LOG = "SYS_LOG"

CHAVE_LEDGER_PERFIL_TRIBUTARIO = "config:aliquota-ibs-cbs:v1"
CHAVE_LEDGER_COFRE_SALDO = "config:saldo-consolidado:v1"
TIPO_REGISTRO_COFRE_SALDO = "company_balance_vault"


def eh_registro_sistema(razao_social: str, regime_tributario: str) -> bool:
    """Verifica se um registro é do sistema (não é cliente real)."""
    razao = str(razao_social or "").strip()
    regime = str(regime_tributario or "").strip()
    return (
        razao.startswith(("[SYS_", "[DELETADO]"))
        or regime.startswith("SYS_")
        or razao.startswith(("SYS_", "DELETADO"))
    )


def eh_cliente_real(razao_social: str, regime_tributario: str, cnae: str) -> bool:
    """Verifica se um registro é uma empresa cliente real."""
    if eh_registro_sistema(razao_social, regime_tributario):
        return False
    return str(cnae or "").strip() != CNAE_REGISTRO_SISTEMA


def identificador_sistema(prefixo: str, chave: str) -> str:
    """Identificador determinístico com no máximo 14 caracteres (cabe em cnpj)."""
    restante = TAMANHO_MAXIMO_CNPJ - len(prefixo)
    digest = hashlib.sha256(str(chave).encode("utf-8")).hexdigest()[:restante]
    return f"{prefixo}{digest}"


def cnpj_usuario_sistema(usuario: str) -> str:
    return identificador_sistema("UA", str(usuario).strip().lower())


def prefixo_cnpj_ledger_empresa(empresa_id) -> str:
    return identificador_sistema("L", str(empresa_id))[:6]


def cnpj_registro_ledger(empresa_id, chave: str) -> str:
    """Prefixo da empresa (6) + hash da chave (8): permite listar por empresa."""
    sufixo = hashlib.sha256(
        f"{empresa_id}|{chave}".encode("utf-8")
    ).hexdigest()[: TAMANHO_MAXIMO_CNPJ - 6]
    return f"{prefixo_cnpj_ledger_empresa(empresa_id)}{sufixo}"


def codificar_registro_ledger(empresa_id, chave: str, payload: dict) -> dict:
    """Linha de empresas_monitoradas que guarda `payload` sob (empresa_id, chave)."""
    conteudo = {
        **payload,
        "empresa_id": str(empresa_id),
        "item_uid": chave,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "razao_social": PREFIXO_LEDGER_SISTEMA + json.dumps(
            conteudo,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ),
        "regime_tributario": REGIME_TRIBUTARIO_LEDGER,
        "cnae_principal": CNAE_REGISTRO_SISTEMA,
        "cnpj": cnpj_registro_ledger(empresa_id, chave),
    }


def decodificar_ledger(row: dict) -> Optional[dict]:
    """Extrai o payload de um registro [SYS_LEDGER] de empresas_monitoradas."""
    razao_social = str(row.get("razao_social", "")).strip()
    regime = str(row.get("regime_tributario", "")).strip()

    if regime != REGIME_TRIBUTARIO_LEDGER or not razao_social.startswith(
        PREFIXO_LEDGER_SISTEMA
    ):
        return None

    try:
        payload = json.loads(razao_social[len(PREFIXO_LEDGER_SISTEMA):])
    except (json.JSONDecodeError, ValueError, TypeError) as erro:
        logger.warning("Erro ao decodificar ledger: %s", erro)
        return None
    if not isinstance(payload, dict):
        return None
    payload["_sys_id"] = row.get("id")
    return payload

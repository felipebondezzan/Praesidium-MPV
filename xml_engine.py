import base64
import hashlib
import hmac
import io
import json
import logging
import math
import random
import re
import threading
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from datetime import timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from xml.sax.saxutils import escape as escape_xml
import requests
import streamlit as st
import pandas as pd

VERSAO_REGRA_TRIBUTARIA = "LC 214/2025 - Art. 47/57 v1.0"
ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA = 0.265
ALIQUOTAS_REFORMA_TRIBUTARIA = {
    "Base Padrão Integral — 26,50% (Sem redutor)": {
        "fator_reducao": 1.0,
        "reducao_percentual": 0.0,
    },
    "Derivação Redução 30% (Profissões Regulamentadas / Serviços) — Alíquota Efetiva 18,55%": {
        "fator_reducao": 0.7,
        "reducao_percentual": 30.0,
    },
    "Derivação Redução 60% (Saúde, Educação, Insumos Agro/Higiene) — Alíquota Efetiva 10,60%": {
        "fator_reducao": 0.4,
        "reducao_percentual": 60.0,
    },
    "Regime Específico / Alíquota Zero — 0,00%": {
        "fator_reducao": 0.0,
        "reducao_percentual": 100.0,
    },
}
TAG_TENANT_PATTERN = re.compile(r"\s*\[T:[^\]]+\]", re.IGNORECASE)
CHAVE_ACESSO_PATTERN = re.compile(r"\d{44}")
DESCRICAO_USO_INTERNO_PATTERN = re.compile(
    r"\b(?:uso\s+interno|consumo\s+interno|uso\s+proprio|uso\s+pr[oó]prio|"
    r"ativo\s+imobilizado|equipamento\s+administrativo)\b",
    re.IGNORECASE,
)
CODIGO_ERRO_PATTERN = re.compile(r"""['"]code['"]\s*:\s*['"]([A-Z0-9]+)['"]""")
COLUNA_ERRO_PATTERNS = (
    re.compile(r"""column\s+['"]([^'"]+)['"]""", re.IGNORECASE),
    re.compile(r"""['"]([^'"]+)['"]\s+column""", re.IGNORECASE),
)

_limpador_cache_dados = None
_gateway_rate_lock = threading.Lock()
_gateway_last_request_by_tenant: dict[str, float] = {}
_gateway_request_interval = 1.0
_gateway_max_retries = 2
_CNPJ_PATTERN = re.compile(r"\D")


def _ler_configuracao_secreta(nome: str) -> str:
    try:
        valor = st.secrets[nome]
    except Exception:
        valor = None
    if valor:
        return str(valor).strip()
    import os

    return (os.environ.get(nome) or "").strip()


class FiscalGatewayClient:
    """Pool HTTP limitado por tenant para gateways fiscais autorizados.

    Não consulta a SEFAZ diretamente nem usa proxies rotativos. O contrato
    específico do fornecedor fica a cargo do caminho/configuração do gateway.
    """

    def __init__(self, base_url: str, api_key: str):
        parsed_url = urlparse(base_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ValueError("A URL do gateway fiscal deve usar HTTPS.")
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=8,
            pool_maxsize=16,
            max_retries=0,
        )
        self.session.mount("https://", adapter)

    def request(
        self,
        method: str,
        path: str,
        tenant_id: str,
        authorized_cnpjs: list[str],
        *,
        json_body: dict | None = None,
        idempotency_key: str | None = None,
        timeout: tuple[float, float] = (3.05, 15.0),
    ) -> requests.Response:
        metodo = method.upper()
        if metodo not in {"GET", "POST"}:
            raise ValueError("O gateway fiscal aceita somente GET ou POST.")
        if metodo == "POST" and (not json_body or not idempotency_key):
            raise ValueError("O POST do gateway exige corpo JSON e chave idempotente.")
        cnpjs = sorted({
            _CNPJ_PATTERN.sub("", str(cnpj))
            for cnpj in authorized_cnpjs
            if len(_CNPJ_PATTERN.sub("", str(cnpj))) == 14
        })
        if not cnpjs:
            raise ValueError("Nenhum CNPJ autorizado foi informado ao gateway.")
        relative_path = str(path or "").lstrip("/")
        url = urljoin(self.base_url, relative_path)
        if urlparse(url).netloc != urlparse(self.base_url).netloc:
            raise ValueError("O caminho do gateway deve permanecer no host configurado.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "X-Tenant-ID": str(tenant_id),
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        ultimo_erro = None
        for tentativa in range(_gateway_max_retries + 1):
            with _gateway_rate_lock:
                agora = time.monotonic()
                anterior = _gateway_last_request_by_tenant.get(str(tenant_id), 0.0)
                instante = max(agora, anterior + _gateway_request_interval)
                _gateway_last_request_by_tenant[str(tenant_id)] = instante
            if instante > agora:
                time.sleep(instante - agora)

            try:
                resposta = self.session.request(
                    metodo,
                    url,
                    headers=headers,
                    params={"cnpjs": ",".join(cnpjs)},
                    json=json_body,
                    timeout=timeout,
                )
            except requests.RequestException as erro:
                ultimo_erro = erro
                if tentativa >= _gateway_max_retries:
                    raise
                time.sleep(0.25 * (2 ** tentativa))
                continue

            if resposta.status_code not in {429, 502, 503, 504}:
                resposta.raise_for_status()
                return resposta
            if tentativa >= _gateway_max_retries:
                resposta.raise_for_status()
            retry_after = resposta.headers.get("Retry-After", "")
            try:
                espera_retry = min(2.0, max(0.0, float(retry_after)))
            except ValueError:
                try:
                    data_retry = parsedate_to_datetime(retry_after)
                    espera_retry = min(
                        2.0,
                        max(0.0, (data_retry - datetime.now(timezone.utc)).total_seconds()),
                    )
                except (TypeError, ValueError, OverflowError):
                    espera_retry = 0.25 * (2 ** tentativa)
            time.sleep(espera_retry)

        if ultimo_erro:
            raise ultimo_erro
        raise requests.RequestException("Falha na comunicação com o gateway fiscal.")


@st.cache_resource(show_spinner=False)
def obter_cliente_gateway_fiscal() -> FiscalGatewayClient:
    base_url = _ler_configuracao_secreta("FISCAL_GATEWAY_URL")
    api_key = _ler_configuracao_secreta("FISCAL_GATEWAY_API_KEY")
    if not base_url or not api_key:
        raise ValueError("Gateway fiscal não configurado.")
    return FiscalGatewayClient(base_url, api_key)


def requisitar_documentos_gateway_fiscal(
    path: str,
    empresa_id_ativa,
    empresa_ativa: dict,
    cnpjs_adicionais_autorizados: list[str] | None = None,
) -> requests.Response:
    if str(empresa_ativa.get("id", "")) != str(empresa_id_ativa):
        raise ValueError("A empresa ativa não corresponde à seleção atual.")
    cnpjs_autorizados = obter_cnpjs_autorizados_empresa(
        empresa_ativa,
        cnpjs_adicionais_autorizados,
    )
    cliente = obter_cliente_gateway_fiscal()
    return cliente.request(
        "GET",
        path,
        str(empresa_id_ativa),
        sorted(cnpjs_autorizados),
    )


def processar_webhook_split_payment(
    supabase,
    corpo_bruto: bytes,
    assinatura: str,
) -> tuple[bool, str, dict]:
    """Valida e grava evento assinado; pronto para ser chamado por endpoint ASGI."""
    segredo = _ler_configuracao_secreta("SPLIT_PAYMENT_WEBHOOK_SECRET")
    if not segredo:
        return False, "Webhook de Split Payment não configurado.", {}
    if not corpo_bruto:
        return False, "O corpo do evento está vazio.", {}
    if len(corpo_bruto) > 1_000_000:
        return False, "O evento excede o limite de tamanho permitido.", {}
    assinatura_recebida = str(assinatura or "").strip()
    if assinatura_recebida.lower().startswith("sha256="):
        assinatura_recebida = assinatura_recebida.split("=", 1)[1]
    assinatura_esperada = hmac.new(
        segredo.encode("utf-8"),
        corpo_bruto,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(assinatura_recebida.lower(), assinatura_esperada):
        return False, "Assinatura do evento inválida.", {}

    try:
        evento = json.loads(corpo_bruto.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, "Formato do evento inválido.", {}
    if not isinstance(evento, dict):
        return False, "Formato do evento inválido.", {}

    evento_id = str(evento.get("event_id") or evento.get("id") or "").strip()
    empresa_id = evento.get("empresa_id")
    cnpj_evento = _CNPJ_PATTERN.sub("", str(evento.get("cnpj") or ""))
    try:
        valor_retido_decimal = Decimal(str(evento.get("valor_retido")))
        valor_bruto_decimal = Decimal(
            str(evento.get("valor_bruto") or evento.get("gross_amount") or 0.0)
        )
    except (InvalidOperation, TypeError, ValueError):
        return False, "Os valores financeiros do evento são inválidos.", {}
    ocorrido_em = str(evento.get("ocorrido_em") or evento.get("occurred_at") or "").strip()
    if (
        not evento_id
        or len(evento_id) > 200
        or empresa_id is None
        or not str(empresa_id).strip()
        or len(cnpj_evento) != 14
        or not ocorrido_em
        or len(str(evento.get("transaction_id") or evento_id)) > 200
        or len(str(evento.get("event_type") or "split_payment.retained")) > 100
        or not valor_retido_decimal.is_finite()
        or valor_retido_decimal <= 0
        or valor_retido_decimal > Decimal("9999999999999999.99")
        or not valor_bruto_decimal.is_finite()
        or valor_bruto_decimal < 0
        or valor_bruto_decimal > Decimal("9999999999999999.99")
    ):
        return False, "O evento não contém os dados financeiros obrigatórios.", {}
    try:
        data_evento = datetime.fromisoformat(ocorrido_em.replace("Z", "+00:00"))
    except ValueError:
        return False, "A data do evento é inválida.", {}
    if data_evento.tzinfo is None:
        data_evento = data_evento.replace(tzinfo=timezone.utc)

    if str(evento.get("currency") or "BRL").upper() != "BRL":
        return False, "Moeda do evento não suportada.", {}
    if valor_bruto_decimal > 0 and valor_retido_decimal > valor_bruto_decimal:
        return False, "A retenção informada supera o valor bruto da transação.", {}
    if supabase is None:
        return False, "Registro do evento indisponível no momento.", {}
    try:
        resposta_empresa = (
            supabase.table("empresas_monitoradas")
            .select("*")
            .eq("id", empresa_id)
            .limit(1)
            .execute()
        )
        empresas = resposta_empresa.data or []
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao validar empresa do evento de Split Payment."
        )
        return False, "Não foi possível validar a empresa do evento.", {}
    if not empresas:
        return False, "Empresa do evento não encontrada.", {}
    empresa = empresas[0]
    if str(empresa.get("id")) != str(empresa_id):
        return False, "Empresa do evento não corresponde ao cadastro informado.", {}
    try:
        resposta_cnpjs = (
            supabase.table("empresa_cnpjs_autorizados")
            .select("cnpj")
            .eq("empresa_id", empresa["id"])
            .execute()
        )
        empresa["cnpjs_autorizados"] = [
            registro.get("cnpj", "")
            for registro in (resposta_cnpjs.data or [])
        ]
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao carregar CNPJs secundários autorizados da empresa %s.",
            empresa_id,
        )
    if cnpj_evento not in obter_cnpjs_autorizados_empresa(empresa):
        return False, "CNPJ do evento não está autorizado para a empresa.", {}

    payload = {
        "empresa_id": empresa["id"],
        "event_id": evento_id,
        "transaction_id": str(evento.get("transaction_id") or evento_id),
        "event_type": str(evento.get("event_type") or "split_payment.retained"),
        "gross_amount": float(
            valor_bruto_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ),
        "retained_amount": float(
            valor_retido_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ),
        "currency": str(evento.get("currency") or "BRL")[:3].upper(),
        "occurred_at": data_evento.isoformat(),
        "payload_sha256": hashlib.sha256(corpo_bruto).hexdigest(),
    }
    payload = sanitizar_payload_supabase("split_payment_events", payload)

    try:
        resultado = (
            supabase.table("split_payment_events")
            .insert(payload)
            .select("*")
            .execute()
        )
        dados = resultado.data or []
        if dados:
            espelhado = persistir_espelho_empresa(
                supabase,
                empresa["id"],
                "split_payment",
                dados,
            )
            if not espelhado:
                logging.getLogger(__name__).error(
                    "Evento de Split Payment foi gravado na tabela primária, "
                    "mas o espelho persistente falhou."
                )
            limpar_caches_dados()
            return True, "Evento de retenção registrado.", dados[0]
        return False, "Não foi possível confirmar o evento de retenção.", {}
    except Exception as erro:
        if _codigo_erro_supabase(erro) == "23505":
            try:
                existente = (
                    supabase.table("split_payment_events")
                    .select("*")
                    .eq("empresa_id", empresa_id)
                    .eq("event_id", evento_id)
                    .limit(1)
                    .execute()
                )
                registros = existente.data or []
                if registros and (
                    round(float(registros[0].get("retained_amount", 0.0)), 2)
                    == payload["retained_amount"]
                    and str(registros[0].get("payload_sha256", ""))
                    == payload["payload_sha256"]
                ):
                    persistir_espelho_empresa(
                        supabase,
                        empresa["id"],
                        "split_payment",
                        registros,
                    )
                    return True, "Evento já processado anteriormente.", registros[0]
            except Exception:
                logging.getLogger(__name__).exception(
                    "Falha ao confirmar a idempotência do evento de Split Payment."
                )
        logging.getLogger(__name__).exception(
            "Falha ao persistir evento de Split Payment para empresa %s.",
            empresa_id,
        )
        if persistir_espelho_empresa(
            supabase,
            empresa["id"],
            "split_payment",
            payload,
        ):
            limpar_caches_dados()
            return True, "Evento de retenção registrado no espelho persistente.", payload
        return False, "Não foi possível registrar o evento de retenção.", {}


def registrar_limpador_cache_dados(func) -> None:
    global _limpador_cache_dados
    _limpador_cache_dados = func


def fragment_if_supported(func):
    fragment = getattr(st, "fragment", None)
    return fragment(func) if callable(fragment) else func


def rerun_fragment() -> None:
    if callable(getattr(st, "fragment", None)):
        st.rerun(scope="fragment")
    else:
        st.rerun()


def limpar_caches_dados() -> None:
    if _limpador_cache_dados is not None:
        _limpador_cache_dados()


def obter_data_referencia_nota(nota: dict):
    """Retorna a data de emissão ou, em dados legados, a data de registro."""
    for campo in ("data_emissao", "created_at"):
        valor = nota.get(campo)
        if valor:
            data = pd.to_datetime(valor, errors="coerce", utc=True)
            if not pd.isna(data):
                return data.tz_convert(None)
    return None


def _arredondar_dinheiro(valor) -> float:
    try:
        decimal = Decimal(str(valor or 0))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0
    if not decimal.is_finite():
        return 0.0
    return float(decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calcular_memoria_aliquota(derivacao: str) -> dict[str, float | str]:
    regra = ALIQUOTAS_REFORMA_TRIBUTARIA.get(
        derivacao,
        ALIQUOTAS_REFORMA_TRIBUTARIA[
            "Base Padrão Integral — 26,50% (Sem redutor)"
        ],
    )
    fator = float(regra["fator_reducao"])
    return {
        "derivacao_setorial": derivacao
        if derivacao in ALIQUOTAS_REFORMA_TRIBUTARIA
        else "Base Padrão Integral — 26,50% (Sem redutor)",
        "aliquota_base": 26.5,
        "fator_reducao": fator,
        "reducao_percentual": float(regra["reducao_percentual"]),
        "aliquota_ibs_base": 17.7,
        "aliquota_cbs_base": 8.8,
        "aliquota_ibs_efetiva": _arredondar_dinheiro(17.7 * fator),
        "aliquota_cbs_efetiva": _arredondar_dinheiro(8.8 * fator),
        "aliquota_efetiva": _arredondar_dinheiro(26.5 * fator),
    }


def calcular_id_operacao(
    empresa_id,
    nota: dict,
    valor_base: float,
    valor_ibs: float,
    valor_cbs: float,
) -> str:
    data_referencia = pd.to_datetime(
        nota.get("data_emissao") or nota.get("created_at"),
        errors="coerce",
        utc=True,
    )
    chave_acesso = str(nota.get("chave_acesso") or "")
    if not pd.isna(data_referencia):
        ano = str(data_referencia.year)
    elif CHAVE_ACESSO_PATTERN.fullmatch(chave_acesso):
        ano = f"20{chave_acesso[2:4]}"
    else:
        ano = str(datetime.now().year)
    identificador = "|".join((
        str(empresa_id),
        str(chave_acesso or nota.get("numero") or nota.get("id") or ""),
        f"{_arredondar_dinheiro(valor_base):.2f}",
        f"{_arredondar_dinheiro(valor_ibs):.2f}",
        f"{_arredondar_dinheiro(valor_cbs):.2f}",
    ))
    digest = hashlib.sha256(identificador.encode("utf-8")).hexdigest()[:8].upper()
    return f"OP-SPLIT-{ano}-{digest}"


def calcular_segregacao_liquidacao(
    valor_total_nota: float,
    valor_ibs_total: float,
    valor_cbs_total: float,
    valor_liquidado: float,
    valor_ja_liquidado: float = 0.0,
) -> dict[str, float]:
    total = max(0.0, _arredondar_dinheiro(valor_total_nota))
    anterior = min(total, max(0.0, _arredondar_dinheiro(valor_ja_liquidado)))
    liquidacao = min(
        max(0.0, _arredondar_dinheiro(valor_liquidado)),
        max(0.0, total - anterior),
    )
    acumulado = min(total, anterior + liquidacao)

    def segregacao(valor_tributo: float) -> tuple[float, float]:
        tributo = max(0.0, _arredondar_dinheiro(valor_tributo))
        antes = (
            _arredondar_dinheiro(tributo * anterior / total)
            if total
            else 0.0
        )
        depois = (
            _arredondar_dinheiro(tributo * acumulado / total)
            if total
            else 0.0
        )
        return _arredondar_dinheiro(depois - antes), _arredondar_dinheiro(
            tributo - depois
        )

    ibs_atual, ibs_pendente = segregacao(valor_ibs_total)
    cbs_atual, cbs_pendente = segregacao(valor_cbs_total)
    return {
        "valor_liquidado": liquidacao,
        "valor_pago_acumulado": acumulado,
        "saldo_em_aberto": _arredondar_dinheiro(total - acumulado),
        "ibs_segregado_atual": ibs_atual,
        "cbs_segregado_atual": cbs_atual,
        "ibs_pendente": ibs_pendente,
        "cbs_pendente": cbs_pendente,
    }


def resumir_eventos_liquidacao(eventos: list[dict]) -> dict[str, dict[str, float | int]]:
    resumo: dict[str, dict[str, float | int]] = {}
    for evento in sorted(
        eventos or [],
        key=lambda registro: str(registro.get("occurred_at") or ""),
    ):
        id_operacao = str(evento.get("id_op") or "").strip()
        if not id_operacao:
            continue
        dados = resumo.setdefault(id_operacao, {
            "valor_pago_acumulado": 0.0,
            "credito_compensado": 0.0,
            "credito_restituido": 0.0,
            "valor_retido_caixa": 0.0,
            "impacto_banco": 0.0,
            "ibs_segregado": 0.0,
            "cbs_segregado": 0.0,
            "valor_negociado": 0.0,
            "eventos": 0,
        })
        tipo = str(evento.get("event_type") or "")
        valor_liquidado = _arredondar_dinheiro(
            evento.get("valor_liquidado")
        )
        if tipo == "Liquidação Parcial/Integral":
            dados["valor_pago_acumulado"] = _arredondar_dinheiro(
                float(dados["valor_pago_acumulado"]) + valor_liquidado
            )
            dados["ibs_segregado"] = _arredondar_dinheiro(
                float(dados["ibs_segregado"])
                + _float_seguro(evento.get("ibs_segregado"), 0.0)
            )
            dados["cbs_segregado"] = _arredondar_dinheiro(
                float(dados["cbs_segregado"])
                + _float_seguro(evento.get("cbs_segregado"), 0.0)
            )
        elif tipo == "Estorno de Transação":
            dados["valor_pago_acumulado"] = _arredondar_dinheiro(
                float(dados["valor_pago_acumulado"]) - valor_liquidado
            )
            dados["ibs_segregado"] = _arredondar_dinheiro(
                float(dados["ibs_segregado"])
                - _float_seguro(evento.get("ibs_segregado"), 0.0)
            )
            dados["cbs_segregado"] = _arredondar_dinheiro(
                float(dados["cbs_segregado"])
                - _float_seguro(evento.get("cbs_segregado"), 0.0)
            )
            dados["credito_restituido"] = _arredondar_dinheiro(
                float(dados["credito_restituido"])
                + _float_seguro(evento.get("credito_restituido"), 0.0)
            )
        elif tipo == "Alteração/Renegociação de Operação":
            dados["valor_negociado"] = _arredondar_dinheiro(
                evento.get("valor_negociado")
            )
        dados["credito_compensado"] = _arredondar_dinheiro(
            float(dados["credito_compensado"])
            + _float_seguro(evento.get("credito_compensado"), 0.0)
        )
        dados["valor_retido_caixa"] = _arredondar_dinheiro(
            float(dados["valor_retido_caixa"])
            + _float_seguro(evento.get("valor_retido_caixa"), 0.0)
        )
        dados["impacto_banco"] = _arredondar_dinheiro(
            float(dados["impacto_banco"])
            + _float_seguro(evento.get("impacto_banco"), 0.0)
        )
        dados["eventos"] = int(dados["eventos"]) + 1
    for dados in resumo.values():
        dados["valor_pago_acumulado"] = max(
            0.0,
            _arredondar_dinheiro(dados["valor_pago_acumulado"]),
        )
        dados["credito_compensado_liquido"] = max(
            0.0,
            _arredondar_dinheiro(
                float(dados["credito_compensado"])
                - float(dados["credito_restituido"])
            ),
        )
    return resumo


def calcular_saldo_credito_digital(
    itens: list[dict],
    devolucoes: list[dict],
    eventos_split: list[dict],
    eventos_liquidacao: list[dict],
) -> dict[str, float]:
    creditos = calcular_resumo_creditos(itens)
    estornado = _arredondar_dinheiro(sum(
        _float_seguro(item.get("credito_estornado"), 0.0)
        for item in devolucoes or []
    ))
    retido_webhook = _arredondar_dinheiro(sum(
        max(0.0, _float_seguro(evento.get("retained_amount"), 0.0))
        for evento in eventos_split or []
    ))
    resumo_operacoes = resumir_eventos_liquidacao(eventos_liquidacao)
    compensado = _arredondar_dinheiro(sum(
        _float_seguro(resumo.get("credito_compensado_liquido"), 0.0)
        for resumo in resumo_operacoes.values()
    ))
    retido_caixa = _arredondar_dinheiro(sum(
        _float_seguro(resumo.get("valor_retido_caixa"), 0.0)
        for resumo in resumo_operacoes.values()
    ))
    retido_caixa = max(0.0, retido_caixa)
    saldo = _arredondar_dinheiro(
        creditos["validado"] - estornado - retido_webhook - compensado
    )
    return {
        **creditos,
        "estornado": estornado,
        "retido_webhook": retido_webhook,
        "credito_compensado": compensado,
        "retido_caixa": retido_caixa,
        "credito_liquido": saldo,
        "credito_disponivel": max(0.0, saldo),
        "valor_retido_split_payment": _arredondar_dinheiro(
            retido_webhook + retido_caixa
        ),
    }


def registrar_evento_liquidacao_supabase(
    supabase,
    empresa_id,
    evento: dict,
) -> dict:
    if supabase is None:
        raise RuntimeError("Supabase indisponível para registrar o evento financeiro.")
    if not empresa_id or str(evento.get("empresa_id")) != str(empresa_id):
        raise ValueError("O evento financeiro não corresponde à empresa ativa.")
    if not str(evento.get("id_op") or "").strip():
        raise ValueError("O evento financeiro não possui ID_OP.")
    if not str(evento.get("event_id") or "").strip():
        raise ValueError("O evento financeiro não possui identificador único.")
    agora = datetime.now(timezone.utc).isoformat()
    evento_persistido = {
        **evento,
        "record_type": "split_payment_operation_event",
        "empresa_id": empresa_id,
        "occurred_at": str(evento.get("occurred_at") or agora),
    }
    resposta = (
        supabase.table("praesidium_ledger_fiscal")
        .upsert(
            {
                "empresa_id": empresa_id,
                "item_uid": f"split-event:{evento_persistido['event_id']}",
                "payload": evento_persistido,
                "updated_at": agora,
            },
            on_conflict="empresa_id,item_uid",
        )
        .select("item_uid")
        .execute()
    )
    if not resposta.data:
        raise RuntimeError("O Supabase não confirmou a gravação do evento financeiro.")
    limpar_caches_dados()
    return evento_persistido


def carregar_perfil_tributario_empresa(supabase, empresa_id) -> dict:
    derivacao_padrao = "Base Padrão Integral — 26,50% (Sem redutor)"
    if supabase is None or not empresa_id:
        return calcular_memoria_aliquota(derivacao_padrao)
    try:
        resposta = (
            supabase.table("praesidium_ledger_fiscal")
            .select("payload")
            .eq("empresa_id", empresa_id)
            .eq("item_uid", "config:aliquota-ibs-cbs:v1")
            .limit(1)
            .execute()
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao carregar o perfil tributário da empresa %s.",
            empresa_id,
        )
        return calcular_memoria_aliquota(derivacao_padrao)
    payload = (resposta.data or [{}])[0].get("payload") or {}
    derivacao = str(payload.get("derivacao_setorial") or derivacao_padrao)
    return calcular_memoria_aliquota(derivacao)


def persistir_perfil_tributario_empresa(
    supabase,
    empresa_id,
    derivacao: str,
) -> dict[str, float | str]:
    if supabase is None:
        raise RuntimeError("Supabase indisponível para salvar o perfil tributário.")
    if not empresa_id:
        raise ValueError("Empresa inválida para salvar o perfil tributário.")
    if derivacao not in ALIQUOTAS_REFORMA_TRIBUTARIA:
        raise ValueError("Derivação setorial inválida.")
    memoria = calcular_memoria_aliquota(derivacao)
    payload = {
        **memoria,
        "record_type": "company_tax_profile",
        "empresa_id": empresa_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    resposta = (
        supabase.table("praesidium_ledger_fiscal")
        .upsert(
            {
                "empresa_id": empresa_id,
                "item_uid": "config:aliquota-ibs-cbs:v1",
                "payload": payload,
                "updated_at": payload["updated_at"],
            },
            on_conflict="empresa_id,item_uid",
        )
        .select("item_uid")
        .execute()
    )
    if not resposta.data:
        raise RuntimeError("O Supabase não confirmou o perfil tributário.")
    limpar_caches_dados()
    return memoria


def obter_credito_quarentena(item: dict) -> float:
    """Calcula o crédito potencial com a alíquota persistida, compatível com dados legados."""
    item = normalizar_item_credito(item)
    if item.get("credito_xml_informado"):
        return _arredondar_dinheiro(
            item.get("valor_credito_item", 0.0)
        )
    credito = float(item.get("valor_credito_item", 0.0) or 0.0)
    if credito > 0:
        return credito
    aliquota = float(item.get("aliquota_aplicada", 0.0) or 0.0)
    if aliquota > 0:
        return _arredondar_dinheiro(
            float(item.get("valor_item", 0.0) or 0.0) * aliquota
        )
    return _arredondar_dinheiro(
        Decimal(str(item.get("valor_item", 0.0) or 0.0))
        * Decimal(str(ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA))
    )


def obter_credito_rejeitado(item: dict) -> float:
    item = normalizar_item_credito(item)
    if item.get("credito_xml_informado"):
        return _arredondar_dinheiro(
            item.get("valor_credito_item", 0.0)
        )
    credito = float(item.get("valor_credito_item", 0.0) or 0.0)
    if credito > 0:
        return credito
    aliquota = float(item.get("aliquota_aplicada", 0.0) or 0.0)
    return _arredondar_dinheiro(
        float(item.get("valor_item", 0.0) or 0.0) * aliquota
    )


def obter_versao_regra_nota(nota: dict) -> str:
    return str(nota.get("regra_tributaria_version") or "Legado — versão não registrada")


def calcular_resumo_creditos(itens: list) -> dict[str, float]:
    validados = Decimal("0")
    quarentena = Decimal("0")
    rejeitados = Decimal("0")
    for registro in itens:
        item = normalizar_item_credito(registro)
        status = item.get("classificacao_item")
        valor_credito = Decimal(str(item.get("valor_credito_item", 0.0) or 0.0))
        if status in {"Aprovado Revenda", "Aprovado Manualmente Contador"}:
            validados += valor_credito
        elif status == "Quarentena Uso/Consumo":
            quarentena += Decimal(str(obter_credito_quarentena(item)))
        elif status in {"Rejeitado", "Rejeitado pelo Contador"}:
            rejeitados += Decimal(str(obter_credito_rejeitado(item)))
    validados = validados.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    quarentena = quarentena.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rejeitados = rejeitados.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "validado": float(validados),
        "quarentena": float(quarentena),
        "rejeitado": float(rejeitados),
        "bruto": float(validados + quarentena),
    }


def normalizar_item_credito(item: dict) -> dict:
    """Converte aliases persistidos no Supabase para o formato usado pelos módulos."""
    normalizado = dict(item)
    if not normalizado.get("descricao"):
        normalizado["descricao"] = normalizado.get("descricao_produto", "")
    if normalizado.get("valor_item") is None:
        normalizado["valor_item"] = normalizado.get("valor_total_bruto", 0.0)
    if normalizado.get("quantidade_comercial") is None:
        normalizado["quantidade_comercial"] = normalizado.get("qcom", 0.0)
    if normalizado.get("qcom") is None:
        normalizado["qcom"] = normalizado["quantidade_comercial"]
    if normalizado.get("aliquota_aplicada") is None:
        normalizado["aliquota_aplicada"] = (
            float(normalizado.get("aliquota_ibs_aplicada", 0.0) or 0.0)
            + float(normalizado.get("aliquota_cbs_aplicada", 0.0) or 0.0)
        )
    if normalizado.get("valor_credito_item") is None:
        normalizado["valor_credito_item"] = (
            float(normalizado.get("credito_ibs_gerado", 0.0) or 0.0)
            + float(normalizado.get("credito_cbs_gerado", 0.0) or 0.0)
        )
    if not normalizado.get("justificativa_motor"):
        normalizado["justificativa_motor"] = normalizado.get("justificativa", "")

    classificacao = str(normalizado.get("classificacao_item") or "").strip()
    if not classificacao:
        status_credito = str(normalizado.get("status_credito") or "").strip().casefold()
        classificacoes_conhecidas = {
            "aprovado revenda",
            "aprovado manualmente contador",
            "quarentena uso/consumo",
            "rejeitado",
            "rejeitado pelo contador",
        }
        if status_credito in classificacoes_conhecidas:
            classificacao = str(normalizado.get("status_credito")).strip()
        elif status_credito in {"validado", "aprovado", "crédito validado"}:
            classificacao = "Aprovado Revenda"
        elif status_credito in {"quarentena", "pendente", "em quarentena"}:
            classificacao = "Quarentena Uso/Consumo"
        elif status_credito in {"rejeitado", "glosado", "rejeitado art. 57"}:
            classificacao = "Rejeitado"
    if classificacao:
        normalizado["classificacao_item"] = classificacao
    return normalizado


def _status_credito_persistido(classificacao: str) -> str:
    if classificacao in {"Aprovado Revenda", "Aprovado Manualmente Contador"}:
        return "Validado"
    if classificacao == "Quarentena Uso/Consumo":
        return "Quarentena"
    if classificacao in {"Rejeitado", "Rejeitado pelo Contador"}:
        return "Rejeitado"
    return classificacao or "N/A"


_TIPOS_ESPELHO = {
    "itens_nota_fiscal": "item",
    "conciliacao_devolucoes": "devolucao",
    "split_payment_events": "split_payment",
}


def _chave_registro_espelho(tipo: str, registro: dict) -> str:
    if tipo == "item":
        item_uid = str(registro.get("item_uid") or "").strip()
        if item_uid:
            return item_uid
        nota_id = str(registro.get("nota_id") or "").strip()
        numero_item = registro.get("nitem")
        if nota_id and numero_item is not None:
            return f"{nota_id}|{numero_item}"
        return ""
    if tipo == "devolucao":
        return str(
            registro.get("chave_nota_devolucao")
            or registro.get("id")
            or ""
        )
    if tipo == "split_payment":
        return str(registro.get("event_id") or "")
    return str(registro.get("id") or "")


def persistir_espelho_empresa(
    supabase,
    empresa_id,
    tipo: str,
    registros: list[dict] | dict,
) -> bool:
    if supabase is None or not empresa_id or not tipo:
        return False
    entradas = registros if isinstance(registros, list) else [registros]
    payload = []
    for registro in entradas:
        dado = dict(registro)
        empresa_registro = dado.get("empresa_id", empresa_id)
        if str(empresa_registro) != str(empresa_id):
            logging.getLogger(__name__).error(
                "Espelho recusou registro com empresa divergente."
            )
            return False
        chave = _chave_registro_espelho(tipo, dado)
        if not chave:
            logging.getLogger(__name__).error(
                "Espelho recusou registro sem chave idempotente (%s).",
                tipo,
            )
            return False
        payload.append({
            "empresa_id": empresa_id,
            "record_type": tipo,
            "record_key": chave,
            "payload": dado,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    if not payload:
        return True
    for inicio in range(0, len(payload), 25):
        try:
            lote = payload[inicio:inicio + 25]
            supabase.table("praesidium_empresa_mirror").upsert(
                lote,
                on_conflict="empresa_id,record_type,record_key",
            ).execute()
            if tipo == "item":
                ledger = [
                    {
                        "empresa_id": entrada["empresa_id"],
                        "item_uid": entrada["record_key"],
                        "payload": entrada["payload"],
                        "updated_at": entrada["updated_at"],
                    }
                    for entrada in lote
                ]
                supabase.table("praesidium_ledger_fiscal").upsert(
                    ledger,
                    on_conflict="empresa_id,item_uid",
                ).execute()
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao persistir o espelho %s da empresa %s.",
                tipo,
                empresa_id,
            )
            return False
    return True


def carregar_espelho_empresa(supabase, empresa_id) -> dict[str, list[dict]]:
    if supabase is None or not empresa_id:
        return {"item": [], "devolucao": [], "split_payment": []}
    try:
        linhas = []
        inicio = 0
        while True:
            resposta = (
                supabase.table("praesidium_empresa_mirror")
                .select("record_type,record_key,payload,updated_at")
                .eq("empresa_id", empresa_id)
                .range(inicio, inicio + 999)
                .execute()
            )
            pagina = resposta.data or []
            linhas.extend(pagina)
            if len(pagina) < 1000:
                break
            inicio += 1000
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao carregar o espelho persistente da empresa %s.",
            empresa_id,
        )
        return {"item": [], "devolucao": [], "split_payment": []}

    registros: dict[str, list[dict]] = {
        "item": [],
        "devolucao": [],
        "split_payment": [],
    }
    for linha in linhas:
        tipo = str(linha.get("record_type", ""))
        dado = linha.get("payload")
        if tipo not in registros or not isinstance(dado, dict):
            continue
        normalizado = dict(dado)
        normalizado.setdefault("empresa_id", empresa_id)
        normalizado["_mirror_updated_at"] = linha.get("updated_at")
        if str(normalizado["empresa_id"]) != str(empresa_id):
            continue
        if tipo == "item":
            record_key = str(linha.get("record_key") or "")
            if not normalizado.get("item_uid") and record_key:
                normalizado["item_uid"] = (
                    record_key
                    if record_key.startswith("legacy:")
                    else f"legacy:{record_key}"
                )
            normalizado = normalizar_item_credito(normalizado)
            normalizado.setdefault(
                "id",
                f"mirror_{record_key or normalizado.get('item_uid', '')}",
            )
        registros[tipo].append(normalizado)
    return registros


def combinar_registros_espelho(
    principais: list[dict],
    espelho: list[dict],
    tipo: str,
) -> list[dict]:
    combinados = {}
    for registro in principais:
        chave = _chave_registro_espelho(tipo, registro)
        if chave:
            combinados[chave] = dict(registro)
        elif tipo != "item" and registro.get("id") is not None:
            combinados[str(registro["id"])] = dict(registro)
    for registro in espelho:
        chave = _chave_registro_espelho(tipo, registro)
        if chave:
            combinados[chave] = {**registro, **combinados.get(chave, {})}
        elif tipo != "item" and registro.get("id") is not None:
            chave = str(registro["id"])
            combinados[chave] = {**combinados.get(chave, {}), **registro}
    return list(combinados.values())


# ==========================================
# 1. TABELAS DE REGRAS LC 214/2025 & CNAE
# ==========================================
NCM_CESTA_BASICA_ZERO = {
    "10063021", "07133319", "07133329", "07133399", "04011010", "04012010",
    "04021010", "04051000", "09012100", "17019900", "11010010", "19021900",
    "15079011", "02013000", "02023000", "02071400", "04072100", "19059090"
}

NCM_REDUCAO_60 = {
    "02032900", "04061010", "04069020", "07019000", "07020000", "07031019",
    "08039000", "08045020", "08051000", "08081000", "11022000", "16010000",
    "19019090", "20029090", "21032010"
}

NCM_SENSIVEIS_USO_CONSUMO = {
    "22021000", "22030000", "22042100", "22083020", "87032310", "85171300",
    "94013090", "94033000", "84713012", "85287200", "71131900"
}

MAPA_CNAE_NCM_PERMITIDOS = {
    "9602": ["3305", "3304", "3307", "3401", "8516", "8213", "9603", "9615", "3926", "4818"],
    "4772": ["3303", "3304", "3305", "3306", "3307", "3401", "9603", "9616", "8516"],
    "4711": ["02", "04", "07", "08", "09", "10", "11", "15", "16", "17", "19", "20", "21", "22", "33", "34", "39", "48"],
    "4712": ["02", "04", "07", "08", "09", "10", "11", "15", "16", "17", "19", "20", "21", "22", "33", "34", "39", "48"],
    "5611": ["02", "03", "04", "07", "08", "09", "10", "11", "15", "16", "17", "19", "20", "21", "22", "39", "48"],
    "4744": ["39", "72", "73", "74", "76", "82", "83", "84", "85", "68", "69", "32", "25"],
    "4530": ["40", "70", "73", "84", "85", "87", "2710", "3819", "3820"],
}

CNAES_VALIDOS_FALLBACK = {
    "4711301", "4711302", "4712100", "9602501", "9602502", "4772500",
    "4771701", "5611201", "5611203", "4744001", "4744099", "4530703",
    "4751201", "4781400", "6201501", "6920601", "4711315"
}


def limpar_tag_tenant_empresa(nome_empresa: str) -> str:
    return TAG_TENANT_PATTERN.sub("", str(nome_empresa)).strip()


def obter_cnpjs_autorizados_empresa(
    empresa_ativa: dict,
    cnpjs_adicionais: list[str] | tuple[str, ...] | set[str] | None = None,
) -> set[str]:
    autorizados = {
        _CNPJ_PATTERN.sub("", str(empresa_ativa.get("cnpj", "")))
    }
    configurados = empresa_ativa.get("cnpjs_autorizados", [])
    if isinstance(configurados, str):
        configurados = re.split(r"[,;\s]+", configurados)
    if not isinstance(configurados, (list, tuple, set)):
        configurados = []
    configurados = list(configurados) + list(cnpjs_adicionais or [])
    autorizados.update(
        _CNPJ_PATTERN.sub("", str(cnpj))
        for cnpj in configurados
        if len(_CNPJ_PATTERN.sub("", str(cnpj))) == 14
    )
    return {
        cnpj for cnpj in autorizados
        if len(cnpj) == 14
    }


# ==========================================
# SANITIZAÇÃO E GRAVAÇÃO RESILIENTE NO SUPABASE
# ==========================================
_VERSAO_REGRA_PADRAO = "LC 214/2025 - Art. 47/57 v1.0"
_STATUS_MANIFESTACAO_PADRAO = "Ciência da Operação (SEFAZ OK)"
_FORNECEDOR_PADRAO = "Fornecedor Comercial LTDA"
_CNPJ_FORNECEDOR_PADRAO = "00000000000191"
_COLUNAS_NUMERICAS = {
    "valor_total_nf", "valor_item", "valor_total_bruto", "quantidade_comercial",
    "qcom", "aliquota_aplicada", "aliquota_ibs_aplicada", "aliquota_cbs_aplicada",
    "valor_credito_item", "credito_ibs_gerado", "credito_cbs_gerado",
    "valor_unitario",
}
_COLUNAS_ITENS_NFE = {
    "empresa_id", "nota_id", "item_uid", "nitem", "codigo", "descricao",
    "descricao_produto", "ncm", "cfop", "quantidade_comercial", "qcom",
    "valor_unitario", "valor_item", "valor_total_bruto", "aliquota_aplicada",
    "aliquota_ibs_aplicada", "aliquota_cbs_aplicada", "valor_credito_item",
    "credito_ibs_gerado", "credito_cbs_gerado", "classificacao_item",
    "status_credito", "justificativa_motor",
}


def _dv_chave_nfe(base: str) -> str:
    soma = sum(
        int(digito) * peso
        for digito, peso in zip(reversed(base), (2, 3, 4, 5, 6, 7, 8, 9) * 6)
    )
    resto = soma % 11
    return str(0 if resto in (0, 1) else 11 - resto)


def gerar_chave_acesso_padrao() -> str:
    agora = datetime.now()
    cnpj = _CNPJ_FORNECEDOR_PADRAO
    base = (
        f"35{agora:%y%m}{cnpj}55"
        f"001{random.randint(1, 999999999):09d}1{random.randint(0, 99999999):08d}"
    )
    return base + _dv_chave_nfe(base)


def _valor_padrao_coluna(tabela: str, coluna: str, registro: dict | None = None):
    registro = registro or {}
    padroes = {
        "empresas_monitoradas": {
            "cnpj": _CNPJ_FORNECEDOR_PADRAO,
            "razao_social": "Empresa sem razão social",
            "regime_tributario": "Não informado",
            "cnae_principal": "0000000",
            "certificado_a1_status": "Não informado",
        },
        "notas_fiscais_recebidas": {
            "empresa_id": st.session_state.get("empresa_id_ativa", ""),
            "crt_emitente": "3",
            "razao_social_emitente": _FORNECEDOR_PADRAO,
            "nome_emitente": _FORNECEDOR_PADRAO,
            "cnpj_emitente": _CNPJ_FORNECEDOR_PADRAO,
            "chave_acesso": gerar_chave_acesso_padrao(),
            "valor_total_nf": 0.0,
            "manifestacao_status": _STATUS_MANIFESTACAO_PADRAO,
            "status_manifestacao": _STATUS_MANIFESTACAO_PADRAO,
            "status_processamento": "Processado",
            "regra_tributaria_version": _VERSAO_REGRA_PADRAO,
            "versao_regra": _VERSAO_REGRA_PADRAO,
            "data_emissao": datetime.now().isoformat(),
            "hash_sha256_xml": "",
        },
        "itens_nota_fiscal": {
            "empresa_id": st.session_state.get("empresa_id_ativa", ""),
            "nota_id": "",
            "ncm": "",
            "cfop": "",
            "descricao": "",
            "descricao_produto": "",
            "quantidade_comercial": 0.0,
            "valor_item": 0.0,
            "valor_total_bruto": 0.0,
            "aliquota_aplicada": 0.0,
            "aliquota_ibs_aplicada": 0.0,
            "aliquota_cbs_aplicada": 0.0,
            "valor_credito_item": 0.0,
            "credito_ibs_gerado": 0.0,
            "credito_cbs_gerado": 0.0,
            "classificacao_item": "N/A",
            "status_credito": "N/A",
            "justificativa_motor": "N/A",
        },
    }
    if coluna in padroes.get(tabela, {}):
        return padroes[tabela][coluna]
    if coluna in _COLUNAS_NUMERICAS or coluna.startswith(
        ("valor_", "aliquota_", "credito_", "quantidade_")
    ):
        return 0.0
    return ""


def sanitizar_payload_supabase(tabela: str, payload, parcial: bool = False):
    registros = [dict(item) for item in payload] if isinstance(payload, list) else [dict(payload)]
    sanitizados = []
    for registro in registros:
        atual = {
            chave: (
                _valor_padrao_coluna(tabela, chave, registro)
                if valor is None
                else valor
            )
            for chave, valor in registro.items()
        }
        if tabela == "empresas_monitoradas":
            obrigatorios = ("cnpj", "razao_social", "regime_tributario", "cnae_principal")
            for coluna in obrigatorios:
                if not parcial and not str(atual.get(coluna, "")).strip():
                    atual[coluna] = _valor_padrao_coluna(tabela, coluna, atual)
        elif tabela == "notas_fiscais_recebidas" and not parcial:
            obrigatorios = {
                "empresa_id": st.session_state.get("empresa_id_ativa", ""),
                "chave_acesso": gerar_chave_acesso_padrao(),
                "cnpj_emitente": _CNPJ_FORNECEDOR_PADRAO,
                "razao_social_emitente": _FORNECEDOR_PADRAO,
                "nome_emitente": _FORNECEDOR_PADRAO,
                "valor_total_nf": 0.0,
                "crt_emitente": "3",
                "manifestacao_status": _STATUS_MANIFESTACAO_PADRAO,
                "status_manifestacao": _STATUS_MANIFESTACAO_PADRAO,
                "status_processamento": "Processado",
                "regra_tributaria_version": _VERSAO_REGRA_PADRAO,
                "versao_regra": _VERSAO_REGRA_PADRAO,
            }
            for coluna, padrao in obrigatorios.items():
                if atual.get(coluna) is None or not str(atual.get(coluna, "")).strip():
                    atual[coluna] = padrao
            chave = str(atual.get("chave_acesso", ""))
            if not CHAVE_ACESSO_PATTERN.fullmatch(chave):
                atual["chave_acesso"] = gerar_chave_acesso_padrao()
            cnpj = "".join(filter(str.isdigit, str(atual.get("cnpj_emitente", ""))))
            atual["cnpj_emitente"] = cnpj if len(cnpj) == 14 else _CNPJ_FORNECEDOR_PADRAO
            crt = str(atual.get("crt_emitente", "")).strip()
            atual["crt_emitente"] = crt if crt in {"1", "2", "3"} else "3"
            atual.setdefault("data_emissao", datetime.now().isoformat())
            atual.setdefault("hash_sha256_xml", "")
            atual.setdefault("status_manifestacao", atual["manifestacao_status"])
        elif tabela == "itens_nota_fiscal" and not parcial:
            aliases_numericos = {
                "valor_item": ("vProd",),
                "credito_ibs_gerado": ("vIBS",),
                "credito_cbs_gerado": ("vCBS",),
                "valor_credito_item": ("credito_calculado",),
            }
            for coluna, aliases in aliases_numericos.items():
                if registro.get(coluna) is None:
                    atual[coluna] = next(
                        (
                            registro[alias]
                            for alias in aliases
                            if registro.get(alias) is not None
                        ),
                        0.0,
                    )
            aliases = {
                "descricao_produto": atual.get("descricao", "N/A"),
                "quantidade_comercial": atual.get("qcom", 0.0),
                "valor_total_bruto": atual.get("valor_item", 0.0),
                "status_credito": _status_credito_persistido(
                    str(atual.get("classificacao_item", ""))
                ),
                "justificativa_motor": atual.get("justificativa", "N/A"),
                "aliquota_ibs_aplicada": atual.get("aliquota_aplicada", 0.0),
                "aliquota_cbs_aplicada": 0.0,
                "credito_ibs_gerado": atual.get("valor_credito_item", 0.0),
                "credito_cbs_gerado": 0.0,
            }
            for coluna, padrao in aliases.items():
                if atual.get(coluna) is None or not str(atual.get(coluna, "")).strip():
                    atual[coluna] = padrao
            obrigatorios = (
                "ncm", "cfop", "descricao_produto", "quantidade_comercial",
                "valor_total_bruto", "aliquota_ibs_aplicada", "aliquota_cbs_aplicada",
                "credito_ibs_gerado", "credito_cbs_gerado", "status_credito",
                "justificativa_motor",
            )
            for coluna in obrigatorios:
                if atual.get(coluna) is None:
                    atual[coluna] = _valor_padrao_coluna(tabela, coluna, atual)
            for coluna in _COLUNAS_NUMERICAS:
                if coluna in atual:
                    atual[coluna] = _float_seguro(atual[coluna], 0.0)
            if "id" in atual:
                try:
                    uuid.UUID(str(atual["id"]))
                except (ValueError, TypeError, AttributeError):
                    atual.pop("id", None)
            atual = {
                coluna: valor
                for coluna, valor in atual.items()
                if coluna in _COLUNAS_ITENS_NFE
            }
        elif tabela == "split_payment_events" and not parcial:
            obrigatorios_split = {
                "empresa_id": "",
                "event_id": "",
                "transaction_id": "",
                "event_type": "split_payment.retained",
                "gross_amount": 0.0,
                "retained_amount": 0.0,
                "currency": "BRL",
                "occurred_at": datetime.now().astimezone().isoformat(),
                "payload_sha256": "",
            }
            for coluna, padrao in obrigatorios_split.items():
                if atual.get(coluna) is None:
                    atual[coluna] = padrao
            for coluna in ("gross_amount", "retained_amount"):
                atual[coluna] = _float_seguro(atual.get(coluna), 0.0)
        sanitizados.append(atual)
    return sanitizados if isinstance(payload, list) else sanitizados[0]


def _codigo_erro_supabase(erro: Exception) -> str:
    codigo = getattr(erro, "code", "")
    if codigo:
        return str(codigo)
    mensagem = str(erro)
    correspondencia = CODIGO_ERRO_PATTERN.search(mensagem)
    if correspondencia:
        return correspondencia.group(1)
    mensagem = mensagem.lower()
    if "null value in column" in mensagem and "not-null constraint" in mensagem:
        return "23502"
    if "column" in mensagem and ("schema cache" in mensagem or "could not find" in mensagem):
        return "PGRST204"
    return ""


def _coluna_erro_supabase(erro: Exception) -> str:
    mensagem = str(erro)
    for padrao in COLUNA_ERRO_PATTERNS:
        correspondencia = padrao.search(mensagem)
        if correspondencia:
            return correspondencia.group(1)
    return ""


def _guardar_fallback(tabela: str, payload, operacao: str = "insert", filtros: dict | None = None) -> list:
    registros = payload if isinstance(payload, list) else [payload]
    guardados = []
    if tabela == "notas_fiscais_recebidas":
        prefixo = "fallback_notas_"
    elif tabela == "itens_nota_fiscal":
        prefixo = "fallback_itens_"
    elif tabela == "conciliacao_devolucoes":
        prefixo = "fallback_devolucoes_"
    elif tabela == "empresas_monitoradas":
        prefixo = "fallback_empresas"
    else:
        prefixo = f"fallback_{tabela}_"

    for registro_original in registros:
        registro = dict(registro_original)
        empresa_id = registro.get("empresa_id") or st.session_state.get("empresa_id_ativa")
        if tabela in {"notas_fiscais_recebidas", "itens_nota_fiscal"} and not empresa_id:
            logging.getLogger(__name__).error(
                "Fallback local rejeitado sem empresa_id para a tabela %s.", tabela
            )
            continue
        if tabela == "notas_fiscais_recebidas" and not registro.get("id"):
            registro["id"] = f"local_{uuid.uuid4().hex}"
        elif tabela == "itens_nota_fiscal" and not registro.get("id"):
            registro["id"] = f"local_{uuid.uuid4().hex}"
        elif tabela in {"empresas_monitoradas", "conciliacao_devolucoes"} and not registro.get("id"):
            registro["id"] = f"local_{uuid.uuid4().hex}"

        if operacao == "update":
            registro.update(registro_original)
            for chave, valor in (filtros or {}).items():
                registro.setdefault(chave, valor)
        chave_sessao = f"{prefixo}{empresa_id}" if tabela in {
            "notas_fiscais_recebidas", "itens_nota_fiscal", "conciliacao_devolucoes"
        } else prefixo
        existentes = list(st.session_state.get(chave_sessao, []))
        identificador = registro.get("item_uid") or registro.get("id")
        encontrado = False
        for indice, existente in enumerate(existentes):
            filtros_correspondem = all(
                str(existente.get(k)) == str(v)
                for k, v in (filtros or {}).items()
            )
            mesmo_id = (
                identificador is not None
                and str(
                    existente.get("item_uid") or existente.get("id")
                ) == str(identificador)
            )
            corresponde = (
                (mesmo_id or filtros_correspondem)
                and filtros_correspondem
                if operacao == "update"
                else mesmo_id
            )
            if corresponde:
                existentes[indice] = {**existente, **registro}
                guardados.append(existentes[indice])
                encontrado = True
                break
            if operacao == "update" and mesmo_id:
                encontrado = True
                break
        if not encontrado:
            existentes.append(registro)
            guardados.append(registro)
        st.session_state[chave_sessao] = existentes
    return guardados


def _aplicar_coluna(payload, coluna: str, valor) -> None:
    registros = payload if isinstance(payload, list) else [payload]
    for registro in registros:
        registro[coluna] = valor


def _remover_coluna(payload, coluna: str) -> None:
    registros = payload if isinstance(payload, list) else [payload]
    for registro in registros:
        registro.pop(coluna, None)


def _persistir_payload_no_espelho(supabase, tabela: str, registros: list[dict]) -> bool:
    tipo = _TIPOS_ESPELHO.get(tabela)
    if not tipo or not registros:
        return False
    empresa_ids = {str(registro.get("empresa_id", "")) for registro in registros}
    if len(empresa_ids) != 1 or not next(iter(empresa_ids)):
        logging.getLogger(__name__).error(
            "Espelho recusou lote sem empresa única na tabela %s.",
            tabela,
        )
        return False
    return persistir_espelho_empresa(
        supabase,
        next(iter(empresa_ids)),
        tipo,
        registros,
    )


def executar_insert_seguro(
    tabela: str,
    payload,
    supabase=None,
    operacao: str = "insert",
) -> tuple[bool, str, list]:
    dados = sanitizar_payload_supabase(tabela, payload)
    registros = dados if isinstance(dados, list) else [dados]
    if tabela == "notas_fiscais_recebidas" and any(
        not str(registro.get("empresa_id", "")).strip() for registro in registros
    ):
        return False, "Não foi possível vincular a nota à empresa ativa.", []
    if tabela == "itens_nota_fiscal" and any(
        not str(registro.get("empresa_id", "")).strip()
        or not str(registro.get("nota_id", "")).strip()
        for registro in registros
    ):
        return False, "Não foi possível vincular os itens à empresa e à nota.", []

    if supabase is None:
        if tabela in {
            "notas_fiscais_recebidas",
            "itens_nota_fiscal",
            "conciliacao_devolucoes",
            "split_payment_events",
        }:
            return False, "Supabase indisponível; dados fiscais não foram persistidos.", []
        local = _guardar_fallback(tabela, dados)
        return (
            (True, "Mantido temporariamente nesta sessão.", local)
            if local or tabela not in {"notas_fiscais_recebidas", "itens_nota_fiscal"}
            else (False, "Não foi possível registrar os dados nesta sessão.", [])
        )

    if tabela == "itens_nota_fiscal" and len(registros) > 25:
        persistidos = []
        mensagens = []
        for inicio in range(0, len(registros), 25):
            ok_lote, mensagem_lote, itens_lote = executar_insert_seguro(
                tabela,
                registros[inicio:inicio + 25],
                supabase=supabase,
                operacao="upsert",
            )
            if not ok_lote:
                return False, mensagem_lote, persistidos
            mensagens.append(mensagem_lote)
            persistidos.extend(itens_lote)
        mensagem_final = (
            "Espelho Supabase persistido."
            if "Espelho Supabase persistido." in mensagens
            else "OK"
        )
        return True, mensagem_final, persistidos

    colunas_corrigidas = set()
    conflito_item_uid = tabela == "itens_nota_fiscal"
    for _ in range(32):
        try:
            construtor = supabase.table(tabela)
            if operacao == "upsert" or tabela == "itens_nota_fiscal":
                if conflito_item_uid and all(
                    registro.get("item_uid") for registro in registros
                ):
                    construtor = construtor.upsert(
                        dados,
                        on_conflict="empresa_id,item_uid",
                    )
                else:
                    construtor = construtor.upsert(dados)
            else:
                construtor = construtor.insert(dados)
            resultado = construtor.select("*").execute()
            persistidos = resultado.data if resultado.data else registros
            ledger_confirmado = _persistir_payload_no_espelho(
                supabase,
                tabela,
                persistidos,
            )
            if tabela == "itens_nota_fiscal" and not ledger_confirmado:
                limpar_caches_dados()
                return (
                    False,
                    "Itens gravados na tabela principal, mas não foi possível "
                    "confirmar o ledger fiscal.",
                    resultado.data or [],
                )
            return True, "OK", persistidos
        except Exception as erro:
            codigo = _codigo_erro_supabase(erro)
            coluna = _coluna_erro_supabase(erro)
            colunas_presentes = set(dados[0] if isinstance(dados, list) and dados else dados)
            if codigo == "23505" and tabela == "conciliacao_devolucoes":
                registro = registros[0] if registros else {}
                chave_devolucao = registro.get("chave_nota_devolucao")
                empresa_id = registro.get("empresa_id")
                if chave_devolucao and empresa_id is not None:
                    try:
                        existente = (
                            supabase.table(tabela)
                            .select("*")
                            .eq("empresa_id", empresa_id)
                            .eq("chave_nota_devolucao", chave_devolucao)
                            .limit(1)
                            .execute()
                        )
                        if existente.data:
                            return (
                                True,
                                "Devolução já registrada anteriormente.",
                                existente.data,
                            )
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Falha ao confirmar a idempotência da devolução."
                        )
                return False, "A devolução já foi registrada ou conflita com outro lançamento.", []
            if codigo == "23502" and coluna:
                if coluna in colunas_corrigidas:
                    break
                colunas_corrigidas.add(coluna)
                _aplicar_coluna(
                    dados,
                    coluna,
                    _valor_padrao_coluna(tabela, coluna, registros[0] if registros else {}),
                )
                continue
            if codigo == "PGRST204" and coluna in {"empresa_id", "nota_id"}:
                logging.getLogger(__name__).error(
                    "Schema sem vínculo obrigatório (%s) na tabela %s.",
                    coluna,
                    tabela,
                )
                return False, "Não foi possível vincular os dados à empresa ativa.", []
            if codigo == "PGRST204" and coluna and coluna in colunas_presentes:
                _remover_coluna(dados, coluna)
                if coluna == "item_uid":
                    conflito_item_uid = False
                continue
            if (
                isinstance(dados, list)
                and len(dados) > 1
            ):
                registros_salvos = []
                falha_individual = False
                for registro in dados:
                    ok_item, mensagem_item, resultado_item = executar_insert_seguro(
                        tabela,
                        registro,
                        supabase=supabase,
                        operacao="upsert" if tabela == "itens_nota_fiscal" else "insert",
                    )
                    if ok_item:
                        registros_salvos.extend(resultado_item or [registro])
                    else:
                        falha_individual = True
                        logging.getLogger(__name__).error(
                            "Falha no fallback individual da tabela %s: %s",
                            tabela,
                            mensagem_item,
                        )
                if not falha_individual and len(registros_salvos) == len(dados):
                    return (
                        True,
                        "Lote persistido com fallback individual.",
                        registros_salvos,
                    )
            if _persistir_payload_no_espelho(supabase, tabela, registros):
                limpar_caches_dados()
                return True, "Espelho Supabase persistido.", registros
            logging.getLogger(__name__).exception(
                "Falha ao gravar na tabela %s e no espelho persistente.",
                tabela,
            )
            local = (
                []
                if tabela in {
                    "notas_fiscais_recebidas",
                    "itens_nota_fiscal",
                    "conciliacao_devolucoes",
                    "split_payment_events",
                }
                else _guardar_fallback(tabela, dados)
            )
            if local or tabela not in {
                "notas_fiscais_recebidas",
                "itens_nota_fiscal",
                "conciliacao_devolucoes",
                "split_payment_events",
            }:
                return True, "Mantido temporariamente nesta sessão.", local
            return False, "Não foi possível registrar os dados. Tente novamente.", []
    logging.getLogger(__name__).error(
        "A recuperação de schema excedeu o limite de tentativas na tabela %s.", tabela
    )
    if _persistir_payload_no_espelho(supabase, tabela, registros):
        limpar_caches_dados()
        return True, "Espelho Supabase persistido.", registros
    local = (
        []
        if tabela in {
            "notas_fiscais_recebidas",
            "itens_nota_fiscal",
            "conciliacao_devolucoes",
            "split_payment_events",
        }
        else _guardar_fallback(tabela, dados)
    )
    if local or tabela not in {
        "notas_fiscais_recebidas",
        "itens_nota_fiscal",
        "conciliacao_devolucoes",
        "split_payment_events",
    }:
        return True, "Mantido temporariamente nesta sessão.", local
    return False, "Não foi possível registrar os dados. Tente novamente.", []


def executar_update_seguro(
    supabase,
    tabela: str,
    payload: dict,
    filtros: dict,
    fallback_record: dict | None = None,
) -> tuple[bool, str, list]:
    dados = sanitizar_payload_supabase(tabela, payload, parcial=True)
    if not dados:
        return False, "Não há alterações disponíveis para salvar.", []
    if supabase is None and tabela in {
        "notas_fiscais_recebidas",
        "itens_nota_fiscal",
        "conciliacao_devolucoes",
        "split_payment_events",
    }:
        return False, "Supabase indisponível; dados fiscais não foram persistidos.", []
    filtros_ativos = dict(filtros)
    colunas_corrigidas = set()
    for _ in range(32):
        try:
            consulta = supabase.table(tabela).update(dados)
            for coluna, valor in filtros_ativos.items():
                consulta = consulta.eq(coluna, valor)
            resultado = consulta.select("*").execute()
            if resultado.data:
                if tabela in _TIPOS_ESPELHO:
                    espelhados = []
                    for registro in resultado.data:
                        atualizado = {
                            **(fallback_record or {}),
                            **registro,
                            **dados,
                        }
                        if atualizado.get("status_credito"):
                            status_normalizado = normalizar_item_credito({
                                "status_credito": atualizado["status_credito"],
                            }).get("classificacao_item")
                            if status_normalizado:
                                atualizado["classificacao_item"] = status_normalizado
                        espelhados.append(atualizado)
                    ledger_confirmado = _persistir_payload_no_espelho(
                        supabase,
                        tabela,
                        espelhados,
                    )
                    if (
                        tabela == "itens_nota_fiscal"
                        and not ledger_confirmado
                    ):
                        limpar_caches_dados()
                        return (
                            False,
                            "Item atualizado, mas não foi possível confirmar "
                            "o ledger fiscal.",
                            resultado.data,
                        )
                return True, "OK", resultado.data
            if (
                tabela in _TIPOS_ESPELHO
                and fallback_record
                and (
                    str(fallback_record.get("id", "")).startswith(
                        ("mirror_", "local_")
                    )
                    or (
                        tabela == "itens_nota_fiscal"
                        and fallback_record.get("item_uid")
                    )
                )
                and all(
                    str(fallback_record.get(chave)) == str(valor)
                    for chave, valor in filtros_ativos.items()
                    if chave not in {"empresa_id", "nota_id", "id"}
                )
            ):
                atualizado = {**fallback_record, **dados}
                if _persistir_payload_no_espelho(
                    supabase,
                    tabela,
                    [atualizado],
                ):
                    if tabela not in {
                        "notas_fiscais_recebidas",
                        "itens_nota_fiscal",
                        "conciliacao_devolucoes",
                        "split_payment_events",
                    }:
                        _guardar_fallback(
                            tabela,
                            atualizado,
                            "update",
                            filtros_ativos,
                        )
                    limpar_caches_dados()
                    return True, "Espelho Supabase atualizado.", [atualizado]
            return False, "A decisão já foi processada ou o item não está mais pendente.", []
        except Exception as erro:
            codigo = _codigo_erro_supabase(erro)
            coluna = _coluna_erro_supabase(erro)
            if codigo == "23502" and coluna:
                if coluna in colunas_corrigidas:
                    break
                colunas_corrigidas.add(coluna)
                _aplicar_coluna(
                    dados,
                    coluna,
                    _valor_padrao_coluna(tabela, coluna, fallback_record or {}),
                )
                continue
            colunas_presentes = set(dados)
            if (
                codigo == "PGRST204"
                and tabela == "itens_nota_fiscal"
                and coluna == "classificacao_item"
                and coluna in filtros_ativos
            ):
                status_esperado = str(filtros_ativos.pop(coluna))
                filtros_ativos["status_credito"] = _status_credito_persistido(
                    status_esperado
                )
                continue
            if codigo == "PGRST204" and coluna and coluna in colunas_presentes:
                dados.pop(coluna, None)
                if dados:
                    continue
            if (
                tabela in _TIPOS_ESPELHO
                and fallback_record
                and (
                    str(fallback_record.get("id", "")).startswith(
                        ("mirror_", "local_")
                    )
                    or (
                        tabela == "itens_nota_fiscal"
                        and fallback_record.get("item_uid")
                    )
                )
                and all(
                    str(fallback_record.get(chave)) == str(valor)
                    for chave, valor in filtros_ativos.items()
                    if chave not in {"empresa_id", "nota_id", "id"}
                )
            ):
                atualizado = {**fallback_record, **dados}
                if _persistir_payload_no_espelho(
                    supabase,
                    tabela,
                    [atualizado],
                ):
                    if tabela not in {
                        "notas_fiscais_recebidas",
                        "itens_nota_fiscal",
                        "conciliacao_devolucoes",
                        "split_payment_events",
                    }:
                        _guardar_fallback(
                            tabela,
                            atualizado,
                            "update",
                            filtros_ativos,
                        )
                    limpar_caches_dados()
                    return True, "Espelho Supabase atualizado.", [atualizado]
            logging.getLogger(__name__).exception(
                "Falha ao atualizar a tabela %s; tentando atualizar o espelho persistente.",
                tabela,
            )
            break

    if tabela in {
        "notas_fiscais_recebidas",
        "itens_nota_fiscal",
        "conciliacao_devolucoes",
        "split_payment_events",
    }:
        return False, "Não foi possível persistir a alteração no Supabase.", []

    fallback = dict(fallback_record or {})
    fallback.update(filtros)
    fallback.update(dados)
    local = _guardar_fallback(tabela, fallback, "update", filtros)
    if local:
        return True, "Alteração mantida temporariamente nesta sessão.", local
    return False, "Não foi possível salvar as alterações. Tente novamente.", []


def inserir_seguro_supabase(supabase, tabela: str, payload) -> tuple[bool, str, list]:
    return executar_insert_seguro(tabela, payload, supabase=supabase)


def _consultar_registros_empresa(supabase, tabela: str, empresa_id) -> list[dict]:
    registros = []
    inicio = 0
    while True:
        resposta = (
            supabase.table(tabela)
            .select("*")
            .eq("empresa_id", empresa_id)
            .range(inicio, inicio + 999)
            .execute()
        )
        pagina = resposta.data or []
        registros.extend(pagina)
        if len(pagina) < 1000:
            return registros
        inicio += 1000


def _normalizar_item_hidratado(
    item: dict,
    notas_por_id: dict[str, dict],
    ordinais_por_nota: dict[str, int],
) -> dict:
    normalizado = normalizar_item_credito(item)
    nota_id = str(normalizado.get("nota_id") or "")
    nota = notas_por_id.get(nota_id, {})
    chave_acesso = str(nota.get("chave_acesso") or "")
    numero = normalizado.get("nitem")
    item_uid = str(normalizado.get("item_uid") or "")

    if numero is None:
        correspondencia = re.search(r"(?:::ITEM::|_ITEM_)(\d+)$", item_uid)
        if correspondencia:
            numero = int(correspondencia.group(1))
    if numero is None and nota_id:
        ordinais_por_nota[nota_id] = ordinais_por_nota.get(nota_id, 0) + 1
        numero = ordinais_por_nota[nota_id]
    if numero is not None:
        try:
            numero = int(numero)
        except (TypeError, ValueError):
            numero = None
    if chave_acesso and numero is not None:
        normalizado["nitem"] = numero
        item_uid_canonico = _gerar_item_uid(chave_acesso, numero)
        normalizado["_uid_legacy"] = item_uid != item_uid_canonico
        normalizado["item_uid"] = item_uid_canonico
    elif item_uid:
        normalizado["_uid_legacy"] = False
        normalizado["item_uid"] = item_uid
    return normalizado


def reconstruir_notas_a_partir_dos_itens(
    notas: list[dict],
    itens: list[dict],
) -> list[dict]:
    notas_por_id = {
        str(nota.get("id")): dict(nota)
        for nota in notas or []
        if nota.get("id") is not None
    }
    notas_por_chave = {
        str(nota.get("chave_acesso")): nota
        for nota in notas_por_id.values()
        if nota.get("chave_acesso")
    }
    grupos: dict[str, list[dict]] = {}
    identificadores = {}
    for item in itens or []:
        nota_id = str(item.get("nota_id") or "")
        nota_existente = notas_por_id.get(nota_id, {})
        item_uid = str(item.get("item_uid") or "")
        chave = str(
            item.get("chave_acesso")
            or nota_existente.get("chave_acesso")
            or ""
        )
        if not chave:
            correspondencia = CHAVE_ACESSO_PATTERN.search(item_uid)
            chave = correspondencia.group(0) if correspondencia else ""
        numero = str(
            item.get("numero")
            or item.get("nNF")
            or nota_existente.get("numero")
            or ""
        )
        chave_grupo = (
            f"chave:{chave}"
            if chave
            else (
                f"id:{nota_id}"
                if nota_id
                else (f"numero:{numero}" if numero else f"item:{item_uid}")
            )
        )
        grupos.setdefault(chave_grupo, []).append(item)
        identificadores[chave_grupo] = (nota_id, chave, numero)

    resultado = list(notas_por_id.values())
    ids_resultado = {str(nota.get("id")) for nota in resultado}
    for chave_grupo, itens_grupo in grupos.items():
        nota_id, chave, numero = identificadores[chave_grupo]
        nota = notas_por_id.get(nota_id) if nota_id else None
        if nota is None and chave:
            nota = notas_por_chave.get(chave)
        valor_itens = _arredondar_dinheiro(sum(
            _float_seguro(
                item.get("valor_item", item.get("valor_total_bruto")),
                0.0,
            )
            for item in itens_grupo
        ))
        if nota is not None:
            if not nota.get("chave_acesso") and chave:
                nota["chave_acesso"] = chave
            if not nota.get("numero") and numero:
                nota["numero"] = numero
            if not _float_seguro(nota.get("valor_total_nf"), 0.0):
                nota["valor_total_nf"] = valor_itens
            continue

        nota_id_resultado = (
            nota_id
            if nota_id and nota_id not in ids_resultado
            else f"reconstruida:{chave or numero or chave_grupo}"
        )
        if str(nota_id_resultado) in ids_resultado:
            continue
        nota_reconstruida = {
            "id": nota_id_resultado,
            "chave_acesso": chave,
            "numero": numero,
            "serie": str(itens_grupo[0].get("serie") or ""),
            "cnpj_emitente": str(itens_grupo[0].get("cnpj_emitente") or ""),
            "razao_social_emitente": str(
                itens_grupo[0].get("razao_social_emitente") or "Emitente não informado"
            ),
            "valor_total_nf": valor_itens,
            "manifestacao_status": "Nota reconstruída a partir dos itens persistidos",
            "status_processamento": "Reconstruída",
            "data_emissao": (
                itens_grupo[0].get("data_emissao")
                or itens_grupo[0].get("created_at")
            ),
            "_nota_reconstruida": True,
        }
        resultado.append(nota_reconstruida)
        ids_resultado.add(str(nota_id_resultado))
        if chave:
            notas_por_chave[chave] = nota_reconstruida
    return resultado


def hidratar_dados_empresa_com_cache(
    supabase,
    empresa_id,
    empresa_ativa: dict,
    cache: dict[str, dict[str, list[dict]]],
) -> dict[str, list[dict]]:
    chave_cache = str(empresa_id)
    try:
        dados = hidratar_dados_empresa_supabase(
            supabase,
            empresa_id,
            empresa_ativa,
        )
    except RuntimeError as erro:
        logging.getLogger(__name__).warning(
            "Falha temporária ao hidratar dados fiscais da empresa %s; "
            "será usado o último snapshot válido. Motivo: %s",
            empresa_id,
            erro,
        )
        snapshot = cache.get(chave_cache)
        if snapshot is not None:
            return snapshot
        return {
            "notas": [],
            "itens": [],
            "devolucoes": [],
            "eventos_split": [],
        }
    cache[chave_cache] = dados
    return dados


def decidir_itens_quarentena_lote(
    supabase,
    empresa_id,
    itens: list[dict],
    decisao: str,
) -> int:
    if supabase is None:
        raise RuntimeError("Supabase indisponível para registrar as decisões.")
    if decisao not in {"aprovar", "rejeitar"}:
        raise ValueError("Decisão de auditoria inválida.")
    if not itens:
        return 0

    classificacao = (
        "Aprovado Manualmente Contador"
        if decisao == "aprovar"
        else "Rejeitado pelo Contador"
    )
    status_credito = "Validado" if decisao == "aprovar" else "Rejeitado"
    justificativa = (
        "Art. 47/57: aprovação após revalidação documental manual pelo contador."
        if decisao == "aprovar"
        else "Art. 57: crédito glosado após análise manual do contador."
    )
    decisoes = []
    uids = set()
    for item in itens:
        uid = str(item.get("item_uid") or "").strip()
        if not uid or uid in uids:
            raise ValueError("A seleção contém item sem UID ou duplicado.")
        if str(item.get("empresa_id")) != str(empresa_id):
            raise ValueError("A seleção contém item de outra empresa.")
        uids.add(uid)
        decisoes.append({
            "item_uid": uid,
            "classificacao_item": classificacao,
            "status_credito": status_credito,
            "valor_credito_item": round(obter_credito_quarentena(item), 2),
            "justificativa_motor": justificativa,
        })

    resposta = supabase.rpc(
        "decidir_itens_quarentena_lote",
        {
            "p_empresa_id": str(empresa_id),
            "p_decisoes": decisoes,
        },
    ).execute()
    resultado = resposta.data
    if isinstance(resultado, list) and len(resultado) == 1:
        resultado = resultado[0]
    if not isinstance(resultado, dict):
        raise RuntimeError("O Supabase não confirmou as decisões em lote.")
    try:
        atualizados = int(resultado.get("updated", 0))
    except (TypeError, ValueError) as erro:
        raise RuntimeError("O Supabase retornou uma confirmação inválida.") from erro
    if (
        str(resultado.get("empresa_id")) != str(empresa_id)
        or atualizados != len(decisoes)
    ):
        raise RuntimeError(
            "Nem todos os itens pendentes foram atualizados; nenhuma decisão foi aplicada."
        )
    limpar_caches_dados()
    return atualizados


def excluir_empresa_supabase(supabase, empresa_id) -> dict:
    if supabase is None:
        raise RuntimeError("Supabase indisponível para excluir o cliente.")
    if empresa_id is None or not str(empresa_id).strip():
        raise ValueError("ID de cliente inválido.")

    try:
        resposta = supabase.rpc(
            "excluir_cliente_cascade",
            {"p_empresa_id": str(empresa_id)},
        ).execute()
    except Exception:
        logging.getLogger(__name__).warning(
            "Exclusão transacional indisponível para a empresa %s; "
            "tentando a sequência direta de exclusão.",
            empresa_id,
            exc_info=True,
        )
    else:
        resultado = resposta.data
        if isinstance(resultado, list) and len(resultado) == 1:
            resultado = resultado[0]
        if (
            isinstance(resultado, dict)
            and resultado.get("deleted") is True
            and str(resultado.get("empresa_id")) == str(empresa_id)
        ):
            _limpar_estado_local_empresa(empresa_id, preservar_perfil=False)
            return resultado
        logging.getLogger(__name__).warning(
            "A RPC não confirmou a exclusão da empresa %s; "
            "tentando a sequência direta.",
            empresa_id,
        )

    try:
        _excluir_registros_empresa_supabase(
            supabase,
            empresa_id,
            preservar_perfil=False,
            incluir_cnpjs=True,
        )
        resultado_exclusao = (
            supabase.table("empresas_monitoradas")
            .delete()
            .eq("id", empresa_id)
            .select("id")
            .execute()
        )
        if not resultado_exclusao.data:
            raise RuntimeError("O Supabase não confirmou a exclusão do cliente.")
        _limpar_estado_local_empresa(empresa_id, preservar_perfil=False)
        return {
            "deleted": True,
            "empresa_id": str(empresa_id),
            "fallback": True,
        }
    except Exception as erro_direto:
        logging.getLogger(__name__).exception(
            "Falha na sequência direta de exclusão da empresa %s.",
            empresa_id,
        )
        try:
            _limpar_estado_local_empresa(empresa_id, preservar_perfil=False)
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao invalidar o estado local da empresa %s após "
                "exclusão parcial.",
                empresa_id,
            )
        raise RuntimeError(
            "Não foi possível concluir a exclusão do cliente."
        ) from erro_direto


def _excluir_registros_empresa_supabase(
    supabase,
    empresa_id,
    *,
    preservar_perfil: bool,
    incluir_cnpjs: bool,
) -> None:
    tabelas = (
        "itens_nota_fiscal",
        "conciliacao_devolucoes",
        "notas_fiscais_recebidas",
        "split_payment_events",
        "praesidium_empresa_mirror",
        "praesidium_ledger_fiscal",
    )
    if incluir_cnpjs:
        tabelas += ("empresa_cnpjs_autorizados",)

    for tabela in tabelas:
        consulta = supabase.table(tabela).delete().eq("empresa_id", empresa_id)
        if tabela == "praesidium_ledger_fiscal" and preservar_perfil:
            consulta = consulta.neq(
                "item_uid",
                "config:aliquota-ibs-cbs:v1",
            )
        try:
            consulta.execute()
        except Exception as erro:
            if _codigo_erro_supabase(erro) not in {"42P01", "PGRST205"}:
                raise
            logging.getLogger(__name__).info(
                "Tabela %s ausente durante limpeza da empresa %s.",
                tabela,
                empresa_id,
            )


def _limpar_estado_local_empresa(
    empresa_id,
    *,
    preservar_perfil: bool,
) -> None:
    id_empresa = str(empresa_id)
    chave_hidratacao = "_dados_hidratados_por_empresa"
    cache_hidratacao = st.session_state.get(chave_hidratacao, {})
    if isinstance(cache_hidratacao, dict):
        cache_hidratacao.pop(id_empresa, None)

    for chave in list(st.session_state.keys()):
        if not isinstance(chave, str):
            continue
        por_empresa = (
            f"_{id_empresa}_" in chave
            or chave.endswith(f"_{id_empresa}")
            or chave == f"fallback_empresas_{id_empresa}"
        )
        if not por_empresa:
            continue
        if preservar_perfil and chave.startswith(
            ("perfil_tributario_", "salvar_perfil_tributario_")
        ):
            continue
        st.session_state.pop(chave, None)
    limpar_caches_dados()


def limpar_dados_empresa_supabase(supabase, empresa_id) -> None:
    if supabase is None:
        raise RuntimeError("Supabase indisponível para zerar os dados do cliente.")
    if empresa_id is None or not str(empresa_id).strip():
        raise ValueError("ID de cliente inválido.")

    try:
        _excluir_registros_empresa_supabase(
            supabase,
            empresa_id,
            preservar_perfil=True,
            incluir_cnpjs=False,
        )
    except Exception:
        try:
            _limpar_estado_local_empresa(empresa_id, preservar_perfil=True)
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao invalidar o estado local da empresa %s após "
                "limpeza fiscal parcial.",
                empresa_id,
            )
        raise
    _limpar_estado_local_empresa(empresa_id, preservar_perfil=True)


def hidratar_dados_empresa_supabase(
    supabase,
    empresa_id,
    empresa_ativa: dict,
) -> dict[str, list[dict]]:
    """Rebuild the active company's working set exclusively from Supabase."""
    if supabase is None:
        raise RuntimeError("Supabase indisponível para hidratar os dados fiscais.")
    if (
        empresa_id is None
        or str(empresa_ativa.get("id", "")) != str(empresa_id)
    ):
        raise ValueError("A empresa ativa não corresponde ao ID da hidratação.")

    try:
        notas = _consultar_registros_empresa(
            supabase,
            "notas_fiscais_recebidas",
            empresa_id,
        )
    except Exception as erro:
        logging.getLogger(__name__).exception(
            "Falha ao carregar notas da empresa %s durante a hidratação.",
            empresa_id,
        )
        raise RuntimeError("Não foi possível hidratar as notas fiscais do Supabase.") from erro

    tabelas_primarias = {}
    falhas_leitura = set()
    for tabela in (
        "itens_nota_fiscal",
        "praesidium_ledger_fiscal",
        "conciliacao_devolucoes",
        "split_payment_events",
    ):
        try:
            tabelas_primarias[tabela] = _consultar_registros_empresa(
                supabase,
                tabela,
                empresa_id,
            )
        except Exception:
            falhas_leitura.add(tabela)
            tabelas_primarias[tabela] = []
            logging.getLogger(__name__).exception(
                "Falha ao ler %s da empresa %s durante a hidratação.",
                tabela,
                empresa_id,
            )

    try:
        linhas_espelho = []
        inicio = 0
        while True:
            resposta = (
                supabase.table("praesidium_empresa_mirror")
                .select("record_type,record_key,payload,updated_at")
                .eq("empresa_id", empresa_id)
                .range(inicio, inicio + 999)
                .execute()
            )
            pagina = resposta.data or []
            linhas_espelho.extend(pagina)
            if len(pagina) < 1000:
                break
            inicio += 1000
        espelho_disponivel = True
    except Exception:
        linhas_espelho = []
        espelho_disponivel = False
        logging.getLogger(__name__).exception(
            "Falha ao ler o espelho persistente da empresa %s durante a hidratação.",
            empresa_id,
        )

    if falhas_leitura and not espelho_disponivel:
        raise RuntimeError(
            "Nem todas as fontes fiscais do Supabase puderam ser lidas."
        )

    espelho: dict[str, list[dict]] = {
        "item": [],
        "devolucao": [],
        "split_payment": [],
    }
    for linha in linhas_espelho:
        tipo = str(linha.get("record_type", ""))
        payload = linha.get("payload")
        if tipo not in espelho or not isinstance(payload, dict):
            continue
        registro = dict(payload)
        if str(registro.get("empresa_id", empresa_id)) != str(empresa_id):
            continue
        registro.setdefault("empresa_id", empresa_id)
        registro["_mirror_updated_at"] = linha.get("updated_at")
        if tipo == "item":
            chave = str(linha.get("record_key") or "")
            registro.setdefault("item_uid", chave)
            registro.setdefault("id", f"mirror_{chave}")
        espelho[tipo].append(registro)

    notas_por_id = {
        str(nota.get("id")): nota
        for nota in notas
        if nota.get("id") is not None
    }
    itens_primarios = sorted(
        tabelas_primarias["itens_nota_fiscal"],
        key=lambda item: (
            str(item.get("nota_id", "")),
            _float_seguro(item.get("nitem"), 0.0),
            str(item.get("id", "")),
        ),
    )
    ledger_por_uid = {}
    eventos_liquidacao = []
    perfil_tributario = None
    for registro in sorted(
        tabelas_primarias["praesidium_ledger_fiscal"],
        key=lambda item: str(item.get("updated_at", "")),
    ):
        payload = registro.get("payload")
        if not isinstance(payload, dict):
            continue
        tipo_registro = str(payload.get("record_type") or "")
        if tipo_registro == "split_payment_operation_event":
            eventos_liquidacao.append(dict(payload))
            continue
        if tipo_registro == "company_tax_profile":
            perfil_tributario = dict(payload)
            continue
        uid_ledger = str(
            payload.get("item_uid")
            or registro.get("item_uid")
            or ""
        )
        if uid_ledger:
            ledger_por_uid[uid_ledger] = dict(payload)
    uids_primarios = {
        str(item.get("item_uid") or "")
        for item in itens_primarios
        if item.get("item_uid")
    }
    itens_primarios.extend(
        item for uid, item in ledger_por_uid.items()
        if uid not in uids_primarios
    )
    itens_espelho = sorted(
        espelho["item"],
        key=lambda item: (
            str(item.get("nota_id", "")),
            _float_seguro(item.get("nitem"), 0.0),
            str(item.get("item_uid", "")),
        ),
    )
    itens_por_uid = {}
    ordinais_por_nota = {}
    for item in itens_primarios + itens_espelho:
        normalizado = _normalizar_item_hidratado(
            item,
            notas_por_id,
            ordinais_por_nota,
        )
        item_uid = str(normalizado.get("item_uid") or "")
        if not item_uid:
            continue
        existente = itens_por_uid.get(item_uid)
        if (
            existente is None
            or (
                existente.get("_uid_legacy", False)
                and not normalizado.get("_uid_legacy", False)
            )
        ):
            itens_por_uid[item_uid] = normalizado
        elif str(normalizado.get("id", "")).startswith("mirror_"):
            itens_por_uid[item_uid] = {
                **normalizado,
                **{
                    chave: valor
                    for chave, valor in existente.items()
                    if valor is not None
                },
            }

    ids_notas_com_itens = {
        str(item.get("nota_id"))
        for item in itens_por_uid.values()
        if item.get("nota_id") is not None
    }
    for nota in notas:
        nota_id = nota.get("id")
        chave = str(nota.get("chave_acesso") or "")
        if (
            nota_id is None
            or not chave
            or str(nota_id) in ids_notas_com_itens
        ):
            continue
        valor_nota = _float_seguro(
            nota.get("valor_total_nf"),
            0.0,
        )
        credito = float(
            (
                Decimal(str(valor_nota))
                * Decimal(str(ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA))
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )
        item_legado = {
            "empresa_id": empresa_id,
            "nota_id": nota_id,
            "item_uid": _gerar_item_uid(chave, 1),
            "nitem": 1,
            "descricao": "NF-e legada sem detalhamento XML original (quarentena)",
            "ncm": "",
            "cfop": "",
            "quantidade_comercial": 1.0,
            "valor_item": round(valor_nota, 2),
            "aliquota_aplicada": ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA,
            "aliquota_ibs_aplicada": 0.177,
            "aliquota_cbs_aplicada": 0.088,
            "valor_credito_item": credito,
            "credito_ibs_gerado": _arredondar_dinheiro(valor_nota * 0.177),
            "credito_cbs_gerado": _arredondar_dinheiro(valor_nota * 0.088),
            "classificacao_item": "Quarentena Uso/Consumo",
            "status_credito": "Quarentena",
            "justificativa_motor": (
                "NF-e legada sem XML original; crédito potencial mantido em "
                "quarentena até a reconstituição documental."
            ),
        }
        ok, mensagem, persistidos = inserir_seguro_supabase(
            supabase,
            "itens_nota_fiscal",
            item_legado,
        )
        if not ok:
            raise RuntimeError(
                f"Não foi possível persistir o item legado da nota {chave}: {mensagem}"
            )
        persistido = dict(persistidos[0]) if persistidos else item_legado
        persistido.setdefault("id", f"mirror_{item_legado['item_uid']}")
        itens_por_uid[item_legado["item_uid"]] = persistido

    devolucoes = combinar_registros_espelho(
        tabelas_primarias["conciliacao_devolucoes"],
        espelho["devolucao"],
        "devolucao",
    )
    eventos_split = combinar_registros_espelho(
        tabelas_primarias["split_payment_events"],
        espelho["split_payment"],
        "split_payment",
    )
    for indice, registro in enumerate(devolucoes):
        registro.pop("_mirror_updated_at", None)
        registro.setdefault("id", f"mirror_devolucao_{indice}")
    for indice, registro in enumerate(eventos_split):
        registro.pop("_mirror_updated_at", None)
        registro.setdefault(
            "id",
            f"mirror_split_{registro.get('event_id') or indice}",
        )
    itens_hidratados = sorted(
        itens_por_uid.values(),
        key=lambda item: str(item.get("item_uid", "")),
    )
    for item in itens_hidratados:
        item.pop("_mirror_updated_at", None)
        item.pop("_uid_legacy", None)
    notas = reconstruir_notas_a_partir_dos_itens(
        notas,
        itens_hidratados,
    )
    return {
        "notas": notas,
        "itens": itens_hidratados,
        "devolucoes": devolucoes,
        "eventos_split": eventos_split,
        "eventos_liquidacao": eventos_liquidacao,
        "perfil_tributario": (
            calcular_memoria_aliquota(
                str(
                    perfil_tributario.get("derivacao_setorial")
                )
            )
            if perfil_tributario
            else None
        ),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def validar_cnae_oficial_ibge(cnae: str) -> tuple[bool, str]:
    cnae_limpo = "".join(filter(str.isdigit, str(cnae)))
    if len(cnae_limpo) != 7:
        return False, "O CNAE deve conter exatamente 7 dígitos numéricos."

    if cnae_limpo in CNAES_VALIDOS_FALLBACK:
        return True, "CNAE validado na base nacional."

    try:
        url_sub = f"https://servicodados.ibge.gov.br/api/v2/cnae/subclasses/{cnae_limpo}"
        resp = requests.get(url_sub, timeout=2)
        if resp.status_code == 200:
            dados = resp.json()
            if dados and (isinstance(dados, dict) or (isinstance(dados, list) and len(dados) > 0)):
                return True, "CNAE validado no IBGE."

        url_cls = f"https://servicodados.ibge.gov.br/api/v2/cnae/classes/{cnae_limpo[:4]}"
        resp_cls = requests.get(url_cls, timeout=2)
        if resp_cls.status_code == 200:
            dados_cls = resp_cls.json()
            if dados_cls and (isinstance(dados_cls, dict) or (isinstance(dados_cls, list) and len(dados_cls) > 0)):
                return True, "Classe CNAE validada no IBGE."

        return False, "CNAE não localizado no IBGE."
    except Exception:
        return True, "Validação offline aplicada."


def classificar_item_inteligente(
    ncm: str,
    cfop: str,
    qcom: float,
    vprod: float,
    crt_emitente: str,
    cnaes_cliente_str: str,
    em_contingencia: bool,
    descricao: str = "",
    fator_reducao: float = 1.0,
) -> tuple[str, float, float]:
    ncm_limpo = "".join(filter(str.isdigit, str(ncm))).ljust(8, "0")[:8]
    cfop_limpo = "".join(filter(str.isdigit, str(cfop)))
    lista_cnaes = [
        "".join(filter(str.isdigit, c))
        for c in str(cnaes_cliente_str).split(",")
        if "".join(filter(str.isdigit, c))
    ]

    aliquota_base = _aliquota_base_item(ncm_limpo, crt_emitente, fator_reducao)

    if em_contingencia:
        return "Quarentena Uso/Consumo", 0.0, 0.0

    if cfop_limpo in {"1556", "2556", "5556", "6556", "1551", "2551", "5551", "6551"}:
        return "Quarentena Uso/Consumo", 0.0, 0.0

    if DESCRICAO_USO_INTERNO_PATTERN.search(str(descricao)):
        return "Quarentena Uso/Consumo", 0.0, 0.0

    if not lista_cnaes:
        lista_cnaes = ["4711302"]

    if "9602501" in lista_cnaes:
        prefixos_salao = (
            "3304", "3305", "3307", "8214", "9603", "851631", "8510",
        )
        descricao_normalizada = unicodedata.normalize(
            "NFKD",
            str(descricao),
        ).encode("ascii", "ignore").decode("ascii").casefold()
        termos_salao = (
            "shampoo", "condicionador", "mascara capilar", "coloracao",
            "agua oxigenada", "po descolorante", "progressiva", "ampola",
            "spray fixador", "oleo reparador", "escova termica", "pente",
            "alicate", "esmalte", "creme esfoliante", "tonico", "matizador",
            "leave-in", "leave in", "pomada", "locao pos-barba",
        )
        item_de_salao = ncm_limpo.startswith(prefixos_salao) or any(
            termo in descricao_normalizada for termo in termos_salao
        )
        if item_de_salao:
            return (
                "Aprovado Revenda",
                aliquota_base,
                _arredondar_dinheiro(vprod * aliquota_base),
            )
        cnaes_alternativos = [
            cnae_cod for cnae_cod in lista_cnaes
            if cnae_cod != "9602501"
        ]
        if not any(
            any(
                ncm_limpo.startswith(prefixo)
                for prefixo in MAPA_CNAE_NCM_PERMITIDOS.get(cnae_cod[:4], [])
            )
            for cnae_cod in cnaes_alternativos
        ):
            return (
                "Quarentena Uso/Consumo",
                aliquota_base,
                _arredondar_dinheiro(vprod * aliquota_base),
            )

    compativel_cnae = False
    for cnae_cod in lista_cnaes:
        prefixo_4 = cnae_cod[:4]
        prefixos_ncm_aceitos = MAPA_CNAE_NCM_PERMITIDOS.get(prefixo_4, [])
        if any(ncm_limpo.startswith(pref) for pref in prefixos_ncm_aceitos):
            compativel_cnae = True
            break

    if ncm_limpo in NCM_SENSIVEIS_USO_CONSUMO:
        if compativel_cnae and qcom >= 6:
            credito = _arredondar_dinheiro(vprod * aliquota_base)
            return "Aprovado Revenda", aliquota_base, credito
        return "Quarentena Uso/Consumo", 0.0, 0.0

    if compativel_cnae:
        if qcom <= 1 and vprod > 1500.0:
            return "Quarentena Uso/Consumo", 0.0, 0.0
        credito = _arredondar_dinheiro(vprod * aliquota_base)
        return "Aprovado Revenda", aliquota_base, credito

    return "Quarentena Uso/Consumo", 0.0, 0.0


def _aliquota_base_item(
    ncm: str,
    crt_emitente: str,
    fator_reducao: float,
) -> float:
    ncm_limpo = "".join(filter(str.isdigit, str(ncm))).ljust(8, "0")[:8]
    try:
        fator_reducao = min(1.0, max(0.0, float(fator_reducao)))
    except (TypeError, ValueError):
        fator_reducao = 1.0
    if ncm_limpo in NCM_CESTA_BASICA_ZERO:
        return 0.0
    if ncm_limpo in NCM_REDUCAO_60:
        return 0.106
    if str(crt_emitente).strip() == "1":
        return 0.080
    return 0.265 * fator_reducao


def _justificativa_classificacao_item(
    descricao: str,
    status: str,
) -> str:
    if DESCRICAO_USO_INTERNO_PATTERN.search(descricao):
        return "Art. 57: descrição indica uso/consumo interno ou ativo imobilizado."
    if status == "Quarentena Uso/Consumo":
        return (
            "Art. 47/57: compatibilidade NCM/CNAE não comprovada, operação de uso/consumo "
            "ou documentação fiscal requer análise do contador."
        )
    return "Art. 47: item compatível com CNAE autorizado e NCM previsto no motor tributário."


# ==========================================
# 2. RECONSTRUÇÃO E EMISSÃO DE XML NF-e
# ==========================================
def gerar_xml_reconstruido_cliente(empresa_ativa: dict, nota: dict, itens_nota: list) -> str:
    chave = str(nota.get("chave_acesso", "")).zfill(44)
    cnpj_emit = str(nota.get("cnpj_emitente", "00000000000191")).zfill(14)
    cnpj_dest = str(empresa_ativa.get("cnpj", "00000000000191")).zfill(14)
    razao_dest = limpar_tag_tenant_empresa(
        str(empresa_ativa.get("razao_social", "CLIENTE MONITORADO"))
    )
    vl_total = float(nota.get("valor_total_nf", 0.0))

    blocos_det = ""
    for idx, it in enumerate(itens_nota or [], start=1):
        desc = escape_xml(str(it.get("descricao", "MERCADORIA AUDITADA")))
        codigo = escape_xml(str(it.get("codigo") or f"ITEM-{idx:03d}"))
        ncm = str(it.get("ncm", "00000000"))
        cfop = str(it.get("cfop", "5102"))
        vl_it = float(it.get("valor_item", 0.0))
        qcom = float(
            it.get("quantidade_comercial")
            or it.get("qcom")
            or 1.0
        )
        vl_unitario = float(
            it.get("valor_unitario")
            or (vl_it / qcom if qcom else vl_it)
        )
        vl_cred = float(it.get("valor_credito_item", 0.0))
        credito_ibs = float(it.get("credito_ibs_gerado", 0.0) or 0.0)
        credito_cbs = float(it.get("credito_cbs_gerado", 0.0) or 0.0)
        status_it = str(it.get("classificacao_item", "Auditado"))

        blocos_det += f"""
        <det nItem="{idx}">
            <prod>
                <cProd>{codigo}</cProd>
                <xProd>{desc}</xProd>
                <NCM>{ncm}</NCM>
                <CFOP>{cfop}</CFOP>
                <uCom>UN</uCom>
                <qCom>{qcom:.4f}</qCom>
                <vUnCom>{vl_unitario:.2f}</vUnCom>
                <vProd>{vl_it:.2f}</vProd>
            </prod>
            <imposto>
                <IBSCBS>
                    <vBC>{vl_it:.2f}</vBC>
                    <vIBS>{credito_ibs:.2f}</vIBS>
                    <vCBS>{credito_cbs:.2f}</vCBS>
                    <vCreditoIBSCBS>{vl_cred:.2f}</vCreditoIBSCBS>
                    <xStatusAuditoria>{status_it}</xStatusAuditoria>
                </IBSCBS>
            </imposto>
        </det>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
    <NFe>
        <infNFe Id="NFe{chave}" versao="4.00">
            <ide>
                <mod>55</mod>
                <serie>{escape_xml(str(nota.get('serie') or '1'))}</serie>
                <nNF>{escape_xml(str(nota.get('numero') or '1'))}</nNF>
                <finNFe>1</finNFe>
                <dhEmi>{datetime.now().strftime('%Y-%m-%dT%H:%M:%S-03:00')}</dhEmi>
            </ide>
            <emit>
                <CNPJ>{cnpj_emit}</CNPJ>
                <xNome>FORNECEDOR MONITORADO SEFAZ</xNome>
            </emit>
            <dest>
                <CNPJ>{cnpj_dest}</CNPJ>
                <xNome>{razao_dest}</xNome>
            </dest>{blocos_det}
            <total>
                <ICMSTot>
                    <vProd>{vl_total:.2f}</vProd>
                    <vNF>{vl_total:.2f}</vNF>
                </ICMSTot>
            </total>
            <infAdic>
                <infCpl>Hash SHA-256: {nota.get('hash_sha256_xml', '')} | Status: {nota.get('manifestacao_status', '')}</infCpl>
            </infAdic>
        </infNFe>
    </NFe>
</nfeProc>"""


def gerar_xml_devolucao_nfe(
    empresa_ativa: dict,
    nota_orig: dict,
    chave_devolucao: str,
    itens_devolvidos: list,
    valor_total_dev: float,
    credito_estornado: float,
    motivo: str
) -> str:
    chave_orig = str(nota_orig.get("chave_acesso", "")).zfill(44)
    cnpj_emit_dev = str(empresa_ativa.get("cnpj", "")).zfill(14)
    nome_emit_dev = limpar_tag_tenant_empresa(str(empresa_ativa.get("razao_social", "")))
    cnpj_dest_dev = str(nota_orig.get("cnpj_emitente", "")).zfill(14)

    det_xml = ""
    for idx, item in enumerate(itens_devolvidos, start=1):
        det_xml += f"""
        <det nItem="{idx}">
            <prod>
                <cProd>DEV-{idx:03d}</cProd>
                <xProd>{str(item['descricao']).replace('&', 'E')}</xProd>
                <NCM>{item['ncm']}</NCM>
                <CFOP>5202</CFOP>
                <uCom>UN</uCom>
                <qCom>1.0000</qCom>
                <vUnCom>{float(item['valor_devolvido']):.2f}</vUnCom>
                <vProd>{float(item['valor_devolvido']):.2f}</vProd>
            </prod>
        </det>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
    <NFe>
        <infNFe Id="NFe{chave_devolucao}" versao="4.00">
            <ide>
                <natOp>DEVOLUCAO DE COMPRA PARA COMERCIALIZACAO</natOp>
                <mod>55</mod>
                <finNFe>4</finNFe>
                <dhEmi>{datetime.now().strftime('%Y-%m-%dT%H:%M:%S-03:00')}</dhEmi>
                <NFref>
                    <refNFe>{chave_orig}</refNFe>
                </NFref>
            </ide>
            <emit>
                <CNPJ>{cnpj_emit_dev}</CNPJ>
                <xNome>{nome_emit_dev}</xNome>
            </emit>
            <dest>
                <CNPJ>{cnpj_dest_dev}</CNPJ>
                <xNome>FORNECEDOR ORIGINAL</xNome>
            </dest>{det_xml}
            <total>
                <ICMSTot>
                    <vProd>{valor_total_dev:.2f}</vProd>
                    <vNF>{valor_total_dev:.2f}</vNF>
                </ICMSTot>
            </total>
            <infAdic>
                <infCpl>NF-e de Devolucao ref. chave {chave_orig} | Motivo: {motivo} | Estorno IBS/CBS: R$ {credito_estornado:.2f}</infCpl>
            </infAdic>
        </infNFe>
    </NFe>
</nfeProc>"""


# ==========================================
# 3. PROCESSAMENTO DE ARQUIVO XML REAL
# ==========================================
def _xml_filhos(elemento, nome: str):
    if elemento is None:
        return []
    return [
        filho
        for filho in elemento.iter()
        if filho is not elemento and filho.tag.rsplit("}", 1)[-1] == nome
    ]


def _gerar_item_uid(chave_acesso: str, numero_item: int | str) -> str:
    return f"{chave_acesso}::ITEM::{int(numero_item):04d}"


def _xml_filho(elemento, nome: str):
    filhos = _xml_filhos(elemento, nome)
    return filhos[0] if filhos else None


def _xml_texto(elemento, nome: str, padrao: str = "") -> str:
    filho = _xml_filho(elemento, nome)
    return str(filho.text or "").strip() if filho is not None else padrao


def _float_seguro(valor, padrao: float) -> float:
    try:
        convertido = float(valor)
        return convertido if math.isfinite(convertido) else padrao
    except (TypeError, ValueError):
        return padrao


def _carregar_notas_existentes(
    supabase,
    empresa_id_ativo,
) -> dict[str, dict]:
    if supabase is None:
        return {}
    try:
        linhas = []
        inicio = 0
        while True:
            resposta = (
                supabase.table("notas_fiscais_recebidas")
                .select("id,chave_acesso")
                .eq("empresa_id", empresa_id_ativo)
                .range(inicio, inicio + 999)
                .execute()
            )
            pagina = resposta.data or []
            linhas.extend(pagina)
            if len(pagina) < 1000:
                break
            inicio += 1000
        return {
            str(n.get("chave_acesso")): n
            for n in linhas
            if n.get("chave_acesso") and n.get("id") is not None
        }
    except Exception as erro:
        logging.getLogger(__name__).exception(
            "Falha ao carregar índice de duplicidade para empresa %s.",
            empresa_id_ativo,
        )
        raise RuntimeError(
            "Não foi possível verificar as notas existentes no Supabase."
        ) from erro


def _carregar_itens_existentes_empresa(
    supabase,
    empresa_id,
    notas_existentes: dict[str, dict],
) -> dict[str, dict]:
    try:
        itens_primarios = _consultar_registros_empresa(
            supabase,
            "itens_nota_fiscal",
            empresa_id,
        )
        leitura_primaria_ok = True
    except Exception:
        itens_primarios = []
        leitura_primaria_ok = False
        logging.getLogger(__name__).exception(
            "Falha ao ler itens atuais da empresa %s antes do reprocessamento.",
            empresa_id,
        )
    try:
        linhas_espelho = []
        inicio = 0
        while True:
            resposta = (
                supabase.table("praesidium_empresa_mirror")
                .select("record_key,payload")
                .eq("empresa_id", empresa_id)
                .eq("record_type", "item")
                .range(inicio, inicio + 999)
                .execute()
            )
            pagina = resposta.data or []
            linhas_espelho.extend(pagina)
            if len(pagina) < 1000:
                break
            inicio += 1000
        leitura_espelho_ok = True
    except Exception:
        linhas_espelho = []
        leitura_espelho_ok = False
        logging.getLogger(__name__).exception(
            "Falha ao ler o espelho de itens da empresa %s antes do reprocessamento.",
            empresa_id,
        )
    if not leitura_primaria_ok and not leitura_espelho_ok:
        raise RuntimeError("Não foi possível preservar as decisões de auditoria existentes.")

    notas_por_id = {
        str(nota.get("id")): nota
        for nota in notas_existentes.values()
        if nota.get("id") is not None
    }
    itens_por_uid = {}
    ordinais_por_nota = {}
    for item in itens_primarios:
        normalizado = _normalizar_item_hidratado(
            item,
            notas_por_id,
            ordinais_por_nota,
        )
        uid = str(normalizado.get("item_uid") or "")
        existente = itens_por_uid.get(uid)
        if uid and (
            existente is None
            or (
                existente.get("_uid_legacy", False)
                and not normalizado.get("_uid_legacy", False)
            )
        ):
            itens_por_uid[uid] = normalizado
    for linha in linhas_espelho:
        payload = linha.get("payload")
        if not isinstance(payload, dict):
            continue
        registro = dict(payload)
        registro.setdefault("item_uid", linha.get("record_key"))
        normalizado = _normalizar_item_hidratado(
            registro,
            notas_por_id,
            ordinais_por_nota,
        )
        uid = str(normalizado.get("item_uid") or "")
        existente = itens_por_uid.get(uid)
        if uid and (
            existente is None
            or (
                existente.get("_uid_legacy", False)
                and not normalizado.get("_uid_legacy", False)
            )
        ):
            itens_por_uid[uid] = normalizado
    return itens_por_uid


def _preparar_xml_nfe(
    xml_bytes: bytes,
    empresa_id_ativo,
    empresa_ativa: dict,
    hashes_lote: set[str],
    chaves_lote: set[str],
    cnpjs_autorizados: set[str] | None = None,
) -> tuple[dict | None, list[dict], str]:
    if empresa_id_ativo is None or str(empresa_id_ativo).strip() == "":
        return None, [], "Importação cancelada: a empresa ativa não possui um ID válido."
    if str(empresa_ativa.get("id")) != str(empresa_id_ativo):
        return None, [], "Importação cancelada: o ID da empresa ativa não corresponde ao cadastro selecionado."

    cnpjs_empresa = cnpjs_autorizados or obter_cnpjs_autorizados_empresa(empresa_ativa)
    if not cnpjs_empresa:
        return None, [], "Importação cancelada: não há CNPJ autorizado configurado."

    hash_sha256 = hashlib.sha256(xml_bytes).hexdigest()
    if hash_sha256 in hashes_lote:
        return None, [], "Nota bloqueada: este XML já foi registrado."

    try:
        raiz = ET.fromstring(xml_bytes)
    except Exception as ex:
        logging.getLogger(__name__).warning("Arquivo XML malformado ou inválido: %s", ex)
        return None, [], "XML malformado ou inválido. Verifique o arquivo e tente novamente."

    nfe = _xml_filho(raiz, "NFe") or raiz
    inf_nfe = _xml_filho(nfe, "infNFe")
    if not inf_nfe:
        return None, [], "Estrutura fiscal não encontrada no XML."

    chave_raw = str(inf_nfe.attrib.get("Id", "")).replace("NFe", "").strip()
    if len(chave_raw) != 44:
        chave_raw = "".join([str(random.randint(0, 9)) for _ in range(44)])
    if chave_raw in chaves_lote:
        return None, [], f"Nota bloqueada: Chave {chave_raw[:12]}... repetida no lote."

    ide = _xml_filho(inf_nfe, "ide")
    emit = _xml_filho(inf_nfe, "emit")
    dest = _xml_filho(inf_nfe, "dest")
    total = _xml_filho(_xml_filho(inf_nfe, "total"), "ICMSTot")
    cnpj_emit = "".join(filter(str.isdigit, _xml_texto(emit, "CNPJ", _xml_texto(emit, "CPF", _CNPJ_FORNECEDOR_PADRAO))))
    cnpj_dest = "".join(filter(str.isdigit, _xml_texto(dest, "CNPJ", _xml_texto(dest, "CPF"))))
    if not cnpj_dest or cnpj_dest not in cnpjs_empresa:
        return (
            None,
            [],
            "Trava de segurança: o destinatário da NF-e não pertence à lista de CNPJs autorizados para esta empresa."
        )

    tp_emis = _xml_texto(ide, "tpEmis", "1")
    em_contingencia = tp_emis != "1"
    status_manifestacao = (
        f"Alerta: Emissão em Contingência (tpEmis={tp_emis})"
        if em_contingencia
        else "Ciência da Operação (SEFAZ OK)"
    )
    data_emissao = _xml_texto(ide, "dhEmi", _xml_texto(ide, "dEmi"))

    crt_emitente = _xml_texto(emit, "CRT", "3") or "3"
    if crt_emitente not in {"1", "2", "3"}:
        crt_emitente = "3"
    valor_total_nf = _float_seguro(
        _xml_texto(total, "vNF", _xml_texto(total, "vProd", "0")),
        0.0,
    )

    payload_nf = {
        "empresa_id": empresa_id_ativo,
        "chave_acesso": chave_raw,
        "numero": _xml_texto(ide, "nNF"),
        "serie": _xml_texto(ide, "serie"),
        "cnpj_emitente": cnpj_emit,
        "razao_social_emitente": _xml_texto(emit, "xNome", _FORNECEDOR_PADRAO),
        "nome_emitente": _xml_texto(emit, "xFant", _xml_texto(emit, "xNome", _FORNECEDOR_PADRAO)),
        "crt_emitente": crt_emitente,
        "valor_total_nf": round(valor_total_nf, 2),
        "manifestacao_status": status_manifestacao,
        "status_processamento": "Processado",
        "hash_sha256_xml": hash_sha256,
        "regra_tributaria_version": VERSAO_REGRA_TRIBUTARIA,
        "versao_regra": VERSAO_REGRA_TRIBUTARIA,
        "data_emissao": str(data_emissao) if data_emissao else None,
        "xml_reconstruido": xml_bytes.decode("utf-8-sig", errors="replace"),
    }

    hashes_lote.add(hash_sha256)
    chaves_lote.add(chave_raw)
    itens = []
    numeros_itens = set()
    detalhes = [
        elemento
        for elemento in raiz.iter()
        if elemento.tag.split("}")[-1] == "det"
    ]
    for ordinal, det in enumerate(detalhes, start=1):
        numero_bruto = str(det.attrib.get("nItem") or ordinal).strip()
        numero_item = int(numero_bruto) if numero_bruto.isdigit() else ordinal
        while numero_item in numeros_itens:
            numero_item += 1
        numeros_itens.add(numero_item)
        prod = _xml_filho(det, "prod")
        imposto = _xml_filho(det, "imposto")
        xprod = _xml_texto(prod, "xProd", "Item NF-e")
        ncm = _xml_texto(prod, "NCM", "00000000")
        cfop = _xml_texto(prod, "CFOP", "5102")
        qcom = _float_seguro(_xml_texto(prod, "qCom", "1"), 1.0)
        vprod = _float_seguro(_xml_texto(prod, "vProd", "0"), 0.0)
        valores_ibs = [
            elemento
            for elemento in (imposto.iter() if imposto is not None else [])
            if elemento.tag.split("}")[-1] == "vIBS"
        ]
        valores_cbs = [
            elemento
            for elemento in (imposto.iter() if imposto is not None else [])
            if elemento.tag.split("}")[-1] == "vCBS"
        ]
        item = {
            "empresa_id": empresa_id_ativo,
            "item_uid": _gerar_item_uid(chave_raw, numero_item),
            "nitem": numero_item,
            "codigo": _xml_texto(prod, "cProd", f"ITEM-{numero_item:03d}"),
            "descricao": xprod,
            "ncm": ncm,
            "cfop": cfop,
            "quantidade_comercial": qcom,
            "valor_unitario": _arredondar_dinheiro(
                _float_seguro(_xml_texto(prod, "vUnCom", "0"), 0.0)
            ),
            "valor_item": _arredondar_dinheiro(vprod),
        }
        if valores_ibs or valores_cbs:
            item["credito_xml_informado"] = True
            item["_tributos_xml_presentes"] = True
            item["_credito_ibs_xml"] = sum(
                _float_seguro(elemento.text, 0.0)
                for elemento in valores_ibs
            )
            item["_credito_cbs_xml"] = sum(
                _float_seguro(elemento.text, 0.0)
                for elemento in valores_cbs
            )
            item["_credito_xml"] = _arredondar_dinheiro(
                item["_credito_ibs_xml"] + item["_credito_cbs_xml"],
            )
        itens.append(item)

    return payload_nf, itens, ""


def _limpar_itens_anteriores(
    supabase,
    empresa_id,
    nota_ids: set[str],
    chaves_acesso: set[str] | None = None,
) -> bool:
    if not nota_ids:
        return True
    chave_sessao = f"fallback_itens_{empresa_id}"
    if supabase is None:
        st.session_state[chave_sessao] = [
            item
            for item in st.session_state.get(chave_sessao, [])
            if str(item.get("nota_id")) not in nota_ids
        ]
        return True

    limpeza_completa = True
    try:
        espelho_resposta = (
            supabase.table("praesidium_empresa_mirror")
            .select("record_key,payload")
            .eq("empresa_id", empresa_id)
            .eq("record_type", "item")
            .execute()
        )
        chaves_espelho = [
            str(linha.get("record_key"))
            for linha in (espelho_resposta.data or [])
            if isinstance(linha.get("payload"), dict)
            and (
                str(linha["payload"].get("nota_id")) in nota_ids
                or any(
                    str(linha.get("record_key", "")).startswith(
                        f"{chave}::ITEM::"
                    )
                    for chave in (chaves_acesso or set())
                )
            )
            and linha.get("record_key")
        ]
        if chaves_espelho:
            (
                supabase.table("praesidium_empresa_mirror")
                .delete()
                .eq("empresa_id", empresa_id)
                .eq("record_type", "item")
                .in_("record_key", chaves_espelho)
                .execute()
            )
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao limpar itens do espelho antes do reprocessamento da empresa %s.",
            empresa_id,
        )
        limpeza_completa = False

    try:
        supabase.table("itens_nota_fiscal").delete().eq(
            "empresa_id", empresa_id
        ).in_("nota_id", list(nota_ids)).execute()
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao limpar itens da tabela antes do reprocessamento da empresa %s; "
            "a sincronização continuará via upsert.",
            empresa_id,
        )
        limpeza_completa = False

    try:
        resposta_ledger = (
            supabase.table("praesidium_ledger_fiscal")
            .select("item_uid")
            .eq("empresa_id", empresa_id)
            .execute()
        )
        uids_ledger = [
            str(registro.get("item_uid"))
            for registro in (resposta_ledger.data or [])
            if registro.get("item_uid")
            and any(
                str(registro["item_uid"]).startswith(f"{chave}::ITEM::")
                for chave in (chaves_acesso or set())
            )
        ]
        if uids_ledger:
            supabase.table("praesidium_ledger_fiscal").delete().eq(
                "empresa_id", empresa_id
            ).in_("item_uid", uids_ledger).execute()
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao limpar o ledger fiscal antes do reprocessamento da empresa %s.",
            empresa_id,
        )
        limpeza_completa = False

    st.session_state[chave_sessao] = [
            item
            for item in st.session_state.get(chave_sessao, [])
            if str(item.get("nota_id")) not in nota_ids
    ]
    return limpeza_completa


def processar_devolucao_nfe_xml(
    xml_bytes: bytes,
    supabase,
    empresa_id_ativo,
    empresa_ativa: dict,
    cnpjs_autorizados: set[str] | None = None,
) -> tuple[bool, str]:
    if supabase is None:
        return False, "Supabase indisponível; a devolução não pode ser persistida."
    try:
        raiz = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return False, "XML de devolução inválido."

    nfe = _xml_filho(raiz, "NFe") or raiz
    inf_nfe = _xml_filho(nfe, "infNFe")
    ide = _xml_filho(inf_nfe, "ide")
    if inf_nfe is None or _xml_texto(ide, "finNFe") != "4":
        return False, "O arquivo não contém uma NF-e de devolução."

    empresa_id_xml = str(empresa_ativa.get("id", ""))
    if (
        empresa_id_ativo is None
        or not empresa_id_xml
        or empresa_id_xml != str(empresa_id_ativo)
    ):
        return False, "Devolução cancelada: a empresa ativa não corresponde ao cadastro."

    cnpjs_permitidos = cnpjs_autorizados or obter_cnpjs_autorizados_empresa(empresa_ativa)
    emitente = _xml_filho(inf_nfe, "emit")
    cnpj_emitente = _CNPJ_PATTERN.sub("", _xml_texto(emitente, "CNPJ"))
    if not cnpj_emitente or cnpj_emitente not in cnpjs_permitidos:
        return False, "Devolução bloqueada: o emitente não pertence à empresa autorizada."

    id_nfe = str(inf_nfe.attrib.get("Id", "")).replace("NFe", "").strip()
    if not CHAVE_ACESSO_PATTERN.fullmatch(id_nfe):
        return False, "Devolução bloqueada: chave de acesso inválida."
    protocolo = _xml_filho(raiz, "protNFe")
    inf_protocolo = _xml_filho(protocolo, "infProt")
    chave_protocolo = _xml_texto(inf_protocolo, "chNFe")
    if _xml_texto(inf_protocolo, "cStat") != "100" or chave_protocolo != id_nfe:
        return (
            False,
            "Devolução bloqueada: o XML não contém protocolo de autorização SEFAZ correspondente.",
        )

    referencias = _xml_filhos(ide, "NFref")
    chave_original = ""
    for referencia in referencias:
        valor_referencia = _xml_texto(referencia, "refNFe")
        if CHAVE_ACESSO_PATTERN.fullmatch(valor_referencia):
            chave_original = valor_referencia
            break
    if not chave_original:
        return False, "Devolução bloqueada: não foi encontrada a chave da nota original."

    nota_original = None
    itens_originais = []
    devolucoes_existentes = []
    if supabase is not None:
        try:
            resposta_nota = (
                supabase.table("notas_fiscais_recebidas")
                .select("*")
                .eq("empresa_id", empresa_id_ativo)
                .eq("chave_acesso", chave_original)
                .limit(1)
                .execute()
            )
            notas_encontradas = resposta_nota.data or []
            nota_original = notas_encontradas[0] if notas_encontradas else None
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao localizar nota original da devolução na empresa %s.",
                empresa_id_ativo,
            )

    if nota_original is None:
        return False, "Devolução bloqueada: a nota original não está registrada para esta empresa."

    nota_original_id = nota_original.get("id")
    if supabase is not None and nota_original_id is not None:
        try:
            resposta_itens = (
                supabase.table("itens_nota_fiscal")
                .select("*")
                .eq("empresa_id", empresa_id_ativo)
                .eq("nota_id", nota_original_id)
                .execute()
            )
            itens_originais = resposta_itens.data or []
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao localizar itens da nota original %s.",
                nota_original_id,
            )
        if not itens_originais:
            espelho_empresa = carregar_espelho_empresa(
                supabase,
                empresa_id_ativo,
            )
            itens_originais = [
                item
                for item in espelho_empresa["item"]
                if str(item.get("nota_id", "")) == str(nota_original_id)
            ]
    if not itens_originais:
        return False, "Devolução bloqueada: não há itens auditados na nota original."

    if supabase is not None:
        try:
            existente = (
                supabase.table("conciliacao_devolucoes")
                .select("id")
                .eq("empresa_id", empresa_id_ativo)
                .eq("chave_nota_devolucao", id_nfe)
                .limit(1)
                .execute()
            )
            if existente.data:
                return True, "Esta devolução já foi processada anteriormente."
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao verificar duplicidade da chave de devolução."
            )
        devolucoes_espelho = carregar_espelho_empresa(
            supabase,
            empresa_id_ativo,
        )["devolucao"]
        if any(
            str(devolucao.get("chave_nota_devolucao", "")) == id_nfe
            for devolucao in devolucoes_espelho
        ):
            return True, "Esta devolução já foi processada anteriormente."
    total_original = _float_seguro(nota_original.get("valor_total_nf"), 0.0)
    if total_original <= 0:
        total_original = sum(
            _float_seguro(item.get("valor_item", item.get("valor_total_bruto")), 0.0)
            for item in itens_originais
        )
    total_devolvido = 0.0
    devolucao_por_ncm: dict[str, float] = {}
    for detalhe in _xml_filhos(inf_nfe, "det"):
        produto = _xml_filho(detalhe, "prod")
        ncm = "".join(filter(str.isdigit, _xml_texto(produto, "NCM")))
        valor = _float_seguro(_xml_texto(produto, "vProd"), 0.0)
        if ncm and valor > 0:
            total_devolvido += valor
            devolucao_por_ncm[ncm] = devolucao_por_ncm.get(ncm, 0.0) + valor

    total_devolvido = round(
        total_devolvido or _float_seguro(
            _xml_texto(
                _xml_filho(_xml_filho(inf_nfe, "total"), "ICMSTot"),
                "vNF",
            ),
            0.0,
        ),
        2,
    )
    if total_devolvido <= 0 or total_original <= 0:
        return False, "Devolução bloqueada: os valores da nota não permitem calcular o estorno."

    total_estornavel = 0.0
    for item in itens_originais:
        if item.get("classificacao_item") not in {
            "Aprovado Revenda",
            "Aprovado Manualmente Contador",
        }:
            continue
        total_estornavel += max(
            0.0,
            _float_seguro(item.get("valor_credito_item"), 0.0),
        )

    if devolucao_por_ncm:
        credito_estornado = 0.0
        for ncm, valor_devolvido_ncm in devolucao_por_ncm.items():
            itens_ncm = [
                item for item in itens_originais
                if "".join(filter(str.isdigit, str(item.get("ncm", "")))) == ncm
                and item.get("classificacao_item") in {
                    "Aprovado Revenda",
                    "Aprovado Manualmente Contador",
                }
            ]
            valor_original_ncm = sum(
                max(
                    0.0,
                    _float_seguro(
                        item.get("valor_item", item.get("valor_total_bruto")),
                        0.0,
                    ),
                )
                for item in itens_ncm
            )
            credito_ncm = sum(
                max(0.0, _float_seguro(item.get("valor_credito_item"), 0.0))
                for item in itens_ncm
            )
            if valor_original_ncm > 0:
                credito_estornado += credito_ncm * min(
                    1.0,
                    valor_devolvido_ncm / valor_original_ncm,
                )
    else:
        credito_estornado = total_estornavel * min(1.0, total_devolvido / total_original)

    if not devolucao_por_ncm:
        credito_estornado = min(
            total_estornavel,
            total_estornavel * min(1.0, total_devolvido / total_original),
        )
    credito_estornado = round(min(total_estornavel, credito_estornado), 2)

    if supabase is not None:
        try:
            resposta_devolucoes = (
                supabase.table("conciliacao_devolucoes")
                .select("valor_mercadoria_devolvida,credito_estornado")
                .eq("empresa_id", empresa_id_ativo)
                .eq("nota_original_id", nota_original_id)
                .execute()
            )
            devolucoes_existentes = resposta_devolucoes.data or []
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao calcular o saldo estornável das devoluções anteriores."
            )
        devolucoes_espelho = carregar_espelho_empresa(
            supabase,
            empresa_id_ativo,
        )["devolucao"]
        espelho_por_chave = {
            str(item.get("chave_nota_devolucao", "")): item
            for item in devolucoes_existentes
        }
        for devolucao in devolucoes_espelho:
            if str(devolucao.get("nota_original_id", "")) == str(nota_original_id):
                espelho_por_chave.setdefault(
                    str(devolucao.get("chave_nota_devolucao", "")),
                    devolucao,
                )
        devolucoes_existentes = list(espelho_por_chave.values())
    valor_ja_devolvido = sum(
        max(0.0, _float_seguro(item.get("valor_mercadoria_devolvida"), 0.0))
        for item in devolucoes_existentes
    )
    credito_ja_estornado = sum(
        max(0.0, _float_seguro(item.get("credito_estornado"), 0.0))
        for item in devolucoes_existentes
    )
    valor_restante = max(0.0, total_original - valor_ja_devolvido)
    valor_devolvido_registrado = min(total_devolvido, valor_restante)
    if total_devolvido > 0 and valor_devolvido_registrado < total_devolvido:
        credito_estornado *= valor_devolvido_registrado / total_devolvido
    credito_estornado = min(
        credito_estornado,
        max(0.0, total_estornavel - credito_ja_estornado),
    )

    payload_devolucao = {
        "empresa_id": empresa_id_ativo,
        "nota_original_id": nota_original_id,
        "chave_nota_devolucao": id_nfe,
        "valor_mercadoria_devolvida": round(valor_devolvido_registrado, 2),
        "credito_estornado": round(credito_estornado, 2),
        "data_devolucao": datetime.now().astimezone().isoformat(),
        "motivo_ajuste": (
            f"NF-e de devolução finNFe=4; referência à chave {chave_original}; "
            "estorno proporcional por NCM."
        ),
    }
    if payload_devolucao["valor_mercadoria_devolvida"] <= 0:
        return True, "O valor da mercadoria já foi integralmente devolvido."
    ok, _, _ = inserir_seguro_supabase(
        supabase,
        "conciliacao_devolucoes",
        payload_devolucao,
    )
    if not ok:
        return False, "Não foi possível registrar o estorno da devolução."
    limpar_caches_dados()
    st.toast(
        f"↩️ Devolução vinculada; R$ {credito_estornado:,.2f} de crédito estornados.",
        icon="🛡️",
    )
    return True, f"Devolução registrada; estorno de crédito R$ {credito_estornado:,.2f}."


def processar_xml_lote_bytes(
    arquivos: list[tuple[str, bytes]],
    supabase,
    empresa_id_ativo,
    empresa_ativa: dict,
    cnpjs_adicionais_autorizados: list[str] | None = None,
) -> list[tuple[bool, str]]:
    resultados = [(False, "Não foi possível processar o XML.") for _ in arquivos]
    if supabase is None:
        return [
            (False, "Supabase indisponível; o XML não foi persistido.")
            for _ in arquivos
        ]
    perfil_tributario = carregar_perfil_tributario_empresa(
        supabase,
        empresa_id_ativo,
    )
    fator_reducao = _float_seguro(
        perfil_tributario.get("fator_reducao"),
        1.0,
    )
    try:
        notas_existentes = _carregar_notas_existentes(
            supabase,
            empresa_id_ativo,
        )
    except RuntimeError as erro:
        return [(False, str(erro)) for _ in arquivos]
    hashes_lote: set[str] = set()
    chaves_lote: set[str] = set()
    preparados = []
    cnpjs_autorizados = obter_cnpjs_autorizados_empresa(
        empresa_ativa,
        cnpjs_adicionais_autorizados,
    )

    for indice, (nome_arquivo, conteudo) in enumerate(arquivos):
        try:
            raiz = ET.fromstring(conteudo)
            nfe = _xml_filho(raiz, "NFe") or raiz
            inf_nfe = _xml_filho(nfe, "infNFe")
            ide = _xml_filho(inf_nfe, "ide")
            if _xml_texto(ide, "finNFe") == "4":
                resultados[indice] = processar_devolucao_nfe_xml(
                    conteudo,
                    supabase,
                    empresa_id_ativo,
                    empresa_ativa,
                    cnpjs_autorizados,
                )
                continue
        except ET.ParseError:
            pass

        nota, itens, erro = _preparar_xml_nfe(
            conteudo,
            empresa_id_ativo,
            empresa_ativa,
            hashes_lote,
            chaves_lote,
            cnpjs_autorizados,
        )
        if erro:
            resultados[indice] = (False, erro)
        else:
            preparados.append((indice, nome_arquivo, nota, itens))

    if not preparados:
        return resultados

    notas_por_chave = {}
    novas_notas = []
    notas_para_atualizar = []
    for item_preparado in preparados:
        indice, _, nota_payload, _ = item_preparado
        chave = str(nota_payload["chave_acesso"])
        nota_existente = notas_existentes.get(chave)
        if nota_existente is not None:
            notas_para_atualizar.append((item_preparado, nota_existente))
        else:
            novas_notas.append(item_preparado)

    try:
        itens_existentes = _carregar_itens_existentes_empresa(
            supabase,
            empresa_id_ativo,
            notas_existentes,
        )
    except RuntimeError:
        itens_existentes = None
        for item_preparado, _ in notas_para_atualizar:
            resultados[item_preparado[0]] = (
                False,
                "Não foi possível carregar as decisões existentes para reprocessar a nota.",
            )
        notas_para_atualizar = []

    mensagem_notas = "OK"
    for item_preparado, nota_existente in notas_para_atualizar:
        indice, _, nota_payload, _ = item_preparado
        nota_id_existente = nota_existente.get("id")
        if nota_id_existente is None:
            resultados[indice] = (
                False,
                "A nota existente não possui vínculo persistente para sincronizar os itens.",
            )
            continue
        ok_atualizacao, mensagem_atualizacao, atualizada = executar_update_seguro(
            supabase,
            "notas_fiscais_recebidas",
            nota_payload,
            {
                "id": nota_id_existente,
                "empresa_id": empresa_id_ativo,
            },
            fallback_record=nota_existente,
        )
        if not ok_atualizacao:
            resultados[indice] = (
                False,
                "Não foi possível atualizar a nota existente para sincronizar seus itens.",
            )
            continue
        notas_por_chave[str(nota_payload["chave_acesso"])] = (
            atualizada[0] if atualizada else {**nota_existente, **nota_payload}
        )
        if mensagem_atualizacao != "OK":
            mensagem_notas = mensagem_atualizacao

    if novas_notas:
        ok_notas, mensagem_novas_notas, notas_resultado = inserir_seguro_supabase(
            supabase,
            "notas_fiscais_recebidas",
            [nota for _, _, nota, _ in novas_notas],
        )
        if not ok_notas or len(notas_resultado) != len(novas_notas):
            for indice, _, _, _ in novas_notas:
                resultados[indice] = (
                    False,
                    "Não foi possível registrar a nota. Tente novamente.",
                )
            return resultados
        mensagem_notas = mensagem_novas_notas
        notas_por_chave.update({
            str(nota.get("chave_acesso")): nota
            for nota in notas_resultado
            if nota.get("chave_acesso")
        })
        if any(
            not notas_por_chave.get(str(nota["chave_acesso"]), {}).get("id")
            for _, _, nota, _ in novas_notas
        ):
            notas_confirmadas = _carregar_notas_existentes(
                supabase,
                empresa_id_ativo,
            )
            for _, _, nota, _ in novas_notas:
                chave = str(nota["chave_acesso"])
                if chave in notas_confirmadas:
                    notas_por_chave[chave] = notas_confirmadas[chave]

    itens_lote = []
    dados_sucesso = []
    for indice, nome_arquivo, nota_payload, itens in preparados:
        if not resultados[indice][0] and resultados[indice][1] != "Não foi possível processar o XML.":
            continue
        nota_resultado = notas_por_chave.get(str(nota_payload["chave_acesso"]))
        nota_id = nota_resultado.get("id") if nota_resultado else None
        if nota_id is None:
            resultados[indice] = (False, "Não foi possível confirmar o registro da nota.")
            continue
        itens_da_nota = []
        cnaes_cliente = str(empresa_ativa.get("cnae_principal", "4711302"))
        em_contingencia = "Contingência" in str(
            nota_payload.get("manifestacao_status", "")
        )
        for item in itens:
            status, aliquota, credito = classificar_item_inteligente(
                ncm=str(item.get("ncm", "")),
                cfop=str(item.get("cfop", "")),
                qcom=_float_seguro(item.get("quantidade_comercial"), 0.0),
                vprod=_float_seguro(item.get("valor_item"), 0.0),
                crt_emitente=str(nota_payload.get("crt_emitente") or "3"),
                cnaes_cliente_str=cnaes_cliente,
                em_contingencia=em_contingencia,
                descricao=str(item.get("descricao", "")),
                fator_reducao=fator_reducao,
            )
            valor_item = _float_seguro(item.get("valor_item"), 0.0)
            aliquota_item = aliquota or _aliquota_base_item(
                str(item.get("ncm", "")),
                str(nota_payload.get("crt_emitente") or "3"),
                fator_reducao,
            )
            if item.get("_tributos_xml_presentes"):
                credito = item.get("_credito_xml", 0.0)
                credito_ibs = item.get("_credito_ibs_xml", 0.0)
                credito_cbs = item.get("_credito_cbs_xml", 0.0)
                aliquota_ibs = 0.177
                aliquota_cbs = 0.088
            else:
                credito = _arredondar_dinheiro(
                    Decimal(str(valor_item)) * Decimal(str(aliquota_item))
                )
                aliquota_ibs = aliquota_item * (0.177 / 0.265)
                aliquota_cbs = aliquota_item * (0.088 / 0.265)
                credito_ibs = _arredondar_dinheiro(
                    Decimal(str(valor_item)) * Decimal(str(aliquota_ibs))
                )
                credito_cbs = _arredondar_dinheiro(
                    Decimal(str(valor_item)) * Decimal(str(aliquota_cbs))
                )
            aliquota = aliquota_item
            uid = str(item.get("item_uid") or "")
            item_persistido = {
                **item,
                "nota_id": nota_id,
                "aliquota_ibs_aplicada": aliquota_ibs,
                "aliquota_cbs_aplicada": aliquota_cbs,
                "credito_ibs_gerado": _arredondar_dinheiro(credito_ibs),
                "credito_cbs_gerado": _arredondar_dinheiro(credito_cbs),
                "aliquota_aplicada": aliquota,
                "valor_credito_item": _arredondar_dinheiro(credito),
                "classificacao_item": status,
                "status_credito": _status_credito_persistido(status),
                "justificativa_motor": _justificativa_classificacao_item(
                    str(item.get("descricao", "")),
                    status,
                ),
            }
            for campo_interno in (
                "_tributos_xml_presentes",
                "_credito_xml",
                "_credito_ibs_xml",
                "_credito_cbs_xml",
            ):
                item_persistido.pop(campo_interno, None)

            anterior = (itens_existentes or {}).get(uid)
            classificacao_anterior = str(
                (anterior or {}).get("classificacao_item", "")
            )
            mesma_mercadoria = bool(anterior) and (
                str(anterior.get("ncm", "")) == str(item.get("ncm", ""))
                and str(anterior.get("descricao", "")) == str(item.get("descricao", ""))
                and round(
                    _float_seguro(
                        anterior.get("valor_item", anterior.get("valor_total_bruto")),
                        0.0,
                    ),
                    2,
                ) == round(valor_item, 2)
            )
            if mesma_mercadoria and classificacao_anterior in {
                "Aprovado Manualmente Contador",
                "Rejeitado pelo Contador",
            }:
                item_persistido.update({
                    campo: anterior[campo]
                    for campo in (
                        "classificacao_item",
                        "status_credito",
                        "aliquota_aplicada",
                        "aliquota_ibs_aplicada",
                        "aliquota_cbs_aplicada",
                        "valor_credito_item",
                        "credito_ibs_gerado",
                        "credito_cbs_gerado",
                        "justificativa_motor",
                    )
                    if campo in anterior
                })
            itens_da_nota.append(item_persistido)
        itens_lote.extend(itens_da_nota)
        dados_sucesso.append((indice, nome_arquivo, nota_payload, itens_da_nota))

    if itens_lote:
        notas_para_limpar = {
            str(notas_por_chave[str(nota_payload["chave_acesso"])]["id"])
            for _, _, nota_payload, _ in dados_sucesso
            if str(nota_payload["chave_acesso"]) in notas_por_chave
        }
        chaves_para_limpar = {
            str(nota_payload["chave_acesso"])
            for _, _, nota_payload, _ in dados_sucesso
        }
        _limpar_itens_anteriores(
            supabase,
            empresa_id_ativo,
            notas_para_limpar,
            chaves_para_limpar,
        )
        mensagens_itens = []
        persistencia_completa = True
        for inicio in range(0, len(itens_lote), 25):
            lote_itens = itens_lote[inicio:inicio + 25]
            ok_itens, mensagem_lote, _ = inserir_seguro_supabase(
                supabase,
                "itens_nota_fiscal",
                lote_itens,
            )
            if not ok_itens:
                espelho_ok = persistir_espelho_empresa(
                    supabase,
                    empresa_id_ativo,
                    "item",
                    lote_itens,
                )
                if espelho_ok:
                    mensagem_lote = "Espelho Supabase persistido."
                else:
                    mensagem_lote = "Não foi possível persistir no Supabase."
            mensagens_itens.append(mensagem_lote)
            if not ok_itens and mensagem_lote != "Espelho Supabase persistido.":
                persistencia_completa = False

        mensagem_itens = (
            "Espelho Supabase persistido."
            if "Espelho Supabase persistido." in mensagens_itens
            else (
                "Não foi possível persistir no Supabase."
                if "Não foi possível persistir no Supabase." in mensagens_itens
                else "OK"
            )
        )
        if not persistencia_completa:
            for indice, _, nota_payload, _ in dados_sucesso:
                try:
                    nota_id = notas_por_chave[str(nota_payload["chave_acesso"])]["id"]
                    if str(nota_id).startswith("local_"):
                        chave_sessao = f"fallback_notas_{empresa_id_ativo}"
                        st.session_state[chave_sessao] = [
                            nota for nota in st.session_state.get(chave_sessao, [])
                            if str(nota.get("id")) != str(nota_id)
                        ]
                    else:
                        supabase.table("notas_fiscais_recebidas").delete().eq(
                            "id", nota_id
                        ).eq("empresa_id", empresa_id_ativo).execute()
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Falha ao desfazer nota parcialmente persistida."
                    )
                resultados[indice] = (False, "Não foi possível registrar os itens da nota.")
            return resultados

    limpar_caches_dados()
    for indice, nome_arquivo, nota, itens in dados_sucesso:
        credito_bruto = calcular_resumo_creditos(itens)["bruto"]
        resultados[indice] = (
            True,
            f"NF-e {str(nota['chave_acesso'])[:10]}... auditada com sucesso "
            f"({len(itens)} itens processados; crédito bruto R$ {credito_bruto:,.2f}).",
        )
    if itens_lote and mensagem_itens == "Espelho Supabase persistido.":
        st.toast(
            "Itens protegidos no espelho persistente do Supabase.",
            icon="✅",
        )
    elif itens_lote and "Não foi possível persistir" in mensagem_itens:
        st.warning(
            "Não foi possível confirmar a persistência dos itens no Supabase.",
        )
    if dados_sucesso:
        st.toast(
            f"✅ {len(dados_sucesso)} NF-e(s) importadas em lote.",
            icon="💰",
        )
    return resultados


def processar_xml_bytes(
    xml_bytes: bytes,
    supabase,
    empresa_id_ativo,
    empresa_ativa: dict,
    cnpjs_adicionais_autorizados: list[str] | None = None,
) -> tuple[bool, str]:
    return processar_xml_lote_bytes(
        [("arquivo.xml", xml_bytes)],
        supabase,
        empresa_id_ativo,
        empresa_ativa,
        cnpjs_adicionais_autorizados,
    )[0]


def converter_danfe_pdf_para_xml(
    pdf_bytes: bytes,
    cnpj_cliente_ativo: str,
    razao_cliente_ativo: str,
) -> bytes:
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("O arquivo não é um PDF válido.")
    reader = None
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        texto = "\n".join(pagina.extract_text() or "" for pagina in reader.pages)
    except ImportError:
        texto = ""
    except Exception as erro:
        raise ValueError("Não foi possível ler o conteúdo deste PDF.") from erro

    metadados = reader.metadata or {} if reader is not None else {}
    payload_pdf = None
    try:
        valor_metadado = metadados.get("/PraesidiumPayload")
        keywords = metadados.get("/Keywords")
        if not valor_metadado and keywords:
            prefixo = "PraesidiumPayload:"
            if str(keywords).startswith(prefixo):
                valor_metadado = str(keywords)[len(prefixo):]
        if valor_metadado:
            texto_metadado = str(valor_metadado)
            if texto_metadado.startswith("{"):
                payload_pdf = json.loads(texto_metadado)
            else:
                payload_pdf = json.loads(
                    base64.urlsafe_b64decode(texto_metadado.encode("ascii"))
                )
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        logging.getLogger(__name__).warning(
            "Metadados fiscais do PDF não puderam ser interpretados; "
            "tentando extração textual."
        )
    if reader is None and payload_pdf is None:
        correspondencia_payload = re.search(
            rb"/(?:PraesidiumPayload\s*\(|Keywords\s*\(PraesidiumPayload:)"
            rb"([A-Za-z0-9_\-=]+)\)",
            pdf_bytes,
        )
        if correspondencia_payload:
            try:
                payload_pdf = json.loads(
                    base64.urlsafe_b64decode(correspondencia_payload.group(1))
                )
            except (ValueError, json.JSONDecodeError):
                payload_pdf = None
        if payload_pdf is None:
            raise RuntimeError(
                "Para importar DANFEs externos em PDF, instale a dependência pypdf."
            )

    cnpj_cliente = "".join(filter(str.isdigit, str(cnpj_cliente_ativo)))
    if len(cnpj_cliente) != 14:
        raise ValueError("O CNPJ da empresa ativa é inválido.")

    def valor_monetario(valor: object) -> float:
        texto_valor = str(valor or "0").strip().replace("R$", "").replace(" ", "")
        if "," in texto_valor:
            texto_valor = texto_valor.replace(".", "").replace(",", ".")
        return _float_seguro(texto_valor, 0.0)

    dados = payload_pdf if isinstance(payload_pdf, dict) else {}
    if dados:
        cnpj_destinatario = "".join(
            filter(str.isdigit, str(dados.get("cnpj_destinatario", "")))
        )
        if cnpj_destinatario != cnpj_cliente:
            raise ValueError(
                "O DANFE pertence a outro destinatário e não pode ser importado."
            )
        chave = "".join(filter(str.isdigit, str(dados.get("chave_acesso", ""))))
        cnpj_emitente = "".join(
            filter(str.isdigit, str(dados.get("cnpj_emitente", "")))
        )
        nome_emitente = str(dados.get("nome_emitente") or "").strip()
        numero = str(dados.get("numero") or "1")
        serie = str(dados.get("serie") or "1")
        data_emissao = str(dados.get("data_emissao") or datetime.now().astimezone().isoformat())
        itens_pdf = dados.get("itens", [])
    else:
        correspondencia_chave = re.search(
            r"(?<!\d)(?:\d[\s.\-/]?){44}(?!\d)",
            texto,
        )
        chave = (
            re.sub(r"\D", "", correspondencia_chave.group(0))
            if correspondencia_chave
            else ""
        )
        cnpjs_encontrados = []
        for correspondencia in re.finditer(
            r"(?<!\d)(?:\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)",
            texto,
        ):
            cnpj_encontrado = re.sub(r"\D", "", correspondencia.group(0))
            if len(cnpj_encontrado) == 14:
                cnpjs_encontrados.append(cnpj_encontrado)
        if cnpj_cliente not in cnpjs_encontrados:
            raise ValueError(
                "Não foi possível validar o CNPJ do destinatário no DANFE."
            )
        cnpj_destinatario = cnpj_cliente
        cnpj_emitente = next(
            (cnpj for cnpj in cnpjs_encontrados if cnpj != cnpj_cliente),
            "",
        )
        if not cnpj_emitente:
            raise ValueError("Não foi possível identificar o CNPJ do emitente.")

        linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
        nome_emitente = ""
        for indice, linha in enumerate(linhas):
            if re.search(r"raz[aã]o social|nome\s*/?\s*raz[aã]o|emitente", linha, re.I):
                nome_emitente = re.sub(
                    r"(?i).*?(?:raz[aã]o social|nome\s*/?\s*raz[aã]o|emitente)\s*:?",
                    "",
                    linha,
                ).strip(" :-")
                if not nome_emitente and indice + 1 < len(linhas):
                    nome_emitente = linhas[indice + 1]
                if nome_emitente:
                    break
        if not nome_emitente:
            nome_emitente = "Emitente identificado no DANFE"
        numero_match = re.search(r"(?i)\bN[º°o]?\s*[:.]?\s*(\d{1,12})", texto)
        serie_match = re.search(r"(?i)\bS[ée]rie\s*[:.]?\s*(\d{1,3})", texto)
        numero = numero_match.group(1) if numero_match else "1"
        serie = serie_match.group(1) if serie_match else "1"
        data_match = re.search(
            r"\b(\d{2}/\d{2}/\d{4})\b",
            texto,
        )
        data_emissao = (
            datetime.strptime(data_match.group(1), "%d/%m/%Y")
            .replace(tzinfo=timezone.utc)
            .isoformat()
            if data_match
            else datetime.now(timezone.utc).isoformat()
        )
        itens_pdf = []
        for linha in linhas:
            if linha.startswith("ITEM|"):
                partes = linha.split("|")
                if len(partes) >= 11:
                    itens_pdf.append({
                        "numero": partes[1],
                        "codigo": partes[2],
                        "descricao": partes[3],
                        "ncm": partes[4],
                        "cfop": partes[5],
                        "quantidade": partes[6],
                        "valor_unitario": partes[7],
                        "valor_total": partes[8],
                        "ibs": partes[9],
                        "cbs": partes[10],
                    })
                continue
            padrao_item = re.search(
                r"(?P<codigo>\S+)\s+(?P<descricao>.*?)\s+"
                r"(?P<ncm>\d{8})\s+(?:(?:\d{2,3})\s+)?(?P<cfop>\d{4})\s+"
                r"\S+\s+(?P<qtd>[\d.,]+)\s+(?P<unit>[\d.,]+)\s+"
                r"(?P<total>[\d.,]+)(?:\s|$)",
                linha,
            )
            if padrao_item:
                itens_pdf.append({
                    "numero": str(len(itens_pdf) + 1),
                    "codigo": padrao_item.group("codigo"),
                    "descricao": padrao_item.group("descricao").strip(),
                    "ncm": padrao_item.group("ncm"),
                    "cfop": padrao_item.group("cfop"),
                    "quantidade": padrao_item.group("qtd"),
                    "valor_unitario": padrao_item.group("unit"),
                    "valor_total": padrao_item.group("total"),
                    "ibs": None,
                    "cbs": None,
                })

        if not chave:
            digest = hashlib.sha256(pdf_bytes).hexdigest()
            chave = str(int(digest, 16)).zfill(44)[-44:]

    if len(chave) != 44 or not chave.isdigit():
        digest = hashlib.sha256(pdf_bytes).hexdigest()
        chave = str(int(digest, 16)).zfill(44)[-44:]
    if len(cnpj_emitente) != 14 or not cnpj_emitente.isdigit():
        raise ValueError("O CNPJ do emitente extraído do PDF é inválido.")
    if cnpj_destinatario != cnpj_cliente:
        raise ValueError("O CNPJ do destinatário não corresponde à empresa ativa.")
    if not isinstance(itens_pdf, list) or not itens_pdf:
        raise ValueError(
            "Não foi possível identificar itens na tabela do DANFE em PDF."
        )

    detalhes_xml = []
    for indice, item in enumerate(itens_pdf, start=1):
        if not isinstance(item, dict):
            continue
        numero_item = int(_float_seguro(item.get("numero"), indice))
        codigo = escape_xml(str(item.get("codigo") or f"ITEM-{numero_item:03d}"))
        descricao = escape_xml(str(item.get("descricao") or "Item extraído do DANFE"))
        ncm = re.sub(r"\D", "", str(item.get("ncm") or ""))
        cfop = re.sub(r"\D", "", str(item.get("cfop") or ""))
        if len(ncm) != 8:
            ncm = "00000000"
        if len(cfop) != 4:
            cfop = "5102"
        quantidade = valor_monetario(item.get("quantidade"))
        valor_unitario = valor_monetario(item.get("valor_unitario"))
        valor_total = valor_monetario(item.get("valor_total"))
        tem_ibs = item.get("ibs") is not None
        tem_cbs = item.get("cbs") is not None
        tags_tributos = ""
        if tem_ibs or tem_cbs:
            ibs = valor_monetario(item.get("ibs"))
            cbs = valor_monetario(item.get("cbs"))
            tags_tributos = (
                f"<IBSCBS><vIBS>{ibs:.2f}</vIBS>"
                f"<vCBS>{cbs:.2f}</vCBS></IBSCBS>"
            )
        detalhes_xml.append(
            f'<det nItem="{numero_item}"><prod><cProd>{codigo}</cProd>'
            f'<xProd>{descricao}</xProd><NCM>{ncm}</NCM><CFOP>{cfop}</CFOP>'
            f'<uCom>UN</uCom><qCom>{quantidade:.4f}</qCom>'
            f'<vUnCom>{valor_unitario:.2f}</vUnCom><vProd>{valor_total:.2f}</vProd>'
            f'</prod><imposto>{tags_tributos}</imposto></det>'
        )
    if not detalhes_xml:
        raise ValueError("Nenhum item válido foi reconstruído a partir do DANFE.")

    total_produtos = sum(
        valor_monetario(item.get("valor_total"))
        for item in itens_pdf
        if isinstance(item, dict)
    )
    chave_formatada = chave
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">'
        '<NFe><infNFe Id="NFe' + chave_formatada + '" versao="4.00">'
        f'<ide><cUF>{chave[:2]}</cUF><mod>55</mod><serie>{escape_xml(serie)}</serie>'
        f'<nNF>{escape_xml(numero)}</nNF><dhEmi>{escape_xml(data_emissao)}</dhEmi>'
        '<tpEmis>1</tpEmis><finNFe>1</finNFe></ide>'
        f'<emit><CNPJ>{cnpj_emitente}</CNPJ><xNome>{escape_xml(nome_emitente)}</xNome>'
        '<CRT>3</CRT></emit>'
        f'<dest><CNPJ>{cnpj_cliente}</CNPJ>'
        f'<xNome>{escape_xml(razao_cliente_ativo)}</xNome></dest>'
        + "".join(detalhes_xml)
        + f'<total><ICMSTot><vProd>{total_produtos:.2f}</vProd>'
        f'<vNF>{total_produtos:.2f}</vNF></ICMSTot></total>'
        '<infAdic><infCpl>DANFE PDF convertido para XML pelo Praesidium; '
        'documento reconstruído para auditoria interna.</infCpl></infAdic>'
        '</infNFe></NFe></nfeProc>'
    )
    return xml.encode("utf-8")


# ==========================================
# 4. INTERFACE: MÓDULO DE INGESTÃO XML & ROBÔ SEFAZ
# ==========================================
@fragment_if_supported
def renderizar_modulo_xml(supabase, empresa_id_ativo, empresa_ativa: dict):
    upload_feedback_key = f"upload_feedback_{empresa_id_ativo}"
    feedback_upload = st.session_state.pop(upload_feedback_key, None)
    if feedback_upload:
        for nivel, mensagem in feedback_upload:
            if nivel == "success":
                st.toast(mensagem, icon="✅")
            else:
                st.warning(mensagem)

    aviso_regra = st.session_state.pop(f"aviso_regra_{empresa_id_ativo}", None)
    if aviso_regra:
        st.warning(aviso_regra)

    with st.expander(
        "📁 1. Central de Ingestão XML & Sincronização Robô SEFAZ (A1 / Upload em Lote)",
        expanded=st.session_state.get("modulo_ativo") == "xml",
    ):
        c_rob, c_up = st.columns([1.1, 1.9])

        with c_rob:
            st.markdown("##### 🤖 Robô de Captura SEFAZ (Simulação Homologação)")
            st.caption(
                f"Simula a busca direta no WebService SEFAZ para **{limpar_tag_tenant_empresa(empresa_ativa['razao_social'])}** "
                f"(CNAEs: `{empresa_ativa.get('cnae_principal', '4711302')}`)."
            )
            if st.button("🔄 Sincronizar Lote de Teste SEFAZ", use_container_width=True, key=f"btn_sync_sefaz_{empresa_id_ativo}"):
                if (
                    empresa_id_ativo is None
                    or str(empresa_ativa.get("id")) != str(empresa_id_ativo)
                    or not "".join(filter(str.isdigit, str(empresa_ativa.get("cnpj", ""))))
                ):
                    st.error("Sincronização indisponível: selecione uma empresa válida com CNPJ.")
                    st.stop()

                cnaes_str = str(empresa_ativa.get("cnae_principal", "4711302"))
                is_beleza = any(c.strip().startswith(("9602", "4772")) for c in cnaes_str.split(","))

                if is_beleza:
                    itens_sim = [
                        ("KIT COLORACAO PROFISSIONAL LOREAL (CX 24 UN)", "33059000", "5102", 24.0, 4800.00),
                        ("SHAMPOO LAVATORIO 5L KERASTASE (LOTE 10 UN)", "33051000", "5102", 10.0, 3200.00),
                        ("SECADOR PROFISSIONAL TAIFF 2400W (LOTE 6 UN)", "85163100", "5102", 6.0, 2400.00),
                        ("CADEIRA DE ESCRITORIO ERGONOMICA DIRETOR (1 UN)", "94013090", "5556", 1.0, 1450.00),
                        ("ENERGETICO RED BULL LATA 250ML (CONSUMO INTERNO)", "22021000", "5102", 2.0, 350.00),
                    ]
                else:
                    itens_sim = [
                        ("ARROZ AGULHINHA TIPO 1 5KG (FD 60 UN - CESTA BASICA)", "10063021", "5102", 60.0, 1800.00),
                        ("QUEIJO MUSSARELA PECA (LOTE 20 KG - REDUCAO 60%)", "04061010", "5102", 20.0, 1200.00),
                        ("SHAMPOO DOVE 400ML REVENDA (CX 48 UN)", "33051000", "5102", 48.0, 6400.00),
                        ("REFRIGERANTE LATA 350ML REVENDA (PALETE 120 UN)", "22021000", "5102", 120.0, 3600.00),
                        ("NOTEBOOK ADMINISTRATIVO USO INTERNO (1 UN)", "84713012", "5556", 1.0, 3500.00),
                    ]

                chave_sim = "352611" + "".join([str(random.randint(0, 9)) for _ in range(38)])
                hash_sim = hashlib.sha256(chave_sim.encode()).hexdigest()
                vl_tot_sim = sum(x[4] for x in itens_sim)

                payload_nf_sim = {
                    "empresa_id": empresa_id_ativo,
                    "chave_acesso": chave_sim,
                    "cnpj_emitente": "60746948000112",
                    "razao_social_emitente": _FORNECEDOR_PADRAO,
                    "nome_emitente": _FORNECEDOR_PADRAO,
                    "crt_emitente": "3",
                    "valor_total_nf": round(vl_tot_sim, 2),
                    "manifestacao_status": "Ciência da Operação (SEFAZ OK)",
                    "status_processamento": "Processado",
                    "hash_sha256_xml": hash_sim,
                    "regra_tributaria_version": VERSAO_REGRA_TRIBUTARIA,
                    "versao_regra": VERSAO_REGRA_TRIBUTARIA,
                    "data_emissao": datetime.now().isoformat(),
                }

                ok_nf, msg_nf, data_nf = inserir_seguro_supabase(supabase, "notas_fiscais_recebidas", payload_nf_sim)
                if not ok_nf:
                    st.error("Não foi possível registrar o lote de teste. Verifique a empresa ativa.")
                    st.stop()
                if not data_nf or data_nf[0].get("id") is None:
                    st.error("Não foi possível confirmar o registro do lote de teste.")
                    st.stop()
                n_id = data_nf[0]["id"]

                novos_itens = []
                for nitem, (desc, ncm, cfop, qcom, vprod) in enumerate(
                    itens_sim,
                    start=1,
                ):
                    status_it, aliq, cred = classificar_item_inteligente(
                        ncm,
                        cfop,
                        qcom,
                        vprod,
                        "3",
                        cnaes_str,
                        False,
                        descricao=desc,
                    )
                    if status_it == "Quarentena Uso/Consumo":
                        aliq = ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA
                    novos_itens.append({
                            "empresa_id": empresa_id_ativo,
                            "nota_id": n_id,
                            "item_uid": _gerar_item_uid(chave_sim, nitem),
                        "nitem": nitem,
                        "descricao": desc,
                        "ncm": ncm,
                        "cfop": cfop,
                        "qcom": qcom,
                        "quantidade_comercial": qcom,
                        "valor_item": vprod,
                        "aliquota_aplicada": aliq,
                        "valor_credito_item": cred,
                        "classificacao_item": status_it,
                        "justificativa_motor": _justificativa_classificacao_item(
                            desc,
                            status_it,
                        ),
                    })

                ok_it, msg_it, _ = inserir_seguro_supabase(supabase, "itens_nota_fiscal", novos_itens)
                if not ok_it:
                    try:
                        supabase.table("notas_fiscais_recebidas").delete().eq(
                            "id", n_id
                        ).eq("empresa_id", empresa_id_ativo).execute()
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Falha ao remover nota simulada %s após falha ao persistir os itens.",
                            n_id,
                        )
                    st.error("Não foi possível registrar os itens do lote de teste.")
                    st.stop()

                limpar_caches_dados()
                credito_lote = calcular_resumo_creditos(novos_itens)["bruto"]
                st.toast(
                    f"✅ Lote sincronizado: R$ {credito_lote:,.2f} em crédito bruto auditado.",
                    icon="💰",
                )
                st.toast("Lote SEFAZ sincronizado e classificado com sucesso.", icon="✅")
                if msg_nf == "Espelho Supabase persistido.":
                    st.toast(
                        "Nota protegida no espelho persistente do Supabase.",
                        icon="✅",
                    )
                if msg_it == "Espelho Supabase persistido.":
                    st.toast(
                        "Itens protegidos no espelho persistente do Supabase.",
                        icon="✅",
                    )
                st.session_state["modulo_ativo"] = "xml"
                st.rerun()

        with c_up:
            st.markdown("##### 📤 Upload de XML ou DANFE em PDF")
            st.caption(
                "Cada NF-e será vinculada somente a um CNPJ autorizado desta empresa; "
                "arquivos de terceiros são rejeitados. DANFEs em PDF são reconstruídos "
                "em memória e passam pela mesma auditoria CNAE."
            )
            if "up_reset" not in st.session_state:
                st.session_state["up_reset"] = 0
            arquivos_xml = st.file_uploader(
                "Selecione um ou mais arquivos .XML ou .PDF (DANFE) de NF-e",
                type=["xml", "pdf"],
                accept_multiple_files=True,
                key=f"xml_up_{empresa_id_ativo}_{st.session_state.get('up_reset', 0)}"
            )
            if arquivos_xml:
                if st.button("⚡ Auditar e Importar Arquivos Selecionados", use_container_width=True, key=f"btn_proc_xml_{empresa_id_ativo}"):
                    documentos_xml = []
                    indices_documentos = []
                    resultados_lote: list[tuple[bool, str] | None] = [
                        None for _ in arquivos_xml
                    ]
                    for indice, arquivo in enumerate(arquivos_xml):
                        conteudo = arquivo.getvalue()
                        if str(arquivo.name).lower().endswith(".pdf"):
                            try:
                                conteudo = converter_danfe_pdf_para_xml(
                                    conteudo,
                                    str(empresa_ativa.get("cnpj", "")),
                                    str(empresa_ativa.get("razao_social", "")),
                                )
                            except (RuntimeError, ValueError) as erro:
                                resultados_lote[indice] = (False, str(erro))
                                continue
                        documentos_xml.append((arquivo.name, conteudo))
                        indices_documentos.append(indice)
                    if documentos_xml:
                        resultados_processados = processar_xml_lote_bytes(
                            documentos_xml,
                            supabase,
                            empresa_id_ativo,
                            empresa_ativa,
                        )
                        for indice_original, resultado in zip(
                            indices_documentos,
                            resultados_processados,
                        ):
                            resultados_lote[indice_original] = resultado
                    resultados_final = [
                        resultado or (
                            False,
                            "Não foi possível processar o arquivo. Tente novamente.",
                        )
                        for resultado in resultados_lote
                    ]
                    sucessos = sum(1 for ok, _ in resultados_final if ok)
                    feedback_processamento = [
                        (
                            "success" if ok else "warning",
                            f"{'✅' if ok else '⚠️'} {arquivo.name}: {mensagem}",
                        )
                        for arquivo, (ok, mensagem) in zip(
                            arquivos_xml,
                            resultados_final,
                        )
                    ]
                    st.session_state["up_reset"] += 1
                    st.session_state[upload_feedback_key] = feedback_processamento
                    if sucessos:
                        limpar_caches_dados()
                    st.session_state["modulo_ativo"] = "xml"
                    st.rerun()


# ==========================================
# 5. INTERFACE: EMISSOR DE NF-e DE DEVOLUÇÃO EXPRESSA
# ==========================================
@fragment_if_supported
def renderizar_emissor_devolucao(supabase, empresa_ativa: dict, notas: list, itens: list):
    with st.expander(
        "📁 2. Emissor Express de NF-e de Devolução (Avaria / Divergência Física & Estorno Split Payment)",
        expanded=st.session_state.get("modulo_ativo") == "devolucao",
    ):
        if not notas:
            st.info("Importe ou sincronize ao menos uma NF-e acima para habilitar o emissor de devolução.")
            return

        opcoes_nf = {
            f"NF-e {str(n.get('chave_acesso', ''))[:12]}... | Fornecedor: {n.get('cnpj_emitente', '')} | R$ {float(n.get('valor_total_nf', 0.0)):,.2f}": n
            for n in notas
        }
        nf_escolhida_label = st.selectbox(
            "1. Selecione a Nota Fiscal de Origem:",
            list(opcoes_nf.keys()),
            key=f"sel_nf_dev_{empresa_ativa['id']}"
        )
        nf_orig = opcoes_nf[nf_escolhida_label]
        itens_da_nota = [
            i for i in itens
            if str(i.get("nota_id")) == str(nf_orig.get("id"))
        ]

        if not itens_da_nota:
            st.warning("Esta nota não possui itens detalhados para devolução.")
            return

        opcoes_itens = {
            (
                f"{item.get('descricao', 'Item')} (NCM: {item.get('ncm', '')} "
                f"| Valor: R$ {float(item.get('valor_item', 0.0)):,.2f} "
                f"| Crédito: R$ {float(item.get('valor_credito_item', 0.0)):,.2f} "
                f"| Item {item.get('nitem') or indice + 1} "
                f"| {item.get('item_uid') or indice})"
            ): item
            for indice, item in enumerate(itens_da_nota)
        }
        item_escolhido_label = st.selectbox(
            "2. Selecione a Mercadoria com Avaria / Divergência:",
            list(opcoes_itens.keys()),
            key=f"sel_item_dev_{nf_orig.get('id', '0')}"
        )
        item_alvo = opcoes_itens[item_escolhido_label]
        vl_max_item = float(item_alvo.get("valor_item", 0.0))
        cred_max_item = float(item_alvo.get("valor_credito_item", 0.0))
        item_key_id = item_alvo.get("id", "item_0")

        c_d1, c_d2, c_d3 = st.columns([1.2, 1.2, 1.6])
        with c_d1:
            tipo_dev = st.radio(
                "Modalidade:",
                ["Devolução Total do Item", "Devolução Parcial (Avaria/Falta)"],
                key=f"rad_tipo_dev_{item_key_id}"
            )
        with c_d2:
            if tipo_dev == "Devolução Total do Item":
                valor_devolvido = st.number_input(
                    "Valor a Devolver (R$):",
                    value=vl_max_item,
                    disabled=True,
                    format="%.2f",
                    key=f"num_dev_tot_{item_key_id}"
                )
            else:
                valor_devolvido = st.number_input(
                    "Valor Parcial a Devolver (R$):",
                    min_value=1.0,
                    max_value=max(1.0, vl_max_item),
                    value=round(vl_max_item * 0.5, 2),
                    step=10.0,
                    format="%.2f",
                    key=f"num_dev_parc_{item_key_id}"
                )
        with c_d3:
            motivo_dev = st.selectbox(
                "Motivo Fiscal (finNFe=4):",
                [
                    "Avaria no Transporte / Embalagem Violada",
                    "Divergência de Quantidade Física na Entrega",
                    "Produto Fora da Especificação / Validade Próxima",
                    "Erro de Faturamento pelo Fornecedor"
                ],
                key=f"mot_dev_{item_key_id}"
            )

        proporcao = (valor_devolvido / vl_max_item) if vl_max_item > 0 else 0.0
        credito_a_estornar = round(cred_max_item * proporcao, 2)

        st.markdown(
            f"<div style='background:#111827; border-left:4px solid #f59e0b; padding:10px 14px; border-radius:6px; margin:8px 0;'>"
            f"📌 <b>Resumo do Estorno Automático:</b> Mercadoria devolvida: <b>R$ {valor_devolvido:,.2f}</b> | "
            f"Crédito IBS/CBS a estornar no painel: <b style='color:#f87171;'>-R$ {credito_a_estornar:,.2f}</b></div>",
            unsafe_allow_html=True
        )

        if st.button("🚨 Emitir NF-e de Devolução (finNFe=4) e Estornar Crédito", use_container_width=True, key=f"btn_emitir_dev_{item_key_id}"):
            chave_dev = "352611" + "".join([str(random.randint(0, 9)) for _ in range(38)])
            motivo_formatado = f"[{empresa_ativa['cnpj']}] {motivo_dev} - Item: {str(item_alvo.get('descricao', ''))[:35]}"

            payload_dev = {
                "empresa_id": empresa_ativa["id"],
                "nota_original_id": nf_orig.get("id"),
                "chave_nota_devolucao": chave_dev,
                "valor_mercadoria_devolvida": round(valor_devolvido, 2),
                "credito_estornado": credito_a_estornar,
                "data_devolucao": datetime.now().astimezone().isoformat(),
                "motivo_ajuste": motivo_formatado
            }
            ok_dev, _, _ = inserir_seguro_supabase(
                supabase, "conciliacao_devolucoes", payload_dev
            )

            xml_dev_str = gerar_xml_devolucao_nfe(
                empresa_ativa=empresa_ativa,
                nota_orig=nf_orig,
                chave_devolucao=chave_dev,
                itens_devolvidos=[{
                    "descricao": item_alvo.get("descricao", "Item"),
                    "ncm": item_alvo.get("ncm", "00000000"),
                    "valor_devolvido": valor_devolvido
                }],
                valor_total_dev=valor_devolvido,
                credito_estornado=credito_a_estornar,
                motivo=motivo_dev
            )
            st.session_state[f"ultimo_xml_dev_{empresa_ativa['id']}"] = (chave_dev, xml_dev_str)
            if ok_dev:
                limpar_caches_dados()
                st.toast(
                    f"↩️ Devolução registrada; R$ {credito_a_estornar:,.2f} de crédito serão estornados.",
                    icon="🛡️",
                )
                st.session_state["modulo_ativo"] = "devolucao"
                st.success("✅ Devolução registrada e crédito estornado em tempo real!")
                st.rerun()
            else:
                st.error("O XML foi gerado, mas não foi possível registrar a devolução.")

        if f"ultimo_xml_dev_{empresa_ativa['id']}" in st.session_state:
            ch_gerada, xml_gerado = st.session_state[f"ultimo_xml_dev_{empresa_ativa['id']}"]
            st.download_button(
                label=f"📥 Baixar XML Oficial da NF-e de Devolução ({ch_gerada[:14]}...)",
                data=xml_gerado.encode("utf-8"),
                file_name=f"NFe_Devolucao_{ch_gerada}.xml",
                mime="application/xml",
                use_container_width=True,
                key=f"dl_xml_dev_{empresa_ativa['id']}"
            )
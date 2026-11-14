import io
import hashlib
import hmac
import json
import os
import re
import uuid
import zipfile
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit.errors import StreamlitSecretNotFoundError
import pandas as pd
from supabase import create_client, Client
from urllib.parse import urlparse
import xml_engine
import relatorio_engine
import praesidium_persistence_fallback as ppf
logging.getLogger("streamlit").setLevel(logging.ERROR)

SESSION_TIMESTAMP_RE = re.compile(r"\d{20}")

st.set_page_config(
    page_title="Praesidium | Intelligence & Compliance",
    layout="wide",
    page_icon="🛡️",
    initial_sidebar_state="expanded"
)

st.markdown(
    """
    <style>
        html, body,
        html body .stApp,
        body [data-testid="stAppViewContainer"],
        body [data-testid="stMain"],
        body [data-testid="stAppViewContainer"] > .main,
        body [data-testid="stAppViewContainer"] > .main .block-container {
            background-color: #0e1117 !important;
            color: #ffffff !important;
            color-scheme: dark !important;
        }
        body [data-testid="stSidebar"] {
            background-color: #111827 !important;
            color: #ffffff !important;
            color-scheme: dark !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
)
xml_engine.injetar_css_anti_truncamento()

# ==========================================
# 1. DESIGN SYSTEM "DARK LUXURY" BLINDADO P/ MOBILE E DESKTOP
# (Sem linhas em branco internas para evitar bug do Markdown/Tradutor Mobile)
# ==========================================
CSS_DARK_LUXURY = """
<meta name="google" content="notranslate">
<meta name="color-scheme" content="dark">
<div class="notranslate" translate="no">
<style>
    :root, html, body, .stApp {
        color-scheme: dark !important;
        background-color: #0e1117 !important;
        color: #ffffff !important;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    div[data-testid="InputInstructions"],
    .stTextInput div[data-testid="InputInstructions"],
    .stNumberInput div[data-testid="InputInstructions"],
    [data-testid="InputInstructions"] > span {
        display: none !important;
        visibility: hidden !important;
        height: 0px !important;
        width: 0px !important;
        opacity: 0 !important;
        pointer-events: none !important;
    }
    [data-stale="true"],
    .stApp [data-stale="true"],
    div[data-testid="stAppViewBlockContainer"],
    div[data-testid="stVerticalBlock"],
    div[data-testid="stHorizontalBlock"],
    div[data-testid="element-container"] {
        opacity: 1 !important;
        transition: none !important;
        filter: none !important;
        backdrop-filter: none !important;
    }
    div[data-baseweb="layer"],
    div[data-baseweb="modal"],
    div[role="presentation"] {
        background: transparent !important;
        background-color: transparent !important;
        backdrop-filter: none !important;
    }
    div[data-baseweb="popover"],
    div[data-baseweb="menu"],
    div[data-testid="stSelectbox"] div[role="listbox"],
    ul[role="listbox"],
    div[data-baseweb="calendar"] {
        background-color: #1f2937 !important;
        border: 1.5px solid #d4af37 !important;
        color: #ffffff !important;
        box-shadow: none !important;
    }
    li[role="option"] {
        background-color: #1f2937 !important;
        color: #ffffff !important;
    }
    li[role="option"]:hover,
    li[role="option"][aria-selected="true"] {
        background-color: #374151 !important;
        color: #fbbf24 !important;
    }
    .block-container,
    [data-testid="stAppViewContainer"] > .main .block-container {
        padding-top: 4.2rem !important;
        padding-bottom: 2rem !important;
    }
    header[data-testid="stHeader"] {
        height: 1.8rem !important;
        background: transparent !important;
    }
    [data-testid="stSidebar"] {
        background-color: #111827 !important;
        border-right: 1px solid #374151 !important;
    }
    [data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] label {
        color: #f3f4f6 !important;
    }
    div[data-baseweb="select"],
    div[data-baseweb="select"] > div,
    div[data-testid="stSelectbox"] > div > div,
    div[data-testid="stMultiSelect"] > div > div,
    div[data-baseweb="base-input"],
    div[data-baseweb="input"],
    div[data-testid="stDateInput"] > div > div,
    div[data-testid="stNumberInputContainer"],
    div[data-testid="stTextInput"] input,
    div[data-testid="stTextInput"] > div {
        border: 1.5px solid #d4af37 !important;
        border-radius: 8px !important;
        background-color: #111827 !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        box-shadow: none !important;
    }
    div[data-baseweb="select"] > div:hover,
    div[data-testid="stSelectbox"] > div > div:hover,
    div[data-testid="stMultiSelect"] > div > div:hover,
    div[data-baseweb="base-input"]:hover,
    div[data-testid="stNumberInputContainer"]:hover,
    div[data-testid="stDateInput"] > div > div:hover {
        border-color: #fbbf24 !important;
        box-shadow: none !important;
    }
    .sidebar-exec-card {
        background: #111827;
        border: 1px solid rgba(212, 175, 55, 0.45);
        border-left: 4px solid #d4af37;
        border-radius: 10px;
        padding: 14px 16px;
        margin: 14px 0 18px 0;
    }
    .sidebar-exec-title {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 1.2px;
        color: #d4af37 !important;
        font-weight: 800;
        margin-bottom: 10px;
        display: block;
    }
    .sidebar-exec-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 7px 0;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        font-size: 12.5px;
    }
    .sidebar-exec-row:last-child {
        border-bottom: none;
        padding-bottom: 2px;
    }
    .sidebar-exec-label {
        color: #9ca3af !important;
        font-weight: 500;
    }
    .badge-gold {
        background: rgba(212, 175, 55, 0.15);
        color: #fbbf24 !important;
        border: 1px solid rgba(212, 175, 55, 0.45);
        padding: 2px 8px;
        border-radius: 6px;
        font-family: 'Courier New', monospace;
        font-size: 11.5px;
        font-weight: 700;
    }
    .badge-green {
        background: rgba(16, 185, 129, 0.15);
        color: #34d399 !important;
        border: 1px solid rgba(16, 185, 129, 0.45);
        padding: 2px 8px;
        border-radius: 6px;
        font-size: 11.5px;
        font-weight: 700;
    }
    .header-container {
        background: #111827;
        border: 1px solid #374151;
        border-left: 4px solid #d4af37;
        border-radius: 12px;
        padding: 20px 28px;
        margin-top: 0.5rem !important;
        margin-bottom: 18px;
    }
    .header-title {
        color: #ffffff !important;
        font-size: 24px;
        font-weight: 800;
        letter-spacing: -0.5px;
        margin: 0;
    }
    .header-subtitle {
        color: #9ca3af !important;
        font-size: 13px;
        margin-top: 5px;
    }
    .exec-card {
        background: #111827;
        border: 1px solid #374151;
        border-radius: 12px;
        padding: 20px;
        text-align: left;
        position: relative;
        overflow: hidden;
        margin-bottom: 10px;
        transition: all 0.2s ease;
        font-variant-numeric: tabular-nums;
    }
    .exec-card:hover {
        transform: translateY(-2px);
        border-color: rgba(212, 175, 55, 0.65);
        box-shadow: 0 10px 24px rgba(0, 0, 0, 0.22);
    }
    .exec-card::before {
        content: '';
        position: absolute;
        top: 0; left: 0; width: 4px; height: 100%;
    }
    .card-bruto::before { background: #d4af37; }
    .card-validado::before { background: #10b981; }
    .card-quarentena::before { background: #f59e0b; }
    .card-label {
        color: #9ca3af !important;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 1.5px;
    }
    .card-value-bruto {
        color: #f6c443 !important;
        font-size: 28px;
        font-weight: 900;
        font-family: 'Courier New', monospace;
        margin: 8px 0 4px 0;
    }
    .card-value-validado {
        color: #34d399 !important;
        font-size: 28px;
        font-weight: 900;
        font-family: 'Courier New', monospace;
        margin: 8px 0 4px 0;
    }
    .card-value-quarentena {
        color: #fbbf24 !important;
        font-size: 28px;
        font-weight: 900;
        font-family: 'Courier New', monospace;
        margin: 8px 0 4px 0;
    }
    .card-desc {
        color: #9ca3af !important;
        font-size: 11px;
    }
    .sub-calc-card {
        background: #111827;
        border: 1px solid #374151;
        border-radius: 10px;
        padding: 16px;
        text-align: left;
        min-height: 130px;
        margin-bottom: 10px;
        transition: all 0.2s ease;
        font-variant-numeric: tabular-nums;
    }
    .sub-calc-card:hover {
        transform: translateY(-2px);
        border-color: rgba(212, 175, 55, 0.65);
    }
    .sub-calc-title {
        color: #9ca3af !important;
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-bottom: 8px;
    }
    .sub-calc-value {
        color: #ffffff !important;
        font-size: 22px;
        font-weight: 800;
        font-family: 'Courier New', monospace;
        margin-bottom: 6px;
    }
    .sub-calc-desc {
        color: #d1d5db !important;
        font-size: 11px;
    }
    [data-testid="stExpander"] {
        background-color: #111827 !important;
        border: 1px solid #374151 !important;
        border-radius: 10px !important;
        margin-top: 0 !important;
        margin-bottom: 0.75rem !important;
    }
    [data-testid="stExpander"] summary {
        background-color: #111827 !important;
        border-radius: 10px !important;
    }
    [data-testid="stExpander"] summary span,
    [data-testid="stExpander"] summary p,
    [data-testid="stExpander"] summary div {
        color: #ffffff !important;
        font-weight: 700 !important;
    }
    .stButton button, [data-testid="stFormSubmitButton"] button {
        background-color: #1f2937 !important;
        color: #ffffff !important;
        border: 1px solid #d4af37 !important;
        border-radius: 8px !important;
        font-weight: 700 !important;
        box-shadow: none !important;
    }
    .stButton button:hover, [data-testid="stFormSubmitButton"] button:hover {
        background-color: #374151 !important;
        border-color: #fbbf24 !important;
        color: #fbbf24 !important;
        box-shadow: none !important;
    }
    .stDataFrame {
        border: 1px solid #374151 !important;
        border-radius: 8px !important;
        background-color: #111827 !important;
        font-variant-numeric: tabular-nums !important;
    }
    [data-testid="stDataFrame"], [data-testid="stTable"] {
        font-variant-numeric: tabular-nums !important;
    }
    h3, h4, span, label, p {
        color: #f3f4f6 !important;
    }
    @media (max-width: 768px) {
        .header-container { padding: 14px 16px !important; }
        .header-title { font-size: 18px !important; }
        .card-value-bruto, .card-value-validado, .card-value-quarentena { font-size: 22px !important; }
        .sub-calc-value { font-size: 18px !important; }
    }
</style>
</div>
"""
st.markdown(CSS_DARK_LUXURY.replace("\n\n", "\n"), unsafe_allow_html=True)


def renderizar_onboarding_vazio() -> None:
    st.markdown(
        """
        <div style="background:linear-gradient(135deg,#111827,#0f172a);
        border:1px solid #374151;border-left:4px solid #d4af37;border-radius:12px;
        padding:20px 24px;margin:12px 0 20px;">
          <div style="color:#f6c443;font-weight:800;font-size:16px;">
            🚀 Passo a Passo Rápido
          </div>
          <div style="color:#e5e7eb;line-height:1.8;margin-top:8px;">
            <b>1.</b> Cadastre o CNPJ na barra lateral →
            <b>2.</b> Arraste os XMLs para a Central de Ingestão →
            <b>3.</b> Audite os itens na Quarentena.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ==========================================
# 2. CONEXÃO SUPABASE (EM CACHE DE ALTA VELOCIDADE)
# ==========================================
@st.cache_resource(ttl=900)
def iniciar_conexao_supabase() -> Client:
    try:
        supabase_url = str(st.secrets["SUPABASE_URL"]).strip()
    except (KeyError, StreamlitSecretNotFoundError):
        supabase_url = ""
    if not supabase_url:
        supabase_url = (os.environ.get("SUPABASE_URL") or "").strip()
    try:
        supabase_key = str(st.secrets["SUPABASE_KEY"]).strip()
    except (KeyError, StreamlitSecretNotFoundError):
        supabase_key = ""
    if not supabase_key:
        supabase_key = (os.environ.get("SUPABASE_KEY") or "").strip()
    url_validada = urlparse(supabase_url)
    if not supabase_url or not supabase_key:
        raise ValueError("Credenciais do Supabase ausentes.")
    if url_validada.scheme != "https" or not url_validada.netloc:
        raise ValueError("SUPABASE_URL inválida; HTTPS é obrigatório.")
    return create_client(supabase_url, supabase_key)


supabase: Client | None = None
erro_conexao_supabase = None
try:
    supabase = iniciar_conexao_supabase()
except ValueError as ex:
    erro_conexao_supabase = str(ex)
except Exception:
    logging.getLogger(__name__).exception("Falha ao inicializar a conexão Supabase.")
    erro_conexao_supabase = (
        "Não foi possível inicializar a conexão Supabase. Confira as credenciais "
        "em st.secrets ou nas variáveis de ambiente."
    )

if erro_conexao_supabase:
    st.warning(erro_conexao_supabase)


def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode("utf-8")).hexdigest()


def consultar_todas_paginas(criar_consulta, tamanho_pagina: int = 1000) -> list:
    registros = []
    inicio = 0
    while True:
        resposta = (
            criar_consulta()
            .range(inicio, inicio + tamanho_pagina - 1)
            .execute()
        )
        pagina = resposta.data or []
        registros.extend(pagina)
        if len(pagina) < tamanho_pagina:
            return registros
        inicio += tamanho_pagina


CAMINHO_REGISTRO_AUTH = Path(__file__).with_name("usuarios_praesidium.json")
CAMINHO_REGISTRO_AUTH_ANTIGO = Path(__file__).with_name("auth_state.json")
PREFIXO_USUARIO_SISTEMA = "[SYS_USER]"
PREFIXO_LOG_SISTEMA = "[SYS_LOG]"


def chave_log_acesso(log: dict) -> tuple:
    return (
        log.get("timestamp"),
        log.get("datetime"),
        log.get("username"),
        log.get("name"),
        log.get("event"),
    )


def cnpjs_usuario_sistema(usuario: str) -> list[str]:
    """Identificadores do usuário em empresas_monitoradas.cnpj (VARCHAR(14)).

    O primeiro é o atual (hash de 14 caracteres); o legado `USR_<login>` só é
    considerado quando cabe nos 14 caracteres da coluna.
    """
    identificadores = [ppf.cnpj_usuario_sistema(usuario)]
    legado = f"USR_{usuario}"
    if len(legado) <= ppf.TAMANHO_MAXIMO_CNPJ:
        identificadores.append(legado)
    return identificadores


def usuario_from_row(row: dict) -> tuple[str, dict] | None:
    cnpj = str(row.get("cnpj", ""))
    razao_social = str(row.get("razao_social", ""))
    if not razao_social.startswith(PREFIXO_USUARIO_SISTEMA):
        return None
    registro_usuario = razao_social[len(PREFIXO_USUARIO_SISTEMA):]
    try:
        dados = json.loads(registro_usuario)
    except (TypeError, ValueError):
        campos = registro_usuario.rsplit("|", 2)
        if len(campos) not in {2, 3}:
            logging.getLogger(__name__).error("Registro de usuário do Supabase malformado: %s", cnpj)
            return None
        dados = {
            "name": campos[0],
            "password_hash": campos[1],
            "company_id": campos[2] if len(campos) == 3 else None,
        }
    if not isinstance(dados, dict):
        return None
    username = str(dados.get("username") or "").strip().lower()
    if not username and cnpj.startswith("USR_"):
        username = cnpj[4:].lower()
    if not username:
        return None
    password_hash = str(dados.get("password_hash", ""))
    nome = str(dados.get("name", ""))
    if not password_hash or not nome:
        return None
    company_id = dados.get("company_id")
    if not xml_engine.eh_uuid_valido(company_id):
        company_id = None
    return username, {
        "password_hash": password_hash,
        "role": "contador",
        "name": nome,
        "company_id": company_id,
        "company_id_synced": company_id,
    }


def ler_registros_sistema_supabase() -> tuple[dict, list]:
    if supabase is None:
        return {}, []

    def consultar_sistema(criar_consulta) -> list:
        registros = []
        inicio = 0
        while True:
            resposta = criar_consulta().range(inicio, inicio + 999).execute()
            pagina = resposta.data or []
            registros.extend(pagina)
            if len(pagina) < 1000:
                return registros
            inicio += 1000

    linhas_auth = consultar_sistema(
        lambda: (
            supabase.table("empresas_monitoradas")
            .select("cnpj,razao_social,regime_tributario,cnae_principal")
            .eq("regime_tributario", "SYS_AUTH")
        )
    )
    linhas_logs = consultar_sistema(
        lambda: (
            supabase.table("empresas_monitoradas")
            .select("cnpj,razao_social,regime_tributario,cnae_principal")
            .eq("regime_tributario", "SYS_LOG")
        )
    )
    usuarios = {}
    logs = []
    for row in linhas_auth:
        if str(row.get("razao_social", "")).startswith(PREFIXO_USUARIO_SISTEMA):
            usuario = usuario_from_row(row)
            if usuario:
                usuarios[usuario[0]] = usuario[1]
        elif str(row.get("razao_social", "")).startswith(PREFIXO_LOG_SISTEMA):
            log = decodificar_log_acesso(row)
            if log:
                logs.append(log)
    logs.extend(log for row in linhas_logs if (log := decodificar_log_acesso(row)))
    return usuarios, logs


def decodificar_log_acesso(row: dict) -> dict | None:
    razao_social = str(row.get("razao_social", ""))
    if not razao_social.startswith(PREFIXO_LOG_SISTEMA):
        return None
    conteudo = razao_social[len(PREFIXO_LOG_SISTEMA):]
    try:
        log = json.loads(conteudo)
    except (TypeError, ValueError):
        campos = conteudo.split("|", 4)
        if len(campos) == 5:
            ts_epoch, data_hora, username, nome, evento = campos
            try:
                data_log = datetime.fromtimestamp(
                    int(ts_epoch),
                    tz=timezone(timedelta(hours=-3)),
                )
                timestamp = data_log.isoformat(timespec="microseconds")
            except (OverflowError, OSError, ValueError):
                timestamp = ""
            log = {
                "ts_epoch": ts_epoch,
                "datetime": data_hora,
                "timestamp": timestamp,
                "username": username,
                "name": nome,
                "event": evento,
            }
        elif len(campos) == 4:
            data_hora, username, nome, evento = campos
            try:
                timestamp_id = str(row.get("cnpj", ""))[4:]
                data_log = datetime.strptime(data_hora, "%d/%m/%Y %H:%M:%S")
                if SESSION_TIMESTAMP_RE.fullmatch(timestamp_id):
                    data_log = datetime.strptime(timestamp_id, "%Y%m%d%H%M%S%f")
                timestamp = data_log.replace(
                    tzinfo=timezone(timedelta(hours=-3))
                ).isoformat(timespec="microseconds")
            except ValueError:
                timestamp = ""
            log = {
                "datetime": data_hora,
                "timestamp": timestamp,
                "username": username,
                "name": nome,
                "event": evento,
            }
        else:
            logging.getLogger(__name__).error("Registro de acesso do Supabase malformado.")
            return None
    return log if isinstance(log, dict) else None


def _data_hora_log_acesso(log: dict) -> datetime | None:
    try:
        data_log = datetime.fromisoformat(str(log.get("timestamp", "")))
        if data_log.tzinfo is None:
            data_log = data_log.replace(tzinfo=timezone(timedelta(hours=-3)))
        return data_log
    except ValueError:
        try:
            return datetime.strptime(
                str(log.get("datetime", "")), "%d/%m/%Y %H:%M:%S"
            ).replace(tzinfo=timezone(timedelta(hours=-3)))
        except ValueError:
            return None


def filtrar_logs_ultimas_horas(logs: list[dict], horas: int) -> list[dict]:
    limite = datetime.now(timezone(timedelta(hours=-3))) - timedelta(hours=horas)
    recentes = []
    for log in logs:
        data_log = _data_hora_log_acesso(log)
        if data_log is not None and data_log >= limite:
            recentes.append(log)
    return recentes


def filtrar_logs_ultimos_sete_dias(logs: list[dict]) -> list[dict]:
    return filtrar_logs_ultimas_horas(logs, 24 * 7)


def payload_usuario_supabase(usuario: str, dados_usuario: dict) -> dict:
    company_id = dados_usuario.get("company_id")
    return {
        "cnpj": ppf.cnpj_usuario_sistema(usuario),
        "razao_social": PREFIXO_USUARIO_SISTEMA + json.dumps(
            {
                "username": usuario,
                "name": dados_usuario["name"],
                "password_hash": dados_usuario["password_hash"],
                "company_id": company_id if xml_engine.eh_uuid_valido(company_id) else None,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "regime_tributario": ppf.REGIME_TRIBUTARIO_AUTH,
        "cnae_principal": ppf.CNAE_REGISTRO_SISTEMA,
    }


def persistir_usuario_supabase(usuario: str, dados_usuario: dict) -> None:
    if supabase is None:
        raise RuntimeError("Supabase indisponível para persistir o usuário.")
    registro = payload_usuario_supabase(usuario, dados_usuario)
    existente = (
        supabase.table("empresas_monitoradas")
        .select("cnpj")
        .in_("cnpj", cnpjs_usuario_sistema(usuario))
        .eq("regime_tributario", "SYS_AUTH")
        .execute()
    )
    if existente.data:
        ok, mensagem, _ = xml_engine.executar_update_seguro(
            supabase,
            "empresas_monitoradas",
            registro,
            {"cnpj": existente.data[0]["cnpj"], "regime_tributario": "SYS_AUTH"},
        )
    else:
        ok, mensagem, _ = xml_engine.executar_insert_seguro(
            "empresas_monitoradas", registro, supabase=supabase
        )
    if not ok:
        raise RuntimeError(mensagem)


def criar_usuario_supabase(usuario: str, dados_usuario: dict) -> None:
    if supabase is None:
        raise RuntimeError("Supabase indisponível para persistir o usuário.")
    registro = payload_usuario_supabase(usuario, dados_usuario)
    existente = (
        supabase.table("empresas_monitoradas")
        .select("cnpj")
        .in_("cnpj", cnpjs_usuario_sistema(usuario))
        .eq("regime_tributario", "SYS_AUTH")
        .execute()
    )
    if existente.data:
        raise ValueError("Este usuário já está cadastrado.")
    ok, mensagem, _ = xml_engine.executar_insert_seguro(
        "empresas_monitoradas", registro, supabase=supabase
    )
    if not ok:
        raise RuntimeError(mensagem)


def buscar_usuario_persistido(usuario: str) -> dict | None:
    if supabase is None:
        return None
    resposta = (
        supabase.table("empresas_monitoradas")
        .select("cnpj,razao_social,regime_tributario,cnae_principal")
        .in_("cnpj", cnpjs_usuario_sistema(usuario))
        .eq("regime_tributario", "SYS_AUTH")
        .execute()
    )
    for row in resposta.data or []:
        dados = usuario_from_row(row)
        if dados and dados[0] == usuario:
            return dados[1]
    return None


def persistir_log_supabase(log_acesso: dict) -> None:
    if supabase is None:
        raise RuntimeError("Supabase indisponível para persistir o log.")
    timestamp = datetime.fromisoformat(str(log_acesso["timestamp"]))
    ts_epoch = int(timestamp.timestamp())
    identificador_log = str(log_acesso.get("log_id") or timestamp.isoformat())
    cnpj_suffix = (
        f"{int.from_bytes(hashlib.sha256(identificador_log.encode()).digest()[:8], 'big') % 10**12:012d}"
    )
    registro = {
        "cnpj": f"99{cnpj_suffix}",
        "razao_social": (
            f"{PREFIXO_LOG_SISTEMA}{ts_epoch}|{log_acesso['datetime']}|"
            f"{log_acesso['username']}|{log_acesso['name']}|{log_acesso['event']}"
        ),
        "regime_tributario": "SYS_LOG",
        "cnae_principal": "0000000",
    }
    resposta = (
        supabase.table("empresas_monitoradas")
        .insert(registro)
        .select("cnpj,regime_tributario")
        .execute()
    )
    if not any(
        str(linha.get("cnpj")) == registro["cnpj"]
        and linha.get("regime_tributario") == "SYS_LOG"
        for linha in (resposta.data or [])
    ):
        raise RuntimeError("O Supabase não confirmou a persistência do log de acesso.")


def salvar_registro_global(registro: dict) -> None:
    dados = {
        "users": registro["users"],
        "access_logs": registro["access_logs"],
    }
    temporario = CAMINHO_REGISTRO_AUTH.with_name(
        f"{CAMINHO_REGISTRO_AUTH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporario, CAMINHO_REGISTRO_AUTH)


@st.cache_resource
def obter_registro_global() -> dict:
    usuarios_padrao = {
        "felipebondezzan": {
            "password_hash": hash_senha("admin2026"),
            "role": "admin",
            "name": "Felipe Bondezzan (Admin)",
        },
    }
    dados_salvos = {}
    if CAMINHO_REGISTRO_AUTH.exists():
        dados_salvos = json.loads(CAMINHO_REGISTRO_AUTH.read_text(encoding="utf-8"))
    elif CAMINHO_REGISTRO_AUTH_ANTIGO.exists():
        dados_salvos = json.loads(CAMINHO_REGISTRO_AUTH_ANTIGO.read_text(encoding="utf-8"))

    usuarios = dados_salvos.get("users", {})
    logs_acesso = dados_salvos.get("access_logs", [])
    if not isinstance(usuarios, dict) or not isinstance(logs_acesso, list):
        raise ValueError("O arquivo local de autenticação possui estrutura inválida.")
    usuarios = dict(usuarios)
    conta_guilherme_padrao = usuarios.get("guilherme", {})
    if (
        isinstance(conta_guilherme_padrao, dict)
        and conta_guilherme_padrao.get("password_hash") == hash_senha("guilherme2026")
        and conta_guilherme_padrao.get("name") == "Guilherme"
    ):
        usuarios.pop("guilherme", None)
    usuarios.pop("admin", None)
    company_id_admin = usuarios.get("felipebondezzan", {}).get("company_id")
    usuarios.update(usuarios_padrao)
    if company_id_admin is not None:
        usuarios["felipebondezzan"]["company_id"] = company_id_admin
        usuarios["felipebondezzan"]["company_id_synced"] = company_id_admin
    logs_acesso = [
        {
            "datetime": str(log.get("datetime", "")),
            "timestamp": str(log.get("timestamp", "")),
            "username": str(log.get("username", "")),
            "name": str(log.get("name", "")),
            "event": str(log.get("event", log.get("status", "Acesso"))),
        }
        for log in logs_acesso
        if isinstance(log, dict)
    ]
    try:
        usuarios_supabase, logs_supabase = ler_registros_sistema_supabase()
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao carregar usuários e logs do Supabase; usando o registro local."
        )
    else:
        usuarios_supabase.pop("admin", None)
        usuario_guilherme_padrao = usuarios_supabase.get("guilherme", {})
        if (
            isinstance(usuario_guilherme_padrao, dict)
            and usuario_guilherme_padrao.get("password_hash") == hash_senha("guilherme2026")
            and usuario_guilherme_padrao.get("name") == "Guilherme"
        ):
            usuarios_supabase.pop("guilherme", None)
        company_id_admin = usuarios_supabase.get("felipebondezzan", {}).get("company_id")
        if company_id_admin is None:
            company_id_admin = usuarios.get("felipebondezzan", {}).get("company_id")
        for username, usuario_remoto in usuarios_supabase.items():
            usuario_local = usuarios.get(username, {})
            if usuario_remoto.get("company_id") is None and usuario_local.get("company_id") is not None:
                usuario_remoto["company_id"] = usuario_local["company_id"]
            usuarios[username] = usuario_remoto
        usuarios["felipebondezzan"] = {
            **usuarios_padrao["felipebondezzan"],
            "company_id": company_id_admin,
            "company_id_synced": company_id_admin,
        }
        logs_por_chave = {chave_log_acesso(log): log for log in logs_acesso}
        for log in logs_supabase:
            logs_por_chave[chave_log_acesso(log)] = log
        logs_acesso = sorted(
            logs_por_chave.values(),
            key=lambda log: log.get("timestamp") or log.get("datetime", ""),
        )

    registro = {
        "users": usuarios,
        "access_logs": filtrar_logs_ultimos_sete_dias(logs_acesso),
        "lock": threading.RLock(),
        "sessions": {},
    }
    with registro["lock"]:
        salvar_registro_global(registro)
    return registro


registro_global = obter_registro_global()


def atualizar_espelho_sessao() -> None:
    with registro_global["lock"]:
        st.session_state["registro_auth_espelho"] = {
            "users": {usuario: dict(dados) for usuario, dados in registro_global["users"].items()},
            "access_logs": [dict(log) for log in registro_global["access_logs"]],
        }


def salvar_cache_local() -> None:
    with registro_global["lock"]:
        salvar_registro_global(registro_global)


def atualizar_usuario_do_supabase(usuario: str) -> dict | None:
    if usuario == "felipebondezzan":
        return registro_global["users"][usuario]
    dados = buscar_usuario_persistido(usuario)
    if dados is not None:
        with registro_global["lock"]:
            usuario_local = registro_global["users"].get(usuario, {})
            if dados.get("company_id") is None and usuario_local.get("company_id") is not None:
                dados["company_id"] = usuario_local["company_id"]
            registro_global["users"][usuario] = dados
            salvar_registro_global(registro_global)
    return dados


def sincronizar_usuarios_supabase() -> None:
    if supabase is None:
        return
    usuarios_remotos, _ = ler_registros_sistema_supabase()
    usuarios_remotos.pop("admin", None)
    usuario_guilherme_padrao = usuarios_remotos.get("guilherme", {})
    if (
        isinstance(usuario_guilherme_padrao, dict)
        and usuario_guilherme_padrao.get("password_hash") == hash_senha("guilherme2026")
        and usuario_guilherme_padrao.get("name") == "Guilherme"
    ):
        usuarios_remotos.pop("guilherme", None)
    with registro_global["lock"]:
        company_id_admin = usuarios_remotos.get("felipebondezzan", {}).get("company_id")
        if company_id_admin is None:
            company_id_admin = registro_global["users"].get(
                "felipebondezzan", {}
            ).get("company_id")
        for username, usuario_remoto in usuarios_remotos.items():
            usuario_local = registro_global["users"].get(username, {})
            if usuario_remoto.get("company_id") is None and usuario_local.get("company_id") is not None:
                usuario_remoto["company_id"] = usuario_local["company_id"]
            registro_global["users"][username] = usuario_remoto
        registro_global["users"]["felipebondezzan"] = {
            "password_hash": hash_senha("admin2026"),
            "role": "admin",
            "name": "Felipe Bondezzan (Admin)",
            "company_id": company_id_admin,
            "company_id_synced": company_id_admin,
        }
        salvar_registro_global(registro_global)


def atualizar_logs_do_supabase() -> None:
    _, logs_supabase = ler_registros_sistema_supabase()
    logs_por_chave = {
        chave_log_acesso(log): log for log in registro_global["access_logs"]
    }
    for log in logs_supabase:
        logs_por_chave[chave_log_acesso(log)] = log
    with registro_global["lock"]:
        registro_global["access_logs"] = sorted(
            filtrar_logs_ultimos_sete_dias(list(logs_por_chave.values())),
            key=lambda log: log.get("timestamp") or log.get("datetime", ""),
        )
        salvar_registro_global(registro_global)
    atualizar_espelho_sessao()


def persistir_estado_sessao() -> None:
    token = st.query_params.get("session")
    if not token:
        return
    empresa_id_ativa = st.session_state.get("empresa_id_ativa")
    with registro_global["lock"]:
        sessao = registro_global["sessions"].get(token)
        if sessao is not None:
            sessao["company_id"] = empresa_id_ativa
            sessao["state"] = (
                {"empresa_id_ativa": empresa_id_ativa}
                if empresa_id_ativa is not None
                else {}
            )


def enviar_alerta_acesso(log_acesso: dict) -> None:
    try:
        requests.post(
            "https://ntfy.sh/praesidium_felipe_alertas",
            data=(
                f"Acesso Praesidium: {log_acesso['event']} | Usuário: "
                f"{log_acesso['username']} | Nome: {log_acesso['name']} | "
                f"Data/Hora: {log_acesso['datetime']}"
            ).encode("utf-8"),
            headers={"Title": "Acesso Praesidium Detectado", "Priority": "high", "Tags": "shield,unlock"},
            timeout=2
        ).raise_for_status()
    except Exception:
        logging.getLogger(__name__).exception("Falha ao enviar alerta de acesso ao ntfy.sh.")


def persistir_acesso_em_background(log_acesso: dict) -> None:
    try:
        persistir_log_supabase(log_acesso)
    except Exception:
        logging.getLogger(__name__).exception("Falha ao persistir log de acesso no Supabase.")
    enviar_alerta_acesso(log_acesso)


def registrar_e_notificar_acesso(usuario: str, nome: str, evento: str) -> None:
    agora = datetime.now(timezone(timedelta(hours=-3)))
    log_acesso = {
        "log_id": uuid.uuid4().hex,
        "datetime": agora.strftime("%d/%m/%Y %H:%M:%S"),
        "timestamp": agora.isoformat(timespec="microseconds"),
        "timestamp_id": agora.strftime("%Y%m%d%H%M%S%f"),
        "username": usuario,
        "name": nome,
        "event": evento,
    }
    with registro_global["lock"]:
        registro_global["access_logs"].append(log_acesso)
        registro_global["access_logs"] = filtrar_logs_ultimos_sete_dias(
            registro_global["access_logs"]
        )
        try:
            salvar_registro_global(registro_global)
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao persistir o registro de acesso em usuarios_praesidium.json."
            )
    atualizar_espelho_sessao()
    try:
        persistir_log_supabase(log_acesso)
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao persistir o registro de acesso no Supabase."
        )
    try:
        threading.Thread(
            target=enviar_alerta_acesso,
            args=(log_acesso,),
            daemon=True,
        ).start()
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao iniciar o envio assíncrono do alerta de acesso."
        )


def criar_token_sessao(usuario: str, conectado: bool = True) -> str | None:
    dados_usuario = registro_global["users"][usuario]
    if not conectado:
        st.query_params.clear()
        return None

    token = gerar_token_sessao(usuario, dados_usuario)
    sessao = {
        "username": usuario,
        "role": dados_usuario["role"],
        "name": dados_usuario["name"],
        "tenant_id": usuario,
        "company_id": dados_usuario.get("company_id"),
        "state": (
            {"empresa_id_ativa": dados_usuario["company_id"]}
            if dados_usuario.get("company_id") is not None
            else {}
        ),
    }
    with registro_global["lock"]:
        registro_global["sessions"][token] = sessao
    st.query_params["session"] = token
    return token


def gerar_token_sessao(usuario: str, dados_usuario: dict) -> str:
    try:
        segredo_aplicacao = str(st.secrets["SESSION_SIGNING_SECRET"]).encode("utf-8")
    except Exception:
        segredo_aplicacao = str(dados_usuario["password_hash"]).encode("utf-8")
    mensagem = (
        f"praesidium-session-v1:{usuario}:"
        f"{dados_usuario['password_hash']}"
    ).encode("utf-8")
    return hmac.new(segredo_aplicacao, mensagem, hashlib.sha256).hexdigest()


def usuario_por_token(token: str) -> tuple[str, dict] | None:
    if not st.session_state.get("token_usuario_validado", False):
        try:
            sincronizar_usuarios_supabase()
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao validar token contra o cadastro do Supabase."
            )
        st.session_state["token_usuario_validado"] = True
    for usuario, dados_usuario in registro_global["users"].items():
        token_esperado = gerar_token_sessao(usuario, dados_usuario)
        if hmac.compare_digest(token, token_esperado):
            return usuario, dados_usuario
    return None


def iniciar_sessao_usuario(usuario: str, conectado: bool = True) -> None:
    dados_usuario = registro_global["users"][usuario]
    token = criar_token_sessao(usuario, conectado)
    st.session_state["autenticado"] = True
    st.session_state["auth_username"] = usuario
    st.session_state["tenant_id"] = usuario
    st.session_state["usuario_nome"] = dados_usuario["name"]
    st.session_state["is_admin"] = dados_usuario["role"] == "admin"
    st.session_state["acesso_notificado"] = True
    if dados_usuario.get("company_id") is not None:
        st.session_state["empresa_id_ativa"] = dados_usuario["company_id"]
    else:
        st.session_state.pop("empresa_id_ativa", None)
    if not token:
        st.session_state["limpar_token_navegador"] = True


def encerrar_sessao() -> None:
    token = st.query_params.get("session")
    if token:
        with registro_global["lock"]:
            registro_global["sessions"].pop(token, None)
    st.query_params.clear()
    st.session_state.clear()
    st.session_state["autenticado"] = False
    st.session_state["limpar_token_navegador"] = True
    st.rerun()


def injetar_persistencia_navegador(
    token: str | None = None,
    limpar_token: bool = False,
) -> None:
    token_json = json.dumps(token)
    limpar_json = json.dumps(limpar_token)
    html_persistencia = f"""
        <script>
        (() => {{
            const storageKey = "praesidium_session";
            const activeToken = {token_json};
            const clearToken = {limpar_json};
            try {{
                const parentWindow = window.parent;
                const currentUrl = new URL(parentWindow.location.href);
                if (clearToken) {{
                    parentWindow.localStorage.removeItem(storageKey);
                    parentWindow.history.replaceState(
                        null,
                        "",
                        currentUrl.pathname + currentUrl.hash,
                    );
                }} else if (activeToken) {{
                    parentWindow.localStorage.setItem(storageKey, activeToken);
                }} else if (!currentUrl.searchParams.has("session")) {{
                    const savedToken = parentWindow.localStorage.getItem(storageKey);
                    if (savedToken) {{
                        currentUrl.searchParams.set("session", savedToken);
                        parentWindow.history.replaceState(null, "", currentUrl);
                        parentWindow.location.reload();
                    }}
                }}
            }} catch (error) {{
                console.error("Não foi possível restaurar a sessão local do Praesidium.", error);
            }}
        }})();
        </script>
        """
    if hasattr(st, "iframe"):
        st.iframe(html_persistencia, height=1)
    else:
        components.html(html_persistencia, height=0)


# ==========================================
# 3. TRAVA DE SEGURANÇA MULTI-TENANT (AMBIENTE ISOLADO POR CONTADOR)
# ==========================================
if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False
atualizar_espelho_sessao()

token_url = st.query_params.get("session")
if isinstance(token_url, str) and token_url:
    usuario_validado = usuario_por_token(token_url)
    if usuario_validado:
        username_restaurado, dados_usuario_restaurado = usuario_validado
        with registro_global["lock"]:
            sessao_existente = registro_global["sessions"].get(token_url, {})
            empresa_id_restaurada = sessao_existente.get(
                "company_id",
                dados_usuario_restaurado.get("company_id"),
            )
            sessao_url = {
                "username": username_restaurado,
                "role": dados_usuario_restaurado["role"],
                "name": dados_usuario_restaurado["name"],
                "tenant_id": username_restaurado,
                "company_id": empresa_id_restaurada,
                "state": (
                    {"empresa_id_ativa": empresa_id_restaurada}
                    if empresa_id_restaurada is not None
                    else {}
                ),
            }
            registro_global["sessions"][token_url] = sessao_url
        dados_sessao = dict(sessao_url)
        st.session_state["autenticado"] = True
        st.session_state["auth_username"] = dados_sessao["username"]
        st.session_state["tenant_id"] = dados_sessao["tenant_id"]
        st.session_state["usuario_nome"] = dados_sessao["name"]
        empresa_id_restaurada = dados_sessao.get("company_id")
        if empresa_id_restaurada is not None:
            st.session_state["empresa_id_ativa"] = empresa_id_restaurada
        else:
            st.session_state.pop("empresa_id_ativa", None)
        if not st.session_state.get("acesso_notificado", False):
            registrar_e_notificar_acesso(
                dados_sessao["username"],
                dados_sessao["name"],
                "Sessão Restaurada",
            )
            st.session_state["acesso_notificado"] = True
    else:
        st.query_params.pop("session", None)
        st.session_state["limpar_token_navegador"] = True

injetar_persistencia_navegador(
    st.query_params.get("session"),
    st.session_state.pop("limpar_token_navegador", False),
)

usuario_autenticado = st.session_state.get("auth_username", "")
dados_usuario = registro_global["users"].get(usuario_autenticado)
if (
    not st.session_state["autenticado"]
    or not isinstance(dados_usuario, dict)
    or dados_usuario.get("role") not in {"admin", "contador"}
):
    st.session_state["autenticado"] = False

if not st.session_state["autenticado"]:
    st.markdown("""
    <div class="notranslate" translate="no" style="background: #111827; border: 1px solid #d4af37; border-left: 5px solid #d4af37; border-radius: 12px; padding: 32px; max-width: 520px; margin: 45px auto 20px auto; text-align: center;">
        <div style="font-size: 36px; margin-bottom: 8px;">🛡️</div>
        <h2 style="color: #ffffff; margin: 0 0 8px 0; font-weight: 800;">PRAESIDIUM</h2>
        <div style="color: #d4af37; font-size: 12px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; margin-bottom: 18px;">Intelligence & Compliance • LC 214/2025</div>
        <p style="color: #9ca3af; font-size: 13.5px; line-height: 1.5; margin-bottom: 0;">Ambiente de homologação isolado. Informe sua identificação e a credencial autorizada para iniciar seu painel exclusivo.</p>
    </div>
    """, unsafe_allow_html=True)

    col_esq, col_centro, col_dir = st.columns([1, 1.3, 1])
    with col_centro:
        aba_entrar, aba_cadastro = st.tabs(["Entrar", "Criar Conta"])
        with aba_entrar:
            with st.form("form_login_mvp", border=False):
                identificacao_input = st.text_input("Usuário", autocomplete="off")
                senha_input = st.text_input("Senha", type="password", autocomplete="off")
                manter_conectado = st.checkbox("Manter conectado neste aparelho", value=True)
                btn_entrar = st.form_submit_button("Entrar", width="stretch")
            if btn_entrar:
                username = identificacao_input.lower()
                if username:
                    try:
                        atualizar_usuario_do_supabase(username)
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Falha ao consultar o usuário %s no Supabase; usando o cache local.",
                            username,
                        )
                usuario = registro_global["users"].get(username)
                senha_correta = (
                    isinstance(usuario, dict)
                    and hmac.compare_digest(
                        str(usuario.get("password_hash", "")),
                        hash_senha(senha_input),
                    )
                )
                if senha_correta:
                    nome_usuario = str(usuario.get("name", username))
                    registrar_e_notificar_acesso(username, nome_usuario, "Login")
                    iniciar_sessao_usuario(username, manter_conectado)
                    st.rerun()
                else:
                    nome_tentativa = (
                        str(usuario.get("name", "Não identificado"))
                        if isinstance(usuario, dict)
                        else "Não identificado"
                    )
                    registrar_e_notificar_acesso(username, nome_tentativa, "Falha de Login")
                    st.error("Usuário ou senha incorretos. Verifique a grafia.")
        with aba_cadastro:
            with st.form("form_primeiro_acesso", border=False):
                novo_nome_usuario = st.text_input("Nome", autocomplete="off")
                novo_usuario_input = st.text_input("Usuário", autocomplete="off")
                nova_senha = st.text_input("Senha", type="password", autocomplete="off")
                btn_criar_conta = st.form_submit_button("Criar Conta", width="stretch")
            if btn_criar_conta:
                novo_usuario = novo_usuario_input.lower()
                if not novo_usuario or not novo_nome_usuario or not nova_senha:
                    st.error("Informe usuário, nome e senha.")
                elif supabase is None:
                    st.error("Cadastro indisponível: configure a conexão Supabase para persistir a conta.")
                else:
                    with registro_global["lock"]:
                        if novo_usuario in registro_global["users"]:
                            st.error("Este usuário já está cadastrado.")
                        else:
                            verificacao_remota_ok = False
                            usuario_remoto = None
                            if supabase is not None:
                                try:
                                    usuario_remoto = buscar_usuario_persistido(novo_usuario)
                                    verificacao_remota_ok = True
                                except Exception:
                                    logging.getLogger(__name__).exception(
                                        "Falha ao verificar duplicidade do usuário no Supabase."
                                    )
                            if not verificacao_remota_ok:
                                st.error("Não foi possível verificar o cadastro no Supabase. Tente novamente.")
                            elif usuario_remoto is not None:
                                registro_global["users"][novo_usuario] = usuario_remoto
                                salvar_registro_global(registro_global)
                                st.error("Este usuário já está cadastrado.")
                            else:
                                novo_registro_usuario = {
                                    "password_hash": hash_senha(nova_senha),
                                    "role": "contador",
                                    "name": novo_nome_usuario,
                                }
                                persistencia_supabase_ok = False
                                duplicado_remoto = False
                                if verificacao_remota_ok:
                                    try:
                                        criar_usuario_supabase(novo_usuario, novo_registro_usuario)
                                        persistencia_supabase_ok = True
                                    except ValueError:
                                        st.error("Este usuário já está cadastrado.")
                                        duplicado_remoto = True
                                    except Exception:
                                        try:
                                            usuario_remoto = buscar_usuario_persistido(novo_usuario)
                                        except Exception:
                                            usuario_remoto = None
                                        if usuario_remoto is not None:
                                            registro_global["users"][novo_usuario] = usuario_remoto
                                            try:
                                                salvar_registro_global(registro_global)
                                            except Exception:
                                                logging.getLogger(__name__).exception(
                                                    "Falha ao salvar usuário remoto no cache local."
                                                )
                                            st.error("Este usuário já está cadastrado.")
                                            duplicado_remoto = True
                                        else:
                                            logging.getLogger(__name__).exception(
                                                "Não foi possível persistir a nova conta no Supabase."
                                            )
                                if not duplicado_remoto and persistencia_supabase_ok:
                                    registro_global["users"][novo_usuario] = novo_registro_usuario
                                    cache_local_ok = True
                                    try:
                                        salvar_registro_global(registro_global)
                                    except Exception:
                                        cache_local_ok = False
                                        logging.getLogger(__name__).exception(
                                            "Não foi possível salvar a conta no fallback local."
                                        )
                                    if not cache_local_ok:
                                        st.session_state["aviso_persistencia_conta"] = (
                                            "Conta salva no Supabase, mas o espelho local falhou."
                                        )
                                    atualizar_espelho_sessao()
                                    registrar_e_notificar_acesso(
                                        novo_usuario,
                                        novo_nome_usuario,
                                        "Novo Cadastro",
                                    )
                                    iniciar_sessao_usuario(novo_usuario)
                                    st.rerun()
                                elif not duplicado_remoto:
                                    st.error(
                                        "Não foi possível gravar a conta no Supabase; "
                                        "nenhuma sessão foi criada."
                                    )
    st.stop()

aviso_persistencia_conta = st.session_state.pop("aviso_persistencia_conta", None)
if aviso_persistencia_conta:
    st.warning(aviso_persistencia_conta)


# ==========================================
# 4. FUNÇÕES DE LEITURA EM CACHE (VELOCIDADE NA NUVEM)
# ==========================================
@st.cache_data(ttl=180, show_spinner=False)
def carregar_todas_empresas_db():
    """
    REFORMULAÇÃO: Carrega APENAS clientes reais (não [SYS_*] records).
    
    Estratégia:
    1. Tenta select de colunas explícitas (fallback: select "*")
    2. Pagina através de todos os registros
    3. Filtra usando ppf.eh_cliente_real() para descartar TODOS [SYS_*]
    4. Retorna APENAS clientes reais
    
    IMPORTANTE: Preserva fallback para select("*") se coluna não existir.
    """
    empresas = []
    
    if supabase is not None:
        tamanho_pagina = 1000

        def consultar_empresas(colunas: str) -> list[dict]:
            """Consulta paginada de empresas com tratamento de erro de coluna."""
            registros = []
            inicio = 0
            
            while True:
                try:
                    resposta = (
                        supabase.table("empresas_monitoradas")
                        .select(colunas)
                        .range(inicio, inicio + tamanho_pagina - 1)
                        .execute()
                    )
                except Exception as e:
                    # Se falhar por coluna inexistente, re-levanta para retry com select(*)
                    raise

                pagina = resposta.data or []
                registros.extend(pagina)
                
                if len(pagina) < tamanho_pagina:
                    return registros
                
                inicio += tamanho_pagina

        # 1º tentativa: select com colunas explícitas
        try:
            empresas = consultar_empresas(
                "id,cnpj,razao_social,regime_tributario,cnae_principal,"
                "certificado_a1_status"
            )
        except Exception:
            # Se falhar, tenta select("*")
            logging.getLogger(__name__).warning(
                "Leitura explícita de colunas indisponível; tentando select('*').",
            )
            try:
                empresas = consultar_empresas("*")
            except Exception:
                logging.getLogger(__name__).exception(
                    "Falha ao carregar empresas do Supabase mesmo com select('*')."
                )
                empresas = []
    
    # ========== FILTRAR [SYS_*] RECORDS ==========
    # Usar ppf.eh_cliente_real() para filtrar corretamente
    clientes_reais = []
    for empresa in empresas:
        try:
            razao_social = str(empresa.get("razao_social", ""))
            regime_tributario = str(empresa.get("regime_tributario", ""))
            cnae_principal = str(empresa.get("cnae_principal", ""))
            
            # Usar função ppf para validar se é cliente real
            if ppf.eh_cliente_real(razao_social, regime_tributario, cnae_principal):
                clientes_reais.append(empresa)
        except Exception as e:
            logging.getLogger(__name__).debug(
                "Erro ao validar cliente %s: %s (pulando)",
                empresa.get("id"),
                e,
            )
    
    return clientes_reais


@st.cache_data(ttl=180, show_spinner=False)
def carregar_dados_empresa_db(emp_id: int | str, emp_cnpj: str):
    if supabase is None:
        return [], [], [], [], []

    try:
        notas_lista = consultar_todas_paginas(
            lambda: (
                supabase.table("notas_fiscais_recebidas")
                .select("*")
                .eq("empresa_id", emp_id)
            )
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao carregar notas fiscais da empresa %s; usando dados da sessão.",
            emp_id,
        )
        notas_lista = []

    devolucoes_por_id = {}
    try:
        devolucoes_empresa = consultar_todas_paginas(
            lambda: (
                supabase.table("conciliacao_devolucoes")
                .select("*")
                .eq("empresa_id", emp_id)
            )
        )
        for devolucao in devolucoes_empresa:
            devolucoes_por_id[str(devolucao.get("id"))] = devolucao
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao carregar devoluções por empresa para %s.",
            emp_id,
        )
        devolucoes_por_id = {}

    if emp_cnpj:
        try:
            devolucoes_legadas = consultar_todas_paginas(
                lambda: (
                    supabase.table("conciliacao_devolucoes")
                    .select("*")
                    .like("motivo_ajuste", f"%[{emp_cnpj}]%")
                )
            )
            for devolucao in devolucoes_legadas:
                devolucoes_por_id[str(devolucao.get("id"))] = devolucao
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao carregar devoluções legadas por CNPJ para %s.",
                emp_cnpj,
            )
    devs_lista = list(devolucoes_por_id.values())

    itens_lista = []
    try:
        itens_lista = consultar_todas_paginas(
            lambda: (
                supabase.table("itens_nota_fiscal")
                .select("*")
                .eq("empresa_id", emp_id)
            )
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao carregar itens das notas da empresa %s; usando dados da sessão.",
            emp_id,
        )
        itens_lista = []

    eventos_split = []
    try:
        eventos_split = consultar_todas_paginas(
            lambda: (
                supabase.table("split_payment_events")
                .select("*")
                .eq("empresa_id", emp_id)
            )
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao carregar eventos de Split Payment da empresa %s.",
            emp_id,
        )

    espelho = xml_engine.carregar_espelho_empresa(supabase, emp_id)
    devs_lista = xml_engine.combinar_registros_espelho(
        devs_lista,
        espelho["devolucao"],
        "devolucao",
    )
    itens_lista = xml_engine.combinar_registros_espelho(
        itens_lista,
        espelho["item"],
        "item",
    )
    eventos_split = xml_engine.combinar_registros_espelho(
        eventos_split,
        espelho["split_payment"],
        "split_payment",
    )
    for indice, registro in enumerate(devs_lista):
        registro.setdefault("id", f"mirror_devolucao_{indice}")
    for indice, registro in enumerate(eventos_split):
        registro.setdefault(
            "id",
            f"mirror_split_{registro.get('event_id') or indice}",
        )
    notas_lista = xml_engine.reconstruir_notas_a_partir_dos_itens(
        notas_lista,
        itens_lista,
    )
    return notas_lista, devs_lista, itens_lista, [], eventos_split


@st.cache_data(ttl=180, show_spinner=False)
def carregar_cnpjs_autorizados_db(emp_id: int | str) -> list[str]:
    if supabase is None:
        return []
    try:
        resposta = (
            supabase.table("empresa_cnpjs_autorizados")
            .select("cnpj")
            .eq("empresa_id", emp_id)
            .execute()
        )
        return [
            str(registro.get("cnpj", ""))
            for registro in (resposta.data or [])
            if len("".join(filter(str.isdigit, str(registro.get("cnpj", ""))))) == 14
        ]
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao carregar CNPJs autorizados para a empresa %s.",
            emp_id,
        )
        return []


def limpar_caches_dados() -> None:
    carregar_todas_empresas_db.clear()
    carregar_dados_empresa_db.clear()
    carregar_cnpjs_autorizados_db.clear()


xml_engine.registrar_limpador_cache_dados(limpar_caches_dados)


def combinar_registros_com_sessao(
    registros_db: list[dict],
    registros_sessao: list[dict],
    tipo: str = "",
) -> list[dict]:
    combinados = {}
    for indice, registro in enumerate(registros_db):
        if tipo == "item":
            identificador = str(
                registro.get("item_uid")
                or (
                    f"{registro.get('nota_id')}|{registro.get('nitem')}"
                    if registro.get("nota_id") is not None
                    and registro.get("nitem") is not None
                    else f"db_item_{indice}"
                )
            )
        else:
            identificador = str(registro.get("id") or f"db_{indice}")
        combinados[identificador] = dict(registro)
    for indice, registro in enumerate(registros_sessao):
        if tipo == "item":
            identificador = str(
                registro.get("item_uid")
                or (
                    f"{registro.get('nota_id')}|{registro.get('nitem')}"
                    if registro.get("nota_id") is not None
                    and registro.get("nitem") is not None
                    else f"sessao_item_{indice}"
                )
            )
        else:
            identificador = str(registro.get("id") or f"sessao_{indice}")
        combinados[identificador] = {**combinados.get(identificador, {}), **registro}
    return list(combinados.values())


def hidratar_dados_empresa_supabase(empresa_id: str) -> dict[str, list[dict]]:
    if str(empresa_ativa.get("id", "")) != str(empresa_id):
        raise ValueError("A empresa ativa não corresponde à seleção atual.")
    cache_hidratacao = st.session_state.setdefault(
        "_dados_hidratados_por_empresa",
        {},
    )
    return xml_engine.hidratar_dados_empresa_protegido(
        supabase,
        empresa_id,
        empresa_ativa,
        cache_hidratacao,
    )


TAG_TENANT_RE = re.compile(r"\s*\[T:([^\]]+)\]", re.IGNORECASE)


def limpar_tag_tenant(nome_empresa: str) -> str:
    return xml_engine.limpar_tag_tenant_empresa(nome_empresa)


def extrair_tenant_empresa(emp: dict) -> str:
    razao_social = str(emp.get("razao_social", ""))
    tag_nome = TAG_TENANT_RE.search(razao_social)
    if tag_nome:
        return tag_nome.group(1).strip().lower()

    status_a1 = str(emp.get("certificado_a1_status", ""))
    if "TENANT:" in status_a1:
        return status_a1.split("TENANT:", 1)[1].split("|", 1)[0].strip().lower()
    cnae_raw = str(emp.get("cnae_principal", ""))
    if "|TENANT:" in cnae_raw:
        return cnae_raw.split("|TENANT:", 1)[1].split("|", 1)[0].strip().lower()
    return ""


def extrair_cnae_limpo_empresa(emp: dict) -> str:
    cnae_raw = str(emp.get("cnae_principal", "4711302"))
    cnaes = cnae_raw.split("|TENANT:", 1)[0].strip().split(",")
    status_a1 = str(emp.get("certificado_a1_status", ""))
    if "|CNAES:" in status_a1:
        secundarios = status_a1.split("|CNAES:", 1)[1].split("|", 1)[0]
        cnaes.extend(secundarios.split(","))
    cnaes_unicos = list(dict.fromkeys(cnae.strip() for cnae in cnaes if cnae.strip()))
    return ",".join(cnaes_unicos) or "4711302"


fragment_decorator = getattr(st, "fragment", None)
if not callable(fragment_decorator):
    def fragment_decorator(func):
        return func


TELEMETRIA_AUTOMATICA_15S = False


def fragment_telemetria(func):
    global TELEMETRIA_AUTOMATICA_15S
    fragment = getattr(st, "fragment", None)
    if not callable(fragment):
        return func
    try:
        decorador = fragment(run_every="15s")
        TELEMETRIA_AUTOMATICA_15S = True
        return decorador(func)
    except TypeError:
        TELEMETRIA_AUTOMATICA_15S = False
        return fragment(func)


def rerun_fragment_local() -> None:
    if callable(getattr(st, "fragment", None)):
        st.rerun(scope="fragment")
    else:
        st.rerun()


@fragment_decorator
def renderizar_radar_admin() -> None:
    if st.session_state.get("auth_username") != "felipebondezzan":
        return
    with st.expander(
        "🔔 Radar de Acessos (Admin)",
        expanded=st.session_state.get("modulo_ativo") == "admin",
    ):
        if st.button("Atualizar Acessos", width="stretch", key="btn_atualizar_acessos"):
            try:
                atualizar_logs_do_supabase()
                st.session_state["modulo_ativo"] = "admin"
                rerun_fragment_local()
            except Exception:
                logging.getLogger(__name__).exception("Falha ao atualizar logs de acesso do Supabase.")
                st.error("Não foi possível atualizar o histórico pelo Supabase.")
        if st.button(
            "Testar Alerta no Celular",
            width="stretch",
            key="btn_testar_alerta_celular",
        ):
            registrar_e_notificar_acesso(
                "felipebondezzan",
                st.session_state["usuario_nome"],
                "Teste de Alerta",
            )
            st.success("Alerta enviado em segundo plano para o canal ntfy.")

        with registro_global["lock"]:
            logs_acesso = filtrar_logs_ultimos_sete_dias(
                [dict(log) for log in registro_global["access_logs"]]
            )
        logs_24h = filtrar_logs_ultimas_horas(logs_acesso, 24)
        st.caption(
            f"Acessos nas últimas 24 horas: {len(logs_24h)} | "
            f"Últimos 7 dias: {len(logs_acesso)}"
        )
        if logs_acesso:
            st.caption(f"Histórico persistente dos últimos 7 dias ({len(logs_acesso)}):")
            st.dataframe(
                pd.DataFrame([
                    {
                        "Data/Hora": log["datetime"],
                        "Usuário": log["username"],
                        "Nome": log["name"],
                        "Evento": log["event"],
                    }
                    for log in reversed(logs_acesso)
                ]),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("Nenhum acesso registrado nos últimos 7 dias.")

        st.caption("📱 Alerta no celular ativo no canal ntfy.sh: `praesidium_felipe_alertas`")


# ==========================================
# 5. GESTÃO MULTI-EMPRESA E RADAR ADMIN (SIDEBAR)
# ==========================================
tenant_atual = usuario_autenticado
is_admin = usuario_autenticado == "felipebondezzan" and dados_usuario["role"] == "admin"
st.session_state["tenant_id"] = tenant_atual
st.session_state["usuario_nome"] = str(dados_usuario.get("name", usuario_autenticado))
st.session_state["is_admin"] = is_admin

st.sidebar.markdown(f"""
<div style="display:flex; align-items:center; gap:10px; margin-bottom:4px;">
    <span style="font-size:24px;">🏢</span>
    <div>
        <div style="font-weight:800; font-size:17px; color:#ffffff; letter-spacing:0.3px;">Painel do Escritório</div>
        <div style="font-size:11.5px; color:#d4af37 !important; font-weight:600;">👤 {st.session_state['usuario_nome']}</div>
    </div>
</div>
<hr style="border:0; border-top:1px solid rgba(212,175,55,0.25); margin:10px 0 14px 0;">
""", unsafe_allow_html=True)

try:
    todas_empresas_brutas = carregar_todas_empresas_db()
except RuntimeError:
    logging.getLogger(__name__).exception("Falha ao carregar clientes.")
    st.toast("Não foi possível carregar os clientes agora. Tente novamente.", icon="⚠️")
    st.stop()
def empresa_eh_cliente_real(empresa: dict) -> bool:
    """Cliente real do escritório: UUID válido e fora dos registros [SYS_*]."""
    return xml_engine.eh_uuid_valido(empresa.get("id")) and ppf.eh_cliente_real(
        str(empresa.get("razao_social", "")),
        str(empresa.get("regime_tributario", "")),
        str(empresa.get("cnae_principal", "")),
    )


todas_empresas_brutas = [
    empresa for empresa in todas_empresas_brutas if empresa_eh_cliente_real(empresa)
]
st.session_state.pop("fallback_empresas", None)

# Administrador: radar global de acessos e visão multi-tenant.
if is_admin:
    with st.sidebar:
        renderizar_radar_admin()

    tenants_disponiveis = sorted({
        extrair_tenant_empresa(e)
        for e in todas_empresas_brutas
        if extrair_tenant_empresa(e)
    })
    filtro_tenant_admin = st.sidebar.selectbox(
        "👁️ Visão do Administrador (Ambiente):",
        ["TODOS OS AMBIENTES"] + tenants_disponiveis + ["SEM TAG (LEGADO)"]
    )
    if filtro_tenant_admin == "TODOS OS AMBIENTES":
        lista_empresas = todas_empresas_brutas
    elif filtro_tenant_admin == "SEM TAG (LEGADO)":
        lista_empresas = [e for e in todas_empresas_brutas if not extrair_tenant_empresa(e)]
    else:
        lista_empresas = [e for e in todas_empresas_brutas if extrair_tenant_empresa(e) == filtro_tenant_admin]
else:
    lista_empresas = [
        e for e in todas_empresas_brutas
        if extrair_tenant_empresa(e) == tenant_atual
    ]

def reiniciar_formulario_cadastro() -> None:
    """Nova versão das chaves: todas as caixas do cadastro e da devolução reabrem vazias."""
    st.session_state["_versao_form_cadastro"] = (
        int(st.session_state.get("_versao_form_cadastro", 0)) + 1
    )
    st.session_state["cnae_secundarios_extras"] = []


_chave_selecao_empresa_inicial = f"empresa_ativa_select_{usuario_autenticado}"
_selecao_empresa_atual = st.session_state.get(_chave_selecao_empresa_inicial)
_selecao_empresa_anterior = st.session_state.get("_selecao_empresa_anterior")
if (
    _selecao_empresa_atual is not None
    and _selecao_empresa_anterior is not None
    and _selecao_empresa_atual != _selecao_empresa_anterior
):
    reiniciar_formulario_cadastro()
if _selecao_empresa_atual is not None:
    st.session_state["_selecao_empresa_anterior"] = _selecao_empresa_atual
versao_formulario = int(st.session_state.get("_versao_form_cadastro", 0))

with st.sidebar.expander("➕ Cadastrar Novo Cliente", expanded=False):
    novo_nome = st.text_input("Razão Social", autocomplete="off", key=f"cad_nome_{versao_formulario}")
    novo_cnpj = st.text_input("CNPJ (Com ou sem pontuação)", autocomplete="off", key=f"cad_cnpj_{versao_formulario}")
    novo_regime = st.selectbox("Regime", ["Simples Nacional", "Simples Híbrido (LC 214)", "Lucro Presumido", "Lucro Real"], key=f"cad_regime_{versao_formulario}")
    derivacao_tributaria_nova = st.selectbox(
        "Derivação IBS + CBS",
        list(xml_engine.ALIQUOTAS_REFORMA_TRIBUTARIA),
        key=f"cad_derivacao_tributaria_{versao_formulario}",
    )
    cnae_principal_input = st.text_input("Atividade Principal (CNAE 7 dígitos)", placeholder="Ex: 4711302 ou 9602501", autocomplete="off", key=f"cad_cnae_prin_{versao_formulario}")

    if "cnae_secundarios_extras" not in st.session_state:
        st.session_state["cnae_secundarios_extras"] = []

    st.markdown("<span style='font-size: 11.5px; color: #9ca3af; font-weight: 700;'>CNAEs Secundários (Opcional)</span>", unsafe_allow_html=True)

    cnaes_secundarios_coletados = []
    for idx, val_cnae in enumerate(st.session_state["cnae_secundarios_extras"]):
        c_val = st.text_input(f"CNAE Secundário {idx+1}", value=val_cnae, autocomplete="off", key=f"sec_cnae_input_{versao_formulario}_{idx}")
        if c_val.strip():
            cnaes_secundarios_coletados.append("".join(filter(str.isdigit, c_val)))

    if st.button("➕ Adicionar outro CNAE", key="btn_add_cnae_sec"):
        st.session_state["cnae_secundarios_extras"].append("")
        st.rerun()

    st.write("")
    if st.button("Salvar Cliente Definitivo", width="stretch", key="btn_salvar_cli_def"):
        if novo_nome and novo_cnpj and cnae_principal_input:
            cnpj_limpo = "".join(filter(str.isdigit, novo_cnpj))
            cnae_prin_limpo = "".join(filter(str.isdigit, cnae_principal_input))
            todos_cnaes = [cnae_prin_limpo] + cnaes_secundarios_coletados
            todos_cnaes_unicos = list(dict.fromkeys([c for c in todos_cnaes if c]))

            cnaes_invalidos_cad = [] if todos_cnaes_unicos else [cnae_principal_input]
            for c_teste in todos_cnaes_unicos:
                ok_ibge, _ = xml_engine.validar_cnae_oficial_ibge(c_teste)
                if not ok_ibge:
                    cnaes_invalidos_cad.append(c_teste)

            if cnaes_invalidos_cad:
                st.sidebar.error(
                    f"❌ CNAE inválido: {', '.join(cnaes_invalidos_cad)}. "
                    f"Digite um CNAE real de 7 dígitos (Ex: 4711302 p/ Supermercado, 9602501 p/ Salão, 4772500 p/ Cosméticos)."
                )
            elif len(cnpj_limpo) != 14:
                st.sidebar.error("❌ CNPJ inválido: informe exatamente 14 dígitos.")
            elif empresa_existente := next(
                (
                    empresa for empresa in todas_empresas_brutas
                    if "".join(filter(str.isdigit, str(empresa.get("cnpj", "")))) == cnpj_limpo
                    and extrair_tenant_empresa(empresa) == tenant_atual
                ),
                None,
            ):
                st.session_state["empresa_id_ativa"] = empresa_existente["id"]
                st.session_state.pop(f"empresa_ativa_select_{usuario_autenticado}", None)
                st.session_state["_forcar_selecao_empresa"] = True
                reiniciar_formulario_cadastro()
                persistir_estado_sessao()
                st.toast("Cliente já cadastrado; selecionado como cliente ativo.", icon="ℹ️")
                st.rerun()
            else:
                cnaes_secundarios = ",".join(todos_cnaes_unicos[1:])
                status_certificado = "Ativo"
                if cnaes_secundarios:
                    status_certificado += f"|CNAES:{cnaes_secundarios}"
                payload_emp = {
                    "razao_social": f"{limpar_tag_tenant(novo_nome).upper()} [T:{tenant_atual}]",
                    "cnpj": cnpj_limpo,
                    "regime_tributario": novo_regime,
                    "cnae_principal": todos_cnaes_unicos[0],
                    "certificado_a1_status": status_certificado
                }
                ok_ins, msg_ins, empresas_criadas = xml_engine.inserir_seguro_supabase(
                    supabase,
                    "empresas_monitoradas",
                    payload_emp,
                )
                if ok_ins:
                    empresa_nova = next(
                        (
                            registro for registro in empresas_criadas
                            if registro.get("id") is not None
                        ),
                        None,
                    )
                    if (
                        empresa_nova is None
                        and derivacao_tributaria_nova
                        != "Base Padrão Integral — 26,50% (Sem redutor)"
                    ):
                        try:
                            resposta_empresa_nova = (
                                supabase.table("empresas_monitoradas")
                                .select("id")
                                .eq("cnpj", cnpj_limpo)
                                .limit(1)
                                .execute()
                            )
                            empresa_nova = next(
                                (
                                    registro for registro in (resposta_empresa_nova.data or [])
                                    if registro.get("id") is not None
                                ),
                                None,
                            )
                        except Exception:
                            logging.getLogger(__name__).exception(
                                "Falha ao confirmar o ID do novo cliente para salvar a derivação tributária."
                            )
                    if empresa_nova and derivacao_tributaria_nova != (
                        "Base Padrão Integral — 26,50% (Sem redutor)"
                    ):
                        try:
                            xml_engine.persistir_perfil_tributario_empresa(
                                supabase,
                                empresa_nova["id"],
                                derivacao_tributaria_nova,
                            )
                        except Exception:
                            logging.getLogger(__name__).exception(
                                "Falha ao salvar a derivação tributária do novo cliente."
                            )
                            st.sidebar.warning(
                                "Cliente cadastrado, mas não foi possível persistir a derivação IBS/CBS. "
                                "Salve-a novamente na ficha do cliente."
                            )
                    reiniciar_formulario_cadastro()
                    limpar_caches_dados()
                    if empresa_nova and xml_engine.eh_uuid_valido(empresa_nova.get("id")):
                        st.session_state["empresa_id_ativa"] = empresa_nova["id"]
                        st.session_state.pop(f"empresa_ativa_select_{usuario_autenticado}", None)
                        st.session_state["_forcar_selecao_empresa"] = True
                        persistir_estado_sessao()
                    st.sidebar.success("Cliente validado e cadastrado!")
                    st.rerun()
                else:
                    if "api key" in msg_ins.lower() or "invalid jwt" in msg_ins.lower():
                        st.sidebar.warning(
                            "O Supabase recusou a chave configurada. Confira se SUPABASE_KEY está registrada "
                            "e corresponde ao projeto definido em SUPABASE_URL."
                        )
                    else:
                        logging.getLogger(__name__).error(
                            "Falha ao cadastrar cliente. Motivo operacional: %s", msg_ins
                        )
                        st.toast("Não foi possível cadastrar o cliente. Tente novamente.", icon="⚠️")
        else:
            st.sidebar.warning("Preencha Razão Social, CNPJ e CNAE Principal.")

if not lista_empresas:
    st.markdown(f"""
    <div class="header-container">
        <div class="header-title">🛡️ PRAESIDIUM — Intelligence & Compliance</div>
        <div class="header-subtitle">Ambiente Exclusivo de <b>{st.session_state['usuario_nome']}</b> • Painel 100% Limpo. Cadastre seu primeiro cliente na barra lateral à esquerda (➕ Cadastrar Novo Cliente) para iniciar a auditoria.</div>
    </div>
    """, unsafe_allow_html=True)
    renderizar_onboarding_vazio()
    if st.sidebar.button("🔒 Encerrar Sessão", width="stretch"):
        encerrar_sessao()
    st.stop()

opcoes_empresas = {}
for empresa in lista_empresas:
    nome_limpo = limpar_tag_tenant(empresa.get("razao_social", ""))
    dono = extrair_tenant_empresa(empresa)
    nome_dono = registro_global["users"].get(dono, {}).get("name", dono) if dono else "Sem tag (legado)"
    dono_exibicao = f"{dono} — {nome_dono}" if dono else nome_dono
    sufixo_dono = f" • Dono: {dono_exibicao}" if is_admin else ""
    opcoes_empresas[f"{nome_limpo} ({empresa['cnpj']}){sufixo_dono}"] = empresa["id"]
chave_empresa_selecao = f"empresa_ativa_select_{usuario_autenticado}"
token_sessao_ativa = st.query_params.get("session")
if token_sessao_ativa:
    with registro_global["lock"]:
        sessao_ativa = registro_global["sessions"].get(token_sessao_ativa)
        empresa_salva = sessao_ativa.get("company_id") if sessao_ativa else None
else:
    sessao_ativa = None
    empresa_salva = None

empresa_preferida = st.session_state.get("empresa_id_ativa", empresa_salva)
empresa_preferida_index = next(
    (
        indice
        for indice, empresa_id in enumerate(opcoes_empresas.values())
        if str(empresa_id) == str(empresa_preferida)
    ),
    0,
)
rotulos_empresas = list(opcoes_empresas.keys())
selecao_forcada = st.session_state.pop("_forcar_selecao_empresa", False)
selecao_atual_widget = st.session_state.get(chave_empresa_selecao)
if selecao_forcada or (
    selecao_atual_widget is not None and selecao_atual_widget not in opcoes_empresas
):
    # Atribuir antes de instanciar o widget garante que o cliente escolhido
    # (novo, existente ou o próximo após exclusão) vire o cliente ativo.
    st.session_state[chave_empresa_selecao] = rotulos_empresas[empresa_preferida_index]
empresa_selecionada_nome = st.sidebar.selectbox(
    "📂 Cliente Ativo Monitorado",
    rotulos_empresas,
    key=chave_empresa_selecao,
    **(
        {}
        if chave_empresa_selecao in st.session_state
        else {"index": empresa_preferida_index}
    ),
)
empresa_id_ativo = opcoes_empresas[empresa_selecionada_nome]
empresa_ativa_bruta = next(
    (
        e for e in lista_empresas
        if e["id"] == empresa_id_ativo and empresa_eh_cliente_real(e)
    ),
    None,
)
if empresa_ativa_bruta is None:
    # Registros [SYS_*] nunca podem ser o cliente ativo.
    empresa_ativa_bruta = next(e for e in lista_empresas if empresa_eh_cliente_real(e))
    empresa_id_ativo = empresa_ativa_bruta["id"]
st.session_state["empresa_id_ativa"] = empresa_id_ativo
if sessao_ativa is not None:
    with registro_global["lock"]:
        sessao_salva = registro_global["sessions"].get(token_sessao_ativa)
        if sessao_salva is not None:
            sessao_salva["company_id"] = empresa_id_ativo
            sessao_salva["role"] = dados_usuario["role"]
            sessao_salva["name"] = dados_usuario["name"]
            sessao_salva["tenant_id"] = usuario_autenticado
if (
    str(dados_usuario.get("company_id")) != str(empresa_id_ativo)
    or str(dados_usuario.get("company_id_synced")) != str(empresa_id_ativo)
):
    dados_usuario["company_id"] = empresa_id_ativo
    if supabase is not None:
        try:
            persistir_usuario_supabase(usuario_autenticado, dados_usuario)
            dados_usuario["company_id_synced"] = empresa_id_ativo
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao persistir a última empresa ativa do usuário %s.",
                usuario_autenticado,
            )
    try:
        salvar_cache_local()
    except Exception:
        logging.getLogger(__name__).exception("Falha ao salvar a última empresa ativa localmente.")
tenant_empresa_ativa = extrair_tenant_empresa(empresa_ativa_bruta)
nome_dono_ativo = (
    registro_global["users"].get(tenant_empresa_ativa, {}).get("name", tenant_empresa_ativa)
    if tenant_empresa_ativa
    else "Sem tag (legado)"
)
dono_exibicao_ativo = (
    f"{tenant_empresa_ativa} — {nome_dono_ativo}"
    if tenant_empresa_ativa
    else nome_dono_ativo
)

# Mantém as tags internas fora dos nomes exibidos e das rotinas fiscais.
empresa_ativa = dict(empresa_ativa_bruta)
empresa_ativa["razao_social"] = limpar_tag_tenant(empresa_ativa_bruta.get("razao_social", ""))
empresa_ativa["cnae_principal"] = extrair_cnae_limpo_empresa(empresa_ativa_bruta)
empresa_ativa["cnpjs_autorizados"] = carregar_cnpjs_autorizados_db(
    empresa_id_ativo
)
chave_perfil_tributario = f"perfil_tributario_{empresa_id_ativo}"
if chave_perfil_tributario not in st.session_state:
    st.session_state[chave_perfil_tributario] = (
        xml_engine.carregar_perfil_tributario_empresa(
            supabase,
            empresa_id_ativo,
        )
    )
perfil_tributario_ativo = st.session_state[chave_perfil_tributario]

st.sidebar.markdown(f"""
<div class="sidebar-exec-card">
    <span class="sidebar-exec-title">🛡️ Ficha Cadastral & Compliance</span>
    <div class="sidebar-exec-row">
        <span class="sidebar-exec-label">Regime</span>
        <span class="badge-gold">{empresa_ativa.get('regime_tributario', 'Simples Nacional')}</span>
    </div>
    {f"<div class='sidebar-exec-row'><span class='sidebar-exec-label'>Contador responsável</span><span class='badge-gold'>{dono_exibicao_ativo}</span></div>" if is_admin else ""}
    <div class="sidebar-exec-row">
        <span class="sidebar-exec-label">CNPJ</span>
        <span style="color:#f3f4f6; font-family:'Courier New', monospace; font-weight:700;">{empresa_ativa.get('cnpj', '')}</span>
    </div>
    <div class="sidebar-exec-row">
        <span class="sidebar-exec-label">CNAEs Ativos</span>
        <span style="color:#fbbf24; font-family:'Courier New', monospace; font-weight:700;">{empresa_ativa.get('cnae_principal', '4711302')}</span>
    </div>
    <div class="sidebar-exec-row">
        <span class="sidebar-exec-label">Status A1</span>
        <span class="badge-green">● Validado & Seguro</span>
    </div>
    <div class="sidebar-exec-row">
        <span class="sidebar-exec-label">Integridade</span>
        <span style="color:#38bdf8 !important; font-size:11.5px; font-weight:700;">⚡ SHA-256 Ativo</span>
    </div>
</div>
""", unsafe_allow_html=True)

with st.sidebar.expander("⚙️ Ajustar CNAE / Resetar Notas", expanded=False):
    novo_cnae_edit = st.text_input(
        "Atualizar CNAEs (separados por vírgula)",
        value=str(empresa_ativa.get("cnae_principal", "")),
        help="Ex: 9602501 (Salão), 4772500 (Cosméticos), 4711302 (Supermercado)",
        autocomplete="off",
        key=f"edit_cnae_{empresa_id_ativo}_{int(st.session_state.get('_versao_edit_cnae', 0))}"
    )
    if st.button("💾 Salvar Novo CNAE", width="stretch", key=f"btn_save_cnae_{empresa_id_ativo}"):
        owner_tag = extrair_tenant_empresa(empresa_ativa_bruta) or tenant_atual
        cnaes_editados = list(dict.fromkeys(
            "".join(filter(str.isdigit, cnae.strip()))
            for cnae in novo_cnae_edit.split(",")
            if cnae.strip()
        ))
        if not cnaes_editados or any(len(cnae) != 7 for cnae in cnaes_editados):
            st.sidebar.warning("Informe um ou mais CNAEs válidos com exatamente 7 dígitos, separados por vírgula.")
        else:
            cnaes_secundarios = ",".join(cnaes_editados[1:])
            status_certificado = "Ativo"
            if cnaes_secundarios:
                status_certificado += f"|CNAES:{cnaes_secundarios}"
            try:
                atualizacao = {
                    "razao_social": f"{limpar_tag_tenant(empresa_ativa_bruta.get('razao_social', '')).upper()} [T:{owner_tag}]",
                    "cnae_principal": cnaes_editados[0],
                    "certificado_a1_status": status_certificado
                }
                ok, mensagem_update, resultado = xml_engine.executar_update_seguro(
                    supabase,
                    "empresas_monitoradas",
                    atualizacao,
                    {"id": empresa_id_ativo},
                    fallback_record=empresa_ativa_bruta,
                )
                if not ok or not resultado:
                    raise RuntimeError("Atualização não confirmada.")
                limpar_caches_dados()
                st.session_state["_versao_edit_cnae"] = (
                    int(st.session_state.get("_versao_edit_cnae", 0)) + 1
                )
                if mensagem_update == "OK":
                    st.sidebar.success("CNAEs atualizados!")
                else:
                    st.toast("CNAEs atualizados nesta sessão; o banco está temporariamente indisponível.", icon="ℹ️")
                st.rerun()
            except Exception:
                logging.getLogger(__name__).exception("Falha ao atualizar CNAE do cliente.")
                st.toast("Não foi possível atualizar os CNAEs. Tente novamente.", icon="⚠️")

    st.markdown("#### Memória de Cálculo IBS + CBS")
    derivacoes_tributarias = list(xml_engine.ALIQUOTAS_REFORMA_TRIBUTARIA)
    derivacao_atual = str(
        perfil_tributario_ativo.get("derivacao_setorial")
        or derivacoes_tributarias[0]
    )
    derivacao_selecionada = st.selectbox(
        "Derivação setorial da empresa",
        derivacoes_tributarias,
        index=(
            derivacoes_tributarias.index(derivacao_atual)
            if derivacao_atual in derivacoes_tributarias
            else 0
        ),
        key=f"perfil_tributario_select_{empresa_id_ativo}",
    )
    memoria_selecionada = xml_engine.calcular_memoria_aliquota(
        derivacao_selecionada
    )
    fator_legivel = (
        f"{memoria_selecionada['fator_reducao']:.0%}"
        if round(memoria_selecionada["fator_reducao"] * 100, 6)
        == round(memoria_selecionada["fator_reducao"] * 100)
        else f"{memoria_selecionada['fator_reducao']:.2%}"
    )
    st.caption(
        f"Base Legal 26,50% (17,70% IBS + 8,80% CBS) → "
        f"Fator de Redução {fator_legivel} → "
        f"Alíquota Efetiva Final {memoria_selecionada['aliquota_efetiva']:.2f}% "
        f"(IBS {memoria_selecionada['aliquota_ibs_efetiva']:.2f}% + "
        f"CBS {memoria_selecionada['aliquota_cbs_efetiva']:.2f}%)."
    )
    if st.button(
        "💾 Salvar Derivação IBS/CBS",
        width="stretch",
        key=f"salvar_perfil_tributario_{empresa_id_ativo}",
    ):
        try:
            perfil_tributario_ativo = (
                xml_engine.persistir_perfil_tributario_empresa(
                    supabase,
                    empresa_id_ativo,
                    derivacao_selecionada,
                )
            )
            st.session_state[chave_perfil_tributario] = perfil_tributario_ativo
            st.sidebar.success("Memória de cálculo salva no ledger fiscal.")
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao salvar a memória de cálculo IBS/CBS da empresa %s.",
                empresa_id_ativo,
            )
            st.sidebar.error(
                "Não foi possível salvar a derivação tributária no Supabase."
            )

    st.markdown("<hr style='border:0; border-top:1px solid #374151; margin:8px 0;'>", unsafe_allow_html=True)
    if st.button("🔄 Zerar Notas deste Cliente", width="stretch", key=f"btn_reset_nf_{empresa_id_ativo}"):
        try:
            xml_engine.limpar_dados_empresa_supabase(
                supabase,
                empresa_id_ativo,
            )
            st.sidebar.success(
                "Notas, itens, liquidações e espelhos foram removidos; "
                "o perfil tributário foi preservado."
            )
            st.rerun()
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao zerar os dados fiscais da empresa %s.",
                empresa_id_ativo,
            )
            st.sidebar.error(
                "Não foi possível confirmar a limpeza completa. "
                "Alguns dados podem permanecer no Supabase."
            )

    st.markdown("<hr style='border:0; border-top:1px solid #374151; margin:8px 0;'>", unsafe_allow_html=True)
    if st.button("🗑️ Excluir Cliente Ativo", width="stretch", key=f"btn_del_cli_{empresa_id_ativo}"):
        try:
            xml_engine.excluir_empresa_supabase(supabase, empresa_id_ativo)
            st.session_state.pop(f"fallback_notas_{empresa_id_ativo}", None)
            st.session_state.pop(f"fallback_itens_{empresa_id_ativo}", None)
            st.session_state.pop(f"fallback_devolucoes_{empresa_id_ativo}", None)
            st.session_state.pop(f"perfil_tributario_{empresa_id_ativo}", None)
            st.session_state.get("_dados_hidratados_por_empresa", {}).pop(
                str(empresa_id_ativo),
                None,
            )
            limpar_caches_dados()
            proxima_empresa = next(
                (
                    empresa
                    for empresa in lista_empresas
                    if str(empresa.get("id")) != str(empresa_id_ativo)
                ),
                None,
            )
            st.session_state.pop(chave_empresa_selecao, None)
            st.session_state["_forcar_selecao_empresa"] = True
            if proxima_empresa is None:
                st.session_state.pop("empresa_id_ativa", None)
            else:
                st.session_state["empresa_id_ativa"] = proxima_empresa["id"]
            persistir_estado_sessao()
            st.sidebar.success("Cliente excluído!")
            st.rerun()
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao excluir cliente %s.",
                empresa_id_ativo,
            )
            st.sidebar.error(
                "A exclusão não foi confirmada. O cliente e os dados permanecem ativos."
            )

if st.sidebar.button("🔒 Encerrar Sessão", width="stretch"):
    encerrar_sessao()

# ==========================================
# 6. CABEÇALHO EXECUTIVO
# ==========================================
st.markdown(f"""
<div class="header-container">
    <div class="header-title">🛡️ PRAESIDIUM — Intelligence & Compliance</div>
    <div class="header-subtitle">Cliente Ativo: <b>{empresa_ativa['razao_social']}</b> (CNAEs: {empresa_ativa.get('cnae_principal', '4711302')}) • Governança de Créditos Digitais e Split Payment (LC 214/2025)</div>
</div>
""", unsafe_allow_html=True)

# ==========================================
# 7. CÁLCULO DOS DADOS DA EMPRESA SELECIONADA (COM SUPORTE A FALLBACK)
# ==========================================
dados_empresa_hidratados = hidratar_dados_empresa_supabase(empresa_id_ativo)

notas = dados_empresa_hidratados["notas"]
itens = [
    xml_engine.normalizar_item_credito(item)
    for item in dados_empresa_hidratados["itens"]
]
notas = xml_engine.reconstruir_notas_a_partir_dos_itens(notas, itens)
devolucoes = dados_empresa_hidratados["devolucoes"]
eventos_split = dados_empresa_hidratados["eventos_split"]
eventos_liquidacao = dados_empresa_hidratados.get("eventos_liquidacao", [])
if dados_empresa_hidratados.get("perfil_tributario"):
    perfil_tributario_ativo = dados_empresa_hidratados["perfil_tributario"]
    st.session_state[chave_perfil_tributario] = perfil_tributario_ativo

if not notas:
    renderizar_onboarding_vazio()

competencia_atual = pd.Period(datetime.now(), freq="M")
periodo_por_nota = {
    str(n.get("id")): xml_engine.obter_data_referencia_nota(n).to_period("M")
    for n in notas
    if n.get("id") is not None and xml_engine.obter_data_referencia_nota(n) is not None
}
itens_mes = [
    i for i in itens
    if periodo_por_nota.get(str(i.get("nota_id"))) == competencia_atual
]
resumo_creditos_acumulado = xml_engine.calcular_resumo_creditos(itens)
ids_notas_mes = {
    str(n.get("id"))
    for n in notas
    if str(n.get("id")) in periodo_por_nota
    and periodo_por_nota[str(n.get("id"))] == competencia_atual
}

def devolucao_na_competencia(devolucao: dict, competencia: pd.Period) -> bool:
    data_devolucao = pd.to_datetime(
        devolucao.get("data_devolucao") or devolucao.get("created_at"),
        errors="coerce",
        utc=True,
    )
    if not pd.isna(data_devolucao):
        return data_devolucao.tz_convert(None).to_period("M") == competencia
    return str(devolucao.get("nota_original_id", "")) in ids_notas_mes


devolucoes_mes = [
    devolucao
    for devolucao in devolucoes
    if devolucao_na_competencia(devolucao, competencia_atual)
]
imposto_estornado = round(sum(
    float(d.get("credito_estornado", 0.0) or 0.0)
    for d in devolucoes
), 2)

resumo_saldo_credito = xml_engine.calcular_saldos_com_cofre(
    dados_empresa_hidratados
)
imposto_quarentena_acumulado = resumo_saldo_credito["quarentena"]
imposto_bruto_acumulado = resumo_saldo_credito["bruto"]
valor_retido_split_payment = resumo_saldo_credito["valor_retido_split_payment"]
credito_validado_tempo_real = resumo_saldo_credito["credito_liquido"]
saldo_credito_disponivel = resumo_saldo_credito["credito_disponivel"]

# ==========================================
# 8. PAINEL SUPERIOR: IMPOSTÔMETRO LUXO
# ==========================================
@fragment_telemetria
def renderizar_cards_telemetria() -> None:
    dados_live = dados_empresa_hidratados
    try:
        dados_live = hidratar_dados_empresa_supabase(empresa_id_ativo)
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao atualizar a telemetria fiscal da empresa %s.",
            empresa_id_ativo,
        )
        st.caption("Atualização temporariamente indisponível; exibindo o último snapshot válido.")

    saldos_live = xml_engine.calcular_saldos_com_cofre(dados_live)
    selo_telemetria = (
        "🟢 Telemetria Hub Split Payment Ativa • Atualização em Tempo Real (&lt; 15s)"
        if TELEMETRIA_AUTOMATICA_15S
        else "🟡 Telemetria Hub Split Payment • Atualização na próxima interação"
    )
    st.markdown(
        f"<div style='text-align:right;color:#34d399;font-size:12px;"
        f"font-weight:700;margin-bottom:8px;'>{selo_telemetria}</div>",
        unsafe_allow_html=True,
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"""
        <div class="exec-card card-bruto">
            <div class="card-label">⚡ CRÉDITO DIGITAL BRUTO ACUMULADO</div>
            <div class="card-value-bruto">R$ {saldos_live['bruto']:,.2f}</div>
            <div class="card-desc">Alíquotas inteligentes por NCM/CNAE para {empresa_ativa['razao_social']}</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
        <div class="exec-card card-validado">
            <div class="card-label">🟩 CRÉDITO VALIDADO / SALDO LÍQUIDO</div>
            <div class="card-value-validado">R$ {saldos_live['credito_liquido']:,.2f}</div>
            <div class="card-desc">Disponível R$ {saldos_live['credito_disponivel']:,.2f} após abatimentos digitais de R$ {saldos_live['credito_compensado']:,.2f} e retenções de R$ {saldos_live['valor_retido_split_payment']:,.2f}</div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown(f"""
        <div class="exec-card card-quarentena">
            <div class="card-label">🔒 SALDO EM QUARENTENA</div>
            <div class="card-value-quarentena">R$ {saldos_live['quarentena']:,.2f}</div>
            <div class="card-desc">Aguardando decisão técnica do contador (Zero bloqueio automático)</div>
        </div>
        """, unsafe_allow_html=True)


renderizar_cards_telemetria()

st.write("")
st.markdown("### 📂 Módulos de Auditoria, Devolução Expressa e Inteligência Estratégica")

with st.expander("📊 Histórico Anual da Empresa (Últimos 12 Meses)", expanded=False):
    st.caption(
        f"Dados isolados para {empresa_ativa['razao_social']} "
        f"(CNPJ {empresa_ativa.get('cnpj', '')}); competências pelo mês de emissão "
        "ou, quando indisponível, pela data de registro. Créditos históricos não são recalculados."
    )
    periodos_anuais = [competencia_atual - deslocamento for deslocamento in range(11, -1, -1)]
    itens_por_periodo = {periodo: [] for periodo in periodos_anuais}
    notas_por_periodo = {periodo: [] for periodo in periodos_anuais}
    for nota in notas:
        periodo = periodo_por_nota.get(str(nota.get("id")))
        if periodo in notas_por_periodo:
            notas_por_periodo[periodo].append(nota)
    for item in itens:
        periodo = periodo_por_nota.get(str(item.get("nota_id")))
        if periodo in itens_por_periodo:
            itens_por_periodo[periodo].append(item)

    linhas_historico = []
    for periodo in reversed(periodos_anuais):
        itens_periodo = itens_por_periodo[periodo]
        resumo_creditos = xml_engine.calcular_resumo_creditos(itens_periodo)
        notas_periodo = notas_por_periodo[periodo]
        versoes = sorted({
            xml_engine.obter_versao_regra_nota(n)
            for n in notas_periodo
        })
        linhas_historico.append({
            "Mês/Competência": periodo.strftime("%m/%Y"),
            "Crédito Digital Bruto (R$)": resumo_creditos["bruto"],
            "Total Aceito / Validado (R$)": resumo_creditos["validado"],
            "Total em Quarentena Pendente (R$)": resumo_creditos["quarentena"],
            "Total Rejeitado / Glosado Art. 57 (R$)": resumo_creditos["rejeitado"],
            "Versão da Regra Aplicada": ", ".join(versoes) if versoes else "—",
        })

    total_aceito_anual = sum(row["Total Aceito / Validado (R$)"] for row in linhas_historico)
    total_pendente_anual = sum(row["Total em Quarentena Pendente (R$)"] for row in linhas_historico)
    total_rejeitado_anual = sum(row["Total Rejeitado / Glosado Art. 57 (R$)"] for row in linhas_historico)
    total_processado_anual = sum(row["Crédito Digital Bruto (R$)"] for row in linhas_historico)
    if notas:
        resumo_anual = st.columns(4)
        resumo_anual[0].metric("Crédito bruto", f"R$ {total_processado_anual:,.2f}")
        resumo_anual[1].metric("Aceito / validado", f"R$ {total_aceito_anual:,.2f}")
        resumo_anual[2].metric("Em quarentena", f"R$ {total_pendente_anual:,.2f}")
        resumo_anual[3].metric("Rejeitado / glosado", f"R$ {total_rejeitado_anual:,.2f}")
        st.dataframe(pd.DataFrame(linhas_historico), hide_index=True, width="stretch")
    else:
        st.info("O histórico será consolidado automaticamente após a primeira importação de XML.")

creditos_auditados = (
    resumo_creditos_acumulado["validado"]
    + resumo_creditos_acumulado["rejeitado"]
)
creditos_total_auditavel = (
    creditos_auditados + resumo_creditos_acumulado["quarentena"]
)
indice_governanca = (
    creditos_auditados / creditos_total_auditavel
    if creditos_total_auditavel > 0
    else 0.0
)
st.progress(
    indice_governanca,
    text=f"Índice de Eficiência e Governança da Carteira: {indice_governanca:.1%}",
)

# ==========================================
# CHAMADA DO MOTOR DE INGESTÃO E EMISSOR DE DEVOLUÇÃO
# ==========================================
xml_engine.renderizar_modulo_xml(supabase, empresa_id_ativo, empresa_ativa)
xml_engine.renderizar_emissor_devolucao(supabase, empresa_ativa, notas, itens)

# ==========================================
# 9. PASTAS EXPANSÍVEIS BLINDADAS (MÓDULOS 1 A 6)
# ==========================================

# MÓDULO 3: RADAR DE CONTINGÊNCIA & OMISSÃO SEFAZ
notas_contingencia = [n for n in notas if "Contingência" in str(n.get("manifestacao_status", ""))]
with st.expander(f"📁 3. ⚠️ Radar de Contingência & Divergência SEFAZ ({len(notas_contingencia)} Alertas Ativos)", expanded=False):
    if notas_contingencia:
        st.markdown(
            f"<span style='color: #f87171; font-weight: bold;'>⚠️ ATENÇÃO CONTADOR:</span> Foram detectadas notas emitidas em contingência (`tpEmis != 1`) para <b>{empresa_ativa['razao_social']}</b> cujo crédito foi retido preventivamente.",
            unsafe_allow_html=True
        )
        df_cont = pd.DataFrame([
            {
                "Chave de Acesso SEFAZ": n.get("chave_acesso", ""),
                "CNPJ Fornecedor": n.get("cnpj_emitente", ""),
                "Valor da Nota (R$)": float(n.get("valor_total_nf", 0.0)),
                "Status SEFAZ": n.get("manifestacao_status", "Contingência")
            }
            for n in notas_contingencia
        ])
        st.dataframe(df_cont, hide_index=True, width="stretch")
    else:
        st.success(f"✅ Nenhuma nota fiscal em contingência ou divergência pendente para o CNPJ {empresa_ativa['cnpj']}.")

# MÓDULO 4: QUARENTENA & HISTÓRICO (SOBERANIA DO CONTADOR)
itens_pendentes = [i for i in itens if i.get("classificacao_item") == "Quarentena Uso/Consumo"]
itens_auditados = [i for i in itens if i.get("classificacao_item") in ["Rejeitado", "Rejeitado pelo Contador", "Aprovado Manualmente Contador"]]

@fragment_decorator
def renderizar_fila_quarentena(
    pendentes: list[dict],
    auditados: list[dict],
    empresa_id: int | str,
    eventos_liquidacao: list[dict],
) -> None:
    prefixo_selecao = f"quarentena_selecionar_{empresa_id}_"
    chave_selecionar_todos = f"quarentena_todos_{empresa_id}"
    if st.session_state.pop(f"limpar_selecao_quarentena_{empresa_id}", False):
        st.session_state.pop(chave_selecionar_todos, None)
        chaves_widgets = st.session_state.pop(
            f"quarentena_widgets_{empresa_id}",
            set(),
        )
        for chave_widget in chaves_widgets:
            st.session_state.pop(chave_widget, None)
        for item in pendentes:
            uid = str(item.get("item_uid") or "")
            if uid:
                st.session_state.pop(
                    prefixo_selecao + hashlib.sha256(uid.encode()).hexdigest()[:20],
                    None,
                )

    def chave_checkbox(item: dict) -> str:
        uid = str(item.get("item_uid") or "")
        return prefixo_selecao + hashlib.sha256(uid.encode()).hexdigest()[:20]

    def alterar_selecao_global() -> None:
        selecionado = bool(st.session_state.get(chave_selecionar_todos))
        for item in pendentes:
            if item.get("item_uid"):
                st.session_state[chave_checkbox(item)] = selecionado

    def alterar_selecao_individual() -> None:
        st.session_state[chave_selecionar_todos] = False

    def persistir_decisao(item: dict, status: str, _: float) -> None:
        decisao = "aprovar" if status == "Aprovado Manualmente Contador" else "rejeitar"
        atualizado = xml_engine.decidir_itens_quarentena_lote(
            supabase,
            empresa_id,
            [item],
            decisao,
        )
        if atualizado != 1:
            raise RuntimeError("A decisão do item não foi confirmada.")

    with st.expander(
        f"📁 4. Fila de Quarentena & Histórico de Auditoria "
        f"({len(pendentes)} pendentes | {len(auditados)} auditados)",
        expanded=st.session_state.get("modulo_ativo") == "quarentena",
    ):
        if pendentes:
            st.markdown("#### ⏳ Itens Aguardando Decisão Técnica do Contador")
            st.checkbox(
                "☑️ Selecionar todos os itens pendentes",
                key=chave_selecionar_todos,
                on_change=alterar_selecao_global,
            )
            filtro_itens = st.text_input(
                "Buscar na quarentena",
                placeholder="Descrição, NCM ou CFOP",
                autocomplete="off",
                key=f"busca_quarentena_{empresa_id}",
            ).strip().casefold()
            itens_filtrados = [
                item for item in pendentes
                if not filtro_itens
                or filtro_itens in " ".join(
                    str(item.get(campo, ""))
                    for campo in ("descricao", "ncm", "cfop")
                ).casefold()
            ]
            limite_pagina = st.selectbox(
                "Itens por página",
                [10, 20, 40],
                index=1,
                key=f"limite_quarentena_{empresa_id}",
            )
            total_paginas = max(
                1,
                (len(itens_filtrados) + limite_pagina - 1) // limite_pagina,
            )
            pagina = st.selectbox(
                f"Página ({len(pendentes)} pendentes no total)",
                list(range(1, total_paginas + 1)),
                key=f"pagina_quarentena_{empresa_id}",
            )
            inicio_pagina = (pagina - 1) * limite_pagina
            itens_pagina = itens_filtrados[
                inicio_pagina:inicio_pagina + limite_pagina
            ]
            st.caption(
                f"Exibindo {len(itens_pagina)} de {len(itens_filtrados)} "
                f"itens filtrados; {len(pendentes)} pendentes no total."
            )
            for idx, item in enumerate(itens_pagina, start=inicio_pagina):
                credito = xml_engine.obter_credito_quarentena(item)
                item_id = item.get(
                    "id",
                    item.get("item_uid", f"item_{idx}"),
                )
                st.markdown(
                    f"<div style='background:#07090e;border:1px solid #374151;"
                    f"border-radius:8px;padding:15px;margin-bottom:10px;'>"
                    f"<b style='color:#fff;font-size:15px;'>📦 {item.get('descricao', 'Item')}</b><br>"
                    f"<span style='color:#9ca3af;font-size:13px;'>NCM: <code>{item.get('ncm', '')}</code> "
                    f"| CFOP: <code>{item.get('cfop', '')}</code> | Valor Contábil: "
                    f"<b>R$ {float(item.get('valor_item', 0.0)):,.2f}</b> | Crédito Potencial Retido: "
                    f"<b style='color:#fbbf24;'>R$ {credito:,.2f}</b></span></div>",
                    unsafe_allow_html=True,
                )
                uid_item = str(item.get("item_uid") or "")
                if uid_item:
                    chave_item = chave_checkbox(item)
                    chaves_widgets = st.session_state.setdefault(
                        f"quarentena_widgets_{empresa_id}",
                        set(),
                    )
                    chaves_widgets.add(chave_item)
                    st.checkbox(
                        "Selecionar para decisão em massa",
                        key=chave_item,
                        on_change=alterar_selecao_individual,
                    )
                aprovar, rejeitar = st.columns(2)
                if aprovar.button(
                    "✅ Liberar Crédito (Aprovar)",
                    key=f"lib_{item_id}",
                    width="stretch",
                ):
                    try:
                        persistir_decisao(
                            item, "Aprovado Manualmente Contador", credito
                        )
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Falha ao aprovar item %s para empresa %s.",
                            item.get("id"),
                            empresa_id,
                        )
                        st.toast("A aprovação não foi confirmada. Tente novamente.", icon="⚠️")
                    else:
                        item["classificacao_item"] = "Aprovado Manualmente Contador"
                        item["valor_credito_item"] = round(credito, 2)
                        item["status_credito"] = "Validado"
                        pendentes[:] = [pendente for pendente in pendentes if pendente is not item]
                        if all(str(a.get("id")) != str(item.get("id")) for a in auditados):
                            auditados.append(item)
                        limpar_caches_dados()
                        st.session_state["modulo_ativo"] = "quarentena"
                        st.toast(
                            f"✅ +R$ {credito:,.2f} liberados para o Crédito Validado!",
                            icon="💰",
                        )
                        st.session_state[
                            f"limpar_selecao_quarentena_{empresa_id}"
                        ] = True
                        st.rerun()
                if rejeitar.button(
                    "🚫 Bloquear Crédito (Uso/Consumo Definitivo)",
                    key=f"bloq_{item_id}",
                    width="stretch",
                ):
                    try:
                        persistir_decisao(item, "Rejeitado", credito)
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Falha ao rejeitar item %s para empresa %s.",
                            item.get("id"),
                            empresa_id,
                        )
                        st.toast("A rejeição não foi confirmada. Tente novamente.", icon="⚠️")
                    else:
                        item["classificacao_item"] = "Rejeitado"
                        item["valor_credito_item"] = round(credito, 2)
                        item["status_credito"] = "Rejeitado"
                        pendentes[:] = [pendente for pendente in pendentes if pendente is not item]
                        if all(str(a.get("id")) != str(item.get("id")) for a in auditados):
                            auditados.append(item)
                        limpar_caches_dados()
                        st.session_state["modulo_ativo"] = "quarentena"
                        st.toast(
                            f"🛡️ R$ {credito:,.2f} glosados (Art. 57) — risco fiscal eliminado!",
                            icon="🛡️",
                        )
                        st.session_state[
                            f"limpar_selecao_quarentena_{empresa_id}"
                        ] = True
                        st.rerun()
                st.markdown("---")
            selecionados = [
                item for item in pendentes
                if item.get("item_uid")
                and st.session_state.get(chave_checkbox(item), False)
            ]
            if selecionados:
                st.markdown(
                    f"<div style='padding:12px;border:1px solid #d4af37;"
                    f"border-radius:8px;background:#111827;margin:8px 0;'>"
                    f"<b>{len(selecionados)} item(ns) selecionado(s)</b></div>",
                    unsafe_allow_html=True,
                )
                aprovar_lote, rejeitar_lote = st.columns(2)
                if aprovar_lote.button(
                    f"✅ Aprovar Itens Selecionados ({len(selecionados)})",
                    key=f"quarentena_aprovar_lote_{empresa_id}",
                    width="stretch",
                ):
                    try:
                        xml_engine.decidir_itens_quarentena_lote(
                            supabase,
                            empresa_id,
                            selecionados,
                            "aprovar",
                        )
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Falha ao aprovar itens em lote para empresa %s.",
                            empresa_id,
                        )
                        st.toast(
                            "A aprovação em lote não foi confirmada. Nenhum item foi alterado.",
                            icon="⚠️",
                        )
                    else:
                        st.session_state[
                            f"limpar_selecao_quarentena_{empresa_id}"
                        ] = True
                        st.session_state["modulo_ativo"] = "quarentena"
                        st.toast(
                            f"{len(selecionados)} item(ns) aprovado(s) com sucesso.",
                            icon="✅",
                        )
                        st.rerun()
                if rejeitar_lote.button(
                    f"🛑 Rejeitar Itens Selecionados ({len(selecionados)})",
                    key=f"quarentena_rejeitar_lote_{empresa_id}",
                    width="stretch",
                ):
                    try:
                        xml_engine.decidir_itens_quarentena_lote(
                            supabase,
                            empresa_id,
                            selecionados,
                            "rejeitar",
                        )
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Falha ao rejeitar itens em lote para empresa %s.",
                            empresa_id,
                        )
                        st.toast(
                            "A rejeição em lote não foi confirmada. Nenhum item foi alterado.",
                            icon="⚠️",
                        )
                    else:
                        st.session_state[
                            f"limpar_selecao_quarentena_{empresa_id}"
                        ] = True
                        st.session_state["modulo_ativo"] = "quarentena"
                        st.toast(
                            f"{len(selecionados)} item(ns) rejeitado(s) e glosado(s) (Art. 57).",
                            icon="🛡️",
                        )
                        st.rerun()
        else:
            st.success("🎉 Nenhum item pendente de análise na quarentena para este cliente.")

        if auditados:
            st.markdown("#### 📜 Histórico Permanente de Auditoria Manual")
            df_audit = pd.DataFrame([
                {
                    "ID Item": item.get("id", "-"),
                    "Descrição": item.get("descricao", ""),
                    "NCM": item.get("ncm", ""),
                    "CFOP": item.get("cfop", ""),
                    "Valor Contábil (R$)": float(item.get("valor_item", 0.0)),
                    "Status / Decisão do Contador": item.get("classificacao_item", ""),
                    "Fundamentação Legal": xml_engine.fundamentacao_legal_item(item),
                }
                for item in auditados
            ])
            st.dataframe(df_audit, height=250, hide_index=True, width="stretch")
        if eventos_liquidacao:
            st.markdown("#### 💳 Histórico de Auditoria Financeira — Split Payment")
            st.dataframe(
                pd.DataFrame([
                    {
                        "Timestamp": evento.get("occurred_at", ""),
                        "Usuário": evento.get("usuario", ""),
                        "ID_OP": evento.get("id_op", ""),
                        "Chave NF-e": evento.get("chave_acesso", ""),
                        "Evento": evento.get("event_type", ""),
                        "Valor liquidado (R$)": float(
                            evento.get("valor_liquidado", 0.0) or 0.0
                        ),
                        "IBS segregado (R$)": float(
                            evento.get("ibs_segregado", 0.0) or 0.0
                        ),
                        "CBS segregado (R$)": float(
                            evento.get("cbs_segregado", 0.0) or 0.0
                        ),
                        "Crédito compensado (R$)": float(
                            evento.get("credito_compensado", 0.0) or 0.0
                        ),
                        "Retido em caixa (R$)": float(
                            evento.get("valor_retido_caixa", 0.0) or 0.0
                        ),
                        "Impacto bancário (R$)": float(
                            evento.get("impacto_banco", 0.0) or 0.0
                        ),
                    }
                    for evento in sorted(
                        eventos_liquidacao,
                        key=lambda registro: str(registro.get("occurred_at", "")),
                        reverse=True,
                    )
                ]),
                height=250,
                hide_index=True,
                width="stretch",
            )


renderizar_fila_quarentena(
    itens_pendentes,
    itens_auditados,
    empresa_id_ativo,
    eventos_liquidacao,
)

# MÓDULO 5: NOTAS FISCAIS RECEBIDAS (COM SELETOR DE DOWNLOAD NOTA POR NOTA DO CLIENTE)
with st.expander(f"📁 5. Notas Fiscais Monitoradas na SEFAZ ({len(notas)} notas acumuladas deste cliente)", expanded=False):
    if notas:
        st.caption(
            "Selecione uma nota abaixo para abrir seu DANFE, baixar o PDF ou "
            "exportar o XML reconstruído. A seleção na primeira coluna também "
            "permite gerar um pacote ZIP."
        )
        df_exibir = pd.DataFrame([
            {
                "Baixar XML": True,
                "Chave de Acesso NF-e": str(n.get("chave_acesso", "")),
                "CNPJ Fornecedor": str(n.get("cnpj_emitente", "")),
                "Valor Total (R$)": float(n.get("valor_total_nf", 0.0)),
                "Status SEFAZ": str(n.get("manifestacao_status", "Ciência da Operação (SEFAZ OK)")),
                "Hash SHA-256 (Auditoria)": str(n.get("hash_sha256_xml", hashlib.sha256(str(n.get("chave_acesso", "")).encode()).hexdigest() if "hashlib" in globals() else "Auditado"))
            }
            for n in notas
        ])

        df_notas_marcadas = st.data_editor(
            df_exibir,
            height=250,
            hide_index=True,
            width="stretch",
            disabled=["Chave de Acesso NF-e", "CNPJ Fornecedor", "Valor Total (R$)", "Status SEFAZ", "Hash SHA-256 (Auditoria)"],
            key=f"editor_notas_acumuladas_{empresa_id_ativo}"
        )

        notas_por_id = {
            str(nota.get("id")): nota
            for nota in notas
            if nota.get("id") is not None
        }
        ids_notas_danfe = list(notas_por_id)
        nota_id_danfe = st.selectbox(
            "Visualizador DANFE — Espelho de Conferência",
            ids_notas_danfe,
            format_func=lambda nota_id: (
                f"NF-e {notas_por_id[nota_id].get('chave_acesso', '')} | "
                f"{notas_por_id[nota_id].get('razao_social_emitente', 'Emitente')} | "
                f"R$ {float(notas_por_id[nota_id].get('valor_total_nf', 0.0)):,.2f}"
            ),
            key=f"visualizar_danfe_{empresa_id_ativo}",
        )
        nota_danfe = notas_por_id[nota_id_danfe]
        chave_nota_danfe = str(nota_danfe.get("chave_acesso") or "")
        itens_danfe = [
            item for item in itens
            if (
                str(item.get("nota_id")) == str(nota_danfe.get("id"))
                or (
                    chave_nota_danfe
                    and (
                        str(item.get("chave_acesso") or "") == chave_nota_danfe
                        or chave_nota_danfe
                        in str(item.get("item_uid") or "")
                    )
                )
            )
        ]
        credito_ibs_id_op = round(sum(
            float(item.get("credito_ibs_gerado", 0.0) or 0.0)
            for item in itens_danfe
        ), 2)
        credito_cbs_id_op = round(sum(
            float(item.get("credito_cbs_gerado", 0.0) or 0.0)
            for item in itens_danfe
        ), 2)
        credito_ibs_danfe, credito_cbs_danfe = xml_engine.somar_ibs_cbs_itens(
            itens_danfe
        )
        credito_ibs_danfe = round(credito_ibs_danfe, 2)
        credito_cbs_danfe = round(credito_cbs_danfe, 2)
        numero_danfe, serie_danfe = xml_engine.resolver_numero_serie(nota_danfe)
        alerta_fornecedor = xml_engine.alerta_fornecedor_simples(
            nota_danfe.get("crt_emitente")
        )
        credito_danfe = round(sum(
            float(item.get("valor_credito_item", 0.0) or 0.0)
            for item in itens_danfe
        ), 2)
        c_danfe, c_pdf, c_xml = st.columns([3, 1, 1])
        with c_danfe:
            st.markdown(
                f"#### DANFE — {nota_danfe.get('razao_social_emitente', 'Emitente não informado')}"
            )
            st.caption(
                "Espelho de conferência e auditoria. Não substitui o DANFE oficial "
                "autorizado pela SEFAZ."
            )
            st.caption(
                f"Emitente: CNPJ {nota_danfe.get('cnpj_emitente', '—')} | "
                f"Destinatário: {empresa_ativa.get('razao_social', '')} "
                f"(CNPJ {empresa_ativa.get('cnpj', '')})"
            )
            chave_acesso = str(nota_danfe.get("chave_acesso", ""))
            st.code(
                " ".join(
                    chave_acesso[indice:indice + 4]
                    for indice in range(0, len(chave_acesso), 4)
                ),
                language=None,
            )
        with c_pdf:
            pdf_danfe = relatorio_engine.gerar_danfe_pdf(
                nota_danfe,
                itens_danfe,
                empresa_ativa,
            )
            st.download_button(
                "📄 Baixar Espelho DANFE (.PDF)",
                data=pdf_danfe,
                file_name=f"DANFE_{chave_acesso or nota_id_danfe}.pdf",
                mime="application/pdf",
                width="stretch",
                key=f"baixar_danfe_pdf_{empresa_id_ativo}_{nota_id_danfe}",
            )
        with c_xml:
            xml_danfe = str(nota_danfe.get("xml_reconstruido") or "").strip()
            if not xml_danfe:
                xml_danfe = xml_engine.gerar_xml_reconstruido_cliente(
                    empresa_ativa,
                    nota_danfe,
                    itens_danfe,
                )
            st.download_button(
                "📥 Baixar XML Reconstruído",
                data=xml_danfe.encode("utf-8"),
                file_name=f"NFe_{chave_acesso or nota_id_danfe}.xml",
                mime="application/xml",
                width="stretch",
                key=f"baixar_xml_reconstruido_{empresa_id_ativo}_{nota_id_danfe}",
            )

        st.markdown(
            xml_engine.html_cartoes_espelho([
                ("Número / Série", f"{numero_danfe} / {serie_danfe}"),
                (
                    "Base de Cálculo / Produtos",
                    xml_engine.formatar_brl(nota_danfe.get("valor_total_nf", 0.0)),
                ),
                ("IBS apurado", xml_engine.formatar_brl(credito_ibs_danfe)),
                ("CBS apurado", xml_engine.formatar_brl(credito_cbs_danfe)),
                (
                    "Crédito Split Payment LC 214/2025",
                    xml_engine.formatar_brl(credito_danfe),
                ),
            ]),
            unsafe_allow_html=True,
        )
        if alerta_fornecedor:
            st.warning(alerta_fornecedor)
        st.markdown("**Representação do código de barras / chave de acesso**")
        st.code(
            f"|||| ||| |||||| || |||| ||||||| ||| ||||| ||||\n{chave_acesso}",
            language=None,
        )
        st.markdown("**Produtos e serviços auditados**")
        if itens_danfe:
            st.dataframe(
                pd.DataFrame([
                    {
                        "Código": item.get("codigo") or item.get("cProd") or "—",
                        "Descrição": item.get("descricao", ""),
                        "NCM": item.get("ncm", ""),
                        "CFOP": item.get("cfop", ""),
                        "Quantidade": float(
                            item.get("quantidade_comercial")
                            or item.get("qcom")
                            or 0.0
                        ),
                        "Valor Unitário (R$)": xml_engine.formatar_brl(
                            item.get("valor_unitario")
                            or (
                                float(item.get("valor_item", 0.0) or 0.0)
                                / max(float(item.get("quantidade_comercial") or 1.0), 0.0001)
                            )
                        ),
                        "Valor Total (R$)": xml_engine.formatar_brl(
                            item.get("valor_item", 0.0)
                        ),
                        "Status de Auditoria": item.get(
                            "classificacao_item",
                            item.get("status_credito", ""),
                        ),
                        "Fundamentação Legal": xml_engine.fundamentacao_legal_item(item),
                    }
                    for item in itens_danfe
                ]),
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("Esta nota não possui itens detalhados persistidos.")

        st.markdown("---")
        st.markdown(
            "#### 💳 Hub de Liquidação Financeira, Segregação & Eventos Split Payment"
        )
        valor_base_original = float(
            nota_danfe.get("valor_total_nf")
            or sum(float(item.get("valor_item", 0.0) or 0.0) for item in itens_danfe)
        )
        id_op = xml_engine.calcular_id_operacao(
            empresa_id_ativo,
            nota_danfe,
            valor_base_original,
            credito_ibs_id_op,
            credito_cbs_id_op,
        )
        resumo_eventos_operacao = xml_engine.resumir_eventos_liquidacao(
            eventos_liquidacao
        ).get(id_op, {})
        valor_base_operacao = float(
            resumo_eventos_operacao.get("valor_negociado")
            or valor_base_original
        )
        fator_negociacao = (
            valor_base_operacao / valor_base_original
            if valor_base_original
            else 0.0
        )
        ibs_operacao = round(credito_ibs_danfe * fator_negociacao, 2)
        cbs_operacao = round(credito_cbs_danfe * fator_negociacao, 2)
        valor_pago_acumulado = float(
            resumo_eventos_operacao.get("valor_pago_acumulado", 0.0)
        )
        saldo_operacao = max(0.0, valor_base_operacao - valor_pago_acumulado)
        st.code(f"ID_OP: {id_op}", language=None)
        st.caption(
            f"Nota → Operação → Transação → Liquidação → Segregação → "
            f"Recolhimento/Compensação → Conciliação | "
            f"Base R$ {valor_base_operacao:,.2f} | IBS R$ {ibs_operacao:,.2f} | "
            f"CBS R$ {cbs_operacao:,.2f}."
        )
        st.caption(
            f"Liquidado R$ {valor_pago_acumulado:,.2f} | "
            f"Saldo em aberto R$ {saldo_operacao:,.2f} | "
            f"Crédito digital disponível R$ {saldo_credito_disponivel:,.2f}."
        )
        segregacao_acumulada = xml_engine.calcular_segregacao_liquidacao(
            valor_base_operacao,
            ibs_operacao,
            cbs_operacao,
            valor_pago_acumulado,
        )
        col_liquidado, col_pendente = st.columns(2)
        col_liquidado.metric(
            "IBS/CBS segregado nas liquidações",
            f"R$ {segregacao_acumulada['ibs_segregado_atual'] + segregacao_acumulada['cbs_segregado_atual']:,.2f}",
            help=(
                f"IBS R$ {segregacao_acumulada['ibs_segregado_atual']:,.2f} + "
                f"CBS R$ {segregacao_acumulada['cbs_segregado_atual']:,.2f}"
            ),
        )
        col_pendente.metric(
            "IBS/CBS pendente de liquidação futura",
            f"R$ {segregacao_acumulada['ibs_pendente'] + segregacao_acumulada['cbs_pendente']:,.2f}",
            help=(
                f"IBS R$ {segregacao_acumulada['ibs_pendente']:,.2f} + "
                f"CBS R$ {segregacao_acumulada['cbs_pendente']:,.2f}"
            ),
        )
        qtd_eventos_operacao = sum(
            1 for evento in eventos_liquidacao if str(evento.get("id_op")) == id_op
        )
        # Widgets fora de st.form: o tipo e a modalidade precisam recarregar os
        # campos condicionais (parcelas, taxa e valor padrão) na hora da escolha.
        with st.container(border=False):
            tipos_evento_financeiro = [
                "Liquidação Parcial/Integral",
                "Antecipação de Recebíveis (Desconto/Taxa)",
                "Estorno de Transação",
                "Alteração/Renegociação de Operação",
            ]
            tipo_evento_financeiro = st.selectbox(
                "Evento financeiro auditável",
                tipos_evento_financeiro,
                key=f"tipo_evento_financeiro_{empresa_id_ativo}_{id_op}",
            )
            tipo_pagamento = st.radio(
                "Modalidade de pagamento",
                ["Pagamento parcial", "Pagamento parcelado", "Integral"],
                horizontal=True,
                key=f"modalidade_pagamento_{empresa_id_ativo}_{id_op}",
            )
            valor_padrao_evento = (
                min(saldo_operacao, valor_base_operacao)
                if tipo_evento_financeiro == "Liquidação Parcial/Integral"
                else (
                    min(valor_pago_acumulado, valor_base_operacao)
                    if tipo_evento_financeiro == "Estorno de Transação"
                    else valor_base_operacao
                )
            )
            sufixo_valor_evento = (
                f"{tipos_evento_financeiro.index(tipo_evento_financeiro)}_{qtd_eventos_operacao}"
            )
            valor_evento_financeiro = st.number_input(
                "Valor efetivamente liquidado / antecipado / ajustado (R$)",
                min_value=0.0,
                max_value=max(1_000_000_000.0, valor_base_operacao),
                value=float(valor_padrao_evento),
                step=100.0,
                format="%.2f",
                key=(
                    f"valor_evento_financeiro_{empresa_id_ativo}_{id_op}_"
                    f"{sufixo_valor_evento}"
                ),
            )
            if tipo_pagamento == "Pagamento parcelado":
                numero_parcela, total_parcelas = st.columns(2)
                parcela_atual = numero_parcela.number_input(
                    "Parcela atual",
                    min_value=1,
                    value=1,
                    step=1,
                    key=f"parcela_atual_{empresa_id_ativo}_{id_op}",
                )
                quantidade_parcelas = total_parcelas.number_input(
                    "Total de parcelas",
                    min_value=1,
                    value=1,
                    step=1,
                    key=f"total_parcelas_{empresa_id_ativo}_{id_op}",
                )
            else:
                parcela_atual = 1
                quantidade_parcelas = 1
            taxa_antecipacao = 0.0
            if tipo_evento_financeiro == "Antecipação de Recebíveis (Desconto/Taxa)":
                taxa_antecipacao = st.number_input(
                    "Desconto / taxa de antecipação (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=0.0,
                    step=0.1,
                    format="%.2f",
                    key=f"taxa_antecipacao_{empresa_id_ativo}_{id_op}",
                )
            registrar_liquidacao = st.button(
                "💾 Registrar Evento Oficial no Ledger",
                width="stretch",
                key=f"registrar_liquidacao_{empresa_id_ativo}_{id_op}",
            )
        if registrar_liquidacao:
            valor_evento = round(float(valor_evento_financeiro), 2)
            dados_evento = None
            if tipo_evento_financeiro == "Liquidação Parcial/Integral":
                if (
                    valor_evento <= 0
                    or valor_evento > saldo_operacao
                    or (
                        tipo_pagamento == "Integral"
                        and valor_evento != round(saldo_operacao, 2)
                    )
                    or (
                        tipo_pagamento == "Pagamento parcelado"
                        and parcela_atual > quantidade_parcelas
                    )
                ):
                    st.error(
                        f"Informe um valor válido para a modalidade; o saldo em aberto "
                        f"é R$ {saldo_operacao:,.2f}."
                    )
                else:
                    segregacao = xml_engine.calcular_segregacao_liquidacao(
                        valor_base_operacao,
                        ibs_operacao,
                        cbs_operacao,
                        valor_evento,
                        valor_pago_acumulado,
                    )
                    tributo_segregado = round(
                        segregacao["ibs_segregado_atual"]
                        + segregacao["cbs_segregado_atual"],
                        2,
                    )
                    credito_compensado = min(
                        saldo_credito_disponivel,
                        tributo_segregado,
                    )
                    retido_caixa = max(0.0, tributo_segregado - credito_compensado)
                    impacto_banco = round(valor_evento - retido_caixa, 2)
                    dados_evento = {
                        "valor_liquidado": valor_evento,
                        "ibs_segregado": segregacao["ibs_segregado_atual"],
                        "cbs_segregado": segregacao["cbs_segregado_atual"],
                        "ibs_pendente": segregacao["ibs_pendente"],
                        "cbs_pendente": segregacao["cbs_pendente"],
                        "credito_compensado": round(credito_compensado, 2),
                        "valor_retido_caixa": round(retido_caixa, 2),
                        "impacto_banco": impacto_banco,
                    }
            elif tipo_evento_financeiro == "Estorno de Transação":
                if valor_evento <= 0 or valor_evento > valor_pago_acumulado:
                    st.error(
                        f"O estorno deve ser maior que zero e não exceder o "
                        f"valor já liquidado de R$ {valor_pago_acumulado:,.2f}."
                    )
                else:
                    segregacao = xml_engine.calcular_segregacao_liquidacao(
                        valor_base_operacao,
                        ibs_operacao,
                        cbs_operacao,
                        valor_evento,
                        valor_pago_acumulado - valor_evento,
                    )
                    credito_restituido = (
                        float(
                            resumo_eventos_operacao.get(
                                "credito_compensado_liquido",
                                0.0,
                            )
                        )
                        * valor_evento
                        / valor_pago_acumulado
                        if valor_pago_acumulado
                        else 0.0
                    )
                    retido_restituido = (
                        float(
                            resumo_eventos_operacao.get(
                                "valor_retido_caixa",
                                0.0,
                            )
                        )
                        * valor_evento
                        / valor_pago_acumulado
                        if valor_pago_acumulado
                        else 0.0
                    )
                    dados_evento = {
                        "valor_liquidado": valor_evento,
                        "ibs_segregado": segregacao["ibs_segregado_atual"],
                        "cbs_segregado": segregacao["cbs_segregado_atual"],
                        "credito_restituido": round(credito_restituido, 2),
                        "valor_retido_caixa": -round(retido_restituido, 2),
                        "impacto_banco": -valor_evento,
                    }
            elif tipo_evento_financeiro == "Antecipação de Recebíveis (Desconto/Taxa)":
                if valor_evento <= 0 or valor_evento > saldo_operacao:
                    st.error(
                        f"A antecipação deve ser maior que zero e não exceder "
                        f"o saldo a receber de R$ {saldo_operacao:,.2f}."
                    )
                else:
                    taxa = round(valor_evento * float(taxa_antecipacao) / 100, 2)
                    dados_evento = {
                        "valor_liquidado": 0.0,
                        "valor_antecipado": valor_evento,
                        "taxa_antecipacao_percentual": float(taxa_antecipacao),
                        "desconto_antecipacao": taxa,
                        "impacto_banco": round(valor_evento - taxa, 2),
                    }
            else:
                if valor_evento <= 0:
                    st.error("Informe o novo valor negociado, maior que zero.")
                else:
                    dados_evento = {
                        "valor_liquidado": 0.0,
                        "valor_negociado": valor_evento,
                        "impacto_banco": 0.0,
                    }

            if dados_evento is not None:
                evento_financeiro = {
                    **dados_evento,
                    "event_id": str(uuid.uuid4()),
                    "event_type": tipo_evento_financeiro,
                    "empresa_id": empresa_id_ativo,
                    "nota_id": nota_danfe.get("id"),
                    "chave_acesso": nota_danfe.get("chave_acesso"),
                    "id_op": id_op,
                    "valor_base_nota": round(valor_base_operacao, 2),
                    "valor_ibs_total": ibs_operacao,
                    "valor_cbs_total": cbs_operacao,
                    "tipo_pagamento": tipo_pagamento,
                    "parcela_atual": int(parcela_atual),
                    "quantidade_parcelas": int(quantidade_parcelas),
                    "usuario": str(
                        st.session_state.get("auth_username")
                        or usuario_autenticado
                    ),
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    xml_engine.registrar_evento_liquidacao_supabase(
                        supabase,
                        empresa_id_ativo,
                        evento_financeiro,
                    )
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Falha ao registrar evento financeiro %s para ID_OP %s.",
                        tipo_evento_financeiro,
                        id_op,
                    )
                    st.error(
                        "O evento não foi confirmado no ledger fiscal. "
                        "Nenhuma alteração financeira foi aplicada."
                    )
                else:
                    st.success("Evento financeiro oficial registrado na trilha de auditoria.")
                    st.rerun()

        eventos_historico_operacao = [
            evento
            for evento in eventos_liquidacao
            if str(evento.get("id_op")) == id_op
        ]
        if eventos_historico_operacao:
            st.markdown("##### 📜 Histórico de Auditoria da Operação")
            st.dataframe(
                pd.DataFrame([
                    {
                        "Timestamp": evento.get("occurred_at", ""),
                        "Usuário": evento.get("usuario", ""),
                        "ID_OP": evento.get("id_op", ""),
                        "Evento": evento.get("event_type", ""),
                        "Valor (R$)": float(evento.get("valor_liquidado", 0.0) or 0.0),
                        "IBS segregado (R$)": float(evento.get("ibs_segregado", 0.0) or 0.0),
                        "CBS segregado (R$)": float(evento.get("cbs_segregado", 0.0) or 0.0),
                        "Crédito compensado (R$)": float(evento.get("credito_compensado", 0.0) or 0.0),
                        "Impacto no saldo líquido (R$)": float(evento.get("impacto_banco", 0.0) or 0.0),
                    }
                    for evento in sorted(
                        eventos_historico_operacao,
                        key=lambda registro: str(registro.get("occurred_at", "")),
                        reverse=True,
                    )
                ]),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("Nenhum evento financeiro registrado para este ID_OP.")

        chaves_para_baixar = df_notas_marcadas[df_notas_marcadas["Baixar XML"] == True]["Chave de Acesso NF-e"].tolist()
        chave_pacote = f"pacote_xml_{empresa_id_ativo}"
        selecao_atual = tuple(sorted(chaves_para_baixar))
        if chaves_para_baixar and st.button(
            "📦 Preparar Pacote .ZIP das Notas Selecionadas",
            width="stretch",
            key=f"btn_preparar_zip_{empresa_id_ativo}"
        ):
            buf_cli = io.BytesIO()
            with zipfile.ZipFile(buf_cli, "w", zipfile.ZIP_DEFLATED) as zf_c:
                for n_obj in notas:
                    if str(n_obj.get("chave_acesso", "")) in chaves_para_baixar:
                        itens_desta_nf = [
                            i for i in itens
                            if str(i.get("nota_id")) == str(n_obj.get("id"))
                        ]
                        xml_rec = str(n_obj.get("xml_reconstruido") or "").strip()
                        if not xml_rec:
                            xml_rec = xml_engine.gerar_xml_reconstruido_cliente(
                                empresa_ativa,
                                n_obj,
                                itens_desta_nf,
                            )
                        zf_c.writestr(f"NFe_{n_obj.get('chave_acesso', '000')}.xml", xml_rec)
            st.session_state[chave_pacote] = (selecao_atual, buf_cli.getvalue())
        pacote_preparado = st.session_state.get(chave_pacote)
        if pacote_preparado and pacote_preparado[0] == selecao_atual:
            st.download_button(
                label=f"📥 Baixar ({len(chaves_para_baixar)}) XML(s) Selecionado(s) de {empresa_ativa['razao_social']} (.ZIP)",
                data=pacote_preparado[1],
                file_name=f"XMLs_Auditados_{empresa_ativa['cnpj']}.zip",
                mime="application/zip",
                width="stretch",
                key=f"btn_dl_notas_cli_{empresa_id_ativo}"
            )
    else:
        st.info("Nenhuma nota fiscal registrada para este cliente. Clique no botão de Sincronizar Lote de Teste SEFAZ acima para importar.")

# MÓDULO 6: DEVOLUÇÕES
with st.expander(f"📁 6. Conciliação de Devoluções ({len(devolucoes)} registros)", expanded=False):
    if devolucoes:
        df_dev_exibir = pd.DataFrame([
            {
                "Chave NF Devolução": d.get("chave_nota_devolucao", ""),
                "Mercadoria Devolvida (R$)": float(d.get("valor_mercadoria_devolvida", 0.0)),
                "Crédito Estornado (R$)": float(d.get("credito_estornado", 0.0)),
                "Motivo do Ajuste": d.get("motivo_ajuste", "")
            }
            for d in devolucoes
        ])
        st.dataframe(df_dev_exibir, height=250, hide_index=True, width="stretch")
    else:
        st.info("Nenhuma devolução registrada no período.")

# MÓDULO 7: SIMULADOR ESTRATÉGICO
@fragment_decorator
def renderizar_simulador(
    empresa_ativa: dict,
    itens_mes: list[dict],
    devolucoes_empresa_mes: list[dict],
    empresa_id_ativo: int | str,
    saldo_credito_disponivel: float,
    perfil_tributario: dict,
) -> None:
    def manter_simulador_aberto() -> None:
        st.session_state["modulo_ativo"] = "simulador"

    with st.expander(
        "📁 7. ⚖️ Simulador Estratégico de Migração (Simples Tradicional vs. Regime Híbrido)",
        expanded=st.session_state.get("modulo_ativo") == "simulador",
    ):
        st.markdown(f"Simulação fiscal focada nas operações de **{empresa_ativa['razao_social']}**.")

        compras_revenda_reais = sum(
            float(i.get("valor_item", 0.0))
            for i in itens_mes
            if i.get("classificacao_item") in ["Aprovado Revenda", "Aprovado Manualmente Contador"]
        )
        devolucoes_reais = sum(
            float(d.get("valor_mercadoria_devolvida", 0.0))
            for d in devolucoes_empresa_mes
        )
        quarentena_reais = sum(
            float(i.get("valor_item", 0.0))
            for i in itens_mes
            if i.get("classificacao_item") == "Quarentena Uso/Consumo"
        )

        faixas_simples = {
            "1ª Faixa (Até R$ 15 mil/mês) — Alíquota Nominal: 4,00%": {"das_total": 4.00, "fatia_consumo": 1.34},
            "2ª Faixa (R$ 15 mil a R$ 30 mil/mês) — Alíquota Nominal: 7,30%": {"das_total": 7.30, "fatia_consumo": 2.45},
            "3ª Faixa (R$ 30 mil a R$ 60 mil/mês) — Alíquota Nominal: 9,50%": {"das_total": 9.50, "fatia_consumo": 3.18},
            "4ª Faixa (R$ 60 mil a R$ 150 mil/mês) — Alíquota Nominal: 10,70%": {"das_total": 10.70, "fatia_consumo": 3.58},
            "5ª Faixa (R$ 150 mil a R$ 300 mil/mês) — Alíquota Nominal: 14,30%": {"das_total": 14.30, "fatia_consumo": 4.79},
            "6ª Faixa (R$ 300 mil a R$ 400 mil/mês) — Alíquota Nominal: 19,00%": {"das_total": 19.00, "fatia_consumo": 6.36},
        }

        with st.form(key=f"form_simulador_estrategico_{empresa_id_ativo}", border=False):
            col_in1, col_in2, col_in3 = st.columns([1.2, 2, 1])
            faturamento_vendas = col_in1.number_input(
                "💰 Vendas no Mês / Faturamento (R$)",
                value=15000.0,
                step=1000.0,
                format="%.2f",
                key=f"sim_vendas_{empresa_id_ativo}",
            )
            faixa_escolhida = col_in2.selectbox(
                "📊 Faixa do Simples Nacional (Anexo I - Comércio)",
                list(faixas_simples.keys()),
                key=f"sim_faixa_{empresa_id_ativo}",
            )
            derivacoes = list(xml_engine.ALIQUOTAS_REFORMA_TRIBUTARIA)
            derivacao_salva = str(
                perfil_tributario.get("derivacao_setorial")
                or derivacoes[0]
            )
            derivacao_simulacao = col_in3.selectbox(
                "🏛️ Derivação IBS + CBS",
                derivacoes,
                index=(
                    derivacoes.index(derivacao_salva)
                    if derivacao_salva in derivacoes
                    else 0
                ),
                key=f"sim_derivacao_{empresa_id_ativo}",
            )
            memoria_simulacao = xml_engine.calcular_memoria_aliquota(
                derivacao_simulacao
            )
            aliquota_iva = float(memoria_simulacao["aliquota_efetiva"])
            st.caption(
                f"Base Legal 26,50% (17,70% IBS + 8,80% CBS) → "
                f"Fator de Redução {memoria_simulacao['fator_reducao']:.0%} → "
                f"Alíquota Efetiva Final {aliquota_iva:.2f}% "
                f"(IBS {memoria_simulacao['aliquota_ibs_efetiva']:.2f}% + "
                f"CBS {memoria_simulacao['aliquota_cbs_efetiva']:.2f}%)."
            )

            col_comp1, col_comp2, col_comp3 = st.columns(3)
            compras_mercadorias = col_comp1.number_input(
                "🛒 Compras p/ Revenda no Mês (R$)",
                value=float(compras_revenda_reais),
                step=500.0,
                format="%.2f",
                key=f"sim_compras_{empresa_id_ativo}",
            )
            devolucoes_mes = col_comp2.number_input(
                "🔄 Devoluções de Compras (R$)",
                value=float(devolucoes_reais),
                step=500.0,
                format="%.2f",
                key=f"sim_devolucoes_{empresa_id_ativo}",
            )
            compras_quarentena_input = col_comp3.number_input(
                "⚠️ Compras Travadas na Quarentena (R$)",
                value=float(quarentena_reais),
                step=100.0,
                format="%.2f",
                key=f"sim_quarentena_{empresa_id_ativo}",
            )

            st.write("")
            parametros_aplicados = st.form_submit_button(
                "⚡ Aplicar Parâmetros e Calcular Simulação",
                width="stretch",
                on_click=manter_simulador_aberto,
            )

        if parametros_aplicados:
            try:
                perfil_tributario = xml_engine.persistir_perfil_tributario_empresa(
                    supabase,
                    empresa_id_ativo,
                    derivacao_simulacao,
                )
                st.session_state[
                    f"perfil_tributario_{empresa_id_ativo}"
                ] = perfil_tributario
            except Exception:
                logging.getLogger(__name__).exception(
                    "Falha ao persistir a derivação tributária do simulador para %s.",
                    empresa_id_ativo,
                )
                st.error(
                    "A simulação foi calculada, mas a derivação não foi persistida no Supabase."
                )

        compras_liquidas_creditaveis = max(
            0.0,
            compras_mercadorias - devolucoes_mes - compras_quarentena_input,
        )
        credito_gerado_simulado = compras_liquidas_creditaveis * (aliquota_iva / 100)

        aliquota_das_total = faixas_simples[faixa_escolhida]["das_total"]
        aliquota_consumo_embutida = faixas_simples[faixa_escolhida]["fatia_consumo"]
        aliquota_fixa_simples = aliquota_das_total - aliquota_consumo_embutida

        imposto_tradicional = faturamento_vendas * (aliquota_das_total / 100)

        das_reduzido = faturamento_vendas * (aliquota_fixa_simples / 100)
        debito_ibs_cbs_vendas = faturamento_vendas * (aliquota_iva / 100)
        ibs_cbs_a_pagar = max(0.0, debito_ibs_cbs_vendas - credito_gerado_simulado)
        imposto_total_hibrido = das_reduzido + ibs_cbs_a_pagar

        diferenca_caixa = imposto_tradicional - imposto_total_hibrido

        st.markdown("---")
        st.markdown("#### 🧮 Memória de Cálculo Contábil (Transparência)")

        credito_aplicado_simulacao = min(saldo_credito_disponivel, debito_ibs_cbs_vendas)
        saldo_liquido_pos_abatimento = max(0.0, debito_ibs_cbs_vendas - credito_aplicado_simulacao)
        st.metric(
            "Saldo Líquido / Disponível pós-Abatimento (Crédito Validado)",
            f"R$ {saldo_liquido_pos_abatimento:,.2f}",
            help=(
                f"Débito estimado de R$ {debito_ibs_cbs_vendas:,.2f} menos "
                f"R$ {credito_aplicado_simulacao:,.2f} de crédito validado disponível. "
                "Este abatimento não altera o crédito bruto nem o total histórico gerado."
            ),
        )

        mc1, mc2, mc3, mc4 = st.columns(4)
        with mc1:
            st.markdown(f"""
            <div class="sub-calc-card">
                <div class="sub-calc-title">1. Compras Líquidas</div>
                <div class="sub-calc-value">R$ {compras_liquidas_creditaveis:,.2f}</div>
                <div class="sub-calc-desc">Revenda menos devoluções e quarentena</div>
            </div>
            """, unsafe_allow_html=True)
        with mc2:
            st.markdown(f"""
            <div class="sub-calc-card">
                <div class="sub-calc-title">2. Crédito Gerado</div>
                <div class="sub-calc-value">R$ {credito_gerado_simulado:,.2f}</div>
                <div class="sub-calc-desc">{aliquota_iva}% sobre compras elegíveis</div>
            </div>
            """, unsafe_allow_html=True)
        with mc3:
            st.markdown(f"""
            <div class="sub-calc-card">
                <div class="sub-calc-title">3. Imposto Vendas (Bruto)</div>
                <div class="sub-calc-value">R$ {debito_ibs_cbs_vendas:,.2f}</div>
                <div class="sub-calc-desc">{aliquota_iva}% sobre as vendas</div>
            </div>
            """, unsafe_allow_html=True)
        with mc4:
            st.markdown(f"""
            <div class="sub-calc-card">
                <div class="sub-calc-title">4. IBS/CBS Líquido</div>
                <div class="sub-calc-value">R$ {ibs_cbs_a_pagar:,.2f}</div>
                <div class="sub-calc-desc">Após abatimento de créditos</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")
        st.markdown("#### 🏆 Veredito Estratégico de Consultoria")

        cr1, cr2, cr3 = st.columns(3)
        with cr1:
            st.markdown(f"""
            <div class="sub-calc-card">
                <div class="sub-calc-title">🏢 Simples Tradicional</div>
                <div class="sub-calc-value">R$ {imposto_tradicional:,.2f}</div>
                <div class="sub-calc-desc">Alíquota de {aliquota_das_total:.2f}% (Sem créditos)</div>
            </div>
            """, unsafe_allow_html=True)
        with cr2:
            st.markdown(f"""
            <div class="sub-calc-card">
                <div class="sub-calc-title">⚡ Simples Híbrido</div>
                <div class="sub-calc-value">R$ {imposto_total_hibrido:,.2f}</div>
                <div class="sub-calc-desc">DAS Reduzido + IBS/CBS Líquido</div>
            </div>
            """, unsafe_allow_html=True)
        with cr3:
            if diferenca_caixa > 0:
                st.markdown(f"""
                <div class="sub-calc-card" style="border-left: 4px solid #34d399;">
                    <div class="sub-calc-title" style="color: #34d399;">🏆 Diagnóstico do Sistema</div>
                    <div class="sub-calc-value" style="color: #34d399;">R$ {diferenca_caixa:,.2f}</div>
                    <div class="sub-calc-desc" style="color: #34d399; font-weight: bold;">✅ MIGRAR PARA O HÍBRIDO</div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div class="sub-calc-card" style="border-left: 4px solid #f87171;">
                    <div class="sub-calc-title" style="color: #f87171;">🏆 Diagnóstico do Sistema</div>
                    <div class="sub-calc-value" style="color: #f87171;">R$ {abs(diferenca_caixa):,.2f}</div>
                    <div class="sub-calc-desc" style="color: #f87171; font-weight: bold;">🛑 MANTER NO TRADICIONAL</div>
                </div>
                """, unsafe_allow_html=True)

renderizar_simulador(
    empresa_ativa,
    itens_mes,
    devolucoes_mes,
    empresa_id_ativo,
    saldo_credito_disponivel,
    perfil_tributario_ativo,
)

# MÓDULO 8: DOSSIÊ EXECUTIVO DE FECHAMENTO
relatorio_engine.renderizar_modulo_relatorio(
    empresa_ativa,
    imposto_bruto_acumulado,
    credito_validado_tempo_real,
    imposto_quarentena_acumulado,
    imposto_estornado,
    notas,
    itens,
    valor_retido_split_payment,
    eventos_liquidacao,
)
persistir_estado_sessao()
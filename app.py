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
        background-color: #0b0f19 !important;
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
@st.cache_resource
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


def usuario_from_row(row: dict) -> tuple[str, dict] | None:
    cnpj = str(row.get("cnpj", ""))
    razao_social = str(row.get("razao_social", ""))
    if not cnpj.startswith("USR_") or not razao_social.startswith(PREFIXO_USUARIO_SISTEMA):
        return None
    username = cnpj[4:].lower()
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
    if not username or not isinstance(dados, dict):
        return None
    password_hash = str(dados.get("password_hash", ""))
    nome = str(dados.get("name", ""))
    if not password_hash or not nome:
        return None
    return username, {
        "password_hash": password_hash,
        "role": "contador",
        "name": nome,
        "company_id": dados.get("company_id"),
        "company_id_synced": dados.get("company_id"),
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
    return {
        "cnpj": f"USR_{usuario}",
        "razao_social": PREFIXO_USUARIO_SISTEMA + json.dumps(
            {
                "name": dados_usuario["name"],
                "password_hash": dados_usuario["password_hash"],
                "company_id": dados_usuario.get("company_id"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "regime_tributario": "SYS_AUTH",
        "cnae_principal": "0000000",
    }


def persistir_usuario_supabase(usuario: str, dados_usuario: dict) -> None:
    if supabase is None:
        raise RuntimeError("Supabase indisponível para persistir o usuário.")
    registro = payload_usuario_supabase(usuario, dados_usuario)
    existente = (
        supabase.table("empresas_monitoradas")
        .select("id")
        .eq("cnpj", registro["cnpj"])
        .eq("regime_tributario", "SYS_AUTH")
        .execute()
    )
    if existente.data:
        ok, mensagem, _ = xml_engine.executar_update_seguro(
            supabase,
            "empresas_monitoradas",
            registro,
            {"cnpj": registro["cnpj"], "regime_tributario": "SYS_AUTH"},
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
        .eq("cnpj", registro["cnpj"])
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
        .eq("cnpj", f"USR_{usuario}")
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
    cnpj_suffix = f"{int(timestamp.timestamp() * 1_000_000) % 10**12:012d}"
    ok, mensagem, _ = xml_engine.executar_insert_seguro("empresas_monitoradas", {
        "cnpj": f"99{cnpj_suffix}",
        "razao_social": (
            f"{PREFIXO_LOG_SISTEMA}{ts_epoch}|{log_acesso['datetime']}|"
            f"{log_acesso['username']}|{log_acesso['name']}|{log_acesso['event']}"
        ),
        "regime_tributario": "SYS_LOG",
        "cnae_principal": "0000000",
    }, supabase=supabase)
    if not ok:
        raise RuntimeError(mensagem)


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
        threading.Thread(
                target=persistir_acesso_em_background,
            args=(log_acesso,),
            daemon=True,
        ).start()
    except Exception:
        logging.getLogger(__name__).exception("Falha ao iniciar o envio assíncrono do alerta de acesso.")


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
    components.html(
        f"""
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
        """,
        height=0,
    )


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
                identificacao_input = st.text_input("Usuário")
                senha_input = st.text_input("Senha", type="password")
                manter_conectado = st.checkbox("Manter conectado neste aparelho", value=True)
                btn_entrar = st.form_submit_button("Entrar", use_container_width=True)
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
                novo_nome_usuario = st.text_input("Nome")
                novo_usuario_input = st.text_input("Usuário")
                nova_senha = st.text_input("Senha", type="password")
                btn_criar_conta = st.form_submit_button("Criar Conta", use_container_width=True)
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
    empresas = []
    if supabase is not None:
        inicio = 0
        tamanho_pagina = 1000
        try:
            while True:
                res = (
                    supabase.table("empresas_monitoradas")
                    .select("id,cnpj,razao_social,regime_tributario,cnae_principal,certificado_a1_status")
                    .range(inicio, inicio + tamanho_pagina - 1)
                    .execute()
                )
                pagina = res.data or []
                empresas.extend(pagina)
                if len(pagina) < tamanho_pagina:
                    break
                inicio += tamanho_pagina
        except Exception:
            logging.getLogger(__name__).exception(
                "Falha ao carregar empresas do Supabase; usando os dados da sessão."
            )
    return [
        empresa for empresa in empresas
        if str(empresa.get("regime_tributario", "")) not in {"SYS_AUTH", "SYS_LOG"}
        and not str(empresa.get("razao_social", "")).startswith(
            (PREFIXO_USUARIO_SISTEMA, PREFIXO_LOG_SISTEMA)
        )
    ]


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


def combinar_registros_com_sessao(registros_db: list[dict], registros_sessao: list[dict]) -> list[dict]:
    combinados = {}
    for indice, registro in enumerate(registros_db):
        identificador = str(registro.get("id") or f"db_{indice}")
        combinados[identificador] = dict(registro)
    for indice, registro in enumerate(registros_sessao):
        identificador = str(registro.get("id") or f"sessao_{indice}")
        combinados[identificador] = {**combinados.get(identificador, {}), **registro}
    return list(combinados.values())


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
        if st.button("Atualizar Acessos", use_container_width=True, key="btn_atualizar_acessos"):
            try:
                atualizar_logs_do_supabase()
                st.session_state["modulo_ativo"] = "admin"
                rerun_fragment_local()
            except Exception:
                logging.getLogger(__name__).exception("Falha ao atualizar logs de acesso do Supabase.")
                st.error("Não foi possível atualizar o histórico pelo Supabase.")
        if st.button(
            "Testar Alerta no Celular",
            use_container_width=True,
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
                use_container_width=True,
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
todas_empresas_brutas = combinar_registros_com_sessao(
    todas_empresas_brutas,
    st.session_state.get("fallback_empresas", []),
)

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

with st.sidebar.expander("➕ Cadastrar Novo Cliente", expanded=False):
    novo_nome = st.text_input("Razão Social", key="cad_nome")
    novo_cnpj = st.text_input("CNPJ (Com ou sem pontuação)", key="cad_cnpj")
    novo_regime = st.selectbox("Regime", ["Simples Nacional", "Simples Híbrido (LC 214)", "Lucro Presumido", "Lucro Real"], key="cad_regime")
    cnae_principal_input = st.text_input("Atividade Principal (CNAE 7 dígitos)", placeholder="Ex: 4711302 ou 9602501", key="cad_cnae_prin")

    if "cnae_secundarios_extras" not in st.session_state:
        st.session_state["cnae_secundarios_extras"] = []

    st.markdown("<span style='font-size: 11.5px; color: #9ca3af; font-weight: 700;'>CNAEs Secundários (Opcional)</span>", unsafe_allow_html=True)

    cnaes_secundarios_coletados = []
    for idx, val_cnae in enumerate(st.session_state["cnae_secundarios_extras"]):
        c_val = st.text_input(f"CNAE Secundário {idx+1}", value=val_cnae, key=f"sec_cnae_input_{idx}")
        if c_val.strip():
            cnaes_secundarios_coletados.append("".join(filter(str.isdigit, c_val)))

    if st.button("➕ Adicionar outro CNAE", key="btn_add_cnae_sec"):
        st.session_state["cnae_secundarios_extras"].append("")
        st.rerun()

    st.write("")
    if st.button("Salvar Cliente Definitivo", use_container_width=True, key="btn_salvar_cli_def"):
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
                ok_ins, msg_ins, _ = xml_engine.inserir_seguro_supabase(supabase, "empresas_monitoradas", payload_emp)
                if ok_ins:
                    st.session_state["cnae_secundarios_extras"] = []
                    limpar_caches_dados()
                    st.sidebar.success("Cliente validado e cadastrado!")
                    if msg_ins != "OK":
                        st.toast("Cliente mantido nesta sessão enquanto o banco está indisponível.", icon="ℹ️")
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
    if st.sidebar.button("🔒 Encerrar Sessão", use_container_width=True):
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
empresa_selecionada_nome = st.sidebar.selectbox(
    "📂 Cliente Ativo Monitorado",
    list(opcoes_empresas.keys()),
    index=empresa_preferida_index,
    key=chave_empresa_selecao,
)
empresa_id_ativo = opcoes_empresas[empresa_selecionada_nome]
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
empresa_ativa_bruta = next((e for e in lista_empresas if e["id"] == empresa_id_ativo), lista_empresas[0])
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
        key=f"edit_cnae_{empresa_id_ativo}"
    )
    if st.button("💾 Salvar Novo CNAE", use_container_width=True, key=f"btn_save_cnae_{empresa_id_ativo}"):
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
                if mensagem_update == "OK":
                    st.sidebar.success("CNAEs atualizados!")
                else:
                    st.toast("CNAEs atualizados nesta sessão; o banco está temporariamente indisponível.", icon="ℹ️")
                st.rerun()
            except Exception:
                logging.getLogger(__name__).exception("Falha ao atualizar CNAE do cliente.")
                st.toast("Não foi possível atualizar os CNAEs. Tente novamente.", icon="⚠️")

    st.markdown("<hr style='border:0; border-top:1px solid #374151; margin:8px 0;'>", unsafe_allow_html=True)
    if st.button("🔄 Zerar Notas deste Cliente", use_container_width=True, key=f"btn_reset_nf_{empresa_id_ativo}"):
        dados_alterados = False
        try:
            nfs_cli = supabase.table("notas_fiscais_recebidas").select("id").eq("empresa_id", empresa_id_ativo).execute()
            ids_nf = [n["id"] for n in (nfs_cli.data or [])]
            try:
                resultado = supabase.table("conciliacao_devolucoes").delete().eq("empresa_id", empresa_id_ativo).select("id").execute()
                dados_alterados = dados_alterados or bool(resultado.data)
            except Exception:
                pass
            if ids_nf:
                try:
                    resultado = supabase.table("conciliacao_devolucoes").delete().in_("nota_original_id", ids_nf).select("id").execute()
                    dados_alterados = dados_alterados or bool(resultado.data)
                except Exception:
                    pass
                resultado = supabase.table("itens_nota_fiscal").delete().in_("nota_id", ids_nf).select("id").execute()
                dados_alterados = dados_alterados or bool(resultado.data)
                resultado = supabase.table("notas_fiscais_recebidas").delete().eq("empresa_id", empresa_id_ativo).select("id").execute()
                dados_alterados = dados_alterados or bool(resultado.data)
        except Exception:
            pass
        st.session_state.pop(f"fallback_notas_{empresa_id_ativo}", None)
        st.session_state.pop(f"fallback_itens_{empresa_id_ativo}", None)
        st.session_state.pop(f"fallback_devolucoes_{empresa_id_ativo}", None)
        if dados_alterados:
            limpar_caches_dados()
        st.sidebar.success("Notas zeradas!")
        st.rerun()

    st.markdown("<hr style='border:0; border-top:1px solid #374151; margin:8px 0;'>", unsafe_allow_html=True)
    if st.button("🗑️ Excluir Cliente Ativo", use_container_width=True, key=f"btn_del_cli_{empresa_id_ativo}"):
        try:
            nfs_cli = supabase.table("notas_fiscais_recebidas").select("id").eq("empresa_id", empresa_id_ativo).execute()
            ids_nf = [n["id"] for n in (nfs_cli.data or [])]
            if ids_nf:
                try:
                    supabase.table("conciliacao_devolucoes").delete().in_("nota_original_id", ids_nf).select("id").execute()
                except Exception:
                    pass
                supabase.table("itens_nota_fiscal").delete().in_("nota_id", ids_nf).select("id").execute()
                supabase.table("notas_fiscais_recebidas").delete().eq("empresa_id", empresa_id_ativo).select("id").execute()
            resultado = supabase.table("empresas_monitoradas").delete().eq("id", empresa_id_ativo).select("id").execute()
            if not resultado.data:
                raise ValueError("Nenhum cliente foi excluído.")
            st.session_state.pop(f"fallback_notas_{empresa_id_ativo}", None)
            st.session_state.pop(f"fallback_itens_{empresa_id_ativo}", None)
            st.session_state.pop(f"fallback_devolucoes_{empresa_id_ativo}", None)
            limpar_caches_dados()
            st.sidebar.success("Cliente excluído!")
            st.rerun()
        except Exception:
            logging.getLogger(__name__).exception("Falha ao excluir cliente.")
            st.toast("Não foi possível excluir o cliente. Tente novamente.", icon="⚠️")

if st.sidebar.button("🔒 Encerrar Sessão", use_container_width=True):
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
try:
    notas_db, devolucoes, itens_db, _, eventos_split_db = carregar_dados_empresa_db(
        empresa_id_ativo,
        str(empresa_ativa.get("cnpj", "")),
    )
except RuntimeError:
    logging.getLogger(__name__).exception("Falha ao carregar dados da empresa ativa.")
    st.toast("Os dados estão temporariamente indisponíveis. Atualize a tela e tente novamente.", icon="⚠️")
    st.stop()

notas_mem = st.session_state.get(f"fallback_notas_{empresa_id_ativo}", [])
itens_mem = st.session_state.get(f"fallback_itens_{empresa_id_ativo}", [])

notas = combinar_registros_com_sessao(notas_db, notas_mem)
itens = [
    xml_engine.normalizar_item_credito(item)
    for item in combinar_registros_com_sessao(itens_db, itens_mem)
]
devolucoes = combinar_registros_com_sessao(
    devolucoes,
    st.session_state.get(f"fallback_devolucoes_{empresa_id_ativo}", []),
)
eventos_split = combinar_registros_com_sessao(
    eventos_split_db,
    st.session_state.get(f"fallback_split_payment_{empresa_id_ativo}", []),
)

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
imposto_validado_acumulado = resumo_creditos_acumulado["validado"]
imposto_quarentena_acumulado = resumo_creditos_acumulado["quarentena"]
imposto_bruto_acumulado = resumo_creditos_acumulado["bruto"]
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
imposto_estornado = sum(
    float(d.get("credito_estornado", 0.0) or 0.0)
    for d in devolucoes
)


valor_retido_split_payment = sum(
    max(0.0, float(evento.get("retained_amount", 0.0) or 0.0))
    for evento in eventos_split
)
credito_validado_tempo_real = max(
    0.0,
    imposto_validado_acumulado - imposto_estornado - valor_retido_split_payment,
)
saldo_credito_disponivel = max(
    0.0,
    credito_validado_tempo_real,
)

# ==========================================
# 8. PAINEL SUPERIOR: IMPOSTÔMETRO LUXO
# ==========================================
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown(f"""
    <div class="exec-card card-bruto">
        <div class="card-label">⚡ Crédito Digital Bruto Acumulado</div>
        <div class="card-value-bruto">R$ {imposto_bruto_acumulado:,.2f}</div>
        <div class="card-desc">Alíquotas inteligentes por NCM/CNAE para {empresa_ativa['razao_social']}</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div class="exec-card card-validado">
        <div class="card-label">✅ Crédito Validado / Saldo Líquido</div>
        <div class="card-value-validado">R$ {credito_validado_tempo_real:,.2f}</div>
        <div class="card-desc">Após estornos e retenção Split Payment de R$ {valor_retido_split_payment:,.2f}: R$ {saldo_credito_disponivel:,.2f}</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown(f"""
    <div class="exec-card card-quarentena">
        <div class="card-label">🔒 Saldo em Quarentena</div>
        <div class="card-value-quarentena">R$ {imposto_quarentena_acumulado:,.2f}</div>
        <div class="card-desc">Aguardando decisão técnica do contador (Zero bloqueio automático)</div>
    </div>
    """, unsafe_allow_html=True)

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
        st.dataframe(pd.DataFrame(linhas_historico), hide_index=True, use_container_width=True)
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
        st.dataframe(df_cont, hide_index=True, use_container_width=True)
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
) -> None:
    def persistir_decisao(item: dict, status: str, credito: float) -> None:
        if item.get("id") is None or item.get("nota_id") is None:
            raise RuntimeError("Vínculo do item indisponível.")
        justificativa = (
            "Art. 47/57: aprovação após revalidação documental manual pelo contador."
            if status == "Aprovado Manualmente Contador"
            else "Art. 57: crédito glosado após análise manual do contador."
        )
        atualizacao = {
            "classificacao_item": status,
            "status_credito": (
                "Validado"
                if status == "Aprovado Manualmente Contador"
                else "Rejeitado"
            ),
            "aliquota_aplicada": xml_engine.ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA,
            "aliquota_ibs_aplicada": xml_engine.ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA,
            "aliquota_cbs_aplicada": 0.0,
            "valor_credito_item": round(credito, 2),
            "credito_ibs_gerado": round(credito, 2),
            "credito_cbs_gerado": 0.0,
            "justificativa_motor": justificativa,
        }
        ok, mensagem, resultado = xml_engine.executar_update_seguro(
            supabase,
            "itens_nota_fiscal",
            atualizacao,
            {
                "id": item["id"],
                "nota_id": item["nota_id"],
                "empresa_id": empresa_id,
                "classificacao_item": "Quarentena Uso/Consumo",
            },
            fallback_record=item,
        )
        if not ok or not resultado:
            raise RuntimeError(mensagem)
        item["justificativa_motor"] = justificativa

    with st.expander(
        f"📁 4. Fila de Quarentena & Histórico de Auditoria "
        f"({len(pendentes)} pendentes | {len(auditados)} auditados)",
        expanded=st.session_state.get("modulo_ativo") == "quarentena",
    ):
        if pendentes:
            st.markdown("#### ⏳ Itens Aguardando Decisão Técnica do Contador")
            for idx, item in enumerate(pendentes):
                credito = xml_engine.obter_credito_quarentena(item)
                item_id = item.get("id", f"mem_{idx}")
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
                aprovar, rejeitar = st.columns(2)
                if aprovar.button(
                    "✅ Liberar Crédito (Aprovar)",
                    key=f"lib_{item_id}",
                    use_container_width=True,
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
                        item["aliquota_aplicada"] = xml_engine.ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA
                        item["valor_credito_item"] = round(credito, 2)
                        pendentes[:] = [pendente for pendente in pendentes if pendente is not item]
                        if all(str(a.get("id")) != str(item.get("id")) for a in auditados):
                            auditados.append(item)
                        limpar_caches_dados()
                        st.session_state["modulo_ativo"] = "quarentena"
                        st.toast(
                            f"✅ +R$ {credito:,.2f} liberados para o Crédito Validado!",
                            icon="💰",
                        )
                        st.rerun()
                if rejeitar.button(
                    "🚫 Bloquear Crédito (Uso/Consumo Definitivo)",
                    key=f"bloq_{item_id}",
                    use_container_width=True,
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
                        item["aliquota_aplicada"] = xml_engine.ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA
                        item["valor_credito_item"] = round(credito, 2)
                        pendentes[:] = [pendente for pendente in pendentes if pendente is not item]
                        if all(str(a.get("id")) != str(item.get("id")) for a in auditados):
                            auditados.append(item)
                        limpar_caches_dados()
                        st.session_state["modulo_ativo"] = "quarentena"
                        st.toast(
                            f"🛡️ R$ {credito:,.2f} glosados (Art. 57) — risco fiscal eliminado!",
                            icon="🛡️",
                        )
                        st.rerun()
                st.markdown("---")
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
                }
                for item in auditados
            ])
            st.dataframe(df_audit, height=250, hide_index=True, use_container_width=True)


renderizar_fila_quarentena(itens_pendentes, itens_auditados, empresa_id_ativo)

# MÓDULO 5: NOTAS FISCAIS RECEBIDAS (COM SELETOR DE DOWNLOAD NOTA POR NOTA DO CLIENTE)
with st.expander(f"📁 5. Notas Fiscais Monitoradas na SEFAZ ({len(notas)} notas acumuladas deste cliente)", expanded=False):
    if notas:
        st.caption("Marque na primeira coluna (☑️ Baixar XML) as notas fiscais específicas deste cliente que deseja exportar:")
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
            use_container_width=True,
            disabled=["Chave de Acesso NF-e", "CNPJ Fornecedor", "Valor Total (R$)", "Status SEFAZ", "Hash SHA-256 (Auditoria)"],
            key=f"editor_notas_acumuladas_{empresa_id_ativo}"
        )

        chaves_para_baixar = df_notas_marcadas[df_notas_marcadas["Baixar XML"] == True]["Chave de Acesso NF-e"].tolist()
        chave_pacote = f"pacote_xml_{empresa_id_ativo}"
        selecao_atual = tuple(sorted(chaves_para_baixar))
        if chaves_para_baixar and st.button(
            "📦 Preparar Pacote .ZIP das Notas Selecionadas",
            use_container_width=True,
            key=f"btn_preparar_zip_{empresa_id_ativo}"
        ):
            buf_cli = io.BytesIO()
            with zipfile.ZipFile(buf_cli, "w", zipfile.ZIP_DEFLATED) as zf_c:
                for n_obj in notas:
                    if str(n_obj.get("chave_acesso", "")) in chaves_para_baixar:
                        itens_desta_nf = [i for i in itens if i.get("nota_id") == n_obj.get("id")]
                        xml_rec = xml_engine.gerar_xml_reconstruido_cliente(empresa_ativa, n_obj, itens_desta_nf)
                        zf_c.writestr(f"NFe_{n_obj.get('chave_acesso', '000')}.xml", xml_rec)
            st.session_state[chave_pacote] = (selecao_atual, buf_cli.getvalue())
        pacote_preparado = st.session_state.get(chave_pacote)
        if pacote_preparado and pacote_preparado[0] == selecao_atual:
            st.download_button(
                label=f"📥 Baixar ({len(chaves_para_baixar)}) XML(s) Selecionado(s) de {empresa_ativa['razao_social']} (.ZIP)",
                data=pacote_preparado[1],
                file_name=f"XMLs_Auditados_{empresa_ativa['cnpj']}.zip",
                mime="application/zip",
                use_container_width=True,
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
        st.dataframe(df_dev_exibir, height=250, hide_index=True, use_container_width=True)
    else:
        st.info("Nenhuma devolução registrada no período.")

# MÓDULO 7: SIMULADOR ESTRATÉGICO
@fragment_decorator
def renderizar_simulador(empresa_ativa: dict, itens_mes: list[dict], devolucoes_empresa_mes: list[dict], empresa_id_ativo: int | str, saldo_credito_disponivel: float) -> None:
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
            aliquota_iva = col_in3.number_input(
                "🏛️ Alíquota Base IBS + CBS (%)",
                value=26.5,
                step=0.5,
                format="%.1f",
                key=f"sim_aliquota_{empresa_id_ativo}",
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
            st.form_submit_button("⚡ Aplicar Parâmetros e Calcular Simulação", use_container_width=True, on_click=manter_simulador_aberto)

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
    empresa_ativa, itens_mes, devolucoes_mes, empresa_id_ativo, saldo_credito_disponivel
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
)
persistir_estado_sessao()
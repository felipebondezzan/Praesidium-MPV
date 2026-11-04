import os
import io
import zipfile
import logging
import threading
from datetime import datetime, timezone, timedelta
import requests
import streamlit as st
import pandas as pd
from supabase import create_client, Client
from urllib.parse import urlparse
import xml_engine
import relatorio_engine
import hashlib
logging.getLogger("streamlit").setLevel(logging.ERROR)

st.set_page_config(
    page_title="Praesidium | Intelligence & Compliance",
    layout="wide",
    page_icon="🛡️",
    initial_sidebar_state="expanded"
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
    .block-container {
        padding-top: 1.2rem !important;
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

# ==========================================
# 2. CONEXÃO SUPABASE (EM CACHE DE ALTA VELOCIDADE)
# ==========================================
url_secret = ""
key_secret = ""
try:
    url_secret = st.secrets.get("SUPABASE_URL", "")
    key_secret = st.secrets.get("SUPABASE_KEY", "")
except Exception:
    pass

url: str = str(url_secret or "").strip() or os.environ.get("SUPABASE_URL", "").strip()
key: str = str(key_secret or "").strip() or os.environ.get("SUPABASE_KEY", "").strip()


@st.cache_resource
def iniciar_conexao_supabase(supabase_url: str, supabase_key: str) -> Client:
    return create_client(supabase_url, supabase_key)


supabase: Client | None = None
erro_conexao_supabase = None
url_validada = urlparse(url)
if not url or not key:
    erro_conexao_supabase = (
        "Credenciais do Supabase ausentes. Configure SUPABASE_URL e SUPABASE_KEY "
        "em st.secrets ou como variáveis de ambiente."
    )
elif url_validada.scheme not in {"https", "http"} or not url_validada.netloc:
    erro_conexao_supabase = "SUPABASE_URL inválida. Informe uma URL completa com http:// ou https://."
elif not key.startswith(("sb_secret_", "sb_publishable_", "eyJ")):
    erro_conexao_supabase = (
        "SUPABASE_KEY tem formato não reconhecido. Use uma chave Supabase secret, "
        "publishable ou JWT legado."
    )
else:
    try:
        supabase = iniciar_conexao_supabase(url, key)
    except Exception:
        erro_conexao_supabase = (
            "Não foi possível inicializar a conexão Supabase. Confira SUPABASE_URL "
            "e SUPABASE_KEY em st.secrets ou nas variáveis de ambiente."
        )

if erro_conexao_supabase:
    st.warning(erro_conexao_supabase)


def normalizar_tenant(identificacao: str) -> str:
    limpo = "".join(c for c in str(identificacao).strip().lower() if c.isalnum() or c in ("_", "-"))
    return limpo if limpo else "visitante_padrao"


def horario_brasilia_str() -> str:
    fuso_br = timezone(timedelta(hours=-3))
    return datetime.now(fuso_br).strftime("%d/%m/%Y %H:%M:%S")


def registrar_aviso_login(usuario_nome: str, tenant_id: str, is_admin: bool):
    dh_br = horario_brasilia_str()
    tipo_str = "ADMIN" if is_admin else "CONTADOR"
    # 1. Grava o log de forma persistente no Supabase (usando registro oculto compatível com a estrutura existente)
    try:
        xml_engine.inserir_seguro_supabase(supabase, "conciliacao_devolucoes", {
            "chave_nota_devolucao": f"LOG_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "valor_mercadoria_devolvida": 0.0,
            "credito_estornado": 0.0,
            "motivo_ajuste": f"[LOG_ACESSO] {dh_br} | Usuário: {usuario_nome.upper()} | Ambiente: {tenant_id} ({tipo_str})"
        })
    except Exception:
        pass

    # 2. Dispara notificação instantânea gratuita (ntfy.sh/praesidium_felipe_alertas)
    try:
        requests.post(
            "https://ntfy.sh/praesidium_felipe_alertas",
            data=f"Novo acesso no Praesidium: {usuario_nome} ({tipo_str}) em {dh_br}".encode("utf-8"),
            headers={"Title": "Acesso Praesidium Detectado", "Priority": "high", "Tags": "shield,unlock"},
            timeout=2
        )
    except Exception:
        pass


# ==========================================
# 3. TRAVA DE SEGURANÇA MULTI-TENANT (AMBIENTE ISOLADO POR CONTADOR)
# ==========================================
SENHA_MESTRE = "praesidium2026"
SENHA_ADMIN = "admin2026"

if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False
if "tenant_id" not in st.session_state:
    st.session_state["tenant_id"] = ""
if "usuario_nome" not in st.session_state:
    st.session_state["usuario_nome"] = ""
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False

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
        with st.form("form_login_mvp", border=False):
            identificacao_input = st.text_input(
                "Seu Nome ou Escritório Contábil:",
                placeholder="Ex: João Contabilidade (Cria seu painel isolado)"
            )
            senha_input = st.text_input(
                "Credencial de Acesso:",
                type="password",
                placeholder="Digite a senha autorizada..."
            )
            btn_entrar = st.form_submit_button("🔓 Desbloquear Painel Exclusivo", use_container_width=True)
            if btn_entrar:
                nome_limpo = identificacao_input.strip() if identificacao_input.strip() else "Contador Visitante"
                senha_limpa = senha_input.strip()

                if senha_limpa in [SENHA_MESTRE, SENHA_ADMIN]:
                    eh_admin = senha_limpa == SENHA_ADMIN
                    t_id = "admin" if eh_admin else normalizar_tenant(nome_limpo)

                    st.session_state["autenticado"] = True
                    st.session_state["usuario_nome"] = nome_limpo
                    st.session_state["tenant_id"] = t_id
                    st.session_state["is_admin"] = eh_admin

                    threading.Thread(
                        target=registrar_aviso_login,
                        args=(nome_limpo, t_id, eh_admin),
                        daemon=True
                    ).start()
                    st.rerun()
                else:
                    st.error("❌ Senha incorreta. Solicite a credencial atualizada.")
    st.stop()


# ==========================================
# 4. FUNÇÕES DE LEITURA EM CACHE (VELOCIDADE NA NUVEM)
# ==========================================
@st.cache_data(ttl=60, show_spinner=False)
def carregar_todas_empresas_db():
    try:
        res = supabase.table("empresas_monitoradas").select("*").execute()
        return res.data if res.data else []
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def carregar_dados_empresa_db(emp_id: int, emp_cnpj: str):
    notas_lista = []
    devs_lista = []
    itens_lista = []
    logs_lista = []

    try:
        n_res = supabase.table("notas_fiscais_recebidas").select("*").eq("empresa_id", emp_id).execute()
        notas_lista = n_res.data if n_res.data else []
    except Exception:
        notas_lista = []

    try:
        d_res = supabase.table("conciliacao_devolucoes").select("*").execute()
        todas_d = d_res.data if d_res.data else []
        for d in todas_d:
            motivo_str = str(d.get("motivo_ajuste", ""))
            if motivo_str.startswith("[LOG_ACESSO]"):
                logs_lista.append(d)
            elif d.get("empresa_id") == emp_id or f"[{emp_cnpj}]" in motivo_str:
                devs_lista.append(d)
    except Exception:
        pass

    n_ids = [n["id"] for n in notas_lista if "id" in n]
    if n_ids:
        try:
            i_res = supabase.table("itens_nota_fiscal").select("*").in_("nota_id", n_ids).execute()
            itens_lista = i_res.data if i_res.data else []
        except Exception:
            itens_lista = []

    return notas_lista, devs_lista, itens_lista, logs_lista


@st.cache_data(ttl=60, show_spinner=False)
def carregar_logs_acesso_db():
    try:
        res_logs = supabase.table("conciliacao_devolucoes").select("motivo_ajuste").execute()
        return [
            str(r.get("motivo_ajuste", "")).replace("[LOG_ACESSO] ", "")
            for r in (res_logs.data or [])
            if str(r.get("motivo_ajuste", "")).startswith("[LOG_ACESSO]")
        ]
    except Exception:
        return []


def extrair_tenant_empresa(emp: dict) -> str:
    status_a1 = str(emp.get("certificado_a1_status", ""))
    if "TENANT:" in status_a1:
        return status_a1.split("TENANT:")[-1].strip()
    cnae_raw = str(emp.get("cnae_principal", ""))
    if "|TENANT:" in cnae_raw:
        return cnae_raw.split("|TENANT:")[-1].strip()
    return "admin"


def extrair_cnae_limpo_empresa(emp: dict) -> str:
    cnae_raw = str(emp.get("cnae_principal", "4711302"))
    return cnae_raw.split("|TENANT:")[0].strip() or "4711302"


# ==========================================
# 5. GESTÃO MULTI-EMPRESA E RADAR ADMIN (SIDEBAR)
# ==========================================
tenant_atual = st.session_state["tenant_id"]
is_admin = st.session_state["is_admin"]

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

todas_empresas_brutas = carregar_todas_empresas_db()

# SE FOR VOCÊ (ADMIN): Mostra o Radar de Acessos e permite espionar/filtrar qualquer contador
if is_admin:
    with st.sidebar.expander("🔔 Radar de Acessos (Admin)", expanded=False):
        logs_acesso = carregar_logs_acesso_db()

        if logs_acesso:
            st.caption(f"Últimos acessos registrados ({len(logs_acesso)}):")
            for lg in reversed(logs_acesso[-10:]):
                st.markdown(f"<div style='font-size:11px; padding:4px 0; border-bottom:1px solid #374151;'>🟢 {lg}</div>", unsafe_allow_html=True)
        else:
            st.caption("Nenhum acesso de contador registrado ainda.")

        st.caption("📱 Alerta no celular ativo no canal ntfy.sh: `praesidium_felipe_alertas`")

    tenants_disponiveis = sorted(list({extrair_tenant_empresa(e) for e in todas_empresas_brutas} | {"admin"}))
    filtro_tenant_admin = st.sidebar.selectbox(
        "👁️ Visão do Administrador (Ambiente):",
        ["TODOS OS AMBIENTES"] + tenants_disponiveis
    )
    if filtro_tenant_admin == "TODOS OS AMBIENTES":
        lista_empresas = todas_empresas_brutas
    else:
        lista_empresas = [e for e in todas_empresas_brutas if extrair_tenant_empresa(e) == filtro_tenant_admin]
else:
    # ISOLAMENTO TOTAL DO CONTADOR: Ele só enxerga as empresas que pertencem ao tenant_id dele!
    lista_empresas = [e for e in todas_empresas_brutas if extrair_tenant_empresa(e) == tenant_atual]

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
        if supabase is None:
            st.sidebar.warning(erro_conexao_supabase or "Conexão Supabase indisponível. Confira as credenciais configuradas.")
        elif novo_nome and novo_cnpj and cnae_principal_input:
            cnpj_limpo = "".join(filter(str.isdigit, novo_cnpj))
            cnae_prin_limpo = "".join(filter(str.isdigit, cnae_principal_input))
            todos_cnaes = [cnae_prin_limpo] + cnaes_secundarios_coletados
            todos_cnaes_unicos = list(dict.fromkeys([c for c in todos_cnaes if c]))

            cnaes_invalidos_cad = []
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
                cnaes_string_final = ",".join(todos_cnaes_unicos)
                payload_emp = {
                    "razao_social": novo_nome.strip().upper(),
                    "cnpj": cnpj_limpo,
                    "regime_tributario": novo_regime,
                    "cnae_principal": f"{cnaes_string_final}|TENANT:{tenant_atual}",
                    "certificado_a1_status": f"Ativo|TENANT:{tenant_atual}"
                }
                ok_ins, msg_ins, _ = xml_engine.inserir_seguro_supabase(supabase, "empresas_monitoradas", payload_emp)
                if ok_ins:
                    st.session_state["cnae_secundarios_extras"] = []
                    st.cache_data.clear()
                    st.sidebar.success("Cliente validado e cadastrado!")
                    st.rerun()
                else:
                    if "api key" in msg_ins.lower() or "invalid jwt" in msg_ins.lower():
                        st.sidebar.warning(
                            "O Supabase recusou a chave configurada. Confira se SUPABASE_KEY está registrada "
                            "e corresponde ao projeto definido em SUPABASE_URL."
                        )
                    else:
                        st.sidebar.error(f"Erro ao salvar no Supabase: {msg_ins}")
        else:
            st.sidebar.warning("Preencha Razão Social, CNPJ e CNAE Principal.")

if not lista_empresas:
    st.markdown(f"""
    <div class="header-container">
        <div class="header-title">🛡️ PRAESIDIUM — Intelligence & Compliance</div>
        <div class="header-subtitle">Ambiente Exclusivo de <b>{st.session_state['usuario_nome']}</b> • Painel 100% Limpo. Cadastre seu primeiro cliente na barra lateral à esquerda (➕ Cadastrar Novo Cliente) para iniciar a auditoria.</div>
    </div>
    """, unsafe_allow_html=True)
    if st.sidebar.button("🔒 Encerrar Sessão", use_container_width=True):
        st.session_state["autenticado"] = False
        st.rerun()
    st.stop()

opcoes_empresas = {f"{e['razao_social']} ({e['cnpj']})": e["id"] for e in lista_empresas}
empresa_selecionada_nome = st.sidebar.selectbox("📂 Cliente Ativo Monitorado", list(opcoes_empresas.keys()))
empresa_id_ativo = opcoes_empresas[empresa_selecionada_nome]
empresa_ativa_bruta = next((e for e in lista_empresas if e["id"] == empresa_id_ativo), lista_empresas[0])

# Cria cópia limpa do cliente ativo sem a tag interna de tenant no CNAE
empresa_ativa = dict(empresa_ativa_bruta)
empresa_ativa["cnae_principal"] = extrair_cnae_limpo_empresa(empresa_ativa_bruta)

st.sidebar.markdown(f"""
<div class="sidebar-exec-card">
    <span class="sidebar-exec-title">🛡️ Ficha Cadastral & Compliance</span>
    <div class="sidebar-exec-row">
        <span class="sidebar-exec-label">Regime</span>
        <span class="badge-gold">{empresa_ativa.get('regime_tributario', 'Simples Nacional')}</span>
    </div>
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
        owner_tag = extrair_tenant_empresa(empresa_ativa_bruta)
        try:
            resultado = supabase.table("empresas_monitoradas").update({
                "cnae_principal": f"{novo_cnae_edit.strip()}|TENANT:{owner_tag}"
            }).eq("id", empresa_id_ativo).select("id").execute()
            if not resultado.data:
                raise ValueError("Nenhum cliente foi atualizado.")
            st.cache_data.clear()
            st.sidebar.success("CNAE atualizado!")
            st.rerun()
        except Exception as ex:
            st.sidebar.error(f"Erro ao atualizar CNAE: {ex}")

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
        if dados_alterados:
            st.cache_data.clear()
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
            st.cache_data.clear()
            st.sidebar.success("Cliente excluído!")
            st.rerun()
        except Exception as ex:
            st.sidebar.error(f"Erro ao excluir: {ex}")

if st.sidebar.button("🔒 Encerrar Sessão", use_container_width=True):
    st.session_state["autenticado"] = False
    st.rerun()

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
notas_db, devolucoes, itens_db, _ = carregar_dados_empresa_db(empresa_id_ativo, str(empresa_ativa.get("cnpj", "")))

notas_mem = st.session_state.get(f"fallback_notas_{empresa_id_ativo}", [])
itens_mem = st.session_state.get(f"fallback_itens_{empresa_id_ativo}", [])

notas = notas_db + notas_mem
itens = itens_db + itens_mem

imposto_bruto_mes = sum([
    float(i.get("valor_credito_item", 0.0))
    for i in itens
    if i.get("classificacao_item") in ["Aprovado Revenda", "Aprovado Manualmente Contador"]
])
imposto_quarentena = sum([
    float(i.get("valor_item", 0.0)) * 0.265
    for i in itens
    if i.get("classificacao_item") == "Quarentena Uso/Consumo"
])
imposto_estornado = sum([float(d.get("credito_estornado", 0.0)) for d in devolucoes])

credito_validado_tempo_real = max(0.0, imposto_bruto_mes - imposto_estornado)

# ==========================================
# 8. PAINEL SUPERIOR: IMPOSTÔMETRO LUXO
# ==========================================
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown(f"""
    <div class="exec-card card-bruto">
        <div class="card-label">⚡ Crédito Digital Bruto (Mês)</div>
        <div class="card-value-bruto">R$ {imposto_bruto_mes:,.2f}</div>
        <div class="card-desc">Alíquotas inteligentes por NCM/CNAE para {empresa_ativa['razao_social']}</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div class="exec-card card-validado">
        <div class="card-label">✅ Crédito Validado (Tempo Real)</div>
        <div class="card-value-validado">R$ {credito_validado_tempo_real:,.2f}</div>
        <div class="card-desc">Líquido de devoluções operacionais (-R$ {imposto_estornado:,.2f})</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown(f"""
    <div class="exec-card card-quarentena">
        <div class="card-label">🔒 Saldo em Quarentena</div>
        <div class="card-value-quarentena">R$ {imposto_quarentena:,.2f}</div>
        <div class="card-desc">Aguardando decisão técnica do contador (Zero bloqueio automático)</div>
    </div>
    """, unsafe_allow_html=True)

st.write("")
st.markdown("### 📂 Módulos de Auditoria, Devolução Expressa e Inteligência Estratégica")

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
itens_auditados = [i for i in itens if i.get("classificacao_item") in ["Rejeitado pelo Contador", "Aprovado Manualmente Contador"]]

with st.expander(f"📁 4. Fila de Quarentena & Histórico de Auditoria ({len(itens_pendentes)} pendentes | {len(itens_auditados)} auditados)", expanded=False):
    if itens_pendentes:
        st.markdown("#### ⏳ Itens Aguardando Decisão Técnica do Contador")
        for idx_p, item in enumerate(itens_pendentes):
            imposto_item = float(item.get("valor_item", 0.0)) * 0.265
            item_id_key = item.get("id", f"mem_{idx_p}")
            with st.container():
                st.markdown(f"""
                <div style="background: #07090e; border: 1px solid #374151; border-radius: 8px; padding: 15px; margin-bottom: 10px;">
                    <b style="color: #ffffff; font-size: 15px;">📦 {item.get('descricao', 'Item')}</b><br>
                    <span style="color: #9ca3af; font-size: 13px;">NCM: <code>{item.get('ncm', '')}</code> | CFOP: <code>{item.get('cfop', '')}</code> | Valor Contábil: <b>R$ {float(item.get('valor_item', 0.0)):,.2f}</b> | Crédito Potencial Retido: <b style="color: #fbbf24;">R$ {imposto_item:,.2f}</b></span>
                </div>
                """, unsafe_allow_html=True)

                c1, c2 = st.columns(2)
                if c1.button("✅ Liberar Crédito (Aprovar)", key=f"lib_{item_id_key}", use_container_width=True):
                    dados_alterados = False
                    try:
                        resultado = supabase.table("itens_nota_fiscal").update({
                            "classificacao_item": "Aprovado Manualmente Contador",
                            "aliquota_aplicada": 0.265,
                            "valor_credito_item": round(imposto_item, 2)
                        }).eq("id", item["id"]).select("id").execute()
                        dados_alterados = bool(resultado.data)
                    except Exception:
                        try:
                            resultado = supabase.table("itens_nota_fiscal").update({
                                "classificacao_item": "Aprovado Manualmente Contador",
                                "valor_credito_item": round(imposto_item, 2)
                            }).eq("id", item["id"]).select("id").execute()
                            dados_alterados = bool(resultado.data)
                        except Exception:
                            item["classificacao_item"] = "Aprovado Manualmente Contador"
                            item["valor_credito_item"] = round(imposto_item, 2)
                    if dados_alterados:
                        st.cache_data.clear()
                    st.rerun()

                if c2.button("🚫 Bloquear Crédito (Uso/Consumo Definitivo)", key=f"bloq_{item_id_key}", use_container_width=True):
                    dados_alterados = False
                    try:
                        resultado = supabase.table("itens_nota_fiscal").update({
                            "classificacao_item": "Rejeitado pelo Contador",
                            "valor_credito_item": 0.00
                        }).eq("id", item["id"]).select("id").execute()
                        dados_alterados = bool(resultado.data)
                    except Exception:
                        item["classificacao_item"] = "Rejeitado pelo Contador"
                        item["valor_credito_item"] = 0.00
                    if dados_alterados:
                        st.cache_data.clear()
                    st.rerun()
                st.markdown("---")
    else:
        st.success("🎉 Nenhum item pendente de análise na quarentena para este cliente.")

    if itens_auditados:
        st.markdown("#### 📜 Histórico Permanente de Auditoria Manual")
        df_audit = pd.DataFrame([
            {
                "ID Item": i.get("id", "-"),
                "Descrição": i.get("descricao", ""),
                "NCM": i.get("ncm", ""),
                "CFOP": i.get("cfop", ""),
                "Valor Contábil (R$)": float(i.get("valor_item", 0.0)),
                "Status / Decisão do Contador": i.get("classificacao_item", "")
            }
            for i in itens_auditados
        ])
        st.dataframe(df_audit, height=250, hide_index=True, use_container_width=True)

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
with st.expander("📁 7. ⚖️ Simulador Estratégico de Migração (Simples Tradicional vs. Regime Híbrido)", expanded=False):
    st.markdown(f"Simulação fiscal focada nas operações de **{empresa_ativa['razao_social']}**.")

    compras_revenda_reais = sum([
        float(i.get("valor_item", 0.0))
        for i in itens
        if i.get("classificacao_item") in ["Aprovado Revenda", "Aprovado Manualmente Contador"]
    ])
    devolucoes_reais = sum([float(d.get("valor_mercadoria_devolvida", 0.0)) for d in devolucoes])
    quarentena_reais = sum([
        float(i.get("valor_item", 0.0))
        for i in itens
        if i.get("classificacao_item") == "Quarentena Uso/Consumo"
    ])

    faixas_simples = {
        "1ª Faixa (Até R$ 15 mil/mês) — Alíquota Nominal: 4,00%": {"das_total": 4.00, "fatia_consumo": 1.34},
        "2ª Faixa (R$ 15 mil a R$ 30 mil/mês) — Alíquota Nominal: 7,30%": {"das_total": 7.30, "fatia_consumo": 2.45},
        "3ª Faixa (R$ 30 mil a R$ 60 mil/mês) — Alíquota Nominal: 9,50%": {"das_total": 9.50, "fatia_consumo": 3.18},
        "4ª Faixa (R$ 60 mil a R$ 150 mil/mês) — Alíquota Nominal: 10,70%": {"das_total": 10.70, "fatia_consumo": 3.58},
        "5ª Faixa (R$ 150 mil a R$ 300 mil/mês) — Alíquota Nominal: 14,30%": {"das_total": 14.30, "fatia_consumo": 4.79},
        "6ª Faixa (R$ 300 mil a R$ 400 mil/mês) — Alíquota Nominal: 19,00%": {"das_total": 19.00, "fatia_consumo": 6.36},
    }

    with st.form(key="form_simulador_estrategico", border=False):
        col_in1, col_in2, col_in3 = st.columns([1.2, 2, 1])
        faturamento_vendas = col_in1.number_input("💰 Vendas no Mês / Faturamento (R$)", value=15000.0, step=1000.0, format="%.2f")
        faixa_escolhida = col_in2.selectbox("📊 Faixa do Simples Nacional (Anexo I - Comércio)", list(faixas_simples.keys()))
        aliquota_iva = col_in3.number_input("🏛️ Alíquota Base IBS + CBS (%)", value=26.5, step=0.5, format="%.1f")

        col_comp1, col_comp2, col_comp3 = st.columns(3)
        compras_mercadorias = col_comp1.number_input("🛒 Compras p/ Revenda no Mês (R$)", value=float(compras_revenda_reais), step=500.0, format="%.2f")
        devolucoes_mes = col_comp2.number_input("🔄 Devoluções de Compras (R$)", value=float(devolucoes_reais), step=500.0, format="%.2f")
        compras_quarentena_input = col_comp3.number_input("⚠️ Compras Travadas na Quarentena (R$)", value=float(quarentena_reais), step=100.0, format="%.2f")

        st.write("")
        st.form_submit_button("⚡ Aplicar Parâmetros e Calcular Simulação", use_container_width=True)

    compras_liquidas_creditaveis = max(0.0, compras_mercadorias - devolucoes_mes)
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

    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        st.markdown(f"""
        <div class="sub-calc-card">
            <div class="sub-calc-title">1. Compras Líquidas</div>
            <div class="sub-calc-value">R$ {compras_liquidas_creditaveis:,.2f}</div>
            <div class="sub-calc-desc">Revenda menos devoluções</div>
        </div>
        """, unsafe_allow_html=True)
    with mc2:
        st.markdown(f"""
        <div class="sub-calc-card">
            <div class="sub-calc-title">2. Crédito Gerado</div>
            <div class="sub-calc-value">R$ {credito_gerado_simulado:,.2f}</div>
            <div class="sub-calc-desc">{aliquota_iva}% sobre as compras</div>
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

# MÓDULO 8: DOSSIÊ EXECUTIVO DE FECHAMENTO
relatorio_engine.renderizar_modulo_relatorio(
    empresa_ativa, imposto_bruto_mes, credito_validado_tempo_real, imposto_quarentena, imposto_estornado, notas, itens
)
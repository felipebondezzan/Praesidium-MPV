import os
import io
import zipfile
import logging
import streamlit as st
import pandas as pd
from supabase import create_client, Client
import xml_engine
import relatorio_engine

logging.getLogger("streamlit").setLevel(logging.ERROR)

st.set_page_config(page_title="Praesidium | Intelligence & Compliance", layout="wide", page_icon="🛡️")

# ==========================================
# 1. DESIGN SYSTEM "DARK LUXURY" + OCULTAR "PRESS ENTER"
# ==========================================
st.markdown("""
<style>
    /* OCULTA LITERALMENTE TODOS OS TEXTOS "PRESS ENTER TO APPLY / SUBMIT" DAS CAIXAS */
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

    /* PROÍBE O STREAMLIT DE ESCURECER A PÁGINA AO ABRIR CAIXAS OU CRIAR CAMPOS NOVOS */
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

    /* REMOVE QUALQUER OVERLAY / FUNDO ESCURO AO ABRIR SELECTBOX, MULTISELECT OU CALENDÁRIO */
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
        height: 1.2rem !important;
        background: transparent !important;
    }
    .stApp {
        background-color: #0b0f19 !important;
        color: #ffffff !important;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    [data-testid="stSidebar"] {
        background-color: #111827 !important;
        border-right: 1px solid #374151 !important;
    }
    [data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] label {
        color: #f3f4f6 !important;
    }

    /* BORDA DOURADA LIMPA E SEM SOMBRA ESCURA EM TODOS OS INPUTS E CAIXAS DE SELEÇÃO */
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
        font-size: 26px;
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
        padding: 22px;
        text-align: left;
        position: relative;
        overflow: hidden;
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
        font-size: 30px;
        font-weight: 900;
        font-family: 'Courier New', monospace;
        margin: 8px 0 4px 0;
    }
    .card-value-validado {
        color: #34d399 !important;
        font-size: 30px;
        font-weight: 900;
        font-family: 'Courier New', monospace;
        margin: 8px 0 4px 0;
    }
    .card-value-quarentena {
        color: #fbbf24 !important;
        font-size: 30px;
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
        padding: 18px;
        text-align: left;
        min-height: 140px;
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
        font-size: 24px;
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
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. TRAVA DE SEGURANÇA QUE SOME APÓS DIGITAR A SENHA
# ==========================================
SENHA_MESTRE = "praesidium2026"  # Altere aqui quando quiser cortar o acesso de teste

if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False

if not st.session_state["autenticado"]:
    st.markdown("""
    <div style="background: #111827; border: 1px solid #d4af37; border-left: 5px solid #d4af37; border-radius: 12px; padding: 36px; max-width: 520px; margin: 70px auto 20px auto; text-align: center;">
        <div style="font-size: 36px; margin-bottom: 8px;">🛡️</div>
        <h2 style="color: #ffffff; margin: 0 0 8px 0; font-weight: 800;">PRAESIDIUM</h2>
        <div style="color: #d4af37; font-size: 12px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; margin-bottom: 18px;">Intelligence & Compliance • LC 214/2025</div>
        <p style="color: #9ca3af; font-size: 13.5px; line-height: 1.5; margin-bottom: 0;">Ambiente de homologação protegido. Insira a credencial autorizada abaixo para desbloquear o motor de auditoria.</p>
    </div>
    """, unsafe_allow_html=True)

    col_esq, col_centro, col_dir = st.columns([1, 1.3, 1])
    with col_centro:
        with st.form("form_login_mvp", border=False):
            senha_input = st.text_input("Credencial de Acesso:", type="password", placeholder="Digite a senha autorizada...")
            btn_entrar = st.form_submit_button("🔓 Desbloquear Painel", use_container_width=True)
            if btn_entrar:
                if senha_input == SENHA_MESTRE:
                    st.session_state["autenticado"] = True
                    st.rerun()
                else:
                    st.error("❌ Senha incorreta. Solicite a credencial atualizada.")
    st.stop()

# ==========================================
# 3. CREDENCIAIS DO SUPABASE (EM CACHE)
# ==========================================
url: str = os.environ.get("SUPABASE_URL", "https://pzgkdktkccunmpckcgoh.supabase.co")
key: str = os.environ.get("SUPABASE_KEY", "sb_secret_7rbhj1b5AkqdC6B4yd-oXQ_b0kmrk8u")
try:
    if "SUPABASE_URL" in st.secrets:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
except Exception:
    pass


@st.cache_resource
def iniciar_conexao_supabase(supabase_url: str, supabase_key: str) -> Client:
    return create_client(supabase_url, supabase_key)


supabase: Client = iniciar_conexao_supabase(url, key)

# ==========================================
# 4. GESTÃO MULTI-EMPRESA (SIDEBAR DE AUDITORIA)
# ==========================================
st.sidebar.markdown("""
<div style="display:flex; align-items:center; gap:10px; margin-bottom:4px;">
    <span style="font-size:24px;">🏢</span>
    <div>
        <div style="font-weight:800; font-size:17px; color:#ffffff; letter-spacing:0.3px;">Painel do Escritório</div>
        <div style="font-size:11.5px; color:#d4af37 !important; font-weight:600;">Governança Tributária • LC 214/2025</div>
    </div>
</div>
<hr style="border:0; border-top:1px solid rgba(212,175,55,0.25); margin:10px 0 14px 0;">
""", unsafe_allow_html=True)

with st.sidebar.expander("➕ Cadastrar Novo Cliente"):
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
                try:
                    supabase.table("empresas_monitoradas").insert({
                        "razao_social": novo_nome.strip().upper(),
                        "cnpj": cnpj_limpo,
                        "regime_tributario": novo_regime,
                        "cnae_principal": cnaes_string_final,
                        "certificado_a1_status": "Ativo"
                    }).execute()
                    st.session_state["cnae_secundarios_extras"] = []
                    st.sidebar.success("Cliente validado e cadastrado!")
                    st.rerun()
                except Exception as ex:
                    st.sidebar.error(f"Erro ao salvar no Supabase: {ex}")
        else:
            st.sidebar.warning("Preencha Razão Social, CNPJ e CNAE Principal.")

empresas_res = supabase.table("empresas_monitoradas").select("id, razao_social, cnpj, regime_tributario.").execute()
lista_empresas = empresas_res.data if empresas_res.data else []

if not lista_empresas:
    st.markdown("""
    <div class="header-container">
        <div class="header-title">🛡️ PRAESIDIUM — Intelligence & Compliance</div>
        <div class="header-subtitle">Ambiente de Homologação Limpo • Cadastre o primeiro cliente na barra lateral à esquerda (➕ Cadastrar Novo Cliente) para iniciar a auditoria.</div>
    </div>
    """, unsafe_allow_html=True)
    if st.sidebar.button("🔒 Encerrar Sessão", use_container_width=True):
        st.session_state["autenticado"] = False
        st.rerun()
    st.stop()

opcoes_empresas = {f"{e['razao_social']} ({e['cnpj']})": e['id'] for e in lista_empresas}
empresa_selecionada_nome = st.sidebar.selectbox("📂 Cliente Ativo Monitorado", list(opcoes_empresas.keys()))
empresa_id_ativo = opcoes_empresas[empresa_selecionada_nome]
empresa_ativa = next((e for e in lista_empresas if e['id'] == empresa_id_ativo), lista_empresas[0])

st.sidebar.markdown(f"""
<div class="sidebar-exec-card">
    <span class="sidebar-exec-title">🛡️ Ficha Cadastral & Compliance</span>
    <div class="sidebar-exec-row">
        <span class="sidebar-exec-label">Regime</span>
        <span class="badge-gold">{empresa_ativa['regime_tributario']}</span>
    </div>
    <div class="sidebar-exec-row">
        <span class="sidebar-exec-label">CNPJ</span>
        <span style="color:#f3f4f6; font-family:'Courier New', monospace; font-weight:700;">{empresa_ativa['cnpj']}</span>
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

with st.sidebar.expander("⚙️ Ajustar CNAE / Resetar Notas"):
    novo_cnae_edit = st.text_input(
        "Atualizar CNAEs (separados por vírgula)",
        value=str(empresa_ativa.get("cnae_principal", "")),
        help="Ex: 9602501 (Salão), 4772500 (Cosméticos), 4711302 (Supermercado)",
        key=f"edit_cnae_{empresa_id_ativo}"
    )
    if st.button("💾 Salvar Novo CNAE", use_container_width=True, key=f"btn_save_cnae_{empresa_id_ativo}"):
        supabase.table("empresas_monitoradas").update({"cnae_principal": novo_cnae_edit.strip()}).eq("id", empresa_id_ativo).execute()
        st.sidebar.success("CNAE atualizado!")
        st.rerun()

    st.markdown("<hr style='border:0; border-top:1px solid #374151; margin:8px 0;'>", unsafe_allow_html=True)
    if st.button("🔄 Zerar Notas deste Cliente", use_container_width=True, key=f"btn_reset_nf_{empresa_id_ativo}"):
        nfs_cli = supabase.table("notas_fiscais_recebidas").select("id").eq("empresa_id", empresa_id_ativo).execute()
        ids_nf = [n["id"] for n in (nfs_cli.data or [])]
        try:
            supabase.table("conciliacao_devolucoes").delete().eq("empresa_id", empresa_id_ativo).execute()
        except Exception:
            pass
        if ids_nf:
            try:
                supabase.table("conciliacao_devolucoes").delete().in_("nota_original_id", ids_nf).execute()
            except Exception:
                pass
            supabase.table("itens_nota_fiscal").delete().in_("nota_id", ids_nf).execute()
            supabase.table("notas_fiscais_recebidas").delete().eq("empresa_id", empresa_id_ativo).execute()
        st.sidebar.success("Notas zeradas!")
        st.rerun()

    st.markdown("<hr style='border:0; border-top:1px solid #374151; margin:8px 0;'>", unsafe_allow_html=True)
    if st.button("🗑️ Excluir Cliente Ativo", use_container_width=True, key=f"btn_del_cli_{empresa_id_ativo}"):
        try:
            nfs_cli = supabase.table("notas_fiscais_recebidas").select("id").eq("empresa_id", empresa_id_ativo).execute()
            ids_nf = [n["id"] for n in (nfs_cli.data or [])]
            try:
                supabase.table("conciliacao_devolucoes").delete().eq("empresa_id", empresa_id_ativo).execute()
            except Exception:
                pass
            if ids_nf:
                try:
                    supabase.table("conciliacao_devolucoes").delete().in_("nota_original_id", ids_nf).execute()
                except Exception:
                    pass
                supabase.table("itens_nota_fiscal").delete().in_("nota_id", ids_nf).execute()
                supabase.table("notas_fiscais_recebidas").delete().eq("empresa_id", empresa_id_ativo).execute()
            supabase.table("empresas_monitoradas").delete().eq("id", empresa_id_ativo).execute()
            st.sidebar.success("Cliente excluído!")
            st.rerun()
        except Exception as ex:
            st.sidebar.error(f"Erro ao excluir: {ex}")

    st.markdown("<hr style='border:0; border-top:1px solid #374151; margin:8px 0;'>", unsafe_allow_html=True)
    if st.button("🧹 ZERAR BANCO COMPLETO (Limpar Tudo p/ Teste)", use_container_width=True, key="btn_wipe_all_unico"):
        try:
            devs = supabase.table("conciliacao_devolucoes").select("id").execute()
            if devs.data:
                supabase.table("conciliacao_devolucoes").delete().in_("id", [d["id"] for d in devs.data]).execute()

            itens_db = supabase.table("itens_nota_fiscal").select("id").execute()
            if itens_db.data:
                supabase.table("itens_nota_fiscal").delete().in_("id", [i["id"] for i in itens_db.data]).execute()

            nfs_db = supabase.table("notas_fiscais_recebidas").select("id").execute()
            if nfs_db.data:
                supabase.table("notas_fiscais_recebidas").delete().in_("id", [n["id"] for n in nfs_db.data]).execute()

            emps_db = supabase.table("empresas_monitoradas").select("id").execute()
            if emps_db.data:
                supabase.table("empresas_monitoradas").delete().in_("id", [e["id"] for e in emps_db.data]).execute()

            st.sidebar.success("Banco 100% zerado para o contador!")
            st.rerun()
        except Exception as ex:
            st.sidebar.error(f"Erro ao limpar banco: {ex}")

if st.sidebar.button("🔒 Encerrar Sessão", use_container_width=True):
    st.session_state["autenticado"] = False
    st.rerun()

# ==========================================
# 5. CABEÇALHO EXECUTIVO
# ==========================================
st.markdown(f"""
<div class="header-container">
    <div class="header-title">🛡️ PRAESIDIUM — Intelligence & Compliance</div>
    <div class="header-subtitle">Cliente Ativo: <b>{empresa_ativa['razao_social']}</b> (CNAEs: {empresa_ativa.get('cnae_principal', '4711302')}) • Governança de Créditos Digitais e Split Payment (LC 214/2025)</div>
</div>
""", unsafe_allow_html=True)

# ==========================================
# 6. CÁLCULO DOS DADOS DA EMPRESA SELECIONADA
# ==========================================
notas_res = supabase.table("notas_fiscais_recebidas").select("*").eq("empresa_id", empresa_id_ativo).execute()
dev_res = supabase.table("conciliacao_devolucoes").select("*").execute()

notas = notas_res.data if notas_res.data else []
todas_devolucoes = dev_res.data if dev_res.data else []

devolucoes = [
    d for d in todas_devolucoes
    if d.get("empresa_id") == empresa_id_ativo
    or f"[{empresa_ativa['cnpj']}]" in str(d.get("motivo_ajuste", ""))
]

nota_ids = [n["id"] for n in notas]
itens = []
if nota_ids:
    itens_res = supabase.table("itens_nota_fiscal").select("*").in_("nota_id", nota_ids).execute()
    itens = itens_res.data if itens_res.data else []

imposto_bruto_mes = sum([float(i["valor_credito_item"]) for i in itens if i["classificacao_item"] in ["Aprovado Revenda", "Aprovado Manualmente Contador"]])
imposto_quarentena = sum([float(i["valor_item"]) * 0.265 for i in itens if i["classificacao_item"] == "Quarentena Uso/Consumo"])
imposto_estornado = sum([float(d.get("credito_estornado", 0.0)) for d in devolucoes])

imposto_aprovado = sum([float(i["valor_credito_item"]) for i in itens if i["classificacao_item"] in ["Aprovado Revenda", "Aprovado Manualmente Contador"]])
credito_validado_tempo_real = max(0.0, imposto_aprovado - imposto_estornado)

# ==========================================
# 7. PAINEL SUPERIOR: IMPOSTÔMETRO LUXO
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
# 8. PASTAS EXPANSÍVEIS (MÓDULOS)
# ==========================================

# MÓDULO 1: RADAR DE CONTINGÊNCIA & OMISSÃO SEFAZ
notas_contingencia = [n for n in notas if "Contingência" in str(n.get("manifestacao_status", ""))]
with st.expander(f"📁 1. ⚠️ Radar de Contingência & Divergência SEFAZ ({len(notas_contingencia)} Alertas Ativos)", expanded=False):
    if notas_contingencia:
        st.markdown(
            f"<span style='color: #f87171; font-weight: bold;'>⚠️ ATENÇÃO CONTADOR:</span> Foram detectadas notas emitidas em contingência (`tpEmis != 1`) para <b>{empresa_ativa['razao_social']}</b> cujo crédito foi retido preventivamente.",
            unsafe_allow_html=True
        )
        df_cont = pd.DataFrame(notas_contingencia)[["chave_acesso", "cnpj_emitente", "valor_total_nf", "manifestacao_status"]]
        df_cont.columns = ["Chave de Acesso SEFAZ", "CNPJ Fornecedor", "Valor da Nota (R$)", "Status SEFAZ"]
        st.dataframe(df_cont, hide_index=True)
    else:
        st.success(f"✅ Nenhuma nota fiscal em contingência ou divergência pendente para o CNPJ {empresa_ativa['cnpj']}.")

# MÓDULO 2: QUARENTENA & HISTÓRICO (SOBERANIA DO CONTADOR)
itens_pendentes = [i for i in itens if i["classificacao_item"] == "Quarentena Uso/Consumo"]
itens_auditados = [i for i in itens if i["classificacao_item"] in ["Rejeitado pelo Contador", "Aprovado Manualmente Contador"]]

with st.expander(f"📁 2. Fila de Quarentena & Histórico de Auditoria ({len(itens_pendentes)} pendentes | {len(itens_auditados)} auditados)", expanded=False):
    if itens_pendentes:
        st.markdown("#### ⏳ Itens Aguardando Decisão Técnica do Contador")
        for item in itens_pendentes:
            imposto_item = float(item["valor_item"]) * 0.265
            with st.container():
                st.markdown(f"""
                <div style="background: #07090e; border: 1px solid #374151; border-radius: 8px; padding: 15px; margin-bottom: 10px;">
                    <b style="color: #ffffff; font-size: 15px;">📦 {item['descricao']}</b><br>
                    <span style="color: #9ca3af; font-size: 13px;">NCM: <code>{item['ncm']}</code> | CFOP: <code>{item['cfop']}</code> | Valor Contábil: <b>R$ {float(item['valor_item']):,.2f}</b> | Crédito Potencial Retido: <b style="color: #fbbf24;">R$ {imposto_item:,.2f}</b></span>
                </div>
                """, unsafe_allow_html=True)

                c1, c2 = st.columns(2)
                if c1.button("✅ Liberar Crédito (Aprovar)", key=f"lib_{item['id']}", use_container_width=True):
                    supabase.table("itens_nota_fiscal").update({
                        "classificacao_item": "Aprovado Manualmente Contador",
                        "aliquota_aplicada": 0.265,
                        "valor_credito_item": round(imposto_item, 2)
                    }).eq("id", item["id"]).execute()
                    st.rerun()

                if c2.button("🚫 Bloquear Crédito (Uso/Consumo Definitivo)", key=f"bloq_{item['id']}", use_container_width=True):
                    supabase.table("itens_nota_fiscal").update({
                        "classificacao_item": "Rejeitado pelo Contador",
                        "valor_credito_item": 0.00
                    }).eq("id", item["id"]).execute()
                    st.rerun()
                st.markdown("---")
    else:
        st.success("🎉 Nenhum item pendente de análise na quarentena para este cliente.")

    if itens_auditados:
        st.markdown("#### 📜 Histórico Permanente de Auditoria Manual")
        df_audit = pd.DataFrame(itens_auditados)[["id", "descricao", "ncm", "cfop", "valor_item", "classificacao_item"]]
        df_audit.columns = ["ID Item", "Descrição", "NCM", "CFOP", "Valor Contábil (R$)", "Status / Decisão do Contador"]
        st.dataframe(df_audit, height=250, hide_index=True)

# MÓDULO 3: NOTAS FISCAIS RECEBIDAS (COM SELETOR DE DOWNLOAD NOTA POR NOTA DO CLIENTE)
with st.expander(f"📁 3. Notas Fiscais Monitoradas na SEFAZ ({len(notas)} notas acumuladas deste cliente)", expanded=False):
    if notas:
        st.caption("Marque na primeira coluna (☑️ Baixar XML) as notas fiscais específicas deste cliente que deseja exportar:")
        df_notas = pd.DataFrame(notas)
        df_exibir = pd.DataFrame({
            "Baixar XML": [True for _ in range(len(df_notas))],
            "Chave de Acesso NF-e": df_notas["chave_acesso"],
            "CNPJ Fornecedor": df_notas["cnpj_emitente"],
            "Valor Total (R$)": df_notas["valor_total_nf"],
            "Status SEFAZ": df_notas["manifestacao_status"],
            "Hash SHA-256 (Auditoria)": df_notas["hash_sha256_xml"]
        })

        df_notas_marcadas = st.data_editor(
            df_exibir,
            height=250,
            hide_index=True,
            disabled=["Chave de Acesso NF-e", "CNPJ Fornecedor", "Valor Total (R$)", "Status SEFAZ", "Hash SHA-256 (Auditoria)"],
            key=f"editor_notas_acumuladas_{empresa_id_ativo}"
        )

        chaves_para_baixar = df_notas_marcadas[df_notas_marcadas["Baixar XML"] == True]["Chave de Acesso NF-e"].tolist()
        if chaves_para_baixar:
            buf_cli = io.BytesIO()
            with zipfile.ZipFile(buf_cli, "w", zipfile.ZIP_DEFLATED) as zf_c:
                for n_obj in notas:
                    if n_obj["chave_acesso"] in chaves_para_baixar:
                        itens_desta_nf = [i for i in itens if i.get("nota_id") == n_obj["id"]]
                        xml_rec = xml_engine.gerar_xml_reconstruido_cliente(empresa_ativa, n_obj, itens_desta_nf)
                        zf_c.writestr(f"NFe_{n_obj['chave_acesso']}.xml", xml_rec)
            buf_cli.seek(0)
            st.download_button(
                label=f"📥 Baixar ({len(chaves_para_baixar)}) XML(s) Selecionado(s) de {empresa_ativa['razao_social']} (.ZIP)",
                data=buf_cli.getvalue(),
                file_name=f"XMLs_Auditados_{empresa_ativa['cnpj']}.zip",
                mime="application/zip",
                use_container_width=True,
                key=f"btn_dl_notas_cli_{empresa_id_ativo}"
            )
    else:
        st.info("Nenhuma nota fiscal registrada para este cliente. Clique no botão de Sincronizar Lote de Teste SEFAZ acima para importar.")

# MÓDULO 4: DEVOLUÇÕES
with st.expander(f"📁 4. Conciliação de Devoluções ({len(devolucoes)} registros)", expanded=False):
    if devolucoes:
        df_dev = pd.DataFrame(devolucoes)
        df_dev_exibir = df_dev[["chave_nota_devolucao", "valor_mercadoria_devolvida", "credito_estornado", "motivo_ajuste"]].copy()
        df_dev_exibir.columns = ["Chave NF Devolução", "Mercadoria Devolvida (R$)", "Crédito Estornado (R$)", "Motivo do Ajuste"]
        st.dataframe(df_dev_exibir, height=250, hide_index=True)
    else:
        st.info("Nenhuma devolução registrada no período.")

# MÓDULO 5: SIMULADOR ESTRATÉGICO
with st.expander("📁 5. ⚖️ Simulador Estratégico de Migração (Simples Tradicional vs. Regime Híbrido)", expanded=True):
    st.markdown(f"Simulação fiscal focada nas operações de **{empresa_ativa['razao_social']}**.")

    compras_revenda_reais = sum([float(i["valor_item"]) for i in itens if i["classificacao_item"] in ["Aprovado Revenda", "Aprovado Manualmente Contador"]])
    devolucoes_reais = sum([float(d["valor_mercadoria_devolvida"]) for d in devolucoes])
    quarentena_reais = sum([float(i["valor_item"]) for i in itens if i["classificacao_item"] == "Quarentena Uso/Consumo"])

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
        compras_quarentena_input = col_comp3.number_input("⚠️ Compras Travadas na Quarentena (R$)", value=float(quarentena_reais), step=100.0, disabled=True, format="%.2f")

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

# MÓDULO 6: DOSSIÊ EXECUTIVO DE FECHAMENTO
relatorio_engine.renderizar_modulo_relatorio(
    empresa_ativa, imposto_bruto_mes, credito_validado_tempo_real, imposto_quarentena, imposto_estornado, notas, itens
)
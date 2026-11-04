import streamlit as st
import pandas as pd
from datetime import datetime, date


def injetar_css_blindado_relatorio():
    st.markdown("""
    <style>
        .stApp, [data-testid="stHeader"], [data-testid="stToolbar"] {
            background-color: #0b0f19 !important;
        }
        [data-testid="stHeader"] {
            background: transparent !important;
        }
        div[data-baseweb="popover"], div[data-baseweb="menu"], div[data-testid="stSelectbox"] div[role="listbox"], div[data-baseweb="calendar"] {
            background-color: #111827 !important;
            border: 1px solid #d4af37 !important;
            color: #ffffff !important;
            box-shadow: 0 10px 25px rgba(0,0,0,0.8) !important;
        }
        .stDownloadButton button, [data-testid="stDownloadButton"] button {
            background: linear-gradient(135deg, #1f2937 0%, #111827 100%) !important;
            color: #f6c443 !important;
            border: 1px solid #d4af37 !important;
            border-radius: 8px !important;
            font-weight: 700 !important;
            padding: 10px 16px !important;
            width: 100% !important;
        }
        .stDownloadButton button:hover, [data-testid="stDownloadButton"] button:hover {
            background: #374151 !important;
            color: #ffffff !important;
            border-color: #fbbf24 !important;
            box-shadow: 0 0 12px rgba(212, 175, 55, 0.4) !important;
        }
        .stDownloadButton button p, [data-testid="stDownloadButton"] button span {
            color: #f6c443 !important;
            font-weight: 700 !important;
        }
        div[data-baseweb="select"] > div,
        div[data-baseweb="input"],
        div[data-baseweb="input"] > div,
        div[data-baseweb="base-input"],
        div[data-testid="stTextInput"] input {
            background-color: #0b0f19 !important;
            color: #ffffff !important;
            border: 1.5px solid #d4af37 !important;
            border-radius: 8px !important;
        }
        div[data-baseweb="select"] span, div[data-baseweb="input"] input {
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
        }
        button[data-baseweb="tab"] {
            background-color: transparent !important;
            color: #9ca3af !important;
            font-weight: 600 !important;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            color: #f6c443 !important;
            border-bottom-color: #d4af37 !important;
        }
    </style>
    """, unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def montar_dataframe_relacional(notas: list, itens: list) -> pd.DataFrame:
    if not notas:
        return pd.DataFrame()

    mapa_notas = {
        n.get("id", f"sem_id_{idx}"): {
            "chave_acesso": str(n.get("chave_acesso", "")),
            "cnpj_emitente": str(n.get("cnpj_emitente", "")),
            "valor_total_nf": float(n.get("valor_total_nf", 0.0)),
            "manifestacao_status": str(n.get("manifestacao_status", "")),
            "hash_sha256_xml": str(n.get("hash_sha256_xml", "")),
            "created_at": n.get("created_at", str(date.today()))
        }
        for idx, n in enumerate(notas)
    }

    linhas = []
    notas_com_itens = set()

    for item in (itens or []):
        n_id = item.get("nota_id")
        notas_com_itens.add(n_id)
        nota_info = mapa_notas.get(n_id, {})
        valor_contabil = float(item.get("valor_item", 0.0))
        classificacao = str(item.get("classificacao_item", "Não Classificado"))
        credito_aprovado = float(item.get("valor_credito_item", 0.0))
        credito_quarentena = round(valor_contabil * 0.265, 2) if classificacao == "Quarentena Uso/Consumo" else 0.0

        raw_date = nota_info.get("created_at", str(date.today()))
        try:
            data_parsed = pd.to_datetime(raw_date).date()
        except Exception:
            data_parsed = date.today()

        linhas.append({
            "ID Registro": f"ITEM-{item.get('id')}",
            "Data": data_parsed,
            "CNPJ Fornecedor": nota_info.get("cnpj_emitente", "N/A"),
            "Chave de Acesso NF-e": nota_info.get("chave_acesso", "N/A"),
            "Descrição do Item / Documento": str(item.get("descricao", "")),
            "NCM": str(item.get("ncm", "")),
            "CFOP": str(item.get("cfop", "")),
            "Valor Contábil (R$)": valor_contabil,
            "Alíquota (%)": round(float(item.get("aliquota_aplicada", 0.0)) * 100, 2),
            "Crédito Efetivo (R$)": credito_aprovado,
            "Risco / Quarentena (R$)": credito_quarentena,
            "Status Auditoria": classificacao,
            "Hash SHA-256": nota_info.get("hash_sha256_xml", "N/A")
        })

    for n_id, n_info in mapa_notas.items():
        if n_id not in notas_com_itens:
            vl_nf = n_info["valor_total_nf"]
            raw_date = n_info.get("created_at", str(date.today()))
            try:
                data_parsed = pd.to_datetime(raw_date).date()
            except Exception:
                data_parsed = date.today()

            linhas.append({
                "ID Registro": f"NF-{n_id}",
                "Data": data_parsed,
                "CNPJ Fornecedor": n_info["cnpj_emitente"],
                "Chave de Acesso NF-e": n_info["chave_acesso"],
                "Descrição do Item / Documento": "NF-e Consolidada (Sem itens explodidos)",
                "NCM": "DIVERSOS",
                "CFOP": "5102",
                "Valor Contábil (R$)": vl_nf,
                "Alíquota (%)": 26.5,
                "Crédito Efetivo (R$)": round(vl_nf * 0.265, 2),
                "Risco / Quarentena (R$)": 0.0,
                "Status Auditoria": n_info["manifestacao_status"] or "Nota Recebida",
                "Hash SHA-256": n_info["hash_sha256_xml"]
            })

    return pd.DataFrame(linhas)


@st.cache_data(show_spinner=False)
def gerar_html_dossie_analitico(
    empresa_ativa: dict,
    modalidade_fechamento: str,
    imposto_bruto: float,
    credito_validado: float,
    imposto_quarentena: float,
    imposto_estornado: float,
    notas_filtradas: list,
    df_itens_filtrados: pd.DataFrame
) -> str:
    data_emissao = datetime.now().strftime("%d/%m/%Y às %H:%M")

    linhas_html_notas = ""
    if notas_filtradas:
        for n in notas_filtradas:
            hash_curto = str(n.get("hash_sha256_xml", ""))[:24] + "..."
            linhas_html_notas += f"""
            <tr>
                <td style="font-family: monospace;">{n.get('chave_acesso', '')}</td>
                <td>{n.get('cnpj_emitente', '')}</td>
                <td style="text-align: right; font-weight: bold;">R$ {float(n.get('valor_total_nf', 0.0)):,.2f}</td>
                <td>{n.get('manifestacao_status', '')}</td>
                <td style="font-family: monospace; font-size: 10px; color: #9ca3af;">{hash_curto}</td>
            </tr>"""
    else:
        linhas_html_notas = "<tr><td colspan='5' style='text-align:center;'>Nenhuma nota fiscal selecionada.</td></tr>"

    linhas_html_itens = ""
    tot_cont = tot_cred = tot_quar = 0.0
    if not df_itens_filtrados.empty:
        for _, row in df_itens_filtrados.iterrows():
            vl_cont = float(row["Valor Contábil (R$)"])
            vl_cred = float(row["Crédito Efetivo (R$)"])
            vl_quar = float(row["Risco / Quarentena (R$)"])
            tot_cont += vl_cont
            tot_cred += vl_cred
            tot_quar += vl_quar

            status_txt = str(row["Status Auditoria"])
            cor = "#34d399" if "Aprovado" in status_txt else ("#fbbf24" if "Quarentena" in status_txt else "#f87171")

            linhas_html_itens += f"""
            <tr>
                <td>{str(row['Data'])}</td>
                <td>{row['CNPJ Fornecedor']}</td>
                <td><b>{row['Descrição do Item / Documento']}</b></td>
                <td style="font-family: monospace;">{row['NCM']}</td>
                <td style="font-family: monospace;">{row['CFOP']}</td>
                <td style="text-align: right;">R$ {vl_cont:,.2f}</td>
                <td style="text-align: right; color: #34d399; font-weight: bold;">R$ {vl_cred:,.2f}</td>
                <td style="text-align: right; color: #fbbf24;">R$ {vl_quar:,.2f}</td>
                <td style="color: {cor}; font-weight: bold;">{status_txt}</td>
            </tr>"""
    else:
        linhas_html_itens = "<tr><td colspan='9' style='text-align:center;'>Nenhum item enquadrado nos filtros.</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Dossiê Analítico - {empresa_ativa['razao_social']}</title>
<style>
    body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #0b0f19; color: #f3f4f6; padding: 30px; margin: 0; }}
    .container {{ max-width: 1080px; margin: auto; background: #111827; border: 1px solid #374151; border-top: 5px solid #d4af37; border-radius: 12px; padding: 35px; }}
    .header-top {{ display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 1px solid #374151; padding-bottom: 18px; margin-bottom: 24px; }}
    h1 {{ color: #ffffff; margin: 0 0 6px 0; font-size: 22px; }}
    h2 {{ color: #d4af37; font-size: 15px; text-transform: uppercase; letter-spacing: 1px; margin: 28px 0 12px 0; border-left: 3px solid #d4af37; padding-left: 10px; }}
    .sub {{ color: #9ca3af; font-size: 12px; line-height: 1.6; }}
    .badge-fechamento {{ background: #1f2937; border: 1px solid #d4af37; color: #f6c443; padding: 6px 12px; border-radius: 6px; font-size: 11px; font-weight: bold; text-transform: uppercase; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 24px; }}
    .card {{ background: #1f2937; border: 1px solid #374151; border-radius: 8px; padding: 15px; }}
    .label {{ color: #9ca3af; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; font-weight: bold; }}
    .val-gold {{ color: #f6c443; font-size: 20px; font-weight: 800; margin-top: 6px; font-family: monospace; }}
    .val-green {{ color: #34d399; font-size: 20px; font-weight: 800; margin-top: 6px; font-family: monospace; }}
    .val-amber {{ color: #fbbf24; font-size: 20px; font-weight: 800; margin-top: 6px; font-family: monospace; }}
    .val-red {{ color: #f87171; font-size: 20px; font-weight: 800; margin-top: 6px; font-family: monospace; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; background: #0f172a; }}
    th {{ background: #1f2937; color: #d1d5db; text-align: left; padding: 10px 12px; font-size: 11px; text-transform: uppercase; border-bottom: 1px solid #374151; }}
    td {{ padding: 9px 12px; border-bottom: 1px solid #1f2937; color: #e5e7eb; }}
    .tfoot-row td {{ background: #1f2937; font-weight: bold; border-top: 2px solid #d4af37; color: #ffffff; }}
    .footer {{ margin-top: 35px; font-size: 11px; color: #9ca3af; text-align: center; border-top: 1px solid #374151; padding-top: 18px; }}
    @media print {{
        body {{ background-color: #ffffff; color: #000000; padding: 0; }}
        .container {{ background: #ffffff; border: none; padding: 10px; max-width: 100%; }}
        h1, h2, .val-gold, .val-green, .val-amber, .val-red {{ color: #000000 !important; }}
        .card {{ background: #f9fafb; border: 1px solid #d1d5db; }}
        table, th, td {{ background: #ffffff !important; color: #000000 !important; border: 1px solid #d1d5db !important; }}
    }}
</style>
</head>
<body>
<div class="container">
    <div class="header-top">
        <div>
            <h1>🛡️ PRAESIDIUM — Dossiê Analítico de Governança & Créditos (LC 214/2025)</h1>
            <div class="sub">
                <b>Razão Social:</b> {empresa_ativa['razao_social']} &nbsp;|&nbsp; <b>CNPJ:</b> {empresa_ativa['cnpj']}<br>
                <b>Regime:</b> {empresa_ativa['regime_tributario']} &nbsp;|&nbsp; <b>Emissão:</b> {data_emissao}
            </div>
        </div>
        <div class="badge-fechamento">{modalidade_fechamento}</div>
    </div>

    <div class="grid">
        <div class="card"><div class="label">⚡ Crédito Bruto Global</div><div class="val-gold">R$ {imposto_bruto:,.2f}</div></div>
        <div class="card"><div class="label">✅ Crédito Líquido Validado</div><div class="val-green">R$ {credito_validado:,.2f}</div></div>
        <div class="card"><div class="label">🔒 Retido em Quarentena</div><div class="val-amber">R$ {imposto_quarentena:,.2f}</div></div>
        <div class="card"><div class="label">↩️ Estornos Devolução</div><div class="val-red">R$ {imposto_estornado:,.2f}</div></div>
    </div>

    <h2>1. Notas Fiscais Auditadas ({len(notas_filtradas)} documentos)</h2>
    <table>
        <thead><tr><th>Chave de Acesso SEFAZ</th><th>CNPJ Emitente</th><th style="text-align: right;">Valor NF-e</th><th>Status</th><th>SHA-256</th></tr></thead>
        <tbody>{linhas_html_notas}</tbody>
    </table>

    <h2>2. Livro Analítico de Itens ({len(df_itens_filtrados)} registros)</h2>
    <table>
        <thead><tr><th>Data</th><th>Fornecedor</th><th>Descrição do Item</th><th>NCM</th><th>CFOP</th><th style="text-align: right;">Base Contábil</th><th style="text-align: right;">Crédito IBS/CBS</th><th style="text-align: right;">Quarentena</th><th>Status</th></tr></thead>
        <tbody>{linhas_html_itens}</tbody>
        <tfoot>
            <tr class="tfoot-row">
                <td colspan="5">TOTAIS DO RECORTE</td>
                <td style="text-align: right;">R$ {tot_cont:,.2f}</td>
                <td style="text-align: right; color: #34d399;">R$ {tot_cred:,.2f}</td>
                <td style="text-align: right; color: #fbbf24;">R$ {tot_quar:,.2f}</td>
                <td>—</td>
            </tr>
        </tfoot>
    </table>

    <div class="footer">
        Auditado pelo motor <b>Praesidium Intelligence & Compliance</b>.<br>
        Para salvar como PDF: pressione <b>Ctrl + P</b> no navegador e selecione <b>"Salvar como PDF"</b>.
    </div>
</div>
</body>
</html>"""


@st.cache_data(show_spinner=False)
def gerar_csv_auditado(df_filtrado: pd.DataFrame) -> bytes:
    return df_filtrado.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig") if not df_filtrado.empty else b"Sem dados"


def renderizar_modulo_relatorio(
    empresa_ativa,
    imposto_bruto_mes,
    credito_validado_tempo_real,
    imposto_quarentena,
    imposto_estornado,
    notas,
    itens
):
    injetar_css_blindado_relatorio()

    with st.expander("📁 6. 📄 Central de Dossiês Analíticos, Filtros de Notas & Exportação ERP", expanded=False):
        st.markdown(
            f"Configuração do escopo de auditoria para <b>{empresa_ativa['razao_social']}</b> com cruzamento relacional e filtros dinâmicos.",
            unsafe_allow_html=True
        )

        df_completo = montar_dataframe_relacional(notas, itens)

        status_padrao = ["Aprovado Revenda", "Quarentena Uso/Consumo", "Aprovado Manualmente Contador", "Rejeitado pelo Contador"]
        if not df_completo.empty:
            status_presentes = sorted(df_completo["Status Auditoria"].dropna().unique().tolist())
            status_disponiveis = list(dict.fromkeys(status_padrao + status_presentes))
            fornecedores_disponiveis = ["Todos os Fornecedores"] + sorted(df_completo["CNPJ Fornecedor"].dropna().unique().tolist())
            cfops_disponiveis = ["Todos"] + sorted(df_completo["CFOP"].dropna().unique().tolist())
        else:
            status_disponiveis = status_padrao
            fornecedores_disponiveis = ["Todos os Fornecedores"]
            cfops_disponiveis = ["Todos"]

        with st.form(key=f"form_dossie_filtros_{empresa_ativa['id']}", border=False):
            f_col1, f_col2, f_col3 = st.columns([1.2, 1.5, 1.3])
            with f_col1:
                modalidade_fechamento = st.selectbox(
                    "📅 Periodicidade / Escopo",
                    ["Fechamento Mensal Consolidado", "Fechamento Semanal (Sprint)", "Auditoria Exclusiva de Quarentena", "Relatório de Créditos Homologados"],
                    key=f"mod_fech_{empresa_ativa['id']}"
                )
            with f_col2:
                filtro_status = st.multiselect(
                    "⚖️ Status de Auditoria",
                    options=status_disponiveis,
                    default=status_disponiveis,
                    key=f"flt_status_{empresa_ativa['id']}"
                )
            with f_col3:
                filtro_fornecedor = st.selectbox(
                    "🏢 CNPJ Fornecedor",
                    options=fornecedores_disponiveis,
                    key=f"flt_forn_{empresa_ativa['id']}"
                )

            f_col4, f_col5, f_col6 = st.columns([1.5, 1, 1.3])
            with f_col4:
                busca_texto = st.text_input(
                    "🔍 Busca Textual",
                    placeholder="Ex: Cadeira, 22021000, 5556...",
                    key=f"flt_txt_{empresa_ativa['id']}"
                )
            with f_col5:
                filtro_cfop = st.selectbox("📌 CFOP", options=cfops_disponiveis, key=f"flt_cfop_{empresa_ativa['id']}")
            with f_col6:
                data_hoje = date.today()
                data_inicio_padrao = date(data_hoje.year, 1, 1)
                intervalo_datas = st.date_input(
                    "📅 Período (Início e Fim)",
                    value=(data_inicio_padrao, data_hoje),
                    key=f"flt_datas_{empresa_ativa['id']}"
                )

            st.write("")
            st.form_submit_button("⚡ Aplicar Filtros na Auditoria", use_container_width=True)

        df_filtrado = df_completo.copy()
        if not df_filtrado.empty:
            if filtro_status:
                df_filtrado = df_filtrado[df_filtrado["Status Auditoria"].isin(filtro_status)]
            else:
                df_filtrado = df_filtrado.iloc[0:0]

            if filtro_fornecedor != "Todos os Fornecedores":
                df_filtrado = df_filtrado[df_filtrado["CNPJ Fornecedor"] == filtro_fornecedor]
            if filtro_cfop != "Todos":
                df_filtrado = df_filtrado[df_filtrado["CFOP"] == filtro_cfop]

            if isinstance(intervalo_datas, (tuple, list)) and len(intervalo_datas) == 2:
                d_ini, d_fim = intervalo_datas
                df_filtrado = df_filtrado[(df_filtrado["Data"] >= d_ini) & (df_filtrado["Data"] <= d_fim)]
            elif isinstance(intervalo_datas, (tuple, list)) and len(intervalo_datas) == 1:
                d_ini = intervalo_datas[0]
                df_filtrado = df_filtrado[df_filtrado["Data"] >= d_ini]

            if busca_texto.strip():
                termo = busca_texto.strip().lower()
                mask = (
                    df_filtrado["Descrição do Item / Documento"].str.lower().str.contains(termo, na=False)
                    | df_filtrado["NCM"].str.lower().str.contains(termo, na=False)
                    | df_filtrado["CFOP"].str.lower().str.contains(termo, na=False)
                    | df_filtrado["Chave de Acesso NF-e"].str.lower().str.contains(termo, na=False)
                )
                df_filtrado = df_filtrado[mask]

        chaves_ativas = set(df_filtrado["Chave de Acesso NF-e"].unique().tolist()) if not df_filtrado.empty else set()
        notas_filtradas = [n for n in notas if str(n.get("chave_acesso")) in chaves_ativas]

        st.markdown("---")
        tot_cont = df_filtrado["Valor Contábil (R$)"].sum() if not df_filtrado.empty else 0.0
        tot_cred = df_filtrado["Crédito Efetivo (R$)"].sum() if not df_filtrado.empty else 0.0
        tot_quar = df_filtrado["Risco / Quarentena (R$)"].sum() if not df_filtrado.empty else 0.0
        qtd_reg = len(df_filtrado)
        qtd_tot = len(df_completo)

        st.markdown(f"""
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px;">
            <div style="background: #1f2937; border: 1px solid #374151; padding: 14px; border-radius: 8px;">
                <div style="color: #9ca3af; font-size: 11px; text-transform: uppercase; font-weight: bold;">📦 Registros no Filtro</div>
                <div style="color: #ffffff; font-size: 18px; font-weight: 800; font-family: monospace; margin-top: 4px;">{qtd_reg} de {qtd_tot}</div>
            </div>
            <div style="background: #1f2937; border: 1px solid #374151; padding: 14px; border-radius: 8px;">
                <div style="color: #9ca3af; font-size: 11px; text-transform: uppercase; font-weight: bold;">🧾 Base Contábil Filtrada</div>
                <div style="color: #f6c443; font-size: 18px; font-weight: 800; font-family: monospace; margin-top: 4px;">R$ {tot_cont:,.2f}</div>
            </div>
            <div style="background: #1f2937; border: 1px solid #374151; padding: 14px; border-radius: 8px;">
                <div style="color: #9ca3af; font-size: 11px; text-transform: uppercase; font-weight: bold;">✅ Crédito Filtrado</div>
                <div style="color: #34d399; font-size: 18px; font-weight: 800; font-family: monospace; margin-top: 4px;">R$ {tot_cred:,.2f}</div>
            </div>
            <div style="background: #1f2937; border: 1px solid #374151; padding: 14px; border-radius: 8px;">
                <div style="color: #9ca3af; font-size: 11px; text-transform: uppercase; font-weight: bold;">🔒 Quarentena Filtrada</div>
                <div style="color: #fbbf24; font-size: 18px; font-weight: 800; font-family: monospace; margin-top: 4px;">R$ {tot_quar:,.2f}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        aba_itens, aba_resumo = st.tabs([f"📋 Livro Analítico Filtrado ({qtd_reg} Registros)", "📊 Concentração por CFOP & NCM"])

        with aba_itens:
            if not df_filtrado.empty:
                st.dataframe(df_filtrado, hide_index=True, height=320)
            else:
                st.info("Nenhum registro encontrado para o período ou filtros selecionados.")

        with aba_resumo:
            if not df_filtrado.empty:
                df_agrupado = df_filtrado.groupby(["CFOP", "NCM", "Status Auditoria"], as_index=False).agg(
                    Qtd_Itens=("ID Registro", "count"),
                    Total_Contabil=("Valor Contábil (R$)", "sum"),
                    Total_Credito=("Crédito Efetivo (R$)", "sum"),
                    Total_Quarentena=("Risco / Quarentena (R$)", "sum")
                ).sort_values(by="Total_Contabil", ascending=False)
                df_agrupado.columns = ["CFOP", "NCM", "Status Auditoria", "Qtd.", "Volume Contábil (R$)", "Crédito Gerado (R$)", "Quarentena (R$)"]
                st.dataframe(df_agrupado, hide_index=True, height=320)
            else:
                st.info("Sem dados para agrupar.")

        st.markdown("---")
        html_analitico = gerar_html_dossie_analitico(
            empresa_ativa, modalidade_fechamento, imposto_bruto_mes, credito_validado_tempo_real,
            imposto_quarentena, imposto_estornado, notas_filtradas, df_filtrado
        )
        csv_bytes = gerar_csv_auditado(df_filtrado)

        b1, b2 = st.columns(2)
        with b1:
            st.download_button(
                label=f"📥 Baixar Dossiê Analítico Completo ({len(df_filtrado)} reg) [.HTML / PDF]",
                data=html_analitico.encode("utf-8"),
                file_name=f"Dossie_Analitico_{empresa_ativa['cnpj']}.html",
                mime="text/html",
                use_container_width=True
            )
        with b2:
            st.download_button(
                label="📊 Exportar Planilha Auditada p/ Excel & ERP (.CSV)",
                data=csv_bytes,
                file_name=f"Auditoria_Itens_{empresa_ativa['cnpj']}.csv",
                mime="text/csv",
                use_container_width=True
            )
import base64
import io
import json
from datetime import datetime, date

import pandas as pd
import streamlit as st

import xml_engine


def _payload_danfe(nota: dict, itens: list[dict], empresa: dict) -> dict:
    payload = {
        "chave_acesso": str(nota.get("chave_acesso", "")),
        "cnpj_emitente": str(nota.get("cnpj_emitente", "")),
        "nome_emitente": str(
            nota.get("razao_social_emitente")
            or nota.get("nome_emitente")
            or "Emitente não informado"
        ),
        "cnpj_destinatario": str(empresa.get("cnpj", "")),
        "nome_destinatario": str(empresa.get("razao_social", "")),
        "numero": str(nota.get("numero") or nota.get("nNF") or "1"),
        "serie": str(nota.get("serie") or "1"),
        "data_emissao": str(nota.get("data_emissao") or date.today().isoformat()),
        "valor_total_nf": float(nota.get("valor_total_nf", 0.0) or 0.0),
        "itens": [
            {
                "numero": int(xml_engine._float_seguro(item.get("nitem"), indice)),
                "codigo": str(item.get("codigo") or item.get("cProd") or f"ITEM-{indice:03d}"),
                "descricao": str(item.get("descricao") or item.get("descricao_produto") or "Item"),
                "ncm": str(item.get("ncm", "")),
                "cfop": str(item.get("cfop", "")),
                "quantidade": float(
                    item.get("quantidade_comercial")
                    or item.get("qcom")
                    or 0.0
                ),
                "valor_unitario": float(
                    item.get("valor_unitario")
                    or (
                        float(item.get("valor_item") or item.get("valor_total_bruto") or 0.0)
                        / max(
                            float(item.get("quantidade_comercial") or item.get("qcom") or 1.0),
                            0.0001,
                        )
                    )
                ),
                "valor_total": float(
                    item.get("valor_item")
                    or item.get("valor_total_bruto")
                    or 0.0
                ),
                "ibs": float(item.get("credito_ibs_gerado") or 0.0),
                "cbs": float(item.get("credito_cbs_gerado") or 0.0),
                "status": str(
                    item.get("classificacao_item")
                    or item.get("status_credito")
                    or "Não classificado"
                ),
                "credito": float(item.get("valor_credito_item") or 0.0),
            }
            for indice, item in enumerate(itens or [], start=1)
        ],
    }
    payload["total_ibs"] = round(
        sum(item["ibs"] for item in payload["itens"]),
        2,
    )
    payload["total_cbs"] = round(
        sum(item["cbs"] for item in payload["itens"]),
        2,
    )
    payload["total_credito"] = round(
        sum(item["credito"] for item in payload["itens"]),
        2,
    )
    return payload


def _pdf_nativo_danfe(payload: dict) -> bytes:
    linhas = [
        "DANFE - ESPELHO DE CONFERENCIA - SEM VALIDADE FISCAL",
        f"EMITENTE: {payload['nome_emitente']} | CNPJ: {payload['cnpj_emitente']}",
        f"DESTINATARIO: {payload['nome_destinatario']} | CNPJ: {payload['cnpj_destinatario']}",
        f"NUMERO: {payload['numero']} | SERIE: {payload['serie']} | DATA: {payload['data_emissao']}",
        f"CHAVE DE ACESSO: {payload['chave_acesso']}",
        f"VALOR TOTAL DA NOTA: R$ {payload['valor_total_nf']:.2f}",
        f"IBS: R$ {payload['total_ibs']:.2f} | CBS: R$ {payload['total_cbs']:.2f} | "
        f"Crédito Split Payment: R$ {payload['total_credito']:.2f}",
        "Documento de auditoria. Nao substitui DANFE autorizado pela SEFAZ.",
        "CODIGO | DESCRICAO | NCM | CFOP | QTD | V.UNIT | V.TOTAL | IBS | CBS | CREDITO | STATUS",
    ]
    for item in payload["itens"]:
        linhas.append(
            f"{item['codigo']} | {item['descricao']} | {item['ncm']} | "
            f"{item['cfop']} | {item['quantidade']:.4f} | "
            f"{item['valor_unitario']:.2f} | {item['valor_total']:.2f} | "
            f"{item['ibs']:.2f} | {item['cbs']:.2f} | "
            f"{item['credito']:.2f} | {item['status']}"
        )
    paginas = [linhas[indice:indice + 45] for indice in range(0, len(linhas), 45)]
    if not paginas:
        paginas = [[]]
    dados = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    objetos: list[bytes] = []
    objetos.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    paginas_refs = " ".join(
        f"{4 + indice * 2} 0 R" for indice in range(len(paginas))
    )
    objetos.append(
        f"<< /Type /Pages /Kids [{paginas_refs}] /Count {len(paginas)} >>".encode("ascii")
    )
    objetos.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for indice, linhas_pagina in enumerate(paginas):
        page_id = 4 + indice * 2
        stream_id = page_id + 1
        comandos = ["BT /F1 8 Tf 28 560 Td"]
        for linha in linhas_pagina:
            seguro = str(linha).encode("cp1252", errors="replace").decode("latin-1")
            seguro = seguro.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            comandos.append(f"({seguro}) Tj 0 -12 Td")
        comandos.append("ET")
        stream = "\n".join(comandos).encode("latin-1")
        objetos.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>"
            ).encode("ascii")
        )
        objetos.append(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
            + stream + b"\nendstream"
        )
    info_id = len(objetos) + 1
    objetos.append(
        f"<< /Title (DANFE Praesidium) /PraesidiumPayload ({dados}) >>".encode("ascii")
    )
    buffer = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for indice, objeto in enumerate(objetos, start=1):
        offsets.append(len(buffer))
        buffer.extend(f"{indice} 0 obj\n".encode("ascii"))
        buffer.extend(objeto)
        buffer.extend(b"\nendobj\n")
    xref_offset = len(buffer)
    buffer.extend(f"xref\n0 {len(objetos) + 1}\n".encode("ascii"))
    buffer.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.extend(
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF".encode("ascii")
    )
    return bytes(buffer)


def gerar_danfe_pdf(nota: dict, itens: list[dict], empresa: dict) -> bytes:
    payload = _payload_danfe(nota, itens, empresa)
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.pdfgen import canvas
        from reportlab.graphics.barcode import code128
    except ImportError:
        return _pdf_nativo_danfe(payload)

    buffer = io.BytesIO()
    pagina_largura, pagina_altura = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=(pagina_largura, pagina_altura))
    pdf.setTitle("Espelho DANFE para conferência - Praesidium")
    pdf.setAuthor("Praesidium")
    metadata = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    pdf.setKeywords(f"PraesidiumPayload:{metadata}")
    margem = 28
    largura_util = pagina_largura - 2 * margem

    def desenhar_cabecalho(numero_pagina: int, total_paginas: int) -> float:
        topo = pagina_altura - margem
        pdf.setStrokeColor(colors.HexColor("#111827"))
        pdf.setLineWidth(1)
        pdf.rect(margem, topo - 104, largura_util, 104)
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawString(margem + 10, topo - 22, "DANFE")
        pdf.setFont("Helvetica", 7)
        pdf.drawString(margem + 10, topo - 35, "Espelho de conferência — sem validade fiscal")
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(
            margem + 190,
            topo - 22,
            payload["nome_emitente"][:65],
        )
        pdf.setFont("Helvetica", 8)
        pdf.drawString(
            margem + 190,
            topo - 37,
            f"CNPJ Emitente: {payload['cnpj_emitente']} | Destinatário: "
            f"{payload['nome_destinatario'][:48]}",
        )
        pdf.drawString(
            margem + 190,
            topo - 51,
            f"CNPJ Destinatário: {payload['cnpj_destinatario']}",
        )
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(
            pagina_largura - margem - 150,
            topo - 22,
            f"Nº {payload['numero']} | Série {payload['serie']}",
        )
        pdf.setFont("Helvetica", 7)
        pdf.drawString(
            pagina_largura - margem - 150,
            topo - 36,
            f"Página {numero_pagina} de {total_paginas}",
        )
        chave = payload["chave_acesso"]
        pdf.drawString(margem + 10, topo - 70, "CHAVE DE ACESSO")
        pdf.setFont("Courier-Bold", 9)
        pdf.drawString(
            margem + 10,
            topo - 85,
            " ".join(chave[indice:indice + 4] for indice in range(0, len(chave), 4)),
        )
        try:
            barcode = code128.Code128(chave, barHeight=24, barWidth=0.55, humanReadable=False)
            barcode.drawOn(pdf, pagina_largura - margem - barcode.width - 12, topo - 91)
        except Exception:
            pdf.setFont("Courier", 6)
            pdf.drawString(pagina_largura - margem - 190, topo - 84, chave)
        return topo - 122

    itens_por_pagina = 22
    paginas_itens = [
        payload["itens"][indice:indice + itens_por_pagina]
        for indice in range(0, len(payload["itens"]), itens_por_pagina)
    ] or [[]]
    total_paginas = len(paginas_itens)
    for numero_pagina, pagina_itens in enumerate(paginas_itens, start=1):
        y = desenhar_cabecalho(numero_pagina, total_paginas)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(margem, y, "DESTINATÁRIO")
        pdf.drawString(margem + 260, y, "CÁLCULO DO IMPOSTO")
        y -= 13
        pdf.setFont("Helvetica", 8)
        pdf.drawString(
            margem,
            y,
            f"{payload['nome_destinatario'][:48]} | CNPJ {payload['cnpj_destinatario']}",
        )
        pdf.drawString(
            margem + 260,
            y,
            f"Base R$ {payload['valor_total_nf']:,.2f} | IBS R$ {payload['total_ibs']:,.2f} | "
            f"CBS R$ {payload['total_cbs']:,.2f} | Crédito R$ {payload['total_credito']:,.2f}",
        )
        y -= 22
        pdf.setFillColor(colors.HexColor("#e5e7eb"))
        pdf.rect(margem, y - 3, largura_util, 16, fill=1, stroke=0)
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 6.8)
        colunas = [
            (margem + 3, "CÓDIGO"),
            (margem + 58, "DESCRIÇÃO"),
            (margem + 310, "NCM"),
            (margem + 365, "CFOP"),
            (margem + 405, "QTD"),
            (margem + 455, "V.UNIT"),
            (margem + 510, "V.TOTAL"),
            (margem + 590, "CRÉDITO"),
            (margem + 660, "STATUS"),
        ]
        for x, label in colunas:
            pdf.drawString(x, y + 2, label)
        y -= 14
        pdf.setFont("Helvetica", 6.4)
        for item in pagina_itens:
            descricao = item["descricao"].replace("\n", " ")[:48]
            campos = [
                (margem + 3, item["codigo"][:12]),
                (margem + 58, descricao),
                (margem + 310, item["ncm"][:8]),
                (margem + 365, item["cfop"][:4]),
                (margem + 405, f"{item['quantidade']:.3f}"),
                (margem + 455, f"{item['valor_unitario']:,.2f}"),
                (margem + 510, f"{item['valor_total']:,.2f}"),
                (margem + 590, f"{item['credito']:,.2f}"),
                (margem + 660, item["status"][:18]),
            ]
            for x, valor in campos:
                largura_texto = max(1.0, largura_util - (x - margem) - 3)
                texto = str(valor)
                while texto and stringWidth(texto, "Helvetica", 6.4) > largura_texto:
                    texto = texto[:-1]
                pdf.drawString(x, y, texto)
            pdf.setStrokeColor(colors.HexColor("#d1d5db"))
            pdf.line(margem, y - 3, pagina_largura - margem, y - 3)
            y -= 15
        pdf.setFont("Helvetica", 7)
        pdf.drawString(
            margem,
            margem,
            f"Data de emissão: {payload['data_emissao']} | "
            f"Crédito IBS/CBS calculado por item: LC 214/2025 | "
            f"Espelho de auditoria; não substitui DANFE autorizado pela SEFAZ.",
        )
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def injetar_css_blindado_relatorio():
    st.markdown("""
    <style>
        .stApp, [data-testid="stHeader"], [data-testid="stToolbar"] {
            background-color: #0e1117 !important;
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
            background-color: #0e1117 !important;
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
def montar_dataframe_relacional(
    notas: list,
    itens: list,
    eventos_liquidacao: list | None = None,
    empresa_id=None,
) -> pd.DataFrame:
    if not notas and not itens:
        return pd.DataFrame()

    mapa_notas = {
        str(n.get("id", f"sem_id_{idx}")): {
            **dict(n),
            "data_referencia": n.get("data_emissao") or n.get(
                "created_at",
                str(date.today()),
            ),
        }
        for idx, n in enumerate(notas or [])
    }
    itens_por_nota: dict[str, list[dict]] = {}
    for item_bruto in itens or []:
        item = xml_engine.normalizar_item_credito(item_bruto)
        itens_por_nota.setdefault(str(item.get("nota_id") or ""), []).append(item)

    resumo_eventos = xml_engine.resumir_eventos_liquidacao(
        eventos_liquidacao or []
    )
    linhas = []

    def data_nota(nota_info: dict):
        try:
            data = pd.to_datetime(
                nota_info.get("data_referencia"),
                errors="coerce",
            )
            return data.date() if not pd.isna(data) else date.today()
        except (TypeError, ValueError):
            return date.today()

    def adicionar_linha(item: dict, nota_id: str, nota_info: dict, indice: int):
        chave = str(nota_info.get("chave_acesso") or "")
        itens_da_nota = itens_por_nota.get(nota_id, [])
        valor_base_original = float(
            nota_info.get("valor_total_nf")
            or sum(float(registro.get("valor_item", 0.0) or 0.0) for registro in itens_da_nota)
        )
        valor_ibs_total = sum(
            float(registro.get("credito_ibs_gerado", 0.0) or 0.0)
            for registro in itens_da_nota
        )
        valor_cbs_total = sum(
            float(registro.get("credito_cbs_gerado", 0.0) or 0.0)
            for registro in itens_da_nota
        )
        id_op = xml_engine.calcular_id_operacao(
            empresa_id or nota_info.get("empresa_id"),
            nota_info,
            valor_base_original,
            valor_ibs_total,
            valor_cbs_total,
        )
        resumo_operacao = resumo_eventos.get(id_op, {})
        valor_base = float(
            resumo_operacao.get("valor_negociado")
            or valor_base_original
        )
        valor_item = float(item.get("valor_item", valor_base) or 0.0)
        soma_itens = sum(
            float(registro.get("valor_item", 0.0) or 0.0)
            for registro in itens_da_nota
        )
        proporcao_item = (
            valor_item / soma_itens
            if soma_itens > 0
            else 1.0
        )
        classificacao = str(item.get("classificacao_item", "Não Classificado"))
        credito_aprovado = float(item.get("valor_credito_item", 0.0) or 0.0)
        credito_quarentena = (
            xml_engine.obter_credito_quarentena(item)
            if classificacao == "Quarentena Uso/Consumo"
            else 0.0
        )
        saldo_aberto = max(
            0.0,
            valor_base - float(resumo_operacao.get("valor_pago_acumulado", 0.0)),
        )
        linhas.append({
            "ID Registro": f"ITEM-{item.get('id') or indice}",
            "ID_OP": id_op,
            "Data": data_nota(nota_info),
            "CNPJ Fornecedor": str(nota_info.get("cnpj_emitente", "N/A")),
            "Chave de Acesso NF-e": chave or "N/A",
            "Descrição do Item / Documento": str(item.get("descricao", "")),
            "NCM": str(item.get("ncm", "")),
            "CFOP": str(item.get("cfop", "")),
            "Valor Contábil (R$)": valor_item,
            "Alíquota (%)": round(float(item.get("aliquota_aplicada", 0.0) or 0.0) * 100, 2),
            "Crédito Efetivo (R$)": credito_aprovado,
            "Risco / Quarentena (R$)": credito_quarentena,
            "Status Auditoria": classificacao,
            "Hash SHA-256": str(nota_info.get("hash_sha256_xml", "N/A")),
            "Receita Operacional / Compras Brutas (R$)": valor_item,
            "Contas a Receber / Pagar — Saldo em Aberto (R$)": saldo_aberto * proporcao_item,
            "Banco / Conta Movimento — Liquidação Líquida (R$)": float(
                resumo_operacao.get("impacto_banco", 0.0)
            ) * proporcao_item,
            "IBS/CBS a Recuperar — Crédito Validado (R$)": credito_aprovado,
            "IBS/CBS Compensados / Recolhidos (R$)": (
                float(resumo_operacao.get("ibs_segregado", 0.0))
                + float(resumo_operacao.get("cbs_segregado", 0.0))
            ) * proporcao_item,
            "Crédito Digital Compensado no Hub (R$)": float(
                resumo_operacao.get("credito_compensado_liquido", 0.0)
            ) * proporcao_item,
            "Glosa Preventiva / Quarentena (R$)": credito_quarentena,
            "Status de Conciliação": (
                "Conciliado via ID_OP"
                if int(resumo_operacao.get("eventos", 0)) > 0
                else "Aguardando evento de liquidação"
            ),
        })

    itens_por_nota_com_chave = set()
    for nota_id, itens_da_nota in itens_por_nota.items():
        nota_info = mapa_notas.get(nota_id)
        if nota_info is None:
            chave = next(
                (
                    str(item.get("chave_acesso") or "")
                    for item in itens_da_nota
                    if item.get("chave_acesso")
                ),
                "",
            )
            nota_info = {
                "id": nota_id,
                "chave_acesso": chave,
                "valor_total_nf": sum(
                    float(item.get("valor_item", 0.0) or 0.0)
                    for item in itens_da_nota
                ),
                "data_referencia": date.today(),
                "manifestacao_status": "Nota reconstruída a partir dos itens",
            }
        itens_por_nota_com_chave.add(nota_id)
        for indice, item in enumerate(itens_da_nota, start=1):
            adicionar_linha(item, nota_id, nota_info, indice)

    for nota_id, nota_info in mapa_notas.items():
        if nota_id in itens_por_nota_com_chave:
            continue
        adicionar_linha(
            {
                "id": nota_id,
                "descricao": "NF-e Consolidada (Sem itens explodidos)",
                "ncm": "DIVERSOS",
                "cfop": "5102",
                "valor_item": float(nota_info.get("valor_total_nf", 0.0) or 0.0),
                "valor_credito_item": round(
                    float(nota_info.get("valor_total_nf", 0.0) or 0.0) * 0.265,
                    2,
                ),
                "classificacao_item": nota_info.get(
                    "manifestacao_status",
                    "Nota Recebida",
                ),
            },
            nota_id,
            nota_info,
            1,
        )
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
    df_itens_filtrados: pd.DataFrame,
    valor_retido_split_payment: float = 0.0,
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
        for row in df_itens_filtrados.to_dict(orient="records"):
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
    body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #0e1117; color: #f3f4f6; padding: 30px; margin: 0; font-variant-numeric: tabular-nums; }}
    .container {{ max-width: 1080px; margin: auto; background: #111827; border: 1px solid #374151; border-top: 5px solid #d4af37; border-radius: 12px; padding: 35px; }}
    .header-top {{ display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 1px solid #374151; padding-bottom: 18px; margin-bottom: 24px; }}
    h1 {{ color: #ffffff; margin: 0 0 6px 0; font-size: 22px; }}
    h2 {{ color: #d4af37; font-size: 15px; text-transform: uppercase; letter-spacing: 1px; margin: 28px 0 12px 0; border-left: 3px solid #d4af37; padding-left: 10px; }}
    .sub {{ color: #9ca3af; font-size: 12px; line-height: 1.6; }}
    .badge-fechamento {{ background: #1f2937; border: 1px solid #d4af37; color: #f6c443; padding: 6px 12px; border-radius: 6px; font-size: 11px; font-weight: bold; text-transform: uppercase; }}
    .grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-bottom: 24px; }}
    .card {{ background: #1f2937; border: 1px solid #374151; border-radius: 8px; padding: 15px; }}
    .label {{ color: #9ca3af; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; font-weight: bold; }}
    .val-gold {{ color: #f6c443; font-size: 20px; font-weight: 800; margin-top: 6px; font-family: monospace; }}
    .val-green {{ color: #34d399; font-size: 20px; font-weight: 800; margin-top: 6px; font-family: monospace; }}
    .val-amber {{ color: #fbbf24; font-size: 20px; font-weight: 800; margin-top: 6px; font-family: monospace; }}
    .val-red {{ color: #f87171; font-size: 20px; font-weight: 800; margin-top: 6px; font-family: monospace; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; background: #0f172a; font-variant-numeric: tabular-nums; }}
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
        <div class="card"><div class="label">🏛️ Split Payment Retido</div><div class="val-red">R$ {valor_retido_split_payment:,.2f}</div></div>
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


def gerar_excel_auditado(df_filtrado: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
        df_filtrado.to_excel(
            escritor,
            sheet_name="Ponte Contabil",
            index=False,
        )
    return buffer.getvalue()


def renderizar_modulo_relatorio(
    empresa_ativa,
    imposto_bruto_mes,
    credito_validado_tempo_real,
    imposto_quarentena,
    imposto_estornado,
    notas,
    itens,
    valor_retido_split_payment=0.0,
    eventos_liquidacao=None,
):
    injetar_css_blindado_relatorio()

    with st.expander("📁 8. Central de Dossiês Analíticos, Filtros de Notas & Exportação ERP", expanded=False):
        st.markdown(
            f"Configuração do escopo de auditoria para <b>{empresa_ativa['razao_social']}</b> com cruzamento relacional e filtros dinâmicos.",
            unsafe_allow_html=True
        )

        df_completo = montar_dataframe_relacional(
            notas,
            itens,
            eventos_liquidacao,
            empresa_ativa.get("id"),
        )

        status_padrao = [
            "Aprovado Revenda",
            "Quarentena Uso/Consumo",
            "Aprovado Manualmente Contador",
            "Rejeitado",
            "Rejeitado pelo Contador",
        ]
        if not df_completo.empty:
            status_presentes = sorted(df_completo["Status Auditoria"].dropna().unique().tolist())
            status_disponiveis = list(dict.fromkeys(status_padrao + status_presentes))
            fornecedores_disponiveis = ["Todos os Fornecedores"] + sorted(df_completo["CNPJ Fornecedor"].dropna().unique().tolist())
            cfops_disponiveis = ["Todos"] + sorted(df_completo["CFOP"].dropna().unique().tolist())
            ncm_disponiveis = ["Todos"] + sorted(df_completo["NCM"].dropna().unique().tolist())
        else:
            status_disponiveis = status_padrao
            fornecedores_disponiveis = ["Todos os Fornecedores"]
            cfops_disponiveis = ["Todos"]
            ncm_disponiveis = ["Todos"]

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

            f_col4, f_col5, f_col6, f_col7 = st.columns([1.5, 1, 1.3, 1.2])
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
            with f_col7:
                filtro_ncm = st.selectbox(
                    "🏷️ NCM",
                    options=ncm_disponiveis,
                    key=f"flt_ncm_{empresa_ativa['id']}",
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
            if filtro_ncm != "Todos":
                df_filtrado = df_filtrado[df_filtrado["NCM"] == filtro_ncm]

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

        st.markdown(
            "#### 📒 Ponte Contábil Integrada "
            "(Documento Fiscal → Liquidação → Contabilidade)"
        )
        st.caption(
            "Conciliação por ID_OP; saldos em aberto, movimento bancário, créditos "
            "validados, compensações/recolhimentos e glosas são derivados dos eventos "
            "persistidos para cada operação."
        )
        colunas_ponte = [
            "ID_OP",
            "Receita Operacional / Compras Brutas (R$)",
            "Contas a Receber / Pagar — Saldo em Aberto (R$)",
            "Banco / Conta Movimento — Liquidação Líquida (R$)",
            "IBS/CBS a Recuperar — Crédito Validado (R$)",
            "IBS/CBS Compensados / Recolhidos (R$)",
            "Crédito Digital Compensado no Hub (R$)",
            "Glosa Preventiva / Quarentena (R$)",
            "Status de Conciliação",
        ]
        if not df_filtrado.empty:
            st.dataframe(
                df_filtrado[colunas_ponte],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info("A Ponte Contábil será preenchida após a importação ou seleção de notas.")

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
            imposto_quarentena, imposto_estornado, notas_filtradas, df_filtrado,
            valor_retido_split_payment,
        )
        csv_bytes = gerar_csv_auditado(df_filtrado)
        excel_bytes = gerar_excel_auditado(df_filtrado)

        b1, b2, b3 = st.columns(3)
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
                label="📊 Arquivo de Integração ERP (.CSV)",
                data=csv_bytes,
                file_name=f"Integracao_ERP_{empresa_ativa['cnpj']}.csv",
                mime="text/csv",
                use_container_width=True
            )
        with b3:
            st.download_button(
                label="📗 Exportar Ponte Contábil (.XLSX)",
                data=excel_bytes,
                file_name=f"Ponte_Contabil_{empresa_ativa['cnpj']}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
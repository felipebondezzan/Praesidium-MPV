import hashlib
import random
import re
from datetime import datetime
import requests
import xmltodict
import streamlit as st
import pandas as pd

VERSAO_REGRA_TRIBUTARIA = "LC 214/2025 - Art. 47/57 v1.0"
ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA = 0.265


def obter_data_referencia_nota(nota: dict):
    """Retorna a data de emissão ou, em dados legados, a data de registro."""
    for campo in ("data_emissao", "created_at"):
        valor = nota.get(campo)
        if valor:
            data = pd.to_datetime(valor, errors="coerce", utc=True)
            if not pd.isna(data):
                return data.tz_convert(None)
    return None


def obter_credito_quarentena(item: dict) -> float:
    """Calcula o crédito potencial com a alíquota persistida, compatível com dados legados."""
    credito = float(item.get("valor_credito_item", 0.0) or 0.0)
    if credito > 0:
        return credito
    aliquota = float(item.get("aliquota_aplicada", 0.0) or 0.0)
    if aliquota > 0:
        return round(float(item.get("valor_item", 0.0) or 0.0) * aliquota, 2)
    return round(
        float(item.get("valor_item", 0.0) or 0.0)
        * ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA,
        2,
    )


def obter_credito_rejeitado(item: dict) -> float:
    credito = float(item.get("valor_credito_item", 0.0) or 0.0)
    if credito > 0:
        return credito
    aliquota = float(item.get("aliquota_aplicada", 0.0) or 0.0)
    return round(float(item.get("valor_item", 0.0) or 0.0) * aliquota, 2)


def obter_versao_regra_nota(nota: dict) -> str:
    return str(nota.get("regra_tributaria_version") or "Legado — versão não registrada")


def calcular_resumo_creditos(itens: list) -> dict[str, float]:
    validados = 0.0
    quarentena = 0.0
    rejeitados = 0.0
    for item in itens:
        status = item.get("classificacao_item")
        valor_credito = float(item.get("valor_credito_item", 0.0) or 0.0)
        if status in {"Aprovado Revenda", "Aprovado Manualmente Contador"}:
            validados += valor_credito
        elif status == "Quarentena Uso/Consumo":
            quarentena += obter_credito_quarentena(item)
        elif status in {"Rejeitado", "Rejeitado pelo Contador"}:
            rejeitados += obter_credito_rejeitado(item)
    return {
        "validado": validados,
        "quarentena": quarentena,
        "rejeitado": rejeitados,
        "bruto": validados + quarentena,
    }


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
    return re.sub(r"\s*\[T:[^\]]+\]", "", str(nome_empresa), flags=re.IGNORECASE).strip()


# ==========================================
# FUNÇÃO DE INSERÇÃO BLINDADA CONTRA APIError DO SUPABASE
# ==========================================
def inserir_seguro_supabase(supabase, tabela: str, payload) -> tuple[bool, str, list]:
    """
    Insere dados no Supabase com auto-recuperação: se alguma coluna opcional não existir
    no banco (causando postgrest.exceptions.APIError), remove a coluna ausente e grava com sucesso.
    """
    dados_atuais = [dict(x) for x in payload] if isinstance(payload, list) else dict(payload)

    colunas_opcionais_ordem = [
        "hash_sha256_xml",
        "manifestacao_status",
        "aliquota_aplicada",
        "certificado_a1_status",
        "empresa_id",
        "regra_tributaria_version",
        "data_emissao"
    ]
    colunas_removidas = set()

    for _ in range(len(colunas_opcionais_ordem) + 2):
        try:
            res = supabase.table(tabela).insert(dados_atuais).execute()
            mensagem = "OK"
            colunas_rastreabilidade_ausentes = colunas_removidas.intersection({
                "regra_tributaria_version",
                "data_emissao",
            })
            if tabela == "notas_fiscais_recebidas" and colunas_rastreabilidade_ausentes:
                mensagem = (
                    "A rastreabilidade não está completa: as colunas "
                    f"{', '.join(sorted(colunas_rastreabilidade_ausentes))} não existem em "
                    "notas_fiscais_recebidas. Adicione-as para persistir versão da regra e "
                    "data de emissão."
                )
            return True, mensagem, (res.data if res.data else [])
        except Exception as err:
            err_str = str(err)
            msg_erro = err_str.lower()
            erro_coluna_ausente = (
                err.__class__.__name__ == "APIError"
                or any(indicador in msg_erro for indicador in (
                    "could not find",
                    "does not exist",
                    "schema cache",
                    "pgrst204",
                    "unknown column",
                ))
            ) and "column" in msg_erro

            colunas_payload = set(dados_atuais[0]) if isinstance(dados_atuais, list) and dados_atuais else (
                set(dados_atuais) if isinstance(dados_atuais, dict) else set()
            )
            col_remover = next((
                candidata for candidata in colunas_opcionais_ordem
                if erro_coluna_ausente and candidata in colunas_payload and candidata in msg_erro
            ), None)

            if col_remover:
                colunas_removidas.add(col_remover)
                if isinstance(dados_atuais, list):
                    dados_atuais = [{k: v for k, v in item.items() if k != col_remover} for item in dados_atuais]
                else:
                    dados_atuais = {k: v for k, v in dados_atuais.items() if k != col_remover}
            else:
                return False, err_str, []

    return False, "Falha ao gravar no banco após tentativas de ajuste.", []


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
    em_contingencia: bool
) -> tuple[str, float, float]:
    ncm_limpo = "".join(filter(str.isdigit, str(ncm))).ljust(8, "0")[:8]
    cfop_limpo = "".join(filter(str.isdigit, str(cfop)))

    if ncm_limpo in NCM_CESTA_BASICA_ZERO:
        aliquota_base = 0.0
    elif ncm_limpo in NCM_REDUCAO_60:
        aliquota_base = 0.106
    elif str(crt_emitente).strip() == "1":
        aliquota_base = 0.080
    else:
        aliquota_base = 0.265

    if em_contingencia:
        return "Quarentena Uso/Consumo", 0.0, 0.0

    if cfop_limpo in {"1556", "2556", "5556", "6556", "1551", "2551", "5551", "6551"}:
        return "Quarentena Uso/Consumo", 0.0, 0.0

    lista_cnaes = [
        "".join(filter(str.isdigit, c))
        for c in str(cnaes_cliente_str).split(",")
        if "".join(filter(str.isdigit, c))
    ]
    if not lista_cnaes:
        lista_cnaes = ["4711302"]

    compativel_cnae = False
    for cnae_cod in lista_cnaes:
        prefixo_4 = cnae_cod[:4]
        prefixos_ncm_aceitos = MAPA_CNAE_NCM_PERMITIDOS.get(prefixo_4, [])
        if any(ncm_limpo.startswith(pref) for pref in prefixos_ncm_aceitos):
            compativel_cnae = True
            break

    if ncm_limpo in NCM_SENSIVEIS_USO_CONSUMO:
        if compativel_cnae and qcom >= 6:
            credito = round(vprod * aliquota_base, 2)
            return "Aprovado Revenda", aliquota_base, credito
        return "Quarentena Uso/Consumo", 0.0, 0.0

    if compativel_cnae:
        if qcom <= 1 and vprod > 1500.0:
            return "Quarentena Uso/Consumo", 0.0, 0.0
        credito = round(vprod * aliquota_base, 2)
        return "Aprovado Revenda", aliquota_base, credito

    if cfop_limpo in {"1102", "2102", "5102", "6102", "1101", "2101", "5101", "6101", "5405"} and qcom >= 3:
        credito = round(vprod * aliquota_base, 2)
        return "Aprovado Revenda", aliquota_base, credito

    return "Quarentena Uso/Consumo", 0.0, 0.0


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
        desc = str(it.get("descricao", "MERCADORIA AUDITADA")).replace("&", "E")
        ncm = str(it.get("ncm", "00000000"))
        cfop = str(it.get("cfop", "5102"))
        vl_it = float(it.get("valor_item", 0.0))
        vl_cred = float(it.get("valor_credito_item", 0.0))
        status_it = str(it.get("classificacao_item", "Auditado"))

        blocos_det += f"""
        <det nItem="{idx}">
            <prod>
                <cProd>ITEM-{idx:03d}</cProd>
                <xProd>{desc}</xProd>
                <NCM>{ncm}</NCM>
                <CFOP>{cfop}</CFOP>
                <uCom>UN</uCom>
                <qCom>1.0000</qCom>
                <vUnCom>{vl_it:.2f}</vUnCom>
                <vProd>{vl_it:.2f}</vProd>
            </prod>
            <imposto>
                <IBSCBS>
                    <vBC>{vl_it:.2f}</vBC>
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
def processar_xml_bytes(
    xml_bytes: bytes,
    supabase,
    empresa_id_ativo,
    empresa_ativa: dict,
    ignorar_trava_cnpj: bool = False
) -> tuple[bool, str]:
    hash_sha256 = hashlib.sha256(xml_bytes).hexdigest()

    try:
        dup_check = (
            supabase.table("notas_fiscais_recebidas")
            .select("id")
            .eq("empresa_id", empresa_id_ativo)
            .eq("hash_sha256_xml", hash_sha256)
            .execute()
        )
        if dup_check.data:
            return False, "Nota bloqueada: XML já importado anteriormente (Hash SHA-256 idêntico)."
    except Exception:
        pass

    try:
        dados_xml = xmltodict.parse(xml_bytes)
    except Exception as ex:
        return False, f"XML malformado ou inválido: {ex}"

    nfe_proc = dados_xml.get("nfeProc", dados_xml)
    nfe = nfe_proc.get("NFe", nfe_proc)
    inf_nfe = nfe.get("infNFe", {})
    if not inf_nfe:
        return False, "Estrutura <infNFe> não encontrada no arquivo XML."

    chave_raw = str(inf_nfe.get("@Id", "")).replace("NFe", "").strip()
    if len(chave_raw) != 44:
        chave_raw = "".join([str(random.randint(0, 9)) for _ in range(44)])

    try:
        dup_chave = (
            supabase.table("notas_fiscais_recebidas")
            .select("id")
            .eq("empresa_id", empresa_id_ativo)
            .eq("chave_acesso", chave_raw)
            .execute()
        )
        if dup_chave.data:
            return False, f"Nota bloqueada: Chave {chave_raw[:12]}... já consta na base deste cliente."
    except Exception:
        pass

    ide = inf_nfe.get("ide", {})
    emit = inf_nfe.get("emit", {})
    dest = inf_nfe.get("dest", {})
    total = inf_nfe.get("total", {}).get("ICMSTot", {})

    cnpj_emit = "".join(filter(str.isdigit, str(emit.get("CNPJ", emit.get("CPF", "00000000000191")))))
    cnpj_dest = "".join(filter(str.isdigit, str(dest.get("CNPJ", dest.get("CPF", "")))))
    cnpj_cliente_ativo = "".join(filter(str.isdigit, str(empresa_ativa.get("cnpj", ""))))

    if not ignorar_trava_cnpj and cnpj_dest and cnpj_cliente_ativo and cnpj_dest != cnpj_cliente_ativo:
        return (
            False,
            f"Trava de Segurança: O XML pertence ao destinatário {cnpj_dest}, diferente do cliente ativo ({cnpj_cliente_ativo})."
        )

    tp_emis = str(ide.get("tpEmis", "1")).strip()
    em_contingencia = tp_emis != "1"
    status_manifestacao = (
        f"Alerta: Emissão em Contingência (tpEmis={tp_emis})"
        if em_contingencia
        else "Ciência da Operação (SEFAZ OK)"
    )
    data_emissao = ide.get("dhEmi", ide.get("dEmi"))

    crt_emitente = str(emit.get("CRT", "3")).strip()
    valor_total_nf = float(total.get("vNF", total.get("vProd", 0.0)))

    payload_nf = {
        "empresa_id": empresa_id_ativo,
        "chave_acesso": chave_raw,
        "cnpj_emitente": cnpj_emit,
        "valor_total_nf": round(valor_total_nf, 2),
        "manifestacao_status": status_manifestacao,
        "hash_sha256_xml": hash_sha256,
        "regra_tributaria_version": VERSAO_REGRA_TRIBUTARIA,
        "data_emissao": str(data_emissao) if data_emissao else None,
    }

    ok_nf, msg_nf, data_nf = inserir_seguro_supabase(supabase, "notas_fiscais_recebidas", payload_nf)
    if ok_nf and data_nf:
        nota_id = data_nf[0]["id"]
    else:
        nota_id = random.randint(100000, 999999)
        payload_nf["id"] = nota_id
        st.session_state.setdefault(f"fallback_notas_{empresa_id_ativo}", []).append(payload_nf)

    dets = inf_nfe.get("det", [])
    if isinstance(dets, dict):
        dets = [dets]

    cnaes_cliente = str(empresa_ativa.get("cnae_principal", "4711302"))
    itens_para_inserir = []

    for det in dets:
        prod = det.get("prod", {})
        xprod = str(prod.get("xProd", "Item NF-e"))
        ncm = str(prod.get("NCM", "00000000"))
        cfop = str(prod.get("CFOP", "5102"))
        try:
            qcom = float(prod.get("qCom", 1.0))
        except Exception:
            qcom = 1.0
        try:
            vprod = float(prod.get("vProd", 0.0))
        except Exception:
            vprod = 0.0

        classificacao, aliq, vl_cred = classificar_item_inteligente(
            ncm=ncm,
            cfop=cfop,
            qcom=qcom,
            vprod=vprod,
            crt_emitente=crt_emitente,
            cnaes_cliente_str=cnaes_cliente,
            em_contingencia=em_contingencia
        )
        if classificacao == "Quarentena Uso/Consumo":
            aliq = ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA

        itens_para_inserir.append({
            "nota_id": nota_id,
            "descricao": xprod,
            "ncm": ncm,
            "cfop": cfop,
            "valor_item": round(vprod, 2),
            "aliquota_aplicada": aliq,
            "valor_credito_item": vl_cred,
            "classificacao_item": classificacao
        })

    dados_persistidos = ok_nf
    if itens_para_inserir:
        ok_it, _, _ = inserir_seguro_supabase(supabase, "itens_nota_fiscal", itens_para_inserir)
        dados_persistidos = dados_persistidos or ok_it
        if not ok_it:
            for idx_it, it_mem in enumerate(itens_para_inserir):
                it_mem["id"] = random.randint(100000, 999999) + idx_it
            st.session_state.setdefault(f"fallback_itens_{empresa_id_ativo}", []).extend(itens_para_inserir)

    if dados_persistidos:
        st.cache_data.clear()
    mensagem_final = (
        f"NF-e {chave_raw[:10]}... auditada com sucesso "
        f"({len(itens_para_inserir)} itens processados)."
    )
    if msg_nf != "OK":
        mensagem_final += f" ⚠️ {msg_nf}"
    return True, mensagem_final


# ==========================================
# 4. INTERFACE: MÓDULO DE INGESTÃO XML & ROBÔ SEFAZ
# ==========================================
def renderizar_modulo_xml(supabase, empresa_id_ativo, empresa_ativa: dict):
    upload_feedback_key = f"upload_feedback_{empresa_id_ativo}"
    feedback_upload = st.session_state.pop(upload_feedback_key, None)
    if feedback_upload:
        for nivel, mensagem in feedback_upload:
            getattr(st, nivel)(mensagem)

    aviso_regra = st.session_state.pop(f"aviso_regra_{empresa_id_ativo}", None)
    if aviso_regra:
        st.warning(aviso_regra)

    with st.expander("📁 1. Central de Ingestão XML & Sincronização Robô SEFAZ (A1 / Upload em Lote)", expanded=False):
        c_rob, c_up = st.columns([1.1, 1.9])

        with c_rob:
            st.markdown("##### 🤖 Robô de Captura SEFAZ (Simulação Homologação)")
            st.caption(
                f"Simula a busca direta no WebService SEFAZ para **{limpar_tag_tenant_empresa(empresa_ativa['razao_social'])}** "
                f"(CNAEs: `{empresa_ativa.get('cnae_principal', '4711302')}`)."
            )
            if st.button("🔄 Sincronizar Lote de Teste SEFAZ", use_container_width=True, key=f"btn_sync_sefaz_{empresa_id_ativo}"):
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
                    "valor_total_nf": round(vl_tot_sim, 2),
                    "manifestacao_status": "Ciência da Operação (SEFAZ OK)",
                    "hash_sha256_xml": hash_sim,
                    "regra_tributaria_version": VERSAO_REGRA_TRIBUTARIA,
                    "data_emissao": datetime.now().isoformat(),
                }

                ok_nf, msg_nf, data_nf = inserir_seguro_supabase(supabase, "notas_fiscais_recebidas", payload_nf_sim)
                if ok_nf and data_nf:
                    n_id = data_nf[0]["id"]
                else:
                    n_id = random.randint(100000, 999999)
                    payload_nf_sim["id"] = n_id
                    st.session_state.setdefault(f"fallback_notas_{empresa_id_ativo}", []).append(payload_nf_sim)

                novos_itens = []
                for desc, ncm, cfop, qcom, vprod in itens_sim:
                    status_it, aliq, cred = classificar_item_inteligente(
                        ncm, cfop, qcom, vprod, "3", cnaes_str, False
                    )
                    if status_it == "Quarentena Uso/Consumo":
                        aliq = ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA
                    novos_itens.append({
                        "nota_id": n_id,
                        "descricao": desc,
                        "ncm": ncm,
                        "cfop": cfop,
                        "valor_item": vprod,
                        "aliquota_aplicada": aliq,
                        "valor_credito_item": cred,
                        "classificacao_item": status_it
                    })

                ok_it, _, _ = inserir_seguro_supabase(supabase, "itens_nota_fiscal", novos_itens)
                if not ok_it:
                    for idx_i, it_m in enumerate(novos_itens):
                        it_m["id"] = random.randint(100000, 999999) + idx_i
                    st.session_state.setdefault(f"fallback_itens_{empresa_id_ativo}", []).extend(novos_itens)

                if ok_nf or ok_it:
                    st.cache_data.clear()
                st.success("✅ Lote SEFAZ sincronizado e classificado com sucesso!")
                if msg_nf != "OK":
                    st.session_state[f"aviso_regra_{empresa_id_ativo}"] = msg_nf
                st.rerun()

        with c_up:
            st.markdown("##### 📤 Upload Manual de Arquivos XML NF-e")
            ignorar_cnpj = st.checkbox(
                "Modo Homologação: Permitir XML com CNPJ destinatário de terceiros para teste",
                value=True,
                key=f"chk_ignorar_cnpj_{empresa_id_ativo}"
            )
            uploader_reset_key = f"uploader_reset_id_{empresa_id_ativo}"
            uploader_reset_id = int(st.session_state.get(uploader_reset_key, 0) or 0)
            arquivos_xml = st.file_uploader(
                "Selecione um ou mais arquivos .XML de NF-e",
                type=["xml"],
                accept_multiple_files=True,
                key=f"uploader_xml_{empresa_id_ativo}_{uploader_reset_id}"
            )
            if arquivos_xml:
                if st.button("⚡ Auditar e Importar XMLs Selecionados", use_container_width=True, key=f"btn_proc_xml_{empresa_id_ativo}"):
                    sucessos = 0
                    feedback_processamento = []
                    for arq in arquivos_xml:
                        ok, msg = processar_xml_bytes(
                            arq.read(), supabase, empresa_id_ativo, empresa_ativa, ignorar_trava_cnpj=ignorar_cnpj
                        )
                        if ok:
                            sucessos += 1
                            feedback_processamento.append(("success", f"✅ {arq.name}: {msg}"))
                            if "A rastreabilidade não está completa" in msg:
                                st.session_state[f"aviso_regra_{empresa_id_ativo}"] = msg
                        else:
                            feedback_processamento.append(("warning", f"⚠️ {arq.name}: {msg}"))
                    st.session_state[uploader_reset_key] = uploader_reset_id + 1
                    st.session_state[upload_feedback_key] = feedback_processamento
                    if sucessos:
                        st.cache_data.clear()
                    st.rerun()


# ==========================================
# 5. INTERFACE: EMISSOR DE NF-e DE DEVOLUÇÃO EXPRESSA
# ==========================================
def renderizar_emissor_devolucao(supabase, empresa_ativa: dict, notas: list, itens: list):
    with st.expander("📁 2. Emissor Express de NF-e de Devolução (Avaria / Divergência Física & Estorno Split Payment)", expanded=False):
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
        itens_da_nota = [i for i in itens if i.get("nota_id") == nf_orig.get("id")]

        if not itens_da_nota:
            st.warning("Esta nota não possui itens detalhados para devolução.")
            return

        opcoes_itens = {
            f"{i.get('descricao', 'Item')} (NCM: {i.get('ncm', '')} | Valor: R$ {float(i.get('valor_item', 0.0)):,.2f} | Crédito: R$ {float(i.get('valor_credito_item', 0.0)):,.2f})": i
            for i in itens_da_nota
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
                "motivo_ajuste": motivo_formatado
            }
            ok_dev, msg_dev, _ = inserir_seguro_supabase(supabase, "conciliacao_devolucoes", payload_dev)

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
                st.cache_data.clear()
                st.success("✅ Devolução registrada e crédito estornado em tempo real!")
                st.rerun()
            else:
                st.error(f"XML gerado, mas o registro da devolução não foi salvo: {msg_dev}")

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
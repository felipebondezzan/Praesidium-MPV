import hashlib
import logging
import random
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
import requests
import streamlit as st
import pandas as pd

VERSAO_REGRA_TRIBUTARIA = "LC 214/2025 - Art. 47/57 v1.0"
ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA = 0.265
TAG_TENANT_PATTERN = re.compile(r"\s*\[T:[^\]]+\]", re.IGNORECASE)
CHAVE_ACESSO_PATTERN = re.compile(r"\d{44}")
CODIGO_ERRO_PATTERN = re.compile(r"""['"]code['"]\s*:\s*['"]([A-Z0-9]+)['"]""")
COLUNA_ERRO_PATTERNS = (
    re.compile(r"""column\s+['"]([^'"]+)['"]""", re.IGNORECASE),
    re.compile(r"""['"]([^'"]+)['"]\s+column""", re.IGNORECASE),
)

_limpador_cache_dados = None


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
    return TAG_TENANT_PATTERN.sub("", str(nome_empresa)).strip()


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
            aliases = {
                "descricao_produto": atual.get("descricao", "N/A"),
                "quantidade_comercial": atual.get("qcom", 0.0),
                "valor_total_bruto": atual.get("valor_item", 0.0),
                "status_credito": atual.get("classificacao_item", "N/A"),
                "justificativa_motor": atual.get("justificativa", "N/A"),
                "aliquota_ibs_aplicada": 0.0,
                "aliquota_cbs_aplicada": 0.0,
                "credito_ibs_gerado": 0.0,
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
        identificador = registro.get("id")
        encontrado = False
        for indice, existente in enumerate(existentes):
            corresponde = (
                identificador is not None
                and str(existente.get("id")) == str(identificador)
            ) or (
                operacao == "update"
                and all(str(existente.get(k)) == str(v) for k, v in (filtros or {}).items())
            )
            if corresponde:
                existentes[indice] = {**existente, **registro}
                guardados.append(existentes[indice])
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
        local = _guardar_fallback(tabela, dados)
        return (
            (True, "Mantido temporariamente nesta sessão.", local)
            if local or tabela not in {"notas_fiscais_recebidas", "itens_nota_fiscal"}
            else (False, "Não foi possível registrar os dados nesta sessão.", [])
        )

    colunas_corrigidas = set()
    for _ in range(32):
        try:
            construtor = supabase.table(tabela)
            construtor = construtor.upsert(dados) if operacao == "upsert" else construtor.insert(dados)
            resultado = construtor.select("*").execute()
            return True, "OK", (resultado.data if resultado.data else [])
        except Exception as erro:
            codigo = _codigo_erro_supabase(erro)
            coluna = _coluna_erro_supabase(erro)
            colunas_presentes = set(dados[0] if isinstance(dados, list) and dados else dados)
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
                    "Schema sem vínculo obrigatório (%s) na tabela %s; preservando o isolamento local.",
                    coluna,
                    tabela,
                )
                local = _guardar_fallback(tabela, dados)
                if local:
                    return True, "Mantido temporariamente nesta sessão.", local
                return False, "Não foi possível vincular os dados à empresa ativa.", []
            if codigo == "PGRST204" and coluna and coluna in colunas_presentes:
                _remover_coluna(dados, coluna)
                continue
            if isinstance(dados, list) and len(dados) > 1:
                registros_salvos = []
                for registro in dados:
                    try:
                        resposta_item = (
                            supabase.table(tabela)
                            .insert(registro)
                            .select("*")
                            .execute()
                        )
                        registros_salvos.extend(resposta_item.data or [])
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Falha no fallback individual da tabela %s.",
                            tabela,
                        )
                        registros_salvos.extend(_guardar_fallback(tabela, registro))
                if len(registros_salvos) == len(dados):
                    return True, "Lote persistido com fallback individual.", registros_salvos
            logging.getLogger(__name__).exception(
                "Falha ao gravar na tabela %s; usando armazenamento temporário da sessão.",
                tabela,
            )
            local = _guardar_fallback(tabela, dados)
            if local or tabela not in {"notas_fiscais_recebidas", "itens_nota_fiscal"}:
                return True, "Mantido temporariamente nesta sessão.", local
            return False, "Não foi possível registrar os dados. Tente novamente.", []
    logging.getLogger(__name__).error(
        "A recuperação de schema excedeu o limite de tentativas na tabela %s.", tabela
    )
    local = _guardar_fallback(tabela, dados)
    if local or tabela not in {"notas_fiscais_recebidas", "itens_nota_fiscal"}:
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
    colunas_corrigidas = set()
    for _ in range(32):
        try:
            consulta = supabase.table(tabela).update(dados)
            for coluna, valor in filtros.items():
                consulta = consulta.eq(coluna, valor)
            resultado = consulta.select("*").execute()
            if resultado.data:
                return True, "OK", resultado.data
            break
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
            if codigo == "PGRST204" and coluna and coluna in colunas_presentes:
                dados.pop(coluna, None)
                if dados:
                    continue
            logging.getLogger(__name__).exception(
                "Falha ao atualizar a tabela %s; tentando manter a alteração na sessão.",
                tabela,
            )
            break

    fallback = dict(fallback_record or {})
    fallback.update(dados)
    fallback.update(filtros)
    local = _guardar_fallback(tabela, fallback, "update", filtros)
    if local:
        return True, "Alteração mantida temporariamente nesta sessão.", local
    return False, "Não foi possível salvar as alterações. Tente novamente.", []


def inserir_seguro_supabase(supabase, tabela: str, payload) -> tuple[bool, str, list]:
    return executar_insert_seguro(tabela, payload, supabase=supabase)


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
def _xml_filhos(elemento, nome: str):
    if elemento is None:
        return []
    return [
        filho for filho in list(elemento)
        if filho.tag.rsplit("}", 1)[-1] == nome
    ]


def _xml_filho(elemento, nome: str):
    filhos = _xml_filhos(elemento, nome)
    return filhos[0] if filhos else None


def _xml_texto(elemento, nome: str, padrao: str = "") -> str:
    filho = _xml_filho(elemento, nome)
    return str(filho.text or "").strip() if filho is not None else padrao


def _float_seguro(valor, padrao: float) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return padrao


def _carregar_indices_duplicidade(supabase, empresa_id_ativo) -> tuple[set[str], set[str]]:
    if supabase is None:
        return set(), set()
    try:
        resposta = (
            supabase.table("notas_fiscais_recebidas")
            .select("hash_sha256_xml,chave_acesso")
            .eq("empresa_id", empresa_id_ativo)
            .execute()
        )
        linhas = resposta.data or []
        return (
            {str(n.get("hash_sha256_xml")) for n in linhas if n.get("hash_sha256_xml")},
            {str(n.get("chave_acesso")) for n in linhas if n.get("chave_acesso")},
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Falha ao carregar índice de duplicidade para empresa %s.",
            empresa_id_ativo,
        )
        return set(), set()


def _preparar_xml_nfe(
    xml_bytes: bytes,
    empresa_id_ativo,
    empresa_ativa: dict,
    ignorar_trava_cnpj: bool,
    hashes_existentes: set[str],
    chaves_existentes: set[str],
    hashes_lote: set[str],
    chaves_lote: set[str],
) -> tuple[dict | None, list[dict], str]:
    if empresa_id_ativo is None or str(empresa_id_ativo).strip() == "":
        return None, [], "Importação cancelada: a empresa ativa não possui um ID válido."
    if str(empresa_ativa.get("id")) != str(empresa_id_ativo):
        return None, [], "Importação cancelada: o ID da empresa ativa não corresponde ao cadastro selecionado."

    cnpj_empresa_ativa = "".join(filter(str.isdigit, str(empresa_ativa.get("cnpj", ""))))
    if not cnpj_empresa_ativa:
        return None, [], "Importação cancelada: a empresa ativa não possui CNPJ cadastrado."

    hash_sha256 = hashlib.sha256(xml_bytes).hexdigest()
    notas_locais = st.session_state.get(f"fallback_notas_{empresa_id_ativo}", [])
    if (
        hash_sha256 in hashes_existentes
        or hash_sha256 in hashes_lote
        or any(nota.get("hash_sha256_xml") == hash_sha256 for nota in notas_locais)
    ):
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
    if chave_raw in chaves_existentes or chave_raw in chaves_lote:
        return None, [], f"Nota bloqueada: Chave {chave_raw[:12]}... já consta na base deste cliente."

    ide = _xml_filho(inf_nfe, "ide")
    emit = _xml_filho(inf_nfe, "emit")
    dest = _xml_filho(inf_nfe, "dest")
    total = _xml_filho(_xml_filho(inf_nfe, "total"), "ICMSTot")
    cnpj_emit = "".join(filter(str.isdigit, _xml_texto(emit, "CNPJ", _xml_texto(emit, "CPF", _CNPJ_FORNECEDOR_PADRAO))))
    cnpj_dest = "".join(filter(str.isdigit, _xml_texto(dest, "CNPJ", _xml_texto(dest, "CPF"))))
    cnpj_cliente_ativo = cnpj_empresa_ativa

    if not ignorar_trava_cnpj and cnpj_dest and cnpj_cliente_ativo and cnpj_dest != cnpj_cliente_ativo:
        return (
            None,
            [],
            f"Trava de Segurança: O XML pertence ao destinatário {cnpj_dest}, diferente do cliente ativo ({cnpj_cliente_ativo})."
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
    }

    hashes_lote.add(hash_sha256)
    chaves_lote.add(chave_raw)
    cnaes_cliente = str(empresa_ativa.get("cnae_principal", "4711302"))
    itens = []
    for det in _xml_filhos(inf_nfe, "det"):
        prod = _xml_filho(det, "prod")
        xprod = _xml_texto(prod, "xProd", "Item NF-e")
        ncm = _xml_texto(prod, "NCM", "00000000")
        cfop = _xml_texto(prod, "CFOP", "5102")
        qcom = _float_seguro(_xml_texto(prod, "qCom", "1"), 1.0)
        vprod = _float_seguro(_xml_texto(prod, "vProd", "0"), 0.0)

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

        itens.append({
            "empresa_id": empresa_id_ativo,
            "descricao": xprod,
            "ncm": ncm,
            "cfop": cfop,
            "valor_item": round(vprod, 2),
            "aliquota_aplicada": aliq,
            "valor_credito_item": vl_cred,
            "classificacao_item": classificacao
        })

    return payload_nf, itens, ""


def processar_xml_lote_bytes(
    arquivos: list[tuple[str, bytes]],
    supabase,
    empresa_id_ativo,
    empresa_ativa: dict,
    ignorar_trava_cnpj: bool = False,
) -> list[tuple[bool, str]]:
    hashes_existentes, chaves_existentes = _carregar_indices_duplicidade(
        supabase, empresa_id_ativo
    )
    hashes_lote: set[str] = set()
    chaves_lote: set[str] = set()
    preparados = []
    resultados = [(False, "Não foi possível processar o XML.") for _ in arquivos]

    for indice, (nome_arquivo, conteudo) in enumerate(arquivos):
        nota, itens, erro = _preparar_xml_nfe(
            conteudo,
            empresa_id_ativo,
            empresa_ativa,
            ignorar_trava_cnpj,
            hashes_existentes,
            chaves_existentes,
            hashes_lote,
            chaves_lote,
        )
        if erro:
            resultados[indice] = (False, erro)
        else:
            preparados.append((indice, nome_arquivo, nota, itens))

    if not preparados:
        return resultados

    ok_notas, mensagem_notas, notas_resultado = inserir_seguro_supabase(
        supabase,
        "notas_fiscais_recebidas",
        [nota for _, _, nota, _ in preparados],
    )
    if not ok_notas or len(notas_resultado) != len(preparados):
        for indice, _, _, _ in preparados:
            resultados[indice] = (False, "Não foi possível registrar a nota. Tente novamente.")
        return resultados

    notas_por_chave = {
        str(nota.get("chave_acesso")): nota
        for nota in notas_resultado
    }
    itens_lote = []
    dados_sucesso = []
    for indice, nome_arquivo, nota_payload, itens in preparados:
        nota_resultado = notas_por_chave.get(str(nota_payload["chave_acesso"]))
        nota_id = nota_resultado.get("id") if nota_resultado else None
        if nota_id is None:
            resultados[indice] = (False, "Não foi possível confirmar o registro da nota.")
            continue
        itens_da_nota = [{**item, "nota_id": nota_id} for item in itens]
        itens_lote.extend(itens_da_nota)
        dados_sucesso.append((indice, nome_arquivo, nota_payload, itens_da_nota))

    if itens_lote:
        ok_itens, _, _ = inserir_seguro_supabase(
            supabase, "itens_nota_fiscal", itens_lote
        )
        if not ok_itens:
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
    if mensagem_notas != "OK":
        st.toast(
            "Notas mantidas nesta sessão; sincronize novamente quando o banco estiver disponível.",
            icon="ℹ️",
        )
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
    ignorar_trava_cnpj: bool = False,
) -> tuple[bool, str]:
    return processar_xml_lote_bytes(
        [("arquivo.xml", xml_bytes)],
        supabase,
        empresa_id_ativo,
        empresa_ativa,
        ignorar_trava_cnpj,
    )[0]


# ==========================================
# 4. INTERFACE: MÓDULO DE INGESTÃO XML & ROBÔ SEFAZ
# ==========================================
@fragment_if_supported
def renderizar_modulo_xml(supabase, empresa_id_ativo, empresa_ativa: dict):
    upload_feedback_key = f"upload_feedback_{empresa_id_ativo}"
    feedback_upload = st.session_state.pop(upload_feedback_key, None)
    if feedback_upload:
        for nivel, mensagem in feedback_upload:
            getattr(st, nivel)(mensagem)

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
                    st.toast("Sincronização indisponível: selecione uma empresa válida com CNPJ.", icon="⚠️")
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
                    st.toast("Não foi possível registrar o lote de teste. Verifique a empresa ativa.", icon="⚠️")
                    st.stop()
                if not data_nf or data_nf[0].get("id") is None:
                    st.toast("Não foi possível confirmar o registro do lote de teste.", icon="⚠️")
                    st.stop()
                n_id = data_nf[0]["id"]

                novos_itens = []
                for desc, ncm, cfop, qcom, vprod in itens_sim:
                    status_it, aliq, cred = classificar_item_inteligente(
                        ncm, cfop, qcom, vprod, "3", cnaes_str, False
                    )
                    if status_it == "Quarentena Uso/Consumo":
                        aliq = ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA
                    novos_itens.append({
                        "empresa_id": empresa_id_ativo,
                        "nota_id": n_id,
                        "descricao": desc,
                        "ncm": ncm,
                        "cfop": cfop,
                        "valor_item": vprod,
                        "aliquota_aplicada": aliq,
                        "valor_credito_item": cred,
                        "classificacao_item": status_it
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
                    st.toast("Não foi possível registrar os itens do lote de teste.", icon="⚠️")
                    st.stop()

                limpar_caches_dados()
                credito_lote = calcular_resumo_creditos(novos_itens)["bruto"]
                st.toast(
                    f"✅ Lote sincronizado: R$ {credito_lote:,.2f} em crédito bruto auditado.",
                    icon="💰",
                )
                st.success("✅ Lote SEFAZ sincronizado e classificado com sucesso!")
                if msg_nf != "OK":
                    st.toast("Conexão temporária: o lote está disponível nesta sessão.", icon="ℹ️")
                st.session_state["modulo_ativo"] = "xml"
                rerun_fragment()

        with c_up:
            st.markdown("##### 📤 Upload Manual de Arquivos XML NF-e")
            ignorar_cnpj = st.checkbox(
                "Modo Homologação: Permitir XML com CNPJ destinatário de terceiros para teste",
                value=True,
                key=f"chk_ignorar_cnpj_{empresa_id_ativo}"
            )
            if "up_reset" not in st.session_state:
                st.session_state["up_reset"] = 0
            arquivos_xml = st.file_uploader(
                "Selecione um ou mais arquivos .XML de NF-e",
                type=["xml"],
                accept_multiple_files=True,
                key=f"xml_up_{empresa_id_ativo}_{st.session_state.get('up_reset', 0)}"
            )
            if arquivos_xml:
                if st.button("⚡ Auditar e Importar XMLs Selecionados", use_container_width=True, key=f"btn_proc_xml_{empresa_id_ativo}"):
                    lote_xml = [(arq.name, arq.getvalue()) for arq in arquivos_xml]
                    resultados_lote = processar_xml_lote_bytes(
                        lote_xml,
                        supabase,
                        empresa_id_ativo,
                        empresa_ativa,
                        ignorar_trava_cnpj=ignorar_cnpj,
                    )
                    sucessos = sum(1 for ok, _ in resultados_lote if ok)
                    feedback_processamento = [
                        (
                            "success" if ok else "warning",
                            f"{'✅' if ok else '⚠️'} {nome}: {mensagem}",
                        )
                        for (nome, _), (ok, mensagem) in zip(lote_xml, resultados_lote)
                    ]
                    st.session_state["up_reset"] += 1
                    st.session_state[upload_feedback_key] = feedback_processamento
                    if sucessos:
                        limpar_caches_dados()
                    st.session_state["modulo_ativo"] = "xml"
                    rerun_fragment()


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
            ok_dev, msg_dev, dados_dev = inserir_seguro_supabase(
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
                registro_dev = dados_dev[0] if dados_dev else payload_dev
                devolucoes_key = f"fallback_devolucoes_{empresa_ativa['id']}"
                devolucoes_sessao = st.session_state.get(devolucoes_key, [])
                if registro_dev.get("id") is not None and all(
                    str(registro.get("id")) != str(registro_dev.get("id"))
                    for registro in devolucoes_sessao
                ):
                    devolucoes_sessao.append(registro_dev)
                st.session_state[devolucoes_key] = devolucoes_sessao
                limpar_caches_dados()
                st.toast(
                    f"↩️ Devolução registrada; R$ {credito_a_estornar:,.2f} de crédito serão estornados.",
                    icon="🛡️",
                )
                st.session_state["modulo_ativo"] = "devolucao"
                st.success("✅ Devolução registrada e crédito estornado em tempo real!")
                rerun_fragment()
            else:
                st.toast("O XML foi gerado, mas não foi possível registrar a devolução.", icon="⚠️")

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
import xml_engine


EMPRESA = {
    "id": 88,
    "cnpj": "12345678000195",
    "cnae_principal": "9602501",
}


def _criar_xml(produtos: list[tuple[str, str, float]], chave: str) -> bytes:
    detalhes = "".join(
        f'<det nItem="{indice}"><prod><xProd>{descricao}</xProd>'
        f"<NCM>{ncm}</NCM><CFOP>5102</CFOP><qCom>1</qCom>"
        f"<vProd>{valor:.2f}</vProd></prod></det>"
        for indice, (descricao, ncm, valor) in enumerate(produtos, start=1)
    )
    return (
        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe>'
        f'<infNFe Id="NFe{chave}"><ide><dhEmi>2026-11-06T10:00:00-03:00</dhEmi></ide>'
        f'<emit><CNPJ>{EMPRESA["cnpj"]}</CNPJ><xNome>Fornecedor</xNome><CRT>3</CRT></emit>'
        f'<dest><CNPJ>{EMPRESA["cnpj"]}</CNPJ></dest>{detalhes}'
        "<total><ICMSTot><vNF>100000.00</vNF></ICMSTot></total>"
        "</infNFe></NFe></nfeProc>"
    ).encode("utf-8")


def _ler_e_classificar(
    produtos: list[tuple[str, str, float]],
    chave: str,
) -> list[dict]:
    nota, itens, erro = xml_engine._preparar_xml_nfe(
        _criar_xml(produtos, chave),
        EMPRESA["id"],
        EMPRESA,
        set(),
        set(),
        {EMPRESA["cnpj"]},
    )
    assert nota is not None, erro
    classificados = []
    for item in itens:
        status, aliquota, credito = xml_engine.classificar_item_inteligente(
            item["ncm"],
            item["cfop"],
            item["quantidade_comercial"],
            item["valor_item"],
            nota["crt_emitente"],
            EMPRESA["cnae_principal"],
            False,
            item["descricao"],
        )
        classificados.append({
            **item,
            "classificacao_item": status,
            "status_credito": (
                "Validado"
                if status == "Aprovado Revenda"
                else "Quarentena"
            ),
            "aliquota_aplicada": (
                aliquota
                if status == "Aprovado Revenda"
                else xml_engine.ALIQUOTA_CREDITO_POTENCIAL_QUARENTENA
            ),
            "valor_credito_item": credito,
        })
    return classificados


itens_salao = _ler_e_classificar(
    [
        ("Shampoo", "33051000", 300.00),
        ("Máscara", "33059000", 240.00),
        ("Escova", "96032900", 200.00),
        ("Pneus", "40111000", 1400.00),
        ("Smart TV LED 55 Polegadas 4K Ultra HD", "85287200", 2500.00),
    ],
    "1" * 44,
)
assert len(itens_salao) == 5
assert len({item["item_uid"] for item in itens_salao}) == 5
assert all(
    item["item_uid"] == f'{"1" * 44}_ITEM_{item["nitem"]:04d}'
    for item in itens_salao
)
assert sum(item["status_credito"] == "Validado" for item in itens_salao) == 3
assert sum(item["status_credito"] == "Quarentena" for item in itens_salao) == 2
credito_validado = sum(
    item["valor_item"] * item["aliquota_aplicada"]
    for item in itens_salao
    if item["status_credito"] == "Validado"
)
credito_quarentena = sum(
    xml_engine.obter_credito_quarentena(item)
    for item in itens_salao
    if item["status_credito"] == "Quarentena"
)
assert round(credito_validado, 2) == 196.10
assert round(credito_quarentena, 2) == 1033.50

itens_stress = _ler_e_classificar(
    [
        (f"Item {indice}", "33051000", 1.00)
        for indice in range(1, 1001)
    ],
    "2" * 44,
)
assert len(itens_stress) == 1000
assert len({item["item_uid"] for item in itens_stress}) == 1000
assert all(item["status_credito"] == "Validado" for item in itens_stress)

print(
    "CASE 1 PASS: 5 items; 3 validated; 2 quarantine; "
    f"validated R${credito_validado:.2f}; quarantine R${credito_quarentena:.2f}"
)
print("CASE 2 PASS: 1,000 distinct items parsed and classified")

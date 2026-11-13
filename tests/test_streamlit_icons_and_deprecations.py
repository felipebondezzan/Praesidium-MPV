"""Garante ícones válidos e ausência de APIs depreciadas do Streamlit 1.67."""
import ast
import unittest
from pathlib import Path

from streamlit.string_util import validate_icon_or_emoji

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVOS = (
    "app.py",
    "xml_engine.py",
    "relatorio_engine.py",
    "praesidium_persistence_fallback.py",
)
PARAMETROS_ICONE = {"icon", "page_icon"}


def _chamadas(arquivo: str):
    arvore = ast.parse((RAIZ / arquivo).read_text(encoding="utf-8"))
    return [no for no in ast.walk(arvore) if isinstance(no, ast.Call)]


class IconesEDeprecacoesStreamlitTests(unittest.TestCase):
    def test_todos_os_icones_literais_sao_emojis_validos(self):
        verificados = 0
        for arquivo in ARQUIVOS:
            for chamada in _chamadas(arquivo):
                for kw in chamada.keywords:
                    if kw.arg not in PARAMETROS_ICONE:
                        continue
                    local = f"{arquivo}:{chamada.lineno}"
                    self.assertIsInstance(
                        kw.value,
                        ast.Constant,
                        f"{local}: ícone dinâmico não pode ser validado estaticamente",
                    )
                    try:
                        validate_icon_or_emoji(kw.value.value)
                    except Exception as erro:
                        self.fail(f"{local}: ícone {kw.value.value!r} inválido: {erro}")
                    verificados += 1
        self.assertGreater(verificados, 0)

    def test_nao_usa_use_container_width(self):
        for arquivo in ARQUIVOS:
            for chamada in _chamadas(arquivo):
                argumentos = {kw.arg for kw in chamada.keywords}
                self.assertNotIn(
                    "use_container_width",
                    argumentos,
                    f"{arquivo}:{chamada.lineno}: use width='stretch'/'content'",
                )

    def test_html_embutido_prefere_st_iframe(self):
        codigo = (RAIZ / "app.py").read_text(encoding="utf-8")
        self.assertIn("st.iframe(", codigo)
        self.assertEqual(codigo.count("components.html("), 1, "apenas o fallback")


if __name__ == "__main__":
    unittest.main()

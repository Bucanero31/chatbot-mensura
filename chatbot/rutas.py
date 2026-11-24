"""Dónde está cada cosa. Un solo sitio, como `lexgeo_rutas.py` en la plataforma.

La carpeta del chatbot se puede copiar a otro PC y funciona: todo lo que necesita para
responder está dentro (`corpus/generado/`). Lo único que vive fuera es el Revisor X, del
que se TRAEN las normas con `herramientas/sincronizar_desde_revisor.py`; sin él el chatbot
sigue respondiendo con lo último que se trajo.
"""

from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

CORPUS = RAIZ / "corpus"
GENERADO = CORPUS / "generado"
PDF = CORPUS / "pdf"
ARTICULOS = GENERADO / "articulos.jsonl"
NORMAS = GENERADO / "normas.json"
FICHAS = GENERADO / "fichas.jsonl"
VOCABULARIO = CORPUS / "vocabulario.yaml"
VOCABULARIO_JSON = GENERADO / "vocabulario.json"
ALIAS = CORPUS / "alias.json"

WEB = RAIZ / "web"

# Lo que el chatbot ESCRIBE mientras funciona (caché de respuestas, valoraciones). No se
# versiona: son datos de uso, no programa.
DATOS = Path(os.environ.get("CHATBOT_DATOS", RAIZ / "datos"))

CONFIG = RAIZ / "config.json"


def revisor_x() -> Path:
    """La carpeta del Revisor X, de donde se traen normas y reglas."""
    if os.environ.get("LEXGEO_REVISOR_X"):
        return Path(os.environ["LEXGEO_REVISOR_X"])
    return RAIZ.parent / "Plataforma_Final_Lexgeo" / "Herramientas" / "Lexgeo Revisor X"


def catalogo() -> Path:
    return CORPUS / "catalogo.yaml"


def dirs_pdf() -> list[Path]:
    """Donde buscar los PDF: SOLO dentro de la carpeta del chatbot (pedido de Henry el
    24-nov-2026: que no dependa de ninguna otra carpeta). El Revisor X es una fuente de la
    que se importa con `sincronizar_desde_revisor.py`, nunca un sitio del que se lee."""
    return [PDF] if PDF.exists() else []

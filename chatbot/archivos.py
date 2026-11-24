"""Escribir archivos sin dejarlos a medias.

Un corte de luz o un disco lleno a mitad de `write_text` deja el archivo truncado, y si es
`articulos.jsonl` o `vectores.json` el chatbot ya no arranca (hallazgo 9 de la revisión del
24-nov-2026, comprobado con copias dañadas). Se escribe a un `.tmp` al lado y se cambia de
nombre con `os.replace`, que en el mismo disco es atómico: o queda el archivo viejo entero, o
el nuevo entero.
"""

from __future__ import annotations

import os
from pathlib import Path


def escribir(ruta: Path, datos: str | bytes) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".tmp")
    if isinstance(datos, str):
        datos = datos.encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(datos)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ruta)

"""Trae del Revisor X el catálogo, el vocabulario y los PDF de la normativa.

    py herramientas/sincronizar_desde_revisor.py          dice qué cambiaría y lo copia
    py herramientas/sincronizar_desde_revisor.py --seco   solo dice qué cambiaría

**El Revisor X es la fuente de verdad de la normativa** (su `CATALOGO.yaml` decide qué está
vigente, y se mantiene allí con su protocolo). El chatbot no tiene catálogo propio que
editar a mano: si lo tuviera, los dos acabarían diciendo cosas distintas de la misma norma.

Lo que el chatbot añade y el Revisor X no tiene (p. ej. la Ley 108-05) va en
`corpus/catalogo_extra.yaml` y se FUNDE aquí, sin tocar el del Revisor X.

Nunca copia `conocimiento/expedientes/`: son documentos de clientes.

Después de sincronizar: `py -m chatbot.corpus --informe`.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot import rutas  # noqa: E402


def main() -> int:
    partes = argparse.ArgumentParser(description=__doc__)
    partes.add_argument("--seco", action="store_true", help="no copiar nada")
    args = partes.parse_args()

    origen = rutas.revisor_x()
    normativa = origen / "conocimiento" / "normativa"
    if not normativa.exists():
        print(f"\n  No encuentro el Revisor X en {origen}.\n"
              "  Indica dónde está con la variable LEXGEO_REVISOR_X.\n")
        return 1

    cambios = []
    for sub in ("vigente", "derogada"):
        for pdf in sorted((normativa / sub).glob("*.pdf")):
            destino = rutas.PDF / sub / pdf.name
            if not destino.exists() or not filecmp.cmp(pdf, destino, shallow=False):
                cambios.append((pdf, destino))
    # Un PDF que cambió de carpeta en el Revisor X deja aquí una copia vieja en la otra.
    sobrantes = []
    for sub in ("vigente", "derogada"):
        for pdf in sorted((rutas.PDF / sub).glob("*.pdf")) if (rutas.PDF / sub).exists() else []:
            if not (normativa / sub / pdf.name).exists():
                sobrantes.append(pdf)

    vocab_origen = origen / "reglas" / "vocabulario_normativo.yaml"

    print(f"\n  Revisor X: {origen}")
    print(f"  PDF nuevos o cambiados: {len(cambios)}")
    for o, d in cambios:
        print(f"    + {d.relative_to(rutas.RAIZ)}")
    for s in sobrantes:
        print(f"    ? {s.relative_to(rutas.RAIZ)} ya no está en el Revisor X (no se borra)")
    if args.seco:
        return 0

    for o, d in cambios:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(o, d)
    if vocab_origen.exists():
        shutil.copy2(vocab_origen, rutas.VOCABULARIO)
    from chatbot.catalogo import fundir, sanear
    (rutas.CORPUS / "catalogo_revisor.yaml").write_text(
        sanear((normativa / "CATALOGO.yaml").read_text(encoding="utf-8")), encoding="utf-8")
    avisos = fundir()
    for a in avisos:
        print(f"  · {a}")
    print("\n  Hecho. Ahora: py -m chatbot.corpus --informe\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Comprueba que lo que se va a subir al repositorio PÚBLICO no lleva nada privado.

    python herramientas/revisar_antes_de_subir.py      (sale con 1 si encuentra algo)

Mira los archivos que git subiría (`git ls-files` + los nuevos no ignorados) y busca:
claves de API, números de expediente, correos, rutas de perfiles de Windows, y archivos que
nunca deben ir (config.json, la memoria de preguntas reales, modelos, PDF).

Un falso positivo se resuelve mirando el archivo, no relajando el patrón.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

PATRONES = {
    "clave de Google/Gemini": re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    "token de GitHub": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "clave tipo sk-": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "número de expediente": re.compile(r"\b66\d{11}\b"),
    "correo electrónico": re.compile(r"[\w.+-]+@[\w-]+\.(?:com|net|org|do|es)\b"),
    "ruta de un perfil de Windows": re.compile(r"C:\\\\?Users\\\\?[A-Za-z]", re.I),
    "cédula": re.compile(r"\b\d{3}-\d{7}-\d\b"),
}
# Correos que SÍ pueden aparecer (son públicos y forman parte de una norma o del sitio oficial).
CORREOS_PERMITIDOS = re.compile(r"@(?:suprema\.gov\.do|poderjudicial\.gob\.do|ri\.gob\.do|"
                                r"codetel\.net\.do|noreply\.anthropic\.com)")
PROHIBIDOS = re.compile(r"(^|/)(config\.json|datos/|runtime/|libs/|motor_local/(llama|modelos|"
                        r"descargas)/|corpus/pdf/)|\.(gguf|pdf|zip|db)$")
# El corpus son normas públicas: puede citar cédulas de funcionarios o correos del Poder
# Judicial en sus firmas. Se revisa igual, pero solo se avisa.
SOLO_AVISO = ("corpus/generado/articulos.jsonl",)


def archivos() -> list[str]:
    salida = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                            cwd=RAIZ, capture_output=True, text=True, check=True).stdout
    return [x for x in salida.splitlines() if x]


def main() -> int:
    errores, avisos = [], []
    for ruta in archivos():
        if PROHIBIDOS.search(ruta):
            errores.append(f"{ruta}: este archivo no debe subirse nunca")
            continue
        p = RAIZ / ruta
        if p.stat().st_size > 20 * 2**20:
            errores.append(f"{ruta}: más de 20 MB")
            continue
        try:
            texto = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for nombre, patron in PATRONES.items():
            for m in patron.finditer(texto):
                if nombre == "correo electrónico" and CORREOS_PERMITIDOS.search(m.group(0)):
                    continue
                linea = texto.count("\n", 0, m.start()) + 1
                destino = avisos if ruta in SOLO_AVISO else errores
                destino.append(f"{ruta}:{linea}: {nombre} «{m.group(0)[:40]}»")
    for a in avisos[:20]:
        print(f"  aviso  {a}")
    for e in errores:
        print(f"  ERROR  {e}")
    print(f"\n  {len(archivos())} archivos revisados · {len(errores)} errores · {len(avisos)} avisos")
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())

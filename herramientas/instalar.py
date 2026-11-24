"""Deja la carpeta lista para funcionar sola, sin nada instalado en Windows.

    INSTALAR.bat                                   (baja Python y llama a este script)
    runtime\\python\\python.exe herramientas\\instalar.py [--normativa] [--todo]

Lo que hace, y solo si falta:
1. **Librerías** (`libs/`): PyMuPDF y PyYAML, bajadas como *wheels* de PyPI y descomprimidas
   aquí. Sin pip: el Python «embeddable» no lo trae y no hace falta.
2. **Motor local** (`motor_local/llama/`): llama.cpp para Windows, versión Vulkan (aprovecha
   cualquier tarjeta gráfica y, si no hay, usa el procesador).
3. **Modelos** (`motor_local/modelos/`): el redactor y el de búsqueda por significado.
4. Con `--normativa`: los PDF oficiales del catálogo (solo hacen falta para RECONSTRUIR el
   corpus; el corpus ya procesado viene en el repositorio).
5. Los **vectores** de búsqueda por significado, si no están o están desactualizados.

Todo con la biblioteca estándar. Las versiones van FIJADAS: una versión nueva de llama.cpp o
de un modelo se prueba con `evaluacion/comparar_modelos.py` antes de cambiarla aquí.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "libs"))

LLAMA = ("https://github.com/ggml-org/llama.cpp/releases/download/b12676/"
         "llama-b12676-bin-win-vulkan-x64.zip")
MODELOS = {
    # El redactor elegido con evaluacion/comparar_modelos.py (ver evaluacion/RESULTADOS.md).
    "gemma-3-4b-it-Q4_K_M.gguf":
        "https://huggingface.co/ggml-org/gemma-3-4b-it-GGUF/resolve/main/gemma-3-4b-it-Q4_K_M.gguf",
    "embeddinggemma-300M-Q8_0.gguf":
        "https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF/resolve/main/"
        "embeddinggemma-300M-Q8_0.gguf",
}
MODELOS_OPCIONALES = {
    # Más rápido y ligero (0,7 GB), menos preciso: para PC sin tarjeta gráfica y con poca RAM.
    "LFM2-1.2B-RAG-Q4_K_M.gguf":
        "https://huggingface.co/LiquidAI/LFM2-1.2B-RAG-GGUF/resolve/main/LFM2-1.2B-RAG-Q4_K_M.gguf",
    "granite-4.0-h-micro-Q4_K_M.gguf":
        "https://huggingface.co/ibm-granite/granite-4.0-h-micro-GGUF/resolve/main/"
        "granite-4.0-h-micro-Q4_K_M.gguf",
}
WHEELS = [("pymupdf", "1.28.2"), ("pyyaml", "6.0.3")]
AGENTE = "ChatbotMensura-instalar/1.0"


def bajar(url: str, destino: Path | None = None) -> bytes | None:
    print(f"    ↓ {url.rsplit('/', 1)[-1]}", flush=True)
    peticion = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(peticion, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
        if destino is None:
            return r.read()
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = destino.with_suffix(destino.suffix + ".parcial")
        hecho = 0
        with parcial.open("wb") as f:
            while True:
                bloque = r.read(1 << 20)
                if not bloque:
                    break
                f.write(bloque)
                hecho += len(bloque)
                if total:
                    print(f"\r      {hecho >> 20} / {total >> 20} MB", end="", flush=True)
        print()
        parcial.replace(destino)
    return None


def librerias() -> None:
    libs = RAIZ / "libs"
    etiqueta = f"cp{sys.version_info.major}{sys.version_info.minor}"
    for paquete, version in WHEELS:
        if any(libs.glob(f"{paquete.replace('-', '_')}-{version}.dist-info")):
            continue
        datos = json.loads(bajar(f"https://pypi.org/pypi/{paquete}/{version}/json"))
        ruedas = [u for u in datos["urls"] if u["filename"].endswith("win_amd64.whl")
                  and (etiqueta in u["filename"] or "abi3" in u["filename"])]
        if not ruedas:
            sys.exit(f"  No hay wheel de {paquete} {version} para {etiqueta} win_amd64.")
        rueda = sorted(ruedas, key=lambda u: etiqueta not in u["filename"])[0]
        zipfile.ZipFile(io.BytesIO(bajar(rueda["url"]))).extractall(libs)
        print(f"    ✓ {paquete} {version}")


def motor() -> None:
    destino = RAIZ / "motor_local" / "llama"
    if (destino / "llama-server.exe").exists():
        return
    zip_ruta = RAIZ / "motor_local" / "descargas" / LLAMA.rsplit("/", 1)[-1]
    if not zip_ruta.exists():
        bajar(LLAMA, zip_ruta)
    zipfile.ZipFile(zip_ruta).extractall(destino)
    print("    ✓ llama.cpp")


def modelos(todos: bool) -> None:
    lista = {**MODELOS, **(MODELOS_OPCIONALES if todos else {})}
    for nombre, url in lista.items():
        destino = RAIZ / "motor_local" / "modelos" / nombre
        if not destino.exists():
            bajar(url, destino)


def normativa() -> None:
    from chatbot.catalogo import GRUPOS
    import yaml
    catalogo = yaml.safe_load((RAIZ / "corpus" / "catalogo.yaml").read_text(encoding="utf-8"))
    for grupo in GRUPOS:
        for n in catalogo.get(grupo) or []:
            url, archivo = str(n.get("url", "")), n.get("archivo")
            if not url.startswith("http") or not archivo:
                continue
            destino = RAIZ / "corpus" / "pdf" / archivo
            if not destino.exists():
                try:
                    bajar(url, destino)
                except Exception as e:  # noqa: BLE001
                    print(f"    ! {n['id']}: {e}")


def vectores() -> None:
    from chatbot.buscador import Buscador
    from chatbot.motor_local import motor as m
    from chatbot.vectores import Vectores
    v = Vectores(m(), Buscador())
    if v.listos:
        return
    print("    calculando vectores de búsqueda (una vez; unos minutos)…", flush=True)
    v.calcular(lambda h, t: print(f"\r      {h}/{t}", end="", flush=True))
    print()
    m().emb.parar()


def main() -> int:
    partes = argparse.ArgumentParser(description=__doc__)
    partes.add_argument("--normativa", action="store_true", help="bajar también los PDF oficiales")
    partes.add_argument("--todo", action="store_true", help="también los modelos opcionales")
    args = partes.parse_args()
    print("\n  Chatbot Mensura · instalación\n")
    print("  1/4 librerías");  librerias()  # noqa: E702
    print("  2/4 motor local");  motor()  # noqa: E702
    print("  3/4 modelos");  modelos(args.todo)  # noqa: E702
    if args.normativa:
        print("  · normativa oficial");  normativa()  # noqa: E702
    print("  4/4 vectores");  vectores()  # noqa: E702
    print("\n  Listo. Arranca con INICIAR.bat\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

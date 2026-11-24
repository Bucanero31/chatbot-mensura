"""Búsqueda por SIGNIFICADO: cada artículo y cada pregunta se convierten en un vector, y se
comparan por el ángulo entre ellos.

    py -m chatbot.vectores          calcula los vectores que falten (incremental)

**Por qué hace falta además del buscador por palabras.** Medido el 24-nov-2026 con la
batería: «¿se pueden depositar varias actuaciones técnicas en un mismo expediente?» no
encontraba «se permite el depósito combinado de distintas actuaciones técnicas en un único
expediente»; «¿lo rechazan en ventanilla?» no encontraba «Admisión de actuaciones». Las
palabras no coinciden; el significado sí. Es lo que Henry pidió: «preguntarle de cualquier
manera y que sepa a lo que me refiero».

Modelo: EmbeddingGemma 300M (Google, multilingüe, 318 MB), en el procesador vía llama.cpp.
Se guardan 256 dimensiones (el modelo admite recortarlas sin reentrenar) y en float32:
~2,6 MB para todo el corpus. Comparar una pregunta con 2.600 artículos en Python puro son
unos 20 ms.

Es **incremental**: cada vector guarda la huella del texto del que salió, y al actualizarse
la normativa solo se calculan los artículos nuevos o cambiados.

Y es **opcional**: sin el modelo o sin los vectores, el buscador funciona solo por palabras.
"""

from __future__ import annotations

import array
import hashlib
import json
import logging
import math
import sys
import threading
import time
from operator import mul

from chatbot import rutas

log = logging.getLogger(__name__)

DIMENSIONES = 256
LOTE = 16
CARACTERES = 1400
BIN = rutas.GENERADO / "vectores.bin"
META = rutas.GENERADO / "vectores.json"


def texto_de(u, normas: dict) -> str:
    """Formato de documento de EmbeddingGemma: «title: … | text: …»."""
    norma = normas.get(u.norma, {}).get("titulo", u.norma)
    titulo = " · ".join(x for x in (norma, u.articulo, u.apartado, u.titulo) if x)
    return f"title: {titulo} | text: {' '.join(u.texto.split())[:CARACTERES]}"


def texto_de_pregunta(pregunta: str) -> str:
    return f"task: search result | query: {pregunta}"


def huella(texto: str) -> str:
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:16]


def _normalizar(v: list[float]) -> list[float]:
    v = v[:DIMENSIONES]
    norma = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norma for x in v]


_CALCULANDO = threading.Lock()


def _leer_guardados() -> tuple[array.array | None, dict | None]:
    """(datos, meta) validados, o (None, None) si no hay. Lanza si están dañados."""
    if not (BIN.exists() and META.exists()):
        return None, None
    meta = json.loads(META.read_text(encoding="utf-8"))
    crudo = BIN.read_bytes()
    dims, huellas = int(meta["dimensiones"]), meta["huellas"]
    if len(crudo) != 4 * dims * len(huellas):
        raise ValueError(f"vectores.bin mide {len(crudo)} bytes y debería medir "
                         f"{4 * dims * len(huellas)}")
    datos = array.array("f")
    datos.frombytes(crudo)
    return datos, meta


class Vectores:
    def __init__(self, motor, buscador):
        self.motor = motor
        self.buscador = buscador
        self.matriz: list[array.array] = []
        self.modelo = ""
        self.cargar()

    @property
    def listos(self) -> bool:
        return bool(self.matriz) and len(self.matriz) == len(self.buscador.unidades)

    def cargar(self) -> None:
        """Nunca impide arrancar: unos vectores dañados se ignoran y se busca por palabras.

        Hallazgo 9 de la revisión (probado con copias dañadas): un `vectores.json` corrupto
        tumbaba el arranque, y un `.bin` truncado a múltiplo de 4 daba «listos» con media
        tabla vacía —similitud 0 en medio corpus, sin un solo error—."""
        try:
            datos, meta = _leer_guardados()
        except (OSError, ValueError, KeyError) as e:
            log.warning("vectores ilegibles (%s): búsqueda solo por palabras hasta recalcular", e)
            datos, meta = None, None
        if datos is None:
            self.matriz = []
            return
        dims = meta["dimensiones"]
        huellas = meta["huellas"]
        actuales = [huella(texto_de(u, self.buscador.normas)) for u in self.buscador.unidades]
        if huellas != actuales:
            log.info("vectores desactualizados (%d de %d coinciden): búsqueda solo por palabras "
                     "hasta recalcular", sum(a == b for a, b in zip(huellas, actuales)),
                     len(actuales))
            self.matriz = []
            return
        self.matriz = [datos[i * dims:(i + 1) * dims] for i in range(len(huellas))]
        self.modelo = meta.get("modelo", "")

    def calcular(self, progreso=None) -> int:
        """Calcula los que falten. Devuelve cuántos calculó. Uno a la vez: la precarga y el
        actualizador podían escribirlos a la vez y ganaba el del corpus viejo (hallazgo 9)."""
        with _CALCULANDO:
            return self._calcular(progreso)

    def _calcular(self, progreso=None) -> int:
        unidades = self.buscador.unidades
        textos = [texto_de(u, self.buscador.normas) for u in unidades]
        huellas = [huella(t) for t in textos]
        previos: dict[str, array.array] = {}
        try:
            datos, meta = _leer_guardados()
        except (OSError, ValueError, KeyError):
            datos, meta = None, None
        if datos is not None:
            d = meta["dimensiones"]
            for i, h in enumerate(meta["huellas"]):
                previos[h] = datos[i * d:(i + 1) * d]
        faltan = [i for i, h in enumerate(huellas) if h not in previos]
        hechos = 0
        for inicio in range(0, len(faltan), LOTE):
            lote = faltan[inicio:inicio + LOTE]
            vecs = self.motor.vectores([textos[i] for i in lote])
            for i, v in zip(lote, vecs):
                previos[huellas[i]] = array.array("f", _normalizar(v))
            hechos += len(lote)
            if progreso:
                progreso(hechos, len(faltan))
        salida = array.array("f")
        for h in huellas:
            salida.extend(previos[h])
        from chatbot.archivos import escribir
        # Primero el .bin y después el .json: si se corta entre los dos, la validación de
        # tamaño de `_leer_guardados` detecta que no casan y no se usan.
        escribir(BIN, salida.tobytes())
        escribir(META, json.dumps({
            "modelo": self.motor.modelo_emb.name if self.motor.modelo_emb else "",
            "dimensiones": DIMENSIONES, "huellas": huellas,
            "calculado": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=0))
        self.cargar()
        return hechos

    def parecidos(self, pregunta: str, k: int = 60) -> dict[int, float]:
        """id de unidad → similitud coseno (las k más parecidas)."""
        if not self.listos:
            return {}
        matriz = self.matriz          # foto fija: una recarga a la vez no la cambia a medias
        try:
            q = array.array("f", _normalizar(
                self.motor.vectores([texto_de_pregunta(pregunta)], rapido=True)[0]))
        except Exception as e:  # noqa: BLE001 — sin vectores, se busca solo por palabras
            log.warning("sin búsqueda por significado: %s", e)
            return {}
        sims = [(i, sum(map(mul, q, v))) for i, v in enumerate(matriz)]
        sims.sort(key=lambda x: -x[1])
        return dict(sims[:k])


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import os

    from chatbot.buscador import Buscador
    from chatbot.motor_local import MotorLocal, leer_config

    b = Buscador()
    # Cálculo masivo: todos los hilos del procesador (en el servidor, con el chat abierto, el
    # motor usa la mitad para no frenar al redactor).
    m = MotorLocal({**leer_config(), "hilos_embeddings": os.cpu_count() or 4})
    v = Vectores(m, b)
    t0 = time.time()

    def progreso(h, total):
        if h % (LOTE * 10) == 0 or h == total:
            print(f"  {h}/{total}  ({time.time() - t0:.0f} s)", flush=True)

    n = v.calcular(progreso)
    print(f"\n  {n} vectores nuevos; {len(v.matriz)} en total. {time.time() - t0:.0f} s")
    m.emb.parar()
    return 0


if __name__ == "__main__":
    sys.exit(main())

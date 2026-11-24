"""Lo que el chatbot aprende con el uso (pedido de Henry: «que aprenda mientras más preguntas
le hagan»).

## Qué aprende, y qué NO

Aprende **dónde buscar**, nunca **qué dice la norma**. Cada pregunta respondida deja anotado
qué artículos se citaron; cuando llega una pregunta parecida, esos artículos suben en la
búsqueda. El texto de la respuesta sigue saliendo siempre del artículo oficial, así que lo
aprendido no puede «contaminar» la normativa: como mucho, ordena mejor.

Es la misma frontera que el Revisor X (M9, ADR-0004): el sistema propone y mejora su
puntería, pero ninguna regla normativa cambia por el uso.

## Tres señales, de menos a más fuerza

| Señal | Peso | De dónde sale |
|---|---|---|
| Citado por el redactor y verificado sin avisos | +0,4 | automático |
| 👍 de la persona | +1,0 | `api/valorar` |
| 👎 de la persona | −1,0 | `api/valorar` (y la pregunta queda en `pendientes`) |

## Caché de respuestas

Una pregunta ya respondida (y no rechazada) se contesta al instante la vez siguiente, mientras
el corpus sea el mismo. Ahorra los segundos del modelo local en las preguntas repetidas, que
en una oficina son la mayoría.

Todo queda en `datos/memoria/` (JSON por líneas, legible y auditable). Borrar la carpeta
devuelve el chatbot a su estado de fábrica sin tocar la normativa.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

from chatbot import rutas
from chatbot.texto import sin_tildes, terminos

PARECIDO_MINIMO = 0.5        # Jaccard entre raíces para considerar dos preguntas «parecidas»
PESO_IMPLICITO = 0.4
PESO_POSITIVO = 1.0
PESO_NEGATIVO = -1.0


def normalizar(pregunta: str) -> str:
    return " ".join(re.findall(r"[a-zñ0-9]+", sin_tildes(pregunta.lower())))


class Memoria:
    def __init__(self, carpeta: Path | None = None):
        self.carpeta = carpeta or (rutas.DATOS / "memoria")
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.registro = self.carpeta / "registro.jsonl"
        self.valoraciones = self.carpeta / "valoraciones.jsonl"
        self._cerrojo = threading.Lock()
        self.entradas: dict[str, dict] = {}
        # Índices para no recorrer TODO el registro en cada pregunta: con 35 personas son
        # ~1.700 preguntas al día; sin índice, en un año cada búsqueda recorrería 600.000.
        self._por_termino: dict[str, set[str]] = defaultdict(set)
        self._por_clave: dict[str, list[str]] = defaultdict(list)
        self._cargar()

    # ----------------------------------------------------------------- disco
    def _cargar(self) -> None:
        if self.registro.exists():
            pos = 0
            with self.registro.open("rb") as f:
                for linea in f:
                    inicio, pos = pos, pos + len(linea)
                    try:
                        e = json.loads(linea)
                    except ValueError:
                        continue
                    self._indexar(e, inicio)
        if self.valoraciones.exists():
            for linea in self.valoraciones.read_text(encoding="utf-8").splitlines():
                try:
                    v = json.loads(linea)
                except ValueError:
                    continue
                if v["id"] in self.entradas:
                    self.entradas[v["id"]]["util"] = v["util"]
                    self.entradas[v["id"]]["comentario"] = v.get("comentario", "")

    # En RAM solo lo que sirve para aprender y para decidir la caché. La respuesta y las
    # fuentes (~6 KB por pregunta) se quedan en el disco y se leen al usar la caché. Medido en
    # la revisión del 24-nov-2026: con todo en RAM, 100.000 preguntas eran 1,4 GB.
    LIGEROS = ("id", "fecha", "pregunta", "clave", "terminos", "citas", "avisos", "modelo",
               "corpus", "util", "comentario", "implicito", "con_historial", "de_cache")

    def _indexar(self, e: dict, posicion: int = -1) -> None:
        e = {k: e[k] for k in self.LIGEROS if k in e}
        e["pregunta"] = e.get("pregunta", "")[:200]
        e["pos"] = posicion
        self.entradas[e["id"]] = e
        for t in e.get("terminos", []):
            self._por_termino[t].add(e["id"])
        self._por_clave[e.get("clave", "")].append(e["id"])

    def _anadir(self, ruta: Path, dato: dict) -> int:
        """Añade una línea y devuelve dónde empieza (para leerla luego sin cargarlo todo)."""
        linea = (json.dumps(dato, ensure_ascii=False) + "\n").encode("utf-8")
        with ruta.open("ab") as f:
            f.seek(0, 2)
            inicio = f.tell()
            f.write(linea)
        return inicio

    def _completa(self, e: dict) -> dict | None:
        """La entrada entera (con respuesta y fuentes), leída del disco por su posición."""
        if e.get("pos", -1) < 0:
            return None
        try:
            with self.registro.open("rb") as f:
                f.seek(e["pos"])
                return json.loads(f.readline())
        except (OSError, ValueError):
            return None

    # -------------------------------------------------------------- escribir
    def anotar(self, ident: str, pregunta: str, citas: list[tuple[str, str, str]],
               respuesta: str, avisos: list[str], modelo: str, version_corpus: str,
               implicito: bool = True, fuentes: list[dict] | None = None,
               con_historial: bool = False, de_cache: str = "") -> None:
        """Una respuesta terminada. `citas` son (norma, artículo, apartado) de las fuentes que
        la redacción citó. `implicito=False` cuando nadie eligió esas fuentes (la respuesta
        rápida sin modelo): entonces solo cuentan si la persona las valora."""
        e = {
            "id": ident, "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pregunta": pregunta, "clave": normalizar(pregunta),
            "terminos": sorted(set(terminos(pregunta))), "citas": [list(c) for c in citas],
            "respuesta": respuesta, "avisos": avisos, "modelo": modelo,
            "corpus": version_corpus, "util": None, "implicito": implicito,
            # Las fuentes tal como se numeraron: una respuesta en caché dice [2] y tiene que
            # seguir apuntando a la MISMA fuente 2, aunque hoy la búsqueda las ordene distinto.
            "fuentes": fuentes or [],
            # Redactada con la conversación anterior delante: no vale como respuesta general
            # y no va a la caché (hallazgo 7 de la revisión).
            "con_historial": con_historial, "de_cache": de_cache,
        }
        with self._cerrojo:
            self._indexar(e, self._anadir(self.registro, e))

    def valorar(self, ident: str, util: bool, comentario: str = "") -> bool:
        with self._cerrojo:
            if ident not in self.entradas:
                return False
            self.entradas[ident]["util"] = util
            self.entradas[ident]["comentario"] = comentario
            self._anadir(self.valoraciones, {"id": ident, "util": util, "comentario": comentario,
                                             "fecha": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return True

    # ----------------------------------------------------------------- leer
    def sugerencias(self, terms: list[str]) -> dict[tuple[str, str, str], float]:
        """Artículos que funcionaron (o no) en preguntas parecidas → peso entre -1 y 1."""
        consulta = set(terms)
        if not consulta:
            return {}
        acumulado: dict[tuple[str, str, str], float] = defaultdict(float)
        with self._cerrojo:
            # Solo las que comparten alguna palabra: las demás no pueden llegar al 0,5.
            ids = set().union(*(self._por_termino.get(t, set()) for t in consulta))
            entradas = [self.entradas[i] for i in ids]
        for e in entradas:
            otras = set(e.get("terminos", []))
            if not otras:
                continue
            parecido = len(consulta & otras) / len(consulta | otras)
            if parecido < PARECIDO_MINIMO:
                continue
            util = e.get("util")
            peso = PESO_POSITIVO if util is True else PESO_NEGATIVO if util is False \
                else (PESO_IMPLICITO if e.get("implicito", True) and not e.get("avisos")
                      else 0.0)
            for c in e.get("citas", []):
                acumulado[tuple(c)] += parecido * peso
        return {k: max(-1.0, min(1.0, v)) for k, v in acumulado.items() if v}

    def en_cache(self, pregunta: str, version_corpus: str) -> dict | None:
        """La misma pregunta, con el mismo corpus, respondida sin avisos y no rechazada."""
        clave = normalizar(pregunta)
        with self._cerrojo:
            candidatas = [e for e in (self.entradas[i] for i in self._por_clave.get(clave, []))
                          if e.get("corpus") == version_corpus
                          and e.get("util") is not False and not e.get("avisos")
                          and e.get("modelo") and not e.get("con_historial")
                          and not e.get("de_cache")]
        # Mejor una valorada 👍; si no, la más reciente.
        for e in sorted(candidatas, key=lambda e: (e.get("util") is True, e["fecha"]),
                        reverse=True):
            completa = self._completa(e)
            if completa and completa.get("respuesta"):
                return completa
        return None

    def pendientes(self) -> list[dict]:
        """Lo que salió mal: 👎 o respuestas con avisos. Para que una persona lo mire."""
        with self._cerrojo:
            return [e for e in self.entradas.values()
                    if e.get("util") is False or e.get("avisos")]

    def resumen(self) -> dict:
        with self._cerrojo:
            es = list(self.entradas.values())
        return {"preguntas": len(es), "utiles": sum(1 for e in es if e.get("util") is True),
                "no_utiles": sum(1 for e in es if e.get("util") is False)}


def main() -> int:
    """py -m chatbot.memoria — lo aprendido y lo que conviene revisar.

    Las preguntas con 👎 o con avisos del verificador son las que enseñan qué le falta al
    chatbot: un sinónimo en `corpus/vocabulario.yaml`, una norma que no está, un artículo que
    el troceo parte mal. Revisarlas de vez en cuando es la forma de que mejore más deprisa."""
    import sys
    m = Memoria()
    r = m.resumen()
    print(f"\n  {r['preguntas']} preguntas · {r['utiles']} útiles · {r['no_utiles']} no útiles\n")
    for e in sorted(m.pendientes(), key=lambda e: e["fecha"], reverse=True)[:40]:
        marca = "👎" if e.get("util") is False else "⚠"
        print(f"  {marca} {e['fecha'][:16]}  {e['pregunta'][:90]}")
        if e.get("comentario"):
            print(f"       comentario: {e['comentario'][:120]}")
        for a in e.get("avisos", [])[:2]:
            print(f"       aviso: {a[:120]}")
    return 0 if sys.stdout else 0


if __name__ == "__main__":
    raise SystemExit(main())

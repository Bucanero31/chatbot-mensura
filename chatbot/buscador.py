"""El buscador: de una pregunta escrita como sea, a los artículos que la responden.

Sin dependencias y en memoria: ~2.600 unidades se indexan al arrancar en un par de segundos
y cada búsqueda tarda milisegundos. No hace falta una base de datos para esto.

## Cómo entiende la pregunta

1. **Raíces, no palabras** (`texto.terminos`): «deslindes», «deslindar» y «deslinde» son lo
   mismo; «regularizacion» sin tilde también.
2. **Vocabulario del agrimensor → vocabulario del legislador** (`corpus/vocabulario.yaml`,
   heredado del Revisor X): «tolerancia» busca también «variación», «letrero» busca
   «aviso». Sin este puente la búsqueda falla en silencio (medido en el Revisor X).
3. **Nombres de normas como los dice la gente**: «la 790», «el reglamento de mensuras»,
   «la DT 8», «la ley 108». Si además nombra un artículo, ese artículo va primero.
4. **Lo aprendido** (`memoria.py`): preguntas parecidas que ya se respondieron bien empujan
   los artículos que se citaron entonces.

## Qué devuelve

Unidades (artículo, o apartado de un artículo-tabla), no trozos sueltos: se busca por trozos
pequeños para acertar y se entrega el artículo entero para entender («recuperar pequeño, leer
grande»). Cada una sabe su vigencia, y un artículo reformado viaja con el texto que lo
reforma.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from chatbot import rutas
from chatbot.texto import palabras, raiz, sin_tildes, terminos

TROZO_MAXIMO = 1600          # caracteres de un trozo de búsqueda
K1, B = 1.2, 0.75            # BM25
PESO_CABECERA = 2            # el título del artículo cuenta doble
PESO_SINONIMO = 0.45         # una palabra añadida por el vocabulario pesa menos que la escrita
PESO_SIGNIFICADO = 0.3       # medido con la batería: 0,3 da el mejor acierto@1 y @5 (0,7 empeora)

ESTADOS_NORMALES = ("vigente", "por_confirmar")
PALABRAS_HISTORICAS = {"derog", "anterior", "antigu", "viej", "histor", "antes", "derogacion"}


@dataclass
class Unidad:
    id: int
    norma: str
    articulo: str
    apartado: str
    titulo: str
    seccion: str
    pagina: int
    texto: str
    orden: int

    @property
    def referencia(self) -> str:
        ref = f"{self.norma}, {self.articulo}"
        if self.apartado:
            ref += f", num. {self.apartado}"
        return ref


@dataclass
class Resultado:
    unidad: Unidad
    puntaje: float
    estado: str
    motivo: str = ""                       # «nombrado en la pregunta», «aprendido», …
    reformado_por: list[Unidad] = field(default_factory=list)
    reforma_a: list[str] = field(default_factory=list)   # si ESTA unidad reforma otras


@dataclass
class Consulta:
    """Lo que el buscador entendió de la pregunta. Se devuelve para poder explicarlo."""
    texto: str
    terminos: list[str]
    expandidos: list[str]
    normas_nombradas: list[str]
    articulos_nombrados: list[str]
    historica: bool
    similitudes: dict = field(default_factory=dict)   # unidad → coseno, si hubo vectores
    borradores: bool = False                          # ¿pregunta por proyectos en consulta?


class Buscador:
    def __init__(self, generado: Path | None = None, memoria=None):
        self.generado = generado or rutas.GENERADO
        self.memoria = memoria
        self.vectores = None          # lo engancha quien tenga el motor (servidor, evaluar)
        self._cerrojo = threading.RLock()
        self.cargar()

    # ------------------------------------------------------------------ carga
    def cargar(self) -> None:
        """Lee y construye TODO fuera del cerrojo y lo cambia de golpe dentro. Si la lectura
        falla a medias, se queda el índice anterior entero (hallazgo 9 de la revisión: antes
        `self.normas` cambiaba primero y podía quedar con los artículos viejos)."""
        normas = json.loads((self.generado / "normas.json").read_text(encoding="utf-8"))
        fichas = {n["id"]: n for n in normas["normas"]}
        unidades = []
        with (self.generado / "articulos.jsonl").open(encoding="utf-8") as f:
            for i, linea in enumerate(f):
                d = json.loads(linea)
                unidades.append(Unidad(id=i, norma=d["norma"], articulo=d["articulo"],
                                       apartado=d.get("apartado", ""), titulo=d["titulo"],
                                       seccion=d["seccion"], pagina=d["pagina"],
                                       texto=d["texto"], orden=d["orden"]))
        vocab_ruta = self.generado / "vocabulario.json"
        vocab = json.loads(vocab_ruta.read_text(encoding="utf-8")) if vocab_ruta.exists() else {}
        indice = _construir_indice(unidades, fichas)
        sinonimos = {raiz(sin_tildes(k.lower())): [t for e in v for t in terminos(e)]
                     for k, v in vocab.items()}
        alias = _alias_de_normas(fichas)

        # Reformas: (norma, artículo) reformado → unidades que lo reforman.
        por_ref = defaultdict(list)
        for u in unidades:
            por_ref[(u.norma, u.articulo)].append(u)
        reformas = defaultdict(list)
        reforma_a = defaultdict(list)
        for r in normas.get("reformas", []):
            for reformador in por_ref.get((r["por_norma"], r.get("articulo_reformador", "")), []):
                reformas[(r["norma"], r["articulo"])].append(reformador)
                reforma_a[reformador.id].append(f"{r['norma']}, {r['articulo']}")
            if not r.get("articulo_reformador"):
                reformas.setdefault((r["norma"], r["articulo"]), [])

        with self._cerrojo:
            self.normas = fichas
            self.unidades = unidades
            self.por_ref = por_ref
            self.reformas = reformas
            self.reforma_a = reforma_a
            self.indice = indice
            self.sinonimos = sinonimos
            self.alias = alias

    # ---------------------------------------------------------------- entender
    def entender(self, pregunta: str) -> Consulta:
        base = terminos(pregunta)
        expandidos = []
        for t in base:
            for s in self.sinonimos.get(t, []):
                if s not in base and s not in expandidos:
                    expandidos.append(s)
        normas = self.normas_nombradas(pregunta)
        arts = articulos_nombrados(pregunta)
        historica = any(t.startswith(p) for t in base for p in PALABRAS_HISTORICAS) or any(
            self.normas[n]["estado"] == "derogada" for n in normas)
        borradores = any(t.startswith(p) for t in base
                         for p in ("proyect", "borrador", "propuest", "consult", "nuev", "futur"))
        return Consulta(pregunta, base, expandidos, normas, arts, historica,
                        borradores=borradores)

    def normas_nombradas(self, pregunta: str) -> list[str]:
        p = " " + " ".join(palabras(pregunta)) + " "
        encontradas = []
        for patron, ident in self.alias:
            if patron in p and ident not in encontradas:
                encontradas.append(ident)
        return encontradas

    # ----------------------------------------------------------------- buscar
    def buscar(self, pregunta: str, k: int = 6, historial: list[str] | None = None,
               consulta: Consulta | None = None) -> tuple[Consulta, list[Resultado]]:
        """Busca con el índice entero de UN momento: la recarga en caliente (actualizador)
        espera a que termine la búsqueda en curso, y la siguiente ya ve el índice nuevo.
        Una búsqueda son milisegundos; la recarga construye fuera del cerrojo y solo cambia
        las referencias dentro, así que nadie espera más que eso."""
        # La búsqueda por significado (vector de la pregunta + coseno: ~140 ms medidos) va
        # FUERA del cerrojo: con 35 personas preguntando a la vez, retenerlo durante la
        # llamada al modelo las ponía en fila sin necesidad. Dentro solo quedan los ~3 ms de
        # la búsqueda por palabras y la fusión.
        sims = None
        v = self.vectores
        if v is not None and v.listos:
            corta = len(terminos(pregunta)) <= 4
            texto_q = f"{historial[-1]} {pregunta}" if historial and corta else pregunta
            sims = v.parecidos(texto_q)
            n_vectores = len(v.matriz)
        # Lo aprendido también fuera: con mucho uso son decenas de ms (hallazgo 14).
        aprendido = self.memoria.sugerencias(terminos(pregunta)) if self.memoria else {}
        with self._cerrojo:
            # Si justo entre medias se recargó el corpus, los ids ya no casan: se descartan.
            if sims is not None and n_vectores != len(self.unidades):
                sims = None
            return self._buscar(pregunta, k, historial, consulta, sims, aprendido)

    def _buscar(self, pregunta: str, k: int = 6, historial: list[str] | None = None,
                consulta: Consulta | None = None, sims: dict | None = None,
                aprendido: dict | None = None) -> tuple[Consulta, list[Resultado]]:
        c = consulta or self.entender(pregunta)
        # Una pregunta de seguimiento corta («¿y en saneamiento?») hereda el tema anterior.
        contexto = []
        if historial and len(c.terminos) <= 4:
            contexto = terminos(historial[-1])
            if not c.normas_nombradas:
                c.normas_nombradas = self.normas_nombradas(historial[-1])

        pesos: Counter = Counter()
        for t in c.terminos:
            pesos[t] += 1.0
        for t in c.expandidos:
            pesos[t] += PESO_SINONIMO
        for t in contexto:
            if t not in pesos:
                pesos[t] += 0.35

        idx = self.indice
        puntos: Counter = Counter()
        for t, peso in pesos.items():
            idf = idx.idf.get(t)
            if not idf:
                continue
            for trozo, tf in idx.postings[t]:
                largo = idx.largos[trozo]
                puntos[trozo] += peso * idf * tf * (K1 + 1) / (
                    tf + K1 * (1 - B + B * largo / idx.largo_medio))

        # Del trozo a la unidad: la mejor puntuación de sus trozos manda.
        por_unidad: dict[int, float] = {}
        for trozo, p in puntos.items():
            u = idx.unidad_de[trozo]
            if p > por_unidad.get(u, 0):
                por_unidad[u] = p

        # Fusión con la búsqueda por significado, si hay vectores. Las dos escalas se llevan a
        # 0-1 y se suman con peso: así entra un artículo que dice lo mismo con otras palabras.
        c.similitudes = {}
        if sims:
            c.similitudes = sims
            if sims:
                max_b = max(por_unidad.values(), default=0) or 1.0
                s_max, s_min = max(sims.values()), min(sims.values())
                rango = (s_max - s_min) or 1.0
                fusion = {}
                for u in set(por_unidad) | set(sims):
                    sb = por_unidad.get(u, 0.0) / max_b
                    sv = (sims[u] - s_min) / rango if u in sims else 0.0
                    fusion[u] = 10 * ((1 - PESO_SIGNIFICADO) * sb + PESO_SIGNIFICADO * sv)
                por_unidad = fusion

        motivos: dict[int, str] = {}
        # Normas nombradas: empujón fuerte. Artículo nombrado: va primero.
        for n in c.normas_nombradas:
            for u in list(por_unidad):
                if self.unidades[u].norma == n:
                    por_unidad[u] *= 1.8
        maximo = max(por_unidad.values(), default=1.0)
        for n in c.normas_nombradas or []:
            for art in c.articulos_nombrados:
                for u in self.por_ref.get((n, art), []):
                    por_unidad[u.id] = maximo * 3 + por_unidad.get(u.id, 0)
                    motivos[u.id] = "nombrado en la pregunta"
        # «¿Sigue vigente la 3642-2016?»: lo que responde no está en la derogada, sino en el
        # artículo de la norma NUEVA que la deroga (que cita su número).
        for n in c.normas_nombradas:
            norma = self.normas.get(n, {})
            reemplazo = norma.get("reemplazada_por")
            if norma.get("estado") == "derogada" and reemplazo:
                numero = "-".join(n.split("-")[1:])          # «3642-2016»
                citan = [u for u in self.unidades if u.norma == reemplazo and numero in u.texto]
                # Si la nueva no la nombra (la 355-2009 la sustituyó otra antes), al menos sale
                # el objeto de la norma que hoy rige esa materia.
                for u in citan or [u for u in self.unidades if u.norma == reemplazo
                                   and u.articulo in ("Art. 1", "Art. 2")]:
                    por_unidad[u.id] = maximo * (2.5 if citan else 1.2) + por_unidad.get(u.id, 0)
                    motivos[u.id] = "deroga la norma nombrada" if citan else "norma que la reemplaza"
        if c.articulos_nombrados and not c.normas_nombradas:
            # «¿qué dice el artículo 13?» sin norma: se empuja ese número en las vigentes.
            for art in c.articulos_nombrados:
                for (n, a), us in self.por_ref.items():
                    if a == art and self.normas[n]["estado"] == "vigente":
                        for u in us:
                            if u.id in por_unidad:
                                por_unidad[u.id] *= 1.5

        # Lo aprendido de preguntas anteriores.
        if aprendido:
            for (n, a, ap), peso in aprendido.items():
                for u in self.por_ref.get((n, a), []):
                    if u.apartado == ap:
                        base = por_unidad.get(u.id, 0)
                        por_unidad[u.id] = base + maximo * 0.6 * peso
                        motivos.setdefault(u.id, "aprendido de preguntas anteriores")

        # Vigencia: las derogadas solo si la pregunta es histórica, y siempre detrás.
        resultados = []
        for u, p in por_unidad.items():
            unidad = self.unidades[u]
            estado = self.normas.get(unidad.norma, {}).get("estado", "por_confirmar")
            if estado == "derogada":
                if not c.historica:
                    continue
                p *= 0.6 if unidad.norma not in c.normas_nombradas else 1.0
            elif estado == "borrador":
                # Un proyecto en consulta pública NO es norma: solo si se pregunta por él.
                if not c.borradores and unidad.norma not in c.normas_nombradas:
                    continue
            elif estado == "por_confirmar":
                p *= 0.75
            if unidad.articulo == "Preámbulo":
                p *= 0.5        # los «considerando» casan con todo y responden poco
            resultados.append(Resultado(unidad, p, estado, motivos.get(u, "")))
        resultados.sort(key=lambda r: -r.puntaje)

        # Como mucho dos unidades del mismo artículo, para no llenar la respuesta de lo mismo.
        elegidos: list[Resultado] = []
        vistos: Counter = Counter()
        for r in resultados:
            clave = (r.unidad.norma, r.unidad.articulo)
            if vistos[clave] >= 2:
                continue
            vistos[clave] += 1
            elegidos.append(r)
            if len(elegidos) >= k:
                break

        for r in elegidos:
            r.reformado_por = list(self.reformas.get((r.unidad.norma, r.unidad.articulo), []))
            r.reforma_a = self.reforma_a.get(r.unidad.id, [])
        return c, elegidos

    def articulo(self, norma: str, articulo: str, apartado: str = "") -> Unidad | None:
        for u in self.por_ref.get((norma, articulo), []):
            if u.apartado == apartado or not apartado:
                return u
        return None


# ---------------------------------------------------------------------------
# Índice
# ---------------------------------------------------------------------------


@dataclass
class _Indice:
    postings: dict[str, list[tuple[int, int]]]
    idf: dict[str, float]
    largos: list[int]
    largo_medio: float
    unidad_de: list[int]


def _trozos(texto: str) -> list[str]:
    """Parte una unidad larga por párrafos, sin pasar de TROZO_MAXIMO."""
    if len(texto) <= TROZO_MAXIMO:
        return [texto]
    bloques = re.split(r"\n(?=\s*(?:P[áa]rrafo|[a-z]\)|\d+[.)]|[ivx]+\.)\s)|\n\n", texto)
    trozos, actual = [], ""
    for b in bloques:
        if len(actual) + len(b) > TROZO_MAXIMO and actual:
            trozos.append(actual)
            actual = ""
        while len(b) > TROZO_MAXIMO:
            corte = b.rfind(". ", 0, TROZO_MAXIMO)
            corte = corte + 1 if corte > TROZO_MAXIMO // 2 else TROZO_MAXIMO
            trozos.append(b[:corte])
            b = b[corte:]
        actual = f"{actual}\n{b}" if actual else b
    if actual.strip():
        trozos.append(actual)
    return trozos


def cabecera(u: Unidad, normas: dict) -> str:
    titulo_norma = normas.get(u.norma, {}).get("titulo", "")
    return " ".join(x for x in (titulo_norma, u.titulo, u.apartado, u.seccion) if x)


def _construir_indice(unidades: list[Unidad], normas: dict) -> _Indice:
    postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
    largos: list[int] = []
    unidad_de: list[int] = []
    for u in unidades:
        cab = terminos(cabecera(u, normas)) * PESO_CABECERA
        for trozo in _trozos(u.texto):
            ts = terminos(trozo) + cab
            if not ts:
                continue
            n = len(largos)
            for t, tf in Counter(ts).items():
                postings[t].append((n, tf))
            largos.append(len(ts))
            unidad_de.append(u.id)
    total = len(largos)
    idf = {t: math.log(1 + (total - len(p) + 0.5) / (len(p) + 0.5)) for t, p in postings.items()}
    return _Indice(dict(postings), idf, largos, sum(largos) / max(1, total), unidad_de)


# ---------------------------------------------------------------------------
# Nombres de normas y de artículos en la pregunta
# ---------------------------------------------------------------------------

_NOMBRES_COMUNES = {
    "LEY-108-05": ["ley de registro inmobiliario", "ley 108", "ley de registro"],
    "LEY-51-07": ["ley 51"],
    "RES-789-2022": ["reglamento general de mensuras", "reglamento de mensuras", "rgmc",
                     "reglamento de mensura"],
    "RES-790-2022": ["reglamento de regularizacion parcelaria", "reglamento de deslinde",
                     "reglamento para la regularizacion"],
    "RES-788-2022": ["reglamento general de registro de titulos", "reglamento de registro de titulos"],
    "RES-787-2022": ["reglamento de los tribunales", "reglamento de tribunales"],
    "RES-008-2020": ["reglamento del registro inmobiliario"],
    "RES-3645-2016": ["mensuras superpuestas", "reglamento de superposiciones"],
    "RES-3644-2016": ["red de estaciones permanentes", "reglamento de coordenadas"],
}


def _alias_de_normas(normas: dict) -> list[tuple[str, str]]:
    """(patrón en minúsculas sin tildes, rodeado de espacios, id). Los más largos primero."""
    alias: list[tuple[str, str]] = []

    def add(texto: str, ident: str) -> None:
        alias.append((" " + " ".join(palabras(texto)) + " ", ident))

    for ident, n in normas.items():
        if n.get("estado") == "borrador":
            continue           # un proyecto no se nombra por número: no lo tiene
        add(ident.replace("-", " "), ident)                    # «res 790 2022»
        partes = ident.split("-")
        m_dt = re.match(r"^(DNMC|DNRT)-DT-(\d{4})-(\d+)$", ident)
        if re.match(r"^(RES|LEY)-\d+-\d{2,4}$", ident):
            numero, anio = partes[1], partes[2]
            add(f"{numero} {anio}", ident)                     # «790 2022», «108 05»
            add(f"{numero} {anio[-2:]}", ident)                # «790 22»
            # «la 790», «resolucion 790», «res 790»: el número solo, si es inequívoco.
            if sum(1 for o in normas if o.split("-")[1:2] == [numero]) == 1:
                for pre in ("resolucion", "res", "reglamento", "la", "ley"):
                    add(f"{pre} {numero}", ident)
        elif m_dt:
            org, anio, num = m_dt.group(1), m_dt.group(2), m_dt.group(3)
            n_int = str(int(num))
            add(f"{org} dt {anio} {num}", ident)
            if n["estado"] == "vigente":
                for pre in ("dt", "disposicion tecnica", f"{org} dt", f"dt {org}"):
                    add(f"{pre} {n_int}", ident)
                    add(f"{pre} {num}", ident)
        for nombre in _NOMBRES_COMUNES.get(ident, []):
            add(nombre, ident)
    # Desempate: si dos normas comparten un patrón, gana la vigente.
    vistos: dict[str, str] = {}
    for patron, ident in alias:
        previo = vistos.get(patron)
        if previo is None or (normas[ident]["estado"] == "vigente"
                              and normas[previo]["estado"] != "vigente"):
            vistos[patron] = ident
    return sorted(vistos.items(), key=lambda x: -len(x[0]))


_RE_ART = re.compile(r"\bart(?:iculo|\.|)\s*(\d{1,3})(\s*bis)?\b")
_RE_PRINCIPIO = re.compile(r"\bprincipio\s+([ivx]+)\b")


def articulos_nombrados(pregunta: str) -> list[str]:
    p = sin_tildes(pregunta.lower())
    salida = [f"Art. {m.group(1)}" + (" bis" if m.group(2) else "") for m in _RE_ART.finditer(p)]
    salida += [f"Principio {m.group(1).upper()}" for m in _RE_PRINCIPIO.finditer(p)]
    return salida

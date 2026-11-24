"""Construye el corpus del chatbot: PDF oficiales → artículos limpios, con página y vigencia.

    py -m chatbot.corpus                 construye corpus/generado/
    py -m chatbot.corpus --informe       además imprime lo que encontró en cada norma

Solo hace falta para RECONSTRUIR el corpus (necesita pymupdf y pyyaml). El resultado
—`corpus/generado/normas.json` y `articulos.jsonl`— va en el repositorio, y el chatbot
arranca de él sin ninguna dependencia.

**Qué mejora sobre el índice M5 del Revisor X** (medido sobre su `indice_normativo.db`):

1. **Quita los encabezados y pies de página.** «1 Resolución núm. 82-2025 del Pleno de la
   Suprema Corte de Justicia» se repetía dentro del texto de cada hoja. Se detectan como las
   líneas que se repiten arriba o abajo en muchas páginas, y se borran.
2. **Guarda la página** de cada artículo, para enlazar el PDF oficial justo ahí (`#page=N`).
3. **Guarda el título del artículo** («Definición. Condiciones esenciales») y la sección en
   la que está, para que una cita se entienda sin abrir el PDF.
4. **Detecta qué artículos reforma una norma** leyendo su texto («Se modifica el artículo 5
   de la Resolución núm. 790-2022…») y lo cruza con `modificada_por` del catálogo.

Lo que se mantiene igual que en M5, porque allí costó aprenderlo:
- la vigencia sale de `CATALOGO.yaml`, nunca de la carpeta;
- un PDF sin texto (escaneo) se RECHAZA con ruido, no se indexa vacío;
- se trocea por artículo, y las líneas del índice del PDF («Art. 166 ...... 84») se descartan.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from chatbot import rutas
from chatbot.texto import limpiar_espacios

UMBRAL_TEXTO_POR_PAGINA = 40          # menos que esto por página = escaneo
MINIMO_CARACTERES = 50                # un «artículo» más corto es un encabezado suelto
SALTO_DE_PAGINA = "\x0c"

ORDINALES = {
    "primero": 1, "segundo": 2, "tercero": 3, "cuarto": 4, "quinto": 5, "sexto": 6,
    "septimo": 7, "séptimo": 7, "octavo": 8, "noveno": 9, "decimo": 10, "décimo": 10,
    "unico": 1, "único": 1,
}

# «Artículo 45.-», «Art. 45.», «ARTÍCULO 212 BIS:», «Artículo primero:». Solo a principio de
# línea: una remisión dentro de una frase («conforme al artículo 45») no abre artículo.
RE_ARTICULO = re.compile(
    r"(?:^|\n)[ \t\x0c]*(?:art[íi]culo|art\.)\s*"      # «Artículo\n125.» (RES-789) también
    r"(\d{1,3}(?:[ \t]*bis)?|primero|segundo|tercero|cuarto|quinto|sexto|s[ée]ptimo|octavo"
    r"|noveno|d[ée]cimo|[úu]nico)\b[ \t]*[.\-–:)ºo°]*",
    re.IGNORECASE,
)
RE_SECCION = re.compile(
    r"^[ \t\x0c]*(T[ÍI]TULO|CAP[ÍI]TULO|SECCI[ÓO]N|LIBRO)[ \t]+([IVXLC]+|\d+|[ÚU]NIC[OA])\b[ \t.:\-–]*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)
# Los «Principios» de la Ley 108-05 van antes del Art. 1 y son normativa como cualquier artículo.
RE_PRINCIPIO = re.compile(r"(?:^|\n)[ \t\x0c]*PRINCIPIO[ \t]+([IVX]+)\b[ \t.:\-–]*")

# Apartados de un artículo-tabla: «5. DESLINDE: …» (DNMC) o «12) HIPOTECA. …» (DNRT).
RE_APARTADO = re.compile(
    r"(?:^|\n)[ \t\x0c]*(\d{1,3})[.)][ \t]+([A-ZÁÉÍÓÚÑ][^\n:.]{2,120}?)[ \t]*(?:[:.]|\n|$)")
APARTADOS_DESDE = 6000      # un artículo más largo que esto se mira por apartados

RE_PUNTOS_GUIA = re.compile(r"\.{4,}|…{2,}")
RE_NUMERO_DE_PAGINA = re.compile(
    r"^\s*(?:p[áa]g(?:ina)?\.?\s*)?\d{1,3}(?:\s*(?:de|/)\s*\d{1,3})?\s*$", re.IGNORECASE)

# «Se modifica el artículo 5 de la Resolución núm. 790-2022» / «Se agrega un párrafo al
# artículo 6 la Resolución núm. 790-2022» / «Se deroga el párrafo II del artículo 54 de la
# Resolución núm. 787-2022».
RE_REFORMA = re.compile(
    r"se\s+(modifica|agrega|a[ñn]ade|deroga|sustituye)\b[^.]{0,80}?\bart[íi]culo\s+(\d+)"
    r"\s+(?:de\s+)?(?:la\s+)?(?:resoluci[óo]n|ley)\s+(?:n[úu]m\.?|no\.?|n\.?º)?\s*(\d{1,4}-\d{2,4})",
    re.IGNORECASE,
)


@dataclass
class Articulo:
    norma: str
    articulo: str               # «Art. 13», «Art. 212 bis», o «Preámbulo»
    orden: int                  # posición dentro de la norma, para mostrar vecinos
    titulo: str                 # título del artículo si lo tiene («Definición»)
    seccion: str                # «Título II · De la mensura › Capítulo III · …»
    pagina: int                 # 1-based, para enlazar el PDF
    texto: str
    apartado: str = ""          # «5. Deslinde» dentro de un artículo-tabla de requisitos


@dataclass
class InformeNorma:
    norma: str
    estado: str
    archivo: str = ""
    paginas: int = 0
    articulos: int = 0
    caracteres: int = 0
    lineas_repetidas_quitadas: int = 0
    reformas_detectadas: list[str] = field(default_factory=list)
    problema: str = ""


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------

GRUPOS = ("leyes", "resoluciones", "disposiciones_tecnicas_dnmc", "disposiciones_tecnicas_dnrt",
          "documentos_oficiales")


def leer_catalogo(ruta: Path) -> list[dict]:
    import yaml

    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    normas = []
    for grupo in GRUPOS:
        for n in datos.get(grupo) or []:
            n = dict(n)
            n["grupo"] = grupo
            normas.append(n)
    return normas


def _numeros(texto: str) -> str:
    return "-".join(re.findall(r"\d+", texto))


def buscar_pdf(norma: dict, dirs: list[Path]) -> Path | None:
    """El campo `archivo` del catálogo manda; si no hay, se busca por el identificador."""
    if norma.get("archivo"):
        for d in dirs:
            for candidato in (d / norma["archivo"], d / Path(norma["archivo"]).name):
                if candidato.exists():
                    return candidato
    ident = norma["id"]
    for d in dirs:
        for pdf in sorted(d.rglob("*.pdf")):
            if pdf.name.upper().startswith(ident.upper() + "_") or pdf.stem.upper() == ident.upper():
                return pdf
    return None


# ---------------------------------------------------------------------------
# PDF → texto limpio, con saltos de página marcados
# ---------------------------------------------------------------------------


def _firma(linea: str) -> str:
    """Una línea de encabezado reducida a su forma: las cifras cambian de hoja en hoja."""
    return re.sub(r"\d+", "#", " ".join(linea.lower().split()))


def _rango(paginas) -> tuple[int, int | None]:
    """«13-64» → (13, 64). Sin rango, el PDF entero."""
    if not paginas:
        return 1, None
    a, _, b = str(paginas).partition("-")
    return int(a), int(b or a)


# Líneas que jamás son encabezado de página aunque se repitan en el borde de muchas hojas.
RE_NUNCA_ES_ENCABEZADO = re.compile(
    r"^(art[íi]culo|art\.|p[áa]rrafo|principio|t[íi]tulo|cap[íi]tulo|secci[óo]n)\b", re.I)


def leer_pdf(ruta: Path, desde: int = 1, hasta: int | None = None) -> tuple[list[str], int]:
    """Texto de cada página, sin encabezados ni pies repetidos. Devuelve (páginas, quitadas)."""
    import pymupdf

    with pymupdf.open(ruta) as doc:
        fin = min(hasta or doc.page_count, doc.page_count)
        paginas = [doc[i].get_text() for i in range(desde - 1, fin)]

    lineas = [[limpiar_espacios(x).strip() for x in p.splitlines()] for p in paginas]
    lineas = [[x for x in p if x] for p in lineas]

    # Encabezados y pies: líneas de los bordes de la hoja que se repiten en muchas páginas.
    bordes = Counter()
    for p in lineas:
        for x in set(p[:4] + p[-4:]):
            bordes[_firma(x)] += 1
    minimo = max(3, int(len(lineas) * 0.3))
    repetidas = {f for f, c in bordes.items()
                 if c >= minimo and len(f) < 160 and not RE_NUNCA_ES_ENCABEZADO.match(f)}

    # Las MUY repetidas (en la mitad de las hojas o más) y cortas se quitan estén donde estén:
    # el membrete de la RES-790-2022 ocupa 5-6 líneas y «SUPREMA CORTE DE JUSTICIA» quedaba
    # fuera de la ventana de 4 líneas, dentro del texto del artículo (visto el 24-nov-2026).
    siempre = {f for f in repetidas if bordes[f] >= max(3, len(lineas) // 2) and len(f) < 90}

    quitadas = 0
    limpias = []
    for p in lineas:
        n = len(p)
        conservar = []
        for i, x in enumerate(p):
            en_borde = i < 6 or i >= n - 6
            firma = _firma(x)
            if firma in siempre or (en_borde and (firma in repetidas
                                                  or RE_NUMERO_DE_PAGINA.match(x))):
                quitadas += 1
                continue
            conservar.append(x)
        limpias.append("\n".join(conservar))
    return limpias, quitadas


def unir_paginas(paginas: list[str]) -> str:
    """Une las páginas marcando cada salto con \\x0c y repara lo que el maquetado partió."""
    texto = ("\n" + SALTO_DE_PAGINA).join(paginas)
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = re.sub(r"(\w)-\n(\x0c?)(\w)", r"\1\2\3", texto)               # pala-\nbra
    # Una línea que sigue en minúscula continúa la frase anterior (también tras salto de hoja).
    texto = re.sub(r"[ \t]*\n(\x0c?)[ \t]*(?=[a-záéíóúñü(,;])", r" \1", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    return texto


# ---------------------------------------------------------------------------
# Troceo por artículo
# ---------------------------------------------------------------------------


def _numero(etiqueta: str) -> tuple[int, str]:
    e = etiqueta.lower().replace("\t", " ")
    e = " ".join(e.split())
    if e in ORDINALES:
        return ORDINALES[e], ""
    m = re.match(r"(\d+)\s*(bis)?", e)
    if not m:
        return 0, ""
    return int(m.group(1)), (m.group(2) or "")


def _titulo_del_articulo(cuerpo: str) -> str:
    """«Artículo 5. Definición. Condiciones esenciales. La regularización…» → «Definición.
    Condiciones esenciales». Solo frases cortas del principio: un título no es una oración."""
    sin_cabecera = RE_ARTICULO.sub("", cuerpo, count=1).strip()
    sin_cabecera = RE_PRINCIPIO.sub("", sin_cabecera, count=1).strip()
    # «(Modificado por el artículo 3 de la Ley No. 51…)» va delante del título en la 108-05.
    sin_cabecera = re.sub(r"^\([^)]{0,160}\)\s*", "", sin_cabecera)
    # Todo el texto en una línea: la primera versión miraba solo la primera línea del PDF, y un
    # título partido en dos («Publicidad de los actos de / levantamiento parcelario», el
    # Art. 78 de la RES-789-2022, el que dice los 10 días) se perdía (24-nov-2026).
    plano = " ".join(sin_cabecera.split())[:400]
    trozos = _RE_FRASE.split(plano, maxsplit=3)
    if len(trozos) < 2:
        # En los PDF escaneados lo que sigue al título puede ir en minúscula («Suelo urbano.
        # encuentre en…»): el corte de frase no salta, pero el título está igual.
        if ". " not in plano:
            return ""
        trozos = [plano.split(". ")[0], plano.split(". ", 1)[1]]
    # Hasta el primer punto: en los PDF escaneados la frase siguiente puede seguir en
    # minúscula («Remediacion. remediacion a la que…») y colarse en el título.
    primero = trozos[0].split(". ")[0].strip().rstrip(".:").strip()
    if not _parece_titulo(primero, 110):
        return ""
    titulo = [primero]
    # Un segundo trozo muy corto también es título: «Definición. Condiciones esenciales».
    if len(trozos) > 2:
        segundo = trozos[1].strip().rstrip(".:").strip()
        if len(segundo.split()) <= 5 and _parece_titulo(segundo, 45):
            titulo.append(segundo)
    return ". ".join(titulo)


# Corte de frase: punto o dos puntos seguidos de mayúscula, salvo tras «núm.», «art.», «No.».
_RE_FRASE = re.compile(r"(?<!\bnúm)(?<!\bnum)(?<!\bart)(?<!\bNo)(?<!\bno)[.:]\s+(?=[A-ZÁÉÍÓÚÑ])")
_VERBO_DE_FRASE = re.compile(
    r"\b(es|son|se|debe|deben|será|serán|podrá|podrán|puede|pueden|tiene|tienen|tendrá|"
    r"tendrán|deroga|derogan|establece|dispone|aplica|modifica|comprende|corresponde|queda|"
    r"quedan|procede|entra|entrará|rige|consiste|ordena|crea|denomina|refiere|regula|"
    r"constituye|realiza|presenta|admite)\b", re.I)
# «El proceso judicial», «El registro» (Ley 108-05) SÍ son títulos: los artículos solos no
# delatan una frase; el verbo sí.
_EMPIEZA_COMO_FRASE = re.compile(
    r"^(en|se|para|cuando|todo|toda|todos|todas|si|con|a|por|al|esta|este|estos|estas|"
    r"dicha|dicho|cada|ningún|ninguna|sin|siempre|salvo|corresponden?|queda[n]?)\b", re.I)


def _parece_titulo(t: str, maximo: int) -> bool:
    """Un título es corto, empieza en mayúscula y no es una oración (sin verbo conjugado ni
    arranque de frase). «De las especificaciones técnicas» sí; «La presente resolución
    deroga…» no."""
    # El verbo de una relativa no delata frase: «Instrumento que crea el fideicomiso».
    sin_relativas = re.sub(r"\bque\s+\w+(\s+\w+)?", " ", t, flags=re.I)
    return (bool(t) and len(t) <= maximo and t[0].isupper()
            and not _VERBO_DE_FRASE.search(sin_relativas) and not _EMPIEZA_COMO_FRASE.match(t))


def _es_indice(cuerpo: str) -> bool:
    if RE_PUNTOS_GUIA.search(cuerpo[:400]):
        return True
    return len(cuerpo) < 200 and bool(re.search(r"\s\d{1,3}\s*$", cuerpo))


def _secciones(texto: str) -> list[tuple[int, str, str]]:
    """(posición, nivel, nombre) de cada TÍTULO / CAPÍTULO / SECCIÓN."""
    salida = []
    for m in RE_SECCION.finditer(texto):
        nivel = m.group(1).capitalize().replace("Titulo", "Título").replace(
            "Capitulo", "Capítulo").replace("Seccion", "Sección")
        nombre = m.group(3).strip(" .:-–")
        if not nombre:
            # El nombre suele ir en la línea siguiente, en mayúsculas.
            resto = texto[m.end():m.end() + 160].lstrip("\n\x0c ").split("\n", 1)[0]
            if resto and len(resto) < 120 and not RE_ARTICULO.match("\n" + resto):
                nombre = resto.strip()
        if nombre.isupper():
            nombre = nombre.capitalize()
        salida.append((m.start(), nivel, f"{nivel} {m.group(2).upper()}" + (
            f" · {nombre}" if nombre else "")))
    return salida


_JERARQUIA = ["Libro", "Título", "Capítulo", "Sección"]


def _ruta_de_seccion(secciones: list[tuple[int, str, str]], pos: int) -> str:
    actual: dict[str, str] = {}
    for inicio, nivel, nombre in secciones:
        if inicio > pos:
            break
        actual[nivel] = nombre
        # Un nivel superior nuevo reinicia los inferiores.
        for inferior in _JERARQUIA[_JERARQUIA.index(nivel) + 1:]:
            actual.pop(inferior, None)
    return " › ".join(actual[n] for n in _JERARQUIA if n in actual)


def trocear(norma: str, texto: str) -> list[Articulo]:
    """Parte el texto de una norma en artículos.

    Acepta un encabezado de artículo solo si su número sigue la secuencia (igual, +1… +4) o
    reinicia en 1 (el reglamento anexo a una resolución vuelve a empezar). Así una remisión
    que cae a principio de línea («Artículo 45 de la Ley 108-05…») no corta el artículo en
    curso.
    """
    candidatos = []
    for m in RE_ARTICULO.finditer(texto):
        num, bis = _numero(m.group(1))
        if num:
            candidatos.append((m.start() + (1 if texto[m.start()] == "\n" else 0), num, bis))

    aceptados = []
    previo = 0
    for pos, num, bis in candidatos:
        if not aceptados or num == 1 or previo <= num <= previo + 4:
            aceptados.append((pos, num, bis))
            previo = num
    # Los Principios (Ley 108-05) entran con número 0 y la etiqueta en `bis`.
    for m in RE_PRINCIPIO.finditer(texto):
        if not aceptados or m.start() < aceptados[0][0]:
            aceptados.append((m.start() + (1 if texto[m.start()] == "\n" else 0), 0,
                              f"Principio {m.group(1)}"))
    aceptados.sort()
    secciones = _secciones(texto)

    articulos: list[Articulo] = []

    def pagina_de(pos: int) -> int:
        return texto.count(SALTO_DE_PAGINA, 0, pos) + 1

    def limpio(t: str) -> str:
        t = t.replace(SALTO_DE_PAGINA, "")
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()

    primero = aceptados[0][0] if aceptados else len(texto)
    preambulo = limpio(texto[:primero])
    if len(preambulo) >= MINIMO_CARACTERES:
        articulos.append(Articulo(norma, "Preámbulo", 0, "", "", 1, preambulo))

    vistos: Counter = Counter()
    for i, (pos, num, bis) in enumerate(aceptados):
        fin = aceptados[i + 1][0] if i + 1 < len(aceptados) else len(texto)
        bruto = texto[pos:fin]
        # Un encabezado de sección pegado al final pertenece al artículo SIGUIENTE.
        corte = None
        for m in RE_SECCION.finditer(bruto):
            if m.start() > 0:
                corte = m.start()
                break
        if corte:
            bruto = bruto[:corte]
        cuerpo = limpio(bruto)
        if len(cuerpo) < MINIMO_CARACTERES or _es_indice(cuerpo):
            continue
        etiqueta = bis if num == 0 else f"Art. {num}" + (" bis" if bis else "")
        vistos[etiqueta] += 1
        if vistos[etiqueta] > 1:
            # Numeración repetida (resolución + reglamento anexo). Se distingue, no se pierde.
            etiqueta = f"{etiqueta} ({vistos[etiqueta]})"
        articulos.append(Articulo(
            norma=norma, articulo=etiqueta, orden=len(articulos),
            titulo=_titulo_del_articulo(cuerpo), seccion=_ruta_de_seccion(secciones, pos),
            pagina=pagina_de(pos + 1), texto=cuerpo,
        ))
    return articulos


def _renumerar_repetidos(articulos: list[Articulo]) -> list[Articulo]:
    """Si una etiqueta se repite, el reglamento anexo (el bloque MÁS LARGO) se queda con la
    etiqueta limpia «Art. N» y la parte resolutiva pasa a «Art. N (resolutivo)».

    Ejemplo: una resolución que dice «Artículo 1. Se aprueba el reglamento…» y luego el
    reglamento con su propio «Artículo 1. Objeto». Quien pregunta por «el artículo 1 del
    Reglamento de Mensuras» quiere el segundo.
    """
    base = lambda a: re.sub(r" \(\d+\)$", "", a.articulo)  # noqa: E731
    grupos: dict[str, list[Articulo]] = {}
    for a in articulos:
        grupos.setdefault(base(a), []).append(a)
    if not any(len(g) > 1 for g in grupos.values()):
        return articulos

    # Bloques contiguos de numeración: cada vez que la numeración vuelve a 1 empieza otro.
    bloque, bloques = 0, {}
    for a in articulos:
        if a.articulo != "Preámbulo" and _numero(base(a).replace("Art. ", ""))[0] == 1 \
                and bloques:
            bloque += 1
        bloques[id(a)] = bloque
    tamano = Counter()
    for a in articulos:
        tamano[bloques[id(a)]] += len(a.texto)
    principal = tamano.most_common(1)[0][0]

    for a in articulos:
        if a.articulo == "Preámbulo":
            continue
        if bloques[id(a)] == principal:
            a.articulo = base(a)
        elif len(grupos[base(a)]) > 1:
            a.articulo = f"{base(a)} (resolutivo)" if bloques[id(a)] < principal \
                else f"{base(a)} (bloque {bloques[id(a)] + 1})"
    return articulos


def partir_en_apartados(art: Articulo) -> list[Articulo]:
    """Un artículo-tabla («Los requisitos para cada actuación… 1. ACTUALIZACIÓN DE
    MENSURAS: … 5. DESLINDE: …») se parte en un trozo por actuación.

    **Medido el 24-nov-2026:** el Art. 23 de la DNMC-DT-2023-001 son 46.000 caracteres y el
    Art. 24 de la DNRT-DT-2025-003, 250.000: las tablas de requisitos de CADA trámite. Como
    un solo artículo, «¿qué documentos lleva un deslinde?» recuperaba un bloque donde el
    deslinde era el 3 % del texto. Partido, el apartado «Deslinde» es una respuesta entera.

    Solo se acepta una secuencia de apartados que avance (1, 2, 3…): una enumeración suelta
    en mayúsculas dentro del texto no corta nada.
    """
    if len(art.texto) < APARTADOS_DESDE:
        return [art]
    marcas = []
    esperado = 1
    for m in RE_APARTADO.finditer(art.texto):
        num = int(m.group(1))
        nombre = m.group(2).strip(" ,-–")
        letras = [c for c in nombre if c.isalpha()]
        # Un nombre de apartado va en MAYÚSCULAS («INSCRIPCIONES PROVISIONALES … o …»): se
        # admiten conectores en minúscula, no una frase normal.
        mayusculas = sum(c.isupper() for c in letras) / max(1, len(letras))
        if num in (esperado, esperado + 1) and len(letras) >= 4 and mayusculas >= 0.75:
            marcas.append((m.start(), num, nombre))
            esperado = num + 1
    if len(marcas) < 3:
        return [art]

    piezas = []
    intro = art.texto[: marcas[0][0]].strip()
    if intro:
        piezas.append(Articulo(**{**asdict(art), "texto": intro}))
    for i, (pos, num, nombre) in enumerate(marcas):
        fin = marcas[i + 1][0] if i + 1 < len(marcas) else len(art.texto)
        cuerpo = art.texto[pos:fin].strip()
        # La página avanza con el texto: se estima por la proporción recorrida.
        nombre_bonito = nombre[:1] + nombre[1:].lower()
        piezas.append(Articulo(**{**asdict(art), "texto": cuerpo,
                                  "apartado": f"{num}. {nombre_bonito}"}))
    return piezas


def detectar_reformas(articulos: list[Articulo]) -> list[dict]:
    """Qué artículos de OTRAS normas reforma esta, leído de su propio texto."""
    salida = []
    for a in articulos:
        for m in RE_REFORMA.finditer(a.texto[:600]):
            accion, art, norma = m.group(1).lower(), m.group(2), m.group(3)
            salida.append({
                "articulo_reformador": a.articulo,
                "accion": {"añade": "agrega", "anade": "agrega"}.get(accion, accion),
                "norma": norma, "articulo": f"Art. {art}",
            })
    return salida


# ---------------------------------------------------------------------------
# Construcción
# ---------------------------------------------------------------------------


def _id_catalogo(numero: str, normas: list[dict]) -> str | None:
    """«790-2022» → «RES-790-2022»."""
    for n in normas:
        if n["id"].endswith(numero) and _numeros(n["id"]) == _numeros(numero):
            return n["id"]
    return None


def construir(dirs_pdf: list[Path], catalogo: Path, destino: Path,
              informe: bool = False) -> list[InformeNorma]:
    normas = leer_catalogo(catalogo)
    destino.mkdir(parents=True, exist_ok=True)
    todos: list[Articulo] = []
    informes: list[InformeNorma] = []
    reformas: list[dict] = []
    fichas_normas = []

    for n in normas:
        inf = InformeNorma(norma=n["id"], estado=n.get("estado", "por_confirmar"))
        pdf = buscar_pdf(n, dirs_pdf)
        ficha = {
            "id": n["id"], "titulo": " ".join(str(n.get("titulo", "")).split()),
            "estado": inf.estado, "anio": n.get("anio"), "grupo": n["grupo"],
            "relevancia": n.get("relevancia", ""),
            "url": n.get("url") if str(n.get("url", "")).startswith("http") else "",
            "reemplazada_por": n.get("reemplazada_por", ""),
            "modificada_por": n.get("modificada_por") or [],
            "nota": " ".join(str(n.get("nota", "")).split()),
            "indexada": False, "articulos": 0,
        }
        if not pdf:
            inf.problema = "sin PDF"
            informes.append(inf)
            fichas_normas.append(ficha)
            continue
        inf.archivo = pdf.name
        desde, hasta = _rango(n.get("paginas"))
        paginas, inf.lineas_repetidas_quitadas = leer_pdf(pdf, desde, hasta)
        inf.paginas = len(paginas)
        bruto = "".join(paginas)
        if inf.paginas and len(bruto.strip()) < UMBRAL_TEXTO_POR_PAGINA * inf.paginas:
            inf.problema = "escaneo sin texto: NO se indexa"
            informes.append(inf)
            fichas_normas.append(ficha)
            continue
        texto = unir_paginas(paginas)
        arts = _renumerar_repetidos(trocear(n["id"], texto))
        arts = [p for a in arts for p in partir_en_apartados(a)]
        busqueda = 0
        for a in arts:
            if a.apartado:
                # Un apartado de una tabla de 100 páginas no está en la página del artículo.
                pos = texto.find(a.texto[:60], busqueda)
                if pos >= 0:
                    a.pagina = texto.count(SALTO_DE_PAGINA, 0, pos) + 1
                    busqueda = pos
        for i, a in enumerate(arts):
            a.orden = i
            a.pagina += desde - 1          # página del PDF entero, para el enlace
        for r in detectar_reformas(arts):
            destino_id = _id_catalogo(r["norma"], normas)
            # Una norma publicada ya CONSOLIDADA (la 108-05 con la 51-07 dentro) no tiene
            # artículos «con texto anterior»: su texto ya es el reformado.
            consolidada = any(x["id"] == destino_id and x.get("texto_consolidado")
                              for x in normas)
            if destino_id and destino_id != n["id"] and not consolidada:
                r.update(norma=destino_id, por_norma=n["id"])
                reformas.append(r)
                inf.reformas_detectadas.append(f"{r['accion']} {destino_id} {r['articulo']}")
        inf.articulos = len({a.articulo for a in arts if a.articulo != "Preámbulo"})
        inf.caracteres = sum(len(a.texto) for a in arts)
        ficha.update(indexada=True, articulos=inf.articulos, paginas=inf.paginas,
                     archivo=pdf.name)
        todos += arts
        informes.append(inf)
        fichas_normas.append(ficha)

    # Las reformas del catálogo (`modificada_por`) mandan; las leídas del texto las completan.
    del_catalogo = {(n["id"], f"Art. {a}", m["norma"])
                    for n in normas for m in (n.get("modificada_por") or [])
                    for a in m.get("articulos", []) if str(a).isdigit()}
    leidas = {(r["norma"], r["articulo"], r["por_norma"]) for r in reformas}
    for norma, art, por in sorted(del_catalogo - leidas):
        reformas.append({"norma": norma, "articulo": art, "por_norma": por,
                         "accion": "modifica", "articulo_reformador": ""})

    (destino / "normas.json").write_text(json.dumps(
        {"normas": fichas_normas, "reformas": reformas}, ensure_ascii=False, indent=1,
        default=str),
        encoding="utf-8")
    with (destino / "articulos.jsonl").open("w", encoding="utf-8") as f:
        for a in todos:
            f.write(json.dumps(asdict(a), ensure_ascii=False) + "\n")

    if informe:
        for inf in informes:
            print(f"  {inf.norma:<20} {inf.estado:<14} pág {inf.paginas:>3}  "
                  f"arts {inf.articulos:>4}  chars {inf.caracteres:>7}  "
                  f"quitadas {inf.lineas_repetidas_quitadas:>4}  {inf.problema}")
            for r in inf.reformas_detectadas:
                print(f"      reforma → {r}")
    return informes


def exportar_lista_de_normas(normas_json: Path, destino: Path) -> None:
    """docs/NORMAS.md: qué sabe el chatbot y con qué vigencia, legible por una persona."""
    datos = json.loads(normas_json.read_text(encoding="utf-8"))
    orden = {"vigente": 0, "por_confirmar": 1, "borrador": 2, "derogada": 3}
    filas = sorted(datos["normas"], key=lambda n: (orden.get(n["estado"], 9), n["grupo"], n["id"]))
    lineas = ["# Normas que conoce el chatbot", "",
              "GENERADO por `chatbot/corpus.py` en cada reconstrucción. La vigencia sale del "
              "catálogo (`corpus/catalogo.yaml`), nunca de la carpeta.", "",
              "| Norma | Título | Estado | Artículos |", "|---|---|---|---|"]
    for n in filas:
        titulo = n["titulo"].replace("|", "/")[:110]
        arts = n.get("articulos", 0) if n.get("indexada") else "— (sin texto)"
        estado = n["estado"] + (f" → {n['reemplazada_por']}" if n.get("reemplazada_por") else "")
        lineas.append(f"| {n['id']} | {titulo} | {estado} | {arts} |")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def exportar_vocabulario(origen: Path, destino: Path) -> None:
    """El vocabulario del Revisor X + el propio del chatbot (`vocabulario_extra.yaml`) → JSON,
    para que el chatbot no necesite pyyaml. Si un término está en los dos, se SUMAN."""
    import yaml
    from chatbot.archivos import escribir

    sinonimos: dict[str, list[str]] = {}
    for ruta in (origen, origen.with_name("vocabulario_extra.yaml")):
        if not ruta.exists():
            continue
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
        for k, v in (datos.get("sinonimos") or {}).items():
            if isinstance(v, dict):
                lista = sinonimos.setdefault(str(k), [])
                lista += [e for e in v.get("equivalentes", []) if e not in lista]
    escribir(destino, json.dumps(sinonimos, ensure_ascii=False, indent=1))


def main() -> int:
    partes = argparse.ArgumentParser(description=__doc__)
    partes.add_argument("--informe", action="store_true")
    args = partes.parse_args()
    informes = construir(rutas.dirs_pdf(), rutas.catalogo(), rutas.GENERADO, args.informe)
    exportar_vocabulario(rutas.VOCABULARIO, rutas.VOCABULARIO_JSON)
    exportar_lista_de_normas(rutas.NORMAS, rutas.RAIZ / "docs" / "NORMAS.md")
    indexadas = [i for i in informes if not i.problema]
    print(f"\n  {len(indexadas)} normas indexadas, "
          f"{sum(i.articulos for i in indexadas)} artículos → {rutas.GENERADO}")
    for i in informes:
        if i.problema:
            print(f"  ⚠ {i.norma}: {i.problema}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Mantiene la normativa al día SIN intervención (pedido de Henry, 24-nov-2026).

    py -m chatbot.actualizador            revisa ahora, descarga y reconstruye lo que cambie
    py -m chatbot.actualizador --seco     solo dice qué encontraría

Dentro del servidor corre solo, cada `cada_dias` (7 por omisión) a la `hora` indicada.

## Qué hace en cada vuelta

1. Lee los índices oficiales de ri.gob.do (resoluciones, disposiciones técnicas de Mensuras
   y de Títulos, leyes, consulta pública) y saca los enlaces a PDF.
2. **PDF ya conocido**: si su contenido cambió (huella SHA-256 distinta), lo vuelve a bajar.
   Pasa: ri.gob.do republica normas con el mismo nombre de archivo.
3. **PDF nuevo**: lo baja y decide si es de la materia (mensura, registro, jurisdicción
   inmobiliaria) leyendo sus primeras páginas. Si no lo es, lo anota y no lo vuelve a mirar.
4. Clasifica lo nuevo:
   - de **consulta pública**, o con «xxxx»/«borrador» en el nombre → `borrador`: se guarda,
     pero no responde preguntas salvo que se pregunte por proyectos;
   - con identificador MÁS RECIENTE que lo que ya hay de su tipo → `vigente`, y lee su
     artículo de **derogaciones**: lo que diga que deroga pasa a `derogada` con la nueva
     como reemplazo (la regla del Revisor X: la derogación expresa manda sobre todo);
   - anterior a lo que ya hay y que nadie catalogó → se anota y no se indexa: lo más
     probable es que esté superada, y citar normativa muerta es el peor error posible.
5. Si el Revisor X está en el mismo PC, trae también su catálogo (allí una persona decide
   las vigencias dudosas, y eso manda sobre lo deducido aquí).
6. Reconstruye el corpus, calcula los vectores de lo nuevo y **recarga el buscador sin parar
   el servidor**. Cada cambio queda en `datos/novedades.jsonl`, que la pantalla enseña.

Si algo falla (sin internet, el sitio caído), no toca nada y lo intenta en la vuelta
siguiente: el chatbot sigue respondiendo con lo que tenía.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

from chatbot import rutas
from chatbot.catalogo import GRUPOS

log = logging.getLogger(__name__)

INDICES = [
    ("resoluciones", "https://ri.gob.do/?page_id=699"),
    ("disposiciones_tecnicas_dnmc", "https://ri.gob.do/?page_id=679"),
    ("disposiciones_tecnicas_dnrt", "https://ri.gob.do/?page_id=8013"),
    ("leyes", "https://ri.gob.do/?page_id=606"),
    ("consulta_publica", "https://ri.gob.do/?cat=79"),
]
AGENTE = "ChatbotMensura/1.0 (actualizador de normativa)"
MAXIMO_PDF = 60 * 2**20
CONOCIDAS = rutas.CORPUS / "fuentes_conocidas.json"
CATALOGO_AUTO = rutas.CORPUS / "catalogo_auto.json"
ESTADO = rutas.DATOS / "actualizador.json"
NOVEDADES = rutas.DATOS / "novedades.jsonl"

PALABRAS_DE_LA_MATERIA = (
    "mensura", "catastral", "inmueble", "registro de titulos", "jurisdiccion inmobiliaria",
    "deslinde", "parcela", "agrimensor", "constancia anotada", "certificado de titulo",
    "registro inmobiliario", "saneamiento", "condominio",
)

RE_ENLACE = re.compile(r'<a[^>]+href="(https?://ri\.gob\.do/wp-content/uploads/[^"]+?\.pdf)"[^>]*>(.*?)</a>',
                       re.I | re.S)
RE_ID_DT = re.compile(r"(DNMC|DNRT)[-_ ]?DT[-_ ]?(\d{4})[-_ ]?(\d{2,5}|x+)", re.I)
RE_ID_RES = re.compile(r"resoluci[óo]n[_\s-]*(?:n[úu]m\.?|no\.?)?[_\s-]*(\d{1,4})[-_](\d{4})", re.I)
RE_ID_LEY = re.compile(r"ley[_\s-]*(?:n[úu]m\.?|no\.?)?[_\s-]*(\d{1,4})[-_](\d{2,4})", re.I)
RE_DEROGA = re.compile(
    r"deroga[n]?\s+(?:la|el|las|los)?\s*(?:resoluci[óo]n|disposici[óo]n\s+t[ée]cnica)?\s*"
    r"(?:n[úu]m\.?|no\.?)?\s*((?:DNMC|DNRT)-DT-\d{4}-\d{2,5}|\d{1,4}-\d{4})", re.I)


# ---------------------------------------------------------------------------
# Red
# ---------------------------------------------------------------------------


def _url(url: str) -> str:
    """ri.gob.do publica archivos con tildes en el nombre («Recepción»): urllib no las codifica
    solo y falla con 'ascii' codec. Las que ya vienen como %20 se respetan."""
    import urllib.parse
    return urllib.parse.quote(url, safe=":/?&=%#~+@")


def _bajar(url: str, maximo: int = MAXIMO_PDF) -> bytes:
    peticion = urllib.request.Request(_url(url), headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(peticion, timeout=90) as r:
        datos = r.read(maximo + 1)
    if len(datos) > maximo:
        raise ValueError(f"demasiado grande: {url}")
    return datos


def cabeceras(url: str) -> list[str] | None:
    """Tamaño, ETag y fecha del archivo SIN bajarlo. Si no cambian, el PDF no cambió: así la
    vuelta semanal no descarga cada semana los ~100 MB de normativa."""
    peticion = urllib.request.Request(_url(url), method="HEAD", headers={"User-Agent": AGENTE})
    try:
        with urllib.request.urlopen(peticion, timeout=30) as r:
            return [r.headers.get("Content-Length", ""), r.headers.get("ETag", ""),
                    r.headers.get("Last-Modified", "")]
    except Exception:  # noqa: BLE001
        return None


class SinConexion(RuntimeError):
    """No se pudo leer NINGÚN índice: no es «no hay novedades», es «no sé»."""


def enlaces_oficiales() -> list[dict]:
    salida, vistos, leidos = [], set(), 0
    for grupo, url in INDICES:
        try:
            pagina = _bajar(url, 5 * 2**20).decode("utf-8", "replace")
            leidos += 1
        except Exception as e:  # noqa: BLE001
            log.warning("no pude leer %s: %s", url, e)
            continue
        for m in RE_ENLACE.finditer(pagina):
            enlace = html.unescape(m.group(1))
            if enlace in vistos:
                continue
            vistos.add(enlace)
            texto = re.sub(r"<[^>]+>", " ", m.group(2))
            salida.append({"url": enlace, "grupo": grupo,
                           "texto": " ".join(html.unescape(texto).split())[:200]})
    if not leidos:
        # Hallazgo 8 de la revisión: sin internet devolvía [] y la vuelta se anotaba como
        # hecha («sin novedades»), aplazando el siguiente intento una semana entera.
        raise SinConexion("no se pudo leer ningún índice de ri.gob.do")
    return salida


# ---------------------------------------------------------------------------
# Identificar una norma nueva
# ---------------------------------------------------------------------------


RE_ID_FORMULARIO = re.compile(r"\b(FO|PL)-(DNMC|DNRT)-(\d{3})", re.I)
RE_ID_GUIA = re.compile(r"Guia[_\s-]*(?:de[_\s-]*)?Requisitos[_\s-]*(DNMC|DNRT)", re.I)


def identificar(nombre: str, texto: str) -> tuple[str, int] | None:
    """(id con el formato del catálogo, año) a partir del nombre del archivo y su texto."""
    m = RE_ID_FORMULARIO.search(nombre)
    if m:   # formularios y políticas oficiales: FO-DNMC-003 (acta de hitos), PL-DNMC-001…
        return f"{m.group(1).upper()}-{m.group(2).upper()}-{m.group(3)}", 0
    m = RE_ID_GUIA.search(nombre)
    if m:
        return f"GUIA-REQUISITOS-{m.group(1).upper()}", 0
    m = re.match(r"Ley[_\s-]*(\d{1,4})\.pdf$", nombre, re.I)
    if m:   # «Ley_675.pdf»: sin año en el nombre; el año sale del encabezado del texto
        anio = re.search(rf"Ley\s*(?:No\.?|n[úu]m\.?)?\s*{m.group(1)}\b[^\n]{{0,160}}?\b(1[89]\d\d|20\d\d)\b",
                         texto[:2000], re.I)
        if anio:
            return f"LEY-{m.group(1)}-{anio.group(1)}", int(anio.group(1))
    m = re.search(r"Disposici[oó]n[_\s-]*(\d{3})[-_](\d{4})", nombre, re.I)
    if m:   # la serie vieja de ri.gob.do: Disposicion_004-2008.pdf → DT-004-2008 (como el catálogo)
        return f"DT-{m.group(1)}-{m.group(2)}", int(m.group(2))
    for fuente in (nombre, texto[:3000]):
        m = RE_ID_DT.search(fuente)
        if m:
            num = m.group(3)
            num = num.zfill(3) if num.isdigit() else "xxxx"
            return f"{m.group(1).upper()}-DT-{m.group(2)}-{num}", int(m.group(2))
        m = RE_ID_RES.search(fuente)
        if m:
            return f"RES-{int(m.group(1))}-{m.group(2)}", int(m.group(2))
        m = RE_ID_LEY.search(fuente)
        if m:
            anio = m.group(2)
            anio_completo = int(anio) if len(anio) == 4 else 2000 + int(anio) if int(anio) < 50 \
                else 1900 + int(anio)
            return f"LEY-{int(m.group(1))}-{anio}", anio_completo
    return None


RE_BORRADOR = re.compile(r"x{3,}|borrador|proyecto|propuesta|consulta[\s_-]*p[úu]blica", re.I)


_RE_DT_EN_TEXTO = re.compile(r"(DNMC|DNRT)[\s-]*DT[\s-]*(\d{4})[\s-]*0*(\d{1,4})", re.I)
_RE_RES_EN_TEXTO = re.compile(
    r"resoluci[óo]n\s*(?:n[úu]m\.?|no\.?|n\.?º)?\s*(\d{1,4})-(\d{4})", re.I)
# La serie vieja, sin organismo: «Disposición Técnica núm. 002-2010» (DNMC-DT-2023-001, Art. 32),
# «Disposición técnica 01-2013» (DNMC-DT-2023-006, Art. 19).
_RE_DT_VIEJA_EN_TEXTO = re.compile(
    r"disposici[óo]n\s+t[ée]cnica\s*(?:n[úu]m\.?|no\.?)?\s*0*(\d{1,3})-(\d{4})", re.I)


def clave(norma: str) -> str:
    """Forma canónica para comparar identificadores escritos de maneras distintas.

    Medido el 24-nov-2026: la DNRT-DT-2025-003 escribe «DNRT-DT-2023001» (sin guion) y la
    DNRT-DT-2021-0001 lleva cuatro cifras. Las tres formas tienen que ser la misma norma."""
    m = re.match(r"(DNMC|DNRT)-DT-(\d{4})-0*(\d+)$", norma)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{int(m.group(3))}"
    m = re.match(r"(RES|LEY|DT)-0*(\d+)-(\d{2,4})$", norma)
    if m:
        return f"{m.group(1)}-{int(m.group(2))}-{m.group(3)[-2:]}"
    return norma


_RE_VERBO_DEROGA = re.compile(r"\b(no\s+(?:se\s+)?)?deroga[n]?\b", re.I)
# Una derogación PARCIAL («se deroga el párrafo II del artículo 54 de la Resolución…») no
# deroga la norma: si entre el verbo y la norma aparece una de estas, no cuenta.
_RE_PARCIAL = re.compile(r"p[áa]rrafo|art[íi]culo|\bart\.|numeral|literal|inciso|cap[íi]tulo|"
                         r"secci[óo]n|ordinal", re.I)
# Fin de frase: punto seguido de mayúscula, salvo «17. Disposición» (número de lista) y las
# abreviaturas de las normas: «resolución núm. DNRT-DT-2023001» NO termina en «núm.» (lo
# cazó la prueba con el texto real de la DNRT-DT-2025-003).
_RE_PUNTO_Y_MAYUSCULA = re.compile(r"\.\s+(?=[A-ZÁÉÍÓÚÑ])")
_ABREVIATURAS = {"núm", "num", "no", "nº", "n", "art", "arts", "res", "lit", "pág", "pag",
                 "párr", "parr", "inc", "dr", "dra", "lic", "ing", "sr", "sra", "ord"}


def _fin_de_frase(texto: str) -> int | None:
    for m in _RE_PUNTO_Y_MAYUSCULA.finditer(texto):
        antes = re.search(r"([\wº]+)$", texto[: m.start()])
        palabra = antes.group(1).lower() if antes else ""
        if palabra.isdigit() or palabra in _ABREVIATURAS:
            continue
        return m.start()
    return None


def derogaciones_en(texto: str) -> set[str]:
    """Claves de las normas que este texto deroga EXPRESAMENTE y ENTERAS.

    Revisión independiente del 24-nov-2026 (hallazgo 1, comprobado): bastaba «deroga» con una
    norma citada 600 caracteres después. Marcaba derogada entera la RES-787-2022 por «se
    deroga el párrafo II del artículo 54», y la 789-2022 por «esta disposición NO deroga…».
    Ahora: verbo afirmativo, norma en la MISMA frase, y nada de párrafo/artículo entre medias.
    """
    salida = set()
    for m in _RE_VERBO_DEROGA.finditer(texto):
        if m.group(1):                              # «no deroga»
            continue
        ventana = texto[m.end(): m.end() + 700]
        fin = _fin_de_frase(ventana)
        if fin is not None:
            ventana = ventana[:fin]

        def total(pos: int) -> bool:
            return not _RE_PARCIAL.search(ventana[:pos])

        for d in _RE_DT_EN_TEXTO.finditer(ventana):
            if total(d.start()):
                salida.add(f"{d.group(1).upper()}-{d.group(2)}-{int(d.group(3))}")
        for r in _RE_RES_EN_TEXTO.finditer(ventana):
            if total(r.start()):
                salida.add(f"RES-{int(r.group(1))}-{r.group(2)[-2:]}")
        for v in _RE_DT_VIEJA_EN_TEXTO.finditer(ventana):
            if total(v.start()):
                salida.add(f"DT-{int(v.group(1))}-{v.group(2)[-2:]}")
    return salida


def derogada_por(norma: str, textos_extra: dict[str, str] | None = None) -> str | None:
    """¿Alguna norma (del corpus o recién bajada) dice expresamente que deroga a esta?"""
    objetivo = clave(norma)
    # Un PROYECTO no deroga nada: el borrador de Recepción y Entrega en consulta pública
    # «deroga» la DNRT-DT-2023-002, y eso no es derecho vigente (lo cazó una prueba).
    for otra, texto in (textos_extra or {}).items():
        if otra != norma and not otra.startswith("BORRADOR-") \
                and objetivo in derogaciones_en(texto):
            return otra
    if not rutas.ARTICULOS.exists():
        return None
    estados = {}
    if rutas.NORMAS.exists():
        estados = {n["id"]: n.get("estado") for n in
                   json.loads(rutas.NORMAS.read_text(encoding="utf-8"))["normas"]}
    with rutas.ARTICULOS.open(encoding="utf-8") as f:
        for linea in f:
            u = json.loads(linea)
            if u["norma"] == norma or estados.get(u["norma"]) == "borrador" \
                    or u["norma"].startswith("BORRADOR-"):
                continue
            if "deroga" in u["texto"].lower() and objetivo in derogaciones_en(u["texto"]):
                return u["norma"]
    return None


def decidir_estado(norma: str, anio: int, nombre: str, enlace: dict,
                   ultimo_anio: dict, textos_nuevos: dict | None = None) -> tuple[str, str, str]:
    """(estado, reemplazada_por, nota) de un documento nuevo encontrado en ri.gob.do.

    Reglas medidas en el primer ensayo contra el sitio (24-nov-2026), no supuestas:
    - un PROYECTO (consulta pública, «xxxx», «propuesta») es `borrador`;
    - una LEY de la base legal oficial es `vigente`: las leyes no caducan porque salga otra;
    - un formulario o guía oficial (FO-/PL-/Guía) es `por_confirmar`: puede ser anterior a la
      disposición técnica vigente, y así se ve en la pantalla;
    - una DT o resolución tan reciente como la última de su tipo es `vigente`;
    - una MÁS ANTIGUA puede ser de otra materia y seguir viva (la DNRT-DT-2023-003 de
      Publicidad Registral no la sustituyó la 2025-003 de Requisitos): queda `derogada` si una
      norma del corpus dice expresamente que la deroga, y `por_confirmar` si no.
    """
    # El borrador se reconoce por el DOCUMENTO, no por la página que lo enlaza: medido el
    # 24-nov-2026, ri.gob.do enlaza desde «consulta pública» también formularios oficiales.
    if norma.startswith("BORRADOR-") or RE_BORRADOR.search(nombre + " " + enlace["texto"]):
        return "borrador", "", "Proyecto o borrador: no es norma vigente."
    tipo = norma.split("-")[0]
    if tipo == "LEY":
        return "vigente", "", "Ley de la base legal publicada por el Registro Inmobiliario."
    if tipo in ("FO", "PL", "GUIA"):
        return "por_confirmar", "", "Documento oficial del Registro Inmobiliario (formulario, " \
                                    "política o guía): confirmar que es la versión vigente."
    quien = derogada_por(norma, textos_nuevos)
    if quien:
        return "derogada", quien, f"Derogada expresamente por {quien}."
    if anio >= ultimo_anio.get(tipo, 0):
        return "vigente", "", ""
    quien = None
    if quien:
        return "derogada", quien, f"Derogada expresamente por {quien}."
    return "por_confirmar", "", "Anterior a la última de su tipo y sin derogación expresa encontrada."


_GENERICOS = re.compile(r"^(ver aqu[íi]|descargar|aqu[íi]|clic|click|pdf|enlace|ver)\W*$", re.I)


def titulo_de(texto_enlace: str, nombre: str, texto_pdf: str) -> str:
    """El título de un documento nuevo. El texto del enlace de ri.gob.do a veces es solo
    «Ver aquí» o «Descargar» (medido): entonces sale del nombre del archivo."""
    t = " ".join(texto_enlace.split())
    if len(t.split()) >= 3 and not _GENERICOS.match(t):
        return t[:200]
    base = re.sub(r"[_\-]+", " ", Path(nombre).stem)
    base = re.sub(r"\bV\s?\.?\s?0\b|\bpag \d\b", "", base, flags=re.I)
    return " ".join(base.split())[:200]


def sustituir_pdf(norma: str, datos: bytes, nombre: str) -> bool:
    """Pone el PDF republicado donde el corpus lo busca. El viejo queda como «.anterior»."""
    import yaml
    from chatbot.archivos import escribir
    from chatbot.corpus import buscar_pdf
    catalogo = yaml.safe_load(rutas.catalogo().read_text(encoding="utf-8"))
    ficha = next((n for g in GRUPOS for n in (catalogo.get(g) or []) if n["id"] == norma), None)
    if not ficha:
        return False
    actual = buscar_pdf(ficha, rutas.dirs_pdf())
    if actual is None:
        actual = rutas.PDF / "automatico" / f"{norma}_{nombre}"
    elif actual.exists():
        respaldo = actual.with_name(actual.name + ".anterior")
        respaldo.write_bytes(actual.read_bytes())
    escribir(actual, datos)
    return True


def es_de_la_materia(texto: str) -> bool:
    from chatbot.texto import sin_tildes
    t = sin_tildes(texto.lower())
    return sum(1 for p in PALABRAS_DE_LA_MATERIA if p in t) >= 3


def _texto_pdf(ruta: Path, paginas: int = 0) -> str:
    import pymupdf
    with pymupdf.open(ruta) as d:
        fin = d.page_count if not paginas else min(paginas, d.page_count)
        return "\n".join(d[i].get_text() for i in range(fin))


# ---------------------------------------------------------------------------
# Estado y novedades
# ---------------------------------------------------------------------------


def _leer(ruta: Path, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def _guardar(ruta: Path, dato) -> None:
    from chatbot.archivos import escribir
    escribir(ruta, json.dumps(dato, ensure_ascii=False, indent=1))


def anotar_novedad(texto: str, nueva: bool = True) -> None:
    NOVEDADES.parent.mkdir(parents=True, exist_ok=True)
    with NOVEDADES.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"fecha": time.strftime("%Y-%m-%d %H:%M"), "texto": texto,
                            "nueva": nueva}, ensure_ascii=False) + "\n")


def conocidas_iniciales() -> dict:
    """La primera vez: todo lo que el catálogo ya tiene se da por conocido, con su huella."""
    import yaml
    conocidas = {}
    catalogo = yaml.safe_load(rutas.catalogo().read_text(encoding="utf-8"))
    for grupo in GRUPOS:
        for n in catalogo.get(grupo) or []:
            url = str(n.get("url", ""))
            # Lo que añadió el propio actualizador NO es punto de partida: si se borra su
            # estado, tiene que volver a encontrarlo (pasó: se perdían los formularios).
            if url.startswith("http") and not n.get("grupo_auto"):
                conocidas[url] = {"norma": n["id"], "huella": "", "decision": "catalogo"}
    return conocidas


# ---------------------------------------------------------------------------
# Una vuelta
# ---------------------------------------------------------------------------


def revisar(seco: bool = False) -> dict:
    """Devuelve {'cambios': [...], 'avisos': [...]}. Si hay cambios y no es en seco, deja el
    corpus reconstruido (el servidor recarga después)."""
    import yaml
    from chatbot.corpus import construir, exportar_vocabulario

    conocidas = _leer(CONOCIDAS, None) or conocidas_iniciales()
    auto = _leer(CATALOGO_AUTO, {"normas": [], "derogaciones": []})
    catalogo = yaml.safe_load(rutas.catalogo().read_text(encoding="utf-8"))
    ids_catalogo = {n["id"] for g in GRUPOS
                    for n in (catalogo.get(g) or [])}
    ids_catalogo |= {n["id"] for n in auto["normas"]}
    ultimo_anio = {}
    for ident in ids_catalogo:
        tipo = ident.split("-")[0]
        anio = int(re.findall(r"\d{4}", ident)[-1]) if re.findall(r"\d{4}", ident) else 0
        ultimo_anio[tipo] = max(ultimo_anio.get(tipo, 0), anio)

    cambios, avisos = [], []
    nuevas: dict[str, dict] = {}
    claves_conocidas = {clave(i) for i in ids_catalogo}
    claves_a_id = {clave(i): i for i in ids_catalogo}
    fichas_catalogo = {n["id"]: n for g in GRUPOS for n in (catalogo.get(g) or [])}
    destino_dir = rutas.PDF / "automatico"
    for e in enlaces_oficiales():
        url = e["url"]
        previo = conocidas.get(url)
        if previo and previo.get("decision") == "descartada":
            continue
        cab = cabeceras(url)
        if previo and previo.get("huella") and cab and cab == previo.get("cabeceras"):
            continue                                  # mismo archivo que la última vez
        if seco:
            cambios.append(f"{'revisar' if previo else 'nuevo'}: {url}")
            continue
        try:
            datos = _bajar(url)
        except Exception as ex:  # noqa: BLE001
            avisos.append(f"no pude bajar {url}: {ex}")
            continue
        if not datos.startswith(b"%PDF"):
            continue
        h = hashlib.sha256(datos).hexdigest()
        if previo is not None:
            previo["cabeceras"] = cab
        if previo and previo.get("huella") == h:
            continue
        if previo and not previo.get("huella"):
            # Primera vez que se mide una norma ya catalogada: solo se guarda su huella.
            previo["huella"] = h
            continue

        nombre = Path(urllib.request.url2pathname(url.rsplit("/", 1)[-1])).name
        if previo and previo.get("norma"):
            # Republicada con otro contenido. Hallazgo 2 de la revisión: el PDF nuevo se
            # guardaba aparte y el corpus seguía leyendo el viejo, sin volver a intentarlo.
            # Ahora se escribe ENCIMA del que usa el corpus (el viejo queda como .anterior).
            if sustituir_pdf(previo["norma"], datos, nombre):
                previo["huella"] = h
                cambios.append(f"{previo['norma']} se republicó con cambios: texto actualizado")
            else:
                avisos.append(f"{previo['norma']} cambió en ri.gob.do pero no encontré su PDF "
                              "local para sustituirlo")
            continue
        temporal = destino_dir / nombre
        temporal.parent.mkdir(parents=True, exist_ok=True)
        temporal.write_bytes(datos)
        texto = _texto_pdf(temporal, paginas=4)

        if previo:                                   # republicada, pero sin norma asociada
            previo["huella"] = h
            continue
        # Lo que el RI publica como su BASE LEGAL, o sus propios formularios y guías, es de la
        # materia por definición. Medido: el filtro de palabras descartaba la Ley 64-00 y la
        # 202-04 (medio ambiente: no dicen «mensura»), que las Regionales citan al observar.
        de_oficio = (e["grupo"] == "leyes" or RE_ID_FORMULARIO.search(nombre)
                     or RE_ID_GUIA.search(nombre))
        if not de_oficio and not es_de_la_materia(texto):
            conocidas[url] = {"cabeceras": cab, "decision": "descartada", "huella": h, "motivo": "otra materia"}
            temporal.unlink(missing_ok=True)
            continue
        if RE_BORRADOR.search(nombre + " " + e["texto"]):
            # Un proyecto no tiene número oficial; el que trae dentro suele ser el de la norma
            # que pretende sustituir. Se nombra por su archivo.
            from chatbot.texto import sin_tildes
            corto = re.sub(r"[^A-Za-z0-9]+", "-", sin_tildes(Path(nombre).stem)).strip("-")
            anio_b = re.findall(r"20\d\d", nombre)
            ident = (f"BORRADOR-{corto[:48].upper()}", int(anio_b[-1]) if anio_b else 0)
        else:
            ident = identificar(nombre, texto)
        if not ident:
            conocidas[url] = {"cabeceras": cab, "decision": "sin_identificar", "huella": h}
            avisos.append(f"PDF de la materia sin identificador reconocible: {url}")
            temporal.unlink(missing_ok=True)
            continue
        norma, anio = ident
        m_pag = re.search(r"pag[_\s-]*(\d)", nombre, re.I)
        if m_pag and m_pag.group(1) != "1":
            norma = f"{norma}-P{m_pag.group(1)}"       # FO-DNMC-003 trae el acta en dos PDF
        if clave(norma) in claves_conocidas:
            # «Resolución núm. 008-2020» y RES-008-2020 son la misma: se compara normalizado.
            existente = claves_a_id.get(clave(norma))
            ficha_cat = fichas_catalogo.get(existente, {})
            if existente and not str(ficha_cat.get("url", "")).startswith("http"):
                # El catálogo la tenía SIN documento (Ley 64-00, 202-04): se completa con
                # este, sin tocar el estado que decidió una persona.
                auto["normas"].append({"id": existente, "url": url,
                                       "archivo": f"automatico/{nombre}",
                                       "grupo_auto": e["grupo"],
                                       "detectada": time.strftime("%Y-%m-%d")})
                conocidas[url] = {"cabeceras": cab, "decision": "completa_catalogo",
                                  "norma": existente, "huella": h}
                cambios.append(f"{existente}: encontrado su texto oficial en ri.gob.do")
                continue
            # Otra copia de algo que ya está (ri.gob.do sube la RES-82-2025 con dos nombres).
            conocidas[url] = {"cabeceras": cab, "decision": "catalogo", "norma": norma, "huella": h}
            temporal.unlink(missing_ok=True)
            continue
        nuevas[norma] = {"anio": anio, "nombre": nombre, "enlace": e, "url": url, "cab": cab,
                         "huella": h, "texto": _texto_pdf(temporal)}
        claves_conocidas.add(clave(norma))

    # Los estados se deciden DESPUÉS de leer todo lo nuevo: las derogaciones van en cadena
    # (la DNRT-DT-2025-002 deroga la 2023-002, que a su vez había sustituido a la 2021-0002) y
    # la que deroga puede haber llegado en la misma vuelta.
    textos = {n: d["texto"] for n, d in nuevas.items()}
    claves_catalogo = {clave(i): i for i in ids_catalogo}
    for norma, d in nuevas.items():
        estado, reemplazo, nota = decidir_estado(norma, d["anio"], d["nombre"], d["enlace"],
                                                 ultimo_anio, textos)
        titulo = titulo_de(d["enlace"]["texto"], d["nombre"], d["texto"])
        ficha = {"id": norma, "titulo": titulo, "anio": d["anio"], "estado": estado,
                 "relevancia": "media", "url": d["url"], "archivo": f"automatico/{d['nombre']}",
                 "grupo_auto": d["enlace"]["grupo"], "detectada": time.strftime("%Y-%m-%d"),
                 "nota": f"Incorporada automáticamente desde ri.gob.do. {nota}".strip()}
        if reemplazo:
            ficha["reemplazada_por"] = reemplazo
        auto["normas"].append(ficha)
        conocidas[d["url"]] = {"cabeceras": d["cab"], "decision": "incorporada", "norma": norma,
                               "huella": d["huella"]}
        cambios.append(f"Nueva norma {norma} ({estado}): {titulo[:120]}")
        # Lo que esta norma deroga expresamente, del catálogo de antes.
        if estado != "borrador":
            for k in derogaciones_en(d["texto"]):
                derogada = claves_catalogo.get(k)
                if derogada and derogada != norma and not any(
                        x["norma"] == derogada for x in auto["derogaciones"]):
                    auto["derogaciones"].append({"norma": derogada, "por": norma,
                                                 "fecha": time.strftime("%Y-%m-%d")})
                    cambios.append(f"{derogada} queda DEROGADA (la deroga expresamente {norma})")

    if not seco:
        _guardar(CONOCIDAS, conocidas)
        _guardar(CATALOGO_AUTO, auto)

    # El Revisor X, si está en este PC, manda en las vigencias que decidió una persona.
    revisor = rutas.revisor_x() / "conocimiento" / "normativa" / "CATALOGO.yaml"
    copia = rutas.CORPUS / "catalogo_revisor.yaml"
    if revisor.exists():
        from chatbot.catalogo import sanear
        saneado = sanear(revisor.read_text(encoding="utf-8"))
        if not copia.exists() or copia.read_text(encoding="utf-8") != saneado:
            if not seco:
                from chatbot.archivos import escribir
                escribir(copia, saneado)
            cambios.append("Catálogo del Revisor X actualizado")

    if cambios and not seco:
        from chatbot.catalogo import fundir
        for aviso in fundir():
            if "posible derogación" in aviso:
                avisos.append(aviso)
                anotar_novedad(f"Para revisar: {aviso}")
        construir(rutas.dirs_pdf(), rutas.catalogo(), rutas.GENERADO)
        exportar_vocabulario(rutas.VOCABULARIO, rutas.VOCABULARIO_JSON)
        from chatbot.corpus import exportar_lista_de_normas
        exportar_lista_de_normas(rutas.NORMAS, rutas.RAIZ / "docs" / "NORMAS.md")
        for c in cambios:
            anotar_novedad(c)
    return {"cambios": cambios, "avisos": avisos}


# ---------------------------------------------------------------------------
# Programación dentro del servidor
# ---------------------------------------------------------------------------


def programar(app) -> None:
    from chatbot.motor_local import leer_config
    cfg = {"activa": True, "cada_dias": 7, "hora": 3}
    try:
        cfg.update(json.loads(rutas.CONFIG.read_text(encoding="utf-8")).get("actualizacion", {}))
    except (OSError, ValueError):
        pass
    leer_config()
    if not cfg.get("activa"):
        log.info("actualización automática apagada (config.json)")
        return

    def bucle():
        # Primera mirada a los 10 min de arrancar (no estorba la carga) y después cada 30 min.
        # Hallazgo 8: exigir la hora exacta («a las 3») hacía que con el .exe —que solo vive
        # mientras la ventana está abierta— o un portátil dormido no actualizara nunca. Ahora
        # se hace en cuanto toca; la hora configurada es la PREFERIDA si coincide.
        time.sleep(600)
        while True:
            estado = _leer(ESTADO, {})
            ultima = estado.get("ultima_ts", 0)
            ahora = time.time()
            atrasada = ahora - ultima >= cfg["cada_dias"] * 86400
            muy_atrasada = ahora - ultima >= (cfg["cada_dias"] + 1) * 86400
            if atrasada and (time.localtime().tm_hour == cfg["hora"] or muy_atrasada or not ultima):
                try:
                    r = revisar()
                    if r["cambios"]:
                        app.recargar()
                        if getattr(app, "vectores", None) is not None:
                            app.vectores.calcular()
                    estado = {"ultima_ts": ahora, "ultima": time.strftime("%Y-%m-%dT%H:%M"),
                              "cambios": r["cambios"], "avisos": r["avisos"]}
                except Exception as e:  # noqa: BLE001
                    log.warning("actualización fallida: %s", e)
                    estado = {**estado, "ultimo_error": str(e)[:300],
                              "ultimo_intento": time.strftime("%Y-%m-%dT%H:%M")}
                _guardar(ESTADO, estado)
            time.sleep(1800)

    threading.Thread(target=bucle, daemon=True, name="actualizador").start()
    log.info("actualización automática: cada %s días a las %s:00", cfg["cada_dias"], cfg["hora"])


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    partes = argparse.ArgumentParser(description=__doc__)
    partes.add_argument("--seco", action="store_true")
    args = partes.parse_args()
    r = revisar(seco=args.seco)
    if not args.seco:   # que el servidor sepa que ya se revisó y no lo repita enseguida
        _guardar(ESTADO, {"ultima_ts": time.time(), "ultima": time.strftime("%Y-%m-%dT%H:%M"),
                          "cambios": r["cambios"], "avisos": r["avisos"], "origen": "a mano"})
    print(f"\n  {len(r['cambios'])} cambio(s)")
    for c in r["cambios"]:
        print(f"    + {c}")
    for a in r["avisos"]:
        print(f"    ! {a}")
    if r["cambios"] and not args.seco:
        print("\n  Corpus reconstruido. Faltan los vectores: py -m chatbot.vectores")
    return 0


if __name__ == "__main__":
    sys.exit(main())

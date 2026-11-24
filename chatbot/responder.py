"""De los artículos encontrados a la respuesta: rápida (literal), redactada (modelo) y verificada.

## La respuesta rápida no inventa nada

Sale en milisegundos y es la que queda si no hay modelo: las frases del artículo que mejor
casan con la pregunta, entre comillas y con su referencia. Lo único que se escribe sin
copiar es la vigencia, que sale del catálogo, no de una deducción.

## Al modelo se le da POCO y BIEN ELEGIDO

Medido el 24-nov-2026 en el portátil de Henry (GTX 1050): con 6 artículos enteros
(~2.700 tokens) el modelo tardaba 25 s solo en LEERLOS, y aun así eligió el artículo
equivocado (regularización en vez de deslinde). Ahora recibe 4 fragmentos de ~900
caracteres —las frases que responden y sus vecinas— y lee tres veces menos.

## Y se le prohíbe lo que hace mal

Un modelo pequeño confunde nombres de normas y números de artículo. Así que no los escribe:
cita con [n] y la pantalla pone la referencia exacta. Lo que aun así escriba se verifica
(`verificar`): cada [n] tiene que existir, cada «Art. N» tiene que estar entre los fragmentos
y **cada cifra tiene que aparecer en lo citado**. Lo que no cuadra sale como aviso visible.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from chatbot.buscador import Buscador, Consulta, Resultado, Unidad
from chatbot.texto import sin_tildes, terminos

FRAGMENTOS_AL_MODELO = 4
CARACTERES_POR_FRAGMENTO = 900
CARACTERES_EXTRACTO = 420
COBERTURA_ALTA = 0.6
COBERTURA_MINIMA = 0.45         # con vectores: ajena si cubre menos que esto…
SIMILITUD_MINIMA = 0.55         # …y además se parece menos que esto a todo el corpus
COBERTURA_SOLO_PALABRAS = 0.40  # sin vectores (más prudente: no hay segunda señal)


@dataclass
class Fuente:
    n: int
    norma: str
    norma_titulo: str
    articulo: str
    apartado: str
    titulo: str
    seccion: str
    estado: str
    aviso: str
    extracto: str
    url: str
    pagina: int
    motivo: str = ""
    contexto: str = field(default="", repr=False)    # lo que lee el modelo (no se envía)
    unidad_id: int = -1
    contexto_amplio: str = field(default="", repr=False)
    texto_completo: str = field(default="", repr=False)

    def publica(self) -> dict:
        d = asdict(self)
        for interno in ("contexto", "contexto_amplio", "unidad_id", "texto_completo"):
            d.pop(interno)
        return d


# «¿Qué documentos lleva un deslinde?», «requisitos para…», «¿qué debe contener el acta?»:
# la respuesta es una LISTA, y un recorte de 900 caracteres la deja a medias.
_RE_PIDE_LISTA = re.compile(
    r"\b(documentos?|requisitos?|lista|cuales son|que lleva|que llevan|que necesito|"
    r"que debe(?:n)? contener|que contiene|contenido|elementos|pasos)\b")
CARACTERES_LISTA = 3800


def pide_lista(pregunta: str) -> bool:
    return bool(_RE_PIDE_LISTA.search(sin_tildes(pregunta.lower())))


# ---------------------------------------------------------------------------
# Nombres
# ---------------------------------------------------------------------------


def nombre_corto(norma: str) -> str:
    """RES-790-2022 → «Res. 790-2022»; LEY-108-05 → «Ley 108-05»; DNMC-DT-2023-001 → «DT DNMC
    2023-001»."""
    if norma.startswith("RES-"):
        return "Res. " + norma[4:]
    if norma.startswith("LEY-"):
        return "Ley " + norma[4:]
    m = re.match(r"(DNMC|DNRT)-DT-(\d{4})-(\d+)", norma)
    if m:
        return f"DT {m.group(1)} {m.group(2)}-{m.group(3)}"
    return norma


def referencia(norma: str, articulo: str, apartado: str = "") -> str:
    ref = f"{nombre_corto(norma)}, {articulo}"
    return ref + (f", num. {apartado}" if apartado else "")


# ---------------------------------------------------------------------------
# Frases relevantes de un artículo
# ---------------------------------------------------------------------------

_CORTE = re.compile(
    r"(?<=[.;:])\s+(?=[A-ZÁÉÍÓÚÑ¿(«\"]|[a-zñ]\)\s|\d{1,2}[.)]\s|P[áa]rrafo)|\n+")


def segmentos(texto: str) -> list[str]:
    piezas = [p.strip() for p in _CORTE.split(texto) if p and p.strip()]
    salida: list[str] = []
    for p in piezas:
        if salida and len(salida[-1]) < 45:
            salida[-1] = f"{salida[-1]} {p}"
        else:
            salida.append(p)
    return salida


def _pesos(c: Consulta, buscador: Buscador) -> dict[str, float]:
    pesos = {}
    for t in c.terminos:
        pesos[t] = buscador.indice.idf.get(t, 0.0)
    for t in c.expandidos:
        pesos.setdefault(t, 0.45 * buscador.indice.idf.get(t, 0.0))
    if getattr(c, "pide_plazo", False):
        # Pregunta de plazo: la frase con la DURACIÓN («diez (10) días…») vale tanto como la
        # palabra más rara de la pregunta. Así es la que llega al modelo y a la respuesta.
        pesos[_DURACION] = max(pesos.values(), default=1.0) or 1.0
    return pesos


_DURACION = "__duracion__"


def _puntuar(segmento: str, pesos: dict[str, float]) -> float:
    from chatbot.buscador import RE_DURACION
    ts = set(terminos(segmento))
    bruto = sum(p for t, p in pesos.items() if t in ts)
    if _DURACION in pesos and RE_DURACION.search(sin_tildes(segmento.lower())):
        bruto += pesos[_DURACION]
    return bruto / (1 + len(segmento) / 700)


def frases_clave(texto: str, pesos: dict[str, float], maximo: int) -> str:
    """Las frases que responden y sus vecinas, en su orden, con «…» donde hay salto."""
    if len(texto) <= maximo:
        return texto.strip()
    segs = segmentos(texto)
    if not segs:
        return texto[:maximo]
    puntos = [_puntuar(s, pesos) for s in segs]
    mejor = max(range(len(segs)), key=lambda i: puntos[i])
    elegidos = {mejor}
    largo = len(segs[mejor])
    # Crecer hacia los vecinos con más puntos, sin pasar del máximo.
    izq, der = mejor - 1, mejor + 1
    while True:
        opciones = [i for i in (izq, der) if 0 <= i < len(segs)
                    and largo + len(segs[i]) + 1 <= maximo]
        if not opciones:
            break
        i = max(opciones, key=lambda j: (puntos[j], -abs(j - mejor)))
        elegidos.add(i)
        largo += len(segs[i]) + 1
        if i == izq:
            izq -= 1
        else:
            der += 1
    # La segunda mejor frase, si está lejos y cabe.
    resto = sorted((i for i in range(len(segs)) if i not in elegidos), key=lambda i: -puntos[i])
    if resto and puntos[resto[0]] > 0 and largo + len(segs[resto[0]]) + 3 <= maximo:
        elegidos.add(resto[0])
    salida, previo = [], None
    for i in sorted(elegidos):
        if previo is not None and i != previo + 1:
            salida.append("…")
        salida.append(segs[i])
        previo = i
    if min(elegidos) > 0:
        salida.insert(0, "…")
    if max(elegidos) < len(segs) - 1:
        salida.append("…")
    return " ".join(salida)


def cobertura(c: Consulta, unidad: Unidad, buscador: Buscador) -> float:
    """Qué parte de lo que se pregunta (ponderado por rareza) aparece en la unidad.

    Una palabra que NO existe en el corpus («sancocho») pesa lo máximo: es justo la señal de
    que la pregunta es de otra materia, y antes no contaba (peso 0)."""
    idf = buscador.indice.idf
    tope = max(idf.values(), default=1.0)
    pesos = {t: idf.get(t, tope) for t in c.terminos}
    total = sum(pesos.values())
    if not total:
        return 0.0
    from chatbot.buscador import cabecera  # evitar import circular al cargar
    ts = set(terminos(unidad.texto + " " + cabecera(unidad, buscador.normas)))
    return sum(p for t, p in pesos.items() if t in ts) / total


# ---------------------------------------------------------------------------
# Fuentes
# ---------------------------------------------------------------------------


def _url(buscador: Buscador, u: Unidad) -> str:
    url = buscador.normas.get(u.norma, {}).get("url", "")
    if url and url.lower().split("?")[0].endswith(".pdf") and u.pagina:
        return f"{url}#page={u.pagina}"
    return url


def _fuente(n: int, u: Unidad, estado: str, buscador: Buscador, pesos, motivo="",
            aviso="") -> Fuente:
    norma = buscador.normas.get(u.norma, {})
    return Fuente(
        n=n, norma=u.norma, norma_titulo=norma.get("titulo", ""), articulo=u.articulo,
        apartado=u.apartado, titulo=u.titulo, seccion=u.seccion, estado=estado, aviso=aviso,
        extracto=frases_clave(u.texto, pesos, CARACTERES_EXTRACTO),
        url=_url(buscador, u), pagina=u.pagina, motivo=motivo,
        contexto=frases_clave(u.texto, pesos, CARACTERES_POR_FRAGMENTO), unidad_id=u.id,
        contexto_amplio=frases_clave(u.texto, pesos, 2 * CARACTERES_POR_FRAGMENTO),
        texto_completo=u.texto)


def preparar_fuentes(c: Consulta, resultados: list[Resultado], buscador: Buscador,
                     maximo: int = 6) -> list[Fuente]:
    """Resultados → fuentes numeradas. Un artículo reformado trae DETRÁS el texto que lo
    reforma, y una norma derogada nombrada en la pregunta trae su ficha de vigencia."""
    pesos = _pesos(c, buscador)
    fuentes: list[Fuente] = []
    usados: set[int] = set()

    # Vigencia de las normas nombradas: sale del catálogo y va primero.
    for ident in c.normas_nombradas:
        n = buscador.normas.get(ident, {})
        if n.get("estado") in ("derogada", "borrador", "por_confirmar"):
            fuentes.append(ficha_de_vigencia(len(fuentes) + 1, ident, buscador))

    for r in resultados:
        if len(fuentes) >= maximo:
            break
        u = r.unidad
        if u.id in usados:
            continue
        aviso = ""
        if r.reformado_por:
            refs = "; ".join(referencia(x.norma, x.articulo) for x in r.reformado_por)
            aviso = f"Texto anterior: este artículo lo reformó {refs}."
        elif r.reformado_por == [] and (u.norma, u.articulo) in buscador.reformas:
            aviso = "Este artículo fue reformado; el PDF trae el texto anterior."
        if r.estado == "derogada":
            reemplazo = buscador.normas.get(u.norma, {}).get("reemplazada_por", "")
            aviso = f"Norma DEROGADA{f'; la reemplaza {nombre_corto(reemplazo)}' if reemplazo else ''}."
        if r.reforma_a:
            aviso = aviso or f"Texto vigente que reforma: {'; '.join(r.reforma_a)}."
        fuentes.append(_fuente(len(fuentes) + 1, u, r.estado, buscador, pesos, r.motivo, aviso))
        usados.add(u.id)
        for x in r.reformado_por:
            if x.id not in usados and len(fuentes) < maximo + 2:
                estado = buscador.normas.get(x.norma, {}).get("estado", "vigente")
                fuentes.append(_fuente(len(fuentes) + 1, x, estado, buscador, pesos,
                                       "reforma el artículo anterior",
                                       f"Texto vigente que reforma {referencia(u.norma, u.articulo)}."))
                usados.add(x.id)
    return fuentes


def ficha_de_vigencia(n: int, ident: str, buscador: Buscador) -> Fuente:
    norma = buscador.normas.get(ident, {})
    estado = norma.get("estado", "por_confirmar")
    partes = [f"{nombre_corto(ident)} ({norma.get('titulo', '')}): estado {estado.upper()} "
              "según el catálogo de normas del chatbot."]
    if norma.get("reemplazada_por"):
        partes.append(f"La reemplaza {nombre_corto(norma['reemplazada_por'])}.")
    if norma.get("nota"):
        partes.append(norma["nota"][:400])
    texto = " ".join(partes)
    return Fuente(n=n, norma=ident, norma_titulo=norma.get("titulo", ""), articulo="Vigencia",
                  apartado="", titulo="Estado de la norma según el catálogo", seccion="",
                  estado=estado, aviso="", extracto=texto, url=norma.get("url", ""), pagina=0,
                  motivo="nombrada en la pregunta", contexto=texto)


# ---------------------------------------------------------------------------
# Respuesta rápida (sin modelo)
# ---------------------------------------------------------------------------


def respuesta_rapida(c: Consulta, resultados: list[Resultado], fuentes: list[Fuente],
                     buscador: Buscador) -> dict:
    if not fuentes:
        return {"texto": "No encontré nada sobre esto en la normativa cargada. Prueba con "
                         "otras palabras o nombra la norma o el trámite.", "confianza": "baja"}
    cob = max((cobertura(c, r.unidad, buscador) for r in resultados[:3]), default=0.0)
    nombrado = any(f.motivo == "nombrado en la pregunta" for f in fuentes)
    # Calibrado con la batería (24-nov-2026, evaluacion/RESULTADOS.md). Con búsqueda por
    # significado: «ajena» solo si cubre poco Y se parece poco → 0 de 96 legítimas rechazadas
    # (frena 2 de 10 ajenas; las demás las frena el modelo con «no encuentro»). Rechazar una
    # pregunta buena es peor que dejar pasar una ajena al modelo.
    if c.similitudes:
        ajena = cob < COBERTURA_MINIMA and max(c.similitudes.values()) < SIMILITUD_MINIMA
    else:
        ajena = cob < COBERTURA_SOLO_PALABRAS
    confianza = "baja" if ajena else ("alta" if (cob >= COBERTURA_ALTA or nombrado) else "media")

    lineas = []
    if confianza == "baja":
        lineas.append("No encontré una respuesta clara en la normativa cargada. "
                      "Lo más cercano es:\n")
    vistos = set()
    for f in fuentes:
        if len(vistos) >= 3:
            break
        clave = (f.norma, f.articulo)
        if clave in vistos:
            continue
        vistos.add(clave)
        cabecera_ref = (f"**{nombre_corto(f.norma)} — vigencia**" if f.articulo == "Vigencia"
                        else f"**{referencia(f.norma, f.articulo, f.apartado)}**")
        titulo = f" ({f.titulo})" if f.titulo and f.articulo != "Vigencia" else ""
        if not vistos - {clave} and pide_lista(c.texto) and f.texto_completo:
            # La primera fuente de una pregunta de «lista» se enseña más larga: es la lista.
            extracto = frases_clave(f.texto_completo, _pesos(c, buscador), 1200)
        else:
            extracto = frases_clave(f.extracto, _pesos(c, buscador), 360)
        # Los saltos de línea del PDF partían la cita en dos párrafos («el Código / Civil»).
        extracto = " ".join(extracto.split())
        lineas.append(f"- {cabecera_ref}{titulo}: «{extracto}» [{f.n}]")
        if f.aviso:
            lineas.append(f"  ⚠ {f.aviso}")
    return {"texto": "\n".join(lineas), "confianza": confianza}


# ---------------------------------------------------------------------------
# Instrucciones para el modelo
# ---------------------------------------------------------------------------

SISTEMA = """Eres el asistente de normativa de mensura de Lexgeo. Respondes preguntas sobre la \
normativa inmobiliaria y de mensura catastral de la República Dominicana usando ÚNICAMENTE \
los fragmentos numerados que se te dan.

Reglas:
1. Empieza por la respuesta concreta (sí/no, la cifra, el plazo, el requisito o la lista) y \
explícala en pocas frases, en español claro.
2. Pon después de cada afirmación el número del fragmento que la sostiene entre corchetes, \
por ejemplo [1] o [2][3].
3. No escribas números de artículo ni nombres de resoluciones o leyes: el programa los \
añade a partir de los corchetes.
4. Copia cifras, plazos, porcentajes y medidas EXACTAMENTE como aparecen en los fragmentos.
5. Si un fragmento dice TEXTO ANTERIOR o DEROGADA, no lo presentes como vigente.
6. Si los fragmentos no responden la pregunta, contesta solo: «No encuentro esto en la \
normativa cargada.» No inventes ni completes con conocimiento propio.
7. Máximo 160 palabras, salvo que pidan una lista de requisitos o documentos."""


def mensajes_para_modelo(pregunta: str, fuentes: list[Fuente],
                         historial: list[dict] | None = None) -> list[dict]:
    bloques = []
    lista = pide_lista(pregunta)
    for i, f in enumerate(fuentes[:FRAGMENTOS_AL_MODELO]):
        marcas = []
        if f.estado == "derogada":
            marcas.append("DEROGADA")
        if f.aviso.startswith("Texto anterior"):
            marcas.append("TEXTO ANTERIOR, YA REFORMADO")
        titulo = f" — {f.titulo}" if f.titulo else ""
        apartado = f" — {f.apartado}" if f.apartado else ""
        cabecera_f = f"[{f.n}] {f.norma_titulo}{apartado}{titulo}"
        if marcas:
            cabecera_f += f" ({', '.join(marcas)})"
        # La primera fuente es la más probable: va con el doble de contexto, para que la
        # frase que responde no se quede fuera por un recorte desafortunado.
        cuerpo = f.contexto_amplio if i == 0 and f.contexto_amplio else f.contexto
        if i == 0 and lista and f.texto_completo:
            cuerpo = f.texto_completo[:CARACTERES_LISTA]
        bloques.append(f"{cabecera_f}\n{cuerpo}")
    anterior = ""
    if historial:
        ultima = historial[-1].get("pregunta", "")
        if ultima:
            anterior = f"(Pregunta anterior de la conversación: «{ultima}»)\n"
    usuario = ("FRAGMENTOS:\n\n" + "\n\n".join(bloques) +
               f"\n\n{anterior}PREGUNTA: {pregunta}\n\nRESPUESTA (con [n]):")
    return [{"role": "system", "content": SISTEMA}, {"role": "user", "content": usuario}]


# ---------------------------------------------------------------------------
# Verificación de lo que escribe el modelo
# ---------------------------------------------------------------------------

_RE_CITA = re.compile(r"\[(\d{1,2}(?:\s*[,y]\s*\d{1,2})*)\]")
_RE_ART_EN_TEXTO = re.compile(r"\bart(?:[íi]culo|\.)\s*(\d{1,3})", re.I)
# Cifras con significado: 5, 0.20, 10 cm, 15 días… (no las de dentro de una cita [n]).
_RE_CIFRA = re.compile(r"(?<![\w\[.,])(\d+(?:[.,]\d+)?)(?![\w\]])")


def limpiar(texto: str) -> str:
    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.S)
    texto = re.sub(r"\[(?:fragmento|fuente|frag\.?)\s*(\d+)\]", r"[\1]", texto, flags=re.I)
    texto = re.sub(r"\((?:fragmento|fuente)\s*(\d+)\)", r"[\1]", texto, flags=re.I)
    # «[2i]», «[2, i]», «[2 lit. e]»: el modelo cita también el literal; la fuente es la 2.
    texto = re.sub(r"\[(\d{1,2})\s*,?\s*(?:lit\.?\s*)?[a-zñ]{1,2}\)?\]", r"[\1]", texto)
    return texto.strip()


def _normalizar_cifra(c: str) -> str:
    return c.replace(",", ".").rstrip("0").rstrip(".") if "." in c or "," in c else c


def verificar(texto: str, fuentes: list[Fuente]) -> dict:
    """Comprueba la respuesta del modelo contra sus fuentes. Nunca la cambia en silencio:
    lo que no cuadra se devuelve como aviso visible."""
    avisos = []
    validos = {f.n for f in fuentes}
    citadas: list[int] = []
    for m in _RE_CITA.finditer(texto):
        for x in re.split(r"\s*[,y]\s*", m.group(1)):
            if x.isdigit():
                citadas.append(int(x))
    malas = sorted({n for n in citadas if n not in validos})
    if malas:
        avisos.append(f"Cita a fuentes que no existen: {', '.join(f'[{n}]' for n in malas)}.")
    citadas_ok = [n for n in dict.fromkeys(citadas) if n in validos]
    no_encuentra = "no encuentro esto" in sin_tildes(texto.lower())
    if not citadas_ok and not no_encuentra:
        avisos.append("La redacción no cita ninguna fuente: contrástala con las citas literales.")

    usadas = [f for f in fuentes if f.n in citadas_ok] or fuentes
    texto_fuentes = " ".join(f"{f.contexto} {f.extracto}" for f in usadas)
    todo = " ".join(f"{f.contexto} {f.extracto} {f.articulo}" for f in fuentes)

    # Artículos mencionados que no están entre las fuentes.
    arts_fuentes = {re.sub(r"\D", "", f.articulo) for f in fuentes}
    for m in _RE_ART_EN_TEXTO.finditer(texto):
        if m.group(1) not in arts_fuentes:
            avisos.append(f"Menciona el artículo {m.group(1)}, que no está entre las fuentes.")

    # Cifras que no aparecen en lo citado.
    sin_citas = _RE_CITA.sub(" ", texto)
    cifras_fuente = {_normalizar_cifra(x) for x in _RE_CIFRA.findall(todo)}
    cifras_citadas = {_normalizar_cifra(x) for x in _RE_CIFRA.findall(texto_fuentes)}
    for cifra in dict.fromkeys(_RE_CIFRA.findall(sin_citas)):
        c = _normalizar_cifra(cifra)
        if c in cifras_citadas:
            continue
        if c in cifras_fuente:
            avisos.append(f"La cifra {cifra} está en otra fuente, no en la citada.")
        else:
            avisos.append(f"La cifra {cifra} no aparece en los artículos: compruébala.")
    return {"citas": citadas_ok, "avisos": list(dict.fromkeys(avisos))}

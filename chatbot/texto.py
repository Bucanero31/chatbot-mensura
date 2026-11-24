"""Texto en español: normalizar, partir en palabras y reducirlas a su raíz.

Sin dependencias. Lo usan por igual el corpus (al indexar) y el buscador (al preguntar): si
los dos no reducen las palabras exactamente igual, la búsqueda falla en silencio.

**Por qué un lematizador.** El índice del Revisor X (FTS5 `unicode61`) busca la palabra tal
cual: «deslindes» no encuentra «deslinde», ni «notificar» encuentra «notificación». En un
informe da igual —allí se busca un artículo concreto—, pero en un chat la persona escribe
como habla.

**Por qué se quitan las tildes ANTES de reducir.** El Snowball original trabaja con tildes:
«regularización» pierde «ación», pero «regularizacion» (tecleado sin tilde, que es lo normal
en un chat) no la pierde y las dos palabras quedan con raíces distintas. Aquí las listas de
sufijos van sin tildes y el texto se reduce ya sin ellas, así que da igual cómo se escriba.
Lo que importa no es imitar a Snowball, es que el índice y la pregunta coincidan.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------

_ESPACIOS_RAROS = dict.fromkeys(map(ord, "     ​﻿"), " ")


def limpiar_espacios(texto: str) -> str:
    """Espacios duros y anchos cero → espacio normal; varios espacios → uno."""
    texto = texto.translate(_ESPACIOS_RAROS)
    return re.sub(r"[ \t]+", " ", texto)


def sin_tildes(texto: str) -> str:
    """«Regularización» → «regularizacion». La ñ se conserva: «año» no es «ano»."""
    salida = []
    for c in unicodedata.normalize("NFD", texto):
        if unicodedata.combining(c):
            # La tilde de la ñ es la única que cambia la palabra.
            if c == "̃" and salida and salida[-1] in "nN":
                salida[-1] = "ñ" if salida[-1] == "n" else "Ñ"
            continue
        salida.append(c)
    return "".join(salida)


# ---------------------------------------------------------------------------
# Palabras vacías
# ---------------------------------------------------------------------------

VACIAS = frozenset(sin_tildes(p) for p in """
a al algo algun alguna algunas alguno algunos ante antes aquel aquella aquellas aquello
aquellos aqui asi aun bajo bien cada como con contra cual cuales cualquier cuando cuanto
cuanta cuantas cuantos de del desde donde dos e el ella ellas ello ellos en entre era eran
es esa esas ese eso esos esta estan estas este esto estos fue fueron ha han hasta hay la
las le les lo los mas me mi mis mucho muy nada ni no nos o otra otras otro otros para pero
poco por porque que quien quienes se sea sean segun ser si sido siempre sin sino sobre su
sus tal tambien tan tanto te tiene tienen toda todas todo todos tras tu tus un una unas uno
unos usted ustedes y ya yo
puede pueden debe deben dice dicen decir hace hacer hacen tengo tenemos necesito necesita
quiero saber cuanto cual cuales que como donde cuando
varios varias vario varia diversos diversas demas mismo misma mismos mismas dicho dicha
""".split())
# «varios» y «variar» comparten raíz («vari»): sin quitar el cuantificador, «¿cuánto puede
# variar la superficie?» elegía el literal j) del Art. 13 («varios deslindes») en vez del e)
# («variación de hasta un cinco por ciento»). Medido el 24-nov-2026 en la pantalla real.

# ---------------------------------------------------------------------------
# Lematizador (Snowball español, sobre texto ya sin tildes)
# ---------------------------------------------------------------------------

_VOCALES = frozenset("aeiou")


def _regiones(p: str) -> tuple[int, int, int]:
    """Posiciones de inicio de RV, R1 y R2 según Snowball."""
    n = len(p)

    def r_desde(i: int) -> int:
        for j in range(i + 1, n):
            if p[j] not in _VOCALES and p[j - 1] in _VOCALES:
                return j + 1
        return n

    r1 = r_desde(0)
    r2 = r_desde(r1) if r1 < n else n

    if n < 2:
        rv = n
    elif p[1] not in _VOCALES:
        rv = next((j + 1 for j in range(2, n) if p[j] in _VOCALES), n)
    elif p[0] in _VOCALES and p[1] in _VOCALES:
        rv = next((j + 1 for j in range(2, n) if p[j] not in _VOCALES), n)
    else:
        rv = 3 if n >= 3 else n
    return rv, r1, r2


def _termina(p: str, sufijos: tuple[str, ...]) -> str | None:
    for s in sufijos:  # ordenados de más largo a más corto
        if p.endswith(s):
            return s
    return None


def _ordenar(lista: str) -> tuple[str, ...]:
    return tuple(sorted(set(lista.split()), key=len, reverse=True))


_PRONOMBRES = _ordenar("me se sela selo selas selos la le lo las les los nos")
_ANTES_DE_PRONOMBRE = _ordenar("iendo ando ar er ir yendo")

_S1_BORRAR_R2 = _ordenar("""anza anzas ico ica icos icas ismo ismos able ables ible ibles
ista istas oso osa osos osas amiento amientos imiento imientos""")
_S1_ADOR = _ordenar("adora ador acion adoras adores aciones ante antes ancia ancias")
_S1_LOGIA = _ordenar("logia logias")
_S1_UCION = _ordenar("ucion uciones")
_S1_ENCIA = _ordenar("encia encias")
_S1_IDAD = _ordenar("idad idades")
_S1_IVO = _ordenar("iva ivo ivas ivos")

_S2A = _ordenar("ya ye yan yen yeron yendo yo yas yes yais yamos")
_S2B_GU = _ordenar("en es eis emos")
_S2B = _ordenar("""
arian arias aran aras ariais aria areis ariamos aremos ara are erian erias eran eras
eriais eria ereis eriamos eremos era ere irian irias iran iras iriais iria ireis iriamos
iremos ira ire aba ada ida ia ara iera ad ed id ase iese aste iste an aban ian aran ieran
asen iesen aron ieron ado ido ando iendo io ar er ir as abas adas idas ias aras ieras ases
ieses is ais abais iais arais ierais aseis ieseis asteis isteis ados idos amos abamos
iamos imos aramos ieramos iesemos asemos""")
# Sin la «i»: en Snowball es «í» con tilde, y sin tildes cortaría «superficie(s)» de dos
# maneras distintas (superfici / superfic).
_S3 = _ordenar("os a o")


def _paso1(p: str, r1: int, r2: int) -> str | None:
    """Sufijos de derivación. Devuelve la palabra cambiada o None si no tocó nada."""
    s = _termina(p, _S1_BORRAR_R2)
    if s and len(p) - len(s) >= r2:
        return p[: -len(s)]
    s = _termina(p, _S1_ADOR)
    if s and len(p) - len(s) >= r2:
        p = p[: -len(s)]
        if p.endswith("ic") and len(p) - 2 >= r2:
            p = p[:-2]
        return p
    s = _termina(p, _S1_LOGIA)
    if s and len(p) - len(s) >= r2:
        return p[: -len(s)] + "log"
    s = _termina(p, _S1_UCION)
    if s and len(p) - len(s) >= r2:
        return p[: -len(s)] + "u"
    s = _termina(p, _S1_ENCIA)
    if s and len(p) - len(s) >= r2:
        return p[: -len(s)] + "ente"
    if p.endswith("amente") and len(p) - 6 >= r1:
        p = p[:-6]
        if p.endswith("iv") and len(p) - 2 >= r2:
            p = p[:-2]
            if p.endswith("at") and len(p) - 2 >= r2:
                p = p[:-2]
        else:
            for pre in ("os", "ic", "ad"):
                if p.endswith(pre) and len(p) - 2 >= r2:
                    p = p[:-2]
                    break
        return p
    if p.endswith("mente") and len(p) - 5 >= r2:
        p = p[:-5]
        for pre in ("ante", "able", "ible"):
            if p.endswith(pre) and len(p) - len(pre) >= r2:
                p = p[: -len(pre)]
                break
        return p
    s = _termina(p, _S1_IDAD)
    if s and len(p) - len(s) >= r2:
        p = p[: -len(s)]
        for pre in ("abil", "ic", "iv"):
            if p.endswith(pre) and len(p) - len(pre) >= r2:
                p = p[: -len(pre)]
                break
        return p
    s = _termina(p, _S1_IVO)
    if s and len(p) - len(s) >= r2:
        p = p[: -len(s)]
        if p.endswith("at") and len(p) - 2 >= r2:
            p = p[:-2]
        return p
    return None


@lru_cache(maxsize=65536)
def raiz(palabra: str) -> str:
    """Raíz de una palabra ya en minúsculas y sin tildes."""
    p = palabra
    if len(p) < 4 or not p.isalpha():
        return p
    rv, r1, r2 = _regiones(p)

    # Paso 0: pronombre enclítico («notificarle» → «notificar»).
    s = _termina(p, _PRONOMBRES)
    if s:
        base = p[: -len(s)]
        antes = _termina(base, _ANTES_DE_PRONOMBRE)
        if antes and len(base) - len(antes) >= rv:
            if antes != "yendo" or base[: -len(antes)].endswith("u"):
                p = base

    cambiada = _paso1(p, r1, r2)
    if cambiada is not None:
        p = cambiada
    else:
        # Paso 2a: verbos con y tras u («construyendo»).
        s = _termina(p, _S2A)
        if s and len(p) - len(s) >= rv and p[: -len(s)].endswith("u"):
            p = p[: -len(s)]
        else:
            # Paso 2b: el resto de terminaciones verbales.
            s = _termina(p, _S2B_GU)
            s2 = _termina(p, _S2B)
            if s and (not s2 or len(s) >= len(s2)) and len(p) - len(s) >= rv:
                p = p[: -len(s)]
                if p.endswith("gu"):
                    p = p[:-1]
            elif s2 and len(p) - len(s2) >= rv:
                p = p[: -len(s2)]

    # Paso 3: vocal residual.
    rv, _, _ = _regiones(p)
    s = _termina(p, _S3)
    if s and len(p) - len(s) >= rv:
        p = p[: -len(s)]
    elif p.endswith("e") and len(p) - 1 >= rv:
        p = p[:-1]
        if p.endswith("gu") and len(p) - 1 >= rv:
            p = p[:-1]
    return _unir_familias(p)


def _unir_familias(p: str) -> str:
    """Une el verbo con su sustantivo, que Snowball deja separados.

    Medido el 24-nov-2026: «¿cuánto puede VARIAR la superficie?» no encontraba «solo se admite
    una VARIACIÓN de hasta un cinco por ciento» porque Snowball da «vari» y «variacion» (la
    regla de R2 no deja quitar «-ación» en palabras cortas). Igual «notificar» → «notific» y
    «notificación» → «notif». No es lingüística fina: es que índice y pregunta coincidan, y
    los dos pasan por aquí."""
    for sufijo in ("acion", "icion", "ucion"):
        if p.endswith(sufijo) and len(p) - len(sufijo) >= 3:
            p = p[: -len(sufijo)]
            break
    if p.endswith("ic") and len(p) >= 6:
        p = p[:-2]
    return p


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

# Palabras, y números con su decimal («0.20», «5%», «10») — las cifras importan en una norma.
_RE_TOKEN = re.compile(r"[a-zñ]+|\d+(?:[.,]\d+)?")


def palabras(texto: str) -> list[str]:
    """Minúsculas, sin tildes, sin signos. Conserva las palabras vacías."""
    return _RE_TOKEN.findall(sin_tildes(texto.lower()))


def terminos(texto: str) -> list[str]:
    """Lo que se indexa y se busca: raíces de las palabras con contenido, y las cifras."""
    salida = []
    for p in palabras(texto):
        if p in VACIAS:
            continue
        if p[0].isdigit():
            salida.append(p.replace(",", "."))
        elif len(p) > 1:
            salida.append(raiz(p))
    return salida

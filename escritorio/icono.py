"""Dibuja el icono del Chatbot Mensura sin ninguna librería de imagen.

    python escritorio/icono.py      → escritorio/icono.ico  y  web/icono.png

Un bocadillo de chat blanco con tres puntos sobre un cuadrado redondeado en el azul de la
plataforma (#0071e3, el de `lexgeo.css`). Se dibuja a 4× y se promedia: bordes suaves sin PIL.
El .ico lleva las imágenes en PNG (Windows Vista en adelante lo admite), una por tamaño.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
AZUL = (0, 113, 227)
BLANCO = (255, 255, 255)


def _dentro_redondeado(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _en_triangulo(x, y, a, b, c):
    def lado(p, q, r):
        return (p[0] - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (p[1] - r[1])
    d1, d2, d3 = lado((x, y), a, b), lado((x, y), b, c), lado((x, y), c, a)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def color(u, v):
    """Color en coordenadas normalizadas 0..1. None = transparente."""
    if not _dentro_redondeado(u, v, 0.0, 0.0, 1.0, 1.0, 0.22):
        return None
    burbuja = (_dentro_redondeado(u, v, 0.17, 0.22, 0.83, 0.68, 0.14)
               or _en_triangulo(u, v, (0.30, 0.64), (0.47, 0.64), (0.26, 0.82)))
    if burbuja:
        for cx in (0.34, 0.50, 0.66):
            if (u - cx) ** 2 + (v - 0.45) ** 2 <= 0.055 ** 2:
                return AZUL
        return BLANCO
    return AZUL


def imagen(lado: int, sobre: int = 4) -> bytes:
    filas = []
    n = lado * sobre
    for y in range(lado):
        fila = bytearray([0])                       # filtro PNG «ninguno»
        for x in range(lado):
            r = g = b = a = 0
            for sy in range(sobre):
                for sx in range(sobre):
                    c = color((x * sobre + sx + 0.5) / n, (y * sobre + sy + 0.5) / n)
                    if c:
                        r += c[0]
                        g += c[1]
                        b += c[2]
                        a += 255
            total = sobre * sobre
            cubiertos = a // 255
            if cubiertos:
                fila += bytes((r // cubiertos, g // cubiertos, b // cubiertos, a // total))
            else:
                fila += b"\x00\x00\x00\x00"
        filas.append(bytes(fila))
    return _png(lado, lado, b"".join(filas))


def _png(ancho: int, alto: int, crudo: bytes) -> bytes:
    def trozo(tipo, datos):
        return (struct.pack(">I", len(datos)) + tipo + datos
                + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF))
    cabecera = struct.pack(">IIBBBBB", ancho, alto, 8, 6, 0, 0, 0)   # RGBA 8 bits
    return (b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", cabecera)
            + trozo(b"IDAT", zlib.compress(crudo, 9)) + trozo(b"IEND", b""))


def ico(pngs: dict[int, bytes]) -> bytes:
    tamanos = sorted(pngs)
    cabecera = struct.pack("<HHH", 0, 1, len(tamanos))
    indice, datos = b"", b""
    desplazamiento = 6 + 16 * len(tamanos)
    for t in tamanos:
        png = pngs[t]
        indice += struct.pack("<BBBBHHII", t % 256, t % 256, 0, 0, 1, 32, len(png),
                              desplazamiento + len(datos))
        datos += png
    return cabecera + indice + datos


def main() -> None:
    pngs = {t: imagen(t) for t in (16, 24, 32, 48, 64, 128, 256)}
    (RAIZ / "escritorio" / "icono.ico").write_bytes(ico(pngs))
    (RAIZ / "web" / "icono.png").write_bytes(pngs[64])
    print("icono.ico y web/icono.png listos")


if __name__ == "__main__":
    main()

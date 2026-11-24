"""El texto y el troceo: si el índice y la pregunta no reducen igual, la búsqueda falla callada."""

import json

from chatbot import rutas
from chatbot.corpus import (Articulo, RE_NUNCA_ES_ENCABEZADO, detectar_reformas,
                            partir_en_apartados, trocear, unir_paginas)
from chatbot.texto import raiz, sin_tildes, terminos


# ---------------------------------------------------------------- texto
def test_con_y_sin_tilde_dan_la_misma_raiz():
    for a, b in [("regularización", "regularizacion"), ("notificación", "notificacion"),
                 ("superficie", "superficies"), ("deslinde", "deslindes"),
                 ("colindante", "colindantes")]:
        assert terminos(a) == terminos(b), (a, b)


def test_la_enie_no_se_pierde():
    assert sin_tildes("año") == "año"
    assert raiz("año") != raiz("ano")


def test_las_cifras_se_conservan():
    assert "5" in terminos("hasta un 5%") and "0.20" in terminos("0,20 metros")


# ---------------------------------------------------------------- troceo
def _texto(*paginas):
    return unir_paginas(list(paginas))


def test_articulo_al_principio_de_pagina():
    """Fallo real: el salto de página (\\x0c) delante de «Artículo 7» lo hacía invisible."""
    t = _texto("Artículo 6. Sujetos. Texto del seis bastante largo para pasar el mínimo.\n",
               "Artículo 7. Profesional. Texto del siete bastante largo para pasar el mínimo.\n")
    assert [a.articulo for a in trocear("X", t)] == ["Art. 6", "Art. 7"]


def test_numero_en_la_linea_siguiente():
    """Fallo real: «Artículo\\n125.» en la RES-789-2022 se perdía."""
    t = _texto("Artículo 124. Coordenadas. Texto suficiente para ser un artículo de verdad.\n"
               "Artículo\n125.\nDocumentación complementaria. A la documentación prevista se agrega.\n")
    assert [a.articulo for a in trocear("X", t)] == ["Art. 124", "Art. 125"]


def test_remision_a_principio_de_linea_no_corta():
    t = _texto("Artículo 10. Uno. Texto suficientemente largo para ser un artículo real.\n"
               "Artículo 45 de la Ley 108-05, que se cita en medio de este artículo largo.\n"
               "Artículo 11. Dos. Texto suficientemente largo para ser un artículo real.\n")
    assert [a.articulo for a in trocear("X", t)] == ["Art. 10", "Art. 11"]


def test_principios_de_la_ley():
    t = _texto("Considerando que la ley es necesaria y tiene un preámbulo largo de verdad.\n"
               "PRINCIPIO I\nEl sistema de registro es el Torrens, con texto suficiente.\n"
               "PRINCIPIO II\nLa publicidad es el segundo principio, con texto suficiente.\n"
               "Art. 1.- Objeto de la ley. La presente ley regula el saneamiento y más.\n")
    assert [a.articulo for a in trocear("LEY", t)][1:] == ["Principio I", "Principio II", "Art. 1"]


def test_articulo_tabla_se_parte_por_apartados():
    cuerpo = "Artículo 23. Los requisitos para cada actuación son:\n" + "".join(
        f"{i}. {nombre}: descripción del trámite {'con mucho texto ' * 30}\n"
        for i, nombre in enumerate(["ACTUALIZACIÓN DE MENSURAS", "ANULACIÓN PARCELARIA",
                                    "DESLINDE", "REFUNDICIÓN"], start=1)) * 3
    a = Articulo("DT", "Art. 23", 0, "", "", 1, cuerpo)
    piezas = partir_en_apartados(a)
    assert [p.apartado for p in piezas if p.apartado][:4] == [
        "1. Actualización de mensuras", "2. Anulación parcelaria", "3. Deslinde", "4. Refundición"]


def test_un_articulo_nunca_es_encabezado_de_pagina():
    assert RE_NUNCA_ES_ENCABEZADO.match("artículo #.")
    assert not RE_NUNCA_ES_ENCABEZADO.match("suprema corte de justicia")


def test_detecta_reformas_en_el_texto():
    a = Articulo("RES-82-2025", "Art. 2", 0, "", "", 1,
                 "Artículo 2. Se agrega un párrafo al artículo 6 la Resolución núm. 790-2022, "
                 "de fecha 27 de octubre de 2022, para que en lo adelante se lea así.")
    r = detectar_reformas([a])
    assert r and r[0]["articulo"] == "Art. 6" and r[0]["norma"] == "790-2022"


# ------------------------------------------------------- corpus generado
def _unidades():
    return [json.loads(x) for x in rutas.ARTICULOS.read_text(encoding="utf-8").splitlines()]


def test_corpus_tiene_la_ley_madre_completa():
    arts = {(u["norma"], u["articulo"]) for u in _unidades()}
    assert ("LEY-108-05", "Principio I") in arts and ("LEY-108-05", "Principio X") in arts
    assert ("LEY-108-05", "Art. 1") in arts and ("LEY-108-05", "Art. 130") in arts


def test_corpus_no_pierde_los_articulos_que_se_perdian():
    arts = {(u["norma"], u["articulo"]) for u in _unidades()}
    for esperado in [("RES-789-2022", "Art. 125"), ("RES-82-2025", "Art. 2"),
                     ("DNMC-DT-2023-001", "Art. 7"), ("DNMC-DT-2023-001", "Art. 14")]:
        assert esperado in arts, esperado


def test_corpus_sin_encabezados_de_pagina():
    for u in _unidades():
        assert "del Pleno de la Suprema Corte de Justicia\n" not in u["texto"][40:], u["articulo"]


def test_apartados_de_tramites():
    aps = {u["apartado"] for u in _unidades() if u["norma"] == "DNMC-DT-2023-001"}
    assert "5. Deslinde" in aps and "14. Saneamiento" in aps


def test_reformas_de_la_790_y_ninguna_sobre_la_ley_consolidada():
    reformas = json.loads(rutas.NORMAS.read_text(encoding="utf-8"))["reformas"]
    de_790 = {r["articulo"] for r in reformas if r["norma"] == "RES-790-2022"}
    assert {"Art. 5", "Art. 6", "Art. 8", "Art. 9", "Art. 12"} <= de_790
    assert not [r for r in reformas if r["norma"] == "LEY-108-05"]

"""El actualizador decide solo qué es vigente: sus reglas salen de textos reales de ri.gob.do."""

from chatbot.actualizador import (clave, decidir_estado, derogaciones_en, es_de_la_materia,
                                  identificar)

ULTIMO = {"DNRT": 2025, "DNMC": 2023, "LEY": 2007, "RES": 2025}


def _enlace(grupo="disposiciones_tecnicas_dnrt", texto=""):
    return {"grupo": grupo, "texto": texto}


def test_identificadores_escritos_de_tres_maneras_son_la_misma_norma():
    assert clave("DNRT-DT-2023-001") == clave("DNRT-DT-2023-0001")
    assert clave("RES-8-2020") == clave("RES-008-2020")     # entró duplicada una vez
    # Así lo escribe de verdad la DNRT-DT-2025-003, Art. 35:
    assert "DNRT-2023-1" in derogaciones_en(
        "La presente disposición técnica deroga la resolución núm. DNRT-DT-2023001, de 28 de agosto")


def test_derogacion_de_resolucion():
    assert clave("RES-3642-2016") in derogaciones_en(
        "La presente resolución deroga la Resolución núm. 3642-2016, de fecha 15 de diciembre")


def test_serie_vieja_de_disposiciones():
    # DNMC-DT-2023-001, Art. 32, tal cual:
    assert clave("DT-002-2010") in derogaciones_en(
        "deroga cualquier otra disposición en cuanto le sea contraria, y específicamente la "
        "Resolución DNMC-DT-2021-001 … y 17. Disposición Técnica núm. 002-2010, de fecha 14")
    assert identificar("Disposicion_004-2008.pdf", "") == ("DT-004-2008", 2008)


def test_sin_la_palabra_deroga_no_hay_derogacion():
    assert not derogaciones_en("conforme a la resolución núm. DNRT-DT-2023001")


def test_identificar_por_nombre_de_archivo():
    assert identificar("FO-DNMC-003_Formulario_Acta_Hito_pag_1_V.0.pdf", "")[0] == "FO-DNMC-003"
    assert identificar("Ley_5038-1958.pdf", "") == ("LEY-5038-1958", 1958)
    assert identificar("DNMC-DT-2026-xxxxx-Recepción y Entrega.pdf", "")[0] == "DNMC-DT-2026-xxxx"


def test_borrador_ley_formulario():
    assert decidir_estado("DNMC-DT-2026-xxxx", 2026, "DNMC-DT-2026-xxxxx-REV.pdf",
                          _enlace("consulta_publica"), ULTIMO)[0] == "borrador"
    assert decidir_estado("LEY-5038-1958", 1958, "Ley_5038-1958.pdf",
                          _enlace("leyes"), ULTIMO)[0] == "vigente"
    assert decidir_estado("FO-DNMC-003", 0, "FO-DNMC-003.pdf", _enlace(), ULTIMO)[0] == "por_confirmar"


def test_antigua_de_otra_materia_queda_por_confirmar():
    """La DNRT-DT-2023-003 (Publicidad Registral) no la derogó nadie: no se da por muerta."""
    estado, _, _ = decidir_estado("DNRT-DT-2023-003", 2023, "x.pdf", _enlace(), ULTIMO, {})
    assert estado == "por_confirmar"


def test_cadena_de_derogaciones_en_la_misma_vuelta():
    textos = {"DNRT-DT-2023-002": "Artículo 60. Derogaciones. Se deroga la resolución "
                                  "DNRT-DT-2021-0002 de Recepción y Entrega."}
    estado, por, _ = decidir_estado("DNRT-DT-2021-0002", 2021, "x.pdf", _enlace(), ULTIMO, textos)
    assert (estado, por) == ("derogada", "DNRT-DT-2023-002")


def test_derogada_por_el_corpus_actual():
    """La DNRT-DT-2025-002 (en el corpus) deroga expresamente la 2023-002."""
    estado, por, _ = decidir_estado("DNRT-DT-2023-002", 2023, "x.pdf", _enlace(), ULTIMO, {})
    assert (estado, por) == ("derogada", "DNRT-DT-2025-002")


def test_materia():
    assert es_de_la_materia("trabajos de mensura catastral sobre el inmueble, deslinde de la parcela")
    assert not es_de_la_materia("ley de contrataciones públicas de bienes y servicios")

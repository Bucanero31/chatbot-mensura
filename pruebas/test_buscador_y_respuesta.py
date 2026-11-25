"""Buscar, responder y verificar: lo que hace que la respuesta sea defendible."""

from chatbot.memoria import Memoria
from chatbot.responder import (Fuente, preparar_fuentes, respuesta_rapida, segmentos,
                               verificar)


def _refs(res):
    return [(r.unidad.norma, r.unidad.articulo) for r in res]


# ---------------------------------------------------------------- buscador
def test_articulo_nombrado_va_primero(buscador):
    _, res = buscador.buscar("¿qué dice el artículo 13 de la 790?")
    assert _refs(res)[0] == ("RES-790-2022", "Art. 13")


def test_norma_por_su_nombre_comun(buscador):
    c = buscador.entender("según el reglamento de mensuras, ¿qué es un hito?")
    assert c.normas_nombradas == ["RES-789-2022"]


def test_principio_de_la_ley(buscador):
    _, res = buscador.buscar("¿qué dice el principio IV de la ley 108-05?")
    assert _refs(res)[0] == ("LEY-108-05", "Principio IV")


def test_derogadas_fuera_salvo_que_se_pregunte(buscador):
    _, res = buscador.buscar("reglamento de regularización parcelaria y deslinde")
    assert all(r.estado != "derogada" for r in res)


def test_norma_derogada_trae_quien_la_deroga(buscador):
    c, res = buscador.buscar("¿sigue vigente la Resolución 3642-2016?")
    assert c.historica
    assert ("RES-790-2022", "Art. 21") in _refs(res)[:3]


def test_articulo_reformado_viaja_con_su_reforma(buscador):
    _, res = buscador.buscar("artículo 5 de la resolución 790-2022 regularización parcelaria")
    r = next(r for r in res if (r.unidad.norma, r.unidad.articulo) == ("RES-790-2022", "Art. 5"))
    assert [(x.norma, x.articulo) for x in r.reformado_por] == [("RES-82-2025", "Art. 1")]


def test_seguimiento_corto_hereda_el_tema(buscador):
    c, _ = buscador.buscar("¿y en saneamiento?", historial=["plazo del aviso de mensura"])
    assert "saneamient" in c.terminos


def test_tramite_por_apartado(buscador):
    _, res = buscador.buscar("¿qué documentos lleva un expediente de deslinde?", k=8)
    assert any(r.unidad.apartado == "5. Deslinde" for r in res)


def test_pregunta_general_trae_la_regla_general(buscador):
    """La pregunta de Henry del 25-nov-2026. Sin trámite nombrado, la regla general de los
    10 días (Res. 789 Art. 78) tiene que llegar al modelo (4 fragmentos), y el plazo de las
    superposiciones (Art. 172, 20 días hábiles), que el modelo contestó por error, no."""
    _, res = buscador.buscar("Que tiempo tengo para realizar el levantamiento luego de notificar",
                             k=6)
    refs = [(r.unidad.norma, r.unidad.articulo) for r in res[:4]]
    assert ("RES-789-2022", "Art. 78") in refs
    assert ("RES-789-2022", "Art. 172") not in refs
    assert ("RES-3645-2016", "Art. 9") not in refs


def test_el_tramite_nombrado_no_se_penaliza(buscador):
    _, res = buscador.buscar("¿con cuántos días se publica la mensura para saneamiento?", k=4)
    assert ("RES-789-2022", "Art. 142") in [(r.unidad.norma, r.unidad.articulo) for r in res]


# ---------------------------------------------------------------- respuesta
def test_ficha_de_vigencia_primero(buscador):
    c, res = buscador.buscar("¿está vigente la 355-2009?")
    fuentes = preparar_fuentes(c, res, buscador)
    assert fuentes[0].articulo == "Vigencia" and fuentes[0].estado == "derogada"


def test_reformado_lleva_aviso_y_la_reforma_detras(buscador):
    c, res = buscador.buscar("artículo 5 de la resolución 790-2022 regularización parcelaria")
    fuentes = preparar_fuentes(c, res, buscador)
    i = next(i for i, f in enumerate(fuentes) if (f.norma, f.articulo) == ("RES-790-2022", "Art. 5"))
    assert fuentes[i].aviso.startswith("Texto anterior")
    assert (fuentes[i + 1].norma, fuentes[i + 1].articulo) == ("RES-82-2025", "Art. 1")


def test_rapida_dice_que_no_encuentra_lo_ajeno(buscador):
    c, res = buscador.buscar("¿qué receta lleva el sancocho de siete carnes?")
    r = respuesta_rapida(c, res, preparar_fuentes(c, res, buscador), buscador)
    assert r["confianza"] == "baja"


def test_rapida_es_literal(buscador):
    c, res = buscador.buscar("¿cuánto puede variar la superficie en un deslinde?")
    fuentes = preparar_fuentes(c, res, buscador)
    r = respuesta_rapida(c, res, fuentes, buscador)
    assert "[1]" in r["texto"] and "«" in r["texto"]


def test_pregunta_de_lista_recibe_el_apartado_entero(buscador):
    from chatbot.responder import mensajes_para_modelo, pide_lista
    p = "¿Qué documentos lleva un expediente de deslinde?"
    assert pide_lista(p) and not pide_lista("¿cuánto puede variar la superficie?")
    c, res = buscador.buscar(p)
    fuentes = preparar_fuentes(c, res, buscador)
    usuario = mensajes_para_modelo(p, fuentes)[1]["content"]
    # El primer fragmento va mucho más largo que los 1.800 caracteres normales.
    primero = usuario.split("\n\n[2]")[0]
    assert len(primero) > 2500 or len(fuentes[0].texto_completo) < 2500


def test_segmentos_parten_literales():
    s = segmentos("Debe contener: a) el plano; b) el acta. Párrafo I. Otra cosa importante aquí.")
    assert len(s) >= 2


# --------------------------------------------------------------- verificar
def _f(n, texto, articulo="Art. 13"):
    return Fuente(n=n, norma="RES-790-2022", norma_titulo="", articulo=articulo, apartado="",
                  titulo="", seccion="", estado="vigente", aviso="", extracto=texto, url="",
                  pagina=1, contexto=texto)


def test_verificar_acepta_lo_correcto():
    f = [_f(1, "solo se admite una variación de hasta un cinco por ciento (5%)")]
    assert verificar("Se admite hasta un 5 % de variación [1].", f)["avisos"] == []


def test_verificar_caza_cifra_inventada():
    f = [_f(1, "solo se admite una variación de hasta un cinco por ciento (5%)")]
    v = verificar("Se admite hasta un 10 % [1].", f)
    assert any("10" in a for a in v["avisos"])


def test_verificar_caza_cita_inexistente_y_articulo_ajeno():
    f = [_f(1, "texto")]
    v = verificar("Lo dice el artículo 99 [1][4].", f)
    assert any("[4]" in a for a in v["avisos"]) and any("99" in a for a in v["avisos"])


def test_verificar_sin_citas():
    assert verificar("Se admite un margen.", [_f(1, "texto")])["avisos"]


def test_verificar_no_encuentro_no_es_aviso():
    assert verificar("No encuentro esto en la normativa cargada.", [_f(1, "x")])["avisos"] == []


# ----------------------------------------------------------------- memoria
def test_aprende_de_lo_bien_valorado(tmp_path):
    m = Memoria(tmp_path)
    m.anotar("a1", "¿plazo del aviso a los colindantes?", [("DNMC-DT-2023-009", "Art. 9", "")],
             "respuesta", [], "modelo", "v1")
    m.valorar("a1", True)
    s = m.sugerencias(["plaz", "avis", "colind"])
    assert s[("DNMC-DT-2023-009", "Art. 9", "")] > 0.5
    # Y lo recuerda al reabrir.
    assert Memoria(tmp_path).sugerencias(["plaz", "avis", "colind"])


def test_lo_mal_valorado_resta_y_queda_pendiente(tmp_path):
    m = Memoria(tmp_path)
    m.anotar("b1", "¿plazo del aviso?", [("X", "Art. 1", "")], "r", [], "modelo", "v1")
    m.valorar("b1", False, "no era eso")
    assert m.sugerencias(["plaz", "avis"])[("X", "Art. 1", "")] < 0
    assert m.pendientes()[0]["comentario"] == "no era eso"


def test_cache_solo_con_el_mismo_corpus(tmp_path):
    m = Memoria(tmp_path)
    m.anotar("c1", "¿Qué es un deslinde?", [], "Es…", [], "modelo", "v1")
    assert m.en_cache("que es un deslinde", "v1")
    assert m.en_cache("¿Qué es un deslinde?", "v2") is None

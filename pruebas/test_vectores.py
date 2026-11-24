"""La búsqueda por significado es opcional: sin vectores (o desactualizados) no rompe nada."""

import math

from chatbot.vectores import DIMENSIONES, Vectores, _normalizar, huella, texto_de_pregunta


def test_normalizar_recorta_y_deja_norma_uno():
    v = _normalizar([3.0, 4.0] + [0.0] * 1000)
    assert len(v) == DIMENSIONES and math.isclose(sum(x * x for x in v), 1.0)


def test_huella_estable():
    assert huella("abc") == huella("abc") != huella("abd")


def test_formato_de_pregunta_de_embeddinggemma():
    assert texto_de_pregunta("¿plazo?").startswith("task: search result | query: ")


class _MotorRoto:
    modelo_emb = None

    def vectores(self, textos):
        raise RuntimeError("sin modelo")


def test_sin_modelo_la_busqueda_sigue_por_palabras(buscador, tmp_path, monkeypatch):
    import chatbot.vectores as mod
    monkeypatch.setattr(mod, "BIN", tmp_path / "no.bin")
    monkeypatch.setattr(mod, "META", tmp_path / "no.json")
    v = Vectores(_MotorRoto(), buscador)
    assert not v.listos and v.parecidos("deslinde") == {}
    viejo = buscador.vectores
    try:
        buscador.vectores = v
        _, res = buscador.buscar("¿qué dice el artículo 13 de la 790?")
        assert res and res[0].unidad.articulo == "Art. 13"
    finally:
        buscador.vectores = viejo

"""La API, de punta a punta, SIN modelo: el chatbot tiene que responder igual (modo citas)."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest


class _SinModelo:
    nombre = ""
    modelo_emb = None
    dispositivo = type("D", (), {"descripcion": "CPU"})()
    chat = type("S", (), {"vivo": lambda self: False})()

    def disponible(self):
        return False

    def en_cola(self):
        return 0

    def recursos(self):
        return {}

    def parar(self):
        pass


@pytest.fixture(scope="module")
def url(tmp_path_factory):
    import chatbot.rutas as rutas
    import chatbot.servidor as srv
    rutas.DATOS = tmp_path_factory.mktemp("datos")
    srv.motor = lambda: _SinModelo()
    app = srv.Aplicacion()
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), srv.manejador(app))
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read())


def _post(url, cuerpo):
    req = urllib.request.Request(url, data=json.dumps(cuerpo).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_preguntar_sin_modelo_da_la_rapida(url):
    codigo, r = _post(url + "/api/preguntar", {"pregunta": "¿qué dice el principio IV de la ley 108-05?"})
    assert codigo == 200 and r["redaccion"] == "no_disponible"
    assert r["fuentes"][0]["articulo"] == "Principio IV"
    assert "[1]" in r["rapida"]["texto"]


def test_pregunta_ajena_no_gasta_modelo(url):
    _, r = _post(url + "/api/preguntar", {"pregunta": "¿qué receptor GNSS compro, Trimble o Topcon?"})
    assert r["rapida"]["confianza"] == "baja" and r["redaccion"] == "innecesaria"


def test_pregunta_vacia(url):
    assert _post(url + "/api/preguntar", {"pregunta": "  "})[0] == 400


def test_articulo_con_vecinos_y_enlace_a_la_pagina(url):
    _, a = _get(url + "/api/articulo?norma=RES-790-2022&articulo=Art.%2013")
    assert a["titulo"] and a["anterior"]["articulo"] == "Art. 12" and "#page=" in a["url"]


def test_articulo_reformado_lo_avisa(url):
    _, a = _get(url + "/api/articulo?norma=RES-790-2022&articulo=Art.%205")
    assert a["aviso"].startswith("Texto anterior")


def test_valorar_y_salud(url):
    _, r = _post(url + "/api/preguntar", {"pregunta": "¿plazo del aviso de mensura a colindantes?"})
    assert _post(url + "/api/valorar", {"id": r["id"], "util": True})[1]["ok"]
    _, s = _get(url + "/api/salud")
    assert s["corpus"]["unidades"] > 2000 and s["memoria"]["utiles"] >= 1


def test_no_sirve_archivos_fuera_de_web(url):
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(url + "/../chatbot/servidor.py", timeout=5)
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(url + "/%2e%2e/config.ejemplo.json", timeout=5)


def test_la_pantalla_se_sirve(url):
    with urllib.request.urlopen(url + "/", timeout=5) as r:
        assert b"Chatbot Mensura" in r.read()

"""Los hallazgos de la revisión independiente del 24-nov-2026, cada uno con su prueba.

Cada prueba reproduce el caso CONCRETO con que el revisor demostró el fallo: si alguien
deshace la corrección, vuelve a fallar aquí.
"""

import http.client
import json
import socket
import threading
from http.server import ThreadingHTTPServer

import pytest

from chatbot.actualizador import clave, derogaciones_en
from chatbot.memoria import Memoria


# ---------------------------------------------- 1. derogaciones parciales y negadas
def test_derogar_un_parrafo_no_deroga_la_norma():
    assert not derogaciones_en("Se deroga el párrafo II del artículo 54 de la Resolución "
                               "núm. 787-2022, de fecha 27 de octubre de 2022.")


def test_no_deroga_no_es_derogacion():
    assert not derogaciones_en("Esta disposición no deroga la Resolución núm. 789-2022.")


def test_la_norma_de_otra_frase_no_cuenta():
    assert not derogaciones_en("Quedan derogadas todas las disposiciones contrarias. La "
                               "Resolución núm. 790-2022 mantiene su vigencia.")


def test_derogacion_total_si_cuenta():
    assert clave("RES-3642-2016") in derogaciones_en(
        "La presente resolución deroga la Resolución núm. 3642-2016, de fecha 15 de diciembre")


def test_lista_numerada_no_corta_la_frase():
    # DNMC-DT-2023-001, Art. 32: «… y 17. Disposición Técnica núm. 002-2010 …»
    assert clave("DT-002-2010") in derogaciones_en(
        "deroga cualquier otra disposición, y específicamente la Resolución DNMC-DT-2021-001, "
        "de 2021, y 17. Disposición Técnica núm. 002-2010, de fecha 14 de septiembre")


def test_lo_que_decidio_una_persona_no_lo_tumba_lo_automatico(tmp_path, monkeypatch):
    import chatbot.catalogo as cat
    import chatbot.rutas as rutas
    monkeypatch.setattr(rutas, "CORPUS", tmp_path)
    monkeypatch.setattr(rutas, "catalogo", lambda: tmp_path / "catalogo.yaml")
    (tmp_path / "catalogo_revisor.yaml").write_text(
        "resoluciones:\n  - id: RES-790-2022\n    titulo: Reglamento\n    estado: vigente\n",
        encoding="utf-8")
    (tmp_path / "catalogo_auto.json").write_text(json.dumps({"normas": [], "derogaciones": [
        {"norma": "RES-790-2022", "por": "RES-1-2027", "fecha": "2027-01-01"}]}), encoding="utf-8")
    avisos = cat.fundir()
    texto = (tmp_path / "catalogo.yaml").read_text(encoding="utf-8")
    assert "estado: vigente" in texto and "aviso_derogacion" in texto
    assert any("posible derogación" in a for a in avisos)


# ------------------------------------------------------------- servidor (3, 4, 11, 15)
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
def puerto(tmp_path_factory):
    import chatbot.rutas as rutas
    import chatbot.servidor as srv
    rutas.DATOS = tmp_path_factory.mktemp("datos")
    srv.motor = lambda: _SinModelo()
    app = srv.Aplicacion()
    s = srv.ServidorExclusivo(("127.0.0.1", 0), srv.manejador(app))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s.server_address[1]
    s.shutdown()


def _crudo(puerto, peticion: bytes) -> str:
    with socket.create_connection(("127.0.0.1", puerto), timeout=10) as c:
        c.sendall(peticion)
        return c.recv(4096).decode("latin-1")


def test_ruta_unc_no_toca_el_disco(puerto):
    respuesta = _crudo(puerto, b"GET /\\\\10.9.9.9\\c\\x.js HTTP/1.1\r\nHost: x\r\n\r\n")
    assert " 404 " in respuesta.split("\r\n")[0]


def test_content_length_negativo(puerto):
    respuesta = _crudo(puerto, b"POST /api/preguntar HTTP/1.1\r\nHost: x\r\n"
                               b"Content-Type: application/json\r\nContent-Length: -1\r\n\r\n")
    assert " 413 " in respuesta.split("\r\n")[0]


def test_solo_json(puerto):
    c = http.client.HTTPConnection("127.0.0.1", puerto, timeout=10)
    c.request("POST", "/api/preguntar", body=json.dumps({"pregunta": "deslinde"}),
              headers={"Content-Type": "text/plain"})
    assert c.getresponse().status == 415


def test_ping(puerto):
    c = http.client.HTTPConnection("127.0.0.1", puerto, timeout=10)
    c.request("GET", "/api/ping")
    assert b"mensura" in c.getresponse().read()


def test_puerto_en_exclusiva(puerto):
    import chatbot.servidor as srv
    with pytest.raises(OSError):
        srv.ServidorExclusivo(("127.0.0.1", puerto), None)


# ---------------------------------------------------------------- memoria (7 y 14)
def test_lo_redactado_con_historial_no_va_a_la_cache(tmp_path):
    m = Memoria(tmp_path)
    m.anotar("a", "¿cuáles son los plazos?", [], "Los del deslinde…", [], "gemma", "v1",
             con_historial=True)
    assert m.en_cache("¿cuáles son los plazos?", "v1") is None


def test_la_cache_lee_la_respuesta_del_disco(tmp_path):
    m = Memoria(tmp_path)
    m.anotar("b", "¿qué es un deslinde?", [], "Es el proceso…", [], "gemma", "v1",
             fuentes=[{"n": 1}])
    assert "respuesta" not in m.entradas["b"]          # en RAM solo lo ligero
    previa = Memoria(tmp_path).en_cache("que es un deslinde", "v1")
    assert previa["respuesta"] == "Es el proceso…" and previa["fuentes"] == [{"n": 1}]


# --------------------------------------------------------------- vectores dañados (9)
def test_vectores_danados_no_impiden_arrancar(buscador, tmp_path, monkeypatch):
    import chatbot.vectores as mod
    bin_, meta = tmp_path / "v.bin", tmp_path / "v.json"
    monkeypatch.setattr(mod, "BIN", bin_)
    monkeypatch.setattr(mod, "META", meta)
    meta.write_text("{ esto no es json", encoding="utf-8")
    bin_.write_bytes(b"\x00" * 8)
    assert not mod.Vectores(None, buscador).listos
    meta.write_text(json.dumps({"dimensiones": 256, "huellas": ["a", "b"]}), encoding="utf-8")
    bin_.write_bytes(b"\x00" * 4 * 256)                 # la mitad de lo que debería
    assert not mod.Vectores(None, buscador).listos

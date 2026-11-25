"""El servidor del chatbot: la pantalla y la API, sin dependencias.

    py -m chatbot.servidor                 puerto 8013, toda la red local
    py -m chatbot.servidor --puerto 8020

`http.server` con un hilo por petición basta para una oficina de 35 PCs: buscar son
milisegundos y la redacción, que es lo lento, va en su propio hilo con cola (una a la vez en
el modelo local). Contrato de la API: `docs/API.md`.
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import secrets
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from chatbot import rutas
from chatbot.buscador import Buscador
from chatbot.memoria import Memoria
from chatbot.motor_local import motor
from chatbot.responder import (ficha_de_vigencia, limpiar, mensajes_para_modelo, pide_lista,
                               preparar_fuentes, respuesta_rapida, verificar)

log = logging.getLogger("chatbot")

CADUCIDAD = 30 * 60
MAXIMO_PREGUNTA = 1200

SUGERENCIAS = [
    "¿Cuánto puede variar la superficie en un deslinde?",
    "¿Qué documentos lleva un expediente de deslinde?",
    "¿Con cuántos días de anticipación se avisa a los colindantes?",
    "¿Cuándo no aplica la regularización parcelaria?",
    "¿Qué dice el Principio IV de la Ley 108-05?",
    "¿Sigue vigente la Resolución 355-2009?",
]


MAXIMO_EN_COLA = 20            # más preguntas esperando que esto → solo la respuesta rápida
ABANDONADA_TRAS = 20           # s sin que la pantalla pregunte por ella → se deja de redactar


class Trabajo:
    def __init__(self, ident: str):
        self.id = ident
        self.creado = time.time()
        self.estado = "en_cola"
        self.trozos: list[str] = []
        self.final: dict | None = None
        self.cerrojo = threading.Lock()
        self.ultimo_sondeo = time.time()

    def abandonado(self) -> bool:
        """La pantalla sondea cada 0,3-1 s: si deja de hacerlo, se cerró la ventana o se
        empezó otra conversación. Hallazgo 5 de la revisión: el modelo seguía redactando para
        nadie y la cola se llenaba de preguntas abandonadas."""
        return time.time() - self.ultimo_sondeo > ABANDONADA_TRAS

    def escribir(self, trozo: str) -> None:
        with self.cerrojo:
            self.estado = "escribiendo"
            self.trozos.append(trozo)

    def texto(self) -> str:
        with self.cerrojo:
            return "".join(self.trozos)


class Aplicacion:
    def __init__(self):
        self.memoria = Memoria()
        self.buscador = Buscador(memoria=self.memoria)
        self.motor = motor()
        self.vectores = None
        if self.motor.modelo_emb:
            try:
                from chatbot.vectores import Vectores
                self.vectores = Vectores(self.motor, self.buscador)
                self.buscador.vectores = self.vectores
            except Exception as e:  # noqa: BLE001 — los vectores son opcionales
                log.warning("búsqueda por significado desactivada: %s", e)
        self.trabajos: dict[str, Trabajo] = {}
        self.fila: list[str] = []               # ids esperando el modelo, en orden
        self.cerrojo = threading.Lock()
        self.version = self._version()
        self.ultima_actividad = time.time()

    def _anotar(self, *args, **kwargs) -> None:
        """Aprender es opcional: un disco lleno no puede dejar sin respuesta (hallazgo 10)."""
        try:
            self.memoria.anotar(*args, **kwargs)
        except OSError as e:
            log.warning("no pude guardar en la memoria (¿disco lleno?): %s", e)

    def _version(self) -> str:
        """Huella de lo que decide una respuesta: corpus, vocabulario y versión del buscador.

        Antes era la FECHA del corpus: el 24-nov-2026 se arregló la búsqueda y se reconstruyó el
        corpus el mismo día, y la caché siguió sirviendo «No encuentro» a la pregunta que se
        acababa de arreglar. Cualquier cambio en estas tres cosas invalida la caché.

        Y el CÓDIGO que busca y redacta, por su contenido: el 25-nov-2026 se cambiaron las
        instrucciones al modelo sin subir VERSION_BUSQUEDA y la caché repitió la respuesta mala.
        Depender de acordarse de subir un número ya falló una vez."""
        import hashlib
        from chatbot.buscador import VERSION_BUSQUEDA
        h = hashlib.sha1(VERSION_BUSQUEDA.encode())
        for nombre in ("articulos.jsonl", "normas.json", "vocabulario.json"):
            ruta = rutas.GENERADO / nombre
            if ruta.exists():
                h.update(f"{nombre}:{ruta.stat().st_size}:{ruta.stat().st_mtime_ns}".encode())
        codigo = Path(__file__).resolve().parent
        for nombre in ("buscador.py", "responder.py", "texto.py"):
            try:
                h.update((codigo / nombre).read_bytes())
            except OSError:
                pass
        return h.hexdigest()[:12]

    def recargar(self) -> None:
        """Tras una actualización del corpus: el buscador nuevo entra sin parar el servidor."""
        self.buscador.cargar()
        if self.vectores is not None:
            self.vectores.cargar()
        self.version = self._version()

    # --------------------------------------------------------------- preguntar
    def preguntar(self, datos: dict) -> tuple[int, dict]:
        pregunta = " ".join(str(datos.get("pregunta", "")).split())[:MAXIMO_PREGUNTA]
        if not pregunta:
            return 400, {"error": "La pregunta está vacía."}
        # El historial entra recortado: una pregunta anterior de 7.000 caracteres desbordaba el
        # contexto del modelo (hallazgo 6) y llama-server respondía 400.
        historial = [{"pregunta": " ".join(str(h.get("pregunta", "")).split())[:300]}
                     for h in (datos.get("historial") or []) if isinstance(h, dict)][-3:]
        historial = [h for h in historial if h["pregunta"]]
        anteriores = [h["pregunta"] for h in historial]

        c, resultados = self.buscador.buscar(pregunta, k=6, historial=anteriores)
        fuentes = preparar_fuentes(c, resultados, self.buscador)
        rapida = respuesta_rapida(c, resultados, fuentes, self.buscador)
        ident = secrets.token_hex(6)
        respuesta = {
            "id": ident,
            "entendido": {"normas": c.normas_nombradas, "articulos": c.articulos_nombrados,
                          "terminos": c.terminos, "historica": c.historica},
            "fuentes": [f.publica() for f in fuentes],
            "rapida": rapida, "redaccion": "no_disponible", "cache": None,
        }

        previa = None if historial else self.memoria.en_cache(pregunta, self.version)
        if previa:
            respuesta["redaccion"] = "desde_cache"
            respuesta["cache"] = {"texto": previa["respuesta"], "avisos": previa["avisos"]}
            if previa.get("fuentes"):
                respuesta["fuentes"] = previa["fuentes"]
            # Una entrada ligera, sin repetir la respuesta: solo para poder valorarla.
            self._anotar(ident, pregunta, [tuple(x) for x in previa["citas"]], "", [], "",
                         self.version, implicito=False, de_cache=previa["id"])
            return 200, respuesta

        if rapida["confianza"] == "baja":
            # Pregunta ajena a la normativa: un modelo pequeño contestaría igual (medido: el
            # LFM2 no dijo «no lo encuentro» ni una vez de tres). Lo decide el programa.
            respuesta["redaccion"] = "innecesaria"
        with self.cerrojo:
            llena = len(self.fila) >= MAXIMO_EN_COLA
        if not self.motor.disponible() or not fuentes or rapida["confianza"] == "baja" or llena:
            self._anotar(ident, pregunta, [(f.norma, f.articulo, f.apartado) for f in fuentes[:3]],
                         rapida["texto"], [], "", self.version, implicito=False,
                         fuentes=respuesta["fuentes"])
            return 200, respuesta

        trabajo = Trabajo(ident)
        with self.cerrojo:
            self._limpiar_viejos()
            self.trabajos[ident] = trabajo
            self.fila.append(ident)
        respuesta["redaccion"] = "en_cola"
        threading.Thread(target=self._redactar, args=(trabajo, pregunta, fuentes, historial),
                         daemon=True, name=f"redactar-{ident}").start()
        return 200, respuesta

    def _redactar(self, t: Trabajo, pregunta: str, fuentes, historial) -> None:
        inicio = time.time()

        def empezar(trozo: str) -> None:
            if t.estado == "en_cola":
                with self.cerrojo:
                    if t.id in self.fila:
                        self.fila.remove(t.id)
            t.escribir(trozo)

        try:
            bruto = self.motor.redactar(mensajes_para_modelo(pregunta, fuentes, historial),
                                        al_escribir=empezar, cancelado=t.abandonado,
                                        max_tokens=800 if pide_lista(pregunta) else None)
            if t.abandonado():
                # Cortada a medias porque nadie la seguía: no es una respuesta. Como «listo»
                # salía vacía y con el aviso «no cita ninguna fuente» a quien volviera a la
                # pestaña; así la pantalla se queda con las citas literales.
                with t.cerrojo:
                    t.estado = "cancelada"
                return
            texto = limpiar(bruto)
            v = verificar(texto, fuentes)
            citadas = [f for f in fuentes if f.n in v["citas"]]
            t.final = {"texto": texto, "avisos": v["avisos"], "citas": v["citas"],
                       "modelo": self.motor.nombre, "segundos": round(time.time() - inicio, 1)}
            with t.cerrojo:
                t.estado = "listo"
        except Exception as e:  # noqa: BLE001 — cualquier fallo deja la respuesta rápida
            log.warning("redacción fallida (%s): %s", t.id, e)
            with t.cerrojo:
                t.estado = "error"
                t.final = {"texto": "", "avisos": [], "citas": [], "modelo": self.motor.nombre,
                           "segundos": round(time.time() - inicio, 1), "error": str(e)[:200]}
            return
        finally:
            with self.cerrojo:
                if t.id in self.fila:
                    self.fila.remove(t.id)
        if t.abandonado() or not texto:
            return                       # nadie la leyó entera: no se aprende de ella
        # Fuera del try: si guardar falla, la respuesta ya está «lista» y se entrega igual
        # (hallazgo 10: antes un disco lleno la convertía en «error» y se perdía).
        # Una respuesta que dependió de la conversación anterior no va a la caché (hallazgo 7).
        self._anotar(t.id, pregunta,
                     [(f.norma, f.articulo, f.apartado) for f in citadas if f.articulo != "Vigencia"],
                     texto, v["avisos"], self.motor.nombre, self.version,
                     fuentes=[f.publica() for f in fuentes], con_historial=bool(historial))

    def _limpiar_viejos(self) -> None:
        ahora = time.time()
        for k in [k for k, t in self.trabajos.items() if ahora - t.creado > CADUCIDAD]:
            del self.trabajos[k]

    def respuesta(self, ident: str, desde: int) -> tuple[int, dict]:
        t = self.trabajos.get(ident)
        if not t:
            return 404, {"error": "Esa respuesta ya no está disponible."}
        t.ultimo_sondeo = time.time()
        texto = t.texto()
        with self.cerrojo:
            # Cuántas van DELANTE de esta (antes se daba el total de la cola: hallazgo 5).
            posicion = self.fila.index(ident) if ident in self.fila else 0
        cuerpo = {"estado": t.estado, "posicion": posicion if t.estado == "en_cola" else 0,
                  "texto": texto[max(0, desde):], "n": len(texto), "final": None}
        if t.estado in ("listo", "error"):
            cuerpo["final"] = t.final
        return 200, cuerpo

    # -------------------------------------------------------------- consultas
    def articulo(self, q: dict) -> tuple[int, dict]:
        norma, art = q.get("norma", ""), q.get("articulo", "")
        apartado = q.get("apartado", "")
        if art == "Vigencia":
            f = ficha_de_vigencia(1, norma, self.buscador)
            return 200, {"norma": norma, "norma_titulo": f.norma_titulo, "articulo": "Vigencia",
                         "apartado": "", "titulo": f.titulo, "seccion": "", "estado": f.estado,
                         "aviso": "", "texto": f.contexto, "url": f.url, "pagina": 0,
                         "anterior": None, "siguiente": None}
        u = self.buscador.articulo(norma, art, apartado)
        if not u:
            return 404, {"error": "No encuentro ese artículo."}
        n = self.buscador.normas.get(u.norma, {})
        de_la_norma = [x for x in self.buscador.unidades if x.norma == u.norma]
        pos = next(i for i, x in enumerate(de_la_norma) if x.id == u.id)

        def vecino(i):
            if 0 <= i < len(de_la_norma):
                return {"articulo": de_la_norma[i].articulo, "apartado": de_la_norma[i].apartado}
            return None

        reformado = self.buscador.reformas.get((u.norma, u.articulo))
        aviso = ""
        if reformado:
            aviso = "Texto anterior: lo reformó " + "; ".join(
                f"{x.norma}, {x.articulo}" for x in reformado) + "."
        elif reformado is not None:
            aviso = "Este artículo fue reformado; el PDF trae el texto anterior."
        url = n.get("url", "")
        if url.lower().split("?")[0].endswith(".pdf") and u.pagina:
            url = f"{url}#page={u.pagina}"
        return 200, {"norma": u.norma, "norma_titulo": n.get("titulo", ""), "articulo": u.articulo,
                     "apartado": u.apartado, "titulo": u.titulo, "seccion": u.seccion,
                     "estado": n.get("estado", ""), "aviso": aviso, "texto": u.texto, "url": url,
                     "pagina": u.pagina, "anterior": vecino(pos - 1), "siguiente": vecino(pos + 1)}

    def salud(self) -> dict:
        normas = self.buscador.normas.values()
        novedades = leer_novedades()
        try:
            estado_act = json.loads((rutas.DATOS / "actualizador.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            estado_act = {}
        ultima = estado_act.get("ultima") or (novedades[0]["fecha"] if novedades else "")
        proxima = (time.strftime("%Y-%m-%d", time.localtime(estado_act["ultima_ts"] + 7 * 86400))
                   if estado_act.get("ultima_ts") else "")
        return {
            "estado": "ok",
            "corpus": {"normas": sum(1 for n in normas if n.get("indexada")),
                       "vigentes": sum(1 for n in normas if n.get("estado") == "vigente"
                                       and n.get("indexada")),
                       "unidades": len(self.buscador.unidades), "version": self.version},
            "modelo": {"disponible": self.motor.disponible(), "nombre": self.motor.nombre,
                       "dispositivo": self.motor.dispositivo.descripcion,
                       "listo": self.motor.chat.vivo() if self.motor.disponible() else False,
                       "recursos": self.motor.recursos() if self.motor.disponible() else {}},
            "memoria": self.memoria.resumen(),
            # La insignia «Normativa actualizada» se enseña un mes; después, ya no es novedad.
            "actualizacion": {"ultima": ultima, "proxima": proxima,
                              "novedades": sum(1 for x in novedades if x.get("nueva") and
                                               x.get("fecha", "") >= time.strftime(
                                                   "%Y-%m-%d", time.localtime(time.time() - 30 * 86400)))},
        }


class ServidorExclusivo(ThreadingHTTPServer):
    """Puerto en exclusiva: ni SO_REUSEADDR ni un segundo servidor escuchando a la vez."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        import socket
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def rotar_registros(maximo: int = 5 * 2**20) -> None:
    """Los registros de llama.cpp crecen sin fin (el de embeddings, 1,8 MB en una hora):
    al arrancar, el que pase de 5 MB pasa a «.1» y empieza de cero (hallazgo 14)."""
    for ruta in rutas.DATOS.glob("*.log"):
        try:
            if ruta.stat().st_size > maximo:
                ruta.replace(ruta.with_suffix(ruta.suffix + ".1"))
        except OSError:
            pass


def leer_novedades() -> list[dict]:
    ruta = rutas.DATOS / "novedades.jsonl"
    if not ruta.exists():
        return []
    filas = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        try:
            filas.append(json.loads(linea))
        except ValueError:
            continue
    return sorted(filas, key=lambda x: x.get("fecha", ""), reverse=True)[:50]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def manejador(app: Aplicacion):
    # Los únicos archivos que se sirven: los de web/, por su nombre exacto. Hallazgo 3 de la
    # revisión (comprobado): «GET /\\10.9.9.9\c\x.js» formaba una ruta UNC y Windows la abría
    # (enviando la autenticación de la cuenta) ANTES de comprobar que caía fuera de web/.
    publicos = {p.name: p for p in rutas.WEB.iterdir() if p.is_file()}

    class Manejador(BaseHTTPRequestHandler):
        server_version = "ChatbotMensura/1.0"
        # Una conexión que no manda nada ya no retiene un hilo para siempre (hallazgo 4).
        timeout = 30

        def log_message(self, formato, *args):  # silencio: el sondeo haría ruido
            log.debug("%s - %s", self.address_string(), formato % args)

        def _json(self, codigo: int, cuerpo: dict) -> None:
            datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(datos)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(datos)

        def _leer_json(self) -> dict | None:
            """None = petición inválida (ya respondida). Hallazgo 4: con Content-Length -1,
            `read(-1)` leía hasta que el cliente cerrara: memoria sin límite."""
            try:
                largo = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                largo = -1
            if largo < 0 or largo > 200_000:
                self._json(413, {"error": "Petición demasiado grande o mal formada."})
                return None
            # Solo JSON: una página de otro sitio no puede mandar `application/json` sin
            # permiso previo (CORS), así que tampoco puede preguntar ni valorar por nadie.
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                self._json(415, {"error": "Se espera application/json."})
                return None
            try:
                datos = json.loads(self.rfile.read(largo) or b"{}")
                return datos if isinstance(datos, dict) else {}
            except ValueError:
                return {}

        def _archivo(self, ruta: str) -> None:
            nombre = ruta.strip("/") or "index.html"
            destino = publicos.get(nombre)
            if destino is None:
                self._json(404, {"error": "No existe."})
                return
            datos = destino.read_bytes()
            tipo = mimetypes.guess_type(destino.name)[0] or "application/octet-stream"
            if tipo.startswith("text/") or tipo in ("application/javascript",):
                tipo += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(datos)))
            # Que una actualización llegue a los 35 PCs sin esperar (lección del portal).
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(datos)

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            ruta = u.path
            app.ultima_actividad = time.time()
            try:
                if ruta == "/api/ping":
                    # Ligero a propósito: el lanzador pregunta aquí si el servidor vive, y
                    # /api/salud consulta los llama-server (hasta varios segundos).
                    self._json(200, {"chatbot": "mensura", "corpus": app.version})
                elif ruta.startswith("/api/respuesta/"):
                    self._json(*app.respuesta(ruta.rsplit("/", 1)[-1], int(q.get("desde", 0) or 0)))
                elif ruta == "/api/salud":
                    self._json(200, app.salud())
                elif ruta == "/api/articulo":
                    self._json(*app.articulo(q))
                elif ruta == "/api/sugerencias":
                    self._json(200, {"preguntas": SUGERENCIAS})
                elif ruta == "/api/normas":
                    self._json(200, {"normas": [
                        {k: n.get(k) for k in ("id", "titulo", "estado", "grupo", "url",
                                               "articulos", "indexada")}
                        for n in app.buscador.normas.values()]})
                elif ruta == "/api/novedades":
                    self._json(200, {"novedades": leer_novedades()})
                elif ruta.startswith("/api/"):
                    self._json(404, {"error": "Ruta desconocida."})
                else:
                    self._archivo(ruta)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # noqa: BLE001
                log.exception("GET %s", ruta)
                self._json(500, {"error": f"Error interno: {e}"})

        def do_POST(self):  # noqa: N802
            ruta = urlparse(self.path).path
            app.ultima_actividad = time.time()
            try:
                datos = self._leer_json()
                if datos is None:
                    return
                if ruta == "/api/preguntar":
                    self._json(*app.preguntar(datos))
                elif ruta == "/api/valorar":
                    ok = app.memoria.valorar(str(datos.get("id", "")), bool(datos.get("util")),
                                             str(datos.get("comentario", ""))[:1000])
                    self._json(200 if ok else 404, {"ok": ok})
                else:
                    self._json(404, {"error": "Ruta desconocida."})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # noqa: BLE001
                log.exception("POST %s", ruta)
                self._json(500, {"error": f"Error interno: {e}"})

    return Manejador


def main() -> int:
    partes = argparse.ArgumentParser(description=__doc__)
    partes.add_argument("--puerto", type=int, default=8013)
    partes.add_argument("--host", default="0.0.0.0")
    partes.add_argument("--sin-precarga", action="store_true",
                        help="no arrancar el modelo hasta la primera pregunta")
    partes.add_argument("--apagar-sin-uso", type=int, default=0, metavar="SEG",
                        help="apagarse solo si ninguna pantalla lo usa en ese tiempo "
                             "(lo pasa el programa de Windows)")
    args = partes.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rotar_registros()

    # Antes que nada, reservar el puerto EN EXCLUSIVA (hallazgo 11): en Windows,
    # SO_REUSEADDR deja a dos servidores escuchar en el mismo puerto y las peticiones se
    # reparten al azar entre los dos.
    try:
        servidor = ServidorExclusivo((args.host, args.puerto), None)
    except OSError:
        log.error("El puerto %d ya está en uso: ¿hay otro Chatbot Mensura abierto?", args.puerto)
        return 2

    app = Aplicacion()
    servidor.RequestHandlerClass = manejador(app)
    log.info("corpus: %d unidades de %d normas", len(app.buscador.unidades), len(app.buscador.normas))
    log.info("redactor: %s en %s", app.motor.nombre or "(ninguno: modo citas)",
             app.motor.dispositivo.descripcion)
    def precargar():
        # En orden: primero el redactor (decide cuánta GPU ocupa), después los embeddings en
        # la GPU solo si sobra memoria, si no en el procesador. La primera pregunta no paga
        # la carga de ninguno de los dos.
        if app.motor.disponible():
            app.motor.preparar()
        if app.vectores is not None:
            if app.vectores.listos:
                app.motor.emb.asegurar()
            else:
                # Faltan vectores (corpus nuevo): se calculan ahora; mientras, la búsqueda va
                # solo por palabras y nadie espera.
                app.vectores.calcular()

    if not args.sin_precarga:
        threading.Thread(target=precargar, daemon=True, name="precarga").start()
    log.info("búsqueda por significado: %s",
             "lista" if app.vectores is not None and app.vectores.listos else "no (solo palabras)")
    try:
        from chatbot import actualizador
        actualizador.programar(app)
    except ImportError:
        pass

    if args.apagar_sin_uso:
        # Hallazgo 12: el lanzador no puede fiarse de saber cuándo se cierra la ventana de
        # Edge (puede seguir viva en segundo plano o ceder la URL a otra). La pantalla pide
        # /api/salud cada minuto: si NADIE pregunta nada en ese tiempo, no hay ventana abierta.
        def vigilar_uso():
            while True:
                time.sleep(15)
                if time.time() - app.ultima_actividad > args.apagar_sin_uso:
                    log.info("ninguna pantalla lo usa desde hace %d s: me apago", args.apagar_sin_uso)
                    servidor.shutdown()
                    return
        threading.Thread(target=vigilar_uso, daemon=True, name="apagar-sin-uso").start()

    log.info("Chatbot Mensura en http://%s:%d/", "localhost" if args.host == "0.0.0.0" else args.host,
             args.puerto)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.motor.parar()
    return 0


if __name__ == "__main__":
    sys.exit(main())

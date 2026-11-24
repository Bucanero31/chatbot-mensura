"""El modelo de lenguaje LOCAL, y cómo se reparte entre la tarjeta gráfica y el procesador.

Sin clave, sin internet y sin cuota (decisión de Henry, 24-nov-2026: «que todo lo procese él
mismo»). Dos procesos `llama-server`, cada uno en su puerto y solo en 127.0.0.1:

- **redactor** (`puerto_chat`): escribe la respuesta a partir de los artículos encontrados;
- **embeddings** (`puerto_embeddings`): convierte texto en vectores para buscar por significado.

## Sacarle el máximo al equipo, según lo que haya libre (pedido de Henry, 24-nov-2026)

Al arrancar se MIDE el equipo (`medir_equipo`: núcleos reales, hilos, RAM libre, tarjetas y su
memoria libre) y se hace un PLAN (`planificar`):

| Si hay… | Entonces |
|---|---|
| Tarjeta dedicada donde cabe el modelo entero | Todo el redactor en la GPU; si sobra memoria, contexto 8192 y **dos respuestas a la vez** |
| Tarjeta dedicada donde NO cabe | **GPU + CPU**: llama.cpp (`--fit`) pone en la tarjeta las capas que caben y el resto en el procesador, dejando `margen_vram_mb` libres para los demás programas (AutoCAD) |
| Sin tarjeta dedicada | Todo en el procesador: **núcleos reales para escribir** (`-t`) y **todos los hilos para leer** (`-tb`), que es lo que más rinde en CPU |
| Poca RAM libre | Un modelo más ligero de los instalados, en vez de hacer que Windows tire de disco |

Y **mientras funciona**:
- los embeddings van al procesador mientras el redactor ocupa la tarjeta (los dos a la vez), y
  a la tarjeta solo si después de cargar el redactor sobra memoria;
- si la tarjeta falla (memoria agotada, «device lost»), el redactor **pasa solo al
  procesador** y lo vuelve a intentar con la GPU en el siguiente arranque;
- tras `liberar_tras_minutos` sin preguntas **suelta la GPU y la RAM** para el resto del PC, y
  la siguiente pregunta lo vuelve a cargar (5-10 s).

**Las integradas se evitan a propósito.** Medido en el portátil de Henry (Intel UHD 630 +
GTX 1050): con todas las capas forzadas a «la GPU», llama.cpp repartió el modelo entre las dos y
Vulkan se cayó a media respuesta («ErrorDeviceLost»).

## Lo que el modelo NO hace

No sabe de leyes ni decide nada: redacta lo que dicen los fragmentos que le da el buscador.
Si no arranca o falla, el chatbot responde igual con las citas literales.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from chatbot import rutas

log = logging.getLogger(__name__)

MOTOR = rutas.RAIZ / "motor_local"
BINARIO = MOTOR / "llama" / "llama-server.exe"
MODELOS = MOTOR / "modelos"

PREFERENCIA_CHAT = (
    # Medido el 24-nov-2026 (evaluacion/RESULTADOS.md): Gemma 3 4B es la única con cero
    # avisos del verificador y la que mejor cita. Granite es la alternativa (y la ligera si
    # falta RAM). El LFM2 1.2B, el más rápido, quedó FUERA: inventó un «Decreto 108-05» para
    # Colombia y un impuesto español para la DGII. Rapidez sin fidelidad no sirve aquí.
    "gemma-3-4b-it-Q4_K_M.gguf",
    "granite-4.0-h-micro-Q4_K_M.gguf",
    "Qwen3-4B-Q4_K_M.gguf",
)
PREFERENCIA_EMBEDDINGS = ("embeddinggemma-300M-Q8_0.gguf",)

CONFIG_POR_DEFECTO = {
    "modelo_chat": "auto",
    "modelo_embeddings": "auto",
    "dispositivo": "auto",          # auto | cpu | Vulkan1 …
    "contexto": 0,                  # 0 = lo decide el plan (4096 u 8192)
    "paralelo": 0,                  # 0 = lo decide el plan (1 o 2 respuestas a la vez)
    "hilos": 0,                     # 0 = núcleos reales del equipo
    "margen_vram_mb": 768,          # memoria de la tarjeta que se deja libre para otros programas
    "liberar_tras_minutos": 20,     # 0 = no soltar nunca la GPU/RAM
    "puerto_chat": 8014,
    "puerto_embeddings": 8015,
    "max_tokens": 450,
    "temperatura": 0.15,
    "segundos_maximos": 150,        # si la redacción pasa de esto, se corta y queda la rápida
}

_INTEGRADAS = re.compile(r"intel|uhd|iris|radeon\(tm\) graphics|vega \d+ graphics|llvmpipe|"
                         r"microsoft basic", re.I)
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def leer_config() -> dict:
    cfg = dict(CONFIG_POR_DEFECTO)
    if rutas.CONFIG.exists():
        try:
            datos = json.loads(rutas.CONFIG.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in datos.get("motor_local", {}).items() if v is not None})
        except (OSError, ValueError) as e:
            log.warning("config.json ilegible (%s): se usan los valores por defecto", e)
    return cfg


def nombre_bonito(modelo: Path | None) -> str:
    if not modelo:
        return ""
    return re.sub(r"[-_](Q\d.*|f16|bf16)$", "", modelo.stem)


# ---------------------------------------------------------------------------
# Medir el equipo
# ---------------------------------------------------------------------------


@dataclass
class Tarjeta:
    argumento: str          # «Vulkan1»
    nombre: str
    total_mb: int
    libre_mb: int


@dataclass
class Equipo:
    nucleos: int
    hilos: int
    ram_total_mb: int
    ram_libre_mb: int
    tarjetas: list[Tarjeta] = field(default_factory=list)

    @property
    def mejor_tarjeta(self) -> Tarjeta | None:
        dedicadas = [t for t in self.tarjetas if not _INTEGRADAS.search(t.nombre)]
        return max(dedicadas, key=lambda t: t.libre_mb, default=None)


def _ram_mb() -> tuple[int, int]:
    class Estado(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    try:
        e = Estado()
        e.dwLength = ctypes.sizeof(Estado)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(e))
        return e.ullTotalPhys // 2**20, e.ullAvailPhys // 2**20
    except (AttributeError, OSError):
        return 8192, 4096


_NUCLEOS: int | None = None


def _nucleos_reales(hilos: int) -> int:
    """Núcleos FÍSICOS: escribir con más hilos que núcleos reales es más lento, no más rápido."""
    global _NUCLEOS
    if _NUCLEOS is None:
        try:
            salida = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum"],
                capture_output=True, text=True, timeout=20, creationflags=_SIN_VENTANA).stdout
            _NUCLEOS = int(salida.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            _NUCLEOS = max(1, hilos // 2)
    return _NUCLEOS


def listar_tarjetas() -> list[Tarjeta]:
    if not BINARIO.exists():
        return []
    try:
        salida = subprocess.run([str(BINARIO), "--list-devices"], capture_output=True,
                                text=True, timeout=60, creationflags=_SIN_VENTANA).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [Tarjeta(m.group(1), m.group(2), int(m.group(3)), int(m.group(4))) for m in re.finditer(
        r"^\s*(Vulkan\d+):\s*(.+?)\s*\((\d+) MiB, (\d+) MiB free\)", salida, re.M)]


def medir_equipo() -> Equipo:
    hilos = os.cpu_count() or 4
    total, libre = _ram_mb()
    return Equipo(_nucleos_reales(hilos), hilos, total, libre, listar_tarjetas())


# ---------------------------------------------------------------------------
# Planificar
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    modelo: Path | None
    modo: str               # «GPU», «GPU+CPU», «CPU»
    descripcion: str        # lo que se enseña en la pantalla
    argumentos: list[str]
    contexto: int
    paralelo: int
    tarjeta: Tarjeta | None = None


def _mb(ruta: Path | None) -> int:
    return ruta.stat().st_size // 2**20 if ruta and ruta.exists() else 0


def planificar(eq: Equipo, cfg: dict, forzar_cpu: bool = False) -> Plan:
    instalados = [MODELOS / n for n in PREFERENCIA_CHAT if (MODELOS / n).exists()]
    if cfg["modelo_chat"] != "auto":
        fijo = MODELOS / cfg["modelo_chat"]
        instalados = [fijo] if fijo.exists() else []
    if not instalados:
        return Plan(None, "", "sin modelo", [], 0, 0)

    tarjeta = None if (forzar_cpu or cfg["dispositivo"] == "cpu") else eq.mejor_tarjeta
    if cfg["dispositivo"] not in ("auto", "cpu"):
        tarjeta = next((t for t in eq.tarjetas if t.argumento == cfg["dispositivo"]), tarjeta)
    margen = int(cfg["margen_vram_mb"])

    # Modelo: el preferido, salvo que no quepa en la RAM libre cuando va (en parte) al
    # procesador. Mejor un modelo más ligero que obligar a Windows a tirar del disco.
    modelo = instalados[0]
    for candidato in instalados:
        en_gpu = tarjeta and tarjeta.libre_mb - margen >= _mb(candidato) + 700
        ram_necesaria = (_mb(candidato) // 3 if en_gpu else _mb(candidato)) + 1200
        if ram_necesaria <= eq.ram_libre_mb:
            modelo = candidato
            break
    tam = _mb(modelo)

    hilos_escribir = int(cfg["hilos"]) or eq.nucleos
    comunes = ["--jinja", "-ctk", "q8_0", "-ctv", "q8_0", "-t", str(hilos_escribir),
               "-tb", str(eq.hilos), "--threads-http", "4"]

    if tarjeta:
        libre_util = tarjeta.libre_mb - margen
        entero = libre_util >= tam + 700
        holgado = libre_util >= tam + 1800
        contexto = int(cfg["contexto"]) or (8192 if holgado else 4096)
        paralelo = int(cfg["paralelo"]) or (2 if holgado and contexto >= 8192 else 1)
        args = ["--device", tarjeta.argumento, "--fit", "on", "--fit-target", str(margen),
                "-fa", "auto", "-c", str(contexto), "-np", str(paralelo), *comunes]
        modo = "GPU" if entero else "GPU+CPU"
        desc = (f"GPU · {tarjeta.nombre}" if entero
                else f"GPU + CPU · {tarjeta.nombre} y {hilos_escribir} núcleos")
        return Plan(modelo, modo, desc, args, contexto, paralelo, tarjeta)

    contexto = int(cfg["contexto"]) or (8192 if eq.ram_libre_mb >= tam + 6000 else 4096)
    paralelo = int(cfg["paralelo"]) or 1
    args = ["--device", "none", "-ngl", "0", "-c", str(contexto), "-np", str(paralelo),
            *comunes]
    return Plan(modelo, "CPU", f"CPU · {hilos_escribir} núcleos / {eq.hilos} hilos",
                args, contexto, paralelo)


# Compatibilidad con quien pregunta solo «¿qué dispositivo?»
@dataclass
class Dispositivo:
    argumento: str
    descripcion: str
    libre_mb: int = 0


def detectar_dispositivo(modelo: Path | None, fijado: str = "auto") -> Dispositivo:
    plan = planificar(medir_equipo(), {**CONFIG_POR_DEFECTO, "dispositivo": fijado,
                                       "modelo_chat": modelo.name if modelo else "auto"})
    t = plan.tarjeta
    return Dispositivo(t.argumento if t else "none", plan.descripcion, t.libre_mb if t else 0)


# ---------------------------------------------------------------------------
# Que ningún llama-server sobreviva al programa que lo lanzó
# ---------------------------------------------------------------------------

_TRABAJO = None


def _atar_a_este_proceso(proceso: subprocess.Popen) -> None:
    """Mete el llama-server en un «Job Object» de Windows con KILL_ON_JOB_CLOSE: cuando este
    Python muere —bien, mal o con taskkill /F— Windows cierra el trabajo y mata al hijo.

    Medido el 24-nov-2026: un script que usó los embeddings y salió sin `parar()` dejó un
    llama-server huérfano en el puerto 8015 ocupando RAM; y el siguiente programa lo
    REUTILIZÓ creyéndolo suyo. En Windows, matar al padre no mata a los hijos."""
    global _TRABAJO
    if os.name != "nt":
        return
    try:
        k32 = ctypes.windll.kernel32
        if _TRABAJO is None:
            class Basica(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", ctypes.c_uint32),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", ctypes.c_uint32),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", ctypes.c_uint32),
                            ("SchedulingClass", ctypes.c_uint32)]

            class Io(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class Extendida(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", Basica), ("IoInfo", Io),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32.CreateJobObjectW.restype = ctypes.c_void_p
            trabajo = k32.CreateJobObjectW(None, None)
            info = Extendida()
            info.BasicLimitInformation.LimitFlags = 0x2000      # KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                   ctypes.c_void_p, ctypes.c_uint32]
            if not k32.SetInformationJobObject(trabajo, 9, ctypes.byref(info),
                                               ctypes.sizeof(info)):
                return
            _TRABAJO = trabajo
        k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k32.AssignProcessToJobObject(_TRABAJO, ctypes.c_void_p(int(proceso._handle)))
    except (AttributeError, OSError, ValueError) as e:  # nunca impide arrancar
        log.debug("no pude atar llama-server al proceso: %s", e)


# ---------------------------------------------------------------------------
# Un proceso llama-server
# ---------------------------------------------------------------------------


class Servidor:
    """Un proceso llama-server, arrancado a demanda y vigilado."""

    def __init__(self, papel: str, modelo: Path | None, puerto: int, argumentos: list[str]):
        self.papel = papel
        self.modelo = modelo
        self.puerto = puerto
        self.argumentos = argumentos
        self.proceso: subprocess.Popen | None = None
        self.ultimo_error = ""
        self._cerrojo = threading.Lock()
        # Clave nueva en cada arranque: llama-server permite CORS desde cualquier origen, así
        # que sin clave CUALQUIER página web abierta en el PC podría usar el modelo y la GPU
        # (hallazgo 15 de la revisión). Solo este programa la conoce.
        import secrets
        self.clave = secrets.token_hex(16)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.puerto}"

    def peticion(self, ruta: str, cuerpo: dict | None = None) -> urllib.request.Request:
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        return urllib.request.Request(self.url + ruta, data=datos, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.clave}"})

    @property
    def registro(self) -> Path:
        return rutas.DATOS / f"llama-{self.papel}.log"

    def vivo(self) -> bool:
        """¿Responde un llama-server en el puerto, y es ESTE modelo? Un huérfano de otra
        ejecución con otro modelo no se reutiliza a ciegas."""
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=2) as r:
                if json.loads(r.read() or b"{}").get("status") != "ok":
                    return False
            if self.modelo:
                # Con NUESTRA clave: un llama-server ajeno (otra clave) contesta 401 y no se
                # toma por propio.
                with urllib.request.urlopen(self.peticion("/v1/models"), timeout=2) as r:
                    ids = " ".join(m.get("id", "") for m in json.loads(r.read()).get("data", []))
                if ids and self.modelo.name not in ids and self.modelo.stem not in ids:
                    self.ultimo_error = f"el puerto {self.puerto} lo ocupa otro modelo ({ids})"
                    return False
            return True
        except (OSError, ValueError):
            return False

    def asegurar(self, espera: float = 180) -> bool:
        """Arranca el proceso si no está. Devuelve si quedó listo."""
        if not self.modelo or not BINARIO.exists():
            self.ultimo_error = "no hay modelo o falta llama-server.exe"
            return False
        with self._cerrojo:
            if self.vivo():
                return True
            if self.proceso is None or self.proceso.poll() is not None:
                rutas.DATOS.mkdir(parents=True, exist_ok=True)
                salida = self.registro.open("ab")
                orden = [str(BINARIO), "-m", str(self.modelo), "--host", "127.0.0.1",
                         "--port", str(self.puerto), "--api-key", self.clave, *self.argumentos]
                log.info("arrancando %s: %s", self.papel,
                         " ".join(orden[1:]).replace(self.clave, "***"))
                self.proceso = subprocess.Popen(orden, stdout=salida, stderr=salida,
                                                creationflags=_SIN_VENTANA)
                _atar_a_este_proceso(self.proceso)
            limite = time.time() + espera
            while time.time() < limite:
                if self.vivo():
                    return True
                if self.proceso.poll() is not None:
                    self.ultimo_error = f"llama-server ({self.papel}) terminó al arrancar"
                    return False
                time.sleep(0.5)
            self.ultimo_error = f"llama-server ({self.papel}) no respondió en {espera:.0f} s"
            return False

    def capas_en_gpu(self) -> str:
        """«35/35» leído del registro de llama.cpp tras arrancar (lo decidió --fit)."""
        try:
            # Solo el final: /api/salud lo piden los 35 PCs cada minuto y el registro crece.
            with self.registro.open("rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 200_000))
                cola = f.read().decode("utf-8", "replace")
        except OSError:
            return ""
        hallados = re.findall(r"offloaded (\d+)/(\d+) layers to GPU", cola)
        return f"{hallados[-1][0]}/{hallados[-1][1]}" if hallados else ""

    def parar(self) -> None:
        if self.proceso and self.proceso.poll() is None:
            self.proceso.terminate()
            try:
                self.proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proceso.kill()


# ---------------------------------------------------------------------------
# El motor: plan + procesos + vigilancia
# ---------------------------------------------------------------------------


class MotorLocal:
    def __init__(self, config: dict | None = None):
        self.cfg = config or leer_config()
        self.equipo = medir_equipo()
        self.forzado_cpu = False
        self.plan = planificar(self.equipo, self.cfg)
        self.modelo_chat = self.plan.modelo
        self.modelo_emb = self._modelo_emb()
        self.chat = Servidor("redactor", self.modelo_chat, self.cfg["puerto_chat"],
                             self.plan.argumentos)
        self.emb = Servidor("embeddings", self.modelo_emb, self.cfg["puerto_embeddings"],
                            self._args_emb(en_gpu=False))
        self._cola = threading.Semaphore(max(1, self.plan.paralelo))
        self._esperando = 0
        self._activos = 0
        self._cerrojo_cola = threading.Lock()
        self._ultimo_uso = time.time()
        self._vigilante = None
        log.info("equipo: %d núcleos / %d hilos, RAM libre %d MB, tarjetas: %s", self.equipo.nucleos,
                 self.equipo.hilos, self.equipo.ram_libre_mb,
                 ", ".join(f"{t.nombre} ({t.libre_mb} MB libres)" for t in self.equipo.tarjetas)
                 or "ninguna")
        log.info("plan: %s · %s · contexto %d · %d a la vez", nombre_bonito(self.modelo_chat),
                 self.plan.descripcion, self.plan.contexto, self.plan.paralelo)

    def _modelo_emb(self) -> Path | None:
        fijado = self.cfg["modelo_embeddings"]
        if fijado and fijado != "auto":
            p = MODELOS / fijado
            return p if p.exists() else None
        return next((MODELOS / n for n in PREFERENCIA_EMBEDDINGS if (MODELOS / n).exists()), None)

    def _args_emb(self, en_gpu: bool) -> list[str]:
        dispositivo = (["--device", self.plan.tarjeta.argumento]
                       if en_gpu and self.plan.tarjeta else ["--device", "none", "-ngl", "0"])
        # Pocos hilos: una pregunta son ~30 tokens, y así no le roba procesador al redactor.
        # Para calcular el corpus entero (sin chat abierto) se piden todos: `hilos_embeddings`.
        hilos = int(self.cfg.get("hilos_embeddings") or max(2, self.equipo.nucleos // 2))
        return ["--embedding", *dispositivo, "-c", "2048", "-b", "2048", "-ub", "2048",
                "-np", "1", "-t", str(hilos), "-tb", str(hilos)]

    # ----------------------------------------------------------------- estado
    @property
    def nombre(self) -> str:
        return nombre_bonito(self.modelo_chat)

    @property
    def dispositivo(self) -> Dispositivo:
        t = self.plan.tarjeta
        return Dispositivo(t.argumento if t else "none", self.plan.descripcion,
                           t.libre_mb if t else 0)

    def disponible(self) -> bool:
        return bool(self.modelo_chat) and BINARIO.exists()

    def en_cola(self) -> int:
        return self._esperando

    def recursos(self) -> dict:
        """Lo que la pantalla y /api/salud enseñan del reparto de recursos."""
        return {
            "modo": self.plan.modo, "descripcion": self.plan.descripcion,
            "capas_en_gpu": self.chat.capas_en_gpu() if self.chat.vivo() else "",
            "contexto": self.plan.contexto, "a_la_vez": self.plan.paralelo,
            "nucleos": self.equipo.nucleos, "hilos": self.equipo.hilos,
            "ram_libre_mb": _ram_mb()[1], "cargado": self.chat.vivo(),
            "embeddings_en": "GPU" if "--embedding" in self.emb.argumentos
            and "none" not in self.emb.argumentos else "CPU",
            "paso_a_cpu_por_fallo": self.forzado_cpu,
            "tarjetas": [asdict(t) for t in self.equipo.tarjetas],
        }

    # ------------------------------------------------------------ arranque
    def preparar(self) -> bool:
        """Carga el redactor; si la GPU falla, cae al procesador. Después, embeddings donde
        quepan. Lo llama la precarga del servidor y la primera pregunta tras liberar."""
        if not self.disponible():
            return False
        listo = self.chat.asegurar()
        if not listo and self.plan.tarjeta and not self.forzado_cpu:
            log.warning("el redactor no arrancó en la GPU (%s): paso al procesador",
                        self.chat.ultimo_error)
            self._a_cpu()
            listo = self.chat.asegurar()
        self._colocar_embeddings()
        self._vigilar()
        return listo

    def _a_cpu(self) -> None:
        self.chat.parar()
        self.forzado_cpu = True
        self.plan = planificar(medir_equipo(), self.cfg, forzar_cpu=True)
        self.chat.argumentos = self.plan.argumentos
        self.chat.modelo = self.modelo_chat = self.plan.modelo

    def _colocar_embeddings(self) -> None:
        """A la GPU solo si, con el redactor ya cargado, sobra memoria de vídeo."""
        if not self.modelo_emb or self.emb.vivo():
            return
        en_gpu = False
        if self.plan.tarjeta and self.plan.modo == "GPU":
            ahora = next((t for t in listar_tarjetas()
                          if t.argumento == self.plan.tarjeta.argumento), None)
            en_gpu = bool(ahora and ahora.libre_mb - int(self.cfg["margen_vram_mb"])
                          >= _mb(self.modelo_emb) + 400)
        self.emb.argumentos = self._args_emb(en_gpu)

    def _vigilar(self) -> None:
        """Suelta GPU y RAM tras un rato sin preguntas (el PC es también de AutoCAD)."""
        minutos = float(self.cfg.get("liberar_tras_minutos") or 0)
        if not minutos or self._vigilante:
            return

        def bucle():
            while True:
                time.sleep(60)
                # Bajo el MISMO cerrojo con que una pregunta se apunta como activa: así no
                # puede colarse una entre la comprobación y el apagado (hallazgo 6).
                with self._cerrojo_cola:
                    inactivo = time.time() - self._ultimo_uso
                    if (self._activos == 0 and self._esperando == 0
                            and inactivo > minutos * 60 and self.chat.proceso
                            and self.chat.proceso.poll() is None):
                        log.info("sin preguntas en %.0f min: libero el redactor (GPU/RAM)",
                                 minutos)
                        self.chat.parar()

        self._vigilante = threading.Thread(target=bucle, daemon=True, name="liberar-recursos")
        self._vigilante.start()

    # ------------------------------------------------------------------ chat
    def redactar(self, mensajes: list[dict], al_escribir=None, cancelado=None,
                 max_tokens: int | None = None) -> str:
        """Pide la respuesta en streaming (local, sin proxy de por medio) y va llamando a
        `al_escribir(trozo)`. Tantas a la vez como diga el plan; el resto espera en cola."""
        with self._cerrojo_cola:
            self._esperando += 1
        with self._cola:
            with self._cerrojo_cola:
                self._esperando -= 1
                self._activos += 1
            try:
                self._ultimo_uso = time.time()
                if cancelado and cancelado():
                    return ""            # la abandonaron mientras esperaba: no se redacta
                if not self.chat.vivo() and not self.preparar():
                    raise RuntimeError(self.chat.ultimo_error)
                try:
                    return self._redactar(mensajes, al_escribir, cancelado, max_tokens)
                except OSError:
                    # Solo si el PROCESO murió (memoria de vídeo agotada, tarjeta reiniciada)
                    # se reintenta en el procesador. Un 400 por contexto excedido o un tiempo
                    # agotado también son OSError, y pasar a CPU por eso dejaba a toda la
                    # oficina 10 veces más lenta sin motivo (hallazgo 6 de la revisión).
                    murio = self.chat.proceso is not None and self.chat.proceso.poll() is not None
                    if murio and self.plan.tarjeta and not self.forzado_cpu:
                        log.warning("el redactor cayó en la GPU: reintento en el procesador")
                        self._a_cpu()
                        if self.preparar():
                            return self._redactar(mensajes, al_escribir, cancelado, max_tokens)
                    raise
            finally:
                self._ultimo_uso = time.time()
                with self._cerrojo_cola:
                    self._activos -= 1

    def _redactar(self, mensajes, al_escribir, cancelado, max_tokens=None) -> str:
        cuerpo = {
            "messages": mensajes, "stream": True, "cache_prompt": True,
            "temperature": self.cfg["temperatura"],
            "max_tokens": max_tokens or self.cfg["max_tokens"],
            "repeat_penalty": 1.05,
            # Qwen3 piensa en voz alta si no se le dice; los demás ignoran esta opción.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        peticion = self.chat.peticion("/v1/chat/completions", cuerpo)
        texto = []
        limite = time.time() + self.cfg["segundos_maximos"]
        with urllib.request.urlopen(peticion, timeout=self.cfg["segundos_maximos"]) as r:
            for linea in r:
                if cancelado and cancelado():
                    break
                if time.time() > limite:
                    texto.append("…")
                    break
                linea = linea.decode("utf-8", "replace").strip()
                if not linea.startswith("data:"):
                    continue
                dato = linea[5:].strip()
                if dato == "[DONE]":
                    break
                try:
                    delta = json.loads(dato)["choices"][0].get("delta", {}).get("content")
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    texto.append(delta)
                    if al_escribir:
                        al_escribir(delta)
        return "".join(texto)

    # ------------------------------------------------------------- embeddings
    def vectores(self, textos: list[str], rapido: bool = False) -> list[list[float]]:
        """`rapido=True` es el camino de una PREGUNTA: si el servidor de embeddings no está
        ya vivo no se espera a que arranque (se lanza aparte) y se corta a los 5 s. Hallazgo
        13: antes una pregunta podía quedarse minutos esperando algo que es opcional."""
        if rapido:
            if not self.emb.vivo():
                self._arrancar_emb_aparte()
                raise RuntimeError("embeddings aún no listos")
        elif not self.emb.asegurar():
            raise RuntimeError(self.emb.ultimo_error)
        peticion = self.emb.peticion("/v1/embeddings",
                                     {"input": textos, "encoding_format": "float"})
        with urllib.request.urlopen(peticion, timeout=5 if rapido else 600) as r:
            datos = json.loads(r.read())
        filas = sorted(datos["data"], key=lambda d: d["index"])
        return [f["embedding"] for f in filas]

    def _arrancar_emb_aparte(self) -> None:
        if getattr(self, "_arrancando_emb", None) and self._arrancando_emb.is_alive():
            return
        self._arrancando_emb = threading.Thread(target=self.emb.asegurar, daemon=True,
                                                name="arrancar-embeddings")
        self._arrancando_emb.start()

    def parar(self) -> None:
        self.chat.parar()
        self.emb.parar()


_UNICO: MotorLocal | None = None


def motor() -> MotorLocal:
    global _UNICO
    if _UNICO is None:
        _UNICO = MotorLocal()
    return _UNICO


if __name__ == "__main__":  # diagnóstico: py -m chatbot.motor_local
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    m = MotorLocal()
    print(json.dumps({"modelo": str(m.modelo_chat), "embeddings": str(m.modelo_emb),
                      "argumentos": m.chat.argumentos, **m.recursos()},
                     ensure_ascii=False, indent=1))

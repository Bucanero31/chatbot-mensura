# Chatbot Mensura — leer esto primero

Chatbot de preguntas sobre la normativa de mensura y registro inmobiliario de RD. Creado el
24-nov-2026 a partir del conocimiento del Revisor X. Plan y decisiones: `PLAN.md`.
Lo que se ha medido: `evaluacion/RESULTADOS.md`. Historia: `BITACORA.md`.

## Reglas de la casa (decididas por Henry el 24-nov-2026)

1. **Todo dentro de esta carpeta.** Python (`runtime/`), librerías (`libs/`), llama.cpp y
   modelos (`motor_local/`), normas (`corpus/`). Nada se lee de otra carpeta en
   funcionamiento. El Revisor X es una FUENTE de la que se importa
   (`herramientas/sincronizar_desde_revisor.py`, y el actualizador si está en el mismo PC).
2. **Sin claves ni servicios externos.** El modelo es local (llama.cpp). Gemini quedó
   descartado por Henry: «que todo lo procese él mismo».
3. **Se actualiza solo** (`chatbot/actualizador.py`, semanal) y **aprende con el uso**
   (`chatbot/memoria.py`). Lo aprendido solo ordena la búsqueda: nunca cambia el texto de una
   norma.
4. **El repositorio de GitHub es PÚBLICO.** Jamás: `config.json`, `datos/` (preguntas reales
   de la oficina), expedientes u oficios de clientes, ni material con derechos de autor (el
   libro comentado de la ENJ sobre la Ley 108-05 se descartó por eso).

## Para cambiar X, toca Y

| Quiero cambiar… | Archivo |
|---|---|
| Cómo se lee un PDF, se trocea por artículos, apartados, principios, reformas | `chatbot/corpus.py` |
| Qué normas hay y su vigencia | NO a mano: `corpus/catalogo_extra.yaml` (añadidos) → `chatbot/catalogo.py` funde |
| Cómo se entiende la pregunta y se ordena (BM25, sinónimos, nombres de normas) | `chatbot/buscador.py` |
| Búsqueda por significado | `chatbot/vectores.py` |
| Respuesta rápida, instrucciones del modelo, verificador de citas y cifras | `chatbot/responder.py` |
| Modelo local, GPU/CPU, puertos 8014/8015 | `chatbot/motor_local.py` · `config.json` |
| Aprendizaje y caché | `chatbot/memoria.py` |
| Actualización automática desde ri.gob.do | `chatbot/actualizador.py` |
| API y servidor (puerto 8013) | `chatbot/servidor.py` · contrato en `docs/API.md` |
| Pantalla | `web/index.html` · `web/chat.js` · `web/chat.css` (`lexgeo.css` es copia de la plataforma) |
| Programa de Windows (lanzador, ventana Edge `--app`, apagado del árbol de procesos) | `escritorio/ChatbotMensura.cs` → `escritorio/construir.bat` (csc de .NET 4, C# 5) |
| Reparto GPU/CPU, RAM, hilos, liberar recursos, paso a CPU si falla la tarjeta | `chatbot/motor_local.py` → `medir_equipo` · `planificar` · `MotorLocal.preparar` |
| Icono | `escritorio/icono.py` (genera `icono.ico` y `web/icono.png`, sin librerías) |

## Comandos (con el Python de la carpeta)

    set PYTHONPATH=%CD%\libs
    runtime\python\python.exe -m chatbot.servidor          servidor
    runtime\python\python.exe -m chatbot.corpus --informe  reconstruir el corpus
    runtime\python\python.exe -m chatbot.vectores          vectores que falten
    runtime\python\python.exe -m chatbot.actualizador --seco
    runtime\python\python.exe evaluacion\evaluar.py --vectores --fallos
    runtime\python\python.exe evaluacion\comparar_modelos.py
    runtime\python\python.exe -m pytest pruebas            (pytest no viene en libs/)

## Trampas que ya costaron tiempo

1. **El salto de página (`\x0c`) delante de «Artículo N»** lo hacía invisible: se perdían
   artículos enteros (DT-2023-001 Arts. 7, 14, 28). Y «Artículo\n125.» con el número en otra
   línea (RES-789). Las dos tienen prueba.
2. **Artículos-tabla gigantes**: DNMC-DT-2023-001 Art. 23 (46.000 caracteres) y DNRT-DT-2025-003
   Art. 24 (250.000) son las tablas de requisitos de CADA trámite. Se parten en apartados
   («5. Deslinde»), si no, «¿qué documentos lleva un deslinde?» no encuentra nada útil.
3. **La Ley 108-05 viene CONSOLIDADA** (con la 51-07 dentro) de la compilación oficial de la
   SCJ de 2011 (`texto_consolidado: true`): las reformas que la 51-07 declara NO se marcan
   como «texto anterior». El enlace «Ley 108-05» de ri.gob.do apunta al PDF de la 51-07.
4. **Vulkan con dos tarjetas** (Intel + NVIDIA) se cae («ErrorDeviceLost») si se fuerzan
   todas las capas: `motor_local` elige la dedicada y evita las integradas.
5. **El proxy del portal no deja pasar *streaming*** (lee todo y corta a 30 s): la redacción
   se pide a trozos (`/api/respuesta/<id>?desde=N`).
6. **Los modelos pequeños no saben decir «no lo encuentro»**: eso lo decide el programa con la
   cobertura de la pregunta (`responder.cobertura`), no el modelo.
7. Matar procesos **por puerto**, nunca por nombre (en este PC corren servidores de producción).
8. **Un llama-server sobrevive a su padre en Windows.** Un script que usó los embeddings y salió
   sin `parar()` dejó un huérfano en el 8015 que el siguiente programa reutilizó. Ahora cada
   llama-server va en un Job Object con KILL_ON_JOB_CLOSE (`_atar_a_este_proceso`) y `vivo()`
   comprueba que el modelo del puerto sea el esperado.
9. **Nunca crear archivos «del tamaño de un modelo» en una prueba**: `truncate` en NTFS ocupa el
   disco de verdad. Una prueba con dos «modelos» de 2,5 GB llenó C: (24-nov-2026). Se simula
   el tamaño con `monkeypatch` de `_mb`.
10. **La raíz de Snowball separa verbo y sustantivo** («variar»→vari, «variación»→variacion):
   `texto._unir_familias` las junta. Sin eso, «¿cuánto puede variar…?» no encontraba el 5 %.
11. **Las preguntas ajenas usan palabras del oficio**: no se separan bien. La regla
   (`responder.respuesta_rapida`) está calibrada para no rechazar NINGUNA legítima; las ajenas
   que pasan las frena el modelo con «No encuentro esto…».

# Bitácora

Una entrada por sesión de trabajo, la más reciente arriba.

## 24-nov-2026 (tarde) · Programa de Windows, GPU+CPU y análisis completo

- **`Chatbot Mensura.exe`** (C# 5 compilado con el csc de .NET 4 de cualquier Windows, 20 KB):
  arranca el servidor sin consola, abre el chat en Edge `--app` con perfil propio y, al
  cerrar la ventana, mata el árbol de procesos. Probado: arranque en ~10 s, puerto 8013
  ocupado → elige otro solo, cierre sin procesos colgados.
- **Plan de recursos** (`motor_local.planificar`): mide núcleos, hilos, RAM y tarjetas; el
  modelo entero en la GPU si cabe, repartido GPU+CPU con `--fit` si no, CPU con `-t` núcleos
  y `-tb` hilos sin tarjeta; contexto y respuestas simultáneas según la memoria; modelo ligero
  si falta RAM; paso a CPU si la tarjeta falla; suelta GPU/RAM tras 20 min sin preguntas.
- **Análisis y optimizaciones medidas**: búsqueda combinada con significado (peso 0,3:
  acierto@1 63 → 70,7 %, @5 89 → 93,5 %); «variar»/«variación» unidas; membrete fuera del
  texto; vector de la pregunta fuera del cerrojo (35 a la vez ya no hacen fila); memoria de
  aprendizaje indexada (35 ms con 50.000 preguntas); llama-server atados al proceso con un
  Job Object (no más huérfanos); regla de «pregunta ajena» recalibrada (0 de 96 legítimas
  rechazadas).
- **Incidente**: una prueba nueva creó «modelos» de 2,5 GB de verdad y llenó C: unos minutos.
  Recuperado (27 GB libres) y la prueba ahora simula el tamaño.
- **Revisión de código independiente**: 15 hallazgos (3 de seguridad, 1 de integridad
  normativa), todos corregidos con prueba propia (`pruebas/test_revision.py`, 80 en total).
  Detalle en `evaluacion/RESULTADOS.md` §3c. Comprobado de punta a punta: matar el `.exe` a
  la fuerza cierra servidor y modelo; sin ventanas, el servidor se apaga solo.

## 24-nov-2026 · Nace el chatbot

- Evaluado el Revisor X como fuente: 34 normas en su catálogo, índice M5 de 2.613 fragmentos,
  ~700 citas literales verificadas en sus YAML. **Faltaba la Ley 108-05** (URL «por
  confirmar»): incorporada desde la compilación oficial de la SCJ (2011), consolidada con la
  51-07, que es el PDF que ri.gob.do enlaza como «Ley 51-07».
- Corpus propio (`chatbot/corpus.py`) con cinco mejoras medidas sobre el de M5: sin
  encabezados de página, página de cada artículo para enlazar el PDF, título y sección,
  reformas leídas del propio texto, y artículos-tabla partidos por trámite (259 apartados).
  Encontró y arregló artículos que el troceo perdía en los saltos de página.
- Buscador BM25 propio con lematizador Snowball sin tildes + vocabulario del Revisor X +
  nombres comunes de normas + búsqueda por significado (EmbeddingGemma local).
- Henry decidió: **todo dentro de la carpeta**, **sin clave de Gemini** (modelo local),
  **que aprenda y se actualice solo**, repositorio **público** en GitHub.
- Comparados cuatro modelos locales como redactores (`evaluacion/comparar_modelos.py`).
- Batería de 106 preguntas redactada por Sonnet desde las citas verificadas de los YAML y
  auditada por script contra el corpus (`evaluar.py --auditar`): 2 anotaciones dudosas, una
  era un artículo que el corpus perdía (RES-789 Art. 125), ya recuperado.
- Pantalla de chat redactada por Sonnet contra `docs/API.md` y revisada en el navegador.
- Primera actualización real contra ri.gob.do. Destapó tres reglas mal supuestas (corregidas y
  con prueba): ri.gob.do enlaza formularios OFICIALES desde la página de consulta pública (el
  borrador se reconoce por el documento, no por la página); los enlaces dicen «Ver aquí» (el
  título sale del archivo); y los nombres de archivo llevan tildes (urllib no las codifica).
- Las derogaciones expresas se leen con los identificadores normalizados: la DNRT-DT-2025-003
  escribe «DNRT-DT-2023001». **Hallazgo para el Revisor X:** su catálogo dice que la
  DNRT-DT-2025-003 sustituye a la DNRT-DT-2021-0001 «(confirmar)», pero su Art. 35 deroga
  expresamente la **DNRT-DT-2023-001**; y la 2025-002 (Art. 67) deroga la **2023-002**.

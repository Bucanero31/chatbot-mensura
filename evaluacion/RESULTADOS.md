# Resultados medidos

Todo lo de aquí sale de correr los scripts de esta carpeta; nada es estimado. Fecha de cada
medición entre paréntesis. Equipo: portátil i5-8300H (4 núcleos), 24 GB, GTX 1050 4 GB.

## 1. La batería (24-nov-2026)

106 preguntas (`preguntas.yaml`), redactadas por un modelo económico (Sonnet) a partir de las
**citas ya verificadas** de los YAML del Revisor X, y **auditadas por script** contra el corpus
(`evaluar.py --auditar`): que el artículo esperado exista y que su texto contenga el dato clave.

- 92 con artículo esperado, 47 de ellas con dato clave (cifra o término que la respuesta debe dar).
- 10 fuera de alcance (honorarios, otro país, AutoCAD…): el chatbot debe decir que no lo sabe.
- La auditoría encontró **2 anotaciones dudosas**: una era un artículo que el corpus PERDÍA
  (RES-789-2022 Art. 125, por el número en otra línea: arreglado y con prueba) y la otra un dato
  que el YAML del Revisor X no saca de la cita literal (P054, «40 km»): queda como está.

## 2. El buscador

¿Está el artículo esperado entre los N primeros? (`evaluar.py --sin-memoria`)

| Versión | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| Primera, solo palabras (24-nov) | 59,8 % | 77,2 % | 83,7 % | 89,1 % |
| + artículos perdidos recuperados, cuantificadores fuera, derogadas → su reemplazo | 62,0 % | 81,5 % | 89,1 % | 93,5 % |
| Corpus ampliado a 75 normas por el actualizador (sin pérdida) | 62,0 % | 80,4 % | 88,0 % | 93,5 % |
| + familias de palabras unidas (variar/variación) y membrete fuera del texto | 63,0 % | 82,6 % | 89,1 % | 94,6 % |
| **+ búsqueda por significado, peso 0,3** | **70,7 %** | **85,9 %** | **93,5 %** | **94,6 %** |

Peso de la búsqueda por significado, medido: 0,2 → 69,6/91,3 %; **0,3 → 70,7/93,5 %**;
0,5 → 63,0/90,2 %; 0,7 → 54,3/80,4 % (acierto@1 / @5). Solo, el significado se despista con
los números de norma; como apoyo de las palabras, encuentra lo dicho con otras palabras.

### Rendimiento (portátil i5-8300H, 24-nov-2026)

| Paso | Tiempo |
|---|---|
| Búsqueda por palabras | 3 ms |
| Vector de la pregunta (EmbeddingGemma en CPU) | 63 ms |
| Coseno contra 4.915 artículos (Python puro) | ~77 ms |
| **Pregunta → respuesta con citas, completa** | **176 ms** |
| Redacción del modelo (GTX 1050 / solo CPU) | ~12 s / ~40 s |
| Arranque del programa hasta poder preguntar | ~10 s |

### ¿Pregunta ajena? (con búsqueda por significado)

Las preguntas ajenas usan palabras del oficio («agrimensor», «deslinde», «inmueble») y ni la
cobertura ni la similitud las separan bien. Regla elegida: ajena si cobertura < 0,45 **y**
similitud < 0,55 → **0 de 96 legítimas rechazadas**, 2 de 10 ajenas frenadas; las demás las
frena el modelo («No encuentro esto en la normativa cargada»). Rechazar una pregunta buena es
peor que dejar pasar una ajena al modelo, que además la verifica.

**Evidencia que llega al modelo:** en el 91,5 % de las preguntas con dato clave, ese dato está
literalmente en el texto que lee el redactor. Es el techo de cualquier redacción: si la frase
no llega, ningún modelo puede acertar. Esta medida la cazó un fallo real: «¿cuánto puede
variar la superficie en un deslinde?» le pasaba al modelo el literal j) del Art. 13 («varios
deslindes…») en lugar del e) («variación de hasta un cinco por ciento»), porque «varios» y
«variar» comparten raíz.

**«No lo encuentro»**: la cobertura (qué parte de la pregunta, ponderada por rareza, aparece en
el mejor artículo) es ≥ 0,50 en el 90 % de las preguntas de la batería; 6 de las 10 ajenas
quedan por debajo de 0,45, que es el umbral. Las ajenas que pasan, las frena el modelo.

## 2b. Las preguntas como las hace la oficina (25-nov-2026)

Henry preguntó «¿qué tiempo tengo para realizar el levantamiento luego de notificar?» y el
chatbot dijo «no encuentro», aunque la Res. 789-2022 Art. 78 lo dice (10 días calendarios
antes). La batería de las reglas no lo veía: está redactada con las palabras de la norma.
Segunda batería, **`preguntas_oficina.yaml`**: 54 preguntas coloquiales (Sonnet, auditadas: 0
problemas). Búsqueda combinada (palabras + significado), `--sin-memoria`:

| | Reglas @1 | @3 | @5 | @8 | Evid. | Oficina @1 | @3 | @5 | @8 | Evid. |
|---|---|---|---|---|---|---|---|---|---|---|
| 24-nov, solo palabras | — | — | — | — | — | — | — | 66,7 | — | 64,8 |
| + títulos, plazos, vocabulario propio, vectores rehechos | 67,4 | — | 92,4 | 95,7 | 89,4 | 51,9 | — | 94,4 | 98,1 | 98,1 |
| **+ el trámite que la pregunta no nombra baja (×0,85)** | **68,5** | **91,3** | **93,5** | **94,6** | **91,5** | **55,6** | **85,2** | **98,1** | **100** | **94,4** |

**Lo que se cambió el 25-nov, y por qué:**

- **Pregunta general → regla general.** Para la pregunta de Henry salían primero la publicidad
  del *saneamiento*, la objeción de la *superposición* y la *refundición*; la regla general quedaba
  7.ª y el modelo contestó «veinte (20) días hábiles» (el plazo para objetar una superposición).
  Ahora un artículo cuyo título, apartado o sección nombra un trámite que la pregunta no nombra
  baja ×0,85 (y el título de la norma cuenta si nombra UN solo trámite). Medidas 1,0 / 0,85 / 0,8
  / 0,7, con y sin sección: 0,85 con sección es la mejor. Coste: P035 sale del top 8 (su artículo
  está en el capítulo de la regularización parcelaria).
- **La memoria se reforzaba con sus propios errores.** El modelo citó el Art. 172 una vez y desde
  entonces la memoria lo ponía PRIMERO para esa pregunta (+0,24 del máximo). Lo implícito (nadie
  lo valoró) ya solo cuenta si lo citan **dos preguntas distintas**; repetir la misma no suma.
- **Al modelo se le dice de qué trámite es cada fragmento** (la sección va en la cabecera) y que
  van ordenados por pertinencia. Sin trámite nombrado, debe dar la regla general.
- **La caché repetía respuestas malas tras un arreglo**: su versión ya incluye el contenido de
  `buscador.py`, `responder.py` y `texto.py`, no un número que hay que acordarse de subir.
- **Pantalla**: un «No encuentro» deja las citas literales desplegadas, y una redacción cortada
  por abandono queda «cancelada» (antes salía vacía y con el aviso «no cita ninguna fuente»).

**Evidencia de la oficina 98,1 → 94,4 %:** en O036 y O037 el dato («dominio público», «a quiénes
ha comunicado») llegaba desde el artículo del *saneamiento* (Art. 142), no desde el esperado
(Art. 78). Ahora delante van la DT-2023-009 Arts. 9/12/13 (notificación a colindantes, al
Ministerio, al Estado) y el Art. 121 (acta de hitos): pertinentes, pero sin esa frase literal.

**Comprobado en la pantalla real** (Gemma 3 4B, GTX 1050):

| Pregunta | Respuesta | Fuente |
|---|---|---|
| Que tiempo tengo para realizar el levantamiento luego de notificar | al menos diez (10) días calendarios antes del inicio | Res. 789 Art. 78 |
| cuantos dias antes tengo que avisarle a los colindantes que voy a medir | diez (10) días calendarios | Res. 789 Art. 142 / 78 |
| con cuanta anticipacion se cita a los colindantes en un saneamiento | diez (10) días calendarios | Res. 789 Art. 142 |
| si hay una superposicion cuanto tiempo tengo para objetar | veinte (20) días hábiles desde la notificación | Res. 789 Art. 172 |

## 3. Los modelos locales como redactores (24-nov)

`comparar_modelos.py`: mismas 12 preguntas con dato clave + 3 ajenas, mismos artículos de
entrada para todos; GPU GTX 1050 (Vulkan).

| Modelo | s/resp. | Cifra correcta | Cita correcta | Sin avisos | Rechaza lo ajeno |
|---|---|---|---|---|---|
| LFM2 1.2B RAG (Liquid AI, 0,7 GB) | 4,4 | 6/12 | 10/12 | 10/12 | 0/3 |
| Granite 4.0 H-Micro (IBM, 1,9 GB) | 9,8 | 11/12 | 9/12 | 9/12 | 2/3 |
| **Gemma 3 4B (Google, 2,5 GB)** | 11,7 | 10/12 | **11/12** | **12/12** | 2/3 |
| Qwen3 4B (Alibaba, 2,5 GB) | 10,6 | 9/12 | 10/12 | 12/12 | 2/3 |

Leídas una a una: el LFM2 **inventa** (un «Decreto 108-05» para Colombia, el IBI español como
impuesto de la DGII); Granite escribe números de artículo pese a la instrucción; Gemma y Qwen
citan limpio. **Elegido: Gemma 3 4B.**

### Solo con procesador (un PC sin tarjeta gráfica)

Gemma 3 4B con `--device none` en el mismo i5-8300H (registro de llama.cpp):
**lee ~37 tokens/s y escribe ~8 tokens/s → unos 40 s por respuesta redactada**
(1.000-1.300 tokens de artículos + 30-120 de respuesta). La respuesta con citas literales
sale igual al instante; la redacción llega después, escribiéndose en pantalla. Con tarjeta
gráfica (GTX 1050) son ~12 s. En el servidor de la oficina, una tarjeta dedicada modesta
reduce el tiempo a la tercera parte.

## 3c. Revisión de código independiente (24-nov-2026)

Un agente que no vio el razonamiento de quien construyó el programa lo revisó entero y
encontró **15 problemas reales**, todos corregidos y cada uno con su prueba en
`pruebas/test_revision.py` (80 pruebas en total entonces; 85 al 25-nov):

| # | Gravedad | Problema | Corrección |
|---|---|---|---|
| 1 | Alta | «Se deroga el párrafo II del artículo 54 de la Res. 787» marcaba derogada la norma ENTERA; también «no deroga» | Solo derogación total, afirmativa y en la misma frase; nunca pisa una vigencia que decidió una persona (queda como aviso) |
| 2 | Alta | Una norma republicada se anunciaba, pero se seguía usando el PDF viejo | El PDF nuevo sustituye al que usa el corpus (el viejo queda como `.anterior`) |
| 3 | Alta | `GET /\\10.9.9.9\c\x.js` abría una ruta de red y enviaba la autenticación de Windows | Solo se sirven por nombre los archivos de `web/` |
| 4 | Alta | `Content-Length: -1` leía sin límite; conexiones mudas retenían hilos | 413 y `timeout = 30` |
| 5 | Media | Preguntas abandonadas seguían ocupando el modelo; cola sin tope; posición mal calculada | Se cancela si la pantalla deja de preguntar 20 s; tope de 20; posición real |
| 6 | Media | Cualquier error HTTP o tiempo agotado se tomaba por «la GPU cayó» → todo a CPU | Solo si el proceso del modelo murió de verdad; historial recortado |
| 7 | Media | La caché guardaba respuestas que dependían de la conversación anterior | No van a la caché; la pantalla no manda como historial lo de otro día |
| 8 | Media | El actualizador exigía la hora exacta y sin internet se daba por hecho | Corre en cuanto toca; sin internet es un fallo y se reintenta |
| 9 | Media | Archivos escritos a medias tumbaban el arranque; vectores dañados pasaban por buenos | Escritura atómica; validación de tamaño; vectores opcionales de verdad |
| 10 | Media | Disco lleno → 500 al preguntar | Aprender es opcional: se registra y se sigue |
| 11 | Media | Dos servidores podían escuchar en el mismo puerto (Windows) | Puerto en exclusiva |
| 12 | Media | Si se mataba el `.exe`, el servidor quedaba vivo; dependía de cómo cerrara Edge | Job Object en el lanzador; el servidor se apaga solo sin pantallas; `/api/ping` |
| 13 | Baja | Una pregunta podía esperar minutos al modelo de embeddings | No espera: máx. 5 s y, si no está, solo palabras |
| 14 | Baja | 100.000 preguntas = 1,4 GB de RAM; registros sin rotar | Solo lo ligero en RAM (respuesta en disco); rotación de registros |
| 15 | Baja | llama-server aceptaba peticiones de cualquier página web del PC | Clave aleatoria por arranque; la API solo acepta JSON |

## 4. El actualizador, contra ri.gob.do de verdad (24-nov-2026)

La primera vuelta real pasó el corpus de **34 a 75 normas** (2.068 → 4.346 artículos):
formularios oficiales de la DNMC (acta de hitos, informe técnico, carta de conformidad,
informe de inspección), leyes de la base legal del RI (189-11, 368-22, 140-15, 5038, 82-25,
64-00 y 202-04 —que las Regionales citan al observar—…), las DT de la DNRT de 2023 con su
vigencia resuelta por **cadena de derogaciones expresas**, y 11 borradores en consulta pública
aislados de las respuestas normales.

Y destapó **seis reglas mal supuestas**, todas corregidas con su prueba: los borradores se
reconocen por el documento y no por la página que los enlaza; el título no puede ser el texto
del enlace («Ver aquí»); las URL llevan tildes; los identificadores se escriben de tres
maneras («DNRT-DT-2023001»); un borrador no deroga nada; y lo que el RI publica como su base
legal es de la materia por definición.

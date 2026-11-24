# Chatbot Mensura — plan de trabajo

> Asistente de preguntas y respuestas sobre la normativa inmobiliaria y de mensura de la
> República Dominicana. Responde **solo con lo que dice la norma**, citando norma, artículo
> y enlace al PDF oficial, y dice «no lo encuentro» antes que inventar.
>
> Plan redactado el 24-nov-2026 tras evaluar el Revisor X. Se actualiza al cerrar cada fase.

---

## 1. De dónde sale el conocimiento (evaluación del Revisor X)

| Fuente en el Revisor X | Qué aporta al chatbot | Estado |
|---|---|---|
| `conocimiento/normativa/*.pdf` + `CATALOGO.yaml` | **El texto oficial**: 34 normas (23 vigentes, 9 derogadas, 2 por confirmar), con su vigencia y quién reformó qué | Base del corpus |
| `datos/indice_normativo.db` (M5) | 2.613 fragmentos ya troceados por artículo | Se **reconstruye mejor** (ver §3), no se copia |
| `reglas/*.yaml`, `reglas/tramites/*.yaml` | **~700 citas literales verificadas** contra el PDF: tolerancias, plazos, documentos por trámite, planos, publicidad | Fichas prácticas + **la batería de evaluación** |
| `reglas/vocabulario_normativo.yaml` | Puente entre cómo habla el agrimensor y cómo escribe el legislador («tolerancia» → «variación») | Se reusa en el buscador |
| `reglas/jerarquia_normativa.yaml` | Qué norma manda: rango > año > derogación expresa | Se reusa en la respuesta |
| `docs/04_GLOSARIO.md` | Términos del dominio | Fichas de definición |

### Debilidades encontradas al evaluarlo (y qué se hace con cada una)

1. **Falta la Ley 108-05**, la ley madre (su URL figura «por_confirmar» en el catálogo), y su
   reforma, la **Ley 51-07**. Sin ellas el chatbot no puede responder nada de saneamiento
   judicial, litis, Tribunales de Tierras o principios del sistema Torrens. → Fase 1: buscarlas
   en la fuente oficial e incorporarlas.
2. **Los encabezados de página se cuelan en el texto** («1 Resolución núm. 82-2025 del Pleno…»
   repetido en cada hoja) y hay espacios duros (`\xa0`). En un informe da igual; en un chat
   ensucia la cita y la búsqueda. → El corpus nuevo los detecta y los quita.
3. **Un párrafo suelto pierde su artículo**: «Párrafo II. En caso de…» sin decir de qué artículo
   ni de qué trata. → Cada trozo lleva la cabecera de su artículo; al responder se le da al
   modelo el artículo ENTERO («recuperar pequeño, leer grande»).
4. **Los artículos reformados**: el PDF de la RES-790-2022 trae el texto de 2022 de los
   Arts. 5, 6, 8, 9 y 12, que la RES-82-2025 cambió. El Revisor X lo avisa; el chatbot además
   **pone el texto nuevo delante** y el viejo como histórico.
5. **Sin raíz de palabra en la búsqueda** (FTS5 `unicode61` no reduce «deslindes» a
   «deslind»). → Buscador propio BM25 con lematizador Snowball del español.
6. **Cuota de IA compartida**: el plan gratuito de Gemini da 20 peticiones/día por modelo
   (~160 con la cadena de 8), y ya lo usan el Vectorizador y el Revisor X. Un chat para 35 PCs
   puede agotarlo. → **Resuelto de raíz**: Henry decidió un modelo LOCAL (§5), sin cuota.
7. **El proxy del portal no deja pasar un *streaming***: lee la respuesta entera y corta a los
   30 s. → La respuesta se genera en un hilo y la pantalla la pide a trozos (patrón §9b de la
   plataforma), que pasa por cualquier proxy.

---

## 2. Arquitectura

```
 pregunta ─► buscador (BM25 + lematizador + vocabulario + identificadores de norma)
               │  · solo normas vigentes, salvo que se pregunte por historia
               │  · artículo reformado → se trae también el texto que lo reforma
               ▼
          8 fragmentos numerados [1]…[8], cada uno con su artículo entero
               ▼
          modelo LOCAL (llama.cpp + Gemma 3 4B)  ──si no arranca──►  modo «solo citas»
               │  instrucciones: responder SOLO desde los fragmentos, citar [n],
               │  decir «no consta» si no está, avisar de reformas y derogaciones
               ▼
          verificador de la respuesta
               · cada [n] existe · cada «Art. N» citado está entre los fragmentos
               · cada cifra (plazos, %, metros) aparece en lo citado
               ▼
          pantalla: texto que se va escribiendo + chips de cita → artículo literal y PDF
```

**Sin dependencias para funcionar**: el servidor, el buscador y la llamada al modelo usan solo
la biblioteca estándar de Python. `pymupdf` y `pyyaml` hacen falta únicamente para
**reconstruir** el corpus desde los PDF; el corpus generado va en el repositorio.

### API (contrato)

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/api/preguntar` | `{pregunta, historial, modo}` → `{id, fuentes[], modo, desde_cache}`. Busca al instante y deja la redacción en un hilo |
| GET | `/api/respuesta/<id>?desde=N` | `{texto, n, estado, final?}` — el texto nuevo desde el carácter N |
| GET | `/api/articulo?norma=&articulo=` | El artículo literal, su vigencia y el enlace al PDF |
| GET | `/api/normas` | El catálogo con su estado |
| GET | `/api/sugerencias` | Preguntas de arranque |
| POST | `/api/valorar` | 👍/👎 y comentario → `DATOS/valoraciones.jsonl` (mejora continua) |
| GET | `/api/salud` | Estado del corpus y de la IA |

---

## 3. Fases

| # | Fase | Quién | Criterio de «hecho» |
|---|---|---|---|
| 0 | Evaluar el Revisor X y planificar | Opus | Este archivo |
| 1 | **Corpus**: PDF → artículos limpios con página, título, vigencia y reformas; fichas desde los YAML; Ley 108-05 | Opus (motor) · Sonnet (buscar fuentes oficiales) | 0 encabezados de página en el corpus; cada norma vigente con artículos; prueba de los 5 artículos reformados |
| 2 | **Buscador** BM25 + lematizador + vocabulario | Opus | Acierto@5 medido sobre la batería |
| 3 | **Respuesta**: prompt, relevo de Gemini, verificador de citas, caché, modo sin IA | Opus | Pruebas sin red + prueba real contra la API |
| 4 | **Pantalla** de chat con el sistema de diseño `lexgeo.css` | Sonnet (con contrato cerrado) → revisión Opus en el navegador | Funciona por el puerto propio y bajo un prefijo de proxy |
| 5 | **Batería de evaluación**: ~90 preguntas con su artículo esperado, sacadas de las citas verificadas de los YAML | Sonnet → muestreo y corrección Opus | Cada pregunta con cita verificada; 10 fuera de alcance |
| 6 | **Medir y optimizar**: acierto de búsqueda, citas inventadas, cifras sin respaldo; ajustar vocabulario y pesos | Opus | Informe en `evaluacion/RESULTADOS.md` |
| 7 | **GitHub**: limpieza de datos de clientes, `.gitignore`, README, repositorio privado | Opus | Repositorio subido sin claves ni datos de clientes |

### Reparto de modelos (pedido por Henry: lo sencillo a modelos que gastan menos)

- **Opus**: diseño, corpus, buscador, verificador, prompt, revisión de todo lo delegado.
- **Sonnet**: buscar fuentes oficiales en la web, redactar la batería de preguntas a partir de
  las citas ya verificadas, maquetar la pantalla contra un contrato cerrado.
- **Haiku**: barridos mecánicos (buscar datos de clientes antes de subir, inventarios).
- **Todo lo delegado se revisa**: la batería se contrasta pregunta por pregunta contra el
  corpus con un script; la pantalla se prueba en el navegador.

---

## 4. Riesgos y cómo se cubren

| Riesgo | Cobertura |
|---|---|
| El modelo inventa un artículo o una cifra | Verificador posterior: cita fuera de los fragmentos o cifra que no aparece → aviso visible en la respuesta |
| Citar una norma derogada como vigente | La vigencia sale de `CATALOGO.yaml`; las derogadas no se buscan salvo pregunta histórica, y si salen van marcadas |
| Citar el texto viejo de un artículo reformado | Texto vigente delante, aviso explícito, prueba dedicada |
| Se agota la cuota de IA | Caché de respuestas + modo «solo citas» que siempre responde con los artículos |
| Datos de clientes en GitHub | Los oficios observados y expedientes **no entran** al corpus; barrido antes de subir; repositorio privado |
| La norma cambia y el chatbot no se entera | `herramientas/sincronizar_desde_revisor.py`: el catálogo del Revisor X es la fuente; un cambio allí se trae aquí con un comando |

---

## 5. Decisiones de Henry durante la construcción (24-nov-2026)

| Decisión | Consecuencia en el diseño |
|---|---|
| **Todo dentro de la carpeta**, sin recurrir a otra | Python 3.14 en `runtime/`, PyMuPDF y PyYAML en `libs/`, llama.cpp y modelos en `motor_local/`, PDF en `corpus/pdf/`. El Revisor X pasa de «sitio del que se lee» a «fuente de la que se importa» |
| **Sin clave de Gemini**: «que todo lo procese él mismo» | Redactor LOCAL con llama.cpp. Se compararon cuatro modelos ligeros (§6); el riesgo de §1.6 (cuota) desaparece |
| **Que entienda la pregunta la haga como la haga** | Búsqueda por significado con EmbeddingGemma local, fusionada con la de palabras |
| **Que aprenda con el uso** | `memoria.py`: lo citado y lo valorado empuja los artículos en preguntas parecidas; caché de respuestas |
| **Que se actualice solo** | `actualizador.py`: semanal desde ri.gob.do, derogaciones expresas leídas del texto, recarga en caliente |
| Descargas de ri.gob.do autorizadas | Ley 108-05 consolidada incorporada; actualización semanal activa |
| **Repositorio público** | Fuera de git: `datos/` (preguntas reales), `config.json`, PDF, runtime y modelos (se bajan con `INSTALAR.bat`) |

## 6. Los modelos locales, medidos (no supuestos)

Misma pregunta, mismos artículos de entrada, GTX 1050 de 4 GB (`evaluacion/comparar_modelos.py`):

| Modelo | s/resp. | Cifra correcta | Cita correcta | Sin avisos | Rechaza lo ajeno |
|---|---|---|---|---|---|
| LFM2 1.2B RAG (Liquid) | 4,4 | 6/12 | 10/12 | 10/12 | 0/3 — inventa |
| Granite 4.0 H-Micro (IBM) | 9,8 | 11/12 | 9/12 | 9/12 | 2/3 |
| **Gemma 3 4B (Google)** — elegido | 11,7 | 10/12 | **11/12** | **12/12** | 2/3 |
| Qwen3 4B | 10,6 | 9/12 | 10/12 | 12/12 | 2/3 |

Y lo que ningún modelo pequeño hace bien —decir «no lo encuentro»— lo decide el programa con
la cobertura de la pregunta (umbral calibrado con la batería).

## 7. Lo que queda abierto

1. **Integrarlo en el portal** como herramienta n.º 12 (`/chatbot-mensura`, tipo «proceso»).
   El proxy del portal no deja pasar *streaming*, y por eso la pantalla ya pide la redacción a
   trozos: está listo para ir detrás del proxy.
2. **El servidor de la oficina**: si no tiene tarjeta gráfica, la redacción irá por el
   procesador (medido en `evaluacion/RESULTADOS.md`). La respuesta con citas sale igual al
   instante.

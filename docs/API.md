# API del Chatbot Mensura (contrato)

Todas las rutas son RELATIVAS a donde se sirva la página: el chatbot corre suelto en
`http://<pc>:8013/` y también detrás del portal en `http://<servidor>/chatbot-mensura/`.
La pantalla calcula la base con `location.pathname` y nunca usa rutas absolutas `/api/...`.

**Por qué no hay *streaming*:** el proxy del portal lee la respuesta entera antes de
reenviarla y corta a los 30 s. La redacción corre en un hilo del servidor y la pantalla la va
pidiendo a trozos (`/api/respuesta/<id>?desde=N`). Funciona igual con o sin proxy.

**Dos respuestas por pregunta:**
1. `rapida` — sale al instante en `/api/preguntar`: frases literales de los artículos, con
   sus citas. Nunca inventa. Es la respuesta completa cuando no hay modelo local.
2. La **redacción** del modelo local — llega después, a trozos, y se escribe en pantalla.

---

## POST `api/preguntar`

```json
{ "pregunta": "¿cuánto puede variar el área en un deslinde?",
  "historial": [ { "pregunta": "...", "respuesta": "..." } ] }
```
`historial`: las 3 últimas vueltas de la conversación, como mucho (para entender «¿y en
saneamiento?»).

Respuesta 200:
```json
{
  "id": "k3f9a2",
  "entendido": { "normas": ["RES-790-2022"], "articulos": ["Art. 13"],
                 "terminos": ["variacion", "superficie", "deslinde"], "historica": false },
  "fuentes": [
    { "n": 1, "norma": "RES-790-2022",
      "norma_titulo": "Reglamento para la Regularización Parcelaria y el Deslinde",
      "articulo": "Art. 13", "apartado": "", "titulo": "De las especificaciones técnicas",
      "seccion": "Capítulo III · Del deslinde",
      "estado": "vigente",               // vigente | por_confirmar | derogada | borrador
      "aviso": "",                       // p. ej. "Texto anterior: lo reformó RES-82-2025, Art. 1"
      "extracto": "… sólo se admite una variación de hasta un cinco por ciento (5%) …",
      "url": "https://ri.gob.do/…/Resolucion_790-2022.pdf#page=24", "pagina": 24,
      "motivo": "" }                     // "nombrado en la pregunta" | "aprendido…" | ""
  ],
  "rapida": { "texto": "Texto con **negritas**, listas y citas [1][2].",
              "confianza": "alta" },     // alta | media | baja
  "redaccion": "en_cola",                // en_cola | escribiendo | no_disponible | desde_cache
                                         // | innecesaria (pregunta ajena: basta la rápida)
  "cache": null                          // o { "texto": "...", "avisos": [] } si ya se respondió antes
}
```
Errores: 400 `{ "error": "La pregunta está vacía." }`.

## GET `api/respuesta/<id>?desde=<n>`

```json
{ "estado": "escribiendo",   // en_cola | escribiendo | listo | error | no_disponible
  "posicion": 0,             // en_cola: cuántas preguntas hay delante
  "texto": "trozo nuevo desde el carácter n",
  "n": 418,                  // largo total escrito hasta ahora
  "final": null }
```
Cuando `estado` es `listo`, `final` trae:
```json
{ "texto": "respuesta completa", "avisos": ["La cifra 20 no aparece en los artículos citados."],
  "citas": [1, 3], "modelo": "LFM2-1.2B-RAG", "segundos": 11.4 }
```
Si es `error` o `no_disponible`, la pantalla se queda con `rapida` y lo dice en una línea.
Sondeo recomendado: cada 300 ms mientras `escribiendo`, cada 1 s mientras `en_cola`.
Un id desconocido o caducado (30 min) → 404.

## POST `api/valorar`
`{ "id": "k3f9a2", "util": true, "comentario": "opcional" }` → `{ "ok": true }`.
Es lo que hace que el chatbot aprenda: lo bien valorado empuja esos artículos en preguntas
parecidas; lo mal valorado los aparta y queda en la lista de pendientes.

## GET `api/articulo?norma=RES-790-2022&articulo=Art.%2013&apartado=`
```json
{ "norma": "...", "norma_titulo": "...", "articulo": "Art. 13", "apartado": "",
  "titulo": "...", "seccion": "...", "estado": "vigente", "aviso": "",
  "texto": "texto literal completo", "url": "...#page=24", "pagina": 24,
  "anterior": { "articulo": "Art. 12", "apartado": "" },
  "siguiente": { "articulo": "Art. 14", "apartado": "" } }
```

## GET `api/salud`
```json
{ "estado": "ok",
  "corpus": { "normas": 36, "vigentes": 24, "unidades": 2588, "version": "2026-11-24" },
  "modelo": { "disponible": true, "nombre": "LFM2-1.2B-RAG", "dispositivo": "GPU · NVIDIA GeForce GTX 1050" },
  "actualizacion": { "ultima": "2026-11-24T03:00:00", "proxima": "2026-12-01T03:00:00",
                     "novedades": 0 } }
```

## GET `api/sugerencias` → `{ "preguntas": ["...", "..."] }` (6 preguntas de arranque)
## GET `api/normas` → `{ "normas": [ { "id", "titulo", "estado", "grupo", "url", "articulos" } ] }`
## GET `api/novedades` → `{ "novedades": [ { "fecha": "2026-11-24", "texto": "..." } ] }`

## Formato del texto (`rapida.texto`, `final.texto`)
Markdown mínimo: párrafos separados por línea en blanco, `**negrita**`, listas con `- ` o
`1. `, y citas `[n]` (también `[1][3]` o `[1, 3]`). La pantalla escapa el HTML ANTES de
aplicar el formato y convierte cada `[n]` en un botón que abre la fuente n.

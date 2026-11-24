# Chatbot Mensura

Asistente de preguntas y respuestas sobre la **normativa inmobiliaria y de mensura catastral
de la República Dominicana**: la Ley 108-05 de Registro Inmobiliario (con la Ley 51-07), los
reglamentos de la Suprema Corte de Justicia y las disposiciones técnicas de Mensuras
Catastrales y de Registro de Títulos.

Responde **solo con lo que dice la norma**, cita el artículo exacto con un enlace a la página
del PDF oficial y dice «no lo encuentro» antes que inventar.

- **Funciona sin internet y sin claves de ningún servicio**: el modelo de lenguaje corre en el
  propio PC (llama.cpp), y usa la tarjeta gráfica si la hay o el procesador si no.
- **Entiende la pregunta la hagas como la hagas**: búsqueda por palabras con raíces del español
  y vocabulario de oficina («tolerancia» → «variación»), más búsqueda por significado.
- **Aprende con el uso**: lo que se responde bien (👍) sube en preguntas parecidas; lo que se
  responde mal (👎) baja y queda en una lista para revisar.
- **Se actualiza solo**: cada semana revisa ri.gob.do, incorpora las normas nuevas, aplica las
  derogaciones expresas y recarga sin parar el servicio.

## Usarlo

```
INSTALAR.bat           una vez: baja Python, llama.cpp y los modelos DENTRO de esta carpeta,
                       y compila el programa de Windows
Chatbot Mensura.exe    doble clic: se abre el chat en su propia ventana; al cerrarla, se apaga todo
```

Para llevarlo a otro PC basta copiar la carpeta entera (todo va dentro, ~3 GB por el modelo).
Para darlo a toda la oficina desde un servidor: `INICIAR.bat` (escucha en la red, puerto 8013)
o `"Chatbot Mensura.exe" --red`, y desde los demás PC `http://<nombre-o-IP>:8013/`.

## Aprovecha el equipo que tenga

Al arrancar mide el PC y reparte el trabajo: el modelo en la **tarjeta gráfica** si cabe; si
no cabe entero, **parte en la tarjeta y parte en el procesador**; sin tarjeta, en el
procesador con núcleos reales para escribir y todos los hilos para leer. La búsqueda por
significado va al procesador mientras el redactor usa la tarjeta. Deja memoria de vídeo libre
para otros programas (AutoCAD), pasa solo al procesador si la tarjeta falla, y tras 20 minutos
sin preguntas suelta la tarjeta y la RAM. Ajustes en `config.ejemplo.json`.

## Cómo responde

1. **Busca** los artículos que responden (palabras + significado + lo aprendido), solo entre
   normas vigentes salvo que se pregunte por historia, y trae el texto que reforma a cualquier
   artículo reformado.
2. **Responde al instante con citas literales**: las frases exactas del artículo, entre
   comillas, con su referencia. Nunca inventa nada.
3. **Redacta** después una explicación con el modelo local, que solo puede usar esos artículos
   y cita con [n].
4. **Verifica** la redacción: cada cita tiene que existir, cada artículo mencionado tiene que
   estar entre las fuentes y **cada cifra tiene que aparecer en lo citado**. Lo que no cuadra
   sale como aviso visible.

## Qué hay en la carpeta

| Carpeta | Qué es |
|---|---|
| `chatbot/` | El programa (Python, solo biblioteca estándar para funcionar) |
| `web/` | La pantalla de chat |
| `corpus/` | El catálogo de normas y el corpus ya procesado (`generado/`) |
| `evaluacion/` | Batería de 106 preguntas y los scripts que miden buscador y modelos |
| `pruebas/` | Pruebas automáticas (`python -m pytest pruebas`) |
| `herramientas/` | Instalar, y traer normas del Revisor X |
| `escritorio/` | El programa de Windows (C#) y su icono; `construir.bat` lo compila |
| `docs/` | Contrato de la API |
| `runtime/`, `libs/`, `motor_local/` | Python, librerías y modelos: los pone `INSTALAR.bat` |

## Medido

Ver `evaluacion/RESULTADOS.md`.

## Aviso

Las respuestas se basan en la normativa oficial cargada y pueden no reflejar cambios
posteriores a la última actualización. No sustituyen la lectura del texto oficial ni el
criterio profesional.

Los textos normativos son documentos públicos oficiales publicados por el Poder Judicial y el
Registro Inmobiliario de la República Dominicana (ri.gob.do).

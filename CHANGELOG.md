# CHANGELOG — Social Media Clipper

Todos los cambios notables del proyecto se documentan en este archivo.

## [v0.8.0] - Zooms reales de tu referencia y edición más profesional (2026-11-23)

- `studio/zoom_detect.py`: mide los zooms reales de cámara de la referencia, cuadro a cuadro (ORB + escala afín con RANSAC, sin cruzar cortes de escena). Distingue zooms de golpe (punch) y progresivos (ramp), con intensidad, duración y zooms por minuto. Un paneo no cuenta como zoom.
- `learn.py`: el aprendizaje guarda la medición (`profile.zoom`). El estilo recibe `zoom_per_min`, `zoom_amount`, `zoom_hold_s` y `zoom_kind`, y el desarme (EDL) usa los zooms medidos en vez de la pista de la IA de visión. Botón «🔍 Medir zooms» en Mis estilos para estilos ya aprendidos, sin repetir todo el análisis.
- Sin zoom alterno mecánico cuando el estilo trae zooms medidos. `reference_rules.place_zooms` los pone en los momentos de énfasis de tu video (exclamación o pregunta, palabra fuerte, pico de voz), con la intensidad y duración de tu referencia, sostenidos hasta el fin de la frase, sin seguirse y respetando la tasa por minuto. Si tu referencia no usa zooms, no se pone ninguno (ni el empuje lento).
- `brief.py`: los límites de videos largos ya no recortan los zooms medidos.
- Director y agente: criterio tipo OpusClip en el prompt (gancho en 3 s, evento en la palabra clave, menos es más). En código: mínimo 3 s entre textos y 4 s entre sonidos; los sonidos de énfasis tienen prioridad.
- Rostro: el seguimiento también mide la altura de la cara y los zooms se centran en ella. Las tarjetas eligen el lado contrario a la cara en cada momento, no según una posición fija.
- `studio/precheck.py`: revisión antes de renderizar. Avisa de inicio en negro, silencio inicial de más de 0.5 s, textos que se pisan y duración lejos de la pedida. Arregla en la copia que se renderiza los textos vacíos, ilegibles o que se salen, y los recursos que pasan del final. Se muestra en el reporte de calidad.
- Aviso claro cuando Ollama o llama.cpp no responde, o falta el modelo: la edición se hace sin IA y lo dice.
- Pruebas: `test_zoom_detect.py` (7), `test_precheck.py` (2). Suite: 175 pasan.

## [v0.7.3] - Biblioteca en tiempo real, DaVinci Resolve y máxima calidad (2026-11-23)

- `library_sync.py`: la Biblioteca, Stream y Clips reflejan las carpetas autorizadas (con subcarpetas). Los videos nuevos se agregan solos, esperando a que el archivo deje de grabarse, y los borrados o movidos ya no se muestran (sus clips y proyectos se conservan). Se refresca cada 5 s y al volver a la ventana.
- Studio → botón «DaVinci Resolve» (`studio/resolve_export.py`): descarga un .zip con FCPXML (toma con cortes y zoom, recursos con tamaño y posición, textos editables, música y efectos con volumen), subtítulos SRT e instrucciones.
- Exportación final en máxima calidad: videos horizontales a 1440p (YouTube los comprime con VP9/AV1), 60 fps si la grabación es de 60, NVENC p7 hq cq 17 con AQ, audio AAC 320k. Clips y verticales a 1080×1920 con la misma calidad de compresión.
- Vista en vivo: la copia de vista previa lleva un cuadro clave cada 0.5 s a 30 fps, y el reproductor espera a que la siguiente toma esté lista (antes se veían partes repetidas).
- Pruebas: `test_library_sync.py`, `test_resolve_export.py`. Suite: 166 pasan.

## [v0.7.2] - Duración final en el brief (2026-11-22)

- Brief de Studio (proyecto nuevo y «Auto-editar de nuevo»): **Duración final**. Opciones: «Todo lo útil», 5/8/10/15/20 min en videos largos, 30/45/60/90 s en shorts, u «Otra» en minutos.
- `studio/condense.py`: después de quitar silencios agrupa las tomas en bloques de 20-45 s que terminan en frase. La IA califica cada bloque (gancho, dato clave y clímax arriba; esperas y relleno abajo), combinado con la energía y el ritmo de voz. Se queda con los mejores en su orden hasta la duración (±5 %). Nunca alarga.
- `autoedit.py`: el reporte conserva las reglas de la referencia y la duración aunque corra la IA editora (antes se sobrescribían). El «Reporte de edición» muestra «Duración: antes → después».
- Pruebas: `tests/test_condense.py` (4). Suite: 162 pasan.

## [v0.7.1] - Juez estricto con retroalimentación iterativa (2026-11-20)

- `studio/strict_judge.py`: nuevo juez estricto que compara la edición contra la referencia de estilo aprendido
  (métricas + contenido vs transcripción + visión opcional) y, si no aprueba (≥ 90 %), aplica correcciones
  específicas (quitar recurso sin soporte, mover tarjeta que tapa el rostro, acortar texto inventado, ajustar
  parámetros de estilo) y vuelve a juzgar, hasta 8 rondas.
- `studio/generative.py`: al finalizar la edición generativa con `style_id`, pasa por `strict_judge.run_strict`
  antes de entregar el timeline. Umbral de aprobación elevado a 90 % cuando hay referencia de estilo.

## [v0.7.1] - Correcciones del proceso de edición (2026-11-21)

Diagnóstico con datos de las ediciones 120, 121 y 124: el juez de estilo se quedaba en 26–33 % porque el primer corte y el director decidían zooms, transiciones y subtítulos antes de consultar la referencia. Respaldo previo: `backups\2026-11-21_1115_antes_correcciones` (con `RESTAURAR.bat`).

- **Juez estricto arreglado** (`generative.py`, `strict_judge.py`): tronaba con `name 'st' is not defined` cada vez que había estilo aprendido. Además, `round` como variable tapaba la función `round()`, se borraba todo recurso a pantalla completa sin revisarlo, los memes se marcaban como "inventados" y los ajustes de parámetros no recolocaban nada. Ahora: meta 80 %, máximo 5 rondas, se detiene si deja de mejorar y recoloca los recursos ya encontrados cuando cambia un parámetro.
- **La referencia manda desde el inicio** (`reference_rules.py`, `autoedit.py`): antes del primer corte aplica zoom alterno, transiciones, subtítulos, memes, sonidos y música según tu referencia. Después del director pone topes (golpes de zoom y memes al ritmo de la referencia, los más fuertes y repartidos). La música se quita aunque falle el generativo. Si la referencia tiene 0 zooms, el generativo ya no pone 1.5 por minuto.
- **Recursos sin repetirse** (`generative.place_cards`): no repite ni encima los recursos que ya puso la IA editora (antes: la foto de la Xbox a pantalla completa + la misma en tarjeta + texto). Modo tarjeta o ventana pequeña y duración según la referencia.
- **Instrucciones primero** (`instructions_plan.py`): las instrucciones del brief se convierten en ajustes del proceso antes de editar. Solo los cambios puntuales van al paso final, y la IA ya no contesta "no se puede, el video es muy largo".
- **Diccionario de nombres** (`ai/vocab.py`, Configuración → Diccionario de nombres): pistas para Whisper (hotwords) y corrección de transcripciones existentes («Neva» → «Eneba»), en Studio y en clips. Aprende de lo que corriges a mano en los subtítulos. Arranca con: Eneba, GTA 6, Xbox Series S, Remote Play.
- **Texto legible** (`style.readable`, `render.build_ass`, `live.js`): si la letra y la caja del título tienen colores parecidos (blanco sobre blanco del estilo aprendido), la letra pasa a negro o blanco.
- **Modelos por tarea** (`db.role_model`): si "escritor" o "juez" apuntan al alias de llama.cpp con el motor en Ollama, se usa el modelo principal en vez de fallar.
- **Juez de estilo** (`style_judge`): los subtítulos se miden por el tiempo en que se habla (antes encendidos = 100 %).
- Pruebas: `tests/test_corrections.py` (12). Suite completa: 158 pasan.
- Simulación sobre el proyecto 26 (sin guardar): parecido 31.7 % → 45.7 %.

## [v0.7.2] - Edición exacta al estilo aprendido: desarme frame-accurate + bucle de agentes (2026-11-22)

### ¿Qué motivó el cambio?
Las ediciones seguían siendo mediocres e improvisadas: los estilos aprendidos se guardaban como tasas agregadas (cortes/min, recursos/min, sfx/min) sin decir QUÉ poner en cada segundo, y los agentes aplicaban la edición una sola vez sin volver a comparar con la referencia. Resultado: cortes, sonidos, textos y recursos en segundos distintos al del estilo de referencia.

### Cambios realizados
- `studio/learn.py`: desarme frame-accurate del video de referencia. Ahora `analyze()` guarda, además de las tasas, una `edl` con cada evento y su timestamp exacto: `cut` y `scene` (cortes de escena), `sound_events`/`sfx` (saltos de energía = candidatos a efecto de sonido), `caption_events` (frase real de subtítulo con inicio/fin y texto), `element_events` (overlay card/fullscreen/pip/meme, ubicación del hablante, fondo, animación) y `design_events` (layout, zoom, diseño de subtítulos, colores, fuente) con `t` de cada fotograma analizado. Nuevo `style_profile(style_id)` para leer el perfil completo con la `edl`.
- `studio/style_judge.py`: `edl_metrics(tl)` extrae del timeline los mismos eventos con timestamp; `compare_edl(ref_edl, mine_edl, dur)` alinea referencia vs edición por tipo y segundo (tolerancia 0,5 s), da nota 0–100 por tipo y genera **correcciones aplicables** (`add_cut`, `add_zoom`, `add_transition`, `add_caption`, `add_overlay_<mode>`, `add_sfx`, `add_music` con el segundo exacto). `edl_similarity()` combina la nota de tasas (60 %) con la nota de desarme frame-accurate (40 %).
- `studio/autoedit.py`: `edit_to_style_loop()` — bucle de agentes (máx. 5 rondas, umbral 88 %): reconstruye el timeline, lo compara con la `edl` del estilo de referencia, aplica las correcciones y repite hasta que la edición coincida con el estilo; guarda el mejor resultado. Se ejecuta al final de la auto-edición del Studio cuando hay `learned_style_id`.
- `studio/clip_project.py`: el mismo bucle EDL se aplica al render de clips cuando hay `style_id`, para que los clips cortos también copien la referencia al segundo exacto y no improvisen.
- `CHANGELOG.md`: esta entrada.

### Antes / después
| Escenario | Antes | Después |
|---|---|---|
| Estilo aprendido | Tasas agregadas (cortes/min, sfx/min) | Desarme frame-accurate con segundo exacto de cada corte, sonido, texto y recurso |
| Agentes de edición | Aplican una sola vez, no comparan con la referencia | Bucle iterativo hasta coincidir con el EDL del estilo (máx. 5 rondas) |
| A dónde aplica | Solo generativo (meta 80 %) | Studio (auto-edición) y Clips con IA |

### Pruebas
- Suite completa de backend: 158 pruebas pasan (`pytest -q`), incluidas `test_learn_multi`, `test_style_judge`, `test_studio`, `test_generative`, `test_clip_edit`, `test_api`.

---

- Respaldo previo: `backups\2026-11-20_1715_antes_llamacpp` (con `RESTAURAR.bat`).
- `ai/llm.py`: segundo motor `llamacpp` (API OpenAI de llama-server, JSON con esquema, visión por data URL, sin razonamiento). Se elige en Configuración → Motor de IA; Ollama sigue disponible.
- `ai/llamacpp_server.py`: arranca/detiene llama-server (solo el que lanzó la app), autoarranque al iniciar, log en `data\logs\llama-server.log`. Flags: ctx 32k, KV q4_0, flash attention, `--fit on`, MTP (`--spec-type draft-mtp`), mmproj.
- API `/ai/llamacpp` (config, start, stop, test de velocidad y visión). `EnginePanel.jsx` en Configuración.
- `tools/llamacpp/descargar.ps1` (llama.cpp b12597 CUDA 13.5 en `D:\llama.cpp`; modelo en `D:\models\Qwen3.6-35B-A3B-Q4-MTP`) y `probar.py`.
- Medido en RTX 5070 12 GB + 40 GB RAM: ~52-55 tok/s escribiendo, ~420 tok/s leyendo un prompt de 6.5k tokens, MTP acepta ~60 %, JSON con esquema OK, visión OK (2.3 s). VRAM 11 GB, RAM 16.7 GB.
## [v0.6.4] — Compositor de publicación por red (2026-11-19)

- `PublishComposer.jsx`: pestañas YouTube / TikTok / Instagram (al marcar una red se abre su pestaña); cada red con su propio texto editable por separado. Vista previa con el aspecto de cada red (YouTube: miniatura, título, descripción; Instagram/TikTok: cuenta, texto con hashtags resaltados). La confirmación «Así se publicará» usa la misma vista por red.
- Descripciones que crecen con el texto y botón «⤢ Ampliar» para editarlas a pantalla completa.
- Etiquetas de YouTube como chips (× quitar, Enter/coma agregar); hashtags de TikTok/Instagram como chips sin tocar el resto del texto.
- Aviso «Según internet, ojo con» recortado (antes podía mostrar un párrafo de transcripción).
- `publish/sources.py`: los textos para la IA salen de los subtítulos de la edición (con tus correcciones, p. ej. «Neva» → «Eneba»).

---

## [v0.6.3] — Mover libremente en la vista previa y corregir subtítulos (2026-11-19)

Aplica a todo el editor (videos largos y clips).

- **Mover y redimensionar sobre la vista previa** (`StudioEditor.jsx` → `MoveLayer`, `live.js` → `itemBox`, `freeTextStyle`): al seleccionar una tarjeta/ventana de recurso o un texto aparece una caja punteada sobre el video. Arrastrar = mover; esquina o rueda = tamaño; doble clic = posición automática. Si el elemento no está en pantalla, un botón lleva a su momento.
- **Textos con posición/tamaño libres en el render** (`render.py` → `build_ass`): `pos` (centro, fracción del cuadro) → `\an5\pos(x,y)`; `scale` → `\fs`. Se conservan al guardar.
- **Subtítulos editables** (`Timeline.jsx`, `Panels.jsx` → `CaptionEditor`, `timeline.py` → `apply_word_fixes`): clic en un subtítulo del timeline → corregir texto o quitarlo. Se guarda en `tl.word_fixes` en tiempo de la toma original, así sobrevive a recortes y re-ediciones; el tiempo se reparte igual.
- Inspector: «Volver a posición automática» y ayuda para arrastrar.
- **Arreglo crítico** (`live.js`): pantalla negra al salir del editor o pausar con tarjetas de recursos en la vista previa (`l.pause is not a function`): el motor intentaba pausar imágenes.
- **La IA del editor ya corrige subtítulos** (`agent.py`): nueva operación `fix_caption` (busca el subtítulo por su texto, tolerando errores, o por segundo) y lista de SUBTÍTULOS en el prompt. Antes no existía: decía «se corrigió» y no hacía nada o agregaba un texto. Regla de honestidad: si no aplicó cambios, el mensaje lo dice.
- **Textos que no se salen del cuadro** (`render.py` → `fit_text`, `live.js` → `fitText`): parte en líneas y baja el tamaño (hasta 45 %) para caber; mismo cálculo en vista previa y render.

### Pruebas
- `tests/test_editor_free.py`: corrección que sobrevive a recortes, quitar subtítulo, texto libre en el ASS.

---

## [v0.6.2] — Timeline: pista «Recursos IA» y pistas que crecen (2026-11-19)

- `Timeline.jsx`: nueva pista **Recursos IA** con todo lo que agrega la edición generativa (tarjetas 🃏, ventanas ▣, fondos 🖼 y textos 🔤 del fondo por tramos), con miniatura; se separa del B-roll tuyo y del contenido que ya trae el video. Se pueden mover, recortar y borrar. El contador muestra cuántos recursos de la IA hay.
- `tl.js`: `trackList`/`findItem`/`deleteItem` entienden los fondos (`chroma.bg_schedule`); `ensureBgIds` da id a fondos antiguos.
- `Panels.jsx` (Inspector): «Recurso de la IA» con tipo (tarjeta/pip/pantalla completa), posición, tamaño y rótulo; «Fondo de la IA» con inicio/fin, encuadre o texto.
- `styles.css`: al agrandar el panel del timeline, las pistas crecen para llenarlo (antes quedaban de 38 px y el resto vacío).
- `generative.py`: los fondos llevan `id` estable.

---

## [v0.6.1] — Edición generativa también en Clips con IA (2026-11-19)

### ¿Qué motivó el cambio?
La búsqueda de recursos en internet solo corría en la auto-edición del Studio; los clips verticales salían sin recursos.

### Cambios realizados
- `studio/clip_project.py`: nueva `_generative()` dentro de `build()` (lo usan el render directo del clip y «Editar en Studio»). Opción `generative` (por defecto sí). Contexto del clip (título, gancho, motivo) para planear solo lo que se dice; después, juez de sonido sobre los efectos.
- `studio/generative.py`: `place_cards(..., zone)` con zonas: `sides` (horizontal, lado contrario a la cara), `top` (clip vertical: arriba de la cabeza), `game` (clip de stream: sobre el juego). En clips: tarjetas de ≤ 3.5 s, rótulo en la propia tarjeta (sin textos extra que choquen con subtítulos) y sin zooms extra (los pone el director).
- `editor/render.py`: el render del clip guarda el resumen (recursos encontrados, nuevos, reutilizados, % de parecido).
- `ClipReview.jsx`: casilla «Recursos de internet (edición generativa)» y resumen en la lista de renders.

### Comparación antes / después
| Escenario | Antes | Después |
|---|---|---|
| Recursos en clips verticales | ❌ Ninguno | ✅ Tarjetas con logos/capturas/clips verificados |
| Varios clips del mismo video | — | ✅ El primero busca; los demás reutilizan el índice |

### Pruebas
- `test_generative.py`: zona vertical arriba, zona de juego, integración con el editor de clips.

---

## [v0.6] — Edición generativa, juez de estilo/sonido, miniaturas enlazadas y exportaciones separadas (2026-11-19)

### ¿Qué motivó el cambio?
- El proyecto «problemas con eneba» salió con solo recortes, una música que no venía al caso, 0 recursos y un fondo de chroma fijo sin relación con lo que se decía. Las instrucciones del usuario nunca se aplicaron (Ollama tardó y la app se rindió en silencio).
- La miniatura elegida no aparecía al publicar (se guardó para el proyecto y se buscaba por el render).
- Los videos editados en el Studio no aparecían en «Exportaciones» ni se podían usar en «Clips con IA».

### Cambios realizados
#### Edición generativa (recursos reales de internet)
- `backend/app/resources/` (nuevo: `planner.py`, `finder.py`, `store.py`) + `routers/resources.py` + página **Recursos**: planea qué mostrar en cada momento (logo, producto, captura web, noticia, clip, juego, texto clave), solo cosas que se dicen; busca en internet (ddgs; clips de 6 s con yt-dlp), verifica con visión (≥ 6/10) y guarda en `data/library/web` + tabla `resource_index` para reutilizar.
- `studio/generative.py` (nuevo): metas del estilo → plan + búsqueda una vez → colocación (fondo por tramos detrás de la cámara de OBS, o tarjetas animadas del lado contrario a la cara) → rótulos, zooms, whoosh suaves → juez de estilo hasta 4 rondas para llegar a 80 % → guarda los mejores ajustes por estilo (`style_tuning`).
- Render y vista en vivo: b-roll `mode` card/pip/full (marco, sombra, rotación, pop/slide/fade, Ken Burns, rótulo) y `chroma.bg_schedule` (fondo por tramos).
#### Juez de estilo y de sonido
- `studio/learn.py`: perfil `elements` (qué tipo de recursos usa la referencia, cada cuánto, textos, zooms) con visión sobre hasta 10 min.
- `studio/style_judge.py` (nuevo): similitud 0–100 contra la referencia con consejos; botón **🎯 Parecido al estilo** y reporte al terminar la auto-edición.
- `studio/sound_judge.py` (nuevo): quita efectos al azar o fuera de estilo, ajusta volumen por pico real, escucha el render (ebur128); botón **🎧 Juez de sonido**.
#### Chroma
- Recorte por tramo con zonas protegidas: se detecta el recuadro fijo de la cámara de OBS para que objetos verdes del cuarto no se vuelvan transparentes. Vista previa a 720p (antes 540p y recorte a media resolución).
#### Miniaturas y publicación
- `thumbs/store.py`: miniaturas enlazadas por «mismo video» (proyecto ↔ sus renders; clip ↔ sus renders). La elegida en «Miniaturas» aparece y se preselecciona al publicar; elegir una desmarca las demás del mismo video.
- `publish/sources.py`: corregido `NameError` (`p["id"]` no existía) que hacía tronar la publicación de videos del Studio — venía del cambio v0.5.
#### Exportaciones y Clips con IA
- `GET /api/studio/finals` y `POST /api/studio/renders/{rid}/to-library` (sin copiar el archivo; una sola vez).
- **Exportaciones**: pestañas separadas «Clips con IA» y «Editados en Studio» (publicar, descargar, hacer clips, abrir proyecto, eliminar).
- **Clips con IA**: lista separada «Originales» / «Editados en Studio»; los exportados del Studio se pueden usar para sacar clips.
#### `add_background` (v0.5) — corrección
- Modo `split`/`blur` ponía el gameplay como b-roll a pantalla completa, que en el render va encima de todo y **tapaba la cámara**. Ahora es una franja (`mode: pip`, sin marco): abajo en vertical, a la derecha en horizontal; ya no cambia `tl.layout`. En `full` no se encoge a la persona si su cámara de OBS está protegida.
#### Otros
- Instrucciones del usuario: timeout 900 s y reintento con prompt corto; sin música «la primera» al azar; consola sin avisos de SOCKS/ConnectionResetError.

### Comparación antes / después
| Escenario | Antes | Después |
|---|---|---|
| Recursos de lo que se dice | ❌ Solo biblioteca local (0 en el proyecto) | ✅ Buscados y verificados en internet, indexados |
| Parecerse a la referencia | ❌ Sin medir | ✅ Juez 0–100 % con meta 80 % y ajustes guardados |
| Efectos de sonido | ❌ Al azar | ✅ Con motivo, densidad de la referencia, volumen medido |
| Objeto verde dentro de la cámara | ❌ Se volvía transparente | ✅ Recuadro de cámara protegido |
| Miniatura elegida al publicar | ❌ No aparecía | ✅ Aparece y se preselecciona |
| Publicar video del Studio | ❌ NameError (v0.5) | ✅ Corregido |
| Videos del Studio en Exportaciones / Clips | ❌ No aparecían | ✅ Pestañas separadas |
| `add_background` split | ❌ Tapaba la cámara | ✅ Franja sin tapar |

### Pruebas
- 138 pruebas de backend (`pytest -q`), incluidas `test_resources`, `test_generative`, `test_style_judge`, `test_cards`, `test_sound_judge`, `test_add_background` y casos nuevos en `test_thumbs`.

### Pendiente
- Probar en la PC la re-edición de «problemas con eneba» con el estilo aprendido de Baitybait (`00ztqETzpKs`); el aprendizaje se cortó por un apagón.
- Primera búsqueda de recursos lenta (hasta ~15 min).
- Revisar derechos de uso de recursos de internet antes de publicar.

---

## [v0.5] — Fondo de juego / gameplay automático (2026-11-19)

### ¿Qué motivó el cambio?

El usuario reportó que el editor de IA no servía: al pedir "cambiar el fondo a un gameplay y pantalla negra", el editor rompía todo, no dejaba especificar qué gameplay usar, agregaba videos aleatorios con sonido ajeno y arruinaba el clip. El editor de IA no editaba bien en absoluto.

### Cambios realizados

#### backend/app/studio/agent.py
- **Nueva operación `add_background`** en `OPS` y `OP_SCHEMA`. Parámetros: `video_id`, `search_query`, `start`, `end`, `mode` (split/full/blur), `fit` (cover/contain), `volume`, `game_x`.
- **Actualizado `INSTRUCT_SYSTEM`** con documentación completa de `add_background`: cómo buscar en internet (`search_query`), cómo usar un video de la biblioteca (`video_id`), los 3 modos, parámetros de volumen y posición.
- **Nueva rama en `apply_ops()`** que maneja `add_background`:
  - Con `search_query`: busca en internet vía `resources.finder.find()` y descarga automáticamente el recurso.
  - Con `video_id`: valida que exista en la biblioteca y usa un tramo (`src_in` calculado automáticamente).
  - Modo `full`: activa chroma con fondo por tramos, sujeto al 50% abajo centro.
  - Modo `split` / `blur`: pone el b-roll a pantalla completa y encoge la toma principal a una esquina (split: arriba centro; blur: centrado).

#### backend/app/studio/timeline.py
- **Nuevo campo `pos`** en segmentos de video (`timeline.py:relayout`): posición x,y (0-1) para colocar el video escalado en una esquina del frame. Útil para modos split y blur.

#### backend/app/studio/render.py
- **`_seg_video_chain`** usa `s.get("pos")` para colocar el video escalado en coordenadas `(px, py)` en lugar de siempre centrarlo (`pad=OW:OH:(ow-iw)/2:(oh-ih)/2`).
- Soporte de fondo por tramos (chroma `bg_schedule`) y b-roll a pantalla completa en modo split/blur.

#### backend/app/routers/studio.py
- **Nuevo endpoint `POST /studio/projects/{pid}/background/search`**: busca videos de gameplay/trailer en internet (ddgs) relacionados con una consulta. Devuelve `{title, url, id, duration, views, publisher}` para que el usuario elija el recurso exacto.
- Import de `research` añadido.

#### frontend/src/components/studio/Panels.jsx
- **Nuevos chips de ejemplo** en `AiBox`: "Pon un gameplay de GTA 6 de fondo en todo el video", "Busca gameplay de Minecraft y ponlo detrás de mi cámara", "Pon este video de la biblioteca como fondo de pantalla verde", "Quita el fondo y pon un color negro detrás".

### Comparación antes / después

| Escenario | Antes (v0.4) | Después (v0.5) |
|---|---|---|
| Poner gameplay de fondo | ❌ No posible sin pantalla verde (`set_chroma` requiere chroma) | ✅ `add_background` modo `split` o `full` |
| Elegir qué gameplay usar | ❌ La IA elegía sola, sin control del usuario | ✅ Endpoint `/background/search` + `search_query` |
| Fondo con sonido ajeno | ❌ El b-roll traía su audio original | ✅ `volume` configurable (default 0) |
| Búsqueda automática en internet | ❌ No existía | ✅ `search_query` descarga recursos verificados |
| Cámara + juego abajo (estilo streamer) | ❌ No disponible | ✅ Modo `split` sin pantalla verde |

### Pruebas

Todas las 133 pruebas de backend pasan (`pytest -q`).

### Pendiente

- Pruebas de frontend para la nueva operación `add_background`.
- Documentar en `docs/contexto-proyecto.md` la integración con Content Intelligence.
- Selector visual de fondo en el frontend (actualmente solo por instrucción de IA o endpoint directo).

---

## [v0.4] — Lanzamiento inicial documentado

Véase `README.md` y `docs/ESTADO_ACTUAL.md` para el estado de v0.4.

## [plan] - Mejora de edición IA tipo OpusClip (2026-11-23)

- `docs/PLAN_MEJORA_EDICION_IA.md`: plan integral de mejora de la edición con modelos IA (detección real de zoom, prompts del director, b-roll semántico, rostro X+Y, subtítulos dinámicos, QA preventivo, modelos). Implementado en v0.8.0, salvo el cambio de hablante activo y los emojis en subtítulos (pendientes).

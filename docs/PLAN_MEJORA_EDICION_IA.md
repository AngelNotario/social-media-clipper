# Plan de mejora — Edición IA tipo OpusClip

**Proyecto:** Social Media Clipper  
**Versión objetivo:** v0.8 (post v0.7.x)  
**Estado:** borrador para implementar por otra IA  
**Fecha:** 2026-11-23

---

## 1. Problema diagnosticado

El usuario subió un video de referencia tipo "GTA6 rubios gameplay" con aproximadamente 1 zoom cada 5 s y cortes dinámicos. El sistema:

1. **No detectó los zooms.** La detección en `studio/learn.py` no analiza movimiento de escala real del video. Infiere `zoomed_in` a partir de ~10 fotogramas del gancho + ~40 frames cada 8 s enviados a Ollama visión (`zoomed_in` booleano). Si un zoom ocurre entre frames muestreados, no existe.
2. **No aplicó los zooms de la referencia.** En el render (`studio/autoedit.py:build_timeline`) el zoom es mecánico: `(i // every) % 2` aplica zoom a tomas alternadas, no copia los momentos de punch de la referencia.
3. **Estilo limitado en videos largos.** `studio/brief.py:style_for()` limita `zoom_amount`, `zoom_every` y fuerza `dinamico` → `suave` en largos, matando la energía.
4. **Prompts genéricos.** `DIRECTOR_SYSTEM` y `ENRICH_SYSTEM` deciden punch/meme/SFX por picos de energía o ventanas de 150 s, no por estándar de retención OpusClip.
5. **b-roll sin semántica profunda.** `resources/planner.py` decide por ventanas de 90 s con ddgs/yt_dlp; no entiende "qué imagen encaja con esta frase exacta".
6. **Subtítulos estáticos.** ASS fijo (karaoke/simple/clean), sin emoji automático, sin color por palabra, sin animación pop.
7. **QA post-render.** `editor/qa.py` detecta problemas después de renderizar, no evita gancho negro, silencio inicial o zoom mal puesto durante la composición.

Resultado: edición mediocre, modelos no editan como profesionales, recursos mal puestos.

---

## 2. Objetivo

Que la app edite videos verticales super dinámicos con:

- Zooms reales detectados y copiados del video de referencia con timestamps exactos.
- Seguimiento de rostro X+Y, cambio automático entre hablantes activos, reencuadre con 2 personas.
- Subtítulos karaoke por palabra con emoji automático, color resaltado dinámico, animación pop.
- b-roll/tarjetas semánticamente acordes a lo que se dice, bien puestos, sin tapar el rostro.
- Estilo aplicable absoluto (estándar OpusClip), no solo "parecido a la referencia mediocre".
- QA preventivo antes del render: gancho, silencio, zoom, duración.

---

## 3. Áreas de mejora (archivo → qué cambiar → por qué)

### 3.1 Detección real de zoom (`studio/learn.py`)

**Líneas clave:** `analyze()` (226-351), `_av_sync()` (175), `elements()` (402-423), `to_style()` (501-583), `build_edl()` (438-498).

**Cambios:**
- Reemplazar inferencia por muestra de frames con análisis de movimiento de escala frame-a-frame sobre toda la referencia (FFmpeg `zoompan`/detección de cambio de escala por frame o OpenCV template matching).
- Guardar `zoom_events` con timestamps exactos `[inicio, fin]` en el EDL y en el estilo (`learned_styles.style`).
- Calcular `zoom_per_min` real, no estimado por visión booleana.
- `to_style()`: exponer `zoom_events`, `zoom_per_min`, `punch_events` (momentos de énfasis con punch).

**Verificación:** con el video GTA6, `learned_styles.style` debe contener ~12-15 zoom_events distribuidos cada ~5 s, no un único `zoom_amount`.

---

### 3.2 Estilo aprendido aplicable (`studio/learn.py`, `studio/style.py`, `studio/brief.py`, `studio/reference_rules.py`)

**Líneas clave:** `to_style()` (501-583), `style_for()` (brief.py:129), `before()` (reference_rules.py:20-110).

**Cambios:**
- Estilo aplicable incluya `zoom_events` y se apliquen en los momentos de énfasis de la referencia, no alternado mecánico `(i // every) % 2`.
- Eliminar o relajar el límite de videos largos en `brief.py:style_for()` (no forzar `dinamico` → `suave`, no limitar `zoom_amount` +0.04).
- Añadir estándar absoluto tipo OpusClip (zoom en frase clave/punch, hold hasta fin de frase, máx 1 texto c/3s) como objetivo del juez, no solo similaridad contra la referencia.

**Verificación:** render de un video nuevo con estilo GTA6 aplicado debe tener zoom en los mismos momentos de punch que la referencia, no tomas alternadas.

---

### 3.3 Director y agente IA con prompts OpusClip (`studio/director.py`, `studio/agent.py`, `studio/style_judge.py`)

**Líneas clave:** `DIRECTOR_SYSTEM` (director.py:105-124), `direct()` (292-427), `ENRICH_SYSTEM` (agent.py:502-510), `INSTRUCT_SYSTEM` (103-158), `VISUAL_PROMPT` (style_judge.py:304-308).

**Cambios:**
- Reescribir `DIRECTOR_SYSTEM`: decidir punch/zoom basado en energía emocional + palabras clave + retención, no solo picos de energía. Punch solo en remate de frase clave, hold hasta fin de frase, separación mínima 3 s entre textos y 4 s entre sonidos.
- `ENRICH_SYSTEM`: título, callouts (máx 5 palabras, verificados con `callout_is_said()`), b-roll con semántica de frase exacta, énfasis SFX.
- `style_judge.py`: juez compara contra estándar OpusClip dinámico (ritmo, retención, colocación de recursos), no solo contra la propia referencia. Umbral absoluto ≥ 85-90 %.

**Verificación:** el director propone punch en remates, no en frases normales; el juez rechaza ediciones con texto colgante o recurso sin soporte.

---

### 3.4 Recursos y b-roll semánticos (`resources/planner.py`, `resources/finder.py`, `studio/generative.py`, `studio/agent.py`)

**Líneas clave:** `plan_needs()` (planner.py:149-198), `find()` (finder.py:434-501), `generative.run()` (196-312), `add_background` (agent.py:348-413).

**Cambios:**
- Planificador que entienda "qué b-roll encaja con esta frase exacta" (entidades nombradas, contexto visual, acción), no solo ventanas de 90 s.
- Tarjetas con emoji automático, color adaptativo por palabra, animación pop/zoom.
- Modo `full` detrás de persona con chroma mejorado y cámara OBS protegida (`studio/chroma.py`).
- Si no hay recurso verificable, `ops: []` y explicación en `message` (regla existente, mantener).

**Verificación:** al decir "GTA6" aparece gameplay de GTA6, no una imagen genérica de ciudad; tarjeta no tapa el rostro.

---

### 3.5 Seguimiento de rostro y multi-cámara (`editor/crop.py`, `studio/render.py`)

**Líneas clave:** `face_track()` (crop.py:52-121), `track_expr()` (132-147), `crop_rect()` (render.py:478-489), `seg_video_chain()` (525-580).

**Cambios:**
- Tracking X+Y, no solo X.
- Cambio automático entre hablantes activos (detectar rostro más grande/central por segmento y reasignar).
- Reencuadre inteligente con 2 personas.
- Fallback robusto cuando YuNet falla (perfil, baja luz, múltiples rostros).

**Verificación:** en video con 2 personas, el recorte sigue al que habla; en perfil, no se centra en el vacío.

---

### 3.6 Subtítulos dinámicos tipo OpusClip (`editor/subtitles.py`, `studio/render.py`)

**Líneas clave:** `group_words()` (subtitles.py:49), `build_ass()` (render.py:629-719).

**Cambios:**
- Karaoke por palabra con emoji automático según palabra clave.
- Color resaltado dinámico en palabra activa.
- Animación pop en palabra activa.
- Posición adaptativa al layout (debajo de cámara en split, tercio inferior en recortado).

**Verificación:** subtítulo resalta palabra por palabra con emoji y color, no bloque estático.

---

### 3.7 QA preventivo (`editor/qa.py`, `studio/render.py`)

**Líneas clave:** `qa.evaluate()` (qa.py:53-100), `build_command()` (render.py:724-993).

**Cambios:**
- QA durante la composición: detectar gancho negro, silencio inicial, zoom mal puesto, duración fuera de rango, texto colgante, antes de ejecutar FFmpeg.
- Rechazar timeline y devolver correcciones específicas si falla QA preventiva.

**Verificación:** ningún render final empieza con pantalla negra o silencio >0.5 s en los primeros 3 s.

---

### 3.8 Modelos y configuración (`backend/app/db.py`, `backend/app/ai/llm.py`)

**Líneas clave:** modelos por defecto (db.py:160-166), `VIDEO_EDIT_SYSTEM` / `VIDEO_EDIT_SCHEMA` (llm.py:155-189).

**Cambios:**
- Umbrales de modelo por tarea (visión qwen3.6 para frames, qwen3:14b para edición/juez).
- Fallback inteligente si Ollama no está disponible (heurística con aviso claro, no edición silenciosa mediocre).
- Configuración en `settings`: modelos activos, umbrales de juez, umbral de similitud OpusClip.

**Verificación:** si Ollama cae, el sistema avisa y usa reglas determinísticas con puntuación baja marcada en el reporte.

---

## 4. Prioridad de implementación

| Prioridad | Área | Por qué primero |
|-----------|------|-----------------|
| 1 | Detección real de zoom + estilo aplicable | Causa directa del fallo con GTA6 |
| 2 | Director/agente prompts OpusClip | Define qué poner y cuándo |
| 3 | b-roll semántico | Recursos bien puestos |
| 4 | Seguimiento rostro X+Y + multi-cámara | Encuadre profesional |
| 5 | Subtítulos dinámicos | Identidad visual OpusClip |
| 6 | QA preventivo | Evita renders basura |
| 7 | Modelos y configuración | Estabilidad del pipeline |

---

## 5. Criterios de éxito (definición de "listo")

1. Con el video de referencia GTA6, `learned_styles.style` contiene `zoom_events` con timestamps cada ~5 s.
2. Render de video nuevo con ese estilo aplica zoom en los mismos momentos de punch (no alternado mecánico).
3. Subtítulos resaltan palabra por palabra con emoji y color.
4. b-roll/tarjetas corresponden a lo que se dice y no tapan el rostro.
5. Ningún render final tiene gancho negro o silencio inicial >0.5 s.
6. Juez de estilo aprueba con ≥ 85 % contra estándar OpusClip dinámico.
7. Suite de pruebas backend pasa (`pytest -q`).

---

## 6. Notas para la IA que implemente

- No commitear credenciales (`client_secret_*.json` expuesto en raíz: revocar y borrar).
- Todo bajo `data/` (gitignored). No commitear videos, DB, logs ni renders.
- Frontend es `.jsx`/`.js` puro, sin TypeScript.
- Un trabajo a la vez (GPU) vía `jobs.py`.
- Documentar cada cambio en `CHANGELOG.md` con antes/después (regla 12 de AGENTS.md).
- Nunca inventar recursos: si no hay nada verificable, `ops: []` y explicar por qué en `message`.

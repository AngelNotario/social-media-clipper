# Plan de edición profesional tipo humano — v0.9.0

**Proyecto:** Social Media Clipper  
**Versión base:** v0.8.0 (ya implementada por Claude, 175 pruebas pasan)  
**Versión objetivo:** v0.9.0  
**Estado:** borrador para implementar  
**Fecha:** 2026-11-23

---

## 1. Contexto y objetivo

La v0.8.0 resolvió la detección de zoom real por cuadro y eliminó el zoom alterno mecánico. Sin embargo, el usuario requiere **edición profesional y precisa que imite videos editados por humanos**, no solo zooms. El aprendizaje de estilos debe capturar el ritmo, la colocación de recursos, los textos, el audio y la narrativa visual de una referencia humana y aplicarla con criterio profesional a nuevos videos.

El enfoque no debe ser "solo zooms", sino un sistema integral de estilo que reproduzca decisiones humanas de edición.

---

## 2. Qué ya hace la v0.8.0 (no volver a implementar)

- Medición de zooms cuadro a cuadro en referencia (cuántos/min, cuánto acercan, cuánto se sostienen, golpe vs progresivo).
- Fin del zoom alterno mecánico `(i // every) % 2`.
- Zooms en momentos fuertes (exclamación, pregunta, pico de voz), sostenidos hasta fin de frase.
- Ritmo copiado por intensidad/frecuencia, no segundos exactos de la referencia.
- Criterio OpusClip: ≥3 s entre textos, ≥4 s entre sonidos.
- Zooms centrados en altura de cara; tarjetas al lado opuesto de la persona.
- QA pre-render: avisa de inicio negro, silencio inicial, duración no pedida; arregla textos vacíos o fuera de video.
- Mensaje claro si Ollama no responde.

---

## 3. Qué falta y qué mejorar (v0.9.0)

### 3.1 Cambio de hablante activo (dos personas)

**Problema:** No hay detección de quién mueve la boca ni encuadre a la persona activa. Con dos personas, el recorte se queda en una o en el centro.

**Cambios:**
- Detectar movimiento de boca (landmarks o diferencia de región labial entre frames) por segmento.
- Elegir al hablante activo y centrar el recorte en su cara (X+Y).
- Si hay dos hablantes en el mismo segmento, alternar según quién habla (corte de cámara humano).
- Fallback robusto si no se detecta boca: mantener cara más grande/central o centro del frame.

**Archivos:** `editor/crop.py`, `studio/render.py`

### 3.2 Emojis en subtítulos como imagen (color real)

**Problema:** FFmpeg en Windows dibuja emojis en blanco y negro o no los dibuja.

**Cambios:**
- Renderizar emoji como imagen PNG separada con transparencia (PIL/Pillow o similar).
- Overlay del emoji junto a la palabra activa en el subtítulo ASS o como capa de vídeo.
- Mantener compatibilidad con karaoke por palabra y color de palabra activa.

**Archivos:** `editor/subtitles.py`, `studio/render.py`

### 3.3 Recorte automático de silencio inicial y inicio en negro

**Problema:** La v0.8.0 solo avisa; no corrige.

**Cambios:**
- Detectar silencio de audio y frames negros en los primeros N segundos.
- Recortar automáticamente ese tramo inicial antes de editar.
- Ajustar timestamps del timeline al recorte.

**Archivos:** `editor/qa.py`, `studio/autoedit.py`, `studio/render.py`

### 3.4 Bucle de estilo: repartir eventos por ritmo, no por segundos exactos

**Problema:** Claude advirtió que su bucle copia eventos en segundos exactos de la referencia, lo que no tiene sentido para otro video con otro contenido.

**Cambios:**
- Revisar `edit_to_style_loop()` en `autoedit.py`.
- Mapear eventos de la referencia (zoom, texto, corte, SFX) a momentos de intensidad relativa del nuevo video (gancho, clímax, pausa), no a segundos absolutos.
- Mantener la frecuencia y el patrón rítmico de la referencia, adaptado a la duración y estructura del nuevo video.

**Archivos:** `autoedit.py` (especialmente `edit_to_style_loop` y `_apply_corrections`)

### 3.5 Pista visual de zooms de referencia en el editor

**Problema:** No se pueden verificar los zooms aprendidos a ojo.

**Cambios:**
- Exportar `zoom_events` del estilo aprendido como pista de marcas/timestamps.
- Mostrarlos en el frontend (página de estilos o editor de timeline) como marcas sobre la forma de onda o el video.
- Permitir activar/desactivar y editar manualmente.

**Archivos:** `studio/learn.py` (export), `frontend/src/pages/` (estilos/editor), `studio/brief.py`

### 3.6 Estilo completo tipo humano (más allá de zooms)

**Objetivo:** Que el estilo aprendido capture y replique decisiones humanas de edición:

| Elemento | Qué capturar de la referencia | Qué aplicar al nuevo video |
|----------|------------------------------|---------------------------|
| Ritmo de cortes | `cuts_per_min`, distribución, cortes en picos de audio | Igual frecuencia y colocación relativa |
| Zooms | `zoom_events` con tipo (golpe/progresivo) y duración | En momentos de énfasis del nuevo video |
| Textos | `captions_ratio`, posición, mayúsculas, colores | Textos en momentos clave, misma densidad |
| b-roll/tarjetas | `overlay_ratio`, `card_ratio`, `pip_ratio`, layout | Recursos semánticos en momentos narrativos |
| Audio | `sfx_per_min`, `music_bed`, ducking, silencio | SFX y música en los mismos momentos relativos |
| Transiciones | `transition_style` (dinamico/suave/cortes) | Igual estilo de transición |

**Cambios:**
- Extender `learned_styles.style` con estos campos si no existen ya.
- `to_style()` y `brief.style_for()` deben aplicar el estilo completo, no solo zoom.
- `reference_rules.before()` y `after_director()` respetan el estilo humano aprendido.

**Archivos:** `studio/learn.py`, `studio/style.py`, `studio/brief.py`, `studio/reference_rules.py`

### 3.7 Juez de estilo absoluto (estándar humano profesional)

**Problema:** El juez compara contra la propia referencia, que puede ser mediocre.

**Cambios:**
- Juez compara contra un estándar absoluto de edición profesional humana (ritmo, retención, colocación de recursos, sincronía audio-visual).
- Umbral de aprobación ≥85-90%.
- Si la referencia es mala, el juez avisa y sugiere estilo base profesional en lugar de copiar defectos.

**Archivos:** `studio/style_judge.py`, `studio/strict_judge.py`

---

## 4. Prioridad de implementación

| Prioridad | Área | Razón |
|-----------|------|-------|
| 1 | Cambio de hablante activo | Edicion profesional con 2 personas |
| 2 | Emojis como imagen en subtítulos | Identidad visual profesional |
| 3 | Recorte auto de silencio/inicio negro | Calidad de entrega |
| 4 | Bucle de estilo por ritmo no segundos | Precisión del aprendizaje |
| 5 | Pista visual de zooms en editor | Verificación humana |
| 6 | Estilo completo (cortes, textos, b-roll, audio) | Imitar humano integralmente |
| 7 | Juez absoluto profesional | Calidad garantizada |

---

## 5. Criterios de éxito (v0.9.0 listo)

1. Con dos personas, el recorte sigue al que habla (cambio de hablante activo).
2. Subtítulos muestran emojis a color junto a la palabra activa.
3. Ningún render empieza con silencio >0.5 s o frames negros iniciales (recorte automático).
4. Los eventos de estilo se distribuyen por ritmo relativo, no por segundos exactos de referencia.
5. Los zooms aprendidos se ven como pista de marcas en el editor y se pueden verificar/editar.
6. El estilo aprendido aplica ritmo de cortes, textos, b-roll y audio como un editor humano, no solo zooms.
7. Juez aprueba con ≥85% contra estándar profesional.
8. Suite de pruebas backend pasa (`pytest -q`).

---

## 6. Reglas de trabajo (AGENTS.md)

- No commitear credenciales (`client_secret_*.json` expuesto en raíz).
- Todo bajo `data/` (gitignored). No commitear videos, DB, logs ni renders.
- Frontend `.jsx`/`.js` puro, sin TypeScript.
- Un trabajo a la vez (GPU) vía `jobs.py`.
- Documentar cada cambio en `CHANGELOG.md` con antes/después.
- Nunca inventar recursos: si no hay nada verificable, `ops: []` y explicar por qué en `message`.

# ARQUITECTURA — Social Media Clipper

Documento de arquitectura del sistema. Sirve como referencia para cualquier desarrollador o agente que deba hacer cambios sin perder el contexto.

---

## 1. Visión general

Aplicación web local de escritorio (ejecutada en el navegador vía localhost) con arquitectura **cliente-servidor monolítica dividida en dos procesos**:

- **Backend:** FastAPI (Python 3.12) + SQLite + FFmpeg + IA local (Ollama + faster-whisper + Chatterbox TTS). Expone API REST bajo `/api` y sirve el frontend compilado como SPA.
- **Frontend:** React 18 + Vite. Comunicación con el backend mediante `fetch` (JSON) y `XMLHttpRequest` (subidas con progreso). Enrutado por hash en el navegador.

Todo corre en la máquina del usuario. No hay nube obligatoria (excepto OAuth para publicación en plataformas externas).

---

## 2. Diagrama de capas (nivel alto)

```
┌─────────────────────────────────────────────────────────────┐
│                    Navegador (frontend React)                │
│  http://localhost:5173 (dev)  /  http://localhost:8000 (prod)│
│  Enrutado: window.location.hash                              │
│  Estado: useState/useEffect local (sin gestor global)        │
│  Llamadas: fetch /api/...  y  XMLHttpRequest (subidas)       │
└─────────────────────────────────────────────────────────────┘
                              │ HTTP / JSON
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Backend FastAPI (uvicorn, puerto 8000)         │
│  main.py  →  CORS  →  Routers (/api/*)  →  SPA fallback      │
│  lifespan: init DB, esquemas studio/stream/knowledge/taste, │
│            iniciar worker de jobs, iniciar runner publish    │
└─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│   SQLite      │   │   FFmpeg /    │   │   IA Local    │
│   (db.py)     │   │   FFprobe     │   │               │
│   clipper.db  │   │   (media.py)  │   │   faster-whisper│
│   - videos    │   │   - probe     │   │   Ollama qwen3│
│   - jobs      │   │   - thumbnails│   │   Chatterbox TTS│
│   - transcripts│  │   - render    │   │   OpenCV YuNet  │
│   - analyses  │   │   - subtitles │   │   Demucs/librosa│
│   - clips     │   │               │   │   earshot       │
│   - studio_*  │   └───────────────┘   └───────────────┘
│   - settings  │         ▲               ▲
│   - taste_*   │         │               │
└───────────────┘   Procesos auxiliares   Procesos auxiliares
                      (audio_worker.py)   (tts_worker.py,
                                           earshot_worker.py)
```

---

## 3. Backend — módulos y responsabilidades

### 3.1 Core
| Archivo | Responsabilidad |
|---------|-----------------|
| `app/main.py` | Factory de la app FastAPI, lifespan (inicio/cierre), CORS, registro de routers, servido de frontend compilado (SPA). |
| `app/config.py` | Rutas de directorios (`DATA_DIR`, `DB_PATH`, `WORK_DIR`, etc.), extensiones de video/audio/imagen, carpeta por defecto autorizada (`%USERPROFILE%\Videos`). Lee `SMC_DATA_DIR` y `SMC_DEFAULT_FOLDERS` del entorno. |
| `app/db.py` | Acceso a SQLite sin ORM. Esquema completo (`settings`, `videos`, `jobs`, `transcripts`, `analyses`, `clips`, `renders`, `studio_projects`, `studio_history`, `studio_renders`, `assets`, `feedback`, `taste_samples`, `video_settings`). |
| `app/security.py` | Control de acceso a carpetas autorizadas. Previene path traversal (`..`) y rutas fuera de las permitidas. |
| `app/jobs.py` | Cola de trabajos en segundo plano. Un trabajo a la vez (protege la GPU). Estados: queued/running/done/error/cancelled. Progreso, cancelación, reejecución al reiniciar. |
| `app/media.py` | Utilidades FFmpeg/FFprobe: metadatos de video, generación de miniaturas, MIME types. |
| `app/streaming.py` | Servir archivos de video con soporte de rangos HTTP (para el reproductor del navegador). |

### 3.2 IA (`app/ai/`)
| Archivo | Responsabilidad |
|---------|-----------------|
| `transcribe.py` | Transcripción con faster-whisper (GPU/CUDA, large-v3-turbo, tiempos por palabra). |
| `audio.py` | Extracción de audio, curvas de energía, detección de pistas. |
| `highlights.py` | Detección de momentos destacados, puntuación de criterios (hook, contexto, emoción, valor, narrativa, retención). |
| `llm.py` | Comunicación con Ollama (chat JSON, verificación de modelos). |
| `judge.py` | Juez de IA: segunda opinión sobre clips. |
| `pipeline.py` | Orquestación del pipeline de análisis (transcripción → análisis → clips). |
| `gpu.py` | Detección de CUDA y NVENC. |
| `models.py` | Verificación de modelos instalados en Ollama. |
| `stems.py` | Separación de fuentes de audio (Demucs). |

### 3.3 Editor (`app/editor/`)
| Archivo | Responsabilidad |
|---------|-----------------|
| `crop.py` | Recorte siguiendo rostro (YuNet/OpenCV). |
| `subtitles.py` | Subtítulos (karaoke, simple, formato ASS). |
| `render.py` | Renderizado de clips (FFmpeg / NVENC). |
| `master.py` | Masterización de audio (-14 LUFS). |
| `qa.py` | Control de calidad automático (negro, silencio, gancho débil). |

### 3.4 Studio (`app/studio/`)
| Archivo | Responsabilidad |
|---------|-----------------|
| `projects.py` | Gestión de proyectos del Studio. |
| `timeline.py` | Modelo de timeline (pistas: video, texto, b-roll, música, efectos, subtítulos). |
| `autoedit.py` | Auto-edición (jump cuts, silencios, muletillas). |
| `agent.py` | Agente de IA para instrucciones de edición en lenguaje natural. |
| `director.py` | Director de edición (zoom, memes, efectos, música). |
| `render.py` | Renderizado de proyectos del Studio. |
| `style.py` | Gestión de estilos de edición. |
| `learn.py` | Aprendizaje de estilos desde videos de referencia. |
| `voice.py` | Clonación de voz (Chatterbox TTS). |
| `brief.py` | Brief del proyecto (tipo, estilo, subtítulos). |
| `topic.py` | Video desde un tema (guion de qwen3). |
| `chroma.py` | Detección de pantalla verde. |
| `footage.py` | Indexación de clips ("mis clips"). |
| `probe.py` | Detección de formato de archivo. |
| `refs.py` | Referencias de YouTube/TikTok/Instagram (yt_dlp opcional). |
| `sound_judge.py` | Juez de sonido. |
| `assets.py` | Biblioteca de recursos (música, sfx, b-roll). |
| `generative.py` | Edición generativa: recursos de internet como fondo por tramos o tarjetas, con juez de estilo. |

### 3.5 Publicación (`app/publish/`)
| Archivo | Responsabilidad |
|---------|-----------------|
| `oauth.py` | Flujo OAuth (Google/YouTube, TikTok, Meta). |
| `metadata.py` | Metadatos generados por IA (títulos, descripciones, hashtags). |
| `platforms.py` | Abstracción de plataformas. |
| `runner.py` | Runner de subida de posts. |
| `sources.py` | Fuentes de contenido. |
| `store.py` | Almacenamiento de credenciales y posts. |

### 3.6 Otros módulos
| Archivo | Responsabilidad |
|---------|-----------------|
| `app/research.py` | Investigación en internet (ddgs, trafilatura), fact-check, trending. |
| `app/knowledge.py` | Guía de edición viral y perfil de gusto. |
| `app/taste.py` | Perfil de gusto estadístico (afinidad 0-100, pesos de criterios). |
| `app/stream.py` | Modo stream: detección de cámara, clips verticales, resumen YouTube. |
| `app/thumbs.py` | Generación y edición de miniaturas con IA. |

### 3.7 Routers de la API (`app/routers/`)
| Router | Prefijo | Rutas principales |
|--------|---------|-------------------|
| `system` | `/api/system` | health, status, settings |
| `library` | `/api/library` | listar, importar, stream, thumbnail, renombrar, quitar |
| `browse` | `/api/browse` | explorador de carpetas autorizadas |
| `ai` | `/api/ai` | modelos, status, settings, analyze, clips, judge, jobs, renders |
| `studio` | `/api/studio` | proyectos, timeline, estilo, render, autoedit, instruct, reference, voice, learn, footage, topic, assets, thumbnails, background/search |
| `stream` | `/api/stream` | videos, clips, youtube, detect-cam, frame |
| `publish` | `/api/publish` | status, credentials, oauth, posts, metadata, sources |
| `knowledge` | `/api/knowledge` | guide, feedback, taste |
| `thumbs` | `/api/thumbs` | upload, sources, generate |

---

## 4. Frontend — módulos y responsabilidades

| Archivo / Carpeta | Responsabilidad |
|-------------------|-----------------|
| `src/App.jsx` | Componente raíz. Enrutado por hash (`#/dashboard`, `#/library`, etc.). Mapa de páginas. Toast notifications. |
| `src/api.js` | Cliente de API. Objetos `pub` (publicación) y `api` (resto). `uploadStudioFile` con progreso (XMLHttpRequest). Utilidades de formateo (`fmtTime`, `fmtDuration`, `fmtSize`, `fmtDate`, `getQuery`). |
| `src/hooks.js` | `useJob(jobId, onDone)`: polling cada 1.2s hasta que un trabajo termine. |
| `src/main.jsx` | Punto de entrada React DOM (`React.StrictMode`). |
| `src/styles.css` | Estilos globales (CSS puro, sin framework). |
| `src/pages/` | Páginas: Dashboard, Library, Explorer, Settings, Clips, Exports, Stream, Studio, StudioEditor, Publish, Thumbs. |
| `src/components/` | Componentes compartidos: Sidebar, VideoCard, ClipReview, PlayerModal, JobProgress, PublishComposer, ModelPicker, RolesPanel, JudgePanel, TastePanel, QaReport, Feedback, Icon. |
| `src/components/studio/` | Componentes del Studio: Timeline, Panels, Player, Recorder, BriefForm, Creators, live.js, tl.js. |

---

## 5. Flujo de datos típico

1. Usuario importa un video (Explorador o Biblioteca).
2. Backend valida la ruta con `security.py` (solo carpetas autorizadas).
3. `media.py` extrae metadatos con FFprobe; se genera miniatura.
4. Video se guarda en `videos` (SQLite) con `storage_mode` = `reference` o `copy`.
5. Usuario lanza "Analizar con IA" → se crea un `job` (tipo `analyze`).
6. Worker ejecuta el pipeline: `faster-whisper` transcribe → `qwen3:14b` (Ollama) propone clips → se guardan en `clips` con puntuación 0-100.
7. Usuario revisa clips en `Clips.jsx`, ajusta tiempos, aprueba/descarta.
8. Usuario renderiza un clip → job tipo `render` → `editor/render.py` (FFmpeg/NVENC) → clip vertical 1080×1920 con subtítulos ASS → `data/exports`.
9. (Opcional) Publicar: OAuth → `publish/posts` → subida a YouTube/TikTok/Instagram.

---

## 6. Configuración y variables de entorno

| Variable | Valor por defecto | Descripción |
|----------|-------------------|-------------|
| `SMC_DATA_DIR` | `<raíz>/data` | Carpeta de datos (DB, media, exports, etc.). |
| `SMC_DEFAULT_FOLDERS` | `%USERPROFILE%\Videos` | Carpetas autorizadas por defecto (separadas por `os.pathsep`). |
| `ollama_url` (DB setting) | `http://127.0.0.1:11434` | URL de Ollama. |
| `ollama_model` (DB setting) | `qwen3:14b` | Modelo LLM. |
| `whisper_model` (DB setting) | `large-v3-turbo` | Modelo de transcripción. |
| `whisper_language` (DB setting) | `es` | Idioma de transcripción. |

**Nota:** No existe archivo `.env` ni `.env.example`. Las configuraciones se guardan en la tabla `settings` de SQLite y se cambian vía `PUT /api/ai/settings` y `PUT /api/settings`.

---

## 7. Pruebas

- Backend: `backend/tests/` con pytest (17 archivos + `conftest.py`). FFmpeg se ejecuta de verdad en pruebas. Whisper y Ollama se mockean.
- Frontend: **sin pruebas**. No hay script de `test` en `frontend/package.json`.
- Comando: `cd backend && .venv\Scripts\python -m pytest -q`

---

## 8. Despliegue

- Solo scripts `.bat` para Windows local (`start.bat`, `dev.bat`).
- No hay Docker, no hay CI/CD, no hay reverse proxy (Nginx/Caddy).
- El frontend compilado se sirve desde el mismo puerto 8000 (SPA fallback).

---

## 9. Seguridad (pendiente crítico)

- `client_secret_*.json` en la raíz del repositorio contiene `client_id` y `client_secret` de Google OAuth. **Debe revocarse inmediatamente en Google Cloud Console y borrarse del repo.**
- No hay autenticación de usuarios de la app (solo OAuth de plataformas externas para publicación).
- CORS configurado solo para `http://localhost:5173` y `http://127.0.0.1:5173`.
- `security.py` restringe acceso a carpetas, pero no hay rate limiting ni sanitización amplia de entradas.

---

## 10. Deuda técnica conocida

1. `requirements.txt` incompleto (faltan `ddgs`, `trafilatura`, `yt_dlp`, `torch`, `demucs`, `librosa`, `soundfile`, `chatterbox`, `earshot`).
2. Frontend sin TypeScript, sin router, sin gestor de estado, sin pruebas.
3. Scripts de diagnóstico sueltos en raíz de backend (`p11.py`, `p12.py`, `posts.py`, etc.).
4. Sin `.env.example`, sin `LICENSE`, sin `CONTRIBUTING.md`, sin `CHANGELOG.md`.
5. Sin Docker ni CI/CD.
6. Documentación `docs/contexto-proyecto.md` es más un prompt de IA que documentación técnica operativa.

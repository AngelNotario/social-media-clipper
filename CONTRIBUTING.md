# Cómo contribuir

¡Gracias por querer mejorar Social Media Clipper! Esta guía explica cómo preparar tu entorno y enviar cambios.

## Preparar el entorno

1. Instala Python 3.12, Node.js 20+, FFmpeg (en el PATH) y, para la IA, [Ollama](https://ollama.com/).
2. Haz un fork y clona tu copia:
   ```bash
   git clone https://github.com/TU_USUARIO/social-media-clipper.git
   cd social-media-clipper
   ```
3. Backend:
   ```bash
   cd backend
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt      # Linux/macOS: .venv/bin/pip
   ```
4. Frontend:
   ```bash
   cd frontend
   npm install
   ```
5. Desarrollo con recarga automática: `dev.bat` (Windows) o, por separado,
   `uvicorn app.main:app --reload --port 8000` en `backend/` y `npm run dev` en `frontend/`.

## Antes de enviar un pull request

- **Pruebas**: `cd backend && .venv\Scripts\python -m pytest -q`. Agrega pruebas para lo que cambies; Whisper y la IA
  se sustituyen por dobles de prueba (mira `tests/conftest.py`) y FFmpeg corre de verdad.
- **Frontend**: `cd frontend && npm run build` debe compilar sin errores.
- **Idioma**: la interfaz y los mensajes para el usuario están en español claro y sin tecnicismos; los nombres de
  variables y funciones, en inglés.
- **Privacidad**: nada de credenciales, tokens, rutas personales ni videos en el repositorio. `data/` y
  `client_secret*.json` están en `.gitignore`.
- **Alcance**: un pull request por cambio. Describe qué cambia, por qué y cómo lo probaste.

## Reportar fallos y proponer ideas

Usa las [plantillas de issues](https://github.com/AngelNotario/social-media-clipper/issues/new/choose). Para fallos,
incluye pasos para reproducirlo, qué esperabas, qué pasó y, si puedes, el mensaje de error o una captura.

## Arquitectura

Lee [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) para entender el backend (FastAPI + SQLite + FFmpeg), la cola de
trabajos y el flujo de auto-edición del Studio.

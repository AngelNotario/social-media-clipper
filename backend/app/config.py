"""Configuración central de SOCIAL MEDIA CLIPPER (backend)."""
from __future__ import annotations

import os
from pathlib import Path

# Raíz del proyecto: .../social-media-clipper
BASE_DIR = Path(__file__).resolve().parents[2]

# Carpeta de datos (base de datos, miniaturas, copias importadas).
# Se puede cambiar con la variable de entorno SMC_DATA_DIR (las pruebas la usan).
DATA_DIR = Path(os.environ.get("SMC_DATA_DIR", BASE_DIR / "data")).resolve()
DB_PATH = DATA_DIR / "clipper.db"
THUMBS_DIR = DATA_DIR / "thumbnails"
MEDIA_DIR = DATA_DIR / "media"
WORK_DIR = DATA_DIR / "work"        # archivos temporales (audio extraído, subtítulos)
EXPORTS_DIR = DATA_DIR / "exports"  # clips renderizados
STUDIO_DIR = DATA_DIR / "studio"    # grabaciones/subidas y renders del Studio
STUDIO_MEDIA_DIR = STUDIO_DIR / "media"
STUDIO_RENDERS_DIR = STUDIO_DIR / "renders"
ASSETS_DIR = DATA_DIR / "assets"    # pack de efectos generados por la app

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".weba"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi",
    ".wmv", ".flv", ".mpg", ".mpeg", ".ts", ".3gp",
}

MIME_OVERRIDES = {
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".m4v": "video/mp4",
    ".ts": "video/mp2t",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".wmv": "video/x-ms-wmv",
    ".flv": "video/x-flv",
    ".3gp": "video/3gpp",
}


def ensure_dirs() -> None:
    for d in (DATA_DIR, THUMBS_DIR, MEDIA_DIR, WORK_DIR, EXPORTS_DIR,
              STUDIO_MEDIA_DIR, STUDIO_RENDERS_DIR, ASSETS_DIR / "sfx"):
        d.mkdir(parents=True, exist_ok=True)


def default_authorized_folders() -> list[str]:
    env = os.environ.get("SMC_DEFAULT_FOLDERS")
    if env is not None:
        return [p for p in env.split(os.pathsep) if p]
    videos = Path.home() / "Videos"
    return [str(videos.resolve())] if videos.is_dir() else []

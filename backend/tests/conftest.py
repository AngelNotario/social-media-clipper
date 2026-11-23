import os
os.environ.setdefault("SMC_NO_RESEARCH", "1")
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# Entorno aislado: nada toca tu carpeta data/ real.
_TMP = Path(tempfile.mkdtemp(prefix="smc_test_"))
VIDEOS_DIR = (_TMP / "videos").resolve()
OUTSIDE_DIR = (_TMP / "fuera").resolve()
VIDEOS_DIR.mkdir()
OUTSIDE_DIR.mkdir()
os.environ["SMC_DATA_DIR"] = str(_TMP / "data")
os.environ["SMC_DEFAULT_FOLDERS"] = str(VIDEOS_DIR)
os.environ["SMC_JOBS_SYNC"] = "1"  # los trabajos se ejecutan al instante en las pruebas

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg no instalado")


def make_video(path: Path, w=640, h=360, seconds=2) -> Path:
    """Genera un video real de prueba con FFmpeg (no son datos inventados)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    return path


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def dirs():
    return {"videos": VIDEOS_DIR, "outside": OUTSIDE_DIR}

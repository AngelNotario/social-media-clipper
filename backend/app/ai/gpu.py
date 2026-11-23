"""Preparación de CUDA en Windows: las DLL de cuBLAS/cuDNN vienen como paquetes pip (nvidia-*)."""
from __future__ import annotations

import glob
import os
import site
import subprocess
import sys

_dlls_ready = False
_nvenc: bool | None = None


def setup_cuda_dlls() -> None:
    global _dlls_ready
    if _dlls_ready or os.name != "nt":
        return
    roots = list(site.getsitepackages())
    try:
        roots.append(site.getusersitepackages())
    except Exception:
        pass
    roots.append(os.path.join(sys.prefix, "Lib", "site-packages"))
    dirs: list[str] = []
    for r in roots:
        dirs += glob.glob(os.path.join(r, "nvidia", "*", "bin"))
    for d in dict.fromkeys(dirs):
        try:
            os.add_dll_directory(d)
        except OSError:
            pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")
    _dlls_ready = True


def cuda_available() -> bool:
    setup_cuda_dlls()
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def nvenc_available() -> bool:
    """Prueba real de codificación con NVENC (GPU NVIDIA). Se cachea."""
    global _nvenc
    if _nvenc is None:
        try:
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.2",
                 "-c:v", "h264_nvenc", "-f", "null", "-"],
                capture_output=True, timeout=20,
            )
            _nvenc = r.returncode == 0
        except Exception:
            _nvenc = False
    return _nvenc

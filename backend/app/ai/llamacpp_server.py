"""Arranca y vigila llama-server (llama.cpp) para usarlo como motor de IA de la app.

Configuración por defecto = la del reel/repo de Ricardo Bertran (Qwen3.6-35B-A3B Q4_K_M + MTP) con dos cambios para
esta app: visión activada (mmproj; la app la usa para miniaturas, recursos y estilos) y contexto de 32K (la app no
manda más; menos contexto = menos RAM). --fit reparte solo entre la VRAM de 12 GB y la RAM.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

import httpx

from .. import config, db

MODEL_DIR = r"D:\models\Qwen3.6-35B-A3B-Q4-MTP"
DEFAULTS = {
    "llamacpp_exe": r"D:\llama.cpp\llama-server.exe",
    "llamacpp_model": MODEL_DIR + r"\Qwen3.6-35B-A3B-Q4_K_M.gguf",
    "llamacpp_draft": MODEL_DIR + r"\mtp-Qwen3.6-35B-A3B-Q4_0.gguf",
    "llamacpp_mmproj": MODEL_DIR + r"\mmproj-Qwen3.6-35B-A3B-Q8_0.gguf",
    "llamacpp_ctx": "32768",
    "llamacpp_url": "http://127.0.0.1:8080",
    "llamacpp_alias": "smc-qwen36",
    "llamacpp_autostart": "1",
    "llamacpp_extra": "",
}
KEYS = tuple(DEFAULTS)
LOG = config.DATA_DIR / "logs" / "llama-server.log"
_proc: subprocess.Popen | None = None
_lock = threading.Lock()
_last_error = ""


def cfg() -> dict:
    out = {}
    for k, v in DEFAULTS.items():
        got = db.get_setting(k)
        out[k] = v if got in (None, "") else str(got)
    return out


def save(values: dict) -> dict:
    for k, v in values.items():
        if k in KEYS and v is not None:
            db.set_setting(k, str(v).strip())
    return cfg()


def _port(url: str) -> int:
    try:
        return int(url.rsplit(":", 1)[1].split("/")[0])
    except (IndexError, ValueError):
        return 8080


def command(c: dict | None = None) -> list[str]:
    c = c or cfg()
    args = [c["llamacpp_exe"], "-m", c["llamacpp_model"], "--alias", c["llamacpp_alias"],
            "--host", "127.0.0.1", "--port", str(_port(c["llamacpp_url"])),
            "-c", str(int(c["llamacpp_ctx"] or 32768)), "--parallel", "1",
            "-ctk", "q4_0", "-ctv", "q4_0", "-fa", "on", "--fit", "on",
            "--reasoning", "off", "--jinja", "--no-webui"]
    if c.get("llamacpp_draft") and Path(c["llamacpp_draft"]).exists():  # MTP: varias palabras por paso
        args += ["--spec-type", "draft-mtp", "-md", c["llamacpp_draft"], "--spec-draft-n-max", "2",
                 "-ngld", "99", "-ctkd", "f16", "-ctvd", "f16"]
    if c.get("llamacpp_mmproj") and Path(c["llamacpp_mmproj"]).exists():  # visión
        args += ["--mmproj", c["llamacpp_mmproj"]]
    else:
        args += ["--no-mmproj"]
    if c.get("llamacpp_extra"):
        args += c["llamacpp_extra"].split()
    return args


def files_ok(c: dict | None = None) -> dict:
    c = c or cfg()
    return {k: Path(c[k]).exists() for k in ("llamacpp_exe", "llamacpp_model", "llamacpp_draft", "llamacpp_mmproj")}


def health(c: dict | None = None) -> str:
    """"ok", "loading" (cargando el modelo) o "down"."""
    c = c or cfg()
    try:
        r = httpx.get(f"{c['llamacpp_url'].rstrip('/')}/health", timeout=2)
        if r.status_code == 200:
            return "ok"
        if r.status_code == 503:
            return "loading"
    except Exception:  # noqa: BLE001
        pass
    return "down"


def running_here() -> bool:
    return _proc is not None and _proc.poll() is None


def log_tail(n: int = 40) -> str:
    try:
        return "\n".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def _free_ollama_vram() -> None:
    """Si Ollama tiene modelos cargados, los saca de la VRAM: llama.cpp necesita la gráfica para él."""
    url = db.get_setting("ollama_url") or "http://127.0.0.1:11434"
    try:
        for m in httpx.get(f"{url}/api/ps", timeout=2).json().get("models", []):
            httpx.post(f"{url}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=20)
    except Exception:  # noqa: BLE001 — Ollama cerrado: nada que liberar
        pass


def start() -> dict:
    """Arranca llama-server si no está ya corriendo (en este u otro proceso)."""
    global _proc, _last_error
    with _lock:
        c = cfg()
        if health(c) != "down":
            return status()
        miss = [k for k, ok in files_ok(c).items() if not ok and k in ("llamacpp_exe", "llamacpp_model")]
        if miss:
            _last_error = "Faltan archivos: " + ", ".join(c[k] for k in miss)
            return status()
        _free_ollama_vram()
        LOG.parent.mkdir(parents=True, exist_ok=True)
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        log = open(LOG, "w", encoding="utf-8", errors="replace")  # noqa: SIM115 — lo hereda el proceso
        _proc = subprocess.Popen(command(c), stdout=log, stderr=subprocess.STDOUT, cwd=str(Path(c["llamacpp_exe"]).parent),
                                 creationflags=flags)
        _last_error = ""
    return status()


def stop() -> dict:
    """Detiene solo el servidor que arrancó la app (no uno abierto a mano)."""
    global _proc
    with _lock:
        if running_here():
            _proc.terminate()
            try:
                _proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                _proc.kill()
        _proc = None
    return status()


def wait_ready(timeout: float = 600) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = health()
        if h == "ok":
            return True
        if h == "down" and _proc is not None and _proc.poll() is not None:
            return False  # se cerró (ver el registro)
        time.sleep(2)
    return False


def status() -> dict:
    c = cfg()
    return {"backend": db.get_setting("llm_backend") or "ollama", "health": health(c), "started_by_app": running_here(),
            "exited": _proc is not None and _proc.poll() is not None, "error": _last_error, "config": c,
            "files": files_ok(c), "command": " ".join(command(c)), "log": log_tail(25)}


def autostart() -> None:
    """Al abrir la app: si el motor elegido es llama.cpp, lo arranca en segundo plano."""
    if (db.get_setting("llm_backend") or "ollama") != "llamacpp" or cfg()["llamacpp_autostart"] not in ("1", "true", "True"):
        return
    threading.Thread(target=start, daemon=True, name="llamacpp-autostart").start()

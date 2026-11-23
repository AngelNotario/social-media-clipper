"""Cliente de IA local. Dos motores:
- Ollama (por defecto): varios modelos, se cargan y descargan según la tarea.
- llama.cpp (llama-server, API compatible con OpenAI): un solo modelo residente (p. ej. Qwen3.6-35B-A3B con MTP y
  visión) que hace todas las tareas; en una RTX 5070 + RAM va mucho más rápido que cambiar modelos en Ollama.

El resto de la app llama siempre a status/chat_json/unload con la URL de Ollama y un nombre de modelo; si el motor
elegido en Configuración es llama.cpp, aquí se redirige al servidor de llama.cpp y el nombre de modelo se ignora.
"""
from __future__ import annotations

import json
import re

import httpx


class LLMError(Exception):
    pass


class _AnyModel(list):
    """Lista de modelos de llama.cpp: el servidor tiene un solo modelo que atiende cualquier tarea, así que
    cualquier nombre de modelo que pida la app «está instalado»."""

    def __contains__(self, item) -> bool:  # noqa: D105
        return True


def backend() -> str:
    try:
        from .. import db
        return (db.get_setting("llm_backend") or "ollama").strip().lower()
    except Exception:  # noqa: BLE001 — sin base de datos (scripts sueltos): Ollama
        return "ollama"


def llamacpp_url() -> str:
    from .. import db
    return (db.get_setting("llamacpp_url") or "http://127.0.0.1:8080").rstrip("/")


def llamacpp_alias() -> str:
    from .. import db
    return db.get_setting("llamacpp_alias") or "smc-qwen36"


def status(url: str) -> dict:
    if backend() == "llamacpp":
        base = llamacpp_url()
        try:
            r = httpx.get(f"{base}/v1/models", timeout=3)
            r.raise_for_status()
            ids = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
            return {"available": True, "models": _AnyModel(ids or [llamacpp_alias()]), "backend": "llamacpp"}
        except Exception:
            return {"available": False, "models": [], "backend": "llamacpp"}
    try:
        r = httpx.get(f"{url}/api/tags", timeout=3)
        r.raise_for_status()
        return {"available": True, "models": [m["name"] for m in r.json().get("models", [])], "backend": "ollama"}
    except Exception:
        return {"available": False, "models": [], "backend": "ollama"}


def _parse(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S).strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, flags=re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise LLMError("El modelo no devolvió JSON válido.")


def unload(url: str, model: str) -> None:
    """Saca el modelo de la VRAM (keep_alive=0). Con llama.cpp no aplica: el modelo queda residente."""
    if backend() == "llamacpp":
        return
    try:
        httpx.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
    except Exception:
        pass


def _img_url(b64: str) -> str:
    mime = "image/png" if b64.startswith("iVBOR") else "image/webp" if b64.startswith("UklGR") else "image/jpeg"
    return f"data:{mime};base64,{b64}"


def _chat_llamacpp(system: str, user: str, schema: dict, timeout: float, images: list[str] | None,
                   max_tokens: int | None) -> dict:
    content: str | list = user
    if images:
        content = [{"type": "text", "text": user}] + [{"type": "image_url", "image_url": {"url": _img_url(b)}}
                                                     for b in images]
    payload = {
        "model": llamacpp_alias(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "temperature": 0.3,
        "max_tokens": int(max_tokens or 4096),
        "stream": False,
        "response_format": {"type": "json_schema", "json_schema": {"name": "respuesta", "schema": schema, "strict": True}},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    base = llamacpp_url()
    try:
        r = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=timeout)
        if r.status_code == 400 and "schema" in r.text.lower():  # esquema que la gramática no soporta: JSON libre
            payload["response_format"] = {"type": "json_object"}
            r = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=timeout)
        if r.status_code >= 400 and images and ("image" in r.text.lower() or "mmproj" in r.text.lower()):
            raise LLMError("El servidor de llama.cpp no tiene visión (falta el archivo mmproj).")
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMError(f"llama.cpp no respondió correctamente: {exc}") from exc
    msg = (r.json().get("choices") or [{}])[0].get("message") or {}
    return _parse(msg.get("content") or "")


def chat_json(url: str, model: str, system: str, user: str, schema: dict,
              timeout: float = 600, num_ctx: int = 16384, images: list[str] | None = None,
              keep_alive: str = "2m", max_tokens: int | None = None) -> dict:
    if backend() == "llamacpp":
        return _chat_llamacpp(system, user, schema, timeout, images, max_tokens)
    user_msg = {"role": "user", "content": user}
    if images:
        user_msg["images"] = images
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, user_msg],
        "stream": False,
        "format": schema,
        "think": False,
        "keep_alive": keep_alive,
        "options": {"temperature": 0.3, "num_ctx": num_ctx, **({"num_predict": max_tokens} if max_tokens else {})},
    }
    try:
        r = httpx.post(f"{url}/api/chat", json=payload, timeout=timeout)
        if r.status_code == 400 and "think" in r.text:
            payload.pop("think")
            r = httpx.post(f"{url}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMError(f"Ollama no respondió correctamente: {exc}") from exc
    return _parse(r.json()["message"]["content"])


# ---------------- modelos especializados en edición de video (ver + escuchar) ----------------

VIDEO_EDIT_SYSTEM = """Eres un editor de video senior especializado en formato vertical (TikTok/Reels/Shorts).
Recibes fotogramas clave de un video y un resumen de lo que se escucha (voz, música, efectos).
Tu trabajo es analizar el ESTILO DE EDICIÓN (no el tema): ritmo de cortes, posición de tarjetas y textos,
tipo de subtítulos, zooms, efectos de sonido sincronizados con la imagen, música de fondo, y qué tanto
se parece a una referencia dada. Responde SOLO JSON válido."""

VIDEO_EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "description": "0-100 de similitud de estilo con la referencia"},
        "cuts_per_min": {"type": "number"},
        "sfx_per_min": {"type": "number"},
        "sfx_on_cuts": {"type": "boolean"},
        "music": {"type": "string", "enum": ["no", "tenue", "sí"]},
        "text_per_min": {"type": "number"},
        "zoom_per_min": {"type": "number"},
        "overlay_ratio": {"type": "number"},
        "fullscreen_ratio": {"type": "number"},
        "pip_ratio": {"type": "number"},
        "captions_ratio": {"type": "number"},
        "memes_ratio": {"type": "number"},
        "problemas": {"type": "array", "items": {"type": "string"},
                      "description": "qué le falta a la edición para parecerse a la referencia"},
        "correcciones": {"type": "array", "items": {"type": "object", "properties": {
            "op": {"type": "string", "enum": ["add_cut", "add_zoom", "add_transition", "add_caption",
                                              "add_overlay_card", "add_overlay_pip", "add_overlay_fullscreen_broll",
                                              "add_sfx", "add_music"]},
            "t": {"type": "number"},
            "end": {"type": "number"},
            "detail": {"type": "string"}
        }, "required": ["op", "t"]}},
        "notas": {"type": "string"}
    },
    "required": ["score", "problemas", "notas"]
}


def video_edit_model(url: str) -> str | None:
    """Devuelve el modelo disponible más adecuado para edición de video (visión + audio)."""
    st = status(url)
    if not st["available"]:
        return None
    models = st["models"]
    # preferir modelos de visión grandes para edición
    for pref in ("qwen3.6:35b-a3b", "qwen2.5vl:72b", "llava:7b", "bakllava:7b"):
        if pref in models:
            return pref
    # cualquier modelo de visión que tenga "vl" o "vision" en el nombre
    for m in models:
        if any(k in m.lower() for k in ("vl", "vision", "llava")):
            return m
    return None


def chat_video(url: str, model: str | None, system: str, user: str, schema: dict,
               frames_b64: list[str], audio_summary: str | None = None,
               timeout: float = 600, num_ctx: int = 16384, max_tokens: int | None = None,
               keep_alive: str = "2m") -> dict:
    """Chat multimodal para edición de video: varios fotogramas + resumen de audio.
    Devuelve JSON parseado según `schema`."""
    if backend() == "llamacpp":
        content: list = [{"type": "text", "text": user}]
        if audio_summary:
            content[0]["text"] = f"LO QUE SE ESCUCHA (resumen de audio): {audio_summary}\n\n{user}"
        for b in frames_b64:
            content.append({"type": "image_url", "image_url": {"url": _img_url(b)}})
        payload = {
            "model": llamacpp_alias(),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": 0.3,
            "max_tokens": int(max_tokens or 4096),
            "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {"name": "respuesta", "schema": schema, "strict": True}},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        base = llamacpp_url()
        r = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=timeout)
        if r.status_code >= 400 and ("image" in r.text.lower() or "mmproj" in r.text.lower()):
            raise LLMError("El servidor de llama.cpp no tiene visión (falta el archivo mmproj).")
        r.raise_for_status()
        msg = (r.json().get("choices") or [{}])[0].get("message") or {}
        return _parse(msg.get("content") or "")

    # Ollama: múltiples imágenes + audio como texto en el user message
    user_msg = {"role": "user", "content": user}
    if frames_b64:
        user_msg["images"] = frames_b64
    if audio_summary:
        user_msg["content"] = f"LO QUE SE ESCUCHA (resumen de audio): {audio_summary}\n\n{user}"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, user_msg],
        "stream": False,
        "format": schema,
        "think": False,
        "keep_alive": keep_alive,
        "options": {"temperature": 0.3, "num_ctx": num_ctx, **({"num_predict": max_tokens} if max_tokens else {})},
    }
    try:
        r = httpx.post(f"{url}/api/chat", json=payload, timeout=timeout)
        if r.status_code == 400 and "think" in r.text:
            payload.pop("think")
            r = httpx.post(f"{url}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMError(f"Ollama no respondió correctamente: {exc}") from exc
    return _parse(r.json()["message"]["content"])

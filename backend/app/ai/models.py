"""Modelos de IA instalados en Ollama y validación del modelo elegido para un trabajo."""
from __future__ import annotations

from fastapi import HTTPException

from .. import db
from . import llm

NON_CHAT = ("embed", "bge", "minilm", "rerank", "ocr")


def installed() -> dict:
    url = db.get_setting("ollama_url")
    st = llm.status(url)
    if st.get("backend") == "llamacpp":  # un solo modelo residente hace todas las tareas
        return {"available": st["available"], "default": llm.llamacpp_alias(), "models": [llm.llamacpp_alias()],
                "backend": "llamacpp"}
    models = [m for m in st["models"] if not any(k in m.lower() for k in NON_CHAT)]
    return {"available": st["available"], "default": db.get_setting("ollama_model"), "models": models}


def check(model: str | None) -> str | None:
    """Devuelve el modelo si está instalado; None si se usa el predeterminado. 400 si no existe."""
    if not model or llm.backend() == "llamacpp":  # con llama.cpp el modelo por tarea no aplica
        return None
    inst = installed()
    if inst["available"] and model not in inst["models"]:
        raise HTTPException(400, f"El modelo «{model}» no está instalado en Ollama.")
    return model

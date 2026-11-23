"""Prueba llama-server como lo usa la app: prompt largo + JSON con esquema + visión."""
import base64
import io
import json
import sys
import time

import httpx
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "backend"))
from app.ai import llm  # noqa: E402

llm.backend = lambda: "llamacpp"  # sin tocar la configuración guardada de la app
llm.llamacpp_url = lambda: "http://127.0.0.1:8080"
llm.llamacpp_alias = lambda: "smc-qwen36"

frase = ("Hoy les vengo a contar lo que pasó con Eneba y los códigos de GTA 6, compré la edición digital, "
         "pasaron doce horas y nunca llegó el código, soporte no responde y en Reddit hay más de cien personas igual. ")
largo = " ".join(f"[{i * 4.2:.1f}s] {frase}" for i in range(110))  # ~6-7 mil tokens, como un video de 8 min
schema = {"type": "object", "properties": {
    "titulo": {"type": "string"}, "momentos": {"type": "array", "items": {"type": "object", "properties": {
        "t": {"type": "number"}, "que_mostrar": {"type": "string"}}, "required": ["t", "que_mostrar"]}}},
    "required": ["titulo", "momentos"]}
t0 = time.time()
r = httpx.post("http://127.0.0.1:8080/v1/chat/completions", timeout=600, json={
    "model": "smc-qwen36", "max_tokens": 500, "temperature": 0.3, "chat_template_kwargs": {"enable_thinking": False},
    "response_format": {"type": "json_schema", "json_schema": {"name": "r", "schema": schema, "strict": True}},
    "messages": [{"role": "system", "content": "Eres editor de video. Responde solo JSON."},
                 {"role": "user", "content": "Transcripción:\n" + largo + "\n\nDa un título y 5 momentos con qué recurso mostrar."}]})
j = r.json()
tm = j["timings"]
out = json.loads(j["choices"][0]["message"]["content"])
print(f"PROMPT LARGO: {tm['prompt_n']} tokens leídos a {tm['prompt_per_second']:.0f} tok/s; "
      f"{tm['predicted_n']} escritos a {tm['predicted_per_second']:.1f} tok/s; total {time.time() - t0:.1f} s")
print("  JSON válido:", out["titulo"], "|", len(out["momentos"]), "momentos")

im = Image.new("RGB", (640, 360), "#5b21b6")
d = ImageDraw.Draw(im)
try:
    f = ImageFont.truetype("arialbd.ttf", 70)
except OSError:
    f = ImageFont.load_default()
d.text((60, 140), "ENEBA GTA 6", fill="white", font=f)
buf = io.BytesIO()
im.save(buf, "JPEG")
t1 = time.time()
v = llm.chat_json("", "", "Describe imágenes. Responde JSON.", "¿Qué texto aparece y de qué color es el fondo?",
                  {"type": "object", "properties": {"texto": {"type": "string"}, "fondo": {"type": "string"}},
                   "required": ["texto", "fondo"]}, images=[base64.b64encode(buf.getvalue()).decode()], max_tokens=80)
print(f"VISIÓN (vía la app): {v} en {time.time() - t1:.1f} s")

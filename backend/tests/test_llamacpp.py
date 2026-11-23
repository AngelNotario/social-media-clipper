"""Motor llama.cpp: el mismo código de la app habla con llama-server (API compatible con OpenAI)."""
import json

import httpx

from app import db
from app.ai import llamacpp_server, llm


class _Resp:
    def __init__(self, code, data):
        self.status_code, self._d, self.text = code, data, json.dumps(data)

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


def test_llamacpp_backend_routes_chat_and_status(client, monkeypatch):
    db.set_setting("llm_backend", "llamacpp")
    sent = {}

    def fake_get(url, timeout=None):
        assert url.endswith("/v1/models")
        return _Resp(200, {"data": [{"id": "smc-qwen36"}]})

    def fake_post(url, json=None, timeout=None):
        sent.update(url=url, payload=json)
        return _Resp(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(llm.httpx, "get", fake_get)
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    try:
        st = llm.status("http://127.0.0.1:11434")
        assert st["available"] and "qwen3:14b" in st["models"] and st["backend"] == "llamacpp"  # cualquier tarea
        out = llm.chat_json("http://ollama", "qwen3:14b", "sys", "hola", {"type": "object"}, images=["iVBORxx"], max_tokens=50)
        assert out == {"ok": True}
        p = sent["payload"]
        assert sent["url"].endswith(":8080/v1/chat/completions") and p["model"] == "smc-qwen36"
        assert p["response_format"]["type"] == "json_schema" and p["chat_template_kwargs"] == {"enable_thinking": False}
        assert p["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        llm.unload("http://ollama", "x")  # no hace nada con llama.cpp (no debe llamar a Ollama)
    finally:
        db.set_setting("llm_backend", "ollama")


def test_command_uses_mtp_vision_and_fit(client, tmp_path):
    exe, model, draft, mm = (tmp_path / n for n in ("llama-server.exe", "m.gguf", "mtp.gguf", "mm.gguf"))
    for f in (exe, model, draft, mm):
        f.write_bytes(b"x")
    c = {**llamacpp_server.DEFAULTS, "llamacpp_exe": str(exe), "llamacpp_model": str(model),
         "llamacpp_draft": str(draft), "llamacpp_mmproj": str(mm)}
    cmd = " ".join(llamacpp_server.command(c))
    assert "--spec-type draft-mtp" in cmd and f"-md {draft}" in cmd and f"--mmproj {mm}" in cmd
    assert "--fit on" in cmd and "--reasoning off" in cmd and "-c 32768" in cmd and "--port 8080" in cmd
    mm.unlink()
    assert "--no-mmproj" in " ".join(llamacpp_server.command(c))  # sin el archivo de visión, arranca igual


def test_api_config_and_status(client):
    r = client.put("/api/ai/llamacpp", json={"llamacpp_ctx": 65536, "llamacpp_autostart": False}).json()
    assert r["config"]["llamacpp_ctx"] == "65536" and r["config"]["llamacpp_autostart"] == "0"
    assert r["health"] in ("down", "loading", "ok") and "--spec-type" in r["command"] or "-m" in r["command"]
    assert client.put("/api/ai/settings", json={"llm_backend": "ollama"}).status_code == 200

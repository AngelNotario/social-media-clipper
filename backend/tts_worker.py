"""Proceso de síntesis de voz (se ejecuta con el entorno .venv-tts, separado del backend).

Uso: python tts_worker.py trabajo.json
trabajo.json = {"language": "es", "items": [{"text", "out", "ref", "exaggeration", "cfg_weight", "temperature"}]}
Imprime una línea JSON por cada frase generada para informar el progreso.
"""
import json
import sys


def main() -> None:
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    import soundfile as sf
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = ChatterboxMultilingualTTS.from_pretrained(device=device)
    print(json.dumps({"status": "loaded", "device": device}), flush=True)
    items = job["items"]
    for i, it in enumerate(items):
        kwargs = {
            "language_id": job.get("language", "es"),
            "exaggeration": float(it.get("exaggeration", 0.5)),
            "cfg_weight": float(it.get("cfg_weight", 0.5)),
            "temperature": float(it.get("temperature", 0.8)),
        }
        if it.get("ref"):
            kwargs["audio_prompt_path"] = it["ref"]
        if it.get("seed") is not None:
            torch.manual_seed(int(it["seed"]))
        wav = model.generate(it["text"], **kwargs)
        sf.write(it["out"], wav.squeeze(0).detach().cpu().numpy(), model.sr)
        print(json.dumps({"progress": (i + 1) / len(items), "out": it["out"]}), flush=True)


if __name__ == "__main__":
    main()

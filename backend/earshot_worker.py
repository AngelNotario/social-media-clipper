"""Escucha videos de referencia con earshot (corre en .venv-earshot, solo CPU).

  python earshot_worker.py salida.json video1 [video2 ...] [--max 180]

Carga los modelos una sola vez y, por cada video, guarda el "timeline" de earshot resumido para editar:
efectos de sonido por minuto y de qué tipo, música (cuánto suena, tempo), volumen, y si los sonidos caen
justo en los cortes de imagen. Imprime una línea JSON con {"ok": true} al terminar.
"""
from __future__ import annotations

import json
import sys
from collections import Counter

# tipo de evento de earshot / palabras de su etiqueta → papel del sonido en el editor
ROLE_RULES = [
    ("transicion", ("WHOOSH", "whoosh", "swoosh", "swish", "whip", "transition")),
    ("impacto", ("IMPACT", "HIT", "BOOM", "impact", "boom", "punch", "slam", "thud", "explosion", "gunshot", "fusillade")),
    ("revelacion", ("DROP", "bass drop", "sub drop", "drop", "stab", "braam")),
    ("suspenso", ("RISER", "riser", "uplifter", "swell", "tension", "heartbeat", "tick")),
    ("fallo", ("ERROR", "error", "buzz", "fail", "wrong", "vine boom")),
    ("exito", ("DING", "ding", "chime", "bell", "success", "coin", "sparkle")),
    ("texto", ("UI", "POP", "pop", "bubble", "click", "notification", "message")),
]


def role_of(ev: dict) -> str | None:
    kind = (ev.get("kind") or "").upper()
    label = (ev.get("label") or "").lower()
    if kind in ("VOICE", "") and not label:
        return None
    if kind == "VOICE":
        return None
    for role, keys in ROLE_RULES:
        if any(k == kind or (k.islower() and k in label) for k in keys):
            return role
    return "otro" if kind and kind not in ("KEY", "FOLEY", "NOTE") else None


def summarize(d: dict) -> dict:
    dur = max(float(d.get("duration") or 0), 0.1)
    ev = d.get("events") or []
    # efectos: eventos etiquetados que no son voz y destacan sobre el fondo
    sfx = [e for e in ev if e.get("kind") and e["kind"] != "VOICE" and (e.get("prominence_db") or 0) >= 6]
    roles = Counter(r for r in (role_of(e) for e in sfx) if r)
    labels = Counter(e.get("label") for e in sfx if e.get("label"))
    secs = d.get("sections") or []
    music_s = sum((s["end"] - s["start"]) for s in secs if (s.get("ast") or {}).get("Music", 0) >= 0.25
                  or "music" in (s.get("label") or "").lower())
    mus = d.get("music") or {}
    sync = ((d.get("sync") or {}).get("cuts") or {}).get("stats") or {}
    loud = d.get("loudness") or {}
    return {
        "duration": round(dur, 1),
        "sfx_count": len(sfx),
        "sfx_per_min": round(len(sfx) / dur * 60, 1),
        "top_sfx": labels.most_common(8),
        "roles": [r for r, _ in roles.most_common() if r != "otro"],
        "role_counts": dict(roles),
        "music_ratio": round(min(1.0, music_s / dur), 2),
        "music_genre": [g["tag"] for s in secs for g in (s.get("music_genre") or [])[:1]][:4],
        "music_mood": [g["tag"] for s in secs for g in (s.get("music_mood") or [])[:1]][:4],
        "tempo": mus.get("tempo") if mus.get("confidence") not in (None, "none") else None,
        "lufs": loud.get("integrated_lufs"),
        "true_peak": loud.get("true_peak_dbtp"),
        "lra": loud.get("lra_lu"),
        "cuts": sync.get("n"),
        "cut_sync": sync.get("hit_within_100ms"),
        "cut_sync_frame": sync.get("hit_within_1_frame"),
        "accent_on_cut": sync.get("accent_within_100ms"),
        "gaps": len(loud.get("silences") or []),
        "summary": (d.get("summary") or "")[:900],
    }


def main(argv: list[str]) -> dict:
    out_path, files = argv[0], [a for a in argv[1:] if not a.startswith("--")]
    max_s = 180.0
    if "--max" in argv:
        max_s = float(argv[argv.index("--max") + 1])
        files = [f for f in files if f != argv[argv.index("--max") + 1]]
    import earshot  # carga perezosa: los modelos se cargan una vez para todos los archivos
    from earshot import api
    tl_fn = getattr(api, "timeline", None) or getattr(earshot, "timeline")
    results = {}
    for f in files:
        try:
            try:
                d = tl_fn(f, video=f, end=max_s)
            except TypeError:  # versiones sin "end"
                d = tl_fn(f, video=f)
            if not isinstance(d, dict):
                d = getattr(d, "to_dict", lambda: dict(d))()
            results[f] = summarize(d)
        except Exception as exc:  # noqa: BLE001
            results[f] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False)
    return {"ok": True, "files": len(files)}


if __name__ == "__main__":
    try:
        res = main(sys.argv[1:])
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}
    print(json.dumps(res), flush=True)

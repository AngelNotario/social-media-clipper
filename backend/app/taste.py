"""Perfil de gusto: lo que la IA aprende de lo que apruebas, descartas y calificas.

Cada vez que apruebas/descartas un clip o le das 👍/👎 se guarda una "foto" del clip (taste_samples):
título, cómo empieza, duración, mediciones (voz, ritmo, aire muerto…), notas de selección y del juez,
y los efectos que llevaba su último render. Las fotos sobreviven aunque se re-analice el video.

Con esas fotos se calcula (sin entrenar redes, solo estadística explicable):
1. Ejemplos reales (few-shot) de clips aprobados y descartados para los prompts de selección y del juez.
2. Pesos de los criterios de selección: suben los que separan tus aprobados de tus descartes.
3. Mezcla de la nota final: cuánto pesa cada señal (selección, juez, medición) según cuál acierta más contigo.
4. Afinidad con tu gusto (0-100): parecido de un clip con tus aprobados frente a tus descartes.
5. Duración preferida y densidad de efectos preferida (para el director).
"""
from __future__ import annotations

import json
import math
from statistics import median

from . import config, db

PROFILE_PATH = config.DATA_DIR / "knowledge" / "taste.json"
MIN_POS, MIN_NEG = 3, 2
FEATS = ["duration", "wpm", "dead_air", "first_word_at", "hook_energy", "peak_at"]
JAXES = ["hook", "retention", "payoff", "standalone"]
DEFAULT_MIX = {"seleccion": 0.4, "juez": 0.4, "reglas": 0.2}

SCHEMA = """
CREATE TABLE IF NOT EXISTS taste_samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id    INTEGER,
    video_id   INTEGER,
    label      INTEGER NOT NULL,          -- 1 aprobado / me gusta, -1 descartado / no me gusta
    source     TEXT NOT NULL,             -- status | feedback
    title      TEXT,
    opening    TEXT,
    duration   REAL,
    features   TEXT,
    scores     TEXT,
    judge      TEXT,
    director   TEXT,
    note       TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (clip_id, source)
);
"""


def ensure_schema() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------------- captura ----------------

def record(clip_id: int, label: int, source: str = "status", note: str | None = None) -> bool:
    """Guarda (o actualiza) la foto de un clip con su etiqueta."""
    from .ai import judge
    from .ai.pipeline import load_transcript
    ensure_schema()
    with db.get_conn() as conn:
        c = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if not c:
            return False
        a = conn.execute("SELECT energy FROM analyses WHERE video_id = ?", (c["video_id"],)).fetchone()
        r = conn.execute("SELECT options, duration FROM renders WHERE clip_id = ? ORDER BY id DESC LIMIT 1",
                         (clip_id,)).fetchone()
    tr = load_transcript(c["video_id"])
    segs = tr["segments"] if tr else []
    energy = json.loads(a["energy"] or "[]") if a else []
    feats = judge.features(segs, c["start_s"], c["end_s"], energy)
    words = judge.clip_words(segs, c["start_s"], c["end_s"])
    opening = " ".join((w.get("word") or "").strip() for w in words[:32])
    director = None
    if r:
        opts = json.loads(r["options"] or "{}")
        if opts.get("director"):
            director = {**opts["director"], "duration": r["duration"]}
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO taste_samples (clip_id, video_id, label, source, title, opening, duration, features, scores, "
            "judge, director, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(clip_id, source) DO UPDATE SET label = excluded.label, title = excluded.title, "
            "opening = excluded.opening, duration = excluded.duration, features = excluded.features, "
            "scores = excluded.scores, judge = excluded.judge, director = excluded.director, "
            "note = COALESCE(excluded.note, taste_samples.note), created_at = datetime('now','localtime')",
            (clip_id, c["video_id"], 1 if label > 0 else -1, source, c["title"], opening,
             round(c["end_s"] - c["start_s"], 2), json.dumps(feats), c["scores"] or "{}", c["judge"],
             json.dumps(director) if director else None, (note or "").strip()[:400] or None))
    _invalidate()
    return True


def forget(clip_id: int, source: str = "status") -> None:
    ensure_schema()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM taste_samples WHERE clip_id = ? AND source = ?", (clip_id, source))
    _invalidate()


def backfill() -> int:
    """Solo la primera vez: crea fotos de los clips ya aprobados/descartados. Tras reiniciar NO se vuelve a hacer
    (antes el reinicio se deshacía solo: los clips aprobados se reimportaban al recalcular el perfil)."""
    ensure_schema()
    if db.get_setting("taste_backfilled"):
        return 0
    db.set_setting("taste_backfilled", True)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, status FROM clips WHERE status IN ('approved','discarded') AND id NOT IN "
                            "(SELECT clip_id FROM taste_samples WHERE source = 'status' AND clip_id IS NOT NULL)").fetchall()
    n = 0
    for r in rows:
        try:
            n += record(r["id"], 1 if r["status"] == "approved" else -1, "status")
        except Exception:  # noqa: BLE001 — un clip roto no debe frenar el resto
            continue
    return n


def reset() -> int:
    ensure_schema()
    with db.get_conn() as conn:
        n = conn.execute("DELETE FROM taste_samples").rowcount
    db.set_setting("taste_backfilled", True)  # empezar de cero de verdad: no reimportar clips viejos
    _invalidate()
    return n


def samples() -> list[dict]:
    ensure_schema()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM taste_samples ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if (d.get("note") or "").startswith(("aprobó el clip", "descartó el clip")):  # notas automáticas antiguas
            d["note"] = None
        for k in ("features", "scores", "judge", "director"):
            d[k] = json.loads(d[k]) if d.get(k) else {}
        out.append(d)
    # una opinión por clip: la de estado manda; si no, la de 👍/👎
    by_clip: dict = {}
    for d in sorted(out, key=lambda x: (x["source"] != "status", -x["id"])):
        key = d["clip_id"] if d["clip_id"] is not None else f"s{d['id']}"
        if key in by_clip:
            if d.get("note") and not by_clip[key].get("note"):
                by_clip[key]["note"] = d["note"]
            continue
        by_clip[key] = d
    return list(by_clip.values())


# ---------------- estadística ----------------

def _vec(s: dict) -> dict[str, float]:
    v: dict[str, float] = {}
    f = s.get("features") or {}
    for k in FEATS:
        if isinstance(f.get(k), (int, float)):
            v["f_" + k] = float(f[k])
    for k, x in (s.get("scores") or {}).items():
        if isinstance(x, (int, float)) and k not in ("seleccion", "juez", "reglas", "gusto"):
            v["s_" + k] = float(x)
    j = s.get("judge") or {}
    for k in JAXES:
        if isinstance(j.get(k), (int, float)):
            v["j_" + k] = float(j[k]) * 10
    return v


def _signals(s: dict) -> dict[str, float]:
    sc, j = s.get("scores") or {}, s.get("judge") or {}
    out = {}
    if isinstance(sc.get("seleccion"), (int, float)):
        out["seleccion"] = float(sc["seleccion"])
    elif isinstance(sc.get("hook"), (int, float)):
        from .ai.highlights import weighted
        out["seleccion"] = weighted(sc)
    if isinstance(j.get("llm"), (int, float)):
        out["juez"] = float(j["llm"])
    if isinstance(j.get("rules"), (int, float)):
        out["reglas"] = float(j["rules"])
    return out


def _pair_acc(pos: list[float], neg: list[float]) -> float | None:
    """Fracción de pares (aprobado, descartado) que la señal ordena bien (≈ AUC)."""
    if not pos or not neg or len(pos) * len(neg) < 4:
        return None
    ok = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return ok / (len(pos) * len(neg))


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def compute(ss: list[dict] | None = None) -> dict:
    from .ai.highlights import CRITERIA
    ss = samples() if ss is None else ss
    pos = [s for s in ss if s["label"] > 0]
    neg = [s for s in ss if s["label"] < 0]
    prof: dict = {"n_pos": len(pos), "n_neg": len(neg), "ready": len(pos) >= MIN_POS and len(neg) >= MIN_NEG,
                  "notes": [], "criteria": dict(CRITERIA), "mix": dict(DEFAULT_MIX), "duration": None,
                  "fx_density": 1.0, "model": None, "examples": {"pos": [], "neg": []}}
    # ejemplos (los más recientes; los descartes con nota primero)
    def ex(s):
        return {"title": s["title"], "opening": (s.get("opening") or "")[:220], "duration": s.get("duration"),
                "note": s.get("note"), "clip_id": s.get("clip_id")}
    prof["examples"]["pos"] = [ex(s) for s in pos[:4]]
    prof["examples"]["neg"] = [ex(s) for s in sorted(neg, key=lambda s: not s.get("note"))[:4]]

    # duración preferida
    pd = sorted(s["duration"] for s in pos if s.get("duration"))
    if len(pd) >= MIN_POS:
        lo, hi = pd[max(0, int(len(pd) * 0.2))], pd[min(len(pd) - 1, int(len(pd) * 0.8))]
        prof["duration"] = [round(max(5.0, lo - 3), 1), round(hi + 3, 1)]
        nd = [s["duration"] for s in neg if s.get("duration")]
        if nd and abs(median(pd) - median(nd)) >= 6:
            prof["notes"].append(f"Apruebas clips de ≈{median(pd):.0f} s y descartas los de ≈{median(nd):.0f} s")

    if prof["ready"]:
        # pesos de criterios: suben los que separan aprobados de descartes
        w = {}
        for k, base in CRITERIA.items():
            p = [s["scores"][k] for s in pos if isinstance((s.get("scores") or {}).get(k), (int, float))]
            n = [s["scores"][k] for s in neg if isinstance((s.get("scores") or {}).get(k), (int, float))]
            if len(p) >= 2 and len(n) >= 1:
                d = (sum(p) / len(p) - sum(n) / len(n)) / (_std(p + n) + 5)
                w[k] = base * (1 + max(-0.6, min(1.0, d)))
            else:
                w[k] = base
        tot = sum(w.values()) or 1
        prof["criteria"] = {k: round(v / tot, 3) for k, v in w.items()}
        top = max(prof["criteria"], key=lambda k: prof["criteria"][k] - CRITERIA[k])
        if prof["criteria"][top] - CRITERIA[top] > 0.03:
            prof["notes"].append(f"Lo que más pesa en lo que apruebas: «{top}»")

        # mezcla de la nota final según qué señal acierta más contigo
        acc = {}
        for sig in DEFAULT_MIX:
            a = _pair_acc([_signals(s)[sig] for s in pos if sig in _signals(s)],
                          [_signals(s)[sig] for s in neg if sig in _signals(s)])
            if a is not None:
                acc[sig] = round(a, 2)
        if acc:
            raw = {k: max(acc.get(k, 0.5) - 0.5, 0.0) + 0.05 for k in DEFAULT_MIX}
            tot = sum(raw.values())
            prof["mix"] = {k: round(v / tot, 3) for k, v in raw.items()}
            prof["accuracy"] = acc

        # modelo de afinidad: centroides normalizados
        vecs = [(s["label"], _vec(s)) for s in pos + neg]
        keys = sorted({k for _, v in vecs for k in v if sum(1 for _, u in vecs if k in u) >= len(vecs) * 0.6})
        stats = {}
        for k in keys:
            xs = [v[k] for _, v in vecs if k in v]
            m = sum(xs) / len(xs)
            stats[k] = (m, _std(xs) or 1.0)
        def cen(lbl):
            out = {}
            for k in keys:
                xs = [(v[k] - stats[k][0]) / stats[k][1] for l, v in vecs if l == lbl and k in v]
                if xs:
                    out[k] = sum(xs) / len(xs)
            return out
        prof["model"] = {"stats": stats, "pos": cen(1), "neg": cen(-1)}
        # cuánto acierta la afinidad (en los propios datos; orientativo)
        a = _pair_acc([affinity(s, prof) for s in pos], [affinity(s, prof) for s in neg])
        if a is not None:
            prof.setdefault("accuracy", {})["gusto"] = round(a, 2)

        # rasgos medibles que distinguen tus aprobados
        labels = {"wpm": ("hablan más rápido", "hablan más pausado"), "dead_air": ("tienen más pausas", "tienen menos aire muerto"),
                  "first_word_at": ("tardan más en arrancar", "arrancan a hablar enseguida"),
                  "hook_energy": ("empiezan con más energía", "empiezan más tranquilos")}
        for k, (more, less) in labels.items():
            p = [s["features"][k] for s in pos if isinstance((s.get("features") or {}).get(k), (int, float))]
            n = [s["features"][k] for s in neg if isinstance((s.get("features") or {}).get(k), (int, float))]
            if len(p) >= 2 and len(n) >= 2:
                mp, mn = median(p), median(n)
                if abs(mp - mn) > 0.25 * (abs(mn) + 1e-6) and abs(mp - mn) > {"wpm": 15, "dead_air": 0.05, "first_word_at": 0.5, "hook_energy": 0.15}[k]:
                    prof["notes"].append(f"Tus aprobados {more if mp > mn else less}")

    # densidad de efectos (de los renders de clips aprobados / con 👍)
    def epm(s):
        d = s.get("director") or {}
        dur = d.get("duration") or s.get("duration") or 0
        if not dur:
            return None
        return sum(int(d.get(k, 0) or 0) for k in ("punch", "shake", "meme", "callout", "sfx")) / dur * 60
    pe = [x for x in (epm(s) for s in pos) if x is not None]
    ne = [x for x in (epm(s) for s in neg) if x is not None]
    if len(pe) >= 2:
        allv = pe + ne
        ref = sum(allv) / len(allv) if ne else 9.0  # ~9 efectos/min es lo que pone el director por defecto
        prof["fx_density"] = round(max(0.6, min(1.4, (sum(pe) / len(pe)) / (ref or 1))), 2)
        if abs(prof["fx_density"] - 1) >= 0.1:
            prof["notes"].append(f"Prefieres {'más' if prof['fx_density'] > 1 else 'menos'} efectos: "
                                 f"≈{sum(pe) / len(pe):.0f} por minuto en lo que apruebas")
    return prof


def affinity(s_or_clip: dict, prof: dict | None = None) -> float | None:
    """0-100: cuánto se parece a tus aprobados frente a tus descartes."""
    prof = prof or profile()
    m = prof.get("model")
    if not m:
        return None
    v = _vec(s_or_clip)
    dp = dn = 0.0
    used = 0
    for k, (mu, sd) in m["stats"].items():
        if k not in v:
            continue
        z = (v[k] - mu) / sd
        dp += (z - m["pos"].get(k, 0)) ** 2
        dn += (z - m["neg"].get(k, 0)) ** 2
        used += 1
    if not used:
        return None
    x = (math.sqrt(dn) - math.sqrt(dp)) / math.sqrt(used)
    return round(100 / (1 + math.exp(-2.5 * x)), 1)


# ---------------- caché y uso ----------------

_cache: dict = {}


def _invalidate() -> None:
    _cache.clear()


def profile() -> dict:
    if "p" not in _cache:
        try:
            backfill() if not _cache.get("filled") else None
            _cache["filled"] = True
            p = compute()
        except Exception as exc:  # noqa: BLE001 — sin perfil la app funciona con los valores por defecto
            from .ai.highlights import CRITERIA
            p = {"n_pos": 0, "n_neg": 0, "ready": False, "notes": [], "criteria": dict(CRITERIA),
                 "mix": dict(DEFAULT_MIX), "duration": None, "fx_density": 1.0, "model": None,
                 "examples": {"pos": [], "neg": []}, "error": str(exc)[:200]}
        _cache["p"] = p
        try:
            PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            PROFILE_PATH.write_text(json.dumps({k: v for k, v in p.items() if k != "model"}, ensure_ascii=False,
                                               indent=1), encoding="utf-8")
        except OSError:
            pass
    return _cache["p"]


def prompt_block(max_chars: int = 1400) -> str:
    """Ejemplos reales y rasgos aprendidos para los prompts de selección y del juez."""
    p = profile()
    if not p["examples"]["pos"] and not p["examples"]["neg"]:
        return ""
    lines = ["## Lo que este creador aprueba y descarta (ejemplos REALES; imita lo aprobado, evita lo descartado)"]
    for e in p["examples"]["pos"][:3]:
        lines.append(f"APROBÓ «{e['title']}» ({e['duration']:.0f} s): empieza «{e['opening'][:140]}…»"
                     + (f" — nota: {e['note']}" if e.get("note") else ""))
    for e in p["examples"]["neg"][:3]:
        lines.append(f"DESCARTÓ «{e['title']}» ({e['duration']:.0f} s): empieza «{e['opening'][:140]}…»"
                     + (f" — motivo: {e['note']}" if e.get("note") else ""))
    if p.get("duration"):
        lines.append(f"Duración que suele aprobar: {p['duration'][0]:.0f}-{p['duration'][1]:.0f} s.")
    lines += [f"- {n}" for n in p["notes"][:4]]
    return "\n".join(lines)[:max_chars]


def public() -> dict:
    p = profile()
    from .ai.highlights import CRITERIA
    return {k: v for k, v in p.items() if k != "model"} | {"default_criteria": CRITERIA, "default_mix": DEFAULT_MIX,
                                                         "has_model": bool(p.get("model")),
                                                         "min": {"pos": MIN_POS, "neg": MIN_NEG}}

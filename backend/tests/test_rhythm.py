"""El bucle de estilo copia el RITMO de la referencia (eventos por minuto), no sus segundos exactos."""
from app.studio import autoedit, style_judge as sj


def _ref():
    ev = [{"t": float(t), "type": "caption", "src": 0} for t in (5, 15, 25, 35, 45, 55)]
    ev += [{"t": float(t), "type": "fx/punch", "amount": 1.12, "src": 0} for t in (8, 28, 48)]
    ev.append({"t": 0.0, "type": "scene", "end": 60.0, "src": 0})
    return ev


def _tl():
    words, t = [], 0.0
    frase = "hoy les cuento algo increíble . ¡ es brutal ! nadie lo sabe ¿ verdad ? funciona de verdad .".split()
    while t < 29:
        for w in frase:
            words.append({"start": round(t, 2), "end": round(t + 0.3, 2), "word": w})
            t += 0.4
    return {"duration": 30.0, "words": words, "width": 1080, "height": 1920,
            "tracks": {"video": [{"start": 0.0, "src_in": 0.0, "src_out": 30.0, "fx": [], "zoom": 1.0}],
                       "text": [], "sfx": [], "broll": [], "music": []}}


def test_rhythm_counts_scaled_to_my_duration():
    res = sj.compare_edl(_ref(), sj.edl_metrics(_tl()), 30.0)
    need = {c["type"]: c["count"] for c in res["corrections"]}
    assert need == {"caption": 3, "fx/punch": 2}  # 6/min y 3/min en un video de 30 s
    assert all("t" not in c for c in res["corrections"])  # nada de "pon esto en el segundo X"


def test_corrections_go_to_my_emphasis_with_my_words():
    tl = _tl()
    res = sj.compare_edl(_ref(), sj.edl_metrics(tl), 30.0)
    n = autoedit._apply_corrections(tl, res["corrections"], {}, {"zoom_amount": 1.12})
    assert n == 5
    said = {w["word"].strip(" ,.;:").upper() for w in tl["words"]}
    for t in tl["tracks"]["text"]:
        assert set(t["text"].split()) <= said      # textos con palabras que se dicen, nunca "..."
    starts = sorted(t["start"] for t in tl["tracks"]["text"])
    assert all(b - a >= 3.0 for a, b in zip(starts, starts[1:]))
    punches = [f for f in tl["tracks"]["video"][0]["fx"] if f["type"] == "punch"]
    assert len(punches) == 2 and all(abs(f["amount"] - 0.12) < 1e-6 for f in punches)
    after = sj.compare_edl(_ref(), sj.edl_metrics(tl), 30.0)
    assert after["score"] > res["score"] and not after["corrections"]


def test_reference_check_clamps_unprofessional_values():
    t = {"sfx_per_min": 30.0, "visual_changes_per_min": 1.0, "text_per_min": 2.0,
         "sources": {"sfx_per_min": "referencia", "visual_changes_per_min": "referencia", "text_per_min": "preset"}}
    w = sj.reference_check(t, "short")
    assert {x["key"] for x in w} == {"sfx_per_min", "visual_changes_per_min"}
    assert t["sfx_per_min"] == 15.0 and t["visual_changes_per_min"] == 6.0 and t["text_per_min"] == 2.0

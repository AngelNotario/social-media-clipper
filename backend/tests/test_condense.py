"""Duración final del brief: se queda con las mejores partes hasta el tiempo pedido."""
from app.studio import brief, condense


def _words(segs, text_for):
    out = []
    for k, (a, b) in enumerate(segs):
        t = a
        for w in text_for(k).split():
            out.append({"start": t, "end": t + 0.3, "word": w})
            t += 0.4
            if t > b - 0.3:
                break
    return out


def test_brief_target_duration_normalizes():
    assert brief.normalize({"target_duration": 600})["target_duration"] == 600.0
    assert brief.normalize({"target_duration": "0"})["target_duration"] is None
    assert brief.normalize({"target_duration": "abc"})["target_duration"] is None
    assert brief.normalize({})["target_duration"] is None


def test_condense_keeps_best_blocks_in_order(monkeypatch):
    segs = [(i * 11.0, i * 11.0 + 10.0) for i in range(60)]  # 10 min de tomas de 10 s
    words = _words(segs, lambda k: ("dato importante sobre el juego." if k % 6 < 3 else "eh este esperen está cargando."))
    calls = []

    def fake_scores(blocks, cfg, target, vt, progress):
        calls.append(len(blocks))
        return {bl["id"]: (9 if "importante" in bl["text"] else 2) for bl in blocks}

    monkeypatch.setattr(condense, "_llm_scores", fake_scores)
    out, info = condense.select(segs, words, [], 180, cfg={"url": "", "model": ""})
    total = sum(b - a for a, b in out)
    assert 170 <= total <= 189 and info["method"] == "ia" and calls
    assert out == sorted(out)  # en su orden original
    assert out[0] == segs[0]  # el gancho del inicio se queda
    text = " ".join(w["word"] for w in words if any(a <= w["start"] < b for a, b in out))
    assert text.count("importante") > text.count("cargando")


def test_condense_does_not_stretch_short_videos():
    segs = [(0.0, 30.0), (31.0, 50.0)]
    out, info = condense.select(segs, [], [], 600)
    assert out == segs and info["after"] == info["before"]


def test_condense_without_ai_uses_voice_energy():
    segs = [(i * 10.0, i * 10.0 + 9.5) for i in range(40)]
    words = _words(segs, lambda k: "hola " * 20)
    energy = [0.9 if (i * 0.05) // 100 % 2 == 0 else 0.1 for i in range(8000)]
    out, info = condense.select(segs, words, energy, 120)
    assert info["method"] == "energía y ritmo de voz" and sum(b - a for a, b in out) <= 126

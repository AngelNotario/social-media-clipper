"""Juez de gancho y retención: reglas medibles, recortes y combinación con el juez LLM."""
from app.ai import highlights, judge


def _seg(start, words, gap=0.3, dur=0.35):
    out, t = [], start
    for w in words:
        out.append({"start": round(t, 2), "end": round(t + dur, 2), "word": w})
        t += dur + gap
    return {"start": out[0]["start"], "end": out[-1]["end"], "text": " ".join(words), "words": out}


def _talk(n_sentences=8, start=0.0, pause_first=0.0):
    segs, t = [], start + pause_first
    for k in range(n_sentences):
        words = ["esto", "es", "una", "frase", "con", "algo", "importante", f"número{k}."]
        s = _seg(t, words)
        segs.append(s)
        t = s["end"] + 0.4
    return segs


def test_features_and_rules_good_clip():
    segs = _talk()
    f = judge.features(segs, 0.0, segs[-1]["end"] + 0.3, [0.6] * 200)
    assert f["first_word_at"] == 0.0 and f["hook_words"] >= 4 and f["ends_clean"] and f["starts_clean"]
    score, notes = judge.rule_score(f)
    assert score >= 85, notes


def test_rules_punish_slow_start_and_cut_ending():
    segs = _talk(pause_first=4.0)
    end = segs[-1]["words"][3]["end"] + 0.1  # termina a mitad de frase (la siguiente palabra sigue enseguida)
    f = judge.features(segs, 0.0, end, [0.6] * 200)
    score, notes = judge.rule_score(f)
    assert score < 70
    assert any("empieza a los" in n for n in notes) and any("mitad de frase" in n for n in notes)


def test_apply_trims_snaps_to_words():
    segs = _talk(10)
    end = segs[-1]["end"] + 0.3
    j = {"fix_start": 5.0, "fix_end": end - 6.0}
    ns, ne, info = judge.apply_trims(segs, 0.0, end, j, min_d=15, duration=200)
    starts = [w["start"] for s in segs for w in s["words"]]
    ends = [w["end"] for s in segs for w in s["words"]]
    assert any(abs(ns + 0.12 - x) < 1e-6 for x in starts)
    assert any(abs(ne - 0.35 - x) < 1e-6 for x in ends)
    assert info["start"] > 4 and info["end"] > 4


def test_apply_trims_respects_min_duration():
    segs = _talk(4)
    end = segs[-1]["end"]
    ns, ne, info = judge.apply_trims(segs, 0.0, end, {"fix_start": end - 3, "fix_end": 0}, min_d=15, duration=100)
    assert (ns, ne, info) == (0.0, end, {})


def test_judge_clip_combines_scores(monkeypatch):
    segs = _talk(10)
    c = {"start": 0.0, "end": segs[-1]["end"] + 0.3, "title": "X", "score": 70.0, "scores": {"hook": 70}}
    monkeypatch.setattr(judge, "llm_judge", lambda *a, **k: {
        "hook": 9, "retention": 8, "payoff": 8, "standalone": 7, "verdict": "publicar",
        "reasons": ["Arranca fuerte"], "fix_start": 0, "fix_end": 0, "hook_text": "Nadie esperaba esto", "fix_tip": ""})
    r = judge.judge_clip(c, segs, [0.5] * 300, {"min_dur": 15}, {"url": "u", "model": "gemma4:12b"}, 300)
    assert r["judge"]["model"] == "gemma4:12b" and r["judge"]["llm"] == 82.0
    assert r["scores"]["seleccion"] == 70.0 and r["scores"]["juez"] == 82.0
    assert abs(r["score"] - (70 * 0.4 + 82.0 * 0.4 + r["judge"]["rules"] * 0.2)) < 0.11


def test_judge_suggest_without_applying(monkeypatch):
    segs = _talk(10)
    end = segs[-1]["end"] + 0.3
    monkeypatch.setattr(judge, "llm_judge", lambda *a, **k: {
        "hook": 4, "retention": 5, "payoff": 5, "standalone": 5, "verdict": "mejorar", "reasons": [],
        "fix_start": 5.0, "fix_end": 0, "hook_text": "", "fix_tip": "Empieza en la segunda frase"})
    r = judge.judge_clip({"start": 0.0, "end": end, "title": "X", "score": 60}, segs, [], {"min_dur": 15},
                         {"url": "u", "model": "m"}, 300, apply=False)
    assert r["start"] == 0.0 and r["judge"]["suggest"]["start"] > 4 and r["judge"]["suggest"]["cut_start"] > 4
    assert r["judge"]["suggest"]["end"] == end


def test_judge_error_does_not_break(monkeypatch):
    segs = _talk(10)

    def boom(*a, **k):
        raise RuntimeError("ollama caído")
    monkeypatch.setattr(judge, "llm_judge", boom)
    r = judge.judge_clip({"start": 0.0, "end": segs[-1]["end"], "score": 60}, segs, [], {"min_dur": 15},
                         {"url": "u", "model": "m"}, 300)
    assert "error" in r["judge"] and r["score"] > 0


def test_pick_model_prefers_a_different_model(monkeypatch):
    monkeypatch.setattr(judge.llm, "status", lambda url: {"available": True, "models": ["qwen3:14b", "gemma4:12b"]})
    assert judge.pick_model("u", "qwen3:14b") == "gemma4:12b"
    assert judge.pick_model("u", "gemma4:12b") == "qwen3:14b"
    assert judge.pick_model("u", "qwen3:14b", "qwen3:14b") == "qwen3:14b"


def test_find_highlights_reranks_with_judge(monkeypatch):
    segs = _talk(40)
    dur = segs[-1]["end"] + 1
    cands = [{"start": s["start"], "end": s["start"] + 20, "title": f"c{k}", "hook": "", "reason": "", "suggestions": "",
              "scores": {}, "score": 90 - k, "source": "ai"} for k, s in enumerate(segs[::6])]
    monkeypatch.setattr(highlights.llm, "status", lambda url: {"available": True, "models": ["qwen3:14b"]})
    monkeypatch.setattr(highlights, "llm_moments", lambda *a, **k: [dict(c) for c in cands])
    # el juez prefiere el último candidato
    monkeypatch.setattr(judge, "llm_judge", lambda url, model, title, *a, **k: {
        "hook": 10 if title == "c3" else 3, "retention": 5, "payoff": 5, "standalone": 5,
        "verdict": "publicar" if title == "c3" else "mejorar", "reasons": [], "fix_start": 0, "fix_end": 0,
        "hook_text": "", "fix_tip": ""})
    cfg = {"max_clips": 3, "min_dur": 15, "max_dur": 60}
    best, method = highlights.find_highlights(segs, dur, [0.5] * 1000, [], cfg,
                                              {"url": "u", "model": "qwen3:14b", "judge": {"url": "u", "model": "j"}})
    assert method == "ai" and len(best) == 3 and best[0]["title"] == "c3"
    assert all("judge" in b for b in best)


def test_hook_text_strips_emojis(monkeypatch):
    monkeypatch.setattr(judge.llm, "chat_json", lambda *a, **k: {
        "hook": 5, "retention": 5, "payoff": 5, "standalone": 5, "verdict": "mejorar", "reasons": [],
        "fix_start": 0, "fix_end": 0, "hook_text": "¡NO LO PUEDO CREER! 😱🔥", "fix_tip": ""})
    segs = _talk(4)
    j = judge.llm_judge("u", "m", "t", segs[0]["words"], 0.0, 10.0, {})
    assert j["hook_text"] == "¡NO LO PUEDO CREER!"


def test_ends_clean_without_punctuation_at_pause():
    segs = [_seg(0.0, ["hola", "qué", "tal", "amigos"]), _seg(5.0, ["otra", "cosa"])]
    f = judge.features(segs, 0.0, 3.0, [])
    assert f["ends_clean"]

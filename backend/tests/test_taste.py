"""Perfil de gusto: aprende de aprobados, descartes y 👍/👎."""
from conftest import make_video, needs_ffmpeg
from app import knowledge, taste
from app.ai import highlights


def _s(label, hook, dur, judge_hook=None, dead=0.05, note=None, director=None, cid=None):
    sc = {"hook": hook, "contexto": 60, "emocion": 60, "valor": 60, "narrativa": 60, "retencion": 60}
    j = {"llm": hook * 0.9, "rules": 80.0}
    if judge_hook is not None:
        j.update(hook=judge_hook, retention=6, payoff=6, standalone=6)
    return {"id": cid or id(sc), "clip_id": cid, "label": label, "source": "status", "title": f"clip {hook}",
            "opening": "esto es lo que pasó", "duration": dur, "note": note,
            "features": {"duration": dur, "wpm": 160, "dead_air": dead, "first_word_at": 0.2,
                         "hook_energy": 1.1, "peak_at": 0.7},
            "scores": {**sc, "seleccion": highlights.weighted(sc)}, "judge": j, "director": director or {}}


def _data():
    pos = [_s(1, 85, 30, 9, cid=1), _s(1, 80, 34, 8, cid=2, note="Gran gancho"), _s(1, 90, 28, 9, cid=3),
           _s(1, 78, 32, 8, cid=4)]
    neg = [_s(-1, 40, 55, 3, dead=0.25, cid=5, note="Gancho flojo"), _s(-1, 45, 58, 4, dead=0.3, cid=6),
           _s(-1, 50, 52, 4, dead=0.22, cid=7)]
    return pos + neg


def test_compute_learns_weights_duration_and_affinity():
    p = taste.compute(_data())
    assert p["ready"] and p["n_pos"] == 4 and p["n_neg"] == 3
    assert p["criteria"]["hook"] > highlights.CRITERIA["hook"]  # el gancho separa lo que apruebas
    assert abs(sum(p["criteria"].values()) - 1) < 0.01
    assert p["duration"][0] < 30 and 34 <= p["duration"][1] < 45
    assert any("Apruebas clips de" in n for n in p["notes"])
    assert p["accuracy"]["seleccion"] == 1.0
    good = taste.affinity(_s(1, 88, 31, 9), p)
    bad = taste.affinity(_s(-1, 42, 57, 3, dead=0.28), p)
    assert good > 70 > 30 > bad


def test_not_ready_keeps_defaults_but_has_examples():
    p = taste.compute(_data()[:2])
    assert not p["ready"] and p["criteria"] == highlights.CRITERIA and p["mix"] == taste.DEFAULT_MIX
    assert len(p["examples"]["pos"]) == 2 and p["model"] is None


def test_fx_density_from_liked_renders():
    d_more = {"punch": 8, "shake": 2, "meme": 3, "callout": 3, "sfx": 6, "duration": 30}  # 44/min
    d_less = {"punch": 2, "shake": 0, "meme": 0, "callout": 1, "sfx": 1, "duration": 30}  # 8/min
    data = [_s(1, 80, 30, director=d_more, cid=1), _s(1, 82, 30, director=d_more, cid=2),
            _s(-1, 50, 30, director=d_less, cid=3), _s(-1, 45, 30, director=d_less, cid=4)]
    p = taste.compute(data)
    assert p["fx_density"] > 1.3 and any("más efectos" in n for n in p["notes"])


def test_prompt_block_and_context(client, monkeypatch):
    p = taste.compute(_data())
    monkeypatch.setattr(taste, "profile", lambda: p)
    block = taste.prompt_block()
    assert "APROBÓ «clip" in block and "DESCARTÓ «clip" in block and "Gancho flojo" in block
    ctx = knowledge.prompt_context("clip", None, max_chars=3000)
    assert "DESCARTÓ" in ctx and "Guía de edición" in ctx  # lo aprendido no se pierde al recortar
    assert "DESCARTÓ" not in knowledge.prompt_context("youtube", None)


def test_learned_weights_used_in_selection(monkeypatch):
    p = taste.compute(_data())
    monkeypatch.setattr(taste, "profile", lambda: p)
    w = highlights.learned_weights()
    sc = {"hook": 100, "contexto": 0, "emocion": 0, "valor": 0, "narrativa": 0, "retencion": 0}
    assert highlights.weighted(sc, w) > highlights.weighted(sc)


@needs_ffmpeg
def test_status_changes_record_samples_that_survive(client, dirs):
    taste.reset()
    v = make_video(dirs["videos"] / "gusto.mp4", seconds=20)
    r = client.post("/api/library/import", json={"paths": [str(v)]}).json()
    vid = r["results"][0]["video"]["id"] if r["imported"] else r["results"][0]["video_id"]
    ids = [client.post("/api/clips", json={"video_id": vid, "start_s": a, "end_s": a + 4, "title": f"c{a}"}).json()["id"]
           for a in (1, 6, 11)]
    client.patch(f"/api/clips/{ids[0]}", json={"status": "approved"})
    client.patch(f"/api/clips/{ids[1]}", json={"status": "discarded"})
    client.post("/api/feedback", json={"kind": "clip", "ref_id": ids[1], "rating": -1, "note": "Muy largo"})
    t = client.get("/api/knowledge/taste").json()
    assert (t["n_pos"], t["n_neg"]) == (1, 1)
    assert t["examples"]["neg"][0]["note"] == "Muy largo"
    client.patch(f"/api/clips/{ids[0]}", json={"status": "pending"})  # volver a pendiente: se olvida
    assert client.get("/api/knowledge/taste").json()["n_pos"] == 0
    client.delete(f"/api/clips/{ids[1]}")  # el ejemplo sobrevive aunque el clip se borre
    assert client.get("/api/knowledge/taste").json()["n_neg"] == 1
    assert client.post("/api/knowledge/taste/reset").json()["n_neg"] == 0
    # reiniciar con un clip aún aprobado: NO debe reimportarse al recalcular (antes seguía «1 aprobado»)
    client.patch(f"/api/clips/{ids[2]}", json={"status": "approved"})
    assert client.get("/api/knowledge/taste").json()["n_pos"] == 1
    assert client.post("/api/knowledge/taste/reset").json()["n_pos"] == 0
    assert client.get("/api/knowledge/taste").json()["n_pos"] == 0
    client.delete(f"/api/library/{vid}")

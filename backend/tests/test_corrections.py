"""Correcciones del proceso de edición (21/11/2026): juez estricto, referencia desde el inicio, instrucciones primero,
diccionario de nombres, texto legible y modelos por tarea."""
from app import db
from app.ai import vocab
from app.studio import generative, instructions_plan, reference_rules, strict_judge, style_judge
from app.studio import style as style_mod


def _tl(dur=120.0, zoom_alt=False):
    segs, t, i = [], 0.0, 0
    while t < dur:
        segs.append({"id": f"v{i}", "start": t, "src_in": t, "src_out": t + 10, "transition": "cut",
                     "zoom": 1.12 if (zoom_alt and i % 2) else 1.0,
                     "fx": [{"t": 2.0, "type": "punch", "amount": 0.05 + 0.01 * (i % 4), "hold": 1.0}]})
        t += 10
        i += 1
    texts = [{"id": f"t{k}", "start": 5.0 + 9 * k, "end": 6.2 + 9 * k, "text": "BRUH", "kind": "meme"} for k in range(12)]
    return {"width": 1920, "height": 1080, "duration": dur, "words": [], "captions": {"enabled": False},
            "chroma": {"enabled": False}, "tracks": {"video": segs, "broll": [], "text": texts, "music": [], "sfx": []}}


REF = {"name": "ref", "visual_changes_per_min": 5.3, "overlays_per_min": 1.9, "card_ratio": 0.22, "overlay_ratio": 0.95,
       "fullscreen_ratio": 0.04, "pip_ratio": 0.74, "speaker_visible_ratio": 0.98, "text_per_min": 0.5,
       "captions_ratio": 0.55, "cuts_per_min": 5.3, "zoom_per_min": 0.0, "sfx_per_min": 4.0, "music": "no",
       "memes_ratio": 0.06}


# ---- texto legible (blanco sobre blanco) ----

def test_title_white_on_white_becomes_readable():
    st = style_mod.clean_style({"title_color": "#FFFFFF", "title_bg": "#FFFFFF"})
    assert st["title_color"] == "#111111"
    st = style_mod.clean_style({"title_color": "#FFFFFF", "title_bg": "#7C5CFF"})
    assert st["title_color"] == "#FFFFFF"  # ya contrastaba: no se toca


# ---- diccionario de nombres ----

def test_vocab_fixes_misheard_names(client):
    db.set_setting("vocabulary", ["Eneba", "GTA 6"])
    db.set_setting("vocab_fixes", {})
    words = [{"start": 0, "end": 0.4, "word": "Neva,"}, {"start": 0.5, "end": 0.9, "word": "nueva"},
             {"start": 1, "end": 1.3, "word": "eneba"}]
    out = [w["word"] for w in vocab.apply(words)]
    assert out == ["Eneba,", "nueva", "Eneba"]
    db.set_setting("vocabulary", [])


def test_vocab_learns_from_caption_fixes(client):
    db.set_setting("vocabulary", [])
    db.set_setting("vocab_fixes", {})
    raw = [{"start": 1.0, "end": 1.4, "word": "compré"}, {"start": 1.5, "end": 1.9, "word": "en"},
           {"start": 2.0, "end": 2.4, "word": "Neva"}]
    n = vocab.learn_from_fixes(raw, [{"src_start": 0.9, "src_end": 2.5, "text": "compré en Eneba"}])
    assert n == 1 and db.get_setting("vocab_fixes") == {"neva": "Eneba"} and "Eneba" in db.get_setting("vocabulary")
    assert vocab.apply([{"start": 0, "end": 1, "word": "Neva"}])[0]["word"] == "Eneba"
    db.set_setting("vocabulary", [])
    db.set_setting("vocab_fixes", {})


def test_vocabulary_setting_api(client):
    r = client.put("/api/ai/settings", json={"vocabulary": "Eneba, GTA 6\nEneba; Xbox Series S"})
    assert r.status_code == 200 and r.json()["vocabulary"] == ["Eneba", "GTA 6", "Xbox Series S"]
    client.put("/api/ai/settings", json={"vocabulary": []})


# ---- modelos por tarea ----

def test_role_model_ignores_llamacpp_alias_on_ollama(client):
    db.set_setting("llm_backend", "ollama")
    db.set_setting("writer_model", "smc-qwen36")
    assert db.role_model("writer") == db.get_setting("ollama_model")
    db.set_setting("writer_model", "gemma4:12b")
    assert db.role_model("writer") == "gemma4:12b"
    db.set_setting("writer_model", "")


# ---- la referencia manda desde el inicio ----

def test_reference_rules_before_editing():
    st, pace = {"zoom_amount": 1.12, "transition_style": "dinamico", "memes": True, "music_volume": 0.1}, {"captions_on": False}
    notes = reference_rules.before(st, pace, {"captions": "auto", "music": "auto"}, REF)
    assert st["zoom_amount"] == 1.0 and st["transition_style"] == "cortes" and pace["captions_on"] is True
    assert st["music_volume"] == 0.0 and notes
    st2, pace2 = {"zoom_amount": 1.12}, {"captions_on": False}
    reference_rules.before(st2, pace2, {"captions": "off", "music": "on"}, REF)
    assert pace2["captions_on"] is False  # lo que fijaste en el brief manda sobre la referencia


def test_reference_caps_after_director():
    tl = _tl()
    removed = reference_rules.after_director(tl, REF)
    assert removed["zooms"] == 12 and all(not s["fx"] for s in tl["tracks"]["video"])
    memes = [t for t in tl["tracks"]["text"] if t["kind"] == "meme"]
    assert 4 <= len(memes) <= 7  # 6 % del tiempo de 2 min ≈ 7 s de memes
    tl2 = _tl()
    reference_rules.after_director(tl2, {**REF, "zoom_per_min": 2.0})
    kept = sum(len(s["fx"]) for s in tl2["tracks"]["video"])
    assert kept == 4  # 2 por minuto × 2 min, repartidos


def test_card_plan_follows_reference():
    p = reference_rules.card_plan(REF, "sides")
    assert p["mode"] == "pip" and p["max_len"] >= 9
    assert reference_rules.card_plan(None, "sides")["mode"] == "card"


def _items(dur=120.0, step=6.0):
    out, t, k = [], 0.0, 0
    while t < dur - 3:
        out.append({"need": {"t0": t, "t1": t + 3, "entity": "Xbox", "what": "producto", "label": "", "priority": 2},
                    "resource": {"kind": "media", "asset_id": 300 + k, "media_type": "image"}})
        t += step
        k += 1
    return out


def test_cards_do_not_repeat_or_overlap_existing_broll(client, monkeypatch):
    tl = _tl()
    tl["tracks"]["broll"].append({"id": "b_ed", "asset_id": 300, "start": 0.0, "end": 8.0, "src_in": 0})
    n = generative.place_cards(tl, _items(), 20, "left", "sides", reference_rules.card_plan(REF, "sides"))
    gen = [b for b in tl["tracks"]["broll"] if b.get("gen")]
    assert n == len(gen) and gen
    assert all(b["asset_id"] != 300 for b in gen)  # no repite la foto de la IA editora
    assert all(b["start"] >= 8.5 for b in gen)  # ni la tapa
    assert all(b["mode"] == "pip" and b["pos"]["x"] > 0.5 for b in gen)


# ---- juez estricto (antes: name 'st' is not defined) ----

def test_generative_with_learned_style_runs_strict_judge(client, monkeypatch):
    from app import resources
    monkeypatch.setattr(resources, "gather", lambda *a, **k: _items())
    monkeypatch.setattr(style_judge, "targets_for", lambda sid, vt, es: dict(REF))
    monkeypatch.setattr(strict_judge.sj, "targets_for", lambda sid, vt, es: dict(REF))
    tl = _tl()
    info = generative.run(tl, {"style_id": 99, "video_type": "long", "edit_style": "gaming", "chroma_protected": False,
                               "face_side": "left"})
    strict = [r for r in info["rounds"] if r.get("strict")]
    assert strict and "error" not in strict[0], strict
    assert info["goal"] == 80.0
    assert all(not s.get("fx") or all(f["type"] != "zoom" for f in s["fx"]) for s in tl["tracks"]["video"])


def test_strict_judge_keeps_full_screen_resources_that_match():
    tl = _tl(30)
    tl["words"] = [{"start": 1.0, "end": 1.5, "word": "Xbox"}, {"start": 1.6, "end": 2.0, "word": "Series"}]
    tl["tracks"]["broll"] = [{"id": "b1", "asset_id": 1, "start": 0.5, "end": 4.0}]
    by_id = {1: {"id": 1, "tags": "xbox series consola", "title": "Xbox Series S"}}
    probs = strict_judge._content_judge(tl, tl["words"], 30, by_id)
    assert not [p for p in probs if p["id"] == "b1"]  # antes se marcaba (y borraba) todo recurso a pantalla completa


# ---- instrucciones primero ----

def test_instructions_plan_applies_before_editing():
    b, st, pace = {"captions": "auto", "music": "auto", "chroma": "auto"}, {"zoom_amount": 1.12}, {"broll_every_s": 20}
    d = {"subtitulos": "si", "musica": "no", "zooms": "ninguno", "memes": "no", "transiciones": "igual",
         "recursos": "mas", "pantalla_verde": "si", "puntuales": ["pon 'ERROR #1' cuando digo el primer error"], "resumen": "x"}
    notes, flags = instructions_plan.apply(d, b, st, pace)
    assert pace["captions_on"] and b["music"] == "off" and st["zoom_amount"] == 1.0 and flags["no_zoom"]
    assert st["memes"] is False and flags["resources"] == 1.5 and b["chroma"] == "on" and len(notes) >= 6

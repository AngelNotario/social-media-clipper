"""Revisión preventiva antes de renderizar."""
from app.studio import precheck


def _p(**tl_over):
    tl = {"duration": 20.0, "words": [{"start": 1.4, "end": 1.8, "word": "hola"}],
          "tracks": {"video": [], "broll": [{"start": 15, "end": 25}],
                     "text": [{"start": 2, "end": 4, "text": "", "kind": "callout"},
                              {"start": 5, "end": 5.1, "text": "rápido", "kind": "callout"},
                              {"start": 18, "end": 24, "text": "se sale", "kind": "callout"},
                              {"start": 19.9, "end": 21, "text": "tarde", "kind": "meme"},
                              {"start": 8, "end": 11, "text": "uno", "kind": "callout"},
                              {"start": 10, "end": 12, "text": "dos", "kind": "meme"}]}}
    tl.update(tl_over)
    return {"timeline": tl, "source_kind": "audio", "source_meta": {"has_audio": True}}


def test_fixes_dangling_texts_without_touching_original():
    p = _p()
    tl, rep = precheck.check(p, target_s=60)
    texts = {t["text"]: t for t in tl["tracks"]["text"]}
    assert set(texts) == {"se sale", "uno", "dos"}
    assert texts["se sale"]["end"] == 20.0
    assert tl["tracks"]["broll"][0]["end"] == 20.0
    assert len(p["timeline"]["tracks"]["text"]) == 6  # el proyecto guardado no cambia
    keys = {i["key"] for i in rep["issues"]}
    assert {"silent_start", "text_overlap", "duration"} <= keys
    assert len(rep["fixed"]) >= 4


def test_clean_timeline_has_no_issues():
    p = _p(words=[{"start": 0.1, "end": 0.5, "word": "ya"}])
    p["timeline"]["tracks"]["text"] = [{"start": 3, "end": 5, "text": "ok", "kind": "callout"}]
    p["timeline"]["tracks"]["broll"] = []
    _, rep = precheck.check(p, target_s=20)
    assert rep == {"issues": [], "fixed": []}

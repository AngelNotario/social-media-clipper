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


def test_trims_silent_start_and_shifts_everything():
    p = _p()
    p["timeline"]["tracks"]["video"] = [{"start": 0.0, "src_in": 10.0, "src_out": 18.0, "fx": [{"t": 2.0, "type": "punch"}]},
                                        {"start": 8.0, "src_in": 30.0, "src_out": 42.0, "fx": []}]
    p["timeline"]["tracks"]["sfx"] = [{"start": 0.5}, {"start": 3.0}]
    tl, rep = precheck.check(p, target_s=None)
    assert any("silencio" in f for f in rep["fixed"])
    d = 1.4 - 0.15
    assert tl["tracks"]["video"][0]["src_in"] == round(10.0 + d, 3)
    assert tl["tracks"]["video"][1]["start"] == round(8.0 - d, 3)
    assert abs(tl["words"][0]["start"] - 0.15) < 1e-6
    assert [s["start"] for s in tl["tracks"]["sfx"]] == [round(3.0 - d, 3)]  # el que caía en lo recortado se va
    assert tl["tracks"]["video"][0]["fx"][0]["t"] == round(2.0 - d, 3)
    assert abs(tl["duration"] - (20.0 - d)) < 1e-6
    assert "silent_start" not in {i["key"] for i in rep["issues"]}


def test_trims_black_start(tmp_path):
    import subprocess
    src = tmp_path / "b.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=320x240:d=1:r=25",
                    "-f", "lavfi", "-i", "testsrc2=s=320x240:d=4:r=25", "-filter_complex", "[0][1]concat=n=2:v=1[v]",
                    "-map", "[v]", "-pix_fmt", "yuv420p", str(src)], check=True)
    p = {"timeline": {"duration": 5.0, "words": [], "tracks": {"video": [{"start": 0.0, "src_in": 0.0, "src_out": 5.0}],
                                                              "text": [], "broll": []}},
         "source_kind": "video", "source_path": str(src), "source_meta": {"has_audio": False, "has_video": True}}
    tl, rep = precheck.check(p)
    assert any("negra" in f for f in rep["fixed"])
    assert 0.9 <= tl["tracks"]["video"][0]["src_in"] <= 1.1
    assert "black_hook" not in {i["key"] for i in rep["issues"]}

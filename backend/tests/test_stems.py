"""Separación de voz (armado de pistas alineadas al timeline) y sincronía con el ritmo."""
import wave

import numpy as np

from app.ai import stems
from app.studio import director
from app.studio import timeline as tlm


def _wav(path, seconds, value):
    n = int(seconds * stems.SR)
    data = np.full((n, 2), value, np.float32)
    stems._write(path, data)


def test_blocks_merge_close_ranges():
    b = stems.blocks([(10, 12), (13, 15), (40, 42)], gap=4.0, pad=0.5)
    assert b == [(9.5, 15.5), (39.5, 42.5)]


def test_remix_modes():
    v, m = np.ones((4, 2), np.float32) * 0.5, np.ones((4, 2), np.float32) * 0.4
    assert np.allclose(stems.remix(v, m, "solo_voz"), v)
    assert np.allclose(stems.remix(v, m, "original"), v + m)
    r = stems.remix(v, m, "realzar")
    assert r[0, 0] > v[0, 0] and r[0, 0] < (v + m)[0, 0] + 0.2


def test_timeline_stems_assembles_in_order(tmp_path, monkeypatch):
    # Bloque falso: voz = 0.2 constante, resto = 0.1
    blk = tmp_path / "blk"
    blk.mkdir()
    _wav(blk / "vocals.wav", 30, 0.2)
    _wav(blk / "accomp.wav", 30, 0.1)
    calls = []
    monkeypatch.setattr(stems, "available", lambda: True)
    monkeypatch.setattr(stems, "_free_vram", lambda: None)
    monkeypatch.setattr(stems, "separate", lambda src, a, b, track=None: calls.append((a, b)) or blk)
    src = tmp_path / "src.mp4"
    src.write_bytes(b"x")
    out = stems.timeline_stems(src, [(5, 8), (12, 14)], tmp_path / "work", "realzar")
    assert calls == [(4.5, 14.5)]  # un solo bloque para los dos tramos
    with wave.open(str(out["vocals"])) as w:
        assert abs(w.getnframes() / stems.SR - 5.0) < 0.01
    with wave.open(str(out["mix"])) as w:
        mid = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, 2)[stems.SR] / 32768
    assert abs(mid[0] - (0.2 * 1.12 + 0.1 * 0.56)) < 0.01
    assert stems.public(out)["mode"] == "realzar"


def test_timeline_stems_refuses_huge(tmp_path, monkeypatch):
    monkeypatch.setattr(stems, "available", lambda: True)
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    r = stems.timeline_stems(src, [(0, stems.MAX_SEPARATE + 100)], tmp_path / "w")
    assert "error" in r


def test_beat_grid_loops():
    info = {"beats": [0.5, 1.0, 1.5], "strong": [1.0], "duration": 2.0, "tempo": 120}
    assert stems.beat_grid(info, 0.0, 4.5) == [0.5, 1.0, 1.5, 2.5, 3.0, 3.5]
    assert stems.beat_grid(info, 1.0, 5.0, strong=True) == [2.0, 4.0]


def test_snap_to_beats_moves_effects_and_adds_accents():
    tl = tlm.empty("vertical")
    tl["tracks"]["video"] = [{"id": "v1", "src_in": 0.0, "src_out": 20.0, "zoom": 1.0, "crop_x": 0.5,
                              "fx": [{"t": 2.1, "type": "punch", "amount": 0.16}]}]
    tlm.relayout(tl, [], 20.0)
    tl["tracks"]["video"][0]["fx"] = [{"t": 2.1, "type": "punch", "amount": 0.16}]
    tl["tracks"]["text"].append({"id": "t1", "start": 3.9, "end": 5.0, "text": "WOW", "kind": "meme"})
    tl["tracks"]["sfx"].append({"id": "s1", "asset_id": 1, "start": 6.12, "volume": 0.5})
    grid = [i * 0.5 for i in range(41)]
    r = director.snap_to_beats(tl, grid, strong=[12.0], tol=0.18)
    fx = tl["tracks"]["video"][0]["fx"]
    assert any(abs(f["t"] - 2.0) < 1e-6 for f in fx)
    assert tl["tracks"]["text"][0]["start"] == 4.0 and tl["tracks"]["text"][0]["end"] == 5.1
    assert abs(tl["tracks"]["sfx"][0]["start"] - 5.98) < 1e-6
    assert r["snapped"] == 3 and r["accents"] == 1
    assert any(abs(f["t"] - 12.0) < 1e-6 and f["amount"] == 0.07 and f["hold"] >= 0.35 for f in fx)


def test_snap_ignores_far_beats():
    tl = tlm.empty("vertical")
    tl["tracks"]["video"] = [{"id": "v1", "src_in": 0.0, "src_out": 5.0, "zoom": 1.0, "crop_x": 0.5}]
    tlm.relayout(tl, [], 5.0)
    tl["tracks"]["video"][0]["fx"] = [{"t": 2.3, "type": "shake", "amount": 0.18}]
    director.snap_to_beats(tl, [2.0, 3.0], tol=0.18)
    assert tl["tracks"]["video"][0]["fx"][0]["t"] == 2.3

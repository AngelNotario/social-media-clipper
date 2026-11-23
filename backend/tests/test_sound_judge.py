from app.studio import sound_judge as sj

A = {1: {"id": 1, "title": "whoosh suave", "tags": "sfx whoosh", "duration": 0.6, "available": True, "path": None},
     2: {"id": 2, "title": "vine boom meme", "tags": "sfx meme", "duration": 1.0, "available": True, "path": None},
     3: {"id": 3, "title": "pop", "tags": "sfx pop", "duration": 0.3, "available": True, "path": None},
     4: {"id": 4, "title": "cancion", "tags": "sfx", "duration": 40, "available": True, "path": None}}


def _tl():
    vid = [{"id": "a", "start": 0, "src_in": 0, "src_out": 30, "transition": "cut"},
           {"id": "b", "start": 30, "src_in": 30, "src_out": 60, "transition": "whip"},
           {"id": "c", "start": 60, "src_in": 60, "src_out": 120, "transition": "cut", "fx": [{"t": 5, "type": "punch"}]}]
    sfx = [{"id": "s1", "asset_id": 1, "start": 29.9, "volume": 0.5},   # transición, pero pegado al del texto
           {"id": "s2", "asset_id": 2, "start": 65.0, "volume": 0.6},   # meme en informativo: fuera
           {"id": "s3", "asset_id": 3, "start": 90.0, "volume": 0.5},   # nada en pantalla: fuera
           {"id": "s4", "asset_id": 4, "start": 10.0, "volume": 0.5},   # canción como efecto: fuera
           {"id": "s5", "asset_id": 3, "start": 40.0, "volume": 0.5}]   # texto: se queda
    return {"width": 1920, "height": 1080, "duration": 120, "words": [],
            "tracks": {"video": vid, "sfx": sfx, "text": [{"start": 40.0, "text": "Dato", "kind": "lower"}],
                       "broll": [], "music": []}}


def test_judge_removes_random_and_off_style(monkeypatch):
    monkeypatch.setattr(sj.assets, "get_asset", lambda i: A.get(i))
    tl = _tl()
    rep = sj.judge(tl, {"memes": False}, video_type="long", edit_style="informativo", use_llm=False)
    ids = [x["id"] for x in tl["tracks"]["sfx"]]
    assert ids == ["s5"], rep["changes"]  # el del texto gana; el whoosh queda muy pegado
    motivos = " ".join(c["motivo"] for c in rep["changes"])
    assert "meme" in motivos and "azar" in motivos and "largo" in motivos and "pegado" in motivos


def test_judge_keeps_memes_in_funny_short(monkeypatch):
    monkeypatch.setattr(sj.assets, "get_asset", lambda i: A.get(i))
    tl = _tl()
    tl["width"], tl["height"], tl["duration"] = 1080, 1920, 120
    sj.judge(tl, {"sfx_per_min": 6}, video_type="short", edit_style="entretenimiento", use_llm=False)
    assert "s2" in [x["id"] for x in tl["tracks"]["sfx"]]

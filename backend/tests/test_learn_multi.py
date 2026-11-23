"""Aprender estilos de varios videos: enlaces (yt-dlp), biblioteca, archivos y escucha con earshot."""
import json

from app import db
from app.studio import learn, refs


def test_parse_urls_dedup_and_filter():
    txt = "https://youtu.be/a\nhttps://youtu.be/a  no-es-url, https://www.tiktok.com/@x/video/1"
    assert refs.parse_urls(txt) == ["https://youtu.be/a", "https://www.tiktok.com/@x/video/1"]
    assert refs.parse_urls(None) == []
    assert refs.parse_urls(["https://a.com/v", "ftp://b"]) == ["https://a.com/v"]


def _prof(cuts, spm, roles, sync):
    return {"duration": 30, "edit": {"cuts": cuts, "cuts_per_10s": cuts / 3},
            "sound": {"music_bed": "none"}, "palette": ["#ffffff"],
            "sound_pro": {"sfx_per_min": spm, "music_ratio": 0.6, "cut_sync": sync,
                          "top_sfx": [["whoosh", 3]], "roles": roles}}


def test_combine_uses_medians_and_common_sounds():
    agg = learn.combine([_prof(6, 10, ["transicion"], 0.8), _prof(9, 20, ["transicion", "impacto"], 0.7),
                         _prof(12, 30, ["impacto"], 0.9)])
    assert agg["n_videos"] == 3
    assert agg["edit"]["cuts"] == 9
    assert agg["sound_pro"]["sfx_per_min"] == 20
    assert set(agg["sound_pro"]["roles"]) == {"transicion", "impacto"}
    assert agg["sound_pro"]["top_sfx"][0] == ("whoosh", 9)


def test_to_style_maps_sound_profile():
    st = learn.to_style(learn.combine([_prof(6, 18, ["transicion", "impacto"], 0.8)]))
    assert st["sfx_per_min"] == 18
    assert "transicion" in st["sound_roles"]
    assert st["sfx_on_cuts"] is True
    assert st["music_volume"] > 0


def test_run_learn_multiple_sources_and_append(client, monkeypatch, tmp_path):
    files = {}
    for k in ("a", "b"):
        p = tmp_path / f"{k}.mp4"
        p.write_bytes(b"x")
        files[f"https://example.com/{k}"] = p

    def fake_download(url, progress=None):
        if url.endswith("/roto"):
            raise refs.RefError("Video privado")
        return {"path": str(files[url]), "url": url, "title": url[-1], "uploader": "x", "platform": "youtube"}

    monkeypatch.setattr(refs, "download", fake_download)
    monkeypatch.setattr(learn, "analyze", lambda path, progress=None: _prof(6, 0, [], 0) | {"sound_pro": None})
    monkeypatch.setattr(learn, "listen", lambda paths, **kw: {str(p): {"sfx_per_min": 12, "roles": ["transicion"],
                                                                       "cut_sync": 0.7, "music_ratio": 0.2} for p in paths})
    monkeypatch.setattr(learn, "write_report", lambda agg: "informe")
    job = {"params": {"urls": "https://example.com/a\nhttps://example.com/roto", "name": "Mi estilo"}}
    res = learn.run_learn(job, lambda *a, **k: None)
    assert res["videos"] == 1 and len(res["failed"]) == 1 and res["sound_pro"]
    sid = res["style_id"]

    res2 = learn.run_learn({"params": {"urls": ["https://example.com/b"], "style_id": sid}}, lambda *a, **k: None)
    assert res2["style_id"] == sid and res2["videos"] == 2 and res2["new"] == 1
    with db.get_conn() as conn:
        row = conn.execute("SELECT profile, style FROM learned_styles WHERE id = ?", (sid,)).fetchone()
    prof = json.loads(row["profile"])
    assert prof["n_videos"] == 2 and len(prof["videos"]) == 2
    assert json.loads(row["style"])["sfx_per_min"] == 12


def test_learn_endpoint_validation(client):
    assert client.post("/api/studio/learn", json={}).status_code == 400
    many = [f"https://example.com/{i}" for i in range(13)]
    assert client.post("/api/studio/learn", json={"urls": many}).status_code == 400
    assert client.post("/api/studio/learn", json={"urls": ["https://example.com/a"], "style_id": 99999}).status_code == 404
    st = client.get("/api/studio/status").json()
    assert "earshot_ready" in st and "ytdlp_ready" in st


def test_delete_style(client):
    from app.studio import learn as L
    L.ensure_schema()
    with db.get_conn() as conn:
        sid = conn.execute("INSERT INTO learned_styles (name, profile, style, report) VALUES ('x', '{}', ?, '')",
                           (json.dumps({"caption_color": "#123456"}),)).lastrowid
    db.set_setting("studio_default_style", {"caption_color": "#123456"})
    assert client.delete(f"/api/studio/styles/{sid}").status_code == 200
    assert db.get_setting("studio_default_style") is None
    assert client.delete(f"/api/studio/styles/{sid}").status_code == 404


def test_restricted_video_retries_with_session(client, monkeypatch):
    import sys
    import types
    calls = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        params = {}

        def process_ie_result(self, info, download=True):
            return info

        def extract_info(self, url, download=True):
            calls.append(self.opts)
            if "cookiefile" not in self.opts and "cookiesfrombrowser" not in self.opts:
                raise Exception("ERROR: [youtube] x: Sign in to confirm your age.")
            out = self.opts["outtmpl"].replace("%(ext)s", "mp4")
            self.params = dict(self.opts)
            open(out, "wb").write(b"v")
            return {"title": "T", "uploader": "U", "duration": 10, "extractor_key": "Youtube", "id": "x"}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    refs.COOKIE_FILE.unlink(missing_ok=True)
    db.set_setting("ytdlp_cookies_browser", "")
    import pytest
    with pytest.raises(refs.RefError, match="restricción de edad"):
        refs.download("https://www.youtube.com/watch?v=edad1")
    r = client.post("/api/studio/ref-cookies/file", files={"file": ("cookies.txt", b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tx\n")})
    assert r.status_code == 200 and r.json()["file"]
    res = refs.download("https://www.youtube.com/watch?v=edad2")
    assert res["title"] == "T" and "cookiefile" in calls[-1]
    assert client.post("/api/studio/ref-cookies/file", files={"file": ("c.txt", b"hola")}).status_code == 400
    client.delete("/api/studio/ref-cookies/file")
    assert client.put("/api/studio/ref-cookies", json={"browser": "firefox"}).json()["browser"] == "firefox"
    assert refs.cookie_source() == {"cookiesfrombrowser": ("firefox",)}
    db.set_setting("ytdlp_cookies_browser", "")

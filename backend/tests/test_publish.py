"""Publicación en redes: metadatos, OAuth, subidas (con transportes simulados) y la cola programada."""
import json
from datetime import datetime, timedelta

import httpx
import pytest

from app import db
from app.ai import llm
from app.publish import metadata, oauth, platforms, runner, store
from conftest import make_video, needs_ffmpeg


def _mock(monkeypatch, handler):
    mk = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # noqa: E731
    monkeypatch.setattr(platforms, "http", mk)
    monkeypatch.setattr(oauth, "http", mk)


SRC = {"title": "El doctor no lo podía creer", "hook": "Nadie esperaba esto", "text": "el doctor revisa la radiografía",
       "duration": 42, "vertical": True}


def test_finalize_rules():
    raw = {"youtube": {"title": "Título " * 30, "description": "Mira esto", "tags": ["doctor", "#salud", "doctor"],
                       "category": "Comedia"},
           "tiktok": {"caption": "Gancho corto", "audio_tip": "suspenso", "search_terms": ["doctor reacción"]},
           "instagram": {"caption": "Primera línea", "audio_name": "Audio original del doctor que no lo creía jaja"},
           "hashtags": ["Médicos", "#fyp", "humor médico", "viral", "salud", "hospital", "doctores", "risas"],
           "thumbnail_text": "NO LO CREÍA"}
    out = metadata.finalize(raw, SRC, {**metadata.DEFAULT_PROFILE, "brand_hashtags": "#MiCanal", "cta": "Sígueme"})
    assert "medicos" in out["hashtags"] and "fyp" not in out["hashtags"] and "viral" not in out["hashtags"]
    assert "humormedico" in out["hashtags"]
    yt = out["youtube"]
    assert len(yt["title"]) <= 89 and yt["category_id"] == "23" and yt["is_short"]
    assert "#shorts" in yt["description"] and "Sígueme" in yt["description"]
    assert [t.lower() for t in yt["tags"]].count("doctor") == 1
    assert len(out["tiktok"]["hashtags"]) <= 5 and "#micanal" in out["tiktok"]["caption"]
    assert len(out["instagram"]["audio_name"]) <= 41
    raw2 = {**raw, "hashtags": ["doctorquevendepaletasdehistoria", "medicina"],
            "youtube": {**raw["youtube"], "description": "Gran historia. Busca: doctor, paletas.\n#a #b",
                        "tags": ["doctor y TikTok", "doctor gracioso"]}}
    o2 = metadata.finalize(raw2, SRC, metadata.DEFAULT_PROFILE)
    assert "doctorquevendepaletasdehistoria" not in o2["hashtags"] and "medicina" in o2["hashtags"]
    assert "Busca" not in o2["youtube"]["description"] and "#a" not in o2["youtube"]["description"]
    assert o2["youtube"]["tags"] == ["doctor gracioso"]
    assert {"humor", "comedia"} <= set(out["hashtags"])  # amplios de la categoría
    o3 = metadata.finalize({**raw, "youtube": {**raw["youtube"], "tags": ["juegos 2019", "juegos clásicos"]}}, SRC, metadata.DEFAULT_PROFILE)
    assert o3["youtube"]["tags"] == ["juegos clásicos"]
    assert out["instagram"]["caption"].startswith("Primera línea")


def test_generate_falls_back_without_ai(client, monkeypatch):
    def boom(*a, **k):
        raise llm.LLMError("sin ollama")
    monkeypatch.setattr(llm, "chat_json", boom)
    out = metadata.generate(SRC)
    assert out["warning"] and out["youtube"]["title"] and out["tiktok"]["caption"]


def test_suggest_time_future():
    t = metadata.suggest_time("tiktok", datetime(2026, 1, 1, 22, 0))
    assert t > datetime(2026, 1, 1, 22, 0) and t.hour in (13, 19, 21)


def test_oauth_start_urls(client):
    with pytest.raises(oauth.OAuthError):
        oauth.start("youtube", "http://localhost:8010/")
    store.set_creds("tiktok", {"client_key": "ck", "client_secret": "cs"})
    url = oauth.start("tiktok", "http://localhost:8010/")
    q = httpx.URL(url).params
    assert q["redirect_uri"] == "http://localhost:8010/api/publish/oauth/tiktok/callback/"
    assert len(q["code_challenge"]) == 64 and all(c in "0123456789abcdef" for c in q["code_challenge"])
    store.set_creds("youtube", {"client_id": "id", "client_secret": "sec"})
    q = httpx.URL(oauth.start("youtube", "http://localhost:8010/")).params
    assert q["access_type"] == "offline" and "youtube.upload" in q["scope"]
    assert store.creds_public("youtube")["client_secret"] == "•" * 8
    store.set_creds("youtube", {"client_secret": "•" * 8})  # enmascarado: no se pisa
    assert store.get_creds("youtube")["client_secret"] == "sec"


def test_oauth_finish_youtube(client, monkeypatch):
    store.set_creds("youtube", {"client_id": "id", "client_secret": "sec"})
    url = oauth.start("youtube", "http://localhost:8010/")
    state = httpx.URL(url).params["state"]

    def handler(req: httpx.Request):
        if "oauth2.googleapis.com" in str(req.url):
            assert b"code_verifier" in req.content
            return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})
        return httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "Mi Canal", "thumbnails": {}}}]})
    _mock(monkeypatch, handler)
    res = oauth.finish(state, "code")
    assert res["accounts"] == ["Mi Canal"]
    acc = [a for a in store.list_accounts("youtube") if a["remote_id"] == "UC1"][0]
    assert acc["tokens"]["refresh_token"] == "RT"
    with pytest.raises(oauth.OAuthError):
        oauth.finish(state, "code")  # el estado se usa una sola vez


def test_token_refresh(client, monkeypatch):
    store.set_creds("youtube", {"client_id": "id", "client_secret": "sec"})
    aid = store.save_account("youtube", "UC9", "Viejo", {"access_token": "old", "refresh_token": "RT", "expires_at": 0})
    _mock(monkeypatch, lambda req: httpx.Response(200, json={"access_token": "new", "expires_in": 3600}))
    assert oauth.access_token(store.get_account(aid)) == "new"
    assert store.get_account(aid)["tokens"]["refresh_token"] == "RT"


def _acc(platform, rid="X"):
    aid = store.save_account(platform, rid, "cuenta", {"access_token": "AT", "expires_at": 9e12})
    return store.get_account(aid)


def test_youtube_upload(client, monkeypatch, tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"0" * 1000)
    seen = {}

    def handler(req: httpx.Request):
        if req.method == "POST":
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, headers={"Location": "https://upload.example/session"})
        assert req.headers["Content-Range"] == "bytes 0-999/1000"
        return httpx.Response(200, json={"id": "abc123", "status": {"privacyStatus": "private"}})
    _mock(monkeypatch, handler)
    meta = {"title": "Hola", "description": "d", "tags": ["a" * 60] * 20, "category_id": "20"}
    res = platforms.youtube_upload(_acc("youtube"), f, meta, {"privacy": "public", "vertical": True}, lambda *a: None)
    assert res["remote_url"] == "https://youtube.com/shorts/abc123" and "auditado" in res["note"]
    assert sum(len(t) + 1 for t in seen["body"]["snippet"]["tags"]) <= 480
    assert seen["body"]["status"]["selfDeclaredMadeForKids"] is False


def test_tiktok_chunks():
    assert platforms._tt_chunks(30 * 1024 * 1024) == (30 * 1024 * 1024, 1)
    chunk, n = platforms._tt_chunks(105 * 1024 * 1024)
    assert chunk == 10 * 1024 * 1024 and n == 10  # el último trozo lleva el resto (15 MB)


def test_tiktok_inbox_upload(client, monkeypatch, tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"1" * 500)
    calls = []

    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if req.url.path.endswith("/inbox/video/init/"):
            assert json.loads(req.content)["source_info"]["total_chunk_count"] == 1
            return httpx.Response(200, json={"data": {"upload_url": "https://up.tt/x", "publish_id": "p1"},
                                             "error": {"code": "ok"}})
        if req.method == "PUT":
            return httpx.Response(201)
        return httpx.Response(200, json={"data": {"status": "SEND_TO_USER_INBOX"}, "error": {"code": "ok"}})
    _mock(monkeypatch, handler)
    res = platforms.tiktok_upload(_acc("tiktok"), f, {"caption": "hola #tag"}, {"mode": "inbox", "_poll_s": 0},
                                  lambda *a: None)
    assert res["status"] == "SEND_TO_USER_INBOX" and "bandeja" in res["note"]


def test_tiktok_unaudited_message(client, monkeypatch, tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"1" * 10)

    def handler(req):
        if "creator_info" in str(req.url):
            return httpx.Response(200, json={"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        return httpx.Response(403, json={"error": {"code": "unaudited_client_can_only_post_to_private_accounts"}})
    _mock(monkeypatch, handler)
    with pytest.raises(platforms.PublishError, match="Borrador en bandeja"):
        platforms.tiktok_upload(_acc("tiktok"), f, {"caption": "x"}, {"mode": "direct", "_poll_s": 0}, lambda *a: None)


def test_instagram_upload(client, monkeypatch, tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"2" * 300)
    step = []

    def handler(req: httpx.Request):
        u = str(req.url)
        if u.endswith("/media") and req.method == "POST":
            body = dict(httpx.QueryParams(req.content.decode()))
            assert body["media_type"] == "REELS" and body["upload_type"] == "resumable" and body["audio_name"] == "Mi audio"
            return httpx.Response(200, json={"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/C1"})
        if "rupload" in u:
            assert req.headers["file_size"] == "300" and req.headers["offset"] == "0"
            return httpx.Response(200, json={"success": True})
        if "media_publish" in u:
            return httpx.Response(200, json={"id": "M1"})
        if "/C1" in u:
            step.append(1)
            return httpx.Response(200, json={"status_code": "FINISHED" if len(step) > 1 else "IN_PROGRESS"})
        return httpx.Response(200, json={"permalink": "https://instagram.com/reel/xyz"})
    _mock(monkeypatch, handler)
    res = platforms.instagram_upload(_acc("instagram", "IG1"), f, {"caption": "hola", "audio_name": "Mi audio"},
                                     {"_poll_s": 0}, lambda *a: None)
    assert res == {"remote_id": "M1", "remote_url": "https://instagram.com/reel/xyz"}


@needs_ffmpeg
def test_publish_api_flow(client, monkeypatch, dirs):
    v = make_video(dirs["videos"] / "pub.mp4", 360, 640, 2)
    with db.get_conn() as conn:
        vid = conn.execute("INSERT INTO videos (title, original_path, stored_path, storage_mode) VALUES ('t', ?, ?, 'reference')",
                           (str(v), str(v) + "#pub")).lastrowid
        cid = conn.execute("INSERT INTO clips (video_id, start_s, end_s, title, hook) VALUES (?, 0, 2, 'Clip prueba', 'gancho')",
                           (vid,)).lastrowid
        rid = conn.execute("INSERT INTO renders (clip_id, path) VALUES (?, ?)", (cid, str(v))).lastrowid
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("x")))
    r = client.post("/api/publish/metadata", json={"source_kind": "clip_render", "source_id": rid})
    assert r.status_code == 200 and r.json()["source"]["vertical"] is True
    meta = r.json()["meta"]
    acc = _acc("instagram", "IGAPI")
    later = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    body = {"source_kind": "clip_render", "source_id": rid, "scheduled_at": later,
            "targets": [{"platform": "instagram", "account_id": acc["id"], "meta": meta["instagram"]}]}
    r = client.post("/api/publish/posts", json=body)
    assert r.status_code == 200
    pid = r.json()["ids"][0]
    assert store.get_post(pid)["status"] == "scheduled"
    assert client.post(f"/api/publish/posts/{pid}/cancel").json()["status"] == "cancelled"
    monkeypatch.setitem(platforms.UPLOADERS, "instagram",
                        lambda acc, path, meta, opts, progress: {"remote_id": "M9", "remote_url": "https://ig/x"})
    out = client.post(f"/api/publish/posts/{pid}/now").json()
    assert store.get_post(pid)["status"] == "published", out
    assert store.get_post(pid)["remote_url"] == "https://ig/x"
    # sin cuenta conectada → 400
    bad = {**body, "targets": [{"platform": "youtube", "account_id": 99999, "meta": {}}]}
    assert client.post("/api/publish/posts", json=bad).status_code == 400
    st = client.get("/api/publish/status").json()
    assert st["platforms"]["tiktok"]["redirect_uri"].endswith("/tiktok/callback/")


def test_failed_upload_is_reported(client, monkeypatch, tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    acc = _acc("youtube", "UCF")
    pid = store.create_post(batch="b", platform="youtube", account_id=acc["id"], source_kind="clip_render", source_id=1,
                            file_path=str(f), title="t", meta={}, options={}, status="queued")

    def boom(*a):
        raise platforms.PublishError("YouTube: se agotó la cuota diaria")
    monkeypatch.setitem(platforms.UPLOADERS, "youtube", boom)
    runner.tick()
    p = store.get_post(pid)
    assert p["status"] == "failed" and "cuota" in p["error"]

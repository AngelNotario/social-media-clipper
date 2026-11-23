from conftest import make_video, needs_ffmpeg


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_settings_default_folder(client, dirs):
    data = client.get("/api/settings").json()
    paths = [f["path"] for f in data["authorized_folders"]]
    assert str(dirs["videos"]) in paths


def test_settings_rejects_missing_folder(client):
    r = client.put("/api/settings", json={"authorized_folders": ["Z:/no/existe/123"]})
    assert r.status_code == 400


def test_browse_roots(client, dirs):
    data = client.get("/api/browse").json()
    assert data["path"] is None
    assert any(r["path"] == str(dirs["videos"]) for r in data["roots"])


def test_browse_blocks_outside_folder(client, dirs):
    r = client.get("/api/browse", params={"path": str(dirs["outside"])})
    assert r.status_code == 403


def test_browse_blocks_traversal(client, dirs):
    sneaky = str(dirs["videos"]) + "/../fuera"
    r = client.get("/api/browse", params={"path": sneaky})
    assert r.status_code == 403


def test_browse_lists_only_videos(client, dirs):
    (dirs["videos"] / "notas.txt").write_text("hola")
    (dirs["videos"] / "subcarpeta").mkdir(exist_ok=True)
    (dirs["videos"] / "falso.mp4").write_bytes(b"no soy video")
    data = client.get("/api/browse", params={"path": str(dirs["videos"])}).json()
    names = [f["name"] for f in data["files"]]
    assert "notas.txt" not in names
    assert "falso.mp4" in names
    assert any(d["name"] == "subcarpeta" for d in data["dirs"])
    assert data["parent"] is None  # estamos en la raíz autorizada


@needs_ffmpeg
def test_import_rejects_fake_video(client, dirs):
    r = client.post("/api/library/import",
                    json={"paths": [str(dirs["videos"] / "falso.mp4")]}).json()
    assert r["imported"] == 0
    assert r["results"][0]["ok"] is False


@needs_ffmpeg
def test_import_rejects_outside(client, dirs):
    v = make_video(dirs["outside"] / "fuera.mp4")
    r = client.post("/api/library/import", json={"paths": [str(v)]}).json()
    assert r["imported"] == 0
    assert "fuera de las carpetas" in r["results"][0]["error"]


@needs_ffmpeg
def test_import_play_rename_remove(client, dirs):
    v = make_video(dirs["videos"] / "subcarpeta" / "vertical.mp4", w=360, h=640)
    r = client.post("/api/library/import", json={"paths": [str(v)], "mode": "reference"}).json()
    assert r["imported"] == 1, r
    video = r["results"][0]["video"]
    assert video["width"] == 360 and video["height"] == 640
    assert video["orientation"] == "vertical"
    assert video["has_audio"] is True
    assert 1.5 < video["duration"] < 2.5
    vid = video["id"]

    # Duplicado
    dup = client.post("/api/library/import", json={"paths": [str(v)]}).json()
    assert dup["imported"] == 0 and dup["results"][0]["video_id"] == vid

    # Aparece como importado en el explorador
    data = client.get("/api/browse", params={"path": str(v.parent)}).json()
    assert data["files"][0]["imported_id"] == vid
    assert data["parent"] == str(dirs["videos"])

    # Miniatura
    t = client.get(f"/api/library/{vid}/thumbnail")
    assert t.status_code == 200 and t.headers["content-type"] == "image/jpeg"

    # Streaming completo y por rangos (lo que usa el reproductor del navegador)
    size = v.stat().st_size
    full = client.get(f"/api/library/{vid}/stream")
    assert full.status_code == 200 and len(full.content) == size
    part = client.get(f"/api/library/{vid}/stream", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 0-99/{size}"
    assert part.content == v.read_bytes()[:100]
    tail = client.get(f"/api/library/{vid}/stream", headers={"Range": "bytes=-50"})
    assert tail.content == v.read_bytes()[-50:]
    bad = client.get(f"/api/library/{vid}/stream", headers={"Range": f"bytes={size + 10}-"})
    assert bad.status_code == 416

    # Renombrar y buscar
    client.patch(f"/api/library/{vid}", json={"title": "Mi clip vertical"})
    found = client.get("/api/library", params={"q": "clip vertical"}).json()
    assert found["total"] == 1
    assert client.get("/api/library", params={"orientation": "horizontal"}).json()["total"] == 0

    # Dashboard
    st = client.get("/api/system/status").json()
    assert st["library"]["videos"] >= 1 and st["library"]["vertical"] >= 1

    # Quitar de la biblioteca NO borra el archivo original
    assert client.delete(f"/api/library/{vid}").json() == {"ok": True}
    assert v.exists()
    assert client.get(f"/api/library/{vid}").status_code == 404


@needs_ffmpeg
def test_import_copy_mode(client, dirs):
    import os
    v = make_video(dirs["videos"] / "copia.mp4")
    r = client.post("/api/library/import", json={"paths": [str(v)], "mode": "copy"}).json()
    video = r["results"][0]["video"]
    stored = video["stored_path"]
    assert stored != str(v) and os.path.exists(stored)
    assert os.environ["SMC_DATA_DIR"] in stored
    client.delete(f"/api/library/{video['id']}")
    assert not os.path.exists(stored)  # se borra solo la copia interna
    assert v.exists()                  # el original sigue intacto


def test_persistence_across_restart(client, dirs):
    client.put("/api/settings", json={"authorized_folders": [str(dirs["videos"]), str(dirs["outside"])]})
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c2:
        paths = [f["path"] for f in c2.get("/api/settings").json()["authorized_folders"]]
    assert str(dirs["outside"]) in paths
    client.put("/api/settings", json={"authorized_folders": [str(dirs["videos"])]})

"""Biblioteca en tiempo real: refleja tus carpetas autorizadas."""
import os
import time

from app import library_sync
from conftest import VIDEOS_DIR, make_video, needs_ffmpeg


@needs_ffmpeg
def test_new_videos_appear_and_deleted_ones_disappear(client):
    sub = VIDEOS_DIR / "sync_sub"
    sub.mkdir(exist_ok=True)
    v = make_video(sub / "nuevo_sync.mp4", seconds=4)
    old = time.time() - 60
    os.utime(v, (old, old))  # ya terminó de grabarse
    rec = make_video(sub / "grabando_sync.mp4", seconds=4)  # recién modificado: aún grabándose
    if v.stat().st_size < library_sync.MIN_BYTES:
        library_sync.MIN_BYTES = 1
    library_sync._run()
    titles = [i["title"] for i in client.get("/api/library").json()["items"]]
    assert "nuevo_sync" in titles and "grabando_sync" not in titles
    assert any(x["title"] == "nuevo_sync" for x in client.get("/api/videos/overview").json())
    v.unlink()
    items = client.get("/api/library").json()["items"]
    assert "nuevo_sync" not in [i["title"] for i in items]  # lo borraste: ya no se muestra
    assert "nuevo_sync" in [i["title"] for i in client.get("/api/library?include_missing=true").json()["items"]]
    assert all(x["title"] != "nuevo_sync" for x in client.get("/api/videos/overview").json())
    rec.unlink()
    library_sync.MIN_BYTES = 200_000


def test_scan_ignores_hidden_folders_and_other_files(client):
    hid = VIDEOS_DIR / ".oculta"
    hid.mkdir(exist_ok=True)
    (hid / "x.mp4").write_bytes(b"0" * 300_000)
    (VIDEOS_DIR / "nota.txt").write_text("hola")
    paths = [str(p) for p in library_sync.scan()]
    assert not any(".oculta" in p or p.endswith(".txt") for p in paths)

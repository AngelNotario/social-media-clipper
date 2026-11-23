"""Tarjetas de b-roll (card/pip con marco, rótulo y animación) y fondo del chroma por tramos."""
import subprocess

from PIL import Image

from app.studio import assets, render
from app.studio import timeline as tlm
from conftest import needs_ffmpeg


def _solid_video(path, seconds=4, color="0x808080", w=640, h=360):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:d={seconds}:r=30",
                    "-f", "lavfi", "-i", f"sine=f=300:d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def _image(path, color, size=(400, 300)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def _project(client, path):
    r = client.post("/api/library/import", json={"paths": [str(path)]}).json()
    x = r["results"][0]
    vid = x["video"]["id"] if x.get("video") else x.get("video_id")
    res = client.post("/api/studio/projects", json={"name": "cards", "source_type": "library", "video_id": vid}).json()
    return res["project"]["id"]


def _render_frames(pid, tmp_path, times):
    out = tmp_path / "out.mp4"
    cmd, _, _ = render.build_command(render.projects.get(pid), "preview", tmp_path, out)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr[-800:] + (tmp_path / "graph.txt").read_text()[-1500:]
    frames = []
    for i, t in enumerate(times):
        f = tmp_path / f"f{i}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(out), "-frames:v", "1", str(f)], check=True)
        frames.append(Image.open(f).convert("RGB"))
    return frames


def _close(px, rgb, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(px, rgb))


def test_broll_opts_defaults_and_clamp():
    assert tlm.broll_opts({})["mode"] == "full" and tlm.broll_opts({})["kenburns"] is False
    o = tlm.broll_opts({"mode": "card", "scale": 3, "rot": 40, "anim": "x", "pos": {"x": 2}}, is_image=True)
    assert o["scale"] == 1.0 and o["rot"] == 12 and o["anim"] == "pop" and o["pos"]["x"] == 1.0
    assert o["frame"] == "white" and o["kenburns"] is True
    it = {"start": 0, "end": 1, "mode": "pip", "rot": -50}
    tlm.norm_broll(it)
    assert it["rot"] == -12 and it["mode"] == "pip"
    legacy = {"start": 0, "end": 1, "asset_id": 1}
    assert tlm.norm_broll(dict(legacy)) == legacy  # los b-roll de antes no cambian
    sch = tlm.bg_schedule([{"start": 2, "end": 4, "asset_id": 3, "gen": True}, {"start": 0, "end": 2.5, "kind": "text",
                           "text": "HOLA"}, {"start": 5, "end": 6}], 10)
    assert [x["kind"] for x in sch] == ["text", "media"] and sch[1]["start"] == 2.5 and sch[1]["gen"] is True


@needs_ffmpeg
def test_image_card_and_legacy_full(client, dirs, tmp_path):
    pid = _project(client, _solid_video(dirs["videos"] / "gris.mp4"))
    red = assets._upsert(_image(dirs["videos"] / "rojo.png", (220, 20, 30)), "broll", "image", "rojo", "test")
    blue = assets._upsert(_image(dirs["videos"] / "azul.png", (20, 40, 220)), "broll", "image", "azul", "test")
    p = client.get(f"/api/studio/projects/{pid}").json()
    tl = p["timeline"]
    tl["tracks"]["broll"] = [
        {"asset_id": red, "start": 0.3, "end": 2.2, "mode": "card", "pos": {"x": 0.68, "y": 0.45}, "scale": 0.4,
         "anim": "none", "kenburns": False, "label": "RTX 4080"},
        {"asset_id": blue, "start": 2.6, "end": 3.6},  # b-roll de antes: pantalla completa
    ]
    assert client.put(f"/api/studio/projects/{pid}/timeline", json={"timeline": tl}).status_code == 200
    tl = render.projects.get(pid)["timeline"]
    W, H = tl["width"], tl["height"]
    f_card, f_full = _render_frames(pid, tmp_path, [1.2, 3.1])
    o = tlm.broll_opts(tl["tracks"]["broll"][0], True)
    g = render.card_geometry(400, 300, o, W, H)
    fcx = min(max(0.68 * W, g["fw"] / 2), W - g["fw"] / 2)
    fcy = min(max(0.45 * H, g["fh"] / 2), H - g["fh"] / 2)
    sx, sy = f_card.width / W, f_card.height / H
    assert _close(f_card.getpixel((int(fcx * sx), int(fcy * sy))), (220, 20, 30))  # la imagen
    bx = int((fcx - g["fw"] / 2 + g["b"] / 2) * sx)
    assert _close(f_card.getpixel((bx, int(fcy * sy))), (255, 255, 255))  # el marco blanco
    assert _close(f_card.getpixel((3, 3)), (128, 128, 128))  # lo demás sigue siendo la toma
    assert _close(f_full.getpixel((f_full.width // 2, f_full.height // 2)), (20, 40, 220))
    assert _close(f_full.getpixel((3, 3)), (20, 40, 220))


@needs_ffmpeg
def test_video_pip_rotated_with_label(client, dirs, tmp_path):
    pid = _project(client, _solid_video(dirs["videos"] / "gris2.mp4"))
    clip = _solid_video(dirs["videos"] / "clip_rojo.mp4", seconds=3, color="0xDC141E")
    aid = assets._upsert(clip, "broll", "video", "clip", "test")
    tl = client.get(f"/api/studio/projects/{pid}").json()["timeline"]
    tl["tracks"]["broll"] = [{"asset_id": aid, "start": 0.5, "end": 3.0, "mode": "pip", "rot": 5, "anim": "slide_left",
                              "label": "Tráiler", "pos": {"x": 0.5, "y": 0.4}, "scale": 0.5, "volume": 0.3}]
    client.put(f"/api/studio/projects/{pid}/timeline", json={"timeline": tl})
    tl = render.projects.get(pid)["timeline"]
    (f,) = _render_frames(pid, tmp_path, [2.0])
    assert _close(f.getpixel((int(f.width * 0.5), int(f.height * 0.4))), (220, 20, 30))
    assert _close(f.getpixel((3, 3)), (128, 128, 128))


@needs_ffmpeg
def test_chroma_bg_schedule(client, dirs, tmp_path):
    fg = dirs["videos"] / "verde.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x00A800:s=640x360:d=4:r=30",
                    "-f", "lavfi", "-i", "color=c=0xC88060:s=120x100:d=4:r=30", "-f", "lavfi", "-i", "sine=f=300:d=4",
                    "-filter_complex", "[0][1]overlay=20:20,format=yuv420p[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(fg)], check=True)
    pid = _project(client, fg)
    blue = assets._upsert(_image(dirs["videos"] / "logo.png", (20, 40, 220), (300, 300)), "broll", "image", "logo", "t")
    tl = client.get(f"/api/studio/projects/{pid}").json()["timeline"]
    tl["chroma"] = {"enabled": True, "color": "#00A800", "similarity": 0.12, "bg": {"kind": "color", "color": "#000000"},
                    "bg_schedule": [
                        {"start": 0, "end": 1.6, "kind": "text", "text": "Marketplace", "bg_color": "#E8424F"},
                        {"start": 1.6, "end": 3.0, "asset_id": blue, "fit": "contain", "bg_color": "#FFFF00",
                         "anim": "fade", "kenburns": False}]}
    assert client.put(f"/api/studio/projects/{pid}/timeline", json={"timeline": tl}).status_code == 200
    tl = render.projects.get(pid)["timeline"]
    assert len(tl["chroma"]["bg_schedule"]) == 2
    f1, f2, f3 = _render_frames(pid, tmp_path, [0.8, 2.5, 3.6])
    W = f1.width
    assert _close(f1.getpixel((W - 4, f1.height - 4)), (0xE8, 0x42, 0x4F))  # tarjeta de texto
    assert _close(f2.getpixel((W - 4, f2.height - 4)), (255, 255, 0))       # logo centrado sobre amarillo
    assert _close(f2.getpixel((W // 2, f2.height // 2)), (20, 40, 220))
    assert _close(f3.getpixel((W - 4, f3.height - 4)), (0, 0, 0))           # hueco: fondo de siempre

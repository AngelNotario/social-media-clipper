"""Pantalla verde: normalización, detección del verde real, render con fondo y operación del asistente."""
import json
import subprocess

from app import db
from app.studio import agent, chroma, render
from conftest import make_video, needs_ffmpeg


def test_normalize_clamps():
    c = chroma.normalize({"enabled": 1, "color": "nope", "similarity": 9, "subject": {"scale": 0.1, "x": 3}})
    assert c["enabled"] and c["color"] == "#00FF00" and c["similarity"] == 0.6
    assert c["subject"]["scale"] == 0.25 and c["subject"]["x"] == 1.0
    assert chroma.despill_type("#22C033") == "green" and chroma.despill_type("#1030E0") == "blue"


def _green_clip(path, seconds=3):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x2BB548:s=360x640:d={seconds}",
                    "-f", "lavfi", "-i", f"color=c=0xC88060:s=120x260:d={seconds}", "-f", "lavfi", "-i",
                    f"sine=f=300:d={seconds}", "-filter_complex", "[0][1]overlay=120:300,format=yuv420p[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


@needs_ffmpeg
def test_detect_color(dirs):
    p = _green_clip(dirs["videos"] / "verde.mp4")
    d = chroma.detect_color(p, 1.0)
    assert d["detected"]
    r, g, b = (int(d["color"][i:i + 2], 16) for i in (1, 3, 5))
    assert abs(r - 0x2B) < 20 and abs(g - 0xB5) < 20 and abs(b - 0x48) < 20


@needs_ffmpeg
def test_render_with_chroma_background(client, dirs, tmp_path):
    import shutil
    fg = _green_clip(dirs["videos"] / "verde2.mp4")
    bgv = make_video(dirs["videos"] / "fondo.mp4", 640, 360, 4)
    r = client.post("/api/library/import", json={"paths": [str(fg), str(bgv)]}).json()
    ids = [x["video"]["id"] if x.get("video") else x.get("video_id") for x in r["results"]]
    res = client.post("/api/studio/projects", json={"name": "croma", "source_type": "library", "video_id": ids[0]}).json()
    pid = res["project"]["id"]
    det = client.post(f"/api/studio/projects/{pid}/chroma/detect").json()
    assert det["detected"]
    p = client.get(f"/api/studio/projects/{pid}").json()
    tl = p["timeline"]
    tl["chroma"] = {"enabled": True, "color": det["color"], "similarity": det["similarity"],
                    "bg": {"kind": "video", "video_id": ids[1], "start": 1}, "subject": {"scale": 0.6, "x": 0, "y": 1}}
    assert client.put(f"/api/studio/projects/{pid}/timeline", json={"timeline": tl}).status_code == 200
    full = render.projects.get(pid)
    out = tmp_path / "out.mp4"
    cmd, dur, _ = render.build_command(full, "preview", tmp_path, out)
    graph = (tmp_path / "graph.txt").read_text()
    assert "chromakey=0x" in graph and "despill=type=green" in graph and "[ckout]" in graph
    subprocess.run(cmd, check=True, capture_output=True, cwd=tmp_path)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(out), "-frames:v", "1", str(tmp_path / "f.png")])
    # con fondo de color sólido: no debe quedar nada del verde original
    tl["chroma"]["bg"] = {"kind": "color", "color": "#FF00FF"}
    client.put(f"/api/studio/projects/{pid}/timeline", json={"timeline": tl})
    cmd, _, _ = render.build_command(render.projects.get(pid), "preview", tmp_path, out)
    subprocess.run(cmd, check=True, capture_output=True, cwd=tmp_path)
    frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(out), "-frames:v", "1", "-vf", "scale=36:64",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    px = [frame[i:i + 3] for i in range(0, len(frame), 3)]
    greens = sum(1 for p_ in px if p_[1] > 120 and p_[1] > p_[0] + 40 and p_[1] > p_[2] + 40)
    magenta = sum(1 for p_ in px if p_[0] > 200 and p_[2] > 200 and p_[1] < 80)
    assert greens < len(px) * 0.02 and magenta > len(px) * 0.5


def test_agent_set_chroma(client, dirs, monkeypatch):
    with db.get_conn() as conn:
        vid = conn.execute("INSERT INTO videos (title, original_path, stored_path, storage_mode) "
                           "VALUES ('GTA 6 emulador', 'x', 'x#gta', 'reference')").lastrowid
    tl = {"duration": 10, "tracks": {"video": [{"src_in": 0, "src_out": 10}], "broll": [], "text": [], "music": [], "sfx": []}}
    monkeypatch.setattr(chroma, "detect_color", lambda p, at=1.0: {"color": "#11AA22", "similarity": 0.2, "detected": True})
    _, notes = agent.apply_ops(tl, {}, [{"op": "set_chroma", "enabled": True, "color": "auto", "bg_video_id": vid,
                                         "bg_start": 120, "subject_scale": 0.55, "subject_x": 0}], source_path="x.mp4")
    assert not notes, notes
    c = tl["chroma"]
    assert c["enabled"] and c["color"] == "#11AA22" and c["bg"]["video_id"] == vid and c["bg"]["start"] == 120
    assert c["subject"]["scale"] == 0.55 and c["subject"]["x"] == 0
    agent.apply_ops(tl, {}, [{"op": "set_chroma", "enabled": False}])
    assert tl["chroma"]["enabled"] is False
    assert "GTA 6 emulador" in agent.library_for_llm()
    json.dumps(tl)

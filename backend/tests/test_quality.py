"""Masterización de audio (-14 LUFS) y control de calidad automático."""
import subprocess

from app.editor import master, qa


def _make(path, black=1.0, silence=1.5, tone=4.0, vol=0.3):
    """Video de prueba: negro + silencio al inicio, luego imagen con tono bajo de volumen."""
    total = black + tone
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=black:s=320x568:d={black}:r=30",
        "-f", "lavfi", "-i", f"testsrc2=s=320x568:d={tone}:r=30",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-f", "lavfi", "-i", f"sine=f=440:r=48000:d={total - silence}",
        "-filter_complex",
        f"[0:v][1:v]concat=n=2:v=1:a=0[v];[3:a]volume={vol},aformat=channel_layouts=stereo[s];"
        f"[2:a]atrim=duration={silence},asetpts=PTS-STARTPTS[z];[z][s]concat=n=2:v=0:a=1[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True)
    return total


def test_normalize_reaches_target(tmp_path):
    f = tmp_path / "quiet.mp4"
    _make(f, black=0.5, silence=0.2, tone=6.0, vol=0.25)
    before = master.measure(f)
    assert before and before["input_i"] < -25
    rep = master.normalize(f)
    assert rep["applied"] and abs(rep["lufs"] - master.TARGET_I) < 1.5
    assert subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_name",
                           "-of", "csv=p=0", str(f)], capture_output=True, text=True).stdout.strip() == "h264"


def test_normalize_skips_silence(tmp_path):
    f = tmp_path / "mute.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=s=320x240:d=2",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(f)], check=True)
    assert master.normalize(f)["applied"] is False


def test_qa_detects_dead_hook(tmp_path):
    f = tmp_path / "bad.mp4"
    total = _make(f, black=1.0, silence=1.5, tone=4.0)
    rep = qa.check(f, total, expected=total, kind="clip", captions=True,
                   words=[{"start": 3.1, "end": 3.4, "word": "hola"}])
    types = {i["type"] for i in rep["issues"]}
    assert {"negro", "silencio", "gancho"} <= types
    assert rep["score"] < 85 and rep["verdict"] in ("revisar", "rehacer")
    assert rep["issues"][0]["severity"] == "alta"


def test_qa_clean_clip_passes(tmp_path):
    f = tmp_path / "ok.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x568:d=5:r=30",
                    "-f", "lavfi", "-i", "sine=f=300:r=48000:d=5", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(f)], check=True)
    rep = qa.check(f, 5.0, expected=5.0, kind="clip", captions=True,
                   words=[{"start": 0.2, "end": 0.6, "word": "mira"}])
    assert rep["score"] >= 85 and rep["verdict"] == "listo", rep


def test_qa_flags_missing_captions_and_length():
    scan = {"black": [], "freeze": [(10.0, -1.0)], "silence": [], "mean_db": -40.0, "max_db": 0.0}
    rep = qa.evaluate(scan, 20.0, expected=30.0, kind="clip", captions=True, words=[])
    types = [i["type"] for i in rep["issues"]]
    for t in ("subtitulos", "duracion", "congelado", "saturado", "volumen"):
        assert t in types
    assert rep["verdict"] == "rehacer"

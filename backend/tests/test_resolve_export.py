"""Exportación a DaVinci Resolve (FCPXML + SRT)."""
import io
import xml.etree.ElementTree as ET
import zipfile

from app.studio import resolve_export


def _project(tmp_path):
    src = tmp_path / "toma.mp4"
    src.write_bytes(b"x")
    img = tmp_path / "xbox.jpg"
    img.write_bytes(b"x")
    tl = {"width": 1920, "height": 1080, "fps": 30, "duration": 10.0,
          "words": [{"start": 0.1, "end": 0.4, "word": "Hola"}, {"start": 0.5, "end": 0.9, "word": "Eneba."}],
          "tracks": {
              "video": [{"id": "v1", "start": 0.0, "src_in": 100.0, "src_out": 104.0, "zoom": 1.12},
                        {"id": "v2", "start": 4.0, "src_in": 110.0, "src_out": 116.0, "zoom": 1.0}],
              "broll": [{"id": "b1", "asset_id": 7, "start": 5.0, "end": 8.0, "mode": "pip", "pos": {"x": 0.8, "y": 0.3},
                         "scale": 0.32}],
              "text": [{"id": "t1", "start": 1.0, "end": 2.0, "text": "GTA VI & Xbox", "kind": "title"}],
              "music": [], "sfx": []}}
    p = {"name": "GTA&6", "source_path": str(src), "source_kind": "video", "timeline": tl,
         "source_meta": {"duration": 200.0, "fps": 60, "has_video": True, "has_audio": True}}
    assets_ = [{"id": 7, "title": "Xbox", "media_type": "image", "path": str(img), "available": True}]
    return p, assets_


def test_fcpxml_is_valid_and_keeps_cuts(tmp_path, monkeypatch):
    p, assets_ = _project(tmp_path)
    monkeypatch.setattr(resolve_export.assets, "list_assets", lambda kind=None: assets_)
    root = ET.fromstring(resolve_export.build_fcpxml(p).split("\n", 2)[2])
    spine = root.find(".//spine")
    clips = spine.findall("asset-clip")
    assert len(clips) == 2
    assert clips[0].get("start") == "6000/60s" and clips[0].get("duration") == "240/60s"  # 60 fps como la toma
    assert clips[1].get("offset") == "240/60s"
    assert clips[0].find("adjust-transform").get("scale") == "1.120 1.120"
    v = clips[1].find("video")  # la imagen cuelga de la toma 2 (empieza en 5 s → 111 s de la toma)
    assert v is not None and v.get("offset") == "6660/60s" and v.get("lane") == "1"
    t = clips[0].find("title")
    assert t is not None and t.find(".//text-style").text == "GTA VI & Xbox"
    assert "file:///" in root.find(".//media-rep").get("src")


def test_zip_has_project_subtitles_and_readme(tmp_path, monkeypatch):
    p, assets_ = _project(tmp_path)
    monkeypatch.setattr(resolve_export.assets, "list_assets", lambda kind=None: assets_)
    data, name = resolve_export.build_zip(p)
    z = zipfile.ZipFile(io.BytesIO(data))
    assert set(z.namelist()) == {f"{name}.fcpxml", "subtitulos.srt", "LEEME.txt"}
    assert "00:00:00,100 --> 00:00:00,900\nHola Eneba." in z.read("subtitulos.srt").decode()

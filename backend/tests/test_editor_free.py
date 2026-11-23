"""Editor: subtítulos corregidos a mano y textos movidos libremente (valen para videos largos y clips)."""
from app.studio import render, style
from app.studio import timeline as tlm

SRC = [{"start": 10.0, "end": 10.3, "word": "que"}, {"start": 10.3, "end": 10.6, "word": "Neva"},
       {"start": 10.6, "end": 11.0, "word": "debería"}, {"start": 12.0, "end": 12.4, "word": "dar"}]


def _tl():
    tl = tlm.empty("vertical")
    tl["tracks"]["video"] = [{"id": "v1", "src_in": 9.0, "src_out": 13.0, "zoom": 1.0}]
    return tl


def test_word_fix_survives_relayout_and_cuts():
    tl = _tl()
    tl["word_fixes"] = [{"src_start": 10.0, "src_end": 11.0, "text": "que Eneba debería"}]
    tlm.relayout(tl, SRC, 60)
    assert [w["word"] for w in tl["words"]] == ["que", "Eneba", "debería", "dar"]
    tl["tracks"]["video"][0]["src_in"] = 9.5  # se recorta la toma: la corrección sigue (está en tiempo de la toma)
    tlm.relayout(tl, SRC, 60)
    assert "Eneba" in [w["word"] for w in tl["words"]] and tl["words"][0]["start"] == 0.5
    tl["word_fixes"] = [{"src_start": 12.0, "src_end": 12.4, "text": ""}]  # vacío = quitar ese subtítulo
    tlm.relayout(tl, SRC, 60)
    assert "dar" not in [w["word"] for w in tl["words"]]


def test_free_text_position_and_size_in_render():
    tl = _tl()
    tlm.relayout(tl, SRC, 60)
    tl["captions"] = {"enabled": False}
    tl["tracks"]["text"] = [{"id": "t1", "start": 0, "end": 2, "text": "Hola", "kind": "title", "pos": {"x": 0.3, "y": 0.5}, "scale": 1.5},
                            {"id": "t2", "start": 0, "end": 2, "text": "Meme", "kind": "meme", "pos": {"x": 0.7, "y": 0.2}}]
    tlm.relayout(tl, SRC, 60)
    assert tl["tracks"]["text"][0]["pos"] == {"x": 0.3, "y": 0.5}  # se conserva al guardar
    ass = [l for l in render.build_ass(tl, style.clean_style({})).splitlines() if l.startswith("Dialogue")]
    assert "\\an5\\pos(324,960)\\fs99" in ass[0]
    assert "\\pos(756,384)" in ass[1] and "\\frz" in ass[1]


def test_ai_fix_caption_op_finds_and_corrects(client):
    from app.studio import agent
    tl = _tl()
    tlm.relayout(tl, SRC, 60)
    st = style.clean_style({})
    agent.apply_ops(tl, st, [{"op": "fix_caption", "find": "que neva deberia", "text": "que Eneba debería"}])
    assert [w["word"] for w in tl["words"]][:3] == ["que", "Eneba", "debería"] and tl["word_fixes"]
    assert not tl["tracks"]["text"]  # no agrega un texto en pantalla
    tlm.relayout(tl, SRC, 60)  # al guardar se conserva
    assert "Eneba" in [w["word"] for w in tl["words"]]
    _, notes = agent.apply_ops(tl, st, [{"op": "fix_caption", "find": "algo que nunca se dijo", "text": "x"}])
    assert notes and "no encontré" in notes[0]

"""Emojis a color en subtítulos: solo de palabras que se dicen, espaciados, dibujados a color."""
import shutil

import pytest

from app.studio import emoji


def test_keywords_and_false_friends():
    assert emoji.for_word("¡Dinero!") == "💰"
    assert emoji.for_word("increíble") == "🤯"
    assert emoji.for_word("autos") == "🚗"
    assert emoji.for_word("automático") is None and emoji.for_word("maleta") is None


def test_plan_spacing():
    w = lambda t, s: {"start": t, "end": t + 0.3, "word": s}  # noqa: E731
    groups = [[w(0.0, "esto"), w(0.4, "es"), w(0.8, "increíble")], [w(1.5, "mucho"), w(1.9, "dinero")],
              [w(5.0, "gané"), w(5.4, "dinero")]]
    p = emoji.plan(groups)
    assert [x["emoji"] for x in p] == ["🤯", "🏆"]  # el de 1.9 s queda muy pegado al primero
    assert p[0]["start"] == 0.8 and p[1]["start"] == 5.0


def test_render_png_in_color(tmp_path):
    if not emoji.font_path():
        pytest.skip("sin fuente de emojis a color")
    out = emoji.render_png("🔥", 96, tmp_path / "e.png")
    assert out and out.exists()
    from PIL import Image
    img = Image.open(out)
    assert max(img.size) <= 96 and img.mode == "RGBA"

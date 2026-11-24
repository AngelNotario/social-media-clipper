"""Cambio de hablante activo: encuadra a quien mueve la boca, con corte y sin saltos por una risa suelta."""
import numpy as np

from app.editor import crop

rng = np.random.default_rng(3)
STILL = rng.normal(0, 20, (crop.MOUTH_H, crop.MOUTH_W)).astype("float32")


def _talk():
    return rng.normal(0, 20, (crop.MOUTH_H, crop.MOUTH_W)).astype("float32")


def _samples(who, n=None):
    """who: lista por muestra con 'L', 'R' o '' (nadie habla). Dos personas: izquierda y derecha."""
    out = []
    for k, w in enumerate(who):
        left = (0.3, 0.4, 0.05, _talk() if w == "L" else STILL)
        right = (0.72, 0.42, 0.045, _talk() if w == "R" else STILL)
        out.append((k * 0.25, [left, right]))
    return out


def test_follows_active_speaker_with_a_cut():
    who = ["L"] * 16 + ["R"] * 16
    ch = crop.choose_speaker(_samples(who))
    assert ch[10][1] == 0.3          # habla la de la izquierda
    assert ch[-1][1] == 0.72         # luego la de la derecha
    switches = [c[0] for c in ch if c[3]]
    assert len(switches) == 1 and 3.5 <= switches[0] <= 4.6  # un solo corte, justo cuando empieza a hablar


def test_short_laugh_does_not_switch():
    who = ["L"] * 12 + ["R"] * 2 + ["L"] * 12
    ch = crop.choose_speaker(_samples(who))
    assert all(c[1] == 0.3 for c in ch[1:])
    assert not any(c[3] for c in ch)


def test_single_face_and_fallback():
    s = [(k * 0.25, [(0.55, 0.4, 0.06, None)]) for k in range(8)]
    ch = crop.choose_speaker(s)
    assert all(c[1] == 0.55 for c in ch)
    nobody = crop.choose_speaker(_samples([""] * 10))
    assert {c[1] for c in nobody} == {0.3}  # nadie habla: se queda con la cara más grande


def test_cut_becomes_jump_in_track():
    who = ["L"] * 16 + ["R"] * 16
    tr = crop.smooth_track(crop.choose_speaker(_samples(who)), with_y=True)
    xs = [p[1] for p in tr]
    jumps = [abs(b - a) for a, b in zip(xs, xs[1:])]
    assert max(jumps) > 0.3                       # corte, no paneo lento
    ts = [p[0] for p in tr]
    assert all(b > a for a, b in zip(ts, ts[1:]))  # tiempos crecientes (los usa FFmpeg)

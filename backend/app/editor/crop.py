"""Detección y seguimiento del rostro para encuadrar en vertical.

Usa YuNet (OpenCV DNN, ~230 KB, se descarga una vez) y, si no está disponible, Haar (incluido en OpenCV).
"""
from __future__ import annotations

import statistics
import urllib.request
from pathlib import Path

from .. import config

YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
_yunet_path = config.DATA_DIR / "models" / "face_detection_yunet_2023mar.onnx"
_yunet_failed = False


def _detector():
    """Devuelve una función frame→[(x, y, w, h, score)] o None."""
    global _yunet_failed
    import cv2

    if not _yunet_failed and hasattr(cv2, "FaceDetectorYN"):
        try:
            if not _yunet_path.exists():
                _yunet_path.parent.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(YUNET_URL, _yunet_path)
            det = cv2.FaceDetectorYN.create(str(_yunet_path), "", (320, 320), 0.7, 0.3, 50)

            def yunet(img):
                h, w = img.shape[:2]
                det.setInputSize((w, h))
                _, faces = det.detect(img)
                return [] if faces is None else [tuple(float(v) for v in (f[0], f[1], f[2], f[3], f[14])) for f in faces]
            return yunet
        except Exception:
            _yunet_failed = True
    data_dir = getattr(getattr(cv2, "data", None), "haarcascades", "")
    cascade = cv2.CascadeClassifier(data_dir + "haarcascade_frontalface_default.xml") if data_dir else None
    if cascade is None or cascade.empty():
        return None

    def haar(img):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6,
                                         minSize=(int(img.shape[1] * 0.06),) * 2)
        return [(x, y, w, h, 1.0) for (x, y, w, h) in faces]
    return haar


MOUTH_W, MOUTH_H = 24, 12
SPEAK_WIN = 6          # muestras (~1.5 s a 0.25 s) para promediar el movimiento de boca
SWITCH_RATIO = 1.35    # el otro debe mover la boca claramente más para cambiar de cámara
SWITCH_HOLD = 4        # y sostenerlo ~1 s (no se cambia por una risa suelta)
MIN_ACTIVITY = 2.0     # por debajo, nadie habla claramente: se queda donde estaba


def _mouth_patch(gray, f) -> "object":
    """Recorte normalizado de la zona de la boca (tercio inferior del rostro)."""
    import cv2
    x, y, w, h = (float(v) for v in f[:4])
    x0, x1 = int(max(0, x + 0.22 * w)), int(min(gray.shape[1], x + 0.78 * w))
    y0, y1 = int(max(0, y + 0.62 * h)), int(min(gray.shape[0], y + 0.98 * h))
    if x1 - x0 < 4 or y1 - y0 < 3:
        return None
    patch = cv2.resize(gray[y0:y1, x0:x1], (MOUTH_W, MOUTH_H), interpolation=cv2.INTER_AREA).astype("float32")
    return patch - patch.mean()


def choose_speaker(samples: list) -> list:
    """Elige a quién encuadrar en cada muestra. samples: [(t, [(cx, cy, area, mouth_patch|None), ...])].

    Con una sola cara (o una mucho más grande) es esa. Con dos o más caras parecidas se sigue a cada persona
    por su posición y se mide cuánto cambia su boca entre muestras: encuadra a quien habla, con histéresis
    (cambia solo si el otro habla claramente más durante ~1 s) para que se vea como un corte de cámara humano.
    Devuelve [(t, x|None, y|None, cambio_de_hablante: bool)]."""
    import numpy as np
    tracks: list[dict] = []  # {x, y, patch, act: [..], last}
    out = []
    cur = None  # índice del track encuadrado
    pending, pend_n = None, 0
    last_cut = 0
    for k, (t, faces) in enumerate(samples):
        if not faces:
            out.append((t, None, None, False))
            continue
        big = max(f[2] for f in faces)
        faces = [f for f in faces if f[2] >= 0.35 * big][:4]
        # asociar caras a personas por cercanía
        used = set()
        for f in sorted(faces, key=lambda f: -f[2]):
            best = min((tr for tr in tracks if id(tr) not in used), key=lambda tr: abs(tr["x"] - f[0]), default=None)
            if best is None or abs(best["x"] - f[0]) > 0.18:
                best = {"x": f[0], "y": f[1], "patch": None, "act": [], "last": k, "area": f[2], "hist": {}}
                tracks.append(best)
            used.add(id(best))
            a = 0.0
            if f[3] is not None and best["patch"] is not None and best["patch"].shape == f[3].shape:
                a = float(np.mean(np.abs(f[3] - best["patch"])))
            best.update(x=f[0], y=f[1], patch=f[3], last=k, area=f[2])
            best["act"] = (best["act"] + [a])[-SPEAK_WIN:]
            best["hist"][k] = (a, f[0], f[1])
        live = [tr for tr in tracks if tr["last"] == k]
        tracks = [tr for tr in tracks if k - tr["last"] <= 8]  # una persona que salió de cuadro se olvida
        if cur is None or cur not in live:
            cur = max(live, key=lambda tr: tr["area"])
            pending, pend_n = None, 0
        switched = False
        if len(live) >= 2:
            score = {id(tr): (sum(tr["act"]) / len(tr["act"]) if tr["act"] else 0.0) for tr in live}
            cand = max(live, key=lambda tr: score[id(tr)])
            if cand is not cur and score[id(cand)] >= MIN_ACTIVITY and \
                    score[id(cand)] > SWITCH_RATIO * max(score[id(cur)], 0.5):
                pend_n = pend_n + 1 if pending is cand else 1
                pending = cand
                if pend_n >= SWITCH_HOLD:
                    # el análisis es posterior al video: el corte se adelanta al instante en que empezó a hablar
                    j = k
                    while j - 1 > last_cut and (j - 1) in cand["hist"] and (j - 1) in cur["hist"] and \
                            cand["hist"][j - 1][0] > cur["hist"][j - 1][0]:
                        j -= 1
                    for q in range(j, k):
                        hx = cand["hist"].get(q)
                        if hx and out[q][1] is not None:
                            out[q] = (out[q][0], float(hx[1]), float(hx[2]), q == j)
                    cur, pending, pend_n = cand, None, 0
                    switched = j == k
                    last_cut = j
            else:
                pending, pend_n = None, 0
        out.append((t, float(cur["x"]), float(cur["y"]), switched))
    return out


def face_track(video: Path, start: float, end: float, step: float = 0.25,
               with_y: bool = False) -> list[tuple] | None:
    """Posición horizontal (0-1) de la persona a encuadrar a lo largo del tiempo, suavizada.

    Con una persona sigue su cara. Con dos (entrevista, podcast) encuadra a quien está hablando según el
    movimiento de su boca, y al cambiar de hablante hace un corte (no un paneo lento).
    Devuelve [(t_absoluto, x)] cada ~0.5 s, o None si casi no hay rostros.
    Con with_y=True devuelve [(t, x, y)]: y = altura (0-1) del centro del rostro, para centrar los zooms en la cara.
    """
    try:
        import cv2
    except ImportError:
        return None
    detect = _detector()
    if detect is None:
        return None
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    every = max(1, int(round(fps * step)))
    samples: list = []
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        idx, t = 0, start
        while t <= end:
            if idx % every == 0:
                ok, frame = cap.read()
                if not ok:
                    break
                pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                t = pos if pos > 0 else t + 1 / fps
                h, w = frame.shape[:2]
                scale = 480 / w if w > 480 else 1.0
                small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1 else frame
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                sh, sw = small.shape[:2]
                faces = [(float((f[0] + f[2] / 2) / sw), float((f[1] + f[3] / 2) / sh),  # float de numpy → JSON
                          float(f[2] * f[3] / (sw * sh)), _mouth_patch(gray, f)) for f in detect(small)]
                samples.append((t, faces))
            else:
                if not cap.grab():
                    break
                t += 1 / fps
            idx += 1
    finally:
        cap.release()
    return smooth_track(choose_speaker(samples), with_y)


def smooth_track(chosen: list, with_y: bool = False) -> list[tuple] | None:
    """Suaviza la ruta del encuadre (sin temblores) y convierte los cambios de hablante en cortes."""
    raw = [(t, x) for t, x, _, _ in chosen]
    found = [r for r in raw if r[1] is not None]
    if len(found) < max(2, len(raw) // 5):
        return None
    xs, last = [], found[0][1]
    for _, x in raw:
        last = x if x is not None else last
        xs.append(last)
    cuts = {i for i, c in enumerate(chosen) if c[3]}
    # Mediana móvil (quita saltos) + suavizado exponencial + zona muerta; se reinicia en cada cambio de hablante
    out, cur, seg0 = [], xs[0], 0
    for i, (t, _) in enumerate(raw):
        if i in cuts:
            seg0, cur = i, xs[i]
        m = statistics.median(xs[max(seg0, i - 2):i + 3] if not any(c in cuts for c in range(i + 1, i + 3))
                              else xs[max(seg0, i - 2):i + 1])
        target = cur * 0.7 + m * 0.3
        if abs(target - cur) > 0.012:
            cur = target
        out.append((t, cur))
    ys = [c[2] for c in chosen]
    yv, last_y = [], statistics.median([y for y in ys if y is not None] or [0.4])
    for y in ys:
        last_y = y if y is not None else last_y
        yv.append(last_y)
    keep, last_t = [], -1.0
    for i, (t, x) in enumerate(out):
        if i in cuts and keep:
            # corte: se mantiene el encuadre anterior hasta el instante del cambio y salta al nuevo
            prev = keep[-1]
            keep.append((round(max(t - 0.05, prev[0] + 0.02), 2), prev[1]) + ((prev[2],) if with_y else ()))
            last_t = -1.0
        if t - last_t >= 0.5:
            pt = (round(t, 2), round(x, 4))
            if with_y:
                pt += (round(statistics.median(yv[max(0, i - 2):i + 3]), 4),)
            keep.append(pt)
            last_t = t
    return keep


def face_center_x(video: Path, start: float, end: float, samples: int = 24) -> float | None:
    """Posición horizontal media del rostro (encuadre fijo)."""
    tr = face_track(video, start, end, step=max(0.25, (end - start) / max(samples, 1)))
    if not tr:
        return None
    return float(statistics.median(x for _, x in tr))


def track_expr(points: list, crop_w: int, src_w: int, t0: float = 0.0) -> str:
    """Expresión FFmpeg (por fotograma) para la x del recorte siguiendo el rostro.

    points [(t_absoluto, x 0-1)]; t0 = instante del video que corresponde a t=0 en el filtro.
    """
    def px(x: float) -> int:
        return int(min(max(x * src_w - crop_w / 2, 0), src_w - crop_w))

    pts = [(max(0.0, float(t) - t0), px(float(x))) for t, x in points]
    pts = [p for i, p in enumerate(pts) if i == 0 or p[0] > pts[i - 1][0]]
    if not pts:
        return str(px(0.5))
    expr = str(pts[-1][1])
    for (ta, xa), (tb, xb) in reversed(list(zip(pts, pts[1:]))):
        expr = f"if(lt(t,{tb:.2f}),{xa}+({xb - xa})*(t-{ta:.2f})/{max(tb - ta, 0.01):.2f},{expr})"
    return f"if(lt(t,{pts[0][0]:.2f}),{pts[0][1]},{expr})"

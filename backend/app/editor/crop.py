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


def face_track(video: Path, start: float, end: float, step: float = 0.25,
               with_y: bool = False) -> list[tuple] | None:
    """Posición horizontal (0-1) del rostro principal a lo largo del tiempo, suavizada.

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
    raw: list[tuple[float, float | None]] = []
    ys: dict[int, float] = {}
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        idx, t, prev_x = 0, start, None
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
                faces = detect(small)
                x = None
                if faces:
                    def rank(f):
                        cx = (f[0] + f[2] / 2) / small.shape[1]
                        near = 0 if prev_x is None else abs(cx - prev_x)
                        return f[2] * f[3] / (small.shape[0] * small.shape[1]) - near * 0.03
                    f = max(faces, key=rank)
                    x = float((f[0] + f[2] / 2) / small.shape[1])  # YuNet da float32 de numpy: no se guarda en JSON
                    ys[len(raw)] = float((f[1] + f[3] / 2) / small.shape[0])
                    prev_x = x
                raw.append((t, x))
            else:
                if not cap.grab():
                    break
                t += 1 / fps
            idx += 1
    finally:
        cap.release()
    found = [r for r in raw if r[1] is not None]
    if len(found) < max(2, len(raw) // 5):
        return None
    xs, last = [], found[0][1]
    for _, x in raw:
        last = x if x is not None else last
        xs.append(last)
    # Mediana móvil (quita saltos) + suavizado exponencial + zona muerta (evita temblores)
    med = [statistics.median(xs[max(0, i - 2):i + 3]) for i in range(len(xs))]
    out, cur = [], med[0]
    for i, (t, _) in enumerate(raw):
        target = cur * 0.7 + med[i] * 0.3
        if abs(target - cur) > 0.012:
            cur = target
        out.append((t, cur))
    yv, last_y = [], (statistics.median(ys.values()) if ys else 0.4)
    for i in range(len(raw)):
        last_y = ys.get(i, last_y)
        yv.append(last_y)
    keep, last_t = [], -1.0
    for i, (t, x) in enumerate(out):
        if t - last_t >= 0.5:
            if with_y:
                y = statistics.median(yv[max(0, i - 2):i + 3])
                keep.append((round(t, 2), round(x, 4), round(y, 4)))
            else:
                keep.append((round(t, 2), round(x, 4)))
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

"""Detección real de zooms en un video de referencia (cuadro a cuadro).

Antes los zooms se adivinaban preguntándole a la IA de visión si ~10 cuadros sueltos "se veían cerrados": un zoom
entre dos cuadros muestreados no existía. Ahora se mide la escala entre cada par de cuadros seguidos:

1. Se decodifica la referencia a baja resolución (15 cuadros/s, gris).
2. Entre cuadros consecutivos se buscan puntos característicos (ORB) y se ajusta una transformación de semejanza
   (escala + giro + desplazamiento) con RANSAC. La escala de esa transformación es cuánto creció la imagen.
3. Un zoom digital escala TODO el cuadro de forma uniforme alrededor de un centro: muchos puntos encajan
   (inliers altos). Avanzar en un juego o mover la cámara deforma la imagen (paralaje): encajan pocos. Solo se
   aceptan pasos con buen ajuste. Los zooms lentos exigen un ajuste casi perfecto.
   Además, si la cámara del juego avanza, el HUD y la facecam se quedan quietos mientras el resto crece: esos
   puntos "quietos donde el zoom debía moverlos" delatan que no es un zoom de edición y el paso se descarta.
4. Eventos:
   - "punch": salto de escala ≥ 4 % de un cuadro al siguiente sin corte de escena (zoom de golpe).
   - "ramp": zoom suave que acumula ≥ 5 % en 0.2–2.5 s dentro de la misma toma.
   Cada evento guarda inicio, fin, cuánto aumenta, y cuánto se sostiene antes de volver o cortar.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import numpy as np

FPS = 15
WIDTH = 320
PUNCH_STEP = math.log(1.06)
RAMP_TOTAL = math.log(1.06)
MERGE_S = 0.6
MIN_INLIERS = 20
MIN_INLIER_RATIO = 0.4
RAMP_MIN_RATIO = 0.8     # un zoom digital lento encaja casi perfecto; caminar en un juego (paralaje) no
MAX_FIXED = 0.03         # fracción de puntos que debían moverse con el zoom y se quedaron quietos (HUD, facecam)


def _frames(path: Path, start: float, dur: float) -> tuple[np.ndarray, int, int]:
    import cv2  # noqa: F401 — se importa aquí para que el módulo cargue sin OpenCV
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
    try:
        w0, h0 = (int(x) for x in r.stdout.strip().split(",")[:2])
    except ValueError:
        return np.zeros((0, 1, 1), np.uint8), 0, 0
    w = WIDTH
    h = max(2, int(round(h0 * w / max(w0, 1) / 2)) * 2)
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-i", str(path), "-t", f"{dur:.2f}",
                        "-vf", f"fps={FPS},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=1800)
    data = np.frombuffer(p.stdout, dtype=np.uint8)
    n = w * h
    k = data.size // n
    return data[: k * n].reshape(k, h, w), w, h


def _step_scale(orb, bf, a, b, cache: dict | None = None) -> tuple[float | None, float]:
    """log(escala) de a→b y fracción de puntos que encajan; None si no hay ajuste confiable."""
    import cv2
    ka, da = orb.detectAndCompute(a, None)
    kb, db = orb.detectAndCompute(b, None)
    if da is None or db is None or len(ka) < MIN_INLIERS or len(kb) < MIN_INLIERS:
        return None, 0.0
    m = bf.match(da, db)
    if len(m) < MIN_INLIERS:
        return None, 0.0
    pa = np.float32([ka[x.queryIdx].pt for x in m])
    pb = np.float32([kb[x.trainIdx].pt for x in m])
    M, inl = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=2.0)
    if M is None or inl is None:
        return None, 0.0
    if cache is not None:
        cache["M"] = M
    ni = int(inl.sum())
    ratio = ni / len(m)
    s = math.hypot(M[0, 0], M[1, 0])
    if ni < MIN_INLIERS or not (0.7 < s < 1.45):
        return None, ratio
    if abs(math.log(s)) > 0.003:
        # puntos que el zoom debía mover (≥ 1.5 px) pero que no se movieron: capa fija encima (HUD/facecam)
        pred = pa @ M[:, :2].T + M[:, 2]
        moved = np.linalg.norm(pb - pa, axis=1)
        should = np.linalg.norm(pred - pa, axis=1)
        bad = (should >= 1.5) & (moved < 0.6)
        if int(bad.sum()) >= 4 and float(np.mean(bad)) > MAX_FIXED:
            return None, ratio
    if ratio < MIN_INLIER_RATIO:
        # un golpe de zoom grande cambia la escala de los puntos y baja la proporción de coincidencias:
        # se acepta con menos proporción solo si el salto es claramente un golpe
        if not (ratio >= 0.25 and abs(math.log(s)) >= PUNCH_STEP):
            return None, ratio
    return math.log(s), ratio


def _fixed_layer(a, b, ls: float, M_cache: dict) -> bool:
    """¿Hay una zona del cuadro que NO siguió el zoom (facecam, HUD, marco)? Se compara por celdas: si una celda
    con detalle se parece más al cuadro anterior sin transformar que transformado, esa zona está fija."""
    import cv2
    M = M_cache.get("M")
    if M is None:
        return False
    h, w = a.shape
    warped = cv2.warpAffine(a, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    gy, gx = 4, 4
    for yi in range(gy):
        for xi in range(gx):
            ys, xs = slice(yi * h // gy, (yi + 1) * h // gy), slice(xi * w // gx, (xi + 1) * w // gx)
            cb = b[ys, xs].astype(np.float32)
            if cb.std() < 10:
                continue  # zona lisa: no dice nada
            e_id = float(np.mean(np.abs(cb - a[ys, xs].astype(np.float32))))
            e_wp = float(np.mean(np.abs(cb - warped[ys, xs].astype(np.float32))))
            if e_id < 6 and e_wp > 2.5 * e_id + 4:
                return True
    return False


def detect(path: Path, start: float = 0.0, dur: float = 180.0, scene_cuts: list[float] | None = None,
           progress=lambda p, s: None) -> dict:
    """Eventos de zoom de la referencia. Devuelve {events, zoom_per_min, punch_per_min, amount, hold_s, kind, ...}."""
    import cv2
    frames, w, h = _frames(path, start, dur)
    out = {"events": [], "zoom_per_min": 0.0, "punch_per_min": 0.0, "amount": None, "hold_s": None,
           "kind": None, "analyzed_s": round(len(frames) / FPS, 1), "confidence": 0.0}
    if len(frames) < 3:
        return out
    cuts = sorted(float(c) for c in (scene_cuts or []))
    orb = cv2.ORB_create(nfeatures=600, fastThreshold=12)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    steps: list[float | None] = [None]
    ratios: list[float] = [0.0]
    good = 0
    for i in range(1, len(frames)):
        t = i / FPS
        if any(t - 1.5 / FPS <= c <= t + 0.5 / FPS for c in cuts):
            steps.append(None)  # corte de escena: no se compara a través del corte
            ratios.append(0.0)
            continue
        mc: dict = {}
        ls, ratio = _step_scale(orb, bf, frames[i - 1], frames[i], mc)
        if ls is not None and abs(ls) >= PUNCH_STEP and _fixed_layer(frames[i - 1], frames[i], ls, mc):
            ls = None  # la cámara del juego/toma se movió bajo una capa fija: no es un zoom de edición
        steps.append(ls)
        ratios.append(ratio)
        good += ls is not None
        if i % 150 == 0:
            progress(i / len(frames), f"Midiendo zooms cuadro a cuadro ({int(t)} s)")
    out["confidence"] = round(good / max(len(frames) - 1, 1), 2)

    events: list[dict] = []
    used = set()
    # 1) golpes de zoom (punch): salto grande en un solo paso
    for i, ls in enumerate(steps):
        if ls is not None and abs(ls) >= PUNCH_STEP:
            events.append({"t0": round(i / FPS, 2), "t1": round(i / FPS, 2), "kind": "punch",
                           "amount": round(math.exp(ls), 3), "dir": "in" if ls > 0 else "out", "i": i})
            used.add(i)
    # 2) zooms suaves (ramp): pasos pequeños del mismo signo que suman ≥ 5 % en 0.2–2.5 s
    i = 1
    while i < len(steps):
        if steps[i] is None or i in used or abs(steps[i]) < 0.002 or ratios[i] < RAMP_MIN_RATIO:
            i += 1
            continue
        sign = 1 if steps[i] > 0 else -1
        j, acc = i, 0.0
        while j < len(steps) and steps[j] is not None and j not in used and steps[j] * sign > 0.0015 \
                and ratios[j] >= RAMP_MIN_RATIO and (j - i) / FPS <= 2.5:
            acc += steps[j]
            j += 1
        span = (j - i) / FPS
        if abs(acc) >= RAMP_TOTAL and span >= 0.2:
            # comprobación de punta a punta: con el desplazamiento acumulado, una capa fija (HUD/facecam) se nota
            mc2: dict = {}
            ls_all, _ = _step_scale(orb, bf, frames[i - 1], frames[j - 1], mc2)
            if ls_all is not None and _fixed_layer(frames[i - 1], frames[j - 1], ls_all, mc2):
                ls_all = None
            if ls_all is None or ls_all * sign < RAMP_TOTAL * 0.6 or abs(acc) > math.log(1.6):
                # >60 % en un zoom lento no es edición: es la cámara de la toma (b-roll, juego)
                i = j
                continue
            events.append({"t0": round(i / FPS, 2), "t1": round(j / FPS, 2), "kind": "ramp",
                           "amount": round(math.exp(acc), 3), "dir": "in" if acc > 0 else "out", "i": i})
            i = j
        else:
            i += 1
    events.sort(key=lambda e: e["t0"])
    # pasos seguidos en la misma dirección (un zoom que dura varios cuadros) se juntan en un solo evento
    merged: list[dict] = []
    for e in events:
        prev = merged[-1] if merged else None
        if prev and prev["dir"] == e["dir"] and e["t0"] - prev["t1"] <= MERGE_S:
            prev["t1"] = max(prev["t1"], e["t1"])
            prev["amount"] = round(prev["amount"] * e["amount"], 3)
            if e["kind"] == "ramp" or prev["t1"] - prev["t0"] > 0.25:
                prev["kind"] = "ramp" if prev["t1"] - prev["t0"] > 0.25 else prev["kind"]
        else:
            merged.append(dict(e))
    events = [e for e in merged if not (e["kind"] == "ramp" and not (1 / 1.6 <= e["amount"] <= 1.6))]
    # 3) cuánto se sostiene cada zoom de entrada: hasta el siguiente zoom de salida, corte o evento
    bounds = sorted(set(cuts + [e["t0"] for e in events] + [len(frames) / FPS]))
    for e in events:
        if e["dir"] == "in":
            nxt = next((b for b in bounds if b > e["t1"] + 0.05), len(frames) / FPS)
            e["hold_s"] = round(min(nxt - e["t1"], 8.0), 2)
        e.pop("i", None)
        e["t0"] = round(e["t0"] + start, 2)
        e["t1"] = round(e["t1"] + start, 2)
    ins = [e for e in events if e["dir"] == "in"]
    mins = max(len(frames) / FPS / 60, 1 / 60)
    out["events"] = events
    out["zoom_per_min"] = round(len(ins) / mins, 2)
    out["punch_per_min"] = round(sum(1 for e in ins if e["kind"] == "punch") / mins, 2)
    if ins:
        out["amount"] = round(float(np.median([e["amount"] for e in ins])), 3)
        holds = [e["hold_s"] for e in ins if e.get("hold_s")]
        out["hold_s"] = round(float(np.median(holds)), 2) if holds else None
        out["kind"] = "punch" if out["punch_per_min"] >= out["zoom_per_min"] / 2 else "ramp"
        gaps = np.diff([e["t0"] for e in ins])
        out["median_gap_s"] = round(float(np.median(gaps)), 2) if len(gaps) else None
    return out

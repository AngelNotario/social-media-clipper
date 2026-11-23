"""Composición de miniaturas 1280x720 con Pillow, al estilo de las miniaturas de YouTube que funcionan:
persona recortada grande con borde blanco y sombra, fondo con color y profundidad, 2-4 palabras gigantes con
contorno, una palabra resaltada, logos/recursos y flecha opcional."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .. import config

W, H = 1280, 720
FONT_DIR = config.DATA_DIR / "fonts"
FONT_URLS = {"Anton-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"}
SYSTEM_FONTS = ["C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/seguibl.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
POS = {"top_left": (0.04, 0.05), "top_right": (0.96, 0.05), "bottom_left": (0.04, 0.95), "bottom_right": (0.96, 0.95),
       "center_left": (0.04, 0.5), "center_right": (0.96, 0.5), "top_center": (0.5, 0.05), "center": (0.5, 0.5)}
SIZES = {"small": 0.17, "medium": 0.26, "large": 0.38}


def font_path() -> str | None:
    f = FONT_DIR / "Anton-Regular.ttf"
    if not f.exists():
        try:  # fuente gruesa y condensada típica de miniaturas (Google Fonts, licencia OFL)
            from ..research import download
            download(FONT_URLS["Anton-Regular.ttf"], f, 2_000_000)
        except Exception:  # noqa: BLE001
            pass
    if f.exists() and f.stat().st_size > 10000:
        return str(f)
    return next((p for p in SYSTEM_FONTS if Path(p).exists()), None)


def _font(size: int):
    p = font_path()
    try:
        return ImageFont.truetype(p, size) if p else ImageFont.load_default(size)
    except OSError:
        return ImageFont.load_default()


def _hex(c: str, default=(255, 255, 255)) -> tuple:
    try:
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return default


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    return ImageOps.fit(img.convert("RGB"), (w, h), Image.LANCZOS, centering=(0.5, 0.45))


def background(kind: str, img: Image.Image | None, c1: str, c2: str, side: str) -> Image.Image:
    if img is not None:
        bg = cover(img, W, H)
        if kind == "frame":  # el fotograma como fondo: desenfocado para que la persona y el texto resalten
            bg = bg.filter(ImageFilter.GaussianBlur(7))
        bg = ImageEnhance.Color(bg).enhance(1.35)
        bg = ImageEnhance.Contrast(bg).enhance(1.12)
    else:
        a, b = _hex(c1, (20, 20, 40)), _hex(c2, (120, 40, 200))
        grad = Image.linear_gradient("L").rotate(-35, expand=True).resize((W, H))
        bg = Image.composite(Image.new("RGB", (W, H), b), Image.new("RGB", (W, H), a), grad)
    # oscurecer el lado del texto y viñeta, para legibilidad
    shade = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(shade)
    for x in range(W):
        t = x / W if side == "right" else 1 - x / W  # side = dónde va el texto
        d.line([(x, 0), (x, H)], fill=int(150 * max(0.0, t - 0.25) / 0.75))
    bg = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), bg, shade)
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse([-W * 0.2, -H * 0.3, W * 1.2, H * 1.3], fill=255)
    vig = ImageOps.invert(vig.filter(ImageFilter.GaussianBlur(120))).point(lambda v: int(v * 0.55))
    return Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), bg, vig)


def outlined(sub: Image.Image, stroke: int = 9, color=(255, 255, 255), shadow: bool = True) -> Image.Image:
    """Recorte con borde blanco y sombra (el sello de las miniaturas de YouTube)."""
    sub = sub.convert("RGBA")
    pad = stroke * 3 + 20
    big = Image.new("RGBA", (sub.width + pad * 2, sub.height + pad * 2), (0, 0, 0, 0))
    big.paste(sub, (pad, pad), sub)
    a = big.getchannel("A").point(lambda v: 255 if v > 40 else 0)
    ring = a.filter(ImageFilter.MaxFilter(stroke * 2 + 1)).filter(ImageFilter.GaussianBlur(1))
    out = Image.new("RGBA", big.size, (0, 0, 0, 0))
    if shadow:
        sh = ring.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.75))
        out.paste(Image.new("RGBA", big.size, (0, 0, 0, 255)), (10, 12), sh)
    out.paste(Image.new("RGBA", big.size, color + (255,)), (0, 0), ring)
    out.alpha_composite(big)
    return out


def boost_subject(sub: Image.Image) -> Image.Image:
    rgb, a = sub.convert("RGB"), sub.getchannel("A")
    rgb = ImageEnhance.Contrast(ImageEnhance.Color(rgb).enhance(1.25)).enhance(1.1)
    rgb = ImageEnhance.Brightness(rgb).enhance(1.06)
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


def _fit_text(lines: list[str], max_w: int, max_h: int) -> tuple[ImageFont.FreeTypeFont, int]:
    size = 220
    while size > 40:
        f = _font(size)
        widths = [f.getbbox(ln)[2] - f.getbbox(ln)[0] for ln in lines]
        total_h = int(size * 1.02 * len(lines))
        if max(widths) <= max_w and total_h <= max_h:
            return f, size
        size -= 6
    return _font(size), size


def split_lines(text: str) -> list[str]:
    words = text.split()
    if len(words) <= 2 or len(text) <= 12:
        return [text]
    best, cut = None, 1
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = abs(len(a) - len(b))
        if best is None or score < best:
            best, cut = score, i
    return [" ".join(words[:cut]), " ".join(words[cut:])]


def draw_text(img: Image.Image, text: str, highlight: str, color: str, accent: str, side: str, box: bool) -> None:
    text = text.upper().strip()
    if not text:
        return
    lines = split_lines(text)
    area_w = int(W * 0.56)
    x0 = W - area_w - 30 if side == "right" else 30
    f, size = _fit_text(lines, area_w, int(H * 0.62))
    stroke = max(6, size // 11)
    y = int(H * 0.5 - size * 1.02 * len(lines) / 2)
    hl = (highlight or "").upper().split()
    d = ImageDraw.Draw(img)
    fill, acc = _hex(color, (255, 255, 255)), _hex(accent, (255, 214, 0))
    for ln in lines:
        lw = f.getbbox(ln)[2] - f.getbbox(ln)[0]
        x = x0 + (area_w - lw) // 2
        cx = x
        for wi, word in enumerate(ln.split()):
            chunk = word + (" " if wi < len(ln.split()) - 1 else "")
            is_hl = word.strip("¡!¿?.,") in [h.strip("¡!¿?.,") for h in hl]
            ww = f.getbbox(word)[2] - f.getbbox(word)[0]
            if is_hl and box:
                d.rounded_rectangle([cx - 10, y + size * 0.06, cx + ww + 10, y + size * 1.05], radius=10, fill=acc + (255,))
                d.text((cx, y), word, font=f, fill=(15, 15, 15), stroke_width=0)
            else:
                d.text((cx, y), word, font=f, fill=acc if is_hl else fill, stroke_width=stroke, stroke_fill=(0, 0, 0))
            cx += f.getbbox(chunk)[2] - f.getbbox(chunk)[0] if chunk.endswith(" ") else ww
        y += int(size * 1.02)


def draw_arrow(img: Image.Image, start: tuple, end: tuple, color=(255, 40, 40)) -> None:
    import math
    d = ImageDraw.Draw(img)
    (x1, y1), (x2, y2) = start, end
    ang = math.atan2(y2 - y1, x2 - x1)
    L = 46
    head = [(x2, y2), (x2 - L * math.cos(ang - 0.45), y2 - L * math.sin(ang - 0.45)),
            (x2 - L * math.cos(ang + 0.45), y2 - L * math.sin(ang + 0.45))]
    shaft_end = (x2 - L * 0.8 * math.cos(ang), y2 - L * 0.8 * math.sin(ang))
    d.line([start, shaft_end], fill=(255, 255, 255), width=26)
    d.polygon([(px + 4 * math.cos(ang), py + 4 * math.sin(ang)) for px, py in head], fill=(255, 255, 255))
    d.line([start, shaft_end], fill=color, width=16)
    d.polygon(head, fill=color)


def compose(concept: dict, subject: Image.Image | None, bg_img: Image.Image | None, assets: dict[str, Image.Image],
            out: Path) -> Path:
    side_face = concept.get("layout") if concept.get("layout") in ("left", "right", "center") else "left"
    text_side = "right" if side_face == "left" else "left"
    bgk = str(concept.get("background") or "frame")
    bg_src = bg_img
    if bgk.startswith("asset:") and assets.get(bgk[6:]) is not None:
        bg_src = assets[bgk[6:]]
    elif bgk == "gradient":
        bg_src = None
    img = background("asset" if bgk.startswith("asset:") else bgk, bg_src, concept.get("bg_color_1", "#14142a"),
                     concept.get("bg_color_2", concept.get("accent_color", "#7c5cff")), text_side).convert("RGBA")
    face_box = None
    if subject is not None:
        sub = boost_subject(subject)
        target_h = int(H * (0.98 if side_face != "center" else 0.8))
        r = target_h / sub.height
        sub = sub.resize((max(1, int(sub.width * r)), target_h), Image.LANCZOS)
        if sub.width > W * 0.62:
            r = W * 0.62 / sub.width
            sub = sub.resize((int(sub.width * r), int(sub.height * r)), Image.LANCZOS)
        ol = outlined(sub)
        x = {"left": -30, "right": W - ol.width + 30, "center": (W - ol.width) // 2}[side_face]
        y = H - ol.height + 40
        img.alpha_composite(ol, (int(x), int(y)))
        face_box = (x, y, x + ol.width, y + ol.height)
    for el in concept.get("elements") or []:
        a = assets.get(str(el.get("asset")))
        if a is None:
            continue
        frac = SIZES.get(el.get("size"), 0.26)
        a = a.convert("RGBA")
        r = (W * frac) / max(a.width, 1)
        if a.height * r > H * 0.5:
            r = H * 0.5 / a.height
        a = a.resize((max(1, int(a.width * r)), max(1, int(a.height * r))), Image.LANCZOS)
        has_alpha = a.getchannel("A").getextrema()[0] < 250
        a = outlined(a, stroke=6 if has_alpha else 8, shadow=True)
        px, py = POS.get(el.get("position"), POS["top_right"])
        x = int(px * W - (a.width if px > 0.5 else (a.width / 2 if px == 0.5 else 0)))
        y = int(py * H - (a.height if py > 0.5 else (a.height / 2 if py == 0.5 else 0)))
        img.alpha_composite(a, (max(-20, min(W - a.width + 20, x)), max(-20, min(H - a.height + 20, y))))
    draw_text(img, str(concept.get("text") or ""), str(concept.get("highlight_word") or ""),
              concept.get("text_color") or "#FFFFFF", concept.get("accent_color") or "#FFD400", text_side,
              bool(concept.get("highlight_box")))
    if concept.get("arrow") and face_box:
        sx = W * (0.62 if text_side == "right" else 0.38)
        draw_arrow(img, (sx, H * 0.78), (face_box[0] + (face_box[2] - face_box[0]) * (0.55 if text_side == "right" else 0.45),
                                       H * 0.55))
    rgb = img.convert("RGB")
    out.parent.mkdir(parents=True, exist_ok=True)
    rgb.save(out, "JPEG", quality=92, optimize=True)
    return out


def tiny(path: Path, w: int = 336) -> Image.Image:
    """Cómo se ve en el feed (pequeña): para que el juez revise legibilidad."""
    im = Image.open(path)
    return im.resize((w, int(w * im.height / im.width)), Image.LANCZOS)


def card(region: Image.Image) -> Image.Image:
    """Sin recorte de persona disponible: la toma como tarjeta con esquinas redondeadas."""
    region = region.convert("RGBA")
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, region.width - 1, region.height - 1], radius=int(min(region.size) * 0.06),
                                           fill=255)
    region.putalpha(ImageChops.multiply(region.getchannel("A"), mask))
    return region

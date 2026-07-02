"""Рендер каруселей: подложка + вшитые элементы + наложение текста (Pillow).

Портировано из прототипа data/carousel_proto/{compose,make_master}.py.
Графика НЕ генерируется — только компоновка текста поверх готовой подложки.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = Path(__file__).parent / "fonts"

# ── дефолтная раскладка/стиль (утверждённая спецификация) ──────────────────
DEFAULT_LAYOUT: dict[str, Any] = {
    "W": 1080, "H": 1350,
    "margin": 84,
    "maxw": 860,
    "safe_top": 170, "safe_bot": 1150,
    "scale": 1.12,                       # множитель размера шрифта (читаемость на телефоне)
    "hook_align": "left",                # left | center
    "white": [240, 240, 240],            # заголовки И тело (иерархия — начертанием)
    "shadow": True,
    "sizes": {                           # базовые размеры (до scale)
        "hook": 60, "hook_sub": 34,
        "point_head": 42, "point_body": 33,
        "cta_lead": 40, "cta_offer": 32,
    },
    "elements": {                        # вшиваемые брендовые элементы
        "username": "@ruslan.roxber",
        "swipe_text": "Л И С Т А Й  ›",
        "dots": 7,
        "icons": True,                   # сердечко/коммент/поделиться/закладка
    },
}


def _merge(base: dict, override: dict | None) -> dict:
    if not override:
        return base
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _font(weight: str, size: int, scale: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"Inter-{weight}.ttf"), int(round(size * scale)))


def _wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> list[str]:
    lines, cur = [], ""
    for wd in text.split():
        test = (cur + " " + wd).strip()
        if draw.textlength(test, font=fnt) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines or [""]


def _wrap_multi(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> list[str]:
    """Перенос с сохранением абзацев: пустая строка между абзацами (текст с \\n)."""
    out: list[str] = []
    for para in (text or "").split("\n"):
        para = para.strip()
        if not para:
            out.append("")
        else:
            out.extend(_wrap(draw, para, fnt, max_w))
    return out or [""]


def _line_h(fnt, lh: float = 1.0) -> int:
    asc, desc = fnt.getmetrics()
    return int((asc + desc) * lh)


def _block_h(lines: list[str], fnt, lh: float) -> int:
    return len(lines) * _line_h(fnt, lh)


def _draw_block(draw, lines, fnt, x, y, color, lh, *, align="left", cx=None, shadow=True):
    step = _line_h(fnt, lh)
    for ln in lines:
        lx = (cx - draw.textlength(ln, font=fnt) / 2) if align == "center" else x
        if shadow:
            draw.text((lx + 2, y + 2), ln, font=fnt, fill=(0, 0, 0))
        draw.text((lx, y), ln, font=fnt, fill=tuple(color))
        y += step
    return y


# ── подложка ───────────────────────────────────────────────────────────────
def _standin_bg(W: int, H: int) -> Image.Image:
    img = Image.new("RGB", (W, H), (8, 8, 9))
    grad = Image.new("L", (W, 1))
    for x in range(W):
        grad.putpixel((x, 0), int(6 + 22 * (x / W)))
    img = Image.composite(Image.new("RGB", (W, H), (40, 40, 44)), img, grad.resize((W, H)))
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse([int(W * 0.59), 90, int(W * 0.93), 470], fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    return Image.composite(Image.new("RGB", (W, H), (95, 95, 100)), img, glow)


def _load_bg(path: str | None, W: int, H: int) -> Image.Image:
    if path and Path(path).exists():
        bg = Image.open(path).convert("RGB")
        scale = W / bg.width
        bg = bg.resize((W, int(bg.height * scale)))
        if bg.height >= H:
            top = (bg.height - H) // 2
            bg = bg.crop((0, top, W, top + H))
        else:
            c = Image.new("RGB", (W, H), (8, 8, 9))
            c.paste(bg, (0, (H - bg.height) // 2))
            bg = c
        return bg
    return _standin_bg(W, H)


# ── иконки нижнего бара (чистые контуры) ─────────────────────────────────────
def _heart(d, cx, cy, s, color, w=4):
    pts = []
    for i in range(0, 361, 6):
        t = math.radians(i)
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * s / 16, cy - y * s / 16))
    d.line(pts + [pts[0]], fill=color, width=w, joint="curve")


def _comment(d, cx, cy, s, color, w=4):
    d.rounded_rectangle([cx - s, cy - s * 0.8, cx + s, cy + s * 0.5], radius=s * 0.5, outline=color, width=w)
    d.line([(cx - s * 0.3, cy + s * 0.5), (cx - s * 0.55, cy + s * 0.95), (cx + s * 0.05, cy + s * 0.5)],
           fill=color, width=w, joint="curve")


def _share(d, cx, cy, s, color, w=4):
    p = [(cx - s, cy - s * 0.55), (cx + s, cy - s * 0.95), (cx + s * 0.15, cy + s * 0.95), (cx - s * 0.1, cy + s * 0.1)]
    d.line(p + [p[0]], fill=color, width=w, joint="curve")
    d.line([(cx - s, cy - s * 0.55), (cx - s * 0.1, cy + s * 0.1)], fill=color, width=w)


def _bookmark(d, cx, cy, s, color, w=4):
    ww, h = s * 0.7, s
    p = [(cx - ww, cy - h), (cx + ww, cy - h), (cx + ww, cy + h), (cx, cy + h * 0.35), (cx - ww, cy + h)]
    d.line(p + [p[0]], fill=color, width=w, joint="curve")


def _bake_elements(img: Image.Image, layout: dict) -> Image.Image:
    el = layout["elements"]
    W = layout["W"]
    white = tuple(layout["white"])
    d = ImageDraw.Draw(img)
    scale = layout["scale"]
    if el.get("username"):
        d.text((84, 62), el["username"], font=_font("SemiBold", 32, scale), fill=white)
    if el.get("icons", True):
        iy = 1268
        _heart(d, 108, iy, 24, white)
        _comment(d, 188, iy, 22, white)
        _share(d, 268, iy, 22, white)
        _bookmark(d, W - 84, iy, 24, white)
    n = int(el.get("dots", 7))
    if n > 0:
        dots_y, gap = 1238, 28
        x0 = W // 2 - (n - 1) * gap // 2
        for i in range(n):
            c = white if i == 0 else (110, 110, 110)
            d.ellipse([x0 + i * gap - 4, dots_y - 4, x0 + i * gap + 4, dots_y + 4], fill=c)
        if el.get("swipe_text"):
            f = _font("SemiBold", 24, scale)
            tw = d.textlength(el["swipe_text"], font=f)
            d.text((W // 2 - tw // 2, dots_y + 18), el["swipe_text"], font=f, fill=(150, 150, 150))
    return img


def build_master(bg_path: str | None, layout: dict | None = None, *, bake_elements: bool = False) -> Image.Image:
    """Подложка, приведённая к 1080×1350.

    bake_elements=False (по умолчанию) — подложка отдаётся как есть: элементы
    (@ник, иконки, «ЛИСТАЙ») должны быть УЖЕ вшиты в загруженный файл, приложение
    кладёт поверх только текст. Это исключает повторное нанесение элементов.
    bake_elements=True — служебный режим: нарисовать элементы на чистом фоне
    (используется офлайн-утилитой для ПОДГОТОВКИ подложки-с-элементами).
    """
    lay = _merge(DEFAULT_LAYOUT, layout)
    img = _load_bg(bg_path, lay["W"], lay["H"]).copy()
    if bake_elements:
        img = _bake_elements(img, lay)
    return img


# ── рендер одного слайда поверх мастер-подложки ──────────────────────────────
# role → (ключ размера заголовка, ключ размера тела, межстрочный загол., тела, gap)
_ROLE_STYLE = {
    "hook":  ("hook", "hook_sub", 1.22, 1.3, 34),
    "cta":   ("cta_lead", "cta_offer", 1.24, 1.4, 36),
    "point": ("point_head", "point_body", 1.2, 1.42, 30),
}
_FIT_MIN = 0.72  # минимальный множитель авто-уменьшения шрифта


def _slide_layout(d: ImageDraw.ImageDraw, slide: dict, lay: dict, fmul: float) -> dict:
    role = slide.get("role") or "point"
    hkey, bkey, hlh, blh, gap0 = _ROLE_STYLE.get(role, _ROLE_STYLE["point"])
    heading = (slide.get("heading") or "").strip()
    body = (slide.get("body") or "").strip()
    scale = lay["scale"] * fmul
    hf = _font("Bold", lay["sizes"][hkey], scale)
    bf = _font("Regular", lay["sizes"][bkey], scale)
    hlines = _wrap(d, heading, hf, lay["maxw"]) if heading else []
    blines = _wrap_multi(d, body, bf, lay["maxw"]) if body else []
    gap = int(gap0 * scale) if (hlines and blines) else 0
    total = _block_h(hlines, hf, hlh) + _block_h(blines, bf, blh) + gap
    align = lay["hook_align"] if role == "hook" else "left"
    return {"hf": hf, "bf": bf, "hlh": hlh, "blh": blh, "gap": gap,
            "align": align, "hlines": hlines, "blines": blines, "total": total}


def _fit_and_layout(d: ImageDraw.ImageDraw, slide: dict, lay: dict) -> tuple[dict, str]:
    """Подбирает множитель шрифта, чтобы слайд влез в safe-зону.
    Возвращает (layout, статус: ok | shrunk | overflow)."""
    avail = lay["safe_bot"] - lay["safe_top"]
    L = _slide_layout(d, slide, lay, 1.0)
    if L["total"] <= avail:
        return L, "ok"
    f = 1.0
    while f > _FIT_MIN:
        f = round(f - 0.06, 3)
        L = _slide_layout(d, slide, lay, f)
        if L["total"] <= avail:
            return L, "shrunk"
    return L, "overflow"


def _render_text(master: Image.Image, slide: dict, lay: dict) -> Image.Image:
    img = master.copy()
    d = ImageDraw.Draw(img)
    margin, white, shadow = lay["margin"], lay["white"], lay["shadow"]
    top, avail = lay["safe_top"], lay["safe_bot"] - lay["safe_top"]
    L, _ = _fit_and_layout(d, slide, lay)
    cx = lay["W"] // 2 if L["align"] == "center" else None
    y = top + max(0, (avail - L["total"]) // 2)
    if L["hlines"]:
        y = _draw_block(d, L["hlines"], L["hf"], margin, y, white, L["hlh"], align=L["align"], cx=cx, shadow=shadow)
    if L["hlines"] and L["blines"]:
        y += L["gap"]
    if L["blines"]:
        _draw_block(d, L["blines"], L["bf"], margin, y, white, L["blh"], align=L["align"], cx=cx, shadow=shadow)
    return img


def measure_fit(slide: dict, layout: dict | None = None) -> str:
    """Статус вместимости слайда без отрисовки: ok | shrunk | overflow."""
    lay = _merge(DEFAULT_LAYOUT, layout)
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    _, fit = _fit_and_layout(d, slide, lay)
    return fit


def render_carousel(bg_path: str | None, slides: list[dict], layout: dict | None = None) -> list[Image.Image]:
    """Возвращает список PIL-изображений (по одному на слайд) в порядке idx."""
    lay = _merge(DEFAULT_LAYOUT, layout)
    master = build_master(bg_path, layout)
    ordered = sorted(slides, key=lambda x: x.get("idx", 0))
    return [_render_text(master, sl, lay) for sl in ordered]

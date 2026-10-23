"""Render the eval's screenshot inputs (text-thread and paper-bill PNGs) from plain text.

    uv run --project backend --with pillow python evals/make_screenshots.py

Each `*.thread` file becomes a phone screenshot: a "with: NAME" header line, then
"<" (helper) or ">" (Jordan) lines, and "--- label" for a timestamp. Each `*.paper`
file becomes a photo-of-paper bill. The PNG is written next to the source with the
same stem; the source stays in the repo so the image can be regenerated.
"""

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent / "scenarios"
SANS = "/System/Library/Fonts/Helvetica.ttc"
MONO = "/System/Library/Fonts/Menlo.ttc"


def thread(src: Path) -> Image.Image:
    lines = src.read_text().strip().splitlines()
    name = lines[0].removeprefix("with:").strip()
    font = ImageFont.truetype(SANS, 30)
    small = ImageFont.truetype(SANS, 24)
    W, pad, maxw = 750, 28, 470
    items = []
    for raw in lines[1:]:
        if raw.startswith("---"):
            items.append(("time", raw[3:].strip()))
        elif raw[:1] in "<>":
            items.append(("them" if raw[0] == "<" else "me", raw[1:].strip()))
    # measure
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    blocks, h = [], 150
    for kind, text in items:
        if kind == "time":
            blocks.append((kind, [text], 0, 50))
            h += 50
            continue
        wrapped = []
        for para in text.split("\\n"):
            wrapped += textwrap.wrap(para, 26) or [""]
        w = max(probe.textlength(l, font=font) for l in wrapped)
        bh = 40 * len(wrapped) + 30
        blocks.append((kind, wrapped, min(w, maxw), bh))
        h += bh + 16
    img = Image.new("RGB", (W, h + 40), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 120], fill=(246, 246, 246))
    d.text((W / 2, 70), name, font=ImageFont.truetype(SANS, 34), fill="black", anchor="mm")
    y = 150
    for kind, wrapped, w, bh in blocks:
        if kind == "time":
            d.text((W / 2, y + 20), wrapped[0], font=small, fill=(140, 140, 140), anchor="mm")
            y += 50
            continue
        x0 = pad if kind == "them" else W - pad - w - 36
        fill, ink = ((233, 233, 235), "black") if kind == "them" else ((10, 132, 255), "white")
        d.rounded_rectangle([x0, y, x0 + w + 36, y + bh], radius=26, fill=fill)
        for i, l in enumerate(wrapped):
            d.text((x0 + 18, y + 14 + 40 * i), l, font=font, fill=ink)
        y += bh + 16
    return img


def paper(src: Path) -> Image.Image:
    lines = src.read_text().rstrip().splitlines()
    font = ImageFont.truetype(MONO, 26)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    w = int(max(probe.textlength(l, font=font) for l in lines)) + 120
    img = Image.new("RGB", (w, 44 * len(lines) + 120), (250, 247, 238))
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        d.text((60, 60 + 44 * i), l, font=font, fill=(40, 40, 60))
    return img.rotate(-1.2, expand=True, fillcolor=(90, 90, 90))  # a slightly crooked phone photo


if __name__ == "__main__":
    for src in sorted(ROOT.rglob("*.thread")) + sorted(ROOT.rglob("*.paper")):
        out = src.with_suffix(".png")
        (thread if src.suffix == ".thread" else paper)(src).save(out)
        print(out.relative_to(ROOT.parent))

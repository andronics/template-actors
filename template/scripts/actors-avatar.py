#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=11"]
# ///
"""Template-owned: template-actors (decision 0001). Post-process an actor's avatar to the spec, or check one.

    uv run scripts/actors-avatar.py IN.png|jpg OUT.png [--preview DIR]   make OUT to the spec
    uv run scripts/actors-avatar.py --check FILE.png ...                  does it meet the spec?

The spec (question 10; generating the image stays manual): a PNG, 1024x1024, at most 256
colours (a palette image), under 1 MB, and legible at 20 to 64 px. The input is centre-cropped to
a square, resized, and quantised; if it is still 1 MB or more, it is quantised to fewer colours
until it fits. --preview writes 20, 32 and 64 px copies to look at; the check also fails an
image whose 20 px copy is nearly flat (too little contrast to read). The same file is the
GitHub App's logo (App settings, Display information) and the vault note's avatar.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image, ImageStat

SIZE = 1024
COLOURS = 256
MAX_BYTES = 1024 * 1024
PREVIEWS = (20, 32, 64)
# The standard deviation of a 20 px copy's luminance (0-255) below which it reads as a blur.
MIN_CONTRAST = 24.0


def square(image: Image.Image) -> Image.Image:
    w, h = image.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    return image.crop((left, top, left + side, top + side))


def encode(image: Image.Image, colours: int) -> bytes:
    if image.mode == "RGBA":
        quantised = image.quantize(colors=colours, method=Image.Quantize.FASTOCTREE)
    else:
        quantised = image.convert("RGB").quantize(colors=colours, method=Image.Quantize.MEDIANCUT)
    buffer = io.BytesIO()
    quantised.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def contrast(image: Image.Image, px: int = 20) -> float:
    small = image.convert("RGB").resize((px, px), Image.Resampling.LANCZOS).convert("L")
    return float(ImageStat.Stat(small).stddev[0])


def process(src: Path, dst: Path, preview: Path | None = None) -> list[str]:
    with Image.open(src) as opened:
        image = opened.convert("RGBA" if "A" in opened.getbands() or opened.mode == "P" else "RGB")
    image = square(image).resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    colours = COLOURS
    data = encode(image, colours)
    while len(data) >= MAX_BYTES and colours > 16:
        colours //= 2
        data = encode(image, colours)
    dst.write_bytes(data)
    if preview is not None:
        preview.mkdir(parents=True, exist_ok=True)
        for px in PREVIEWS:
            image.resize((px, px), Image.Resampling.LANCZOS).save(preview / f"{dst.stem}-{px}.png")
    return check(dst)


def check(path: Path) -> list[str]:
    errors = []
    size = path.stat().st_size
    try:
        with Image.open(path) as image:
            image.load()
            fmt, dims, mode = image.format, image.size, image.mode
            used = image.getcolors(maxcolors=COLOURS + 1)
            flat = contrast(image)
    except OSError as e:
        return [f"error: {path}: not an image: {e}"]
    if fmt != "PNG":
        errors.append(f"error: {path}: {fmt}, not PNG")
    if dims != (SIZE, SIZE):
        errors.append(f"error: {path}: {dims[0]}x{dims[1]}, not {SIZE}x{SIZE}")
    if used is None:
        errors.append(f"error: {path}: more than {COLOURS} colours")
    elif mode not in ("P", "L", "1"):
        errors.append(f"error: {path}: {mode}, not a palette image (run actors-avatar.py on it)")
    if size >= MAX_BYTES:
        errors.append(f"error: {path}: {size} bytes, not under 1 MB")
    if flat < MIN_CONTRAST:
        errors.append(f"error: {path}: at 20 px it is nearly flat (contrast {flat:.0f} < {MIN_CONTRAST:.0f}):"
                      " a bolder, simpler figure reads at 20 to 64 px")  # fmt: skip
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--check", action="store_true", help="check each file against the spec")
    parser.add_argument("--preview", type=Path, help="also write 20, 32 and 64 px copies here")
    args = parser.parse_args(argv)
    if args.check:
        errors = [e for p in args.paths for e in check(p)]
    elif len(args.paths) != 2:
        parser.error("give IN and OUT, or --check FILE...")
    else:
        src, dst = args.paths
        errors = process(src, dst, args.preview)
        if not errors:
            print(f"wrote {dst} ({dst.stat().st_size} bytes)")
    if errors:
        print("\n".join(errors))
        return 1
    if args.check:
        print(f"{len(args.paths)} avatar(s) meet the spec")
    return 0


if __name__ == "__main__":
    sys.exit(main())

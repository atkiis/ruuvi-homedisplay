"""Spectra 6 palette helpers for the colour e-paper (reTerminal E1002).

The panel can only show black, white, red, green, blue and yellow. Anything
else is dithered or rounded by the device, which makes anti-aliased text and
pastel fills look noisy. The renderer therefore draws with these colours (plus
deliberate dot patterns for "tints") and snaps the final image to them, so the
server preview matches the panel.
"""

from PIL import Image, ImageChops, ImageDraw

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (208, 0, 0)
GREEN = (0, 144, 40)
BLUE = (0, 48, 168)
YELLOW = (240, 192, 0)

PALETTE = [BLACK, WHITE, RED, GREEN, BLUE, YELLOW]

_palette_image = Image.new("P", (1, 1))
_flat = [c for rgb in PALETTE for c in rgb]
_palette_image.putpalette(_flat + _flat[:3] * (256 - len(PALETTE)))

# 4x4 Bayer matrix, values 0..15.
_BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]


def snap(img: Image.Image) -> Image.Image:
    """Map every pixel to the nearest panel colour without dithering."""
    return img.convert("RGB").quantize(palette=_palette_image, dither=Image.Dither.NONE).convert("RGB")


def _pattern(size: tuple[int, int], density: float) -> Image.Image:
    """Mask with roughly ``density`` (0..1) of its pixels set, in a Bayer pattern."""
    threshold = round(density * 16)
    tile = Image.new("L", (4, 4))
    tile.putdata([255 if v < threshold else 0 for v in _BAYER])
    mask = Image.new("L", size)
    for y in range(0, size[1], 4):
        for x in range(0, size[0], 4):
            mask.paste(tile, (x, y))
    return mask


def tint_rounded(img: Image.Image, box, colour, density: float = 0.25, radius: int = 6,
                 base=WHITE) -> None:
    """Fill a rounded box with ``base`` plus a dot pattern of ``colour``.

    Stands in for pastel fills, which the panel cannot show as flat colour.
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    size = (x1 - x0 + 1, y1 - y0 + 1)
    shape = Image.new("L", size, 0)
    ImageDraw.Draw(shape).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    ImageDraw.Draw(img).rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=base)
    img.paste(colour, (x0, y0), ImageChops.multiply(shape, _pattern(size, density)))


def tint_rect(img: Image.Image, box, colour, density: float = 0.25) -> None:
    x0, y0, x1, y1 = (int(v) for v in box)
    size = (x1 - x0 + 1, y1 - y0 + 1)
    img.paste(colour, (x0, y0), _pattern(size, density))

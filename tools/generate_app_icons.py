from __future__ import annotations

import struct
from io import BytesIO
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFilter


ROOT_DIR = Path(__file__).resolve().parent.parent
RESOURCE_DIR = ROOT_DIR / "buildResources"
PUBLIC_DIR = ROOT_DIR / "public"
ASSET_DIR = ROOT_DIR / "src" / "assets"
ICONSET_DIR = RESOURCE_DIR / "icon.iconset"
PNG_OUTPUT = RESOURCE_DIR / "icon.png"
ICNS_OUTPUT = RESOURCE_DIR / "icon.icns"
ICO_OUTPUT = RESOURCE_DIR / "icon.ico"
PUBLIC_ICON_OUTPUT = PUBLIC_DIR / "icon.png"
FAVICON_OUTPUT = PUBLIC_DIR / "favicon.ico"
APP_ICON_ASSET_OUTPUT = ASSET_DIR / "app-icon.png"

MAC_ICONSET: tuple[tuple[str, int], ...] = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_BLOCKS: tuple[tuple[str, int], ...] = (
    ("icp4", 16),
    ("icp5", 32),
    ("icp6", 64),
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),
)


def scaled(value: float, factor: float) -> int:
    return round(value * factor)


def lerp(start: int, end: int, ratio: float) -> int:
    return round(start + (end - start) * ratio)


def diagonal_gradient(size: int, colors: tuple[tuple[int, int, int], ...]) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    pixels = image.load()
    stops = (0.0, 0.58, 1.0)

    for y in range(size):
        for x in range(size):
            ratio = (x + y) / (2 * (size - 1))
            if ratio <= stops[1]:
                local = ratio / stops[1]
                c0, c1 = colors[0], colors[1]
            else:
                local = (ratio - stops[1]) / (stops[2] - stops[1])
                c0, c1 = colors[1], colors[2]

            pixels[x, y] = (
                lerp(c0[0], c1[0], local),
                lerp(c0[1], c1[1], local),
                lerp(c0[2], c1[2], local),
                255,
            )

    return image


def rounded_mask(size: int, bbox: tuple[int, int, int, int], radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(bbox, radius=radius, fill=255)
    return mask


def draw_round_line(
    draw: ImageDraw.ImageDraw,
    points: Iterable[tuple[int, int]],
    *,
    fill: tuple[int, int, int, int],
    width: int,
) -> None:
    point_list = list(points)
    draw.line(point_list, fill=fill, width=width, joint="curve")
    radius = width // 2
    for x, y in point_list:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def draw_segmented_round_line(
    draw: ImageDraw.ImageDraw,
    points: Iterable[tuple[int, int]],
    *,
    fills: tuple[tuple[int, int, int, int], ...],
    width: int,
) -> None:
    point_list = list(points)
    radius = width // 2

    for index, (start, end) in enumerate(zip(point_list, point_list[1:])):
        fill = fills[min(index, len(fills) - 1)]
        draw.line((start, end), fill=fill, width=width)
        for x, y in (start, end):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def cubic_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    a = (1 - t) ** 3
    b = 3 * (1 - t) ** 2 * t
    c = 3 * (1 - t) * t**2
    d = t**3
    return (
        a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
        a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
    )


def sample_cubic_path(
    segments: Iterable[tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]],
    *,
    steps: int,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for segment_index, segment in enumerate(segments):
        start = 0 if segment_index == 0 else 1
        for step in range(start, steps + 1):
            points.append(cubic_point(*segment, step / steps))
    return points


def mix_rgba(
    start: tuple[int, int, int, int],
    end: tuple[int, int, int, int],
    ratio: float,
) -> tuple[int, int, int, int]:
    return (
        lerp(start[0], end[0], ratio),
        lerp(start[1], end[1], ratio),
        lerp(start[2], end[2], ratio),
        lerp(start[3], end[3], ratio),
    )


def curve_color(ratio: float) -> tuple[int, int, int, int]:
    cyan = (14, 165, 233, 255)
    blue = (37, 99, 235, 255)
    indigo = (99, 102, 241, 255)

    if ratio <= 0.52:
        return mix_rgba(cyan, blue, ratio / 0.52)
    return mix_rgba(blue, indigo, (ratio - 0.52) / 0.48)


def draw_gradient_curve(
    draw: ImageDraw.ImageDraw,
    points: Iterable[tuple[int, int]],
    *,
    width: int,
) -> None:
    point_list = list(points)
    radius = width // 2
    segment_count = max(1, len(point_list) - 1)

    for index, (start, end) in enumerate(zip(point_list, point_list[1:])):
        fill = curve_color(index / segment_count)
        draw.line((start, end), fill=fill, width=width)
        for x, y in (start, end):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def render_icon(size: int) -> Image.Image:
    # 4x 超采样用于保证小尺寸 PNG / ICO 边缘仍然干净。
    scale = 4
    canvas_size = size * scale
    factor = canvas_size / 1024
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))

    shell_box = tuple(scaled(value, factor) for value in (118, 106, 906, 894))
    shell_radius = scaled(178, factor)
    shadow = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_offset = scaled(26, factor)
    shadow_draw.rounded_rectangle(
        (shell_box[0], shell_box[1] + shadow_offset, shell_box[2], shell_box[3] + shadow_offset),
        radius=shell_radius,
        fill=(15, 23, 42, 46),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(scaled(24, factor)))
    image.alpha_composite(shadow)

    mask = rounded_mask(canvas_size, shell_box, shell_radius)
    tile = Image.new("RGBA", (canvas_size, canvas_size), (255, 255, 255, 255))
    image.alpha_composite(Image.composite(tile, Image.new("RGBA", (canvas_size, canvas_size)), mask))

    draw = ImageDraw.Draw(image)
    border_box = tuple(scaled(value, factor) for value in (119, 107, 905, 893))
    draw.rounded_rectangle(border_box, radius=scaled(177, factor), outline=(226, 232, 240, 190), width=max(1, scaled(2, factor)))

    curve = sample_cubic_path(
        (
            ((286, 632), (348, 482), (442, 366), (544, 430)),
            ((544, 430), (652, 498), (596, 686), (748, 606)),
        ),
        steps=28,
    )
    scaled_curve = [(scaled(x, factor), scaled(y, factor)) for x, y in curve]
    draw_round_line(draw, scaled_curve, fill=(219, 234, 254, 255), width=scaled(58, factor))
    draw_gradient_curve(draw, scaled_curve, width=scaled(36, factor))

    node_specs = (
        (286, 632, (14, 165, 233, 255), 58, 19),
        (544, 430, (37, 99, 235, 255), 48, 15),
        (748, 606, (99, 102, 241, 255), 58, 19),
    )
    for cx, cy, outer_fill, outer_size, inner_size in node_specs:
        x = scaled(cx, factor)
        y = scaled(cy, factor)
        outer = scaled(outer_size, factor)
        inner = scaled(inner_size, factor)
        draw.ellipse((x - outer, y - outer, x + outer, y + outer), fill=outer_fill)
        draw.ellipse((x - inner, y - inner, x + inner, y + inner), fill=(255, 255, 255, 255))

    return image.resize((size, size), Image.Resampling.LANCZOS)


def write_png(path: Path, size: int) -> None:
    render_icon(size).save(path, "PNG")


def make_icns() -> None:
    if ICONSET_DIR.exists():
        for item in ICONSET_DIR.iterdir():
            item.unlink()
    else:
        ICONSET_DIR.mkdir(parents=True)

    for filename, size in MAC_ICONSET:
        write_png(ICONSET_DIR / filename, size)

    blocks: list[bytes] = []
    for block_type, size in ICNS_BLOCKS:
        data = png_bytes(size)
        blocks.append(block_type.encode("ascii") + struct.pack(">I", len(data) + 8) + data)

    ICNS_OUTPUT.write_bytes(b"icns" + struct.pack(">I", 8 + sum(len(block) for block in blocks)) + b"".join(blocks))


def png_bytes(size: int) -> bytes:
    buffer = BytesIO()
    render_icon(size).save(buffer, "PNG")
    return buffer.getvalue()


def make_ico() -> None:
    images = [(size, png_bytes(size)) for size in ICO_SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + len(images) * 16
    entries: list[bytes] = []

    for size, data in images:
        width = 0 if size >= 256 else size
        height = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(data), offset))
        offset += len(data)

    ICO_OUTPUT.write_bytes(header + b"".join(entries) + b"".join(data for _, data in images))


def main() -> None:
    RESOURCE_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    write_png(PNG_OUTPUT, 1024)
    write_png(PUBLIC_ICON_OUTPUT, 1024)
    write_png(APP_ICON_ASSET_OUTPUT, 512)
    make_icns()
    make_ico()
    FAVICON_OUTPUT.write_bytes(ICO_OUTPUT.read_bytes())


if __name__ == "__main__":
    main()

import io
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rasterio.io import MemoryFile
from rasterio.windows import Window
from rasterio.windows import transform as window_transform
from shapely.ops import polylabel

SPI_LABELS = {
    1: "Extremely Dry",
    2: "Severely Dry",
    3: "Moderately Dry",
    4: "Near Normal",
    5: "Moderately Wet",
    6: "Severely Wet",
    7: "Extremely Wet",
}

STI_LABELS = {
    1: "Extremely Cold",
    2: "Severely Cold",
    3: "Moderately Cold",
    4: "Near Normal",
    5: "Moderately Hot",
    6: "Severely Hot",
    7: "Extremely Hot",
}

# Tens digit is STI, ones digit is SPI. Same 49 colors as the Earth Engine app.
BIVARIATE_PALETTE = {
    "11": "#d5d4d5", "12": "#bdc7d2", "13": "#a5b9cf", "14": "#8cabcd", "15": "#709eca", "16": "#5091c7", "17": "#1a84c3",
    "21": "#dcc0b5", "22": "#c3b3b3", "23": "#aaa7b1", "24": "#8f9bae", "25": "#738fac", "26": "#5284a9", "27": "#2278a6",
    "31": "#e0ab96", "32": "#c6a095", "33": "#ab9693", "34": "#8f8b91", "35": "#72818f", "36": "#52778d", "37": "#246c8a",
    "41": "#e29678", "42": "#c68d77", "43": "#aa8576", "44": "#8d7c74", "45": "#6f7373", "46": "#4f6a71", "47": "#22606f",
    "51": "#e1815b", "52": "#c37a5a", "53": "#a67459", "54": "#896d58", "55": "#6a6557", "56": "#4a5d56", "57": "#1f5555",
    "61": "#dd6a3d", "62": "#bf673d", "63": "#a1633d", "64": "#825e3d", "65": "#64583d", "66": "#43513c", "67": "#184a3b",
    "71": "#d9521e", "72": "#b9531f", "73": "#995220", "74": "#7a5021", "75": "#5b4b22", "76": "#3a4622", "77": "#0f3e22",
}

SPI_ORDER = [1, 2, 3, 4, 5, 6, 7]
STI_ORDER = [7, 6, 5, 4, 3, 2, 1]


def classify_value(value):
    if value <= -2:
        return 1
    if value <= -1.5:
        return 2
    if value <= -1:
        return 3
    if value < 1:
        return 4
    if value < 1.5:
        return 5
    if value < 2:
        return 6
    return 7
BIVARIATE_CODES = {sti * 10 + spi for sti in range(1, 8) for spi in range(1, 8)}

_RGB = {
    code: tuple(int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    for code, hex_color in BIVARIATE_PALETTE.items()
}


_LEGEND_NOTE = "Columns are temperature, from cold to hot. Rows are rainfall, from wet to dry."
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


def legend_png():
    cold_to_hot = list(range(1, 8))
    wet_to_dry = list(range(7, 0, -1))
    font = _legend_font(28)
    note_font = _legend_font(22)
    ink = (28, 28, 28, 255)
    cell = 64
    gap = 6
    pad = 28

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    label_w = max(_text_size(probe, SPI_LABELS[spi], font)[0] for spi in wet_to_dry)
    header_h = max(_text_size(probe, STI_LABELS[sti], font)[0] for sti in cold_to_hot)
    note_w, note_h = _text_size(probe, _LEGEND_NOTE, note_font)
    grid = 7 * cell + 6 * gap
    width = pad + label_w + 16 + grid + pad
    width = max(width, pad + note_w + pad)
    height = pad + header_h + 12 + grid + 18 + note_h + pad

    image = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)
    origin_x = pad + label_w + 16
    origin_y = pad + header_h + 12

    for index, sti in enumerate(cold_to_hot):
        label = _upright_label(STI_LABELS[sti], font, ink)
        x = origin_x + index * (cell + gap) + (cell - label.width) // 2
        y = origin_y - 12 - label.height
        image.paste(label, (x, y), label)

    for row, spi in enumerate(wet_to_dry):
        text_w, text_h = _text_size(probe, SPI_LABELS[spi], font)
        y = origin_y + row * (cell + gap) + (cell - text_h) // 2
        draw.text((origin_x - 16 - text_w, y), SPI_LABELS[spi], font=font, fill=ink)
        for col, sti in enumerate(cold_to_hot):
            color = _RGB[f"{sti}{spi}"]
            x0 = origin_x + col * (cell + gap)
            y0 = origin_y + row * (cell + gap)
            draw.rectangle([x0, y0, x0 + cell - 1, y0 + cell - 1], fill=color, outline=(210, 210, 210))

    draw.text((pad, origin_y + grid + 18), _LEGEND_NOTE, font=note_font, fill=ink)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _legend_font(size):
    for path in _FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _text_size(draw, text, font):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def _upright_label(text, font, fill):
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    width, height = _text_size(draw, text, font)
    plate = Image.new("RGBA", (width + 2, height + 2), (0, 0, 0, 0))
    ImageDraw.Draw(plate).text((1, 1), text, font=font, fill=fill)
    return plate.rotate(90, expand=True)


def render_bivariate_png(tif_bytes, max_side=2400):
    rgba, _, _ = _colorized_bivariate(tif_bytes)
    image = _fit_image(Image.fromarray(rgba, mode="RGBA"), max_side)
    return _png_bytes(image), max(rgba.shape[:2]) > max_side


def render_labeled_png(tif_bytes, gdf, name_field, max_side=1800):
    """Bivariate map with the shapefile outline and a small name from ``name_field`` on each polygon."""
    if name_field not in getattr(gdf, "columns", ()):
        raise ValueError(f"The shapefile has no field named {name_field}.")
    rgba, transform, crs = _colorized_bivariate(tif_bytes)
    work = gdf.to_crs(crs) if crs is not None else gdf
    fitted = _fit_image(Image.fromarray(rgba, mode="RGBA"), max_side)
    sx = fitted.width / rgba.shape[1]
    sy = fitted.height / rgba.shape[0]
    inv = ~transform
    names = [_feature_label(value) for value in work[name_field].tolist()]
    short_side = min(fitted.size)
    font_size = _map_font_size(short_side, sum(bool(name) for name in names))
    font = _legend_font(font_size)
    stroke = max(2, font_size // 8)
    margin = _map_margin(names, font, stroke, font_size, short_side)
    plate = Image.new("RGBA", (fitted.width + margin * 2, fitted.height + margin * 2), (255, 255, 255, 255))
    plate.paste(fitted, (margin, margin), fitted)
    draw = ImageDraw.Draw(plate)
    line_w = max(2, round(short_side / 420))
    left, bottom, right, top = _raster_bounds(rgba.shape[0], rgba.shape[1], transform)

    placed = []
    pending = []
    for geom, name in zip(work.geometry, names):
        if geom is None or geom.is_empty:
            continue
        gminx, gminy, gmaxx, gmaxy = geom.bounds
        if gmaxx < left or gminx > right or gmaxy < bottom or gminy > top:
            continue
        _draw_boundary(draw, geom, inv, sx, sy, margin, line_w)
        if not name:
            continue
        for part in _parts(geom):
            point = _interior_point(part)
            px, py = _to_px(inv, point.x, point.y, sx, sy, margin)
            pending.append((part.area, px, py, name))

    pending.sort(key=lambda item: item[0], reverse=True)
    ink = (24, 24, 24, 255)
    halo = (255, 255, 255, 255)
    for _, px, py, name in pending:
        if not (-margin <= px <= plate.width + margin and -margin <= py <= plate.height + margin):
            continue
        box = _label_box(draw, name, font, px, py, stroke)
        if box is None or _overlaps(box, placed):
            continue
        placed.append(box)
        draw.text((px, py), name, font=font, fill=ink, anchor="mm", stroke_width=stroke, stroke_fill=halo)

    return _png_bytes(plate.convert("RGB"))


def _colorized_bivariate(tif_bytes):
    with MemoryFile(tif_bytes) as mem, mem.open() as src:
        if src.count < 5:
            raise ValueError(f"Expected a 5-band GeoTIFF, found {src.count} band(s).")
        band = src.read(5, masked=True)
        src_transform = src.transform
        crs = src.crs

    values = np.ma.filled(band.astype("float64"), np.nan) if np.ma.isMaskedArray(band) else band.astype("float64")
    rounded = np.rint(values)
    valid = np.isfinite(values) & (np.abs(values - rounded) < 0.05)
    codes = np.where(valid, rounded, -1).astype(np.int32)

    rgba = np.zeros(codes.shape + (4,), dtype=np.uint8)
    for code, rgb in _RGB.items():
        rgba[codes == int(code), :3] = rgb
        rgba[codes == int(code), 3] = 255

    present = np.where(rgba[:, :, 3] > 0)
    if present[0].size == 0:
        raise ValueError("The raster has no bivariate pixels inside the boundary.")
    row0 = int(present[0].min())
    row1 = int(present[0].max()) + 1
    col0 = int(present[1].min())
    col1 = int(present[1].max()) + 1
    rgba = rgba[row0:row1, col0:col1]
    cropped = window_transform(Window(col0, row0, col1 - col0, row1 - row0), src_transform)
    return rgba, cropped, crs


def _fit_image(image, max_side):
    long_side = max(image.size)
    if long_side <= max_side:
        return image
    scale = max_side / long_side
    return image.resize(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.Resampling.NEAREST,
    )


def _png_bytes(image):
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _feature_label(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    text = str(value).strip()
    if text.lower() in ("", "none", "nan", "<na>", "nat"):
        return ""
    return text


def _map_font_size(short_side, count):
    size = short_side / (36 + 2.2 * math.sqrt(max(count, 1)))
    return int(max(13, min(28, round(size))))


def _map_margin(names, font, stroke, font_size, short_side):
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    widest = 0
    for name in names:
        if not name:
            continue
        left, _, right, _ = probe.textbbox((0, 0), name, font=font, stroke_width=stroke)
        widest = max(widest, right - left)
    room = max(18, short_side // 10)
    return max(10, font_size // 2 + 4, min(widest // 2 + 6, room))


def _parts(geom):
    kind = geom.geom_type
    if kind == "Polygon":
        yield geom
    elif kind in ("MultiPolygon", "GeometryCollection"):
        for part in geom.geoms:
            yield from _parts(part)


def _interior_point(geom):
    minx, miny, maxx, maxy = geom.bounds
    span = max(maxx - minx, maxy - miny, 0)
    try:
        point = polylabel(geom, tolerance=max(span / 100, 1e-9))
        if geom.covers(point):
            return point
    except Exception:
        pass
    return geom.representative_point()


def _raster_bounds(height, width, transform):
    left, top = transform * (0, 0)
    right, bottom = transform * (width, height)
    return min(left, right), min(bottom, top), max(left, right), max(bottom, top)


def _to_px(inv, x, y, sx, sy, margin):
    col, row = inv * (float(x), float(y))
    return col * sx + margin, row * sy + margin


def _draw_boundary(draw, geom, inv, sx, sy, margin, width):
    for ring in _rings(geom):
        points = [_to_px(inv, x, y, sx, sy, margin) for x, y in ring.coords]
        if len(points) < 2:
            continue
        draw.line(points, fill=(255, 255, 255, 230), width=width + 2)
        draw.line(points, fill=(32, 32, 32, 255), width=width)


def _rings(geom):
    kind = geom.geom_type
    if kind == "Polygon":
        yield geom.exterior
        yield from geom.interiors
    elif kind == "MultiPolygon":
        for part in geom.geoms:
            yield from _rings(part)
    elif kind == "GeometryCollection":
        for part in geom.geoms:
            yield from _rings(part)


def _label_box(draw, text, font, px, py, stroke):
    left, top, right, bottom = draw.textbbox((px, py), text, font=font, anchor="mm", stroke_width=stroke)
    if right <= left or bottom <= top:
        return None
    pad = 2
    return (left - pad, top - pad, right + pad, bottom + pad)


def _overlaps(box, placed):
    left, top, right, bottom = box
    for other in placed:
        oleft, otop, oright, obottom = other
        if left < oright and right > oleft and top < obottom and bottom > otop:
            return True
    return False

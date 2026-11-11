import io

import numpy as np
from PIL import Image
from rasterio.io import MemoryFile

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


def legend_html():
    cold_to_hot = list(range(1, 8))
    wet_to_dry = list(range(7, 0, -1))
    head = "<tr><th></th>" + "".join(
        f"<th style='font-weight:500;padding:4px 6px'>{STI_LABELS[sti]}</th>"
        for sti in cold_to_hot
    ) + "</tr>"
    rows = []
    for spi in wet_to_dry:
        cells = []
        for sti in cold_to_hot:
            color = BIVARIATE_PALETTE[f"{sti}{spi}"]
            title = f"{STI_LABELS[sti]}, {SPI_LABELS[spi]}"
            cells.append(
                f"<td title='{title}' style='background:{color};width:34px;height:22px;padding:0'></td>"
            )
        rows.append(
            f"<tr><th style='font-weight:500;text-align:right;padding-right:8px'>{SPI_LABELS[spi]}</th>"
            + "".join(cells)
            + "</tr>"
        )
    return (
        "<table style='border-collapse:collapse;font-size:12px'>"
        "<caption style='caption-side:bottom;text-align:left;padding-top:8px'>"
        "Columns are temperature, from cold to hot. Rows are rainfall, from wet to dry."
        "</caption>"
        + head
        + "".join(rows)
        + "</table>"
    )


def render_bivariate_png(tif_bytes, max_side=2400):
    with MemoryFile(tif_bytes) as mem, mem.open() as src:
        if src.count < 5:
            raise ValueError(f"Expected a 5-band GeoTIFF, found {src.count} band(s).")
        band = src.read(5, masked=True)

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
    rgba = rgba[present[0].min() : present[0].max() + 1, present[1].min() : present[1].max() + 1]

    image = Image.fromarray(rgba, mode="RGBA")
    long_side = max(image.size)
    if long_side > max_side:
        scale = max_side / long_side
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.Resampling.NEAREST,
        )
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue(), long_side > max_side

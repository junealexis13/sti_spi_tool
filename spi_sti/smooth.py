import math

import numpy as np
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.warp import reproject

MIN_CELL_M = 10
MAX_PIXELS = 40_000_000
_SENTINEL = np.float32(-9999)
_BANDS = ("SPI3", "STI3", "SPI_Class", "STI_Class", "Bivariate_Code")


def smooth_geotiff(tif_bytes, cell_m, source_scale_m):
    cell_m = int(cell_m)
    if cell_m < MIN_CELL_M:
        raise ValueError(f"Smoothed cells cannot be finer than {MIN_CELL_M} m.")
    if cell_m >= int(source_scale_m):
        return tif_bytes, False

    with MemoryFile(tif_bytes) as mem, mem.open() as src:
        if src.crs is None or not src.crs.is_geographic:
            raise ValueError("The downloaded raster is not in longitude and latitude, so it cannot be smoothed to meters.")
        if src.count < 2:
            raise ValueError(f"Expected SPI and STI bands, found {src.count}.")
        spi = src.read(1).astype("float32")
        sti = src.read(2).astype("float32")
        src_crs = src.crs
        src_transform = src.transform
        bounds = src.bounds
        nodata = src.nodata

    valid = _finite(spi, nodata) & _finite(sti, nodata)
    if not np.any(valid):
        raise ValueError("The raster has no SPI and STI values to smooth.")

    lat = (bounds.bottom + bounds.top) / 2
    xres, yres = _cell_degrees(cell_m, lat)
    left, bottom, right, top = bounds.left, bounds.bottom, bounds.right, bounds.top
    width = max(1, int(math.ceil((right - left) / xres)))
    height = max(1, int(math.ceil((top - bottom) / yres)))
    pixels = width * height
    if pixels > MAX_PIXELS:
        finest = int(math.ceil(math.sqrt((right - left) * (top - bottom) * (111_320 ** 2) * abs(math.cos(math.radians(lat))) / MAX_PIXELS)))
        finest = max(finest, cell_m + 1, MIN_CELL_M)
        raise ValueError(
            f"Smoothing to {cell_m} m would make a {width:,} by {height:,} raster. "
            f"On this boundary the finest cell that still fits is about {finest} m."
        )

    dst_transform = from_origin(left, top, xres, yres)
    spi_f = _warp(np.where(valid, spi, _SENTINEL), src_transform, src_crs, dst_transform, src_crs, height, width)
    sti_f = _warp(np.where(valid, sti, _SENTINEL), src_transform, src_crs, dst_transform, src_crs, height, width)
    cover = _warp(np.where(valid, np.float32(1), _SENTINEL), src_transform, src_crs, dst_transform, src_crs, height, width, Resampling.bilinear)
    keep = np.isfinite(spi_f) & np.isfinite(sti_f) & np.isfinite(cover) & (cover >= 0.5)
    spi_f = np.where(keep, spi_f, np.nan).astype("float32")
    sti_f = np.where(keep, sti_f, np.nan).astype("float32")
    spi_c = _classify(spi_f)
    sti_c = _classify(sti_f)
    code = np.where(keep, sti_c * 10 + spi_c, np.nan).astype("float32")
    stacked = np.stack([spi_f, sti_f, spi_c, sti_c, code])

    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 5,
        "dtype": "float32",
        "crs": src_crs,
        "transform": dst_transform,
        "compress": "deflate",
        "predictor": 3,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(stacked)
            for index, name in enumerate(_BANDS, start=1):
                dst.set_band_description(index, name)
        return mem.read(), True


def _finite(values, nodata):
    ok = np.isfinite(values)
    if nodata is not None and np.isfinite(nodata):
        ok &= values != np.float32(nodata)
    return ok


def _cell_degrees(cell_m, lat):
    lat = min(89.0, max(-89.0, float(lat)))
    yres = cell_m / 111_320.0
    xres = cell_m / (111_320.0 * math.cos(math.radians(lat)))
    return xres, yres


def _warp(values, src_transform, src_crs, dst_transform, dst_crs, height, width, resampling=Resampling.bilinear):
    dest = np.full((height, width), _SENTINEL, dtype="float32")
    reproject(
        source=np.ascontiguousarray(values, dtype="float32"),
        destination=dest,
        src_transform=src_transform,
        src_crs=src_crs,
        src_nodata=float(_SENTINEL),
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        dst_nodata=float(_SENTINEL),
        resampling=resampling,
    )
    return np.where(dest == _SENTINEL, np.nan, dest)


def _classify(values):
    out = np.full(values.shape, np.nan, dtype="float32")
    valid = np.isfinite(values)
    out[valid] = 4
    out[valid & (values <= -2)] = 1
    out[valid & (values > -2) & (values <= -1.5)] = 2
    out[valid & (values > -1.5) & (values <= -1)] = 3
    out[valid & (values >= 1) & (values < 1.5)] = 5
    out[valid & (values >= 1.5) & (values < 2)] = 6
    out[valid & (values >= 2)] = 7
    return out

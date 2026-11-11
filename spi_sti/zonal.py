import csv
import io
import re
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio.features import rasterize
from rasterio.io import MemoryFile
from shapely import force_2d, make_valid
from shapely.ops import unary_union

from spi_sti.style import BIVARIATE_CODES, SPI_LABELS, SPI_ORDER, STI_LABELS, STI_ORDER

NAME_HINTS = ("MUN_NAME", "MUNI_NAME", "NAME_2", "ADM3_EN", "NAME", "MUNICIPALI", "TOWN")
_CLEAN = re.compile(r"[^A-Za-z0-9_\-]+")


def clean_stem(name):
    stem = _CLEAN.sub("_", (name or "").strip()).strip("_")
    return (stem or "boundary")[:80]


def read_boundary(data):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            shapefiles = [
                name for name in archive.namelist()
                if name.lower().endswith(".shp") and not name.startswith("__MACOSX") and "/." not in name
            ]
            if len(shapefiles) != 1:
                raise ValueError("Zip the shapefile so the archive contains exactly one .shp, with its .shx, .dbf, and .prj.")
            _extract(archive, root)
        matches = [path for path in root.rglob("*.shp") if "__MACOSX" not in path.parts]
        gdf = gpd.read_file(matches[0])
        layer = Path(shapefiles[0]).stem

    if gdf.crs is None:
        raise ValueError("The shapefile has no .prj, so its coordinate system is unknown.")
    gdf = gdf.to_crs(4326)
    gdf["geometry"] = gdf.geometry.apply(_as_polygon)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    if gdf.empty:
        raise ValueError("No polygons were found in the shapefile.")

    minx, miny, maxx, maxy = gdf.total_bounds
    if max(abs(minx), abs(maxx)) > 180 or max(abs(miny), abs(maxy)) > 90:
        raise ValueError("The boundary is still not in longitude and latitude. Check the .prj.")
    return gdf.reset_index(drop=True), clean_stem(layer)


def guess_name_field(gdf, preferred="Mun_Name"):
    columns = [col for col in gdf.columns if col != "geometry"]
    if preferred in columns and not _numeric(gdf[preferred]):
        return preferred
    for hint in NAME_HINTS:
        for col in columns:
            if hint in col.upper() and not _numeric(gdf[col]):
                return col
    for col in columns:
        if not _numeric(gdf[col]):
            return col
    raise ValueError("No text field on the shapefile looks like a municipality name.")


def zone_tables(tif_bytes, gdf, name_field, layer_name):
    if name_field not in gdf.columns:
        raise ValueError(f"The shapefile has no field named {name_field}.")

    with MemoryFile(tif_bytes) as mem, mem.open() as src:
        if src.count < 5:
            raise ValueError(f"Expected a 5-band GeoTIFF, found {src.count} band(s).")
        work = gdf.to_crs(src.crs) if src.crs else gdf
        zones = rasterize(
            ((geom, i) for i, geom in enumerate(work.geometry, start=1)),
            out_shape=(src.height, src.width),
            transform=src.transform,
            fill=0,
            dtype="int32",
        )
        spi_band = src.read(3, masked=True)
        sti_band = src.read(4, masked=True)
        biv_band = src.read(5, masked=True)

    names = [_label(value) for value in work[name_field].tolist()]
    spi_map, spi_nodata = _presence(spi_band, zones, names, set(SPI_LABELS))
    sti_map, sti_nodata = _presence(sti_band, zones, names, set(STI_LABELS))
    biv_map, biv_nodata = _presence(biv_band, zones, names, BIVARIATE_CODES)

    biv_rows = _bivariate_rows(biv_map, biv_nodata)
    spi_rows = _summary_rows(spi_map, spi_nodata, SPI_ORDER, SPI_LABELS, "SPI_Class")
    sti_rows = _summary_rows(sti_map, sti_nodata, STI_ORDER, STI_LABELS, "STI_Class")

    biv_headers = ["SPI / STI"] + [STI_LABELS[k] for k in STI_ORDER] + ["Missing Pixels (NoData)"]
    stem = clean_stem(layer_name)
    files = {
        f"{stem}_SPI_STI_Bivariate_Matrix.csv": _csv_bytes(biv_headers, biv_rows),
        f"{stem}_SPI_Summary.csv": _csv_bytes(["SPI_Class", "Municipalities"], spi_rows),
        f"{stem}_STI_Summary.csv": _csv_bytes(["STI_Class", "Municipalities"], sti_rows),
    }
    return {
        "files": files,
        "bivariate": biv_rows,
        "spi": spi_rows,
        "sti": sti_rows,
        "counts": {
            "spi": {code: len(spi_map.get(code, ())) for code in SPI_ORDER},
            "sti": {code: len(sti_map.get(code, ())) for code in STI_ORDER},
            "bivariate": {code: len(biv_map.get(code, ())) for code in BIVARIATE_CODES},
            "spi_nodata": len(spi_nodata),
            "sti_nodata": len(sti_nodata),
            "bivariate_nodata": len(biv_nodata),
        },
    }


def _extract(archive, root):
    root = root.resolve()
    for member in archive.infolist():
        dest = (root / member.filename).resolve()
        if not dest.is_relative_to(root):
            raise ValueError("The zip contains a path outside its own folder.")
    archive.extractall(root)


def _as_polygon(geom):
    if geom is None or geom.is_empty:
        return None
    geom = make_valid(force_2d(geom))
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    if geom.geom_type == "GeometryCollection":
        parts = [part for part in geom.geoms if part.geom_type in ("Polygon", "MultiPolygon")]
        if parts:
            return unary_union(parts)
    return None


def _numeric(series):
    return series.dtype.kind in "biufc"


def _label(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    text = str(value).strip()
    if text.lower() in ("", "none", "nan"):
        return ""
    return text


def _presence(band, zones, names, allowed):
    values = np.ma.filled(band.astype("float64"), np.nan) if np.ma.isMaskedArray(band) else np.asarray(band, dtype="float64")
    rounded = np.rint(values)
    known = np.isfinite(values) & (np.abs(values - rounded) < 0.05)
    codes = np.where(known, rounded, -1).astype(np.int32)
    known &= np.isin(codes, list(allowed))

    found = {code: set() for code in allowed}
    nodata = set()
    for zone_id in np.unique(zones):
        if zone_id == 0:
            continue
        name = names[zone_id - 1]
        if not name:
            continue
        inside = zones == zone_id
        if not np.any(inside):
            continue
        if np.any(inside & ~known):
            nodata.add(name)
        for code in np.unique(codes[inside & known]):
            found[int(code)].add(name)
    return found, nodata


def _joined(names):
    ordered = sorted(names)
    return ", ".join(ordered) if ordered else "-"


def _bivariate_rows(biv_map, nodata):
    missing = _joined(nodata) if nodata else "-"
    rows = []
    for index, spi in enumerate(SPI_ORDER):
        row = {"SPI / STI": SPI_LABELS[spi]}
        for sti in STI_ORDER:
            code = sti * 10 + spi
            row[STI_LABELS[sti]] = _joined(biv_map.get(code, ()))
        row["Missing Pixels (NoData)"] = missing if index == 0 else "-"
        rows.append(row)
    return rows


def _summary_rows(class_map, nodata, order, labels, field):
    rows = []
    for code in order:
        names = sorted(class_map.get(code, ()))
        if names:
            rows.append({field: labels[code], "Municipalities": ", ".join(names)})
    if nodata:
        rows.append({field: "Missing Pixels (NoData)", "Municipalities": ", ".join(sorted(nodata))})
    return rows


def _csv_bytes(headers, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")

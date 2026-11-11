import io
import json
import math
import re
import zipfile
from datetime import datetime, timezone

import ee
import requests

CHIRPS_FINAL = "UCSB-CHC/CHIRPS/V3/PENTAD"
CHIRPS_PRELIM = "projects/climate-engine-pro/assets/ce-chirps-prelim-pentad"
ERA5 = "projects/climate-engine-pro/assets/ce-ag-era5-v2/daily"
ERA_BAND = "Temperature_Air_2m_Mean_24h"
KELVIN_TO_C = 273.15

# A 3-month window shares two months with the next one, so the outlook is a damped copy of the latest index.
PHI_SPI = 0.65
PHI_STI = 0.70

BASELINE_START = 1990
BASELINE_END = 2025
MIN_BASELINE_YEARS = 10
WARN_BASELINE_YEARS = 20
MAP_FIRST_YEAR = 1992
EXPORT_SCALES = (1000, 2000, 5000)

MODE_OBSERVED = "Observed"
MODE_OUTLOOK = "Persistence Outlook"
MODES = (MODE_OBSERVED, MODE_OUTLOOK)

GAMMA_MIN_NONZERO = 3
GAMMA_MIN_A = 1e-6
SPI_P_EPS = 1e-6
SPLICE_TOLERANCE_MS = 7 * 86400000

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_RETRYABLE = re.compile(r"less than or equal|too large|pixel grid|memory|timed out|too many", re.I)
_gammainc_x_first = None


def month_label(year, month):
    return f"{MONTHS[month - 1]} {year}"


def add_months(year, month, count):
    index = year * 12 + (month - 1) + count
    return index // 12, index % 12 + 1


def prepare_datasets():
    final = ee.ImageCollection(CHIRPS_FINAL).select("precipitation")
    prelim = ee.ImageCollection(CHIRPS_PRELIM).select("precipitation")
    era = ee.ImageCollection(ERA5).select(ERA_BAND)
    now = ee.Date(int(datetime.now(timezone.utc).timestamp() * 1000))

    try:
        res = ee.Dictionary({
            "finalFirst": final.aggregate_min("system:time_start"),
            "finalLast": _last_millis(final, now),
            "prelimFirst": prelim.aggregate_min("system:time_start"),
            "prelimLast": _last_millis(prelim, now),
            "eraFirst": era.aggregate_min("system:time_start"),
            "eraLast": _last_millis(era, now),
        }).getInfo()
    except ee.EEException as exc:
        raise ValueError(_asset_message(exc)) from exc

    if not res:
        raise ValueError("Earth Engine did not return dataset dates.")

    has_final = res.get("finalLast") is not None
    has_prelim = res.get("prelimLast") is not None
    if res.get("eraFirst") is None or res.get("eraLast") is None:
        raise ValueError(
            "AgERA5 did not return any images. The service account needs read access to "
            f"{ERA5}."
        )
    if not has_final and not has_prelim:
        raise ValueError("CHIRPS did not return any images.")

    contiguous = (
        has_final
        and has_prelim
        and res["prelimFirst"] <= res["finalLast"] + SPLICE_TOLERANCE_MS
    )
    use_prelim = has_prelim and (not has_final or contiguous)
    if use_prelim and has_final:
        recent = prelim.filter(ee.Filter.gt("system:time_start", res["finalLast"]))
        chirps = final.merge(recent)
        chirps_last = max(res["finalLast"], res["prelimLast"])
    elif use_prelim:
        chirps = prelim
        chirps_last = res["prelimLast"]
    else:
        chirps = final
        chirps_last = res["finalLast"]

    chirps_first = res["finalFirst"] if has_final else res["prelimFirst"]
    info = {
        "chirps_start": _first_full_month(chirps_first),
        "chirps_end": _pentad_end(chirps_last),
        "final_end": _pentad_end(res["finalLast"]) if has_final else (1980, 1),
        "use_final": has_final,
        "use_prelim": use_prelim,
        "prelim_gap": has_final and has_prelim and not contiguous,
        "era_start": _first_full_month(res["eraFirst"]),
        "era_end": _daily_end(res["eraLast"]),
    }
    return {"chirps": chirps, "era": era, "info": info}


def coverage_text(info):
    if info["use_final"]:
        rain = (
            f"CHIRPS final: {month_label(*info['chirps_start'])} to "
            f"{month_label(*add_months(*info['final_end'], -1))}"
        )
        if info["use_prelim"] and _ym_key(*info["chirps_end"]) > _ym_key(*info["final_end"]):
            rain += (
                f", then preliminary through {month_label(*add_months(*info['chirps_end'], -1))}"
            )
    else:
        rain = (
            f"CHIRPS preliminary only: {month_label(*info['chirps_start'])} to "
            f"{month_label(*add_months(*info['chirps_end'], -1))}"
        )
    era = (
        f"AgERA5: {month_label(*info['era_start'])} to "
        f"{month_label(*add_months(*info['era_end'], -1))}"
    )
    gap = ""
    if info["prelim_gap"]:
        gap = " Preliminary CHIRPS does not meet the final series, so those recent months are left out."
    return f"Complete months — {rain}. {era}.{gap}"


def latest_complete_month(info):
    end = _min_ym(info["chirps_end"], info["era_end"])
    return add_months(end[0], end[1], -1)


def suggest_filename(mode, year, month):
    end_y, end_m = add_months(int(year), int(month), 1)
    start_y, start_m = add_months(end_y, end_m, -3)
    last_y, last_m = add_months(end_y, end_m, -1)
    kind = "Persistence_Outlook" if mode == MODE_OUTLOOK else "Hindcast"
    return clean_filename(f"SPI3_STI3_{kind}_{start_y:04d}-{start_m:02d}_to_{last_y:04d}-{last_m:02d}")


def clean_filename(name):
    text = re.sub(r"\.(tif|tiff|zip)$", "", (name or "").strip(), flags=re.I)
    text = re.sub(r"[^A-Za-z0-9_\-]+", "_", text).strip("_")
    return text[:100]


def scales_from(start):
    start = int(start)
    if start in EXPORT_SCALES:
        return [scale for scale in EXPORT_SCALES if scale >= start]
    return [start] + [scale for scale in EXPORT_SCALES if scale > start]


def build_export(gdf, mode, year, month, datasets):
    if mode not in MODES:
        raise ValueError(f"Unknown analysis mode: {mode}")

    year, month = int(year), int(month)
    info = datasets["info"]
    forecast = mode == MODE_OUTLOOK
    end_y, end_m = add_months(year, month, 1)
    start = add_months(end_y, end_m, -3)
    observed = add_months(start[0], start[1], -1) if forecast else start
    observed_end = add_months(observed[0], observed[1], 3)
    _require_window(info, observed, observed_end, forecast)

    chirps_years, era_years = _baseline_years(info, observed[1])
    if len(chirps_years) < MIN_BASELINE_YEARS or len(era_years) < MIN_BASELINE_YEARS:
        raise ValueError(
            f"Not enough baseline years for this season (CHIRPS: {len(chirps_years)}, "
            f"AgERA5: {len(era_years)}; minimum {MIN_BASELINE_YEARS}). "
            "Check dataset coverage or the baseline years."
        )

    spi = _gamma_spi(
        _precip_window(datasets["chirps"], observed[0], observed[1]),
        datasets["chirps"],
        chirps_years,
        observed[1],
    )
    sti = _temperature_z(
        _temp_window(datasets["era"], observed[0], observed[1]),
        datasets["era"],
        era_years,
        observed[1],
    )
    if forecast:
        spi = spi.multiply(PHI_SPI)
        sti = sti.multiply(PHI_STI)

    spi_class = _classify(spi)
    sti_class = _classify(sti)
    code = sti_class.multiply(10).add(spi_class)
    features = _feature_collection(gdf)
    mask = ee.Image(0).byte().paint(features, 1)
    image = ee.Image.cat(
        spi.rename("SPI3"),
        sti.rename("STI3"),
        spi_class.rename("SPI_Class"),
        sti_class.rename("STI_Class"),
        code.rename("Bivariate_Code"),
    ).toFloat().updateMask(mask)

    target_end = add_months(end_y, end_m, -1)
    observed_last = add_months(observed_end[0], observed_end[1], -1)
    summary, short = _summary(
        forecast, start, target_end, observed, observed_last, len(chirps_years), len(era_years)
    )
    meta = {
        "summary": summary,
        "short_baseline": short,
        "filename": suggest_filename(mode, year, month),
    }
    return image, features.geometry(100).bounds(100), meta


def download_geotiff(image, region, name, scales):
    name = clean_filename(name) or "SPI3_STI3"
    region_json = region.getInfo()
    errors = []
    for scale in scales:
        try:
            url = image.getDownloadURL({
                "name": name,
                "region": region_json,
                "scale": int(scale),
                "crs": "EPSG:4326",
                "format": "ZIPPED_GEO_TIFF",
                "filePerBand": False,
            })
            response = requests.get(url, timeout=(30, 900))
            if response.status_code >= 400:
                raise RuntimeError(response.text[:800])
            payload = response.content
            if payload[:1] in (b"{", b"<"):
                raise RuntimeError(payload[:800].decode("utf-8", "replace"))
            return _unpack_tiff(payload), int(scale)
        except Exception as exc:
            errors.append(f"{int(scale)} m: {exc}")
            if not _RETRYABLE.search(str(exc)):
                break
    raise RuntimeError("The GeoTIFF download failed. " + " ".join(errors))


def _last_millis(collection, now):
    recent = collection.filterDate(now.advance(-3, "year"), now.advance(1, "day"))
    return ee.Algorithms.If(
        recent.size().gt(0),
        recent.aggregate_max("system:time_start"),
        collection.aggregate_max("system:time_start"),
    )


def _asset_message(exc):
    text = str(exc).strip()
    lowered = text.lower()
    if not any(token in lowered for token in ("climate-engine", "ce-chirps", "ce-ag-era5", "not found", "permission", "access")):
        return text
    return (
        "Earth Engine could not read the Climate Engine preliminary CHIRPS or AgERA5 collection. "
        "The service account needs access to those assets. "
        f"Earth Engine said: {text}"
    )


def _ym_key(year, month):
    return year * 12 + month


def _min_ym(left, right):
    return left if _ym_key(*left) <= _ym_key(*right) else right


def _millis_to_utc(millis):
    return datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc)


def _first_full_month(millis):
    if millis is None:
        return (9999, 1)
    stamp = _millis_to_utc(millis)
    if stamp.day == 1:
        return stamp.year, stamp.month
    return add_months(stamp.year, stamp.month, 1)


def _daily_end(millis):
    if millis is None:
        return (1980, 1)
    stamp = _millis_to_utc(millis)
    nxt = _millis_to_utc(int(millis) + 86400000)
    if nxt.month != stamp.month:
        return add_months(stamp.year, stamp.month, 1)
    return stamp.year, stamp.month


def _pentad_end(millis):
    if millis is None:
        return (1980, 1)
    stamp = _millis_to_utc(millis)
    if stamp.day >= 26:
        return add_months(stamp.year, stamp.month, 1)
    return stamp.year, stamp.month


def _covered(data_start, window_start):
    return _ym_key(*window_start) >= _ym_key(*data_start)


def _available(data_end, window_end):
    return _ym_key(*window_end) <= _ym_key(*data_end)


def _require_window(info, observed, observed_end, forecast):
    missing = []
    if not _available(info["chirps_end"], observed_end):
        last = add_months(*info["chirps_end"], -1)
        missing.append(f"CHIRPS (complete through {month_label(*last)})")
    if not _available(info["era_end"], observed_end):
        last = add_months(*info["era_end"], -1)
        missing.append(f"AgERA5 (complete through {month_label(*last)})")
    if not missing:
        return
    last_month = add_months(observed_end[0], observed_end[1], -1)
    window = f"{month_label(*observed)} – {month_label(*last_month)}"
    hint = "." if forecast else ", or switch to Persistence Outlook."
    raise ValueError(
        f"Observed window {window} is not fully available: {'; '.join(missing)}. "
        f"Choose an earlier month{hint}"
    )


def _baseline_years(info, month):
    chirps_years = []
    era_years = []
    for year in range(BASELINE_START, BASELINE_END + 1):
        start = (year, month)
        end = add_months(year, month, 3)
        if _covered(info["chirps_start"], start) and _available(info["chirps_end"], end):
            chirps_years.append(year)
        if _covered(info["era_start"], start) and _available(info["era_end"], end):
            era_years.append(year)
    return chirps_years, era_years


def _precip_window(collection, year, month):
    start = ee.Date.fromYMD(int(year), int(month), 1)
    return collection.filterDate(start, start.advance(3, "month")).sum().rename("precip")


def _temp_window(collection, year, month):
    start = ee.Date.fromYMD(int(year), int(month), 1)
    return (
        collection.filterDate(start, start.advance(3, "month"))
        .mean()
        .subtract(KELVIN_TO_C)
        .rename("temp")
    )


def _classify(img):
    return (
        ee.Image.constant(4)
        .where(img.lte(-2.0), 1)
        .where(img.gt(-2.0).And(img.lte(-1.5)), 2)
        .where(img.gt(-1.5).And(img.lte(-1.0)), 3)
        .where(img.gte(1.0).And(img.lt(1.5)), 5)
        .where(img.gte(1.5).And(img.lt(2.0)), 6)
        .where(img.gte(2.0), 7)
        .updateMask(img.mask())
    )


def _gamma_lower(z, alpha):
    global _gammainc_x_first
    if _gammainc_x_first is None:
        _gammainc_x_first = _detect_gammainc_order()
    if _gammainc_x_first:
        return z.gammainc(alpha)
    return alpha.gammainc(z)


def _detect_gammainc_order():
    # P(a=2, x=1) = 1 - 2/e ≈ 0.2642 when the call is x.gammainc(a).
    value = (
        ee.Image.constant(1)
        .gammainc(ee.Image.constant(2))
        .reduceRegion(reducer=ee.Reducer.first(), geometry=ee.Geometry.Point([0, 0]), scale=1000)
        .values()
        .get(0)
        .getInfo()
    )
    if value is None:
        return True
    return abs(value - 0.2642) < abs(value - 0.8647)


def _gamma_spi(current, collection, years, month):
    # Thom's maximum likelihood fit on the non-zero totals, then the normal quantile. WMO-No. 1090.
    images = []
    for year in years:
        total = _precip_window(collection, year, month)
        positive = total.gt(0)
        images.append(ee.Image.cat(
            total.eq(0).rename("zero"),
            total.updateMask(positive).rename("xnz"),
            total.updateMask(positive).log().rename("lnxnz"),
            total.lte(current).rename("le"),
        ))

    stack = ee.ImageCollection.fromImages(images)
    count = stack.select("zero").count()
    n_zero = stack.select("zero").sum()
    n_nonzero = stack.select("xnz").count()
    mean_nonzero = stack.select("xnz").mean()
    mean_log = stack.select("lnxnz").mean()
    n_at_most = stack.select("le").sum()

    q = n_zero.divide(count)
    a_stat = mean_nonzero.log().subtract(mean_log)
    fit_ok = n_nonzero.gte(GAMMA_MIN_NONZERO).And(a_stat.gt(GAMMA_MIN_A)).unmask(0)
    a_safe = a_stat.unmask(1).where(fit_ok.Not(), 1)
    mean_safe = mean_nonzero.unmask(1).where(fit_ok.Not(), 1)
    alpha = a_safe.multiply(4 / 3).add(1).sqrt().add(1).divide(a_safe.multiply(4))
    beta = mean_safe.divide(alpha)

    z = current.max(0).divide(beta)
    empirical = n_at_most.divide(count.add(1))
    probability = (
        ee.Image.constant(1).subtract(q).multiply(_gamma_lower(z, alpha)).add(q)
        .where(current.lte(0), q)
        .where(fit_ok.Not(), empirical)
    )
    return (
        probability.clamp(SPI_P_EPS, 1 - SPI_P_EPS)
        .multiply(2)
        .subtract(1)
        .erfInv()
        .multiply(math.sqrt(2))
        .updateMask(current.mask())
        .rename("SPI3")
    )


def _temperature_z(current, collection, years, month):
    images = [_temp_window(collection, year, month) for year in years]
    reducer = ee.Reducer.mean().combine(reducer2=ee.Reducer.stdDev(), sharedInputs=True)
    stats = ee.ImageCollection.fromImages(images).reduce(reducer)
    mean = stats.select("temp_mean")
    std = stats.select("temp_stdDev")
    return current.subtract(mean).divide(std).updateMask(std.gt(0)).rename("STI3")


def _summary(forecast, target_start, target_end, observed_start, observed_end, n_chirps, n_era):
    kind = "Persistence Outlook" if forecast else "Hindcast"
    text = f"{kind} for {month_label(*target_start)} – {month_label(*target_end)}"
    if forecast:
        text += f", persisted from {month_label(*observed_start)} – {month_label(*observed_end)}"
    text += f". Baseline years — SPI: {n_chirps}, STI: {n_era}."
    short = min(n_chirps, n_era) < WARN_BASELINE_YEARS
    if short:
        text += f" Fewer than {WARN_BASELINE_YEARS} baseline years, so read the map with caution."
    return text, short


def _feature_collection(gdf):
    features = []
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        features.append({
            "type": "Feature",
            "properties": {},
            "geometry": json.loads(json.dumps(geom.__geo_interface__)),
        })
    if not features:
        raise ValueError("No polygons to send to Earth Engine.")
    return ee.FeatureCollection({"type": "FeatureCollection", "features": features})


def _unpack_tiff(payload):
    if payload[:2] in (b"II", b"MM"):
        return payload
    if payload[:2] != b"PK":
        raise RuntimeError("Earth Engine did not return a GeoTIFF.")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith((".tif", ".tiff"))]
        if not names:
            raise RuntimeError("The download archive did not contain a GeoTIFF.")
        return archive.read(names[0])

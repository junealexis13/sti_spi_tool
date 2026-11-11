import math
from datetime import datetime, timezone

import ee
import pandas as pd

from spi_sti.indices import KELVIN_TO_C, add_months, month_label
from spi_sti.style import SPI_LABELS, STI_LABELS, classify_value

POINT_START_YEAR = 1990
POINT_SAMPLE_SCALE = 5000
TIMESCALES = (1, 3, 6, 9, 12)
FORECAST_MONTHS = 5
MIN_MONTHS = 24
GAMMA_MIN_NONZERO = 3
GAMMA_MIN_A = 1e-6

# The map damps the latest 3-month index by fixed factors. This page estimates
# lag-1 correlation from the last 24 months and steps back toward the
# calendar-month mean. That is the Earth Engine point dashboard, not the map.
PHI_MIN = 0.0
PHI_MAX = 0.9
PHI_DEFAULT = 0.3


def places_inside(gdf, name_field):
    found = {}
    if gdf is None or not name_field or name_field not in gdf.columns:
        return found
    for _, row in gdf.iterrows():
        label = _place_name(row.get(name_field))
        if not label or label in found:
            continue
        point = row.geometry.representative_point()
        found[label] = (float(point.x), float(point.y))
    return dict(sorted(found.items(), key=lambda item: item[0].lower()))


def sample_monthly(datasets, lon, lat):
    lon = float(lon)
    lat = float(lat)
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("Longitude must be between -180 and 180, and latitude between -90 and 90.")

    info = datasets["info"]
    start = _later((POINT_START_YEAR, 1), _later(info["chirps_start"], info["era_start"]))
    end = _earlier(info["chirps_end"], info["era_end"])
    count = _span(start, end)
    if count < MIN_MONTHS:
        raise ValueError("Fewer than 24 complete months are available in both CHIRPS and AgERA5.")

    base = ee.Date.fromYMD(int(start[0]), int(start[1]), 1)
    chirps = datasets["chirps"]
    era = datasets["era"]

    def month_image(step):
        opened = base.advance(ee.Number(step), "month")
        closed = opened.advance(1, "month")
        precip = chirps.filterDate(opened, closed).sum().rename("precip")
        temp = era.filterDate(opened, closed).mean().subtract(KELVIN_TO_C).rename("temp")
        return precip.addBands(temp).set("system:time_start", opened.millis())

    monthly = ee.ImageCollection.fromImages(ee.List.sequence(0, count - 1).map(month_image))
    rows = monthly.getRegion(ee.Geometry.Point([lon, lat]), POINT_SAMPLE_SCALE).getInfo()
    records = _records(rows)
    if not records:
        raise ValueError("No data returned for this point. Try another location.")
    last = add_months(end[0], end[1], -1)
    return {
        "lon": lon,
        "lat": lat,
        "start": start,
        "end": end,
        "label": f"{month_label(*start)} to {month_label(*last)}",
        "records": records,
    }


def analyze_point(records, timescale):
    timescale = int(timescale)
    if timescale not in TIMESCALES:
        raise ValueError(f"The window must be one of {', '.join(str(item) for item in TIMESCALES)} months.")

    dates = [row["date"] for row in records]
    precip = [_finite(row["precip"]) for row in records]
    temp = [_finite(row["temp"]) for row in records]
    if all(value is None for value in precip):
        raise ValueError(
            "No rainfall data at this pixel (often a coastal or ocean pixel). Try a point slightly inland."
        )
    if all(value is None for value in temp):
        raise ValueError("No AgERA5 temperature data at this pixel. Try another location.")

    precip = _rolling(precip, timescale, "sum")
    temp = _rolling(temp, timescale, "mean")
    kept = [
        (date, rain, heat)
        for date, rain, heat in zip(dates, precip, temp)
        if rain is not None and heat is not None
    ]
    if len(kept) < MIN_MONTHS:
        raise ValueError("Not enough valid months at this point to compute indices.")

    dates = [item[0] for item in kept]
    precip = [item[1] for item in kept]
    temp = [item[2] for item in kept]
    rain_stats = _precip_stats(dates, precip)
    heat_stats = _temp_stats(dates, temp)

    spi_values = []
    sti_values = []
    for date, rain, heat in zip(dates, precip, temp):
        month = date[5:7]
        stats = rain_stats[month]
        if stats["ok"]:
            fitted = gamma_cdf(rain, stats["shape"], stats["scale"]) if rain > 0 else 0
            if fitted is None or not math.isfinite(fitted):
                probability = empirical_cdf(rain, stats["sorted"])
            else:
                probability = stats["q"] + (1 - stats["q"]) * fitted
        else:
            probability = empirical_cdf(rain, stats["sorted"])
        spi_values.append(norminv(_clamp(probability, 1e-12, 1 - 1e-12)))
        heat_fit = heat_stats[month]
        if heat_fit["std"] > 0 and math.isfinite(heat_fit["std"]):
            sti_values.append((heat - heat_fit["mean"]) / heat_fit["std"])
        else:
            sti_values.append(0.0)

    history = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "precip_mm": precip,
            "temp_c": temp,
            "spi": spi_values,
            "sti": sti_values,
        }
    )
    history["spi_class"] = history["spi"].map(lambda value: SPI_LABELS[classify_value(value)])
    history["sti_class"] = history["sti"].map(lambda value: STI_LABELS[classify_value(value)])

    forecast = _forecast(history)
    return {
        "timescale": timescale,
        "phi_spi": _phi(history["spi"].tail(24).tolist()),
        "phi_sti": _phi(history["sti"].tail(24).tolist()),
        "history": history,
        "forecast": forecast,
    }


def gamma_cdf(x, shape, scale):
    if not (x > 0 and shape > 0 and scale > 0):
        return None
    magnitude = x / scale
    if magnitude < shape + 1:
        return _gamma_series(shape, magnitude)
    return _gamma_fraction(shape, magnitude)


def norminv(p):
    if p <= 0 or p >= 1 or not math.isfinite(p):
        return None
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )
    if p > 1 - plow:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def empirical_cdf(x, sorted_values):
    total = len(sorted_values)
    if total == 0:
        return 0.5
    below = 0
    while below < total and sorted_values[below] <= x:
        below += 1
    return below / (total + 1)


def _forecast(history):
    spi_clim, sti_clim = _climatology(history)
    phi_spi = _phi(history["spi"].tail(24).tolist())
    phi_sti = _phi(history["sti"].tail(24).tolist())
    last = history.iloc[-1]
    year = int(last["date"].year)
    month = int(last["date"].month)
    prev_spi = float(last["spi"])
    prev_sti = float(last["sti"])
    rows = []
    for _ in range(FORECAST_MONTHS):
        month += 1
        if month > 12:
            month = 1
            year += 1
        spi_mean = spi_clim.get(month, 0.0)
        sti_mean = sti_clim.get(month, 0.0)
        prev_spi = spi_mean + phi_spi * (prev_spi - spi_mean)
        prev_sti = sti_mean + phi_sti * (prev_sti - sti_mean)
        rows.append(
            {
                "date": pd.Timestamp(year=year, month=month, day=1),
                "spi": prev_spi,
                "sti": prev_sti,
                "spi_class": SPI_LABELS[classify_value(prev_spi)],
                "sti_class": STI_LABELS[classify_value(prev_sti)],
            }
        )
    return pd.DataFrame(rows)


def _climatology(history):
    spi_clim = {}
    sti_clim = {}
    counts = {}
    for stamp, spi, sti in zip(history["date"], history["spi"], history["sti"]):
        month = int(stamp.month)
        spi_clim[month] = spi_clim.get(month, 0.0) + float(spi)
        sti_clim[month] = sti_clim.get(month, 0.0) + float(sti)
        counts[month] = counts.get(month, 0) + 1
    for month, count in counts.items():
        spi_clim[month] /= count
        sti_clim[month] /= count
    return spi_clim, sti_clim


def _phi(values):
    numerator = 0.0
    denominator = 0.0
    for earlier, later in zip(values, values[1:]):
        if earlier is None or later is None:
            continue
        if not (math.isfinite(earlier) and math.isfinite(later)):
            continue
        numerator += earlier * later
        denominator += earlier * earlier
    estimate = numerator / denominator if denominator else PHI_DEFAULT
    return min(PHI_MAX, max(PHI_MIN, estimate))


def _precip_stats(dates, precip):
    grouped = {}
    for date, rain in zip(dates, precip):
        grouped.setdefault(date[5:7], []).append(rain)
    stats = {}
    for month, values in grouped.items():
        ordered = sorted(values)
        nonzero = [value for value in ordered if value > 0]
        zeros = sum(1 for value in ordered if value == 0)
        fit = _gamma_mle(nonzero)
        stats[month] = {
            "q": zeros / len(ordered) if ordered else 0,
            "shape": None if fit is None else fit[0],
            "scale": None if fit is None else fit[1],
            "ok": fit is not None,
            "sorted": ordered,
        }
    return stats


def _temp_stats(dates, temp):
    grouped = {}
    for date, heat in zip(dates, temp):
        grouped.setdefault(date[5:7], []).append(heat)
    stats = {}
    for month, values in grouped.items():
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / max(1, len(values) - 1)
        stats[month] = {"mean": mean, "std": math.sqrt(variance)}
    return stats


def _gamma_mle(nonzero):
    count = len(nonzero)
    if count < GAMMA_MIN_NONZERO:
        return None
    mean = sum(nonzero) / count
    log_mean = sum(math.log(value) for value in nonzero) / count
    spread = math.log(mean) - log_mean
    if not (spread > GAMMA_MIN_A) or not math.isfinite(spread):
        return None
    shape = (1 + math.sqrt(1 + 4 * spread / 3)) / (4 * spread)
    scale = mean / shape
    if not (shape > 0 and scale > 0 and math.isfinite(shape) and math.isfinite(scale)):
        return None
    return shape, scale


def _rolling(values, window, kind):
    rolled = []
    for index in range(len(values)):
        if index < window - 1:
            rolled.append(None)
            continue
        subset = [value for value in values[index - window + 1 : index + 1] if value is not None]
        if len(subset) < window:
            rolled.append(None)
            continue
        total = sum(subset)
        rolled.append(total if kind == "sum" else total / len(subset))
    return rolled


def _records(rows):
    if not rows or len(rows) < 2:
        return []
    header = list(rows[0])
    try:
        time_at = header.index("time")
        precip_at = header.index("precip")
        temp_at = header.index("temp")
    except ValueError as exc:
        raise ValueError("Earth Engine returned an unexpected table for this point.") from exc

    seen = set()
    records = []
    for row in rows[1:]:
        stamp = row[time_at]
        if stamp is None or stamp in seen:
            continue
        seen.add(stamp)
        records.append(
            {
                "date": _month_start(stamp),
                "precip": _finite(row[precip_at]),
                "temp": _finite(row[temp_at]),
            }
        )
    records.sort(key=lambda item: item["date"])
    return records


def _month_start(millis):
    stamp = datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc)
    return f"{stamp.year:04d}-{stamp.month:02d}-01"


def _finite(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _place_name(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _span(start, end):
    return (end[0] - start[0]) * 12 + (end[1] - start[1])


def _ym(year, month):
    return year * 12 + month


def _earlier(left, right):
    return left if _ym(*left) <= _ym(*right) else right


def _later(left, right):
    return left if _ym(*left) >= _ym(*right) else right


def _clamp(value, low, high):
    return max(low, min(high, value))


def _log_gamma(z):
    coeff = (
        76.18009172947146,
        -86.50532032941677,
        24.01409824083091,
        -1.231739572450155,
        0.001208650973866179,
        -5.395239384953e-06,
    )
    x = z
    y = z
    tmp = x + 5.5
    tmp -= (x + 0.5) * math.log(tmp)
    series = 1.000000000190015
    for term in coeff:
        y += 1
        series += term / y
    return -tmp + math.log(2.5066282746310005 * series / x)


def _gamma_series(shape, magnitude):
    total = 1 / shape
    rising = shape
    delta = total
    for _ in range(100):
        rising += 1
        delta *= magnitude / rising
        total += delta
        if abs(delta) < abs(total) * 3e-7:
            break
    return total * math.exp(-magnitude + shape * math.log(magnitude) - _log_gamma(shape))


def _gamma_fraction(shape, magnitude):
    gold = 0.0
    a0, a1 = 1.0, magnitude
    b0, b1 = 0.0, 1.0
    fac = 1 / a1
    estimate = b1 * fac
    for n in range(1, 101):
        a0 = (a1 + a0 * (n - shape)) * fac
        b0 = (b1 + b0 * (n - shape)) * fac
        step = n * fac
        a1 = magnitude * a0 + step * a1
        b1 = magnitude * b0 + step * b1
        if a1:
            fac = 1 / a1
            estimate = b1 * fac
        if estimate and abs((estimate - gold) / estimate) < 3e-7:
            break
        gold = estimate
    return 1 - math.exp(-magnitude + shape * math.log(magnitude) - _log_gamma(shape)) * estimate

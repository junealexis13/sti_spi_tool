import altair as alt
import numpy as np
import pandas as pd

from spi_sti.style import BIVARIATE_PALETTE, SPI_LABELS, SPI_ORDER, STI_LABELS, STI_ORDER


def class_charts(tables):
    counts = _counts(tables)
    spi = _class_frame(SPI_ORDER, SPI_LABELS, counts["spi"], "4")
    sti = _class_frame(list(range(1, 8)), STI_LABELS, counts["sti"], None)
    heat = _bivariate_frame(counts["bivariate"])
    return {
        "spi": _bars(spi, "Municipalities with each rainfall class"),
        "sti": _bars(sti, "Municipalities with each temperature class"),
        "bivariate": _heatmap(heat),
        "nodata": {
            "spi": counts.get("spi_nodata", 0),
            "sti": counts.get("sti_nodata", 0),
            "bivariate": counts.get("bivariate_nodata", 0),
        },
        "empty": int(spi["Municipalities"].sum() + sti["Municipalities"].sum()) == 0,
    }


def _counts(tables):
    counts = tables.get("counts")
    if counts:
        return counts
    spi = {code: 0 for code in SPI_ORDER}
    sti = {code: 0 for code in range(1, 8)}
    bivariate = {sti_code * 10 + spi_code: 0 for sti_code in range(1, 8) for spi_code in SPI_ORDER}
    spi_by_label = {label: code for code, label in SPI_LABELS.items()}
    sti_by_label = {label: code for code, label in STI_LABELS.items()}
    for row in tables.get("spi") or []:
        code = spi_by_label.get(row.get("SPI_Class"))
        if code:
            spi[code] = _name_count(row.get("Municipalities"))
    for row in tables.get("sti") or []:
        code = sti_by_label.get(row.get("STI_Class"))
        if code:
            sti[code] = _name_count(row.get("Municipalities"))
    for row in tables.get("bivariate") or []:
        spi_code = spi_by_label.get(row.get("SPI / STI"))
        if not spi_code:
            continue
        for label, sti_code in sti_by_label.items():
            bivariate[sti_code * 10 + spi_code] = _name_count(row.get(label))
    return {"spi": spi, "sti": sti, "bivariate": bivariate}


def _class_frame(order, labels, counts, spi_digit):
    rows = []
    for code in order:
        if spi_digit is None:
            color = BIVARIATE_PALETTE[f"{code}4"]
        else:
            color = BIVARIATE_PALETTE[f"{spi_digit}{code}"]
        rows.append({"Class": labels[code], "Municipalities": int(counts.get(code, 0)), "color": color})
    return pd.DataFrame(rows)


def _bivariate_frame(counts):
    rows = []
    for spi in range(7, 0, -1):
        for sti in range(1, 8):
            rows.append({
                "Rainfall": SPI_LABELS[spi],
                "Temperature": STI_LABELS[sti],
                "Municipalities": int(counts.get(sti * 10 + spi, 0)),
            })
    return pd.DataFrame(rows)


def _bars(frame, title):
    order = frame["Class"].tolist()
    return (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            y=alt.Y("Class:N", sort=order, title=None, axis=alt.Axis(labelLimit=0)),
            x=alt.X("Municipalities:Q", title="Municipalities"),
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip=["Class", "Municipalities"],
        )
        .properties(title=title, height=280)
    )


def _heatmap(frame):
    rain = [SPI_LABELS[code] for code in range(7, 0, -1)]
    temp = [STI_LABELS[code] for code in range(1, 8)]
    peak = float(frame["Municipalities"].max() or 0)
    base = alt.Chart(frame).encode(
        x=alt.X("Temperature:N", sort=temp, title="Temperature"),
        y=alt.Y("Rainfall:N", sort=rain, title="Rainfall", axis=alt.Axis(labelLimit=0)),
    )
    cells = base.mark_rect().encode(
        color=alt.Color(
            "Municipalities:Q",
            title="Municipalities",
            scale=alt.Scale(scheme="blues"),
        ),
        tooltip=["Rainfall", "Temperature", "Municipalities"],
    )
    labels = base.mark_text(fontSize=11).encode(
        text="Municipalities:Q",
        color=alt.condition(
            alt.datum.Municipalities > peak / 2,
            alt.value("#ffffff"),
            alt.value("#1a1a1a"),
        ),
    )
    return (cells + labels).properties(
        title="Municipalities in each rainfall and temperature class",
        height=340,
    )


def _name_count(text):
    if not text or text == "-":
        return 0
    return len([part for part in str(text).split(", ") if part])


def point_charts(history, forecast, timescale):
    return {
        "spi": _index_area(
            history,
            forecast,
            "spi",
            f"SPI ({timescale}-month)",
            "SPI",
            "Wetter (SPI > 0)",
            "Drier (SPI < 0)",
            "#1f77b4",
            "#d62728",
        ),
        "sti": _index_area(
            history,
            forecast,
            "sti",
            f"STI ({timescale}-month)",
            "STI",
            "Hotter (STI > 0)",
            "Cooler (STI < 0)",
            "#d62728",
            "#1f77b4",
        ),
        "quadrant": _quadrant(history, forecast),
    }


def _index_area(history, forecast, field, title, vtitle, pos_label, neg_label, pos_color, neg_color):
    rows = []
    for stamp, value in zip(history["date"], history[field]):
        rows.append({"date": stamp, "band": pos_label, "value": value if value > 0 else 0, "index": value})
        rows.append({"date": stamp, "band": neg_label, "value": value if value < 0 else 0, "index": value})
    long = pd.DataFrame(rows)
    areas = (
        alt.Chart(long)
        .mark_area(opacity=0.5, interpolate="linear")
        .encode(
            x=alt.X("date:T", title="Date"),
            y=alt.Y("value:Q", title=vtitle),
            color=alt.Color(
                "band:N",
                title=None,
                scale=alt.Scale(domain=[pos_label, neg_label], range=[pos_color, neg_color]),
                legend=alt.Legend(orient="top"),
            ),
            tooltip=[
                alt.Tooltip("date:T", title="Month", format="%b %Y"),
                alt.Tooltip("index:Q", title=vtitle, format=".2f"),
            ],
        )
    )
    zero = alt.Chart(pd.DataFrame({"value": [0]})).mark_rule(color="#444444", strokeWidth=1).encode(y="value:Q")
    layers = [areas, zero]
    if forecast is not None and len(forecast):
        last = history.iloc[-1]
        path = pd.DataFrame(
            {
                "date": [last["date"], *forecast["date"].tolist()],
                "value": [float(last[field]), *forecast[field].astype(float).tolist()],
            }
        )
        layers.append(
            alt.Chart(path)
            .mark_line(strokeDash=[6, 4], color="#281e5d", strokeWidth=2)
            .encode(
                x="date:T",
                y=alt.Y("value:Q"),
                tooltip=[
                    alt.Tooltip("date:T", title="Month", format="%b %Y"),
                    alt.Tooltip("value:Q", title=vtitle, format=".2f"),
                ],
            )
        )
    return alt.layer(*layers).resolve_scale(y="shared", x="shared").properties(title=title, height=280)


def _quadrant(history, forecast):
    groups = ["Historical", "Historical, dry and hot", "Outlook", "Outlook, dry and hot"]
    colors = ["#3498db", "#ff2400", "#281e5d", "#ff2400"]
    shapes = ["circle", "circle", "diamond", "diamond"]
    hist = history.copy()
    hist["group"] = np.where(
        (hist["spi"] <= -1) & (hist["sti"] >= 1),
        "Historical, dry and hot",
        "Historical",
    )
    fore = forecast.copy()
    fore["group"] = np.where(
        (fore["spi"] <= -1) & (fore["sti"] >= 1),
        "Outlook, dry and hot",
        "Outlook",
    )
    points = pd.concat([hist, fore], ignore_index=True)
    points["group"] = pd.Categorical(points["group"], categories=groups, ordered=True)
    extent = np.concatenate([points["spi"].to_numpy(), points["sti"].to_numpy()])
    limit = float(max(3.5, np.nanmax(np.abs(extent)) + 0.35))

    quads = pd.DataFrame(
        [
            {"x": -limit, "x2": 0, "y": 0, "y2": limit, "fill": "#add8e6", "name": "Wet and cool"},
            {"x": 0, "x2": limit, "y": 0, "y2": limit, "fill": "#ffff99", "name": "Wet and hot"},
            {"x": -limit, "x2": 0, "y": -limit, "y2": 0, "fill": "#d3d3d3", "name": "Dry and cool"},
            {"x": 0, "x2": limit, "y": -limit, "y2": 0, "fill": "#ffcccb", "name": "Dry and hot"},
        ]
    )
    labels = pd.DataFrame(
        [
            {"sti": -limit * 0.62, "spi": limit * 0.86, "text": "Wet and cool"},
            {"sti": limit * 0.62, "spi": limit * 0.86, "text": "Wet and hot"},
            {"sti": -limit * 0.62, "spi": -limit * 0.86, "text": "Dry and cool"},
            {"sti": limit * 0.62, "spi": -limit * 0.86, "text": "Dry and hot"},
        ]
    )
    rules = pd.DataFrame(
        [
            {"axis": "spi", "value": -1, "label": "Moderate drought", "color": "#BD924D"},
            {"axis": "spi", "value": -1.5, "label": "Severe drought", "color": "#ff0000"},
            {"axis": "sti", "value": 1, "label": "Heat stress", "color": "#BD924D"},
            {"axis": "sti", "value": 1.5, "label": "Severe heat", "color": "#ff0000"},
        ]
    )
    x_scale = alt.Scale(domain=[-limit, limit])
    y_scale = alt.Scale(domain=[-limit, limit])
    rects = (
        alt.Chart(quads)
        .mark_rect(opacity=0.35)
        .encode(
            x=alt.X("x:Q", scale=x_scale, title="STI (temperature)"),
            x2="x2:Q",
            y=alt.Y("y:Q", scale=y_scale, title="SPI (rainfall)"),
            y2="y2:Q",
            color=alt.Color("fill:N", scale=None, legend=None),
            tooltip=["name:N"],
        )
    )
    h_rules = (
        alt.Chart(rules[rules["axis"] == "spi"])
        .mark_rule(strokeDash=[4, 4], strokeWidth=1.5)
        .encode(y="value:Q", color=alt.Color("color:N", scale=None, legend=None), tooltip=["label:N"])
    )
    v_rules = (
        alt.Chart(rules[rules["axis"] == "sti"])
        .mark_rule(strokeDash=[4, 4], strokeWidth=1.5)
        .encode(x="value:Q", color=alt.Color("color:N", scale=None, legend=None), tooltip=["label:N"])
    )
    zero_h = alt.Chart(pd.DataFrame({"v": [0]})).mark_rule(color="#666666", strokeWidth=1).encode(y="v:Q")
    zero_v = alt.Chart(pd.DataFrame({"v": [0]})).mark_rule(color="#666666", strokeWidth=1).encode(x="v:Q")
    legend = alt.Legend(orient="top", title=None)
    dots = (
        alt.Chart(points)
        .mark_point(filled=True, opacity=0.75)
        .encode(
            x=alt.X("sti:Q", scale=x_scale, title="STI (temperature)"),
            y=alt.Y("spi:Q", scale=y_scale, title="SPI (rainfall)"),
            color=alt.Color("group:N", scale=alt.Scale(domain=groups, range=colors), legend=legend),
            shape=alt.Shape("group:N", scale=alt.Scale(domain=groups, range=shapes), legend=legend),
            size=alt.Size(
                "group:N",
                scale=alt.Scale(domain=groups, range=[50, 50, 140, 140]),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("date:T", title="Month", format="%b %Y"),
                alt.Tooltip("group:N", title="Series"),
                alt.Tooltip("spi:Q", title="SPI", format=".2f"),
                alt.Tooltip("sti:Q", title="STI", format=".2f"),
            ],
        )
    )
    names = (
        alt.Chart(labels)
        .mark_text(fontSize=12, fontStyle="italic", color="#5c5c5c")
        .encode(x="sti:Q", y="spi:Q", text="text:N")
    )
    return (rects + zero_h + zero_v + h_rules + v_rules + dots + names).properties(
        title="SPI and STI quadrants",
        height=520,
    )

import folium
import pandas as pd
import streamlit as st
from shapely.geometry import Point
from streamlit_folium import st_folium

from spi_sti.charts import point_charts
from spi_sti.indices import clean_filename, coverage_text, latest_complete_month, month_label, prepare_datasets
from spi_sti.point import POINT_SAMPLE_SCALE, TIMESCALES, analyze_point, places_inside, sample_monthly
from spi_sti.ui import connect, explain

SENTINEL = "Select a municipality"
_COLUMNS = {
    "Month": st.column_config.TextColumn("Month"),
    "Rainfall mm": st.column_config.NumberColumn("Rainfall mm", format="%.1f", help="Sum over the selected window."),
    "Temperature °C": st.column_config.NumberColumn("Temperature °C", format="%.2f", help="Mean over the selected window."),
    "SPI": st.column_config.NumberColumn(format="%.2f"),
    "STI": st.column_config.NumberColumn(format="%.2f"),
    "SPI class": st.column_config.TextColumn("SPI class"),
    "STI class": st.column_config.TextColumn("STI class"),
}


def render():
    _prepare_state()
    gdf = st.session_state.get("gdf")
    name_field = st.session_state.get("name_field")
    places = places_inside(gdf, name_field)
    st.session_state["places"] = places
    options = [SENTINEL, *places]
    if st.session_state.get("point_place") not in options:
        st.session_state["point_place"] = SENTINEL

    st.title("Point analysis")
    st.write(
        "Sample one location the way the Earth Engine point dashboard does. "
        "The series runs from 1990 through the latest month that both CHIRPS and AgERA5 have finished. "
        "Each calendar month is standardized on its own history at that point."
    )

    map_col, controls = st.columns([3, 2], vertical_alignment="top")
    with map_col:
        st.caption("Click the map to move the pin. The sample is the 5 km pixel under the pin.")
        clicked = _selection_map(gdf, name_field)
        if clicked and clicked != st.session_state.get("_point_click"):
            lon, lat = clicked
            st.session_state["_point_click"] = clicked
            st.session_state["point_lon"] = lon
            st.session_state["point_lat"] = lat
            st.session_state["point_name"] = f"Point [{lon:.4f}, {lat:.4f}]"
            st.session_state["point_place"] = SENTINEL
            st.rerun()

    with controls:
        if places:
            st.selectbox("Municipality", options, key="point_place", on_change=_apply_place)
            st.caption("Choosing a municipality moves the pin to a point inside that polygon.")
        else:
            st.caption("Upload a boundary in the sidebar to pick a municipality by name. A map click still works.")
        st.number_input("Longitude", min_value=-180.0, max_value=180.0, step=0.0001, format="%.4f", key="point_lon", on_change=_apply_typed)
        st.number_input("Latitude", min_value=-90.0, max_value=90.0, step=0.0001, format="%.4f", key="point_lat", on_change=_apply_typed)
        st.selectbox(
            "Window",
            TIMESCALES,
            index=TIMESCALES.index(3),
            format_func=lambda months: f"{months} month" if months == 1 else f"{months} months",
            key="point_timescale",
            help="Rainfall is summed over this many months. Temperature is averaged.",
        )
        name = _active_name(places)
        st.markdown(f"**Selected point:** {name}")
        _boundary_note(gdf)
        run = st.button(
            "Run point analysis",
            type="primary",
            disabled="key" not in st.session_state,
            width="stretch",
        )
        if "key" not in st.session_state:
            st.caption("Upload a service account JSON in the sidebar first.")

    if run:
        _sample(name)

    _show_cached()


def _prepare_state():
    st.session_state.setdefault("point_lon", 121.2410)
    st.session_state.setdefault("point_lat", 14.1650)
    st.session_state.setdefault("point_name", "Point [121.2410, 14.1650]")
    st.session_state.setdefault("point_place", SENTINEL)
    st.session_state.setdefault("point_timescale", 3)
    if "pending_place" in st.session_state:
        st.session_state["point_place"] = st.session_state.pop("pending_place")
        lon = float(st.session_state["point_lon"])
        lat = float(st.session_state["point_lat"])
        st.session_state["point_name"] = f"Point [{lon:.4f}, {lat:.4f}]"


def _apply_place():
    choice = st.session_state.get("point_place")
    places = st.session_state.get("places") or {}
    if choice not in places:
        return
    lon, lat = places[choice]
    st.session_state["point_lon"] = lon
    st.session_state["point_lat"] = lat
    st.session_state["point_name"] = f"{choice} ({lon:.4f}, {lat:.4f})"
    st.session_state["point_fly"] = {"lat": lat, "lng": lon, "zoom": 12}
    st.rerun()


def _apply_typed():
    places = st.session_state.get("places") or {}
    choice = st.session_state.get("point_place")
    lon = round(float(st.session_state["point_lon"]), 4)
    lat = round(float(st.session_state["point_lat"]), 4)
    if choice in places and tuple(round(value, 4) for value in places[choice]) == (lon, lat):
        return
    st.session_state["pending_place"] = SENTINEL
    st.session_state["point_fly"] = {"lat": lat, "lng": lon}
    st.rerun()


def _active_name(places):
    choice = st.session_state.get("point_place")
    lon = round(float(st.session_state["point_lon"]), 4)
    lat = round(float(st.session_state["point_lat"]), 4)
    if choice in places and tuple(round(value, 4) for value in places[choice]) == (lon, lat):
        return f"{choice} ({lon:.4f}, {lat:.4f})"
    return f"Point [{lon:.4f}, {lat:.4f}]"


def _boundary_note(gdf):
    if gdf is None:
        return
    lon = float(st.session_state["point_lon"])
    lat = float(st.session_state["point_lat"])
    inside = bool(gdf.geometry.intersects(Point(lon, lat)).any())
    if inside:
        st.caption("The pin is inside the uploaded boundary.")
    else:
        st.caption("The pin is outside the uploaded boundary. The sample still runs.")


def _selection_map(gdf, name_field):
    lon = float(st.session_state["point_lon"])
    lat = float(st.session_state["point_lat"])
    layer = st.session_state.get("layer")
    if gdf is not None and st.session_state.get("_map_layer") != (layer, name_field):
        st.session_state["_map_layer"] = (layer, name_field)
        st.session_state.pop("point_fly", None)

    if gdf is not None:
        minx, miny, maxx, maxy = gdf.total_bounds
        canvas = folium.Map(tiles="OpenStreetMap", control_scale=True)
        canvas.fit_bounds([[miny, minx], [maxy, maxx]])
        folium.GeoJson(
            _boundary_json(gdf, name_field),
            style_function=lambda _feature: {
                "color": "#40AD4A",
                "weight": 1.5,
                "fillColor": "#40AD4A",
                "fillOpacity": 0.08,
            },
            tooltip=folium.GeoJsonTooltip(fields=[name_field], aliases=["Name"]) if name_field else None,
        ).add_to(canvas)
    else:
        canvas = folium.Map(location=[12.5, 122.0], zoom_start=6, tiles="OpenStreetMap", control_scale=True)

    marker = folium.FeatureGroup(name="Selected point")
    folium.CircleMarker(
        [lat, lon],
        radius=7,
        color="#C0392B",
        weight=2,
        fill=True,
        fill_color="#C0392B",
        fill_opacity=0.95,
        tooltip=st.session_state.get("point_name") or "Selected point",
    ).add_to(marker)

    fly = st.session_state.pop("point_fly", None)
    event = st_folium(
        canvas,
        key="point-analysis-map",
        height=640,
        use_container_width=True,
        returned_objects=["last_clicked"],
        feature_group_to_add=marker,
        center=(fly["lat"], fly["lng"]) if fly else None,
        zoom=fly.get("zoom") if fly else None,
    )
    click = (event or {}).get("last_clicked") or {}
    lng = click.get("lng", click.get("lon"))
    clicked_lat = click.get("lat")
    if lng is None or clicked_lat is None:
        return None
    return (round(float(lng), 5), round(float(clicked_lat), 5))


def _boundary_json(gdf, name_field):
    token = (st.session_state.get("layer"), name_field, len(gdf))
    if st.session_state.get("_geo_token") == token:
        return st.session_state["_geojson"]
    columns = [name_field, "geometry"] if name_field else ["geometry"]
    slim = gdf[columns].copy()
    if name_field:
        slim[name_field] = slim[name_field].map(lambda value: "" if pd.isna(value) else str(value))
    slim["geometry"] = slim.geometry.map(_draw_geom)
    payload = slim.to_json()
    st.session_state["_geo_token"] = token
    st.session_state["_geojson"] = payload
    return payload


def _draw_geom(geom):
    simplified = geom.simplify(0.001, preserve_topology=True)
    if simplified.is_empty:
        return geom
    return simplified


def _sample(name):
    lon = round(float(st.session_state["point_lon"]), 4)
    lat = round(float(st.session_state["point_lat"]), 4)
    error = None
    with st.status("Sampling this point...", expanded=True) as status:
        try:
            status.write("Signing in to Earth Engine.")
            connect()
            status.write("Checking which months CHIRPS and AgERA5 cover.")
            datasets = prepare_datasets()
            st.session_state["coverage"] = datasets["info"]
            status.write(
                f"Reading monthly rainfall and temperature at {POINT_SAMPLE_SCALE / 1000:g} km. "
                "The first request for a location takes a minute or two."
            )
            sampled = sample_monthly(datasets, lon, lat)
            st.session_state["monthly"] = {
                "key": (lon, lat),
                "name": name,
                "label": sampled["label"],
                "records": sampled["records"],
            }
            status.update(label="Series ready", state="complete")
        except Exception as exc:
            error = exc
            status.update(label="The point could not be sampled", state="error")
    if error:
        st.error(explain(error))


def _show_cached():
    coverage = st.session_state.get("coverage")
    if coverage:
        st.info(coverage_text(coverage))
        latest = latest_complete_month(coverage)
        st.caption(f"Latest month with both datasets complete: {month_label(*latest)}.")

    monthly = st.session_state.get("monthly")
    current = (round(float(st.session_state["point_lon"]), 4), round(float(st.session_state["point_lat"]), 4))
    timescale = int(st.session_state.get("point_timescale") or 3)
    if monthly and monthly["key"] == current:
        try:
            view = analyze_point(monthly["records"], timescale)
        except ValueError as exc:
            st.session_state.pop("point_view", None)
            st.error(str(exc))
            return
        st.session_state["point_view"] = view
        st.session_state["point_view_meta"] = {
            "key": current,
            "name": monthly["name"],
            "label": monthly["label"],
        }
    else:
        view = st.session_state.get("point_view")
        meta = st.session_state.get("point_view_meta") or {}
        if view and meta.get("key") != current:
            st.info(
                f"The charts below are still for {meta.get('name', 'the previous point')}. "
                "Run point analysis to sample this pin."
            )
            _show_view(view, meta)
        else:
            st.caption(
                "The pin starts near Los Baños. Move it, then run the analysis. "
                "Changing the window later reuses the sampled months."
            )
        return

    meta = st.session_state.get("point_view_meta") or {}
    _show_view(st.session_state.get("point_view"), meta)


def _show_view(view, meta):
    if not view:
        return
    history = view["history"]
    forecast = view["forecast"]
    last = history.iloc[-1]
    title = meta.get("name") or "Selected point"
    st.subheader(title)
    st.caption(
        f"{view['timescale']}-month window, baseline {meta.get('label', 'the sampled series')}. "
        f"Sampled at {POINT_SAMPLE_SCALE / 1000:g} km. "
        f"The dashed outlook uses the last 24 months "
        f"(SPI persistence {view['phi_spi']:.2f}, STI persistence {view['phi_sti']:.2f}), "
        "pulled back toward that calendar month's average. "
        "This is separate from the map's fixed 0.65 and 0.70 outlook."
    )

    month_col, spi_col, sti_col = st.columns(3)
    month_col.metric("Latest month", last["date"].strftime("%b %Y"))
    spi_col.metric(f"SPI · {last['spi_class']}", f"{last['spi']:.2f}")
    sti_col.metric(f"STI · {last['sti_class']}", f"{last['sti']:.2f}")
    if float(last["spi"]) <= -1 and float(last["sti"]) >= 1:
        st.warning("The latest month is compound dry and hot: SPI at or below −1 and STI at or above 1.")
    ahead = forecast[(forecast["spi"] <= -1) & (forecast["sti"] >= 1)]
    if len(ahead):
        labels = ", ".join(ahead["date"].dt.strftime("%b %Y"))
        st.warning(f"Outlook months in the dry and hot quadrant: {labels}.")

    charts = point_charts(history, forecast, view["timescale"])
    spi_chart, sti_chart = st.columns(2)
    spi_chart.altair_chart(charts["spi"], width="stretch")
    sti_chart.altair_chart(charts["sti"], width="stretch")
    st.caption("Dashed line: five-month outlook. Filled areas are the history at this point.")
    st.altair_chart(charts["quadrant"], width="stretch")
    st.caption(
        "Dashed lines mark moderate drought (SPI −1), severe drought (SPI −1.5), "
        "heat stress (STI 1), and severe heat (STI 1.5). "
        "Red points are dry and hot. Diamonds are the outlook."
    )

    st.subheader("Five-month outlook")
    st.dataframe(_outlook_table(forecast), width="stretch", hide_index=True, column_config=_COLUMNS)
    with st.expander("Full history"):
        st.dataframe(_history_table(history), width="stretch", hide_index=True, column_config=_COLUMNS)
    st.download_button(
        "Download series CSV",
        _series_csv(history, forecast),
        file_name=f"{_csv_name(meta, view['timescale'])}.csv",
        mime="text/csv",
    )


def _history_table(history):
    frame = history.copy()
    frame["Month"] = frame["date"].dt.strftime("%Y-%m")
    return frame.rename(
        columns={
            "precip_mm": "Rainfall mm",
            "temp_c": "Temperature °C",
            "spi": "SPI",
            "sti": "STI",
            "spi_class": "SPI class",
            "sti_class": "STI class",
        }
    )[["Month", "Rainfall mm", "Temperature °C", "SPI", "STI", "SPI class", "STI class"]]


def _outlook_table(forecast):
    frame = forecast.copy()
    frame["Month"] = frame["date"].dt.strftime("%Y-%m")
    return frame.rename(
        columns={"spi": "SPI", "sti": "STI", "spi_class": "SPI class", "sti_class": "STI class"}
    )[["Month", "SPI", "STI", "SPI class", "STI class"]]


def _series_csv(history, forecast):
    past = history.copy()
    past["kind"] = "Historical"
    ahead = forecast.copy()
    ahead["kind"] = "Outlook"
    ahead["precip_mm"] = pd.NA
    ahead["temp_c"] = pd.NA
    frame = pd.concat([past, ahead], ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m")
    columns = ["date", "kind", "precip_mm", "temp_c", "spi", "sti", "spi_class", "sti_class"]
    return frame[columns].to_csv(index=False).encode()


def _csv_name(meta, timescale):
    stem = clean_filename(meta.get("name") or "point") or "point"
    return f"SPI{timescale}_STI{timescale}_{stem}"[:100]

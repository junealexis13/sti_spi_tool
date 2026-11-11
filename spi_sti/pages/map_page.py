import io
import zipfile
from datetime import datetime, timezone

import streamlit as st

from spi_sti.charts import class_charts
from spi_sti.indices import (
    EXPORT_SCALES,
    MAP_FIRST_YEAR,
    MODES,
    build_export,
    clean_filename,
    coverage_text,
    download_geotiff,
    latest_complete_month,
    month_label,
    prepare_datasets,
    scales_from,
    suggest_filename,
)
from spi_sti.smooth import MIN_CELL_M, smooth_geotiff
from spi_sti.style import legend_png, render_bivariate_png, render_labeled_png
from spi_sti.ui import connect, explain
from spi_sti.zonal import zone_tables


def render():
    st.title("SPI and STI drought map")
    st.write(
        "Upload a municipality boundary and an Earth Engine service account. "
        "The app builds the same 3-month SPI and STI GeoTIFF as the Earth Engine script, "
        "then the municipality tables the QGIS exporter used to write. "
        "Time series for one location are on the Point analysis page."
    )

    gdf = st.session_state.get("gdf")
    name_field = st.session_state.get("name_field")
    has_key = "key" in st.session_state

    with st.sidebar:
        st.header("Window")
        mode = st.selectbox("Analysis mode", MODES, index=0, key="map_mode")
        this_year = datetime.now(timezone.utc).year
        year = st.selectbox("Window end year", list(range(this_year, MAP_FIRST_YEAR - 1, -1)), key="map_year")
        month = st.selectbox(
            "Window end month",
            list(range(1, 13)),
            index=datetime.now(timezone.utc).month - 1,
            format_func=lambda value: month_label(2000, value).split()[0],
            key="map_month",
        )
        st.caption(
            "Observed uses the 3-month window that ends in this month. "
            "Persistence outlook uses the previous window, then damps SPI by 0.65 and STI by 0.70."
        )
        scale_choice = st.selectbox(
            "Export scale",
            EXPORT_SCALES,
            format_func=lambda meters: f"{meters / 1000:g} km",
            key="map_scale",
        )
        with st.expander("Advanced"):
            smooth = st.checkbox("Smooth the raster", key="map_smooth")
            smooth_cell = st.number_input(
                "Smoothed cell size (meters)",
                min_value=MIN_CELL_M,
                max_value=1000,
                value=100,
                step=10,
                disabled=not smooth,
                key="map_cell",
            )
            st.caption(
                "Interpolates SPI and STI onto this grid, then classifies again. "
                f"{MIN_CELL_M} m is the finest. The climate data is still the Earth Engine raster."
            )
        filename_input = st.text_input(
            "Export filename",
            placeholder="Leave blank for an automatic name",
            key="map_filename",
        )
        check = st.button("Check dataset coverage", disabled=not has_key)
        generate = st.button("Generate files", type="primary", disabled=not (has_key and name_field))

    if check:
        try:
            with st.spinner("Asking Earth Engine which months are complete..."):
                connect()
                st.session_state["coverage"] = prepare_datasets()["info"]
        except Exception as exc:
            st.error(explain(exc))

    if generate:
        try:
            with st.status("Building the map...", expanded=True) as status:
                status.write("Signing in to Earth Engine.")
                connect()
                status.write("Checking which months CHIRPS and AgERA5 cover.")
                datasets = prepare_datasets()
                st.session_state["coverage"] = datasets["info"]
                status.write("Computing the 3-month SPI and STI. This often takes a few minutes.")
                image, region, meta = build_export(gdf, mode, year, month, datasets)
                filename = clean_filename(filename_input) or meta["filename"] or suggest_filename(mode, year, month)
                status.write("Downloading the GeoTIFF.")
                tif, used_scale = download_geotiff(image, region, filename, scales_from(scale_choice))
                smoothed = False
                used_cell = None
                if smooth:
                    status.write(f"Smoothing the raster to {int(smooth_cell)} m.")
                    tif, smoothed = smooth_geotiff(tif, smooth_cell, used_scale)
                    used_cell = int(smooth_cell) if smoothed else None
                status.write("Counting classes inside each municipality.")
                tables = zone_tables(tif, gdf, name_field, st.session_state.get("layer") or filename)
                png, scaled = render_bivariate_png(tif)
                status.write("Drawing the boundary and municipality names.")
                labeled = render_labeled_png(tif, gdf, name_field)
                status.update(label="Files are ready", state="complete")
            st.session_state["result"] = {
                "filename": filename,
                "tif": tif,
                "png": png,
                "labeled_png": labeled,
                "scaled": scaled,
                "tables": tables,
                "meta": meta,
                "scale": used_scale,
                "requested_scale": int(scale_choice),
                "smoothed": smoothed,
                "smooth_m": used_cell,
                "mode": mode,
                "name_field": name_field,
            }
        except Exception as exc:
            st.error(explain(exc))

    coverage = st.session_state.get("coverage")
    if coverage:
        st.info(coverage_text(coverage))
        latest = latest_complete_month(coverage)
        st.caption(f"Latest month with both datasets complete: {month_label(*latest)}.")

    result = st.session_state.get("result")
    if result:
        _show_result(result)

    if result is None and not coverage:
        st.caption(
            "Observed is the default. Pick Persistence outlook when you want the one-month-ahead map. "
            "Method notes and the service-account setup are in the README."
        )


def _show_result(result):
    meta = result["meta"]
    if meta["short_baseline"]:
        st.warning(meta["summary"])
    else:
        st.success(meta["summary"])
    if result["scale"] != result["requested_scale"]:
        st.caption(
            f"Earth Engine could not serve the raster at {result['requested_scale'] / 1000:g} km, "
            f"so the download is at {result['scale'] / 1000:g} km."
        )
    elif not result.get("smoothed"):
        st.caption(f"Raster scale: {result['scale'] / 1000:g} km.")
    if result.get("smoothed"):
        st.caption(
            f"Smoothed to {result['smooth_m']} m from the {result['scale'] / 1000:g} km download. "
            "SPI and STI were interpolated, then classified again."
        )

    caption = "Bivariate SPI-3 and STI-3"
    if result["scaled"]:
        caption += ". The picture is reduced to fit the page. The GeoTIFF is full resolution."
    map_col, legend_col = st.columns([2, 1], vertical_alignment="top")
    with map_col:
        st.image(result["png"], caption=caption)
    with legend_col:
        legend = legend_png()
        st.image(legend)
        st.download_button(
            "Download legend",
            legend,
            file_name=f"{result['filename']}_legend.png",
            mime="image/png",
        )

    bundle = _zip_outputs(
        result["filename"],
        result["tif"],
        result["png"],
        result["tables"]["files"],
        result.get("labeled_png"),
    )
    tif_col, png_col, zip_col = st.columns(3)
    tif_col.download_button("Download GeoTIFF", result["tif"], file_name=f"{result['filename']}.tif", mime="image/tiff")
    png_col.download_button("Download PNG", result["png"], file_name=f"{result['filename']}_bivariate.png", mime="image/png")
    zip_col.download_button("Download all files", bundle, file_name=f"{result['filename']}.zip", mime="application/zip")

    labeled_tab, plots, tables_tab = st.tabs(["Labeled map", "Plots", "Tables"])
    with labeled_tab:
        _labeled_panel(result)
    charts = class_charts(result["tables"])
    with plots:
        if charts["empty"]:
            st.write("No municipalities fell in a class.")
        else:
            spi_col, sti_col = st.columns(2)
            spi_col.altair_chart(charts["spi"], width="stretch")
            sti_col.altair_chart(charts["sti"], width="stretch")
            st.altair_chart(charts["bivariate"], width="stretch")
        nodata = charts["nodata"]
        if any(nodata.values()):
            st.caption(
                "Municipalities with missing pixels — "
                f"SPI {nodata['spi']}, STI {nodata['sti']}, bivariate {nodata['bivariate']}."
            )
        st.caption("A municipality is counted in every class that contains at least one of its pixels.")
    with tables_tab:
        _table_panel("Bivariate matrix", "bivariate", result["tables"])
        _table_panel("SPI summary", "spi", result["tables"])
        _table_panel("STI summary", "sti", result["tables"])


def _labeled_panel(result):
    labeled = result.get("labeled_png")
    field = result.get("name_field") or "the selected field"
    if not labeled:
        st.caption("Generate the files again to draw the boundary and municipality names.")
        return
    st.image(
        labeled,
        caption=f"Bivariate SPI-3 and STI-3 with the shapefile boundary. Each small name is the {field} value.",
        width="stretch",
    )
    st.download_button(
        "Download labeled map",
        labeled,
        file_name=f"{result['filename']}_labeled.png",
        mime="image/png",
        key="download-labeled-map",
    )


def _table_panel(title, key, tables):
    st.subheader(title)
    rows = tables[key]
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.write("No municipalities fell in a class.")
    for csv_name, content in tables["files"].items():
        matched = (
            (key == "bivariate" and "Bivariate" in csv_name)
            or (key == "spi" and csv_name.endswith("_SPI_Summary.csv"))
            or (key == "sti" and csv_name.endswith("_STI_Summary.csv"))
        )
        if matched:
            st.download_button("Download CSV", content, file_name=csv_name, mime="text/csv", key=f"dl-{csv_name}")


def _zip_outputs(filename, tif, png, files, labeled=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{filename}.tif", tif)
        archive.writestr(f"{filename}_bivariate.png", png)
        if labeled:
            archive.writestr(f"{filename}_labeled.png", labeled)
        for csv_name, content in files.items():
            archive.writestr(csv_name, content)
    return buf.getvalue()

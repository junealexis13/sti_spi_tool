"""
QGIS Python Script: Multi-Band Stylizer (Bands 3, 4, 5) & Municipality Bivariate Matrix Exporter [v3.2]
Author: UPLB SARAI CeNTRO Regional Hubs (AI Assistant: Gemini Notebook)
Description:
  1. Applies standardized styling to Bands 3, 4, and 5 of the multi-band GeoTIFF:
  2. Extracts Zonal Statistics / Classifications for each municipality:
  3. Exports 3 structured CSV tables matching matrix layout:
"""

import os
import csv
import processing
from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsMapLayer,
    QgsColorRampShader,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsWkbTypes
)
from qgis.utils import iface
from qgis.PyQt.QtGui import QColor

# ==============================================================================
# CONFIGURATION SETTINGS
# ==============================================================================
# Name of your Municipality vector polygon layer in the QGIS Layers panel:
MUNI_LAYER_NAME = "Sulu_mun"  # <--- Update with your polygon layer name

# Field name containing municipality names (e.g., "Mun_Name", "MUNI_NAME", "NAME_2", "ADM3_EN"):
# The script will auto-detect a string name column if this exact name isn't found!
MUNI_FIELD_NAME = "Mun_Name"

# ==============================================================================
# 1. BAND 3 (SPI_Class) & BAND 4 (STI_Class) INTEGER CATEGORY DEFINITIONS
# ==============================================================================
SPI_INTEGER_CLASSES = [
    (1, "#d7191c", "Class 1: Extremely Dry"),
    (2, "#fdae61", "Class 2: Severely Dry"),
    (3, "#ffffbf", "Class 3: Moderately Dry"),
    (4, "#e0f3f8", "Class 4: Near Normal"),
    (5, "#abd9e9", "Class 5: Moderately Wet"),
    (6, "#2c7bb6", "Class 6: Severely Wet"),
    (7, "#0571b0", "Class 7: Extremely Wet"),
]

STI_INTEGER_CLASSES = [
    (1, "#0571b0", "Class 1: Extremely Cold"),
    (2, "#92c5de", "Class 2: Severely Cold"),
    (3, "#d1e5f0", "Class 3: Moderately Cold"),
    (4, "#f7f7f7", "Class 4: Near Normal"),
    (5, "#fddbc7", "Class 5: Moderately Hot"),
    (6, "#f4a582", "Class 6: Severely Hot"),
    (7, "#b2182b", "Class 7: Extremely Hot"),
]

# ==============================================================================
# 2. BAND 5 (BIVARIATE CODE) 49-COLOR GEE PALETTE
# ==============================================================================
# Code Formula: (STI_Class * 10) + SPI_Class
# Tens Digit (x = 1..7) = Temperature STI Class (1=Extremely Cold, 7=Extremely Hot)
# Ones Digit (y = 1..7) = Precipitation SPI Class (1=Extremely Dry, 7=Extremely Wet)
BIVARIATE_PALETTE = {
    "11": "#d5d4d5", "12": "#bdc7d2", "13": "#a5b9cf", "14": "#8cabcd", "15": "#709eca", "16": "#5091c7", "17": "#1a84c3",
    "21": "#dcc0b5", "22": "#c3b3b3", "23": "#aaa7b1", "24": "#8f9bae", "25": "#738fac", "26": "#5284a9", "27": "#2278a6",
    "31": "#e0ab96", "32": "#c6a095", "33": "#ab9693", "34": "#8f8b91", "35": "#72818f", "36": "#52778d", "37": "#246c8a",
    "41": "#e29678", "42": "#c68d77", "43": "#aa8576", "44": "#8d7c74", "45": "#6f7373", "46": "#4f6a71", "47": "#22606f",
    "51": "#e1815b", "52": "#c37a5a", "53": "#a67459", "54": "#896d58", "55": "#6a6557", "56": "#4a5d56", "57": "#1f5555",
    "61": "#dd6a3d", "62": "#bf673d", "63": "#a1633d", "64": "#825e3d", "65": "#64583d", "66": "#43513c", "67": "#184a3b",
    "71": "#d9521e", "72": "#b9531f", "73": "#995220", "74": "#7a5021", "75": "#5b4b22", "76": "#3a4622", "77": "#0f3e22",
}

SPI_LABELS = {
    1: "Extremely Dry", 2: "Severely Dry", 3: "Moderately Dry",
    4: "Near Normal", 5: "Moderately Wet", 6: "Severely Wet", 7: "Extremely Wet"
}

STI_LABELS = {
    1: "Extremely Cold", 2: "Severely Cold", 3: "Moderately Cold",
    4: "Near Normal", 5: "Moderately Hot", 6: "Severely Hot", 7: "Extremely Hot"
}

STI_ORDER = [7, 6, 5, 4, 3, 2, 1]  # Extremely Hot -> Extremely Cold
SPI_ORDER = [1, 2, 3, 4, 5, 6, 7]  # Extremely Dry -> Extremely Wet

RECLASS_TABLE_ZSCORE = [
    -9999.0, -2.0, 1.0,
    -2.0, -1.5, 2.0,
    -1.5, -1.0, 3.0,
    -1.0, 1.0, 4.0,
    1.0, 1.5, 5.0,
    1.5, 2.0, 6.0,
    2.0, 9999.0, 7.0
]
# ==============================================================================
# 3. UNIQUE NAMING & HELPER FUNCTIONS
# ==============================================================================
def get_unique_layer_name(base_name):
    """Generates a unique layer name in QGIS, appending (2), (3), etc. ONLY if currently in QGIS Layers panel."""
    project = QgsProject.instance()
    if not project.mapLayersByName(base_name):
        return base_name
    
    counter = 2
    while project.mapLayersByName(f"{base_name} ({counter})"):
        counter += 1
    return f"{base_name} ({counter})"


def get_unique_csv_path_and_name(folder, base_name):
    """
    Generates a unique QGIS layer name and CSV file path based STRICTLY on 
    layers existing in the active QGIS panel.
    """
    project = QgsProject.instance()
    if not project.mapLayersByName(base_name):
        current_name = base_name
    else:
        counter = 2
        current_name = f"{base_name} ({counter})"
        while project.mapLayersByName(current_name):
            counter += 1
            current_name = f"{base_name} ({counter})"
            
    file_path = os.path.join(folder, f"{current_name}.csv")
    return file_path, current_name


def safe_write_csv(file_path, headers, rows):
    """Writes to CSV cleanly. If file is open/locked in another program (e.g. Excel), appends _new."""
    try:
        with open(file_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        return file_path
    except PermissionError:
        base, ext = os.path.splitext(file_path)
        alt_path = f"{base}_new{ext}"
        print(f"Warning: File '{file_path}' is open in another application. Saving to '{alt_path}' instead.")
        with open(alt_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        return alt_path


def find_name_field(layer):
    fields = layer.fields()
    if MUNI_FIELD_NAME in [f.name() for f in fields]:
        idx = fields.indexOf(MUNI_FIELD_NAME)
        if not fields.at(idx).isNumeric():
            return MUNI_FIELD_NAME

    keywords = ["MUN_NAME", "MUNI_NAME", "NAME_2", "ADM3_EN", "NAME", "MUNICIPALI", "TOWN"]
    for kw in keywords:
        for f in fields:
            if kw in f.name().upper() and not f.isNumeric():
                return f.name()

    for f in fields:
        if not f.isNumeric():
            return f.name()

    return fields[0].name()


def is_band_integer_classified(raster_layer, band_number):
    provider = raster_layer.dataProvider()
    stats = provider.bandStatistics(band_number)
    return stats.minimumValue >= 0 and stats.maximumValue <= 10


def reclassify_band_to_integer(raster_layer, band_number):
    try:
        res = processing.run("native:reclassifybytable", {
            'INPUT_RASTER': raster_layer,
            'RASTER_BAND': band_number,
            'TABLE': RECLASS_TABLE_ZSCORE,
            'NO_DATA': -9999.0,
            'RANGE_BOUNDARIES': 0,
            'NODATA_FOR_MISSING': True,
            'DATA_TYPE': 1,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        })
        if res and 'OUTPUT' in res and res['OUTPUT']:
            return res['OUTPUT']
    except Exception as e:
        print(f"Notice: reclassifybytable failed for Band {band_number} ({e}), trying fallback...")

    try:
        layer_name = raster_layer.name()
        expr = (
            f'("{layer_name}@{band_number}" <= -2.0) * 1 + '
            f'("{layer_name}@{band_number}" > -2.0 AND "{layer_name}@{band_number}" <= -1.5) * 2 + '
            f'("{layer_name}@{band_number}" > -1.5 AND "{layer_name}@{band_number}" <= -1.0) * 3 + '
            f'("{layer_name}@{band_number}" > -1.0 AND "{layer_name}@{band_number}" <= 1.0) * 4 + '
            f'("{layer_name}@{band_number}" > 1.0 AND "{layer_name}@{band_number}" <= 1.5) * 5 + '
            f'("{layer_name}@{band_number}" > 1.5 AND "{layer_name}@{band_number}" <= 2.0) * 6 + '
            f'("{layer_name}@{band_number}" > 2.0) * 7'
        )
        res_calc = processing.run("qgis:rastercalculator", {
            'EXPRESSION': expr,
            'LAYERS': [raster_layer],
            'OUTPUT': 'TEMPORARY_OUTPUT'
        })
        return res_calc['OUTPUT']
    except Exception as e2:
        print(f"ERROR: Reclassification failed for Band {band_number}: {e2}")
        return None

# ==============================================================================
# 4. RENDERER BUILDER FUNCTIONS
# ==============================================================================
def create_integer_class_renderer(raster_layer, band_number, class_items):
    shader = QgsRasterShader()
    color_ramp = QgsColorRampShader()
    color_ramp.setColorRampType(QgsColorRampShader.Exact)

    items = [QgsColorRampShader.ColorRampItem(float(val), QColor(hex_color), label) for val, hex_color, label in class_items]
    color_ramp.setColorRampItemList(items)
    shader.setRasterShaderFunction(color_ramp)
    return QgsSingleBandPseudoColorRenderer(raster_layer.dataProvider(), band_number, shader)


def create_bivariate_code_renderer(raster_layer, band_number=5):
    shader = QgsRasterShader()
    color_ramp = QgsColorRampShader()
    color_ramp.setColorRampType(QgsColorRampShader.Exact)

    items = []
    for x in range(1, 8):
        for y in range(1, 8):
            code_str = f"{x}{y}"
            code_val = x * 10 + y
            hex_color = BIVARIATE_PALETTE.get(code_str, "#ffffff")
            label = f"Code {code_val}: STI {x} ({STI_LABELS[x]}) & SPI {y} ({SPI_LABELS[y]})"
            items.append(QgsColorRampShader.ColorRampItem(float(code_val), QColor(hex_color), label))

    color_ramp.setColorRampItemList(items)
    shader.setRasterShaderFunction(color_ramp)
    return QgsSingleBandPseudoColorRenderer(raster_layer.dataProvider(), band_number, shader)

# ==============================================================================
# 5. RASTER STYLING WORKFLOW (BANDS 3, 4, 5 ONLY)
# ==============================================================================
def style_raster_bands(active_layer):
    project = QgsProject.instance()
    band_count = active_layer.bandCount()
    layer_name_base = active_layer.name()

    print(f"--- STYLING RASTER BANDS ({band_count} Bands Loaded) ---")

    # Style Band 3 (SPI_Class Categorized 1-7)
    if band_count >= 3:
        spi3_layer = active_layer.clone()
        unique_name = get_unique_layer_name(f"{layer_name_base} - Band 3 (SPI_Class)")
        spi3_layer.setName(unique_name)
        spi3_renderer = create_integer_class_renderer(spi3_layer, 3, SPI_INTEGER_CLASSES)
        spi3_layer.setRenderer(spi3_renderer)
        project.addMapLayer(spi3_layer)
        spi3_layer.triggerRepaint()
        iface.layerTreeView().refreshLayerSymbology(spi3_layer.id())
        print(f"  ✓ Added styled Band 3 -> '{unique_name}'")

    # Style Band 4 (STI_Class Categorized 1-7)
    if band_count >= 4:
        sti4_layer = active_layer.clone()
        unique_name = get_unique_layer_name(f"{layer_name_base} - Band 4 (STI_Class)")
        sti4_layer.setName(unique_name)
        sti4_renderer = create_integer_class_renderer(sti4_layer, 4, STI_INTEGER_CLASSES)
        sti4_layer.setRenderer(sti4_renderer)
        project.addMapLayer(sti4_layer)
        sti4_layer.triggerRepaint()
        iface.layerTreeView().refreshLayerSymbology(sti4_layer.id())
        print(f"  ✓ Added styled Band 4 -> '{unique_name}'")

    # Style Band 5 (Bivariate_Code Integer 11-77)
    if band_count >= 5:
        biv_layer = active_layer.clone()
        unique_name = get_unique_layer_name(f"{layer_name_base} - Band 5 (Bivariate SPI/STI Matrix)")
        biv_layer.setName(unique_name)
        biv_renderer = create_bivariate_code_renderer(biv_layer, 5)
        biv_layer.setRenderer(biv_renderer)
        project.addMapLayer(biv_layer)
        biv_layer.triggerRepaint()
        iface.layerTreeView().refreshLayerSymbology(biv_layer.id())
        print(f"  ✓ Added styled Band 5 -> '{unique_name}'")

# ==============================================================================
# 6. MATRIX EXTRACTION & EXPORT WORKFLOW
# ==============================================================================
def extract_and_import_summary(raster_layer):
    project = QgsProject.instance()

    muni_layers = project.mapLayersByName(MUNI_LAYER_NAME)
    if not muni_layers:
        raise ValueError(
            f"ERROR: Vector layer '{MUNI_LAYER_NAME}' was not found in the QGIS Layers panel. "
            f"Please ensure '{MUNI_LAYER_NAME}' is loaded into QGIS or update MUNI_LAYER_NAME in the configuration settings."
        )

    muni_layer = muni_layers[0]
    muni_layer_title = muni_layer.name()
    name_field = find_name_field(muni_layer)

    print("--- EXTRACTING MUNICIPALITY CLASSIFICATIONS ---")
    print(f"Using vector layer: '{muni_layer_title}'")
    print(f"Using Name Field: '{name_field}'")

    # A. EXTRACT SPI SUMMARY FROM BAND 3
    print("Extracting SPI Summary directly from Band 3...")
    spi_map = {k: set() for k in SPI_LABELS}
    spi_nodata_munis = set()

    if is_band_integer_classified(raster_layer, 3):
        spi_target_layer = raster_layer
        spi_target_band = 3
    else:
        spi_target_layer = reclassify_band_to_integer(raster_layer, 3)
        spi_target_band = 1

    if spi_target_layer:
        res_spi = processing.run("native:zonalhistogram", {
            'INPUT_RASTER': spi_target_layer,
            'RASTER_BAND': spi_target_band,
            'INPUT_VECTOR': muni_layer,
            'COLUMN_PREFIX': 'CODE_',
            'OUTPUT': 'memory:'
        })['OUTPUT']

        code_fields_spi = [f.name() for f in res_spi.fields() if f.name().startswith('CODE_')]
        for feat in res_spi.getFeatures():
            muni_name = str(feat[name_field]).strip() if feat[name_field] is not None else ""
            if not muni_name: continue

            for col in code_fields_spi:
                pixel_count = feat[col]
                if pixel_count and pixel_count > 0:
                    code_str = col.replace('CODE_', '')
                    if code_str.isdigit():
                        cls = int(code_str)
                        if cls in spi_map:
                            spi_map[cls].add(muni_name)
                    else:
                        spi_nodata_munis.add(muni_name)

    # B. EXTRACT STI SUMMARY FROM BAND 4
    print("Extracting STI Summary directly from Band 4...")
    sti_map = {k: set() for k in STI_LABELS}
    sti_nodata_munis = set()

    if is_band_integer_classified(raster_layer, 4):
        sti_target_layer = raster_layer
        sti_target_band = 4
    else:
        sti_target_layer = reclassify_band_to_integer(raster_layer, 4)
        sti_target_band = 1

    if sti_target_layer:
        res_sti = processing.run("native:zonalhistogram", {
            'INPUT_RASTER': sti_target_layer,
            'RASTER_BAND': sti_target_band,
            'INPUT_VECTOR': muni_layer,
            'COLUMN_PREFIX': 'CODE_',
            'OUTPUT': 'memory:'
        })['OUTPUT']

        code_fields_sti = [f.name() for f in res_sti.fields() if f.name().startswith('CODE_')]
        for feat in res_sti.getFeatures():
            muni_name = str(feat[name_field]).strip() if feat[name_field] is not None else ""
            if not muni_name: continue

            for col in code_fields_sti:
                pixel_count = feat[col]
                if pixel_count and pixel_count > 0:
                    code_str = col.replace('CODE_', '')
                    if code_str.isdigit():
                        cls = int(code_str)
                        if cls in sti_map:
                            sti_map[cls].add(muni_name)
                    else:
                        sti_nodata_munis.add(muni_name)

    # C. EXTRACT BIVARIATE MATRIX FROM BAND 5
    print("Extracting Bivariate Matrix directly from Band 5...")
    biv_map = {(spi_k, sti_k): set() for spi_k in SPI_LABELS for sti_k in STI_LABELS}
    biv_nodata_munis = set()

    res_biv = processing.run("native:zonalhistogram", {
        'INPUT_RASTER': raster_layer,
        'RASTER_BAND': 5,
        'INPUT_VECTOR': muni_layer,
        'COLUMN_PREFIX': 'CODE_',
        'OUTPUT': 'memory:'
    })['OUTPUT']

    code_fields_biv = [f.name() for f in res_biv.fields() if f.name().startswith('CODE_')]
    for feat in res_biv.getFeatures():
        muni_name = str(feat[name_field]).strip() if feat[name_field] is not None else ""
        if not muni_name: continue

        for col in code_fields_biv:
            pixel_count = feat[col]
            if pixel_count and pixel_count > 0:
                code_str = col.replace('CODE_', '')
                if code_str.isdigit():
                    code_val = int(code_str)
                    sti_cls = code_val // 10
                    spi_cls = code_val % 10
                    if (spi_cls, sti_cls) in biv_map:
                        biv_map[(spi_cls, sti_cls)].add(muni_name)
                else:
                    biv_nodata_munis.add(muni_name)

    proj_folder = project.homePath()
    if not proj_folder or not os.path.exists(proj_folder):
        proj_folder = os.path.expanduser("~")

    # GENERATE UNIQUE PATHS AND NAMES BASED STRICTLY ON QGIS LAYERS PANEL
    biv_base = f"{muni_layer_title}_SPI_STI_Bivariate_Matrix"
    spi_base = f"{muni_layer_title}_SPI_Summary"
    sti_base = f"{muni_layer_title}_STI_Summary"

    csv_biv_path, biv_layer_name = get_unique_csv_path_and_name(proj_folder, biv_base)
    csv_spi_path, spi_layer_name = get_unique_csv_path_and_name(proj_folder, spi_base)
    csv_sti_path, sti_layer_name = get_unique_csv_path_and_name(proj_folder, sti_base)

    # 1. SAVE BIVARIATE MATRIX CSV
    biv_headers = ["SPI / STI"] + [STI_LABELS[k] for k in STI_ORDER] + ["Missing Pixels (NoData)"]
    biv_nodata_str = ", ".join(sorted(list(biv_nodata_munis))) if biv_nodata_munis else "-"

    biv_rows = []
    for idx, spi_k in enumerate(SPI_ORDER):
        row = {"SPI / STI": SPI_LABELS[spi_k]}
        for sti_k in STI_ORDER:
            munis = sorted(list(biv_map[(spi_k, sti_k)]))
            row[STI_LABELS[sti_k]] = ", ".join(munis) if munis else "-"
        row["Missing Pixels (NoData)"] = biv_nodata_str if idx == 0 else "-"
        biv_rows.append(row)

    out_biv_path = safe_write_csv(csv_biv_path, biv_headers, biv_rows)

    # 2. SAVE SPI SUMMARY CSV
    spi_rows = []
    for spi_k in SPI_ORDER:
        munis = sorted(list(spi_map[spi_k]))
        if munis:
            spi_rows.append({"SPI_Class": SPI_LABELS[spi_k], "Municipalities": ", ".join(munis)})
    if spi_nodata_munis:
        spi_rows.append({"SPI_Class": "Missing Pixels (NoData)", "Municipalities": ", ".join(sorted(list(spi_nodata_munis)))})

    out_spi_path = safe_write_csv(csv_spi_path, ["SPI_Class", "Municipalities"], spi_rows)

    # 3. SAVE STI SUMMARY CSV
    sti_rows = []
    for sti_k in STI_ORDER:
        munis = sorted(list(sti_map[sti_k]))
        if munis:
            sti_rows.append({"STI_Class": STI_LABELS[sti_k], "Municipalities": ", ".join(munis)})
    if sti_nodata_munis:
        sti_rows.append({"STI_Class": "Missing Pixels (NoData)", "Municipalities": ", ".join(sorted(list(sti_nodata_munis)))})

    out_sti_path = safe_write_csv(csv_sti_path, ["STI_Class", "Municipalities"], sti_rows)

    print("  ✓ Saved 3 matrix CSV tables to project folder.")

    tables_to_load = [
        (out_biv_path, biv_layer_name),
        (out_spi_path, spi_layer_name),
        (out_sti_path, sti_layer_name)
    ]

    for csv_path, layer_name in tables_to_load:
        tbl_layer = QgsVectorLayer(csv_path, layer_name, "ogr")
        if tbl_layer.isValid():
            project.addMapLayer(tbl_layer)
            print(f"  ✓ Automatically imported table layer: '{layer_name}'")


def main():
    active_layer = iface.activeLayer()

    if not active_layer:
        print("ERROR: No layer selected. Please select the multi-band GeoTIFF in the Layers panel.")
        return

    if active_layer.type() != active_layer.RasterLayer:
        print("ERROR: Selected layer is not a Raster Layer.")
        return

    style_raster_bands(active_layer)
    extract_and_import_summary(active_layer)

    print("SUCCESS: All tasks completed successfully!")

main()
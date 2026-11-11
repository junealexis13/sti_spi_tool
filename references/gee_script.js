// ================================================================================
// Smarter Approaches to Reinvigorate Agriculture as an Industry in the Philippines
// Community Level - SARAi Enhanced Agricultural Monitoring System
// SPI & STI-based Drought Monitoring Tool
// ================================================================================

//  - Rainfall data is a merged pentad series built from two collections:
//      CHIRPS final        (UCSB-CHG/CHIRPS/PENTAD)                       -> historical baseline
//      CHIRPS preliminary  (projects/climate-engine-pro/assets/ce-chirps-prelim-pentad)
//                                                                          -> months after the final series ends
//  - Temperature data is from AgERA5 daily 2 m mean air temp (ce-ag-era5-v2/daily)

// ==============================================================================
// SCRIPT SECTION 1. CONFIGURATION, STYLING & GLOBAL DATASETS
// ==============================================================================
var app = {};



// Rainfall dataset
var CHIRPS_FINAL_ID  = ee.ImageCollection("UCSB-CHC/CHIRPS/V3/PENTAD");
var CHIRPS_PRELIM_ID = 'projects/climate-engine-pro/assets/ce-chirps-prelim-pentad';
var CHIRPS_FINAL  = ee.ImageCollection(CHIRPS_FINAL_ID).select('precipitation');
var CHIRPS_PRELIM = ee.ImageCollection(CHIRPS_PRELIM_ID).select('precipitation');
var CHIRPS = null;
// Largest gap (ms) allowed between the last final pentad and the first preliminary pentad for the
// two to count as one continuous series (pentads are 3-6 days long, so 7 days is generous).
var CHIRPS_SPLICE_TOLERANCE_MS = 7 * 86400000;

// Temperature dataset
var ERA5   = ee.ImageCollection('projects/climate-engine-pro/assets/ce-ag-era5-v2/daily').select('Temperature_Air_2m_Mean_24h');
var KELVIN_TO_C = 273.15;

// Climatology (baseline) period for the BIVARIATE MAP
// For reference, the WMO standard normal period is 1991-2020; a longer period gives more stable statistics.
var BASELINE_START_YEAR = 1990;
var BASELINE_END_YEAR   = 2025;
// First year of the monthly series used by the POINT DASHBOARD.
// The point dashboard uses the whole series from this year to the latest complete month as its baseline. 
// It is automatically moved forward if either dataset starts later.
var POINT_START_YEAR    = 1990;
// Fewest baseline years allowed for the map (Below MIN the map is not drawn; below WARN a warning is shown)
var MIN_BASELINE_YEARS  = 10;
var WARN_BASELINE_YEARS = 20;
// Earliest year offered in the map's year picker.
var MAP_FIRST_YEAR      = 1992;
// Point sampling scale (m): 
var POINT_SAMPLE_SCALE  = 5000;

// AR(1) lag-1 persistence multipliers for the 1-month-ahead BIVARIATE RASTER outlook (Outlook Index = PHI x latest index).
// Rationale: two 3-month windows that start one month apart share 2 of their 3 months, so the expected
// lag-1 autocorrelation of a 3-month rainfall index is about 2/3, hence 0.65 for SPI.
var PHI_SPI = 0.65;
// Temperature anomalies are generally more persistent from month to month than rainfall anomalies,
// so STI uses a slightly higher value than SPI.
var PHI_STI = 0.70;

// Download scales tried in order (if a request is too large / times out)
var EXPORT_SCALES = [1000, 2000, 5000];
var MODE_HINDCAST = 'Observed';
var MODE_FORECAST = 'Persistence Outlook';

// UI Color Palette
app.colors = {
  primary: '#40AD4A', secondary: '#5DC366', accent1: '#FFC845', accent2: '#6CC7D9',
  warning: '#BD924D', dark: '#12120F', shade: '#9e9e9e', bg: '#FFFFF7', alert: '#C0392B'
};

app.SECTION_STYLE   = {margin: '20px 0 0 0', backgroundColor: app.colors.bg};
app.HEADING_STYLE   = {fontSize: '14px', fontWeight: 'bold', backgroundColor: app.colors.bg, color: app.colors.dark, margin: '15px 15px 10px 15px'};
app.HELP_TEXT_STYLE = {margin: '8px 15px -4px 15px', fontSize: '11px', color: app.colors.shade, backgroundColor: app.colors.bg};
app.WIDGET_STYLE    = {stretch: 'horizontal', margin: '8px 15px'};

// ==============================================================================
// SCRIPT SECTION 2. BIVARIATE 7x7 COLOR MATRIX (CODES 11 TO 77)
// ==============================================================================

var bivariate_palette = {
  "11": "#d5d4d5", "12": "#bdc7d2", "13": "#a5b9cf", "14": "#8cabcd", "15": "#709eca", "16": "#5091c7", "17": "#1a84c3",
  "21": "#dcc0b5", "22": "#c3b3b3", "23": "#aaa7b1", "24": "#8f9bae", "25": "#738fac", "26": "#5284a9", "27": "#2278a6",
  "31": "#e0ab96", "32": "#c6a095", "33": "#ab9693", "34": "#8f8b91", "35": "#72818f", "36": "#52778d", "37": "#246c8a",
  "41": "#e29678", "42": "#c68d77", "43": "#aa8576", "44": "#8d7c74", "45": "#6f7373", "46": "#4f6a71", "47": "#22606f",
  "51": "#e1815b", "52": "#c37a5a", "53": "#a67459", "54": "#896d58", "55": "#6a6557", "56": "#4a5d56", "57": "#1f5555",
  "61": "#dd6a3d", "62": "#bf673d", "63": "#a1633d", "64": "#825e3d", "65": "#64583d", "66": "#43513c", "67": "#184a3b",
  "71": "#d9521e", "72": "#b9531f", "73": "#995220", "74": "#7a5021", "75": "#5b4b22", "76": "#3a4622", "77": "#0f3e22"
};

var classKeys = [];
var paletteList = [];
for (var bi_i = 1; bi_i <= 7; bi_i++) {
  for (var bi_j = 1; bi_j <= 7; bi_j++) {
    classKeys.push(bi_i * 10 + bi_j);
    paletteList.push(bivariate_palette[bi_i.toString() + bi_j.toString()]);
  }
}
var eeClassKeys    = ee.List(classKeys);
var eeClassIndices = ee.List.sequence(0, 48);

// ==============================================================================
// SCRIPT SECTION 3. CLIENT-SIDE DATE HELPERS & DATASET AVAILABILITY
// ==============================================================================

var MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
var DAY_MS = 86400000;

function ym(y, m) { var d = new Date(Date.UTC(y, m - 1, 1)); return {y: d.getUTCFullYear(), m: d.getUTCMonth() + 1}; }
function addMonths(a, n) { return ym(a.y, a.m + n); }
function ymToMillis(a) { return Date.UTC(a.y, a.m - 1, 1); }
function ymFromMillis(ms) { var d = new Date(ms); return {y: d.getUTCFullYear(), m: d.getUTCMonth() + 1}; }
function minYm(a, b) { return ymToMillis(a) <= ymToMillis(b) ? a : b; }
function maxYm(a, b) { return ymToMillis(a) >= ymToMillis(b) ? a : b; }
function pad2(n) { return n < 10 ? '0' + n : '' + n; }
function monthLabel(a) { return MONTH_NAMES[a.m - 1] + ' ' + a.y; }
function ymCompact(a) { return a.y + '-' + pad2(a.m); }
function isoMonth(ms) { var d = new Date(ms); return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-01'; }
function eeDate(a) { return ee.Date.fromYMD(a.y, a.m, 1); }
function monthsBetween(a, b) { return (b.y - a.y) * 12 + (b.m - a.m); }

// A window ending (exclusively) at windowEnd is complete if the data run at least that far.
function isAvailable(dataEnd, windowEnd) { return ymToMillis(windowEnd) <= ymToMillis(dataEnd); }
// A window starting at windowStart is covered if the data begin on or before it.
function isCovered(dataStart, windowStart) { return ymToMillis(windowStart) >= ymToMillis(dataStart); }

// "Start" = first month that is fully covered by the data.
function firstFullMonth(ms) {
  if (ms === null || ms === undefined) return {y: 9999, m: 1};
  var d = new Date(ms), cur = ymFromMillis(ms);
  return (d.getUTCDate() === 1) ? cur : addMonths(cur, 1);
}

// "Exclusive end" = first month that is NOT complete.
function dailyEnd(ms) {
  if (ms === null || ms === undefined) return {y: 1980, m: 1};
  var d = new Date(ms), nxt = new Date(ms + DAY_MS), cur = ymFromMillis(ms);
  return (nxt.getUTCMonth() !== d.getUTCMonth()) ? addMonths(cur, 1) : cur;
}
function pentadEnd(ms) {
  if (ms === null || ms === undefined) return {y: 1980, m: 1};
  var d = new Date(ms), cur = ymFromMillis(ms);
  return (d.getUTCDate() >= 26) ? addMonths(cur, 1) : cur; // 6th pentad starts on day 26
}

function firstImageMillis(ic) { return ic.aggregate_min('system:time_start'); }
function lastImageMillis(ic) {
  var now = ee.Date(Date.now());
  var recent = ic.filterDate(now.advance(-3, 'year'), now.advance(1, 'day'));
  return ee.Algorithms.If(recent.size().gt(0),
    recent.aggregate_max('system:time_start'),
    ic.aggregate_max('system:time_start'));
}

var dataInfo = null, dataInfoLoading = false, dataInfoCallbacks = [];
function withDataInfo(callback) {
  if (dataInfo) { callback(dataInfo); return; }
  dataInfoCallbacks.push(callback);
  if (dataInfoLoading) return;
  dataInfoLoading = true;
  ee.Dictionary({
    finalFirst:  firstImageMillis(CHIRPS_FINAL),  finalLast:  lastImageMillis(CHIRPS_FINAL),
    prelimFirst: firstImageMillis(CHIRPS_PRELIM), prelimLast: lastImageMillis(CHIRPS_PRELIM),
    eraFirst:    firstImageMillis(ERA5),          eraLast:    lastImageMillis(ERA5)
  }).evaluate(function(res, err) {
    dataInfoLoading = false;
    var cbs = dataInfoCallbacks; dataInfoCallbacks = [];
    if (err || !res) { cbs.forEach(function(cb) { cb(null, err || 'Unknown error'); }); return; }

    // ---- Splice CHIRPS final (history) + preliminary (recent months)
    var has = function(v) { return v !== null && v !== undefined; };
    var hasFinal  = has(res.finalLast);
    var hasPrelim = has(res.prelimLast);
    // Preliminary is usable only if it connects to the end of the final series (or if there is no final series).
    var contiguous = hasFinal && hasPrelim && res.prelimFirst <= res.finalLast + CHIRPS_SPLICE_TOLERANCE_MS;
    var usePrelim  = hasPrelim && (!hasFinal || contiguous);
    var prelimGap  = hasFinal && hasPrelim && !contiguous;

    var merged = hasFinal ? CHIRPS_FINAL : null;
    if (usePrelim) {
      // Preliminary pentads only AFTER the last final pentad, so no pentad is counted twice.
      var pre = hasFinal ? CHIRPS_PRELIM.filter(ee.Filter.gt('system:time_start', res.finalLast)) : CHIRPS_PRELIM;
      merged = merged ? merged.merge(pre) : pre;
    }
    CHIRPS = merged || CHIRPS_FINAL;

    var chirpsFirstMs = hasFinal ? res.finalFirst : res.prelimFirst;
    var chirpsLastMs  = usePrelim ? (hasFinal ? Math.max(res.finalLast, res.prelimLast) : res.prelimLast) : res.finalLast;

    dataInfo = {
      chirpsStart: firstFullMonth(chirpsFirstMs),
      chirpsEnd:   pentadEnd(chirpsLastMs),
      finalEnd:    hasFinal ? pentadEnd(res.finalLast) : {y: 1980, m: 1},
      useFinal:    hasFinal,
      usePrelim:   usePrelim,
      prelimGap:   prelimGap,
      prelimStart: hasPrelim ? firstFullMonth(res.prelimFirst) : null,
      eraStart:    firstFullMonth(res.eraFirst),
      eraEnd:      dailyEnd(res.eraLast)
    };
    cbs.forEach(function(cb) { cb(dataInfo); });
  });
}

// ==============================================================================
// SCRIPT SECTION 4. IMAGE PROCESSING & MATH HELPERS
// ==============================================================================

function classifyIndex(img) {
  return ee.Image(4)
    .where(img.lte(-2.0), 1)
    .where(img.gt(-2.0).and(img.lte(-1.5)), 2)
    .where(img.gt(-1.5).and(img.lte(-1.0)), 3)
    .where(img.gte(1.0).and(img.lt(1.5)), 5)
    .where(img.gte(1.5).and(img.lt(2.0)), 6)
    .where(img.gte(2.0), 7)
    .updateMask(img.mask()); // no-data pixels stay no-data instead of showing as "normal"
}

function rollingAggregate(arr, window, type) {
  var result = [];
  for (var i = 0; i < arr.length; i++) {
    if (i < window - 1) { result.push(null); continue; }
    var subset = arr.slice(i - window + 1, i + 1).filter(function(v) { return v !== null; });
    if (subset.length < window) { result.push(null); continue; }
    var s = subset.reduce(function(a, b) { return a + b; }, 0);
    result.push(type === 'sum' ? s : s / subset.length);
  }
  return result;
}

function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }

function norminv(p) {
  if (p <= 0 || p >= 1) return null;
  var a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00];
  var b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01];
  var c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00];
  var d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00];
  var plow = 0.02425, phigh = 1 - plow, q, r;
  if (p < plow) { q = Math.sqrt(-2 * Math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
  if (p > phigh) { q = Math.sqrt(-2 * Math.log(1 - p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
  q = p - 0.5; r = q*q; return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5]) * q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1);
}

function logGamma(z) {
  var coeff = [76.18009172947146, -86.50532032941677, 24.01409824083091, -1.231739572450155, 0.001208650973866179, -0.000005395239384953];
  var x = z, y = z, tmp = x + 5.5; tmp -= (x + 0.5) * Math.log(tmp);
  var ser = 1.000000000190015;
  for (var j = 0; j < coeff.length; j++) { y += 1; ser += coeff[j] / y; }
  return -tmp + Math.log(2.5066282746310005 * ser / x);
}

function gammaCDF(x, shape, scale) {
  if (!(x > 0) || !(shape > 0) || !(scale > 0)) return null;
  var a = shape, b = x / scale;
  function gser(a, b) { var sum = 1 / a, ap = a, del = sum; for (var n=1; n<=100; n++){ ap += 1; del *= b / ap; sum += del; if (Math.abs(del) < Math.abs(sum) * 3e-7) break; } return sum * Math.exp(-b + a * Math.log(b) - logGamma(a)); }
  function gcf(a, b) { var gold = 0, a0 = 1, a1 = b, b0 = 0, b1 = 1, fac = 1 / a1, g = b1 * fac; for (var n=1; n<=100; n++){ var an = n; a0 = (a1 + a0 * (an - a)) * fac; b0 = (b1 + b0 * (an - a)) * fac; var anf = an * fac; a1 = b * a0 + anf * a1; b1 = b * b0 + anf * b1; if (a1 !== 0){ fac = 1 / a1; g = b1 * fac; } if (Math.abs((g - gold) / g) < 3e-7) break; gold = g; } return 1 - Math.exp(-b + a * Math.log(b) - logGamma(a)) * g; }
  return (b < a + 1) ? gser(a, b) : gcf(a, b);
}

// Gamma parameters by maximum likelihood, as in the WMO SPI User Guide (WMO-No. 1090, 2012).
// The guide gives the closed-form ML estimators of Thom (1958), fitted to the NON-ZERO values only:
//   A     = ln(mean(x)) - mean(ln(x))
//   alpha = (1 + sqrt(1 + 4A/3)) / (4A)      (shape)
//   beta  = mean(x) / alpha                  (scale)
// Zeros are handled separately through the probability of zero q (see buildPointResults).
// A > 0 for any sample with at least two distinct values (Jensen's inequality); A ~ 0 means all values are equal.
var GAMMA_MIN_NONZERO = 3;   // fewest non-zero totals needed to fit a gamma; otherwise the empirical CDF is used
var GAMMA_MIN_A = 1e-6;      // A below this means (numerically) identical values: no usable fit
function gammaMLE(nonzero) {
  var n = nonzero.length;
  if (n < GAMMA_MIN_NONZERO) return null;
  var sum = 0, sumLn = 0;
  for (var i = 0; i < n; i++) { sum += nonzero[i]; sumLn += Math.log(nonzero[i]); }
  var mean = sum / n;
  var A = Math.log(mean) - sumLn / n;
  if (!(A > GAMMA_MIN_A) || !isFinite(A)) return null;
  var shape = (1 + Math.sqrt(1 + 4 * A / 3)) / (4 * A);
  var scale = mean / shape;
  if (!(shape > 0) || !(scale > 0) || !isFinite(shape) || !isFinite(scale)) return null;
  return {shape: shape, scale: scale};
}

function empiricalCDF(x, arrSorted) {
  var N = arrSorted.length; if (N === 0) return 0.5;
  var idx = 0; while (idx < N && arrSorted[idx] <= x) idx++;
  return idx / (N + 1);
}

function sanitizeFilename(s) {
  return (s || '').trim()
    .replace(/\.(tif|tiff|zip)$/i, '')
    .replace(/[^A-Za-z0-9_\-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 100);
}

function setStatus(label, text, colorKey) {
  label.setValue(text);
  label.style().set('color', app.colors[colorKey] || colorKey);
}

// ==============================================================================
// SCRIPT SECTION 5. APPLICATION STATE & UI INITIALIZATION
// ==============================================================================

var state = {
  boundaryFC: null,
  boundaryMask: null, 
  boundaryRegion: null,
  locationMap: {},
  currentPoint: null,
  currentPointName: null,
  cachedCharts: {},
  isComputing: false,
  pendingMode: null,
  requestId: 0,
  exportImage: null,
  exportDefaultName: null
};

ui.root.clear();
var map = ui.Map();

state.bivariateLayer = ui.Map.Layer(ee.Image(0).selfMask(), {}, 'Bivariate SPI3-STI3 Map', false);
state.boundaryLayer  = ui.Map.Layer(ee.Image(0).selfMask(), {}, 'Boundary', false);
state.pointLayer     = ui.Map.Layer(ee.Geometry.Point([0, 0]), {color: app.colors.alert}, 'Selected Point', false);
map.layers().add(state.bivariateLayer);
map.layers().add(state.boundaryLayer);
map.layers().add(state.pointLayer);


// UI Section - TITLE PANEL
var dataStatusLabel = ui.Label('Checking dataset availability...', {
  fontSize: '10px', color: app.colors.shade, margin: '5px 15px 5px 15px', backgroundColor: app.colors.bg
});

var introPanel = ui.Panel([
  ui.Label({
    value: 'Community Level - SARAI Enhanced \nAgricultural Monitoring System',
    style: {fontWeight: 'bold', fontSize: '18px', color: app.colors.primary, margin: '15px 15px 10px 15px', whiteSpace: 'pre', backgroundColor: app.colors.bg}
  }),
  ui.Label({
    value: 'SPI & STI-based Drought Risk \nMapping and Analysis',
    style: {fontWeight: 'bold', fontSize: '20px', margin: '10px 15px', whiteSpace: 'pre', color: app.colors.dark, backgroundColor: app.colors.bg}
  }),
  ui.Label({
    value: 'An Earth Engine-based tool that allows users to investigate drought effects through localized SPI and STI time series graphs and bivariate risk maps.',
    style: {fontSize: '12px', backgroundColor: app.colors.bg, color: app.colors.dark, margin: '10px 15px'}
  }),
  ui.Label({
    value: 'Data: CHIRPS Pentad Final and Preliminary for rainfall · AgERA5 Daily for temperature.',
    style: {fontSize: '11px', backgroundColor: app.colors.bg, color: app.colors.dark, margin: '0 15px 5px 15px'}
  }),
  dataStatusLabel
], ui.Panel.Layout.Flow(), {backgroundColor: app.colors.bg});

// UI Section - BOUNDARY INGESTION
var assetInput = ui.Textbox({placeholder: 'e.g. projects/ee-user/assets/boundary', style: app.WIDGET_STYLE});
var colInput   = ui.Textbox({placeholder: 'e.g. Mun_Name or Pro_Name', style: app.WIDGET_STYLE});
var btnLoadBoundary = ui.Button({label: 'Ingest & Center Boundary', style: {stretch: 'horizontal', margin: '12px 15px 8px 15px'}});
var boundaryStatus = ui.Label('Map initialized empty. Please ingest a boundary asset.', {
  fontSize: '11px', color: app.colors.shade, margin: '8px 15px', backgroundColor: app.colors.bg
});

var phase1Panel = ui.Panel({
  widgets: [
    ui.Label('Section 1: Boundary Setup', app.HEADING_STYLE),
    ui.Label('Shapefile Asset ID', app.HELP_TEXT_STYLE), assetInput,
    ui.Label('Asset Field Name', app.HELP_TEXT_STYLE), colInput,
    btnLoadBoundary,
    boundaryStatus
  ],
  style: app.SECTION_STYLE
});

// UI Section - POINT LEVEL ANALYTICS
var muniDropdown = ui.Select({placeholder: 'Awaiting boundary ingestion...', style: app.WIDGET_STYLE, disabled: true});
var pointCoordLabel = ui.Label('Selected Point: None', {
  fontSize: '11px', fontWeight: 'bold', color: app.colors.shade, margin: '8px 15px', backgroundColor: app.colors.bg
});
var timescaleSelect = ui.Select({items: ['1', '3', '6', '9', '12'], value: '3', style: app.WIDGET_STYLE});

var btnPanel1 = ui.Panel({layout: ui.Panel.Layout.flow('horizontal'), style: {stretch: 'horizontal', margin: '4px 15px', backgroundColor: app.colors.bg}});
var btnTimeSeries = ui.Button({label: 'Time Series', style: {width: '160px', margin: '0 10px 0 0'}, disabled: true});
var btnScatter    = ui.Button({label: 'Quadrants',   style: {width: '160px', margin: '0'}, disabled: true});
btnPanel1.add(btnTimeSeries).add(btnScatter);
var btnRunAll = ui.Button({label: 'Run Comprehensive Assessment (All)', style: {stretch: 'horizontal', margin: '15px 15px'}, disabled: true});

var phase2Panel = ui.Panel({
  widgets: [
    ui.Label('Section 2: Point Level Analytics', app.HEADING_STYLE),
    ui.Label('Selection Method 1: Feature Dropdown', app.HELP_TEXT_STYLE), muniDropdown,
    ui.Label('Selection Method 2: Click directly on the map', app.HELP_TEXT_STYLE), pointCoordLabel,
    ui.Label('SPI / STI Timescale Window', app.HELP_TEXT_STYLE), timescaleSelect,
    btnPanel1,
    btnRunAll
  ],
  style: app.SECTION_STYLE
});

// UI Section - BIVARIATE MAPPING
var modeSelect = ui.Select({items: [MODE_HINDCAST, MODE_FORECAST], value: MODE_HINDCAST, style: app.WIDGET_STYLE});

var yearPanel = ui.Panel({layout: ui.Panel.Layout.flow('horizontal'), style: {stretch: 'horizontal', margin: '8px 15px', backgroundColor: app.colors.bg}});
var yearList = [];
for (var yy = new Date().getUTCFullYear(); yy >= MAP_FIRST_YEAR; yy--) { yearList.push(yy.toString()); }
var yearSelect = ui.Select({items: yearList, value: yearList[0], style: {width: '160px', margin: '0 10px 0 0'}});
var monthItems = MONTH_NAMES.map(function(n, i) { return {label: n, value: i + 1}; });
var monthSelect = ui.Select({items: monthItems, value: 8, style: {width: '160px', margin: '0'}});
yearPanel.add(yearSelect).add(monthSelect);

var btnRenderBivariate = ui.Button({label: 'Render Bivariate Map', style: {stretch: 'horizontal', margin: '12px 15px 8px 15px'}, disabled: true});
var bivariateStatus = ui.Label('Available once a boundary is ingested. Point analysis is not required.', {
  fontSize: '11px', color: app.colors.shade, margin: '4px 15px', backgroundColor: app.colors.bg
});

var filenameInput = ui.Textbox({placeholder: 'e.g. Luzon_SPI3_STI3_Aug2026', style: app.WIDGET_STYLE, disabled: true});
var btnExport = ui.Button({
  label: 'Generate Download Link (GeoTIFF)',
  style: {stretch: 'horizontal', margin: '8px 15px', color: app.colors.primary, fontWeight: 'bold'},
  disabled: true
});
var exportLinkPanel = ui.Panel({layout: ui.Panel.Layout.flow('vertical'), style: {stretch: 'horizontal', margin: '0 15px', backgroundColor: app.colors.bg}});

var phase3Panel = ui.Panel({
  widgets: [
    ui.Label('Section 3: Generate Bivariate Map', app.HEADING_STYLE),
    ui.Label('Analysis Mode', app.HELP_TEXT_STYLE), modeSelect,
    ui.Label('Target 3-Month Window End (Year / Month)', app.HELP_TEXT_STYLE), yearPanel,
    btnRenderBivariate,
    bivariateStatus,
    ui.Label('Export Filename (no extension needed)', app.HELP_TEXT_STYLE), filenameInput,
    btnExport,
    exportLinkPanel
  ],
  style: app.SECTION_STYLE
});

var mainPanel = ui.Panel({
  widgets: [introPanel, phase1Panel, phase2Panel, phase3Panel],
  style: {width: '390px', padding: '10px 5px', backgroundColor: app.colors.bg}
});

ui.root.insert(0, mainPanel);
ui.root.insert(1, map);

// ==============================================================================
// SCRIPT SECTION 6. MAP OVERLAYS (RESULTS PANEL & LEGEND)
// ==============================================================================

var resultsOverlay = ui.Panel({
  layout: ui.Panel.Layout.flow('vertical'),
  style: {
    position: 'top-right', width: '560px', maxHeight: '92%', padding: '12px 16px',
    backgroundColor: 'rgba(255, 255, 247, 0.95)', border: '1px solid ' + app.colors.shade, shown: false
  }
});
map.add(resultsOverlay);

var overlayHeader = ui.Panel({layout: ui.Panel.Layout.flow('horizontal'), style: {margin: '0 0 8px 0', backgroundColor: '00000000'}});
var overlayTitle = ui.Label('Point Analysis Results', {fontSize: '16px', fontWeight: 'bold', color: app.colors.primary, stretch: 'horizontal', backgroundColor: '00000000'});
var btnCloseOverlay = ui.Button({
  label: '✕',
  style: {margin: '0 0 0 auto', color: app.colors.alert, fontWeight: 'bold'},
  onClick: function() { resultsOverlay.style().set('shown', false); }
});
overlayHeader.add(overlayTitle).add(btnCloseOverlay);

var overlaySubtitle = ui.Label('', {fontSize: '11px', color: app.colors.shade, margin: '0 0 8px 0', backgroundColor: '00000000'});
var overlayStatus   = ui.Label('', {fontSize: '11px', fontWeight: 'bold', color: app.colors.accent1, margin: '0 0 8px 0', backgroundColor: '00000000'});
var overlayChartBox = ui.Panel({layout: ui.Panel.Layout.flow('vertical'), style: {margin: '0', backgroundColor: '00000000'}});
resultsOverlay.add(overlayHeader).add(overlaySubtitle).add(overlayStatus).add(overlayChartBox);

function openResultsOverlay(title, subtitle) {
  resultsOverlay.style().set('shown', true);
  overlayTitle.setValue(title || 'Point Analysis Results');
  overlaySubtitle.setValue(subtitle || '');
}

var bivariateLegendPanel = null;
function addBivariateLegend() {
  if (bivariateLegendPanel) return;

  bivariateLegendPanel = ui.Panel({
    style: {position: 'bottom-left', padding: '8px 12px', backgroundColor: 'rgba(255, 255, 247, 0.95)', border: '1px solid ' + app.colors.shade}
  });
  bivariateLegendPanel.add(ui.Label('Bivariate SPI-3 & STI-3 Matrix', {
    fontWeight: 'bold', fontSize: '13px', margin: '0 0 4px 0', color: app.colors.dark, backgroundColor: '00000000'
  }));

  var legendContent = ui.Panel({layout: ui.Panel.Layout.flow('horizontal'), style: {margin: '0', padding: '0', backgroundColor: '00000000'}});
  var yAxisPanel = ui.Panel({layout: ui.Panel.Layout.flow('vertical'), style: {width: '45px', height: '154px', padding: '0', margin: '0', backgroundColor: '00000000'}});
  yAxisPanel.add(ui.Label({value: 'Wetter', style: {fontSize: '9px', textAlign: 'center', margin: '0 0 auto 0', backgroundColor: '00000000'}}));
  yAxisPanel.add(ui.Label({value: 'Normal', style: {fontSize: '9px', textAlign: 'center', margin: '0 0 auto 0', backgroundColor: '00000000'}}));
  yAxisPanel.add(ui.Label({value: 'Drier',  style: {fontSize: '9px', textAlign: 'center', margin: '0', backgroundColor: '00000000'}}));
  legendContent.add(yAxisPanel);

  var gridAndXAxisContainer = ui.Panel({layout: ui.Panel.Layout.flow('vertical'), style: {margin: '0', padding: '0', backgroundColor: '00000000'}});
  var legendGrid = ui.Panel({layout: ui.Panel.Layout.flow('vertical'), style: {width: '154px', height: '154px', margin: '0', padding: '0', backgroundColor: '00000000'}});
  var cellSize = 22;
  for (var ly = 7; ly >= 1; ly--) {
    var rowPanel = ui.Panel({layout: ui.Panel.Layout.flow('horizontal'), style: {margin: '0', padding: '0', height: cellSize + 'px', backgroundColor: '00000000'}});
    for (var lx = 1; lx <= 7; lx++) {
      rowPanel.add(ui.Label(' ', {backgroundColor: bivariate_palette[lx.toString() + ly.toString()], width: cellSize + 'px', height: cellSize + 'px', margin: '0', padding: '0'}));
    }
    legendGrid.add(rowPanel);
  }
  gridAndXAxisContainer.add(legendGrid);

  var xAxisLabels = ui.Panel({layout: ui.Panel.Layout.flow('horizontal'), style: {margin: '4px 0 0 0', width: '154px', backgroundColor: '00000000'}});
  xAxisLabels.add(ui.Label({value: 'Cooler', style: {fontSize: '9px', textAlign: 'left',   width: '45px', margin: '0', backgroundColor: '00000000'}}));
  xAxisLabels.add(ui.Label({value: 'Normal', style: {fontSize: '9px', textAlign: 'center', width: '64px', margin: '0', backgroundColor: '00000000'}}));
  xAxisLabels.add(ui.Label({value: 'Hotter', style: {fontSize: '9px', textAlign: 'right',  width: '45px', margin: '0', backgroundColor: '00000000'}}));
  gridAndXAxisContainer.add(xAxisLabels);

  legendContent.add(gridAndXAxisContainer);
  bivariateLegendPanel.add(legendContent);
  map.add(bivariateLegendPanel);
}

// ==============================================================================
// SCRIPT SECTION 7. BOUNDARY INGESTION LOGIC
// ==============================================================================

btnLoadBoundary.onClick(function() {
  var assetId = assetInput.getValue().trim();
  var colMuni = colInput.getValue().trim();

  if (!assetId) { setStatus(boundaryStatus, 'Warning: Please enter an Asset ID.', 'warning'); return; }

  setStatus(boundaryStatus, 'Verifying asset and loading polygons...', 'accent2');
  btnLoadBoundary.setDisabled(true);

  var fc = ee.FeatureCollection(assetId);
  var rows = fc.map(function(f) {
    var c = f.geometry().centroid(100).coordinates();
    return ee.Feature(null, {name: f.get(colMuni), lon: c.get(0), lat: c.get(1)});
  }).reduceColumns(ee.Reducer.toList(3), ['name', 'lon', 'lat']).get('list');

  ee.Dictionary({
    count:  fc.size(),
    props:  ee.Feature(fc.first()).propertyNames(),
    rows:   rows,
    bounds: fc.geometry(100).bounds(100)
  }).evaluate(function(res, err) {
    btnLoadBoundary.setDisabled(false);

    if (err || !res || !res.count) {
      setStatus(boundaryStatus, 'Error: Could not load FeatureCollection. Check Asset ID and sharing permissions.' + (err ? ' (' + err + ')' : ''), 'alert');
      return;
    }

    state.boundaryFC     = fc;
    state.colMuni        = colMuni;
    state.boundaryMask   = ee.Image().byte().paint(fc, 1);
    state.boundaryRegion = ee.Geometry(res.bounds);

    // Boundary outline
    state.boundaryLayer.setEeObject(ee.Image().byte().paint({featureCollection: fc, color: 1, width: 2}));
    state.boundaryLayer.setVisParams({palette: [app.colors.primary]});
    state.boundaryLayer.setName('Boundary: ' + assetId);
    state.boundaryLayer.setShown(true);
    map.centerObject(state.boundaryRegion);

    // A new boundary invalidates any previously rendered/exportable map
    state.bivariateLayer.setShown(false);
    resetExport('Boundary changed. Render the bivariate map to enable download.');

    state.locationMap = {};
    var names = [];
    (res.rows || []).forEach(function(r) {
      if (r[0] !== null && r[1] !== null && r[2] !== null) {
        var mName = r[0].toString();
        if (!state.locationMap[mName]) names.push(mName);
        state.locationMap[mName] = {lon: parseFloat(r[1]), lat: parseFloat(r[2])};
      }
    });
    names.sort();

    if (names.length > 0) {
      muniDropdown.items().reset(names);
      muniDropdown.setPlaceholder('Select a Municipality (' + names.length + ' found)...');
      muniDropdown.setDisabled(false);
    } else {
      muniDropdown.items().reset([]);
      muniDropdown.setPlaceholder('No names found for column: ' + colMuni);
      muniDropdown.setDisabled(true);
    }

    btnRenderBivariate.setDisabled(false);
    setStatus(bivariateStatus, 'Ready. You can render the bivariate map now; point analysis is optional.', 'primary');

    var msg = 'Success: Boundary loaded (' + res.count + ' polygons).';
    if (names.length === 0 && res.props) {
      msg += ' Column "' + colMuni + '" not found. Available columns: ' + res.props.filter(function(p) { return p.indexOf('system:') !== 0; }).join(', ');
      setStatus(boundaryStatus, msg, 'warning');
    } else {
      setStatus(boundaryStatus, msg + ' Proceed to Phase 2 or Phase 3.', 'primary');
    }
  });
});

// ==============================================================================
// SCRIPT SECTION 8. POINT ANALYTICS LOGIC
// ==============================================================================

function setAnalysisPoint(pointGeom, displayName) {
  state.currentPoint = pointGeom;
  state.currentPointName = displayName;
  state.cachedCharts = {};
  state.requestId++;
  state.isComputing = false;
  state.pendingMode = null;

  pointCoordLabel.setValue(displayName);
  pointCoordLabel.style().set({color: app.colors.primary, fontWeight: 'bold'});

  state.pointLayer.setEeObject(pointGeom);
  state.pointLayer.setName('Selected Point: ' + displayName);
  state.pointLayer.setShown(true);

  btnTimeSeries.setDisabled(false);
  btnScatter.setDisabled(false);
  btnRunAll.setDisabled(false);

  openResultsOverlay('Analysis Ready', displayName);
  setStatus(overlayStatus, 'Point selected. Click any analysis button on the left panel or Run All.', 'primary');
  overlayChartBox.clear();
}

muniDropdown.onChange(function(selectedName) {
  if (!selectedName || !state.locationMap[selectedName]) return;
  var c = state.locationMap[selectedName];
  var pt = ee.Geometry.Point([c.lon, c.lat]);
  map.setCenter(c.lon, c.lat, 12);
  setAnalysisPoint(pt, selectedName + ' (' + c.lon.toFixed(4) + ', ' + c.lat.toFixed(4) + ')');
});

map.onClick(function(coords) {
  var pt = ee.Geometry.Point([coords.lon, coords.lat]);
  setAnalysisPoint(pt, 'Point [' + coords.lon.toFixed(4) + ', ' + coords.lat.toFixed(4) + ']');
});

timescaleSelect.onChange(function() {
  state.cachedCharts = {};
  state.requestId++;
  state.isComputing = false;
  state.pendingMode = null;
  if (state.currentPoint) {
    setStatus(overlayStatus, 'Timescale updated to ' + timescaleSelect.getValue() + ' months. Ready to calculate.', 'primary');
    overlayChartBox.clear();
  }
});

// ==============================================================================
// SCRIPT SECTION 9. POINT DATA EXTRACTION
// ==============================================================================

function calculateAndDisplay(modeType) {
  if (!state.currentPoint) {
    openResultsOverlay('No point selected', '');
    setStatus(overlayStatus, 'Please select a municipality or click on the map first.', 'alert');
    return;
  }
  if (state.cachedCharts.spiPlot) { renderChartToOverlay(modeType); return; }

  state.pendingMode = modeType;
  if (state.isComputing) {
    setStatus(overlayStatus, 'Still computing... results will appear automatically when ready.', 'accent1');
    return;
  }
  state.isComputing = true;

  var reqId = ++state.requestId;
  var point = state.currentPoint;
  var TIMESCALE = parseInt(timescaleSelect.getValue(), 10);

  openResultsOverlay('Processing Data...', state.currentPointName);
  setStatus(overlayStatus, 'Checking data availability...', 'accent1');
  overlayChartBox.clear();

  withDataInfo(function(info, err) {
    if (reqId !== state.requestId) return;
    if (!info) {
      state.isComputing = false;
      setStatus(overlayStatus, 'Error reading dataset availability: ' + err, 'alert');
      return;
    }

    // Monthly series window: from POINT_START_YEAR (or the first month both datasets cover)
    // to the last month complete in BOTH datasets.
    var startYm = maxYm({y: POINT_START_YEAR, m: 1}, maxYm(info.chirpsStart, info.eraStart));
    var endYm   = minYm(info.chirpsEnd, info.eraEnd); // exclusive
    var nMonths = monthsBetween(startYm, endYm);
    if (nMonths < 24) {
      state.isComputing = false;
      setStatus(overlayStatus, 'Error: fewer than 24 complete months are available in both CHIRPS and AgERA5.', 'alert');
      return;
    }
    var base = eeDate(startYm);

    setStatus(overlayStatus, 'Extracting monthly series (' + monthLabel(startYm) + ' to ' + monthLabel(addMonths(endYm, -1)) + ') and fitting Gamma distributions...', 'accent1');

    var monthlyIC = ee.ImageCollection.fromImages(ee.List.sequence(0, nMonths - 1).map(function(n) {
      var s = base.advance(ee.Number(n), 'month');
      var e = s.advance(1, 'month');
      var precip = CHIRPS.filterDate(s, e).sum();                    // mm / month (sum of merged CHIRPS pentads)
      var temp   = ERA5.filterDate(s, e).mean().subtract(KELVIN_TO_C); // degC (mean of daily means)
      return precip.rename('precip').addBands(temp.rename('temp')).set('system:time_start', s.millis());
    }));

    monthlyIC.getRegion(point, POINT_SAMPLE_SCALE).evaluate(function(rows, err2) {
      if (reqId !== state.requestId) return;
      state.isComputing = false;

      if (err2 || !rows || rows.length < 2) {
        setStatus(overlayStatus, 'Error: No data returned for this point. ' + (err2 || 'Please try another location.'), 'alert');
        return;
      }
      try {
        buildPointResults(rows, TIMESCALE);
      } catch (e) {
        setStatus(overlayStatus, 'Error: ' + e.message, 'alert');
        return;
      }
      setStatus(overlayStatus, 'Calculation complete.', 'primary');
      renderChartToOverlay(state.pendingMode || modeType);
    });
  });
}

// ==============================================================================
// SCRIPT SECTION 10. CLIENT-SIDE STATISTICS & CHART BUILDING
// ==============================================================================

function signedAreaChart(dates, values, title, vTitle, posLabel, negLabel, posColor, negColor) {
  var rows = dates.map(function(d, i) {
    var v = values[i];
    return {c: [{v: d}, {v: (v !== null && v > 0) ? v : 0}, {v: (v !== null && v < 0) ? v : 0}, {v: 0}]};
  });
  return ui.Chart({
    cols: [
      {id: 'date', label: 'Date', type: 'string'},
      {id: 'pos', label: posLabel, type: 'number'},
      {id: 'neg', label: negLabel, type: 'number'},
      {id: 'zero', label: 'Zero', type: 'number'}
    ],
    rows: rows
  }).setChartType('ComboChart').setOptions({
    title: title,
    hAxis: {title: 'Date', textStyle: {fontSize: 10}},
    vAxis: {title: vTitle, baseline: 0, baselineColor: '#444'},
    isStacked: false,
    series: {
      0: {type: 'area', color: posColor, areaOpacity: 0.5, lineWidth: 1.2},
      1: {type: 'area', color: negColor, areaOpacity: 0.5, lineWidth: 1.2},
      2: {type: 'line', color: '#444', lineWidth: 1, visibleInLegend: false}
    },
    legend: {position: 'top', textStyle: {fontSize: 10}},
    backgroundColor: app.colors.bg,
    chartArea: {width: '85%', height: '65%'}
  });
}

function buildPointResults(rows, TIMESCALE) {
  // ---- Parse getRegion output: [id, longitude, latitude, time, precip, temp]
  var header = rows[0];
  var iTime = header.indexOf('time'), iP = header.indexOf('precip'), iT = header.indexOf('temp');
  var seen = {}, recs = [];
  for (var r = 1; r < rows.length; r++) {
    var t = rows[r][iTime];
    if (t === null || seen[t]) continue;
    seen[t] = true;
    recs.push({t: t, p: rows[r][iP], te: rows[r][iT]});
  }
  recs.sort(function(a, b) { return a.t - b.t; });

  var cleanNum = function(v) { return (v === null || v === undefined || !isFinite(v)) ? null : v; };
  var dates = recs.map(function(x) { return isoMonth(x.t); });
  var precs = recs.map(function(x) { return cleanNum(x.p); });
  var temps = recs.map(function(x) { return cleanNum(x.te); });

  if (precs.every(function(v) { return v === null; })) {
    throw new Error('No rainfall data at this pixel (often a coastal/ocean pixel). Try a point slightly inland.');
  }
  if (temps.every(function(v) { return v === null; })) {
    throw new Error('No AgERA5 temperature data at this pixel. Try another location.');
  }

  // ---- Aggregate over the selected timescale
  var precsAgg = rollingAggregate(precs, TIMESCALE, 'sum');
  var tempsAgg = rollingAggregate(temps, TIMESCALE, 'mean');
  var validIdx = [];
  for (var vi = 0; vi < dates.length; vi++) { if (precsAgg[vi] !== null && tempsAgg[vi] !== null) validIdx.push(vi); }
  dates = validIdx.map(function(i) { return dates[i]; });
  precs = validIdx.map(function(i) { return precsAgg[i]; });
  temps = validIdx.map(function(i) { return tempsAgg[i]; });

  // ---- Per-calendar-month baseline distributions
  var precipByMonth = {}, tempByMonth = {};
  dates.forEach(function(d, i) {
    var m = d.split('-')[1];
    (precipByMonth[m] = precipByMonth[m] || []).push(precs[i]);
    (tempByMonth[m] = tempByMonth[m] || []).push(temps[i]);
  });
  var cmp = function(a, b) { return a - b; };

  var precipStats = {}, tempStats = {};
  Object.keys(precipByMonth).forEach(function(m) {
    var all = precipByMonth[m].slice().sort(cmp);
    var zeros = all.filter(function(v) { return v === 0; }).length;
    var nonzero = all.filter(function(v) { return v > 0; });
    var q = all.length > 0 ? zeros / all.length : 0, shape = null, scale = null, ok = false;
    // Gamma fit by maximum likelihood (WMO-No. 1090); falls back to the empirical CDF if the fit is not possible
    var fit = gammaMLE(nonzero);
    if (fit) { shape = fit.shape; scale = fit.scale; ok = true; }
    precipStats[m] = {q: q, shape: shape, scale: scale, ok: ok, sortedAll: all};
  });

  Object.keys(tempByMonth).forEach(function(m) {
    var arr = tempByMonth[m];
    var mean = arr.reduce(function(a, b) { return a + b; }, 0) / arr.length;
    var std = Math.sqrt(arr.reduce(function(a, b) { return a + Math.pow(b - mean, 2); }, 0) / Math.max(1, arr.length - 1));
    tempStats[m] = {mean: mean, std: std};
  });

  // ---- Standardized SPI & STI
  var spiList = [], stiList = [], validDataJS = [];
  for (var i = 0; i < dates.length; i++) {
    var mKey = dates[i].split('-')[1], p = precs[i], tv = temps[i], spi = null, sti = null;
    var ps = precipStats[mKey], prob;
    if (ps.ok) { prob = ps.q + (1 - ps.q) * ((p > 0) ? gammaCDF(p, ps.shape, ps.scale) : 0); }
    else { prob = empiricalCDF(p, ps.sortedAll); }
    spi = norminv(clamp(prob, 1e-12, 1 - 1e-12));
    var ts = tempStats[mKey];
    sti = (ts && isFinite(ts.std) && ts.std > 0) ? (tv - ts.mean) / ts.std : 0;
    spiList.push(spi); stiList.push(sti);
    if (spi !== null && sti !== null) validDataJS.push({date: dates[i], spi: spi, sti: sti});
  }
  if (validDataJS.length < 24) throw new Error('Not enough valid months at this point to compute indices.');

  // ---- 5-month AR(1) forecast
  var spiClim = {}, stiClim = {}, counts = {};
  validDataJS.forEach(function(f) {
    var mn = parseInt(f.date.split('-')[1], 10);
    spiClim[mn] = (spiClim[mn] || 0) + f.spi;
    stiClim[mn] = (stiClim[mn] || 0) + f.sti;
    counts[mn]  = (counts[mn] || 0) + 1;
  });
  for (var fm = 1; fm <= 12; fm++) { if (counts[fm]) { spiClim[fm] /= counts[fm]; stiClim[fm] /= counts[fm]; } }

  var lastFeat = validDataJS[validDataJS.length - 1];
  var lastYear = parseInt(lastFeat.date.split('-')[0], 10);
  var lastMonth = parseInt(lastFeat.date.split('-')[1], 10);

  function lag1(arr, key) {
    var num = 0, den = 0;
    for (var k = 1; k < arr.length; k++) {
      var x = arr[k - 1][key], y = arr[k][key];
      if (isFinite(x) && isFinite(y)) { num += x * y; den += x * x; }
    }
    return (den !== 0) ? num / den : 0.3;
  }
  var tailData = validDataJS.slice(-24);
  var phiSPI = Math.max(0, Math.min(0.9, lag1(tailData, 'spi')));
  var phiSTI = Math.max(0, Math.min(0.9, lag1(tailData, 'sti')));

  var forecastList = [], prevSPI = lastFeat.spi, prevSTI = lastFeat.sti;
  for (var fi = 1; fi <= 5; fi++) {
    lastMonth++;
    if (lastMonth > 12) { lastMonth = 1; lastYear++; }
    var fLabel = lastYear + '-' + pad2(lastMonth);
    var spiF = (spiClim[lastMonth] || 0) + phiSPI * (prevSPI - (spiClim[lastMonth] || 0));
    var stiF = (stiClim[lastMonth] || 0) + phiSTI * (prevSTI - (stiClim[lastMonth] || 0));
    forecastList.push({date: fLabel, spi: spiF, sti: stiF});
    prevSPI = spiF; prevSTI = stiF;
  }

  // ---- Chart 1 & 2: SPI / STI time series
  state.cachedCharts.spiPlot = signedAreaChart(dates, spiList,
    'SPI (' + TIMESCALE + '-Month) Historical Trajectory', 'SPI',
    'Wetter (SPI > 0)', 'Drier (SPI < 0)', '#1f77b4', '#d62728');
  state.cachedCharts.stiPlot = signedAreaChart(dates, stiList,
    'STI (' + TIMESCALE + '-Month) Historical Trajectory', 'STI',
    'Hotter (STI > 0)', 'Cooler (STI < 0)', '#d62728', '#1f77b4');

  // ---- Chart 3: SPI vs STI quadrant scatter
  var scatterCols = [
    {id: 'sti', label: 'STI', type: 'number'},
    {id: 'area_wc', label: 'Wet & Cool (SPI+/STI-)', type: 'number'},
    {id: 'area_wh', label: 'Wet & Hot (SPI+/STI+)', type: 'number'},
    {id: 'area_dc', label: 'Dry & Cool (SPI-/STI-)', type: 'number'},
    {id: 'area_dh', label: 'Dry & Hot (SPI-/STI+)', type: 'number'},
    {id: 'line_md', label: 'Moderate Drought (-1)', type: 'number'},
    {id: 'line_sd', label: 'Severe Drought (-1.5)', type: 'number'},
    {id: 'line_hs', label: 'Heat Stress (+1)', type: 'number'},
    {id: 'line_sh', label: 'Severe Heat (+1.5)', type: 'number'},
    {id: 'hist', label: 'Historical', type: 'number'},
    {id: 'hist_tt', role: 'tooltip', type: 'string'},
    {id: 'hist_cd', label: 'Historical (Compound Dry-Hot)', type: 'number'},
    {id: 'hist_cd_tt', role: 'tooltip', type: 'string'},
    {id: 'forecast', label: 'Forecast', type: 'number'},
    {id: 'forecast_tt', role: 'tooltip', type: 'string'},
    {id: 'forecast_cd', label: 'Forecast (Compound Dry-Hot)', type: 'number'},
    {id: 'forecast_cd_tt', role: 'tooltip', type: 'string'},
    {id: 'quad_labels', label: 'Quadrant Labels', type: 'number'},
    {id: 'quad_labels_annot', role: 'annotation', type: 'string'}
  ];
  var scatterRows = [];
  var limit = 5;
  function addScatterRow(sti, a0, a1, a2, a3, l0, l1, l2, l3, h, h_tt, he, he_tt, f, f_tt, fe, fe_tt) {
  var sanitize = function(val) {
    return (val === undefined || val === null || isNaN(val)) ? null : val;
  };
  
  scatterRows.push({c: [
    {v: sanitize(sti)}, {v: sanitize(a0)}, {v: sanitize(a1)}, {v: sanitize(a2)}, {v: sanitize(a3)},
    {v: sanitize(l0)},  {v: sanitize(l1)}, {v: sanitize(l2)}, {v: sanitize(l3)},
    {v: sanitize(h)},   {v: sanitize(h_tt)}, {v: sanitize(he)}, {v: sanitize(he_tt)},
    {v: sanitize(f)},   {v: sanitize(f_tt)}, {v: sanitize(fe)}, {v: sanitize(fe_tt)},
    {v: null}, {v: null}
  ]});
}

  addScatterRow(-limit, limit, null, -limit, null, null, null, null, null, null, null, null, null, null, null, null, null);
  addScatterRow(0,      limit, null, -limit, null, null, null, null, null, null, null, null, null, null, null, null, null);
  addScatterRow(0.0001, null, limit, null, -limit, null, null, null, null, null, null, null, null, null, null, null, null);
  addScatterRow(limit,  null, limit, null, -limit, null, null, null, null, null, null, null, null, null, null, null, null);
  addScatterRow(-limit, null, null, null, null, -1, -1.5, null, null, null, null, null, null, null, null, null, null);
  addScatterRow(limit,  null, null, null, null, -1, -1.5, null, null, null, null, null, null, null, null, null, null);
  addScatterRow(1,   null, null, null, null, null, null, -limit, null, null, null, null, null, null, null, null, null);
  addScatterRow(1,   null, null, null, null, null, null,  limit, null, null, null, null, null, null, null, null, null);
  addScatterRow(1.5, null, null, null, null, null, null, null, -limit, null, null, null, null, null, null, null, null);
  addScatterRow(1.5, null, null, null, null, null, null, null,  limit, null, null, null, null, null, null, null, null);

  validDataJS.forEach(function(row) {
    var tt = 'Date: ' + row.date + '\nType: Historical\nSPI: ' + row.spi.toFixed(3) + '\nSTI: ' + row.sti.toFixed(3);
    if (row.spi <= -1 && row.sti >= 1) addScatterRow(row.sti, null,null,null,null, null,null,null,null, null,null, row.spi,tt, null,null, null,null);
    else                               addScatterRow(row.sti, null,null,null,null, null,null,null,null, row.spi,tt, null,null, null,null, null,null);
  });
  forecastList.forEach(function(row) {
    var tt = 'Date: ' + row.date + '\nType: Forecast\nSPI: ' + row.spi.toFixed(3) + '\nSTI: ' + row.sti.toFixed(3);
    if (row.spi <= -1 && row.sti >= 1) addScatterRow(row.sti, null,null,null,null, null,null,null,null, null,null, null,null, row.spi,tt, row.spi,tt);
    else                               addScatterRow(row.sti, null,null,null,null, null,null,null,null, null,null, null,null, row.spi,tt, null,null);
  });

  var labelConfig = [
    {sti: -3.0, spi:  3.0, text: 'Wet-Cool'},
    {sti:  3.0, spi:  3.0, text: 'Wet-Hot'},
    {sti: -3.0, spi: -3.2, text: 'Dry-Cool'},
    {sti:  3.0, spi: -3.2, text: 'Dry-Hot (Compound Risk)'},
    {sti: -1.8, spi: -0.9, text: 'Moderate Drought'},
    {sti: -1.8, spi: -1.4, text: 'Severe Drought'},
    {sti: 1.05, spi: -2.2, text: 'Heat Stress'},
    {sti: 1.55, spi: -2.6, text: 'Severe Heat'}
  ];
  labelConfig.forEach(function(item) {
    var cells = [{v: item.sti}];
    for (var k = 1; k <= 16; k++) cells.push({v: null});
    cells.push({v: item.spi});
    cells.push({v: item.text});
    scatterRows.push({c: cells});
  });

  state.cachedCharts.scatterCombined = ui.Chart({cols: scatterCols, rows: scatterRows})
    .setChartType('ComboChart')
    .setOptions({
      title: 'SPI vs STI Quadrant Matrix (Compound Stress)',
      backgroundColor: app.colors.bg,
      hAxis: {title: 'STI (Temperature Index)', gridlines: {count: 5}, viewWindow: {min: -3.5, max: 3.5}, ticks: [-3, -2, -1, 0, 1, 2, 3]},
      vAxis: {title: 'SPI (Precipitation Index)', gridlines: {count: 5}, viewWindow: {min: -3.5, max: 3.5}, ticks: [-3, -2, -1, 0, 1, 2, 3]},
      interpolateNulls: true,
      tooltip: {trigger: 'focus'},
      annotations: {textStyle: {fontSize: 9, italic: true, color: app.colors.shade}, highContrast: false, stem: {length: 0}},
      seriesType: 'scatter',
      series: {
        0: {type: 'area', color: '#add8e6', areaOpacity: 0.25, enableInteractivity: false, visibleInLegend: false},
        1: {type: 'area', color: '#ffff99', areaOpacity: 0.25, enableInteractivity: false, visibleInLegend: false},
        2: {type: 'area', color: '#d3d3d3', areaOpacity: 0.25, enableInteractivity: false, visibleInLegend: false},
        3: {type: 'area', color: '#ffcccb', areaOpacity: 0.25, enableInteractivity: false, visibleInLegend: false},
        4: {type: 'line', color: app.colors.warning, lineWidth: 1.5, lineDashStyle: [4, 4], visibleInLegend: true},
        5: {type: 'line', color: '#ff0000', lineWidth: 1.5, lineDashStyle: [4, 4], visibleInLegend: true},
        6: {type: 'line', color: app.colors.warning, lineWidth: 1.5, lineDashStyle: [4, 4], visibleInLegend: true},
        7: {type: 'line', color: '#ff0000', lineWidth: 1.5, lineDashStyle: [4, 4], visibleInLegend: true},
        8: {type: 'scatter', color: '#3498db', pointSize: 3, dataOpacity: 0.6},
        9: {type: 'scatter', color: '#ff2400', pointSize: 3, dataOpacity: 0.6},
        10: {type: 'scatter', color: '#281e5d', pointSize: 8, pointShape: 'diamond'},
        11: {type: 'scatter', color: '#ff2400', pointSize: 8, pointShape: 'diamond'},
        12: {type: 'scatter', color: 'transparent', pointSize: 0, visibleInLegend: false, enableInteractivity: false}
      },
      chartArea: {width: '80%', height: '70%'}
    });
}

// RENDER CHARTS TO RIGHT OVERLAY PANEL
function renderChartToOverlay(modeType) {
  openResultsOverlay('Point Analysis Results - ' + state.currentPointName, 'Timescale: ' + timescaleSelect.getValue() + '-Month');
  overlayChartBox.clear();

  if (modeType === 'timeseries' || modeType === 'all') {
    if (state.cachedCharts.spiPlot) overlayChartBox.add(state.cachedCharts.spiPlot);
    if (state.cachedCharts.stiPlot) overlayChartBox.add(state.cachedCharts.stiPlot);
  }
  
  if (modeType === 'scatter' || modeType === 'all') {
    if (state.cachedCharts.scatterCombined) {
      overlayChartBox.add(state.cachedCharts.scatterCombined);
    } else {
      setStatus(overlayStatus, 'Quadrant scatter chart could not be generated for this location.', 'warning');
    }
  }
}

btnTimeSeries.onClick(function() { calculateAndDisplay('timeseries'); });
btnScatter.onClick(function()    { calculateAndDisplay('scatter'); });
btnRunAll.onClick(function()     { calculateAndDisplay('all'); });

// ==============================================================================
// SCRIPT SECTION 11. BIVARIATE RASTER ENGINE
// ==============================================================================

// One combined reducer gives baseline mean and SD in a single pass over the yearly images.
var BASELINE_STATS = ee.Reducer.mean().combine({reducer2: ee.Reducer.stdDev(), sharedInputs: true});

// Rainfall total (mm) of the 3-month window starting at month m of year y (merged CHIRPS pentads).
function chirpsSeasonalSum(y, m) {
  var s = ee.Date.fromYMD(y, m, 1);
  return CHIRPS.filterDate(s, s.advance(3, 'month')).sum().rename('precip');
}
// Mean temperature (degC) of the 3-month window starting at month m of year y (AgERA5 daily means).
function eraSeasonalMean(y, m) {
  var s = ee.Date.fromYMD(y, m, 1);
  return ERA5.filterDate(s, s.advance(3, 'month')).mean().subtract(KELVIN_TO_C).rename('temp');
}

// Per-pixel baseline mean and SD of fn(year, m) over the given years.
function baselineStats(fn, years, m, band) {
  var st = ee.ImageCollection.fromImages(years.map(function(y) { return fn(y, m); })).reduce(BASELINE_STATS);
  return {mean: st.select(band + '_mean'), std: st.select(band + '_stdDev')};
}

// Standardized anomaly; pixels with zero baseline SD are masked rather than returning Inf/NaN.
function zScore(current, stats, name) {
  return current.subtract(stats.mean).divide(stats.std).updateMask(stats.std.gt(0)).rename(name);
}

// ---- Gamma SPI for the map (same method as the point dashboard, WMO-No. 1090, 2012) ----
// Per pixel: fit a gamma by maximum likelihood (Thom 1958 closed form) to the NON-ZERO baseline totals,
// H(x) = q + (1 - q) * G(x) with q = share of zero totals, then SPI = standard-normal quantile of H.
// Pixels with fewer than GAMMA_MIN_NONZERO non-zero baseline totals, or no spread (A ~ 0), use the empirical CDF
// (count(<= x) / (N + 1)), exactly like the point dashboard.
// (GAMMA_MIN_NONZERO and GAMMA_MIN_A are defined with gammaMLE in Section 4.)
// Keeps the normal quantile finite: H is clamped to [eps, 1 - eps], so the map SPI is limited to about +/-4.75.
var SPI_P_EPS = 1e-6;

// The Earth Engine docs describe ee.Image.gammainc as the regularized lower incomplete gamma "γ(x,a)" without
// spelling out which image is which, so the argument order is checked once at startup against a case with a known
// answer: P(a=2, x=1) = 1 - 2/e = 0.2642 if the call is x.gammainc(a); P(a=1, x=2) = 1 - e^-2 = 0.8647 if reversed.
var gammaincXFirst = true; // documented order; overwritten by the check below
ee.Image.constant(1).gammainc(ee.Image.constant(2))
  .reduceRegion({reducer: ee.Reducer.first(), geometry: ee.Geometry.Point([0, 0]), scale: 1000})
  .values().get(0)
  .evaluate(function(v) {
    if (v === null || v === undefined || !isFinite(v)) return;
    gammaincXFirst = Math.abs(v - 0.2642) < Math.abs(v - 0.8647);
    print('ee.Image.gammainc order check (expect 0.2642 or 0.8647):', v, gammaincXFirst ? '-> x.gammainc(a)' : '-> a.gammainc(x)');
  });

// current: image with the window total being standardized; fn(y, m): baseline total image; years: baseline years.
function gammaSpiImage(current, fn, years, m) {
  // One 4-band image per baseline year, reduced once per statistic (shared sub-graph).
  var stack = ee.ImageCollection.fromImages(years.map(function(y) {
    var x = fn(y, m);
    var pos = x.gt(0);
    return ee.Image.cat([
      x.eq(0).rename('zero'),                       // 1 where the total is zero
      x.updateMask(pos).rename('xnz'),              // non-zero totals
      x.updateMask(pos).log().rename('lnxnz'),      // ln of non-zero totals
      x.lte(current).rename('le')                   // for the empirical-CDF fallback
    ]);
  }));
  var n      = stack.select('zero').count();        // baseline years with data
  var nZero  = stack.select('zero').sum();
  var nNz    = stack.select('xnz').count();
  var meanNz = stack.select('xnz').mean();
  var meanLn = stack.select('lnxnz').mean();
  var nLe    = stack.select('le').sum();

  var q = nZero.divide(n);                          // probability of zero
  var A = meanNz.log().subtract(meanLn);            // A = ln(mean) - mean(ln x), > 0 whenever the values differ
  var fitOk = nNz.gte(GAMMA_MIN_NONZERO).and(A.gt(GAMMA_MIN_A)).unmask(0);

  // Dummy parameters where no fit is possible keep every intermediate value finite; those pixels are replaced below.
  var aSafe = A.unmask(1).where(fitOk.not(), 1);
  var mSafe = meanNz.unmask(1).where(fitOk.not(), 1);
  var alpha = aSafe.multiply(4 / 3).add(1).sqrt().add(1).divide(aSafe.multiply(4)); // shape
  var beta  = mSafe.divide(alpha);                                                   // scale

  var z = current.max(0).divide(beta);
  var G = gammaincXFirst ? z.gammainc(alpha) : alpha.gammainc(z);
  var H = ee.Image(1).subtract(q).multiply(G).add(q)                  // q + (1 - q) * G(x)
    .where(current.lte(0), q)                                          // zero total -> H = q
    .where(fitOk.not(), nLe.divide(n.add(1)));                         // fallback: empirical CDF

  return H.clamp(SPI_P_EPS, 1 - SPI_P_EPS).multiply(2).subtract(1).erfInv().multiply(Math.SQRT2)
    .updateMask(current.mask()).rename('SPI3');
}

// Computes 3-month SPI/STI for the window starting at `win` ({y, m}).
// Baseline years whose window is not fully covered by that dataset (at either end) are skipped.
// SPI is a gamma-based SPI (maximum likelihood, WMO-No. 1090); STI is a standardized anomaly (z-score).
// Both variables come from the same sources as the current window, so no bias correction is applied.
function computeSeasonalIndices(win, info) {
  var m = win.m;
  var chirpsYears = [], eraYears = [];
  for (var y = BASELINE_START_YEAR; y <= BASELINE_END_YEAR; y++) {
    var yStart = {y: y, m: m}, yEnd = addMonths(yStart, 3);
    if (isCovered(info.chirpsStart, yStart) && isAvailable(info.chirpsEnd, yEnd)) chirpsYears.push(y);
    if (isCovered(info.eraStart, yStart)    && isAvailable(info.eraEnd, yEnd))    eraYears.push(y);
  }
  if (chirpsYears.length < MIN_BASELINE_YEARS || eraYears.length < MIN_BASELINE_YEARS) {
    return {error: 'Not enough baseline years for this season (CHIRPS: ' + chirpsYears.length +
      ', AgERA5: ' + eraYears.length + '; minimum ' + MIN_BASELINE_YEARS + '). Check dataset coverage or the baseline years.'};
  }

  var spi = gammaSpiImage(chirpsSeasonalSum(win.y, m), chirpsSeasonalSum, chirpsYears, m);   // gamma SPI (ML fit)
  var sti = zScore(eraSeasonalMean(win.y, m),   baselineStats(eraSeasonalMean,   eraYears,     m, 'temp'),   'STI3');

  return {spi: spi, sti: sti, nChirps: chirpsYears.length, nEra: eraYears.length};
}

function resetExport(message) {
  state.exportImage = null;
  filenameInput.setDisabled(true);
  btnExport.setDisabled(true);
  btnExport.setLabel('Generate Download Link (GeoTIFF)');
  exportLinkPanel.clear();
  if (message) exportLinkPanel.add(ui.Label(message, {fontSize: '11px', color: app.colors.shade, margin: '4px 0', backgroundColor: app.colors.bg}));
}

function runBivariateAnalysis(mode, year, month) {
  if (!state.boundaryFC) {
    setStatus(bivariateStatus, 'Please ingest a boundary asset first (Phase 1).', 'alert');
    return;
  }

  btnRenderBivariate.setDisabled(true);
  btnRenderBivariate.setLabel('Checking data availability...');

  withDataInfo(function(info, err) {
    btnRenderBivariate.setDisabled(false);
    btnRenderBivariate.setLabel(state.exportImage ? 'Re-render Bivariate Map' : 'Render Bivariate Map');

    if (!info) { setStatus(bivariateStatus, 'Could not read dataset availability: ' + err, 'alert'); return; }

    var isForecast  = (mode === MODE_FORECAST);
    var targetEnd   = addMonths({y: year, m: month}, 1);   // exclusive
    var targetStart = addMonths(targetEnd, -3);
    var obsStart    = isForecast ? addMonths(targetStart, -1) : targetStart;
    var obsEnd      = addMonths(obsStart, 3);

    var missing = [];
    if (!isAvailable(info.chirpsEnd, obsEnd)) missing.push('CHIRPS (complete through ' + monthLabel(addMonths(info.chirpsEnd, -1)) + ')');
    if (!isAvailable(info.eraEnd, obsEnd))    missing.push('AgERA5 (complete through ' + monthLabel(addMonths(info.eraEnd, -1)) + ')');
    if (missing.length) {
      setStatus(bivariateStatus,
        'Observed window ' + monthLabel(obsStart) + ' – ' + monthLabel(addMonths(obsEnd, -1)) +
        ' is not fully available: ' + missing.join('; ') + '. Choose an earlier month' +
        (isForecast ? '.' : ', or switch to Forecast mode.'), 'alert');
      return;
    }

    var idx = computeSeasonalIndices(obsStart, info);
    if (idx.error) { setStatus(bivariateStatus, idx.error, 'alert'); return; }

    var spi = idx.spi, sti = idx.sti;
    if (isForecast) {
      spi = spi.multiply(PHI_SPI).rename('SPI3');
      sti = sti.multiply(PHI_STI).rename('STI3');
    }

    var stiClass = classifyIndex(sti);
    var spiClass = classifyIndex(spi);
    var code     = stiClass.multiply(10).add(spiClass);
    var indexed  = code.remap(eeClassKeys, eeClassIndices).updateMask(state.boundaryMask);

    var modeShort  = isForecast ? 'Persistence Outlook' : 'Hindcast';
    var windowText = monthLabel(targetStart) + ' – ' + monthLabel(addMonths(targetEnd, -1));

    state.bivariateLayer.setEeObject(indexed);
    state.bivariateLayer.setVisParams({min: 0, max: 48, palette: paletteList});
    state.bivariateLayer.setName(modeShort + ' SPI3-STI3 ' + windowText);
    state.bivariateLayer.setShown(true);
    addBivariateLegend();

    // Export image (all bands float so they fit in one GeoTIFF)
    state.exportImage = ee.Image.cat([
      spi, sti,
      spiClass.rename('SPI_Class'),
      stiClass.rename('STI_Class'),
      code.rename('Bivariate_Code')
    ]).toFloat().updateMask(state.boundaryMask);

    var newDefault = 'SPI3_STI3_' + modeShort + '_' + ymCompact(targetStart) + '_to_' + ymCompact(addMonths(targetEnd, -1));
    var currentName = filenameInput.getValue();
    if (!currentName || currentName === state.exportDefaultName) filenameInput.setValue(newDefault);
    state.exportDefaultName = newDefault;

    filenameInput.setDisabled(false);
    btnExport.setDisabled(false);
    btnExport.setLabel('Generate Download Link (GeoTIFF)');
    exportLinkPanel.clear();

    var shortBaseline = Math.min(idx.nChirps, idx.nEra) < WARN_BASELINE_YEARS;
    btnRenderBivariate.setLabel('Re-render Bivariate Map');
    setStatus(bivariateStatus,
      modeShort + ' map for ' + windowText + (isForecast ? ' (persisted from ' + monthLabel(obsStart) + ' – ' + monthLabel(addMonths(obsEnd, -1)) + ')' : '') +
      '. Tiles load progressively. Baseline years — SPI: ' + idx.nChirps + ', STI: ' + idx.nEra + '.' +
      (shortBaseline ? ' Warning: fewer than ' + WARN_BASELINE_YEARS + ' baseline years; interpret with caution.' : ''),
      shortBaseline ? 'warning' : 'primary');
  });
}

btnRenderBivariate.onClick(function() {
  runBivariateAnalysis(modeSelect.getValue(), parseInt(yearSelect.getValue(), 10), monthSelect.getValue());
});

// ==============================================================================
// SCRIPT SECTION 12. DIRECT DOWNLOAD OF MAP
// ==============================================================================

function tryDownload(name, attempt) {
  var scale = EXPORT_SCALES[attempt];
  btnExport.setLabel('Preparing link at ' + scale + ' m...');

  state.exportImage.getDownloadURL({
    name: name,
    region: state.boundaryRegion,
    scale: scale,
    crs: 'EPSG:4326',
    format: 'ZIPPED_GEO_TIFF',
    filePerBand: false
  }, function(url, err) {
    if (err) {
      var retryable = /less than or equal|too large|Pixel grid|memory|timed out|Too many/i.test(err);
      if (retryable && attempt + 1 < EXPORT_SCALES.length) {
        exportLinkPanel.clear();
        exportLinkPanel.add(ui.Label('Too large at ' + scale + ' m, retrying at ' + EXPORT_SCALES[attempt + 1] + ' m...', {
          fontSize: '11px', color: app.colors.warning, margin: '4px 0', backgroundColor: app.colors.bg
        }));
        tryDownload(name, attempt + 1);
        return;
      }
      btnExport.setDisabled(false);
      btnExport.setLabel('Generate Download Link (GeoTIFF)');
      exportLinkPanel.clear();
      exportLinkPanel.add(ui.Label('Download failed: ' + err, {fontSize: '11px', color: app.colors.alert, margin: '4px 0', backgroundColor: app.colors.bg}));
      return;
    }

    btnExport.setDisabled(false);
    btnExport.setLabel('Generate Download Link (GeoTIFF)');
    exportLinkPanel.clear();
    exportLinkPanel.add(ui.Label({
      value: '⬇ Download ' + name + '.zip (GeoTIFF, ' + scale + ' m)',
      style: {fontWeight: 'bold', color: app.colors.primary, fontSize: '12px', margin: '8px 0 2px 0', backgroundColor: app.colors.bg},
      targetUrl: url
    }));
    exportLinkPanel.add(ui.Label('Bands: SPI3, STI3, SPI_Class, STI_Class, Bivariate_Code. Link is temporary; generate a new one if it expires.', {
      fontSize: '10px', color: app.colors.shade, margin: '0 0 6px 0', backgroundColor: app.colors.bg
    }));
  });
}

btnExport.onClick(function() {
  if (!state.exportImage) return;
  var name = sanitizeFilename(filenameInput.getValue()) || state.exportDefaultName;
  filenameInput.setValue(name);
  btnExport.setDisabled(true);
  exportLinkPanel.clear();
  tryDownload(name, 0);
});

// ==============================================================================
// SCRIPT SECTION 13. STARTUP: DATASET AVAILABILITY & SENSIBLE DEFAULTS
// ==============================================================================

withDataInfo(function(info, err) {
  if (!info) { setStatus(dataStatusLabel, 'Dataset availability check failed: ' + err, 'alert'); return; }

  var rainTxt;
  if (info.useFinal) {
    rainTxt = 'CHIRPS Final: ' + monthLabel(info.chirpsStart) + ' to ' + monthLabel(addMonths(info.finalEnd, -1));
    if (info.usePrelim && ymToMillis(info.chirpsEnd) > ymToMillis(info.finalEnd)) {
      rainTxt += ' & CHIRPS Prelim: ' + monthLabel(info.finalEnd) + ' to ' + monthLabel(addMonths(info.chirpsEnd, -1));
    }
  } else {
    rainTxt = 'CHIRPS preliminary only: ' + monthLabel(info.chirpsStart) + ' to ' + monthLabel(addMonths(info.chirpsEnd, -1));
  }
  dataStatusLabel.setValue('Complete months — ' + rainTxt +
    ' · AgERA5: ' + monthLabel(info.eraStart) + ' to ' + monthLabel(addMonths(info.eraEnd, -1)) +
    (info.prelimGap ? ' · WARNING: preliminary series does not connect to the final series (gap), so it is ignored; recent months are unavailable.' : ''));
  if (info.prelimGap) dataStatusLabel.style().set('color', app.colors.alert);

  // Default the Phase 3 picker to the latest month with complete CHIRPS + AgERA5 data
  var latest = addMonths(minYm(info.chirpsEnd, info.eraEnd), -1);
  if (yearList.indexOf(String(latest.y)) !== -1) {
    yearSelect.setValue(String(latest.y));
    monthSelect.setValue(latest.m);
  }
  print('Dataset coverage (first / last complete month):', {
    CHIRPS_merged:  ymCompact(info.chirpsStart) + ' / ' + ymCompact(addMonths(info.chirpsEnd, -1)),
    CHIRPS_final_through: info.useFinal ? ymCompact(addMonths(info.finalEnd, -1)) : 'n/a',
    CHIRPS_prelim_used:   info.usePrelim,
    AgERA5:         ymCompact(info.eraStart) + ' / ' + ymCompact(addMonths(info.eraEnd, -1))
  });
});

print('Unified SPI-STI Drought Monitoring Tool (V9: CHIRPS final + preliminary, AgERA5) successfully initialized.');
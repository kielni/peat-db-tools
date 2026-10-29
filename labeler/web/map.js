// Filled in by `make render`.
const MAPTILER_KEY = "__MAPTILER_API_KEY__";
const API_URL = "__API_URL__";

// 7.5 arcsecond grid: every cell is 7.5″ of latitude by 7.5″ of longitude,
// about 231 m tall and narrower away from the equator.
const CELL_DEG = 7.5 / 3600;
const MAX_LAT = 85;

const GRID_MIN_ZOOM = 13;
const SEARCH_ZOOM = 14;
const FLUSH_MS = 2500;
const MAX_GRID_CELLS = 20000;

// Okabe–Ito colors, distinguishable with common color vision deficiencies.
// "conflict" is display only: the show-all view's cells where contributors disagree.
const COLORS = { peat: "#E69F00", "not peat": "#0072B2", conflict: "#CC79A7" };

// Below GRID_MIN_ZOOM each class gets its own cluster markers, nudged apart (px)
// so markers of different classes at the same place don't hide each other.
// Text colors keep the count readable on each fill.
const CLUSTERS = {
  "not peat": { offset: [-11, 0], text: "#ffffff" },
  peat: { offset: [11, 0], text: "#000000" },
  conflict: { offset: [0, -18], text: "#000000" },
};

function rowFor(lat) {
  return Math.floor(lat / CELL_DEG);
}
function colFor(lng) {
  return Math.floor(lng / CELL_DEG);
}

// Cell center as [longitude, latitude], rounded to 0.1 m
function cellCenter(row, col) {
  return [col, row].map((index) => Number(((index + 0.5) * CELL_DEG).toFixed(6)));
}

function clusterSource(label) {
  return `clusters-${label.replace(" ", "-")}`;
}

function cellPolygon(row, col, properties = {}) {
  const [s, w] = [row * CELL_DEG, col * CELL_DEG];
  const [n, e] = [s + CELL_DEG, w + CELL_DEG];
  return {
    type: "Feature",
    properties,
    geometry: {
      type: "Polygon",
      coordinates: [
        [
          [w, s],
          [e, s],
          [e, n],
          [w, n],
          [w, s],
        ],
      ],
    },
  };
}

function collection(features) {
  return { type: "FeatureCollection", features };
}

const params = new URLSearchParams(location.search);
const KEY = params.get("key");
// Optional start point, e.g. ?key=…&lat=-3.5&lng=-62.1; ignored unless both are valid
const START = [params.get("lng"), params.get("lat")].map((value) =>
  value ? Number(value) : NaN,
);
const hasStart =
  START.every(Number.isFinite) &&
  Math.abs(START[0]) <= 180 &&
  Math.abs(START[1]) <= MAX_LAT;
const marked = new Map(); // cell_id ("row_col") -> label drawn on the map
const touched = new Set(); // cells clicked on this page, so loading won't overwrite them
const queue = []; // labels not yet saved
let mode = "not peat";
let showAll = false; // set by the load response for SHOW_ALL_KEY

const hint = document.getElementById("hint");
const statusBox = document.getElementById("status");
const modeButtons = document.querySelectorAll("#modes button");
const doneButton = document.getElementById("done");

function setStatus(text, isError = false) {
  statusBox.textContent = text;
  statusBox.classList.toggle("error", isError);
  statusBox.hidden = false;
}

for (const swatch of document.querySelectorAll(".swatch")) {
  const label = swatch.dataset.label ?? swatch.closest("[data-label]")?.dataset.label;
  if (label) swatch.style.background = COLORS[label];
}

function setMode(label) {
  mode = label;
  for (const button of modeButtons) {
    button.setAttribute("aria-pressed", button.dataset.label === mode);
  }
  drawGrid();
}

for (const button of modeButtons) {
  button.addEventListener("click", () => setMode(button.dataset.label));
}

if (!KEY) {
  const error = document.getElementById("error");
  error.textContent = "This page needs your personal link (…/?key=…).";
  error.hidden = false;
  doneButton.hidden = true;
}

const map = new maplibregl.Map({
  container: "map",
  style: `https://api.maptiler.com/maps/hybrid/style.json?key=${MAPTILER_KEY}`,
  center: hasStart ? START : [0, 20],
  zoom: hasStart ? SEARCH_ZOOM : 1.5,
});
map.addControl(new maplibregl.NavigationControl(), "top-right");

const labelColor = (fallback) => [
  "match",
  ["get", "label"],
  "peat",
  COLORS["peat"],
  "not peat",
  COLORS["not peat"],
  "conflict",
  COLORS["conflict"],
  fallback,
];

map.on("load", () => {
  map.addSource("grid", { type: "geojson", data: collection([]) });
  map.addSource("marked", { type: "geojson", data: collection([]) });
  map.addLayer({
    id: "grid-lines",
    type: "line",
    source: "grid",
    minzoom: GRID_MIN_ZOOM,
    paint: { "line-color": "#ffffff", "line-opacity": 0.6, "line-width": 1 },
  });
  // Fill plus a solid outline so labeled cells read over any imagery;
  // unknown cells (show-all view only) get the outline alone.
  map.addLayer({
    id: "marked-fill",
    type: "fill",
    source: "marked",
    minzoom: GRID_MIN_ZOOM,
    paint: {
      "fill-color": labelColor("#ffffff"),
      "fill-opacity": ["match", ["get", "label"], "unknown", 0, 0.45],
    },
  });
  map.addLayer({
    id: "marked-outline",
    type: "line",
    source: "marked",
    minzoom: GRID_MIN_ZOOM,
    paint: { "line-color": labelColor("#ffffff"), "line-width": 2 },
  });
  for (const [label, { offset, text }] of Object.entries(CLUSTERS)) {
    const source = clusterSource(label);
    map.addSource(source, {
      type: "geojson",
      data: collection([]),
      cluster: true,
      clusterRadius: 40,
      clusterMaxZoom: GRID_MIN_ZOOM - 1,
    });
    map.addLayer({
      id: `${source}-circle`,
      type: "circle",
      source,
      maxzoom: GRID_MIN_ZOOM,
      paint: {
        "circle-color": COLORS[label],
        "circle-radius": [
          "step",
          ["coalesce", ["get", "point_count"], 1],
          11,
          10,
          14,
          100,
          18,
        ],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 2,
        "circle-translate": offset,
      },
    });
    map.addLayer({
      id: `${source}-count`,
      type: "symbol",
      source,
      maxzoom: GRID_MIN_ZOOM,
      layout: {
        // Unclustered points have no point_count
        "text-field": ["coalesce", ["get", "point_count_abbreviated"], "1"],
        "text-font": ["Noto Sans Bold"], // a font the MapTiler style serves
        "text-size": 12,
        "text-allow-overlap": true,
        "text-ignore-placement": true,
      },
      paint: { "text-color": text, "text-translate": offset },
    });
  }
  drawGrid();
  drawMarked();
});
map.on("moveend", drawGrid);
map.on("click", labelCell);

function drawGrid() {
  const zoomedIn = map.getZoom() >= GRID_MIN_ZOOM;
  const labeled = [...marked.values()].filter((label) => label !== "unknown").length;
  hint.textContent = !zoomedIn
    ? "Search or zoom in to see the 7.5″ grid."
    : showAll
      ? `Showing labels from all contributors: ${labeled} cells.`
      : `Click a cell to mark it ${mode}; click a ${mode} cell again to clear it. ` +
        `${labeled} labeled.`;
  map.getCanvas().style.cursor = zoomedIn && KEY ? "crosshair" : "";
  if (!zoomedIn) return; // grid layer is hidden by minzoom

  const bounds = map.getBounds();
  const south = Math.max(bounds.getSouth(), -MAX_LAT);
  const north = Math.min(bounds.getNorth(), MAX_LAT);
  const [west, east] = [colFor(bounds.getWest()), colFor(bounds.getEast())];
  const features = [];
  for (let row = rowFor(south); row <= rowFor(north); row++) {
    for (let col = west; col <= east; col++) {
      features.push(cellPolygon(row, col));
      if (features.length > MAX_GRID_CELLS) return;
    }
  }
  map.getSource("grid").setData(collection(features));
}

function drawMarked() {
  const features = [...marked].map(([id, label]) => {
    const [row, col] = id.split("_").map(Number);
    return cellPolygon(row, col, { label });
  });
  map.getSource("marked")?.setData(collection(features)); // undefined until map load
  for (const label of Object.keys(CLUSTERS)) {
    const points = [...marked]
      .filter(([, value]) => value === label)
      .map(([id]) => {
        const [row, col] = id.split("_").map(Number);
        return {
          type: "Feature",
          properties: {},
          geometry: { type: "Point", coordinates: cellCenter(row, col) },
        };
      });
    map.getSource(clusterSource(label))?.setData(collection(points));
  }
  drawGrid();
}

function labelCell(event) {
  if (!KEY || map.getZoom() < GRID_MIN_ZOOM) return;
  const { lat, lng } = event.lngLat.wrap();
  if (Math.abs(lat) > MAX_LAT) return;
  const [row, col] = [rowFor(lat), colFor(lng)];
  const id = `${row}_${col}`;
  const label = marked.get(id) === mode ? "unknown" : mode;
  if (label === "unknown") marked.delete(id);
  else marked.set(id, label);
  touched.add(id);
  const [longitude, latitude] = cellCenter(row, col);
  queue.push({ cell_id: id, latitude, longitude, label });
  drawMarked();
  setStatus("Saving…");
}

// All requests go to the same function URL; a 403 stops further requests.
let stopped = false;

async function post(body) {
  const res = await fetch(API_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: KEY, ...body }),
  });
  if (res.status === 403) {
    stopped = true;
    setStatus("This link is not valid, so labels are not being saved.", true);
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// Combine the labels for one cell: one label per contributor, unknown ignored
// unless it is all there is.
function combine(labels) {
  const known = new Set(labels.filter((label) => label !== "unknown"));
  if (known.size === 0) return "unknown";
  return known.size === 1 ? [...known][0] : "conflict";
}

async function loadLabels() {
  try {
    const data = await post({ action: "load" });
    showAll = data.show_all;
    const byCell = new Map();
    for (const { cell_id, label } of data.labels) {
      byCell.set(cell_id, [...(byCell.get(cell_id) ?? []), label]);
    }
    for (const [id, labels] of byCell) {
      const label = combine(labels);
      if (touched.has(id) || (label === "unknown" && !showAll)) continue;
      marked.set(id, label);
    }
    document.getElementById("legend").hidden = !showAll;
    doneButton.hidden = showAll; // SHOW_ALL_KEY has no row in the keys sheet
    drawMarked();
  } catch (err) {
    if (!stopped)
      setStatus("Could not load your earlier labels; reload to try again.", true);
    console.warn(err);
  }
}
if (KEY) loadLabels();

// Send queued labels every FLUSH_MS; on failure keep them and retry next time.
let saving = null; // the save in progress, so callers can wait for it

function flush() {
  saving ??= saveQueue().finally(() => {
    saving = null;
  });
  return saving;
}

async function saveQueue() {
  if (stopped || !queue.length) return;
  const batch = queue.slice();
  try {
    await post({ action: "save", rows: batch });
    queue.splice(0, batch.length);
    setStatus(queue.length ? "Saving…" : "Saved ✓");
  } catch (err) {
    if (!stopped) setStatus("Retrying…", true);
    console.warn(err);
  }
}
setInterval(flush, FLUSH_MS);

// "I'm done" saves any queued labels, then records the time in the keys sheet.
// Labeling stays enabled afterwards.
doneButton.addEventListener("click", async () => {
  doneButton.disabled = true;
  try {
    // Keep saving while each pass makes progress (clicks can queue more labels)
    let before;
    do {
      before = queue.length;
      await flush();
    } while (queue.length && queue.length < before && !stopped);
    if (queue.length) throw new Error("labels not saved");
    await post({ action: "done" });
    setStatus("Thanks, recorded ✓");
  } catch (err) {
    if (!stopped) setStatus("Could not record that you're done; try again.", true);
    console.warn(err);
  } finally {
    doneButton.disabled = false;
  }
});

// Place search with the MapTiler Geocoding API.
const results = document.getElementById("results");

function addResult(text, onPick) {
  const item = document.createElement("li");
  item.textContent = text;
  if (onPick) item.addEventListener("click", onPick);
  else item.className = "empty";
  results.append(item);
}

document.getElementById("search").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = document.getElementById("query").value.trim();
  results.replaceChildren();
  if (!query) return;
  const url =
    `https://api.maptiler.com/geocoding/${encodeURIComponent(query)}.json` +
    `?key=${MAPTILER_KEY}&language=en&limit=5`;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data.features?.length) addResult("No matches");
    for (const feature of data.features ?? []) {
      addResult(feature.place_name, () => {
        results.replaceChildren();
        map.flyTo({ center: feature.center, zoom: SEARCH_ZOOM });
      });
    }
  } catch (err) {
    addResult("Search failed; try again.");
    console.warn(err);
  }
});

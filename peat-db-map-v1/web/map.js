// Filled in by `make render` from local.env and scripts/config.py.
const MAPTILER_KEY = "__MAPTILER_API_KEY__";
const CHUNK_SIZE = __CHUNK_SIZE__;
const CLUSTER_MAX_ZOOM = __CLUSTER_MAX_ZOOM__;
const CLUSTER_RADIUS_PX = __CLUSTER_RADIUS_PX__;

const DATA_URL = "data";
// clusters_world.json holds zooms below this; clusters.json the rest.
const WORLD_MAX_ZOOM = 3;
// Client-side clusters stay on up to the map's maximum zoom, so cores at
// identical coordinates remain one cluster.
const MAX_ZOOM = 18;

// Okabe–Ito colors, distinguishable with common color vision deficiencies.
const CLUSTER_COLOR = "#0072B2";
const LOCATION_COLOR = "#CC79A7"; // several cores at the same coordinates
const CORE_COLOR = "#E69F00";
const NON_PEAT_COLOR = "#F0E442"; // peat_measurement false (WoSIS profiles)
const ERROR_COLOR = "#E41A1C"; // error_found true; red, distinct from orange
const CROSS_COLOR = "#D7191C"; // card cross before a false flag

// Card fields after the coordinates, in display order: [row key, label].
const CARD_FIELDS = [
  ["depth_cm", "Peat depth (cm)"],
  ["sample_date", "Sample date"],
  ["site_condition", "Site condition"],
];
const card = document.getElementById("card");
const cardTitle = document.getElementById("card-title");
const cardFields = document.getElementById("card-fields");
const cardFlags = document.getElementById("card-flags");
const cardLoading = document.getElementById("card-loading");
const pager = document.getElementById("card-pager");
const pagerLabel = document.getElementById("pager-label");
const pagerPrev = document.getElementById("pager-prev");
const pagerNext = document.getElementById("pager-next");

const chunks = new Map(); // chunk number -> Promise of card rows
let datasets = null; // Promise of datasets.json, fetched on first click
let markersRequest = 0; // id of the latest markers.fgb query
let cardRequest = 0; // id of the latest card shown
// Group of cores the pager steps through: their core_ids, the page shown
// (0 is the summary, 1 to coreIds.length are the cores), and a function that
// shows the summary. A group is a location, or a cluster too dense to split.
let group = null;

function collection(features) {
  return { type: "FeatureCollection", features };
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

// Count label that matches the client-side clusters' point_count_abbreviated:
// 1234 -> "1.2k", 12345 -> "12k".
function countLabel(count) {
  return [
    "case",
    [">=", count, 10000],
    ["concat", ["to-string", ["round", ["/", count, 1000]]], "k"],
    [">=", count, 1000],
    ["concat", ["to-string", ["/", ["round", ["/", count, 100]], 10]], "k"],
    ["to-string", count],
  ];
}

// The largest circle, about 38 px across, fits within CLUSTER_RADIUS_PX.
function clusterRadius(count) {
  return ["step", count, 10, 10, 13, 100, 16, 1000, 19];
}

function addClusterLayers(id, source, count, extra, color = CLUSTER_COLOR) {
  map.addLayer({
    id: `${id}-circle`,
    type: "circle",
    source,
    ...extra,
    paint: {
      "circle-color": color,
      "circle-radius": clusterRadius(count),
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 2,
    },
  });
  map.addLayer({
    id: `${id}-count`,
    type: "symbol",
    source,
    ...extra,
    layout: {
      "text-field": countLabel(count),
      "text-font": ["Noto Sans Bold"], // a font the MapTiler style serves
      "text-size": 12,
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: { "text-color": "#ffffff" },
  });
}

// One pair of layers per pre-built zoom level, each shown only at its zoom.
// Preprocess stores a single core once, at the lowest zoom in its file where
// it stands alone, so each level also shows single cores from lower zooms.
function addPrebuiltLayers(source, minZoom, maxZoom) {
  for (let zoom = minZoom; zoom < maxZoom; zoom++) {
    addClusterLayers(`prebuilt-${zoom}`, source, ["get", "count"], {
      filter: [
        "any",
        ["==", ["get", "zoom"], zoom],
        ["all", ["==", ["get", "count"], 1], ["<", ["get", "zoom"], zoom]],
      ],
      minzoom: zoom,
      maxzoom: zoom + 1,
    });
  }
}

const prebuiltCircleLayers = Array.from(
  { length: CLUSTER_MAX_ZOOM },
  (_, zoom) => `prebuilt-${zoom}-circle`,
);

maptilersdk.config.apiKey = MAPTILER_KEY;

// Optional ?lat=y&lng=x in the page URL opens the map zoomed in on that point;
// otherwise it opens on the world view.
function startView() {
  const params = new URLSearchParams(window.location.search);
  const lat = parseFloat(params.get("lat"));
  const lng = parseFloat(params.get("lng"));
  if (Math.abs(lat) <= 90 && Math.abs(lng) <= 180) {
    return { center: [lng, lat], zoom: MAX_ZOOM };
  }
  return { center: [0, 20], zoom: 1.5 };
}

// The SDK adds a navigation control at top right by default.
const map = new maptilersdk.Map({
  container: "map",
  style: maptilersdk.MapStyle.HYBRID,
  // Preprocess clusters in Web Mercator; keep the map in it too.
  projection: "mercator",
  ...startView(),
  maxZoom: MAX_ZOOM,
  // Name the data release in the attribution line at the bottom of the map.
  customAttribution:
    '<a href="https://zenodo.org/records/15530645" target="_blank">Peat-DBase version 1.0</a>',
});

map.on("load", () => {
  map.addSource("clusters-world", {
    type: "geojson",
    data: `${DATA_URL}/clusters_world.json`,
  });
  addPrebuiltLayers("clusters-world", 0, WORLD_MAX_ZOOM);

  // Load the larger file only after the world view has drawn.
  map.once("idle", () => {
    map.addSource("clusters", {
      type: "geojson",
      data: `${DATA_URL}/clusters.json`,
    });
    addPrebuiltLayers("clusters", WORLD_MAX_ZOOM, CLUSTER_MAX_ZOOM);
  });

  // Each point is one location: a core, or several cores at the same
  // coordinates with their count. Clusters sum the counts into `cores`.
  map.addSource("cores", {
    type: "geojson",
    data: collection([]),
    cluster: true,
    clusterRadius: CLUSTER_RADIUS_PX,
    clusterMaxZoom: MAX_ZOOM,
    clusterProperties: { cores: ["+", ["get", "count"]] },
  });
  addClusterLayers("cores-cluster", "cores", ["get", "cores"], {
    filter: ["has", "point_count"],
    minzoom: CLUSTER_MAX_ZOOM,
  });
  addClusterLayers(
    "cores-location",
    "cores",
    ["get", "count"],
    {
      filter: ["all", ["!", ["has", "point_count"]], [">", ["get", "count"], 1]],
      minzoom: CLUSTER_MAX_ZOOM,
    },
    LOCATION_COLOR,
  );
  map.addLayer({
    id: "cores-point",
    type: "circle",
    source: "cores",
    filter: ["all", ["!", ["has", "point_count"]], ["==", ["get", "count"], 1]],
    minzoom: CLUSTER_MAX_ZOOM,
    paint: {
      "circle-color": [
        "case",
        ["to-boolean", ["get", "error_found"]],
        ERROR_COLOR,
        ["to-boolean", ["get", "peat_measurement"]],
        CORE_COLOR,
        NON_PEAT_COLOR,
      ],
      "circle-radius": 6,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 2,
    },
  });

  loadMarkers();
});

map.on("moveend", loadMarkers);
map.on("click", handleClick);
document.getElementById("card-close").addEventListener("click", closeCard);
pagerPrev.addEventListener("click", () => showGroupPage(group.page - 1));
pagerNext.addEventListener("click", () => showGroupPage(group.page + 1));

// Merge cores at identical coordinates into one point per location, with
// the location's core count and its lowest core_id. Preprocess numbers a
// location's cores consecutively, so that is the group's first core.
function locations(features) {
  const byCoordinates = new Map();
  for (const feature of features) {
    const key = feature.geometry.coordinates.join(",");
    const location = byCoordinates.get(key);
    if (location) {
      location.properties.count += 1;
      location.properties.core_id = Math.min(
        location.properties.core_id,
        feature.properties.core_id,
      );
    } else {
      feature.properties.count = 1;
      byCoordinates.set(key, feature);
    }
  }
  return [...byCoordinates.values()];
}

// Load the cores in view from markers.fgb at the cluster max zoom and above.
async function loadMarkers() {
  if (map.getZoom() < CLUSTER_MAX_ZOOM) return;
  const request = ++markersRequest;
  const bounds = map.getBounds();
  const rect = {
    minX: Math.max(bounds.getWest(), -180),
    minY: bounds.getSouth(),
    maxX: Math.min(bounds.getEast(), 180),
    maxY: bounds.getNorth(),
  };
  const features = [];
  try {
    for await (const feature of flatgeobuf.deserialize(
      `${DATA_URL}/markers.fgb`,
      rect,
    )) {
      if (request !== markersRequest) return; // a later move replaced this one
      features.push(feature);
    }
  } catch (error) {
    console.error("Could not load markers", error);
    return;
  }
  if (request === markersRequest) {
    map.getSource("cores").setData(collection(locations(features)));
  }
}

function handleClick(event) {
  const { lat, lng } = event.lngLat;
  console.log(`click at ${lat}, ${lng}, zoom ${map.getZoom().toFixed(2)}`);
  const [core] = map.queryRenderedFeatures(event.point, {
    layers: ["cores-point"],
  });
  if (core) {
    group = null;
    showCoreCard(core.properties.core_id);
    return;
  }
  const [location] = map.queryRenderedFeatures(event.point, {
    layers: ["cores-location-circle"],
  });
  if (location) {
    const { core_id, count } = location.properties;
    openGroup(coreRange(core_id, count), () => showLocationSummary(core_id, count));
    return;
  }
  const [cluster] = map.queryRenderedFeatures(event.point, {
    layers: ["cores-cluster-circle"],
  });
  if (cluster) {
    expandCluster(cluster);
    return;
  }
  const loaded = prebuiltCircleLayers.filter((id) => map.getLayer(id));
  const [prebuilt] = map.queryRenderedFeatures(event.point, { layers: loaded });
  if (prebuilt) {
    const { count, west, south, east, north } = prebuilt.properties;
    if (count > 1) {
      // Bounds of the cluster's cores, recorded by preprocess
      fitCores([
        [west, south],
        [east, north],
      ]);
    } else {
      // A single core becomes clickable at the cluster max zoom.
      map.easeTo({
        center: prebuilt.geometry.coordinates,
        zoom: CLUSTER_MAX_ZOOM,
      });
    }
    return;
  }
  closeCard();
}

// Zoom the map to fit the bounds of a cluster's cores, leaving room for the
// circles at the edges.
function fitCores(bounds) {
  map.fitBounds(bounds, { padding: CLUSTER_RADIUS_PX, maxZoom: MAX_ZOOM });
}

// Zoom in to split a cluster. A cluster that only splits past the map's
// maximum zoom (locations a few meters apart) opens in the pager instead.
async function expandCluster(cluster) {
  const { cluster_id, point_count, cores } = cluster.properties;
  const source = map.getSource("cores");
  // Ask for every location in the cluster (the default limit is 10).
  const [leaves, expansionZoom] = await Promise.all([
    source.getClusterLeaves(cluster_id, point_count),
    source.getClusterExpansionZoom(cluster_id),
  ]);
  if (expansionZoom > MAX_ZOOM) {
    const coreIds = leaves
      .flatMap((leaf) => coreRange(leaf.properties.core_id, leaf.properties.count))
      .sort((a, b) => a - b);
    const [lng, lat] = cluster.geometry.coordinates;
    openGroup(coreIds, () => showClusterSummary(cores, leaves.length, lat, lng));
    return;
  }
  const bounds = new maptilersdk.LngLatBounds();
  for (const leaf of leaves) bounds.extend(leaf.geometry.coordinates);
  fitCores(bounds);
}

// core_ids first to first + count - 1: a location's cores, numbered
// consecutively by preprocess.
function coreRange(first, count) {
  return Array.from({ length: count }, (_, i) => first + i);
}

function chunkFor(coreId) {
  const n = Math.floor(coreId / CHUNK_SIZE);
  if (!chunks.has(n)) {
    const request = fetchJson(`${DATA_URL}/cards/${n}.json`);
    // Forget failed requests so a later click can retry.
    request.catch(() => chunks.delete(n));
    chunks.set(n, request);
  }
  return chunks.get(n);
}

function datasetList() {
  if (!datasets) {
    datasets = fetchJson(`${DATA_URL}/datasets.json`);
    datasets.catch(() => {
      datasets = null;
    });
  }
  return datasets;
}

// Return the card row for a core and the dataset list.
async function cardData(coreId) {
  const [rows, allDatasets] = await Promise.all([chunkFor(coreId), datasetList()]);
  return [rows[coreId % CHUNK_SIZE], allDatasets];
}

// Show "Loading…" in the card and return an id that later steps check, so
// a slow load cannot overwrite a card the reader opened after it.
function startCard() {
  cardTitle.textContent = "Loading…";
  cardFields.replaceChildren();
  cardFlags.replaceChildren();
  cardLoading.hidden = false;
  card.hidden = false;
  updatePager();
  return ++cardRequest;
}

// A flag line with a red cross, for a property the core lacks.
function crossFlag(label) {
  const mark = document.createElement("span");
  mark.className = "flag-mark";
  mark.textContent = "✗";
  mark.style.color = CROSS_COLOR;
  const item = document.createElement("li");
  item.append(mark, ` ${label}`);
  return item;
}

// A flag line in red text, for a problem with the core.
function alertFlag(label) {
  const item = document.createElement("li");
  item.textContent = label;
  item.style.color = ERROR_COLOR;
  return item;
}

// Fields are [label, value] pairs; flags are list items from crossFlag or
// alertFlag, listed above the fields.
function renderCard(title, fields, flags = []) {
  cardLoading.hidden = true;
  cardTitle.textContent = title;
  cardFlags.append(...flags);
  for (const [label, value] of fields) {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = value === "" || value == null ? "—" : String(value);
    cardFields.append(term, detail);
  }
}

function renderError(error) {
  console.error("Could not load card", error);
  cardLoading.hidden = true;
  cardTitle.textContent = "";
  const detail = document.createElement("dd");
  detail.textContent = "Could not load these details.";
  cardFields.append(detail);
}

function coordinates(row) {
  return `${row.lat}, ${row.lon}`;
}

// "a – b", or one value when both ends match.
function rangeText([low, high]) {
  return low === high ? String(low) : `${low} – ${high}`;
}

// Describe a core's relationship to cores at the same coordinates and to
// rows of the same sample that preprocess dropped.
function relatedText(row) {
  const duplicates = row.also_reported_in?.length ?? 0;
  if (!row.location_count && !duplicates) return null;
  const location = row.location_count
    ? `Duplicate location (core ${row.core_id - row.location_first + 1} of ` +
      `${row.location_count.toLocaleString()})`
    : "Unique location";
  const sample = duplicates
    ? `duplicate sample (represents ${duplicates + 1} records)`
    : "unique sample";
  return `${location}, ${sample}`;
}

// Dataset names of the rows a sample representative replaces.
function alsoReportedIn(row, allDatasets) {
  if (!row.also_reported_in) return null;
  const names = row.also_reported_in.map(([dataset]) => allDatasets[dataset].name);
  return [...new Set(names)].join("; ");
}

async function showCoreCard(coreId) {
  const request = startCard();
  try {
    const [row, allDatasets] = await cardData(coreId);
    if (request !== cardRequest) return;
    const dataset = allDatasets[row.dataset];
    const fields = [
      ["Coordinates", coordinates(row)],
      ...CARD_FIELDS.map(([key, label]) => [label, row[key]]),
    ];
    const related = relatedText(row);
    if (related) fields.push(["Related", related]);
    fields.push(["Dataset", dataset.name]);
    const also = alsoReportedIn(row, allDatasets);
    if (also) fields.push(["Also reported in", also]);
    fields.push(["Source notes", dataset.source_notes]);
    const flags = [];
    if (["False", "0"].includes(row.peat_measurement)) {
      flags.push(crossFlag("No peat measured"));
    }
    if (row.error_found === "True") flags.push(alertFlag("Error found"));
    renderCard(`Sample ID ${row.sample_id}`, fields, flags);
  } catch (error) {
    if (request === cardRequest) renderError(error);
  }
}

// Summary of a location group, stored on the card of its first core.
async function showLocationSummary(first, count) {
  const request = startCard();
  try {
    const [row, allDatasets] = await cardData(first);
    if (request !== cardRequest) return;
    const summary = row.location_summary;
    renderCard(`${count.toLocaleString()} cores at one location`, [
      ["Coordinates", coordinates(row)],
      ["Datasets", summary.datasets.map((i) => allDatasets[i].name).join("; ")],
      ["Peat depth (cm)", summary.depth_cm && rangeText(summary.depth_cm)],
      ["Sample date", summary.sample_date && rangeText(summary.sample_date)],
    ]);
  } catch (error) {
    if (request === cardRequest) renderError(error);
  }
}

// Summary of a cluster whose locations are too close to split on the map.
function showClusterSummary(coreCount, locationCount, lat, lng) {
  startCard();
  renderCard(`${coreCount.toLocaleString()} cores within a few meters`, [
    ["Center", `${lat}, ${lng}`],
    ["Locations", locationCount.toLocaleString()],
    ["Note", "These cores sit too close together to show separately on the map."],
  ]);
}

function openGroup(coreIds, showSummary) {
  group = { coreIds, page: 0, showSummary };
  showGroupPage(0);
}

// Page 0 of a group is its summary; pages 1 to coreIds.length are its cores.
function showGroupPage(page) {
  group.page = page;
  if (page === 0) group.showSummary();
  else showCoreCard(group.coreIds[page - 1]);
}

function updatePager() {
  pager.hidden = !group;
  if (!group) return;
  const { page } = group;
  const count = group.coreIds.length;
  pagerLabel.textContent =
    page === 0 ? "Summary" : `${page.toLocaleString()} of ${count.toLocaleString()}`;
  pagerPrev.disabled = page === 0;
  pagerNext.disabled = page === count;
}

function closeCard() {
  card.hidden = true;
  group = null;
}

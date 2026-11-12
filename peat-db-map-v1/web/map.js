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
const CORE_COLOR = "#E69F00";

// Card fields in display order: [row key, label].
const CARD_FIELDS = [
  ["sample_id", "Sample ID"],
  ["lat", "Latitude"],
  ["lon", "Longitude"],
  ["depth_cm", "Peat depth (cm)"],
  ["sample_date", "Sample date"],
  ["site_condition", "Site condition"],
  ["peat_measurement", "Peat measurement"],
  ["error_found", "Error found"],
];

const card = document.getElementById("card");
const cardTitle = document.getElementById("card-title");
const cardFields = document.getElementById("card-fields");

const chunks = new Map(); // chunk number -> Promise of card rows
let datasets = null; // Promise of datasets.json, fetched on first click
let markersRequest = 0; // id of the latest markers.fgb query

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

function addClusterLayers(id, source, count, extra) {
  map.addLayer({
    id: `${id}-circle`,
    type: "circle",
    source,
    ...extra,
    paint: {
      "circle-color": CLUSTER_COLOR,
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

// The SDK adds a navigation control at top right by default.
const map = new maptilersdk.Map({
  container: "map",
  style: maptilersdk.MapStyle.HYBRID,
  // Preprocess clusters in Web Mercator; keep the map in it too.
  projection: "mercator",
  center: [0, 20],
  zoom: 1.5,
  maxZoom: MAX_ZOOM,
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

  map.addSource("cores", {
    type: "geojson",
    data: collection([]),
    cluster: true,
    clusterRadius: CLUSTER_RADIUS_PX,
    clusterMaxZoom: MAX_ZOOM,
  });
  addClusterLayers("cores-cluster", "cores", ["get", "point_count"], {
    filter: ["has", "point_count"],
    minzoom: CLUSTER_MAX_ZOOM,
  });
  map.addLayer({
    id: "cores-point",
    type: "circle",
    source: "cores",
    filter: ["!", ["has", "point_count"]],
    minzoom: CLUSTER_MAX_ZOOM,
    paint: {
      "circle-color": CORE_COLOR,
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
    map.getSource("cores").setData(collection(features));
  }
}

function handleClick(event) {
  const [core] = map.queryRenderedFeatures(event.point, {
    layers: ["cores-point"],
  });
  if (core) {
    showCard(core.properties.core_id);
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
// circles at the edges. Cores at identical coordinates still form one
// cluster at the maximum zoom; listing them is a P2 step.
function fitCores(bounds) {
  map.fitBounds(bounds, { padding: CLUSTER_RADIUS_PX, maxZoom: MAX_ZOOM });
}

async function expandCluster(cluster) {
  const { cluster_id, point_count } = cluster.properties;
  // Ask for every core in the cluster (the default limit is 10).
  const leaves = await map.getSource("cores").getClusterLeaves(cluster_id, point_count);
  const bounds = new maptilersdk.LngLatBounds();
  for (const leaf of leaves) bounds.extend(leaf.geometry.coordinates);
  fitCores(bounds);
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

async function showCard(coreId) {
  cardTitle.textContent = `Core ${coreId}`;
  cardFields.replaceChildren();
  card.hidden = false;
  try {
    const [rows, allDatasets] = await Promise.all([chunkFor(coreId), datasetList()]);
    const row = rows[coreId % CHUNK_SIZE];
    const dataset = allDatasets[row.dataset];
    const fields = [
      ...CARD_FIELDS.map(([key, label]) => [label, row[key]]),
      ["Dataset", dataset.name],
      ["Source notes", dataset.source_notes],
    ];
    cardTitle.textContent = row.sample_id || `Core ${coreId}`;
    for (const [label, value] of fields) {
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = value === "" || value == null ? "—" : String(value);
      cardFields.append(term, detail);
    }
  } catch (error) {
    console.error("Could not load card", error);
    const detail = document.createElement("dd");
    detail.textContent = "Could not load this core's details.";
    cardFields.append(detail);
  }
}

function closeCard() {
  card.hidden = true;
}

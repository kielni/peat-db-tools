"""Convert Peat_DBase.csv into static files for the web map.

Outputs, under the output directory:

- markers.fgb: core points with ``core_id`` and a FlatGeobuf spatial index
- datasets.json: one entry per original dataset with its source notes
- cards/<n>.json: card rows for cores ``n * CHUNK_SIZE`` and up
- clusters_world.json: cluster centroids and counts for zooms 0 to 2, the
  first page load
- clusters.json: cluster centroids and counts for zooms 3 up to
  ``CLUSTER_MAX_ZOOM``

Usage: python preprocess.py INPUT_CSV OUTPUT_DIR
"""

import argparse
import json
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from config import CHUNK_SIZE, CLUSTER_MAX_ZOOM, CLUSTER_RADIUS_PX

log = logging.getLogger(__name__)

# MapTiler SDK draws the world 512 px wide at zoom 0.
TILE_PX = 512
# Web Mercator stops at this latitude.
MAX_LAT = 85.05112878

DATASET_COLUMNS = ["original_dataset", "original_dataset_source_notes"]


def size_text(path: Path) -> str:
    """Return the size of a file, or the total of files in a directory."""
    if path.is_dir():
        size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    else:
        size = path.stat().st_size
    if size < 1_000_000:
        return f"{size / 1_000:,.1f} KB"
    return f"{size / 1_000_000:,.1f} MB"


def read_cores(path: Path) -> gpd.GeoDataFrame:
    """Read the CSV and return rows with valid coordinates as points.

    All columns stay text except ``lat`` and ``lon``. Rows with missing or
    out-of-range coordinates are logged and dropped.
    """
    log.info(f"Reading {path} ({size_text(path)})")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    log.info(f"Read {len(df):,} rows, {len(df.columns)} fields")

    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    valid = df["lat"].between(-90, 90) & df["lon"].between(-180, 180)
    invalid_count = int((~valid).sum())
    if invalid_count:
        log.warning(f"Dropping {invalid_count:,} rows with invalid lat/lon")
        df = df[valid]
    log.info(f"Kept {len(df):,} rows with valid lat/lon")

    return gpd.GeoDataFrame(
        df.reset_index(drop=True),
        geometry=gpd.points_from_xy(df["lon"], df["lat"]),
        crs="EPSG:4326",
    )


def sort_cores(cores: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Sort cores along a Hilbert curve and number them with ``core_id``."""
    distance = cores.hilbert_distance()
    order = np.argsort(distance.to_numpy(), kind="stable")
    sorted_cores = cores.iloc[order].reset_index(drop=True)
    sorted_cores.insert(0, "core_id", np.arange(len(sorted_cores)))
    log.info(f"Sorted {len(sorted_cores):,} cores by Hilbert distance")
    return sorted_cores


def keep_fields(cores: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep only fields the map draws with or the detail card shows.

    Drawing needs ``core_id``, the point, and ``error_found`` (error
    color). The card shows the rest, per the spec's "detail card should
    show" list.
    """
    kept = [
        "core_id",
        "sample_id",
        "lat",
        "lon",
        "depth_cm",
        "sample_date",
        "site_condition",
        "peat_measurement",
        "error_found",
        *DATASET_COLUMNS,
        "geometry",
    ]
    dropped = [column for column in cores.columns if column not in kept]
    log.info(f"Dropping fields: {', '.join(dropped)}")
    log.info(f"Kept {len(kept) - 1} fields plus geometry")
    return cores[kept]


def write_markers(cores: gpd.GeoDataFrame, path: Path) -> None:
    """Write core points with ``core_id`` and a spatial index."""
    cores[["core_id", "geometry"]].to_file(
        path, driver="FlatGeobuf", engine="pyogrio", SPATIAL_INDEX="YES"
    )
    log.info(f"Wrote {len(cores):,} markers to {path} ({size_text(path)})")


def build_datasets(cores: pd.DataFrame) -> tuple[np.ndarray, list[dict]]:
    """Return each core's dataset index and the list of datasets.

    Each dataset keeps the source notes of its first core.
    """
    codes, names = pd.factorize(cores["original_dataset"])
    notes = cores.groupby(codes)["original_dataset_source_notes"].first()
    datasets = [
        {"name": name, "source_notes": notes[i]} for i, name in enumerate(names)
    ]
    return codes, datasets


def write_json(data: object, path: Path) -> None:
    """Write compact JSON."""
    with path.open("w") as f:
        json.dump(data, f, separators=(",", ":"))


def write_cards(
    cores: pd.DataFrame, codes: np.ndarray, out_dir: Path, chunk_size: int
) -> None:
    """Write card rows in chunks of ``chunk_size`` by ``core_id``.

    Card ``core_id`` sits at index ``core_id % chunk_size`` of chunk
    ``core_id // chunk_size``. Each row holds ``dataset``, an index into
    datasets.json, instead of the dataset columns.
    """
    cards = cores.drop(columns=["geometry", *DATASET_COLUMNS])
    cards.insert(1, "dataset", codes)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk_count = 0
    for start in range(0, len(cards), chunk_size):
        chunk = cards.iloc[start : start + chunk_size]
        write_json(
            chunk.to_dict(orient="records"), out_dir / f"{start // chunk_size}.json"
        )
        chunk_count += 1
    log.info(f"Wrote {chunk_count} card chunks to {out_dir} ({size_text(out_dir)})")


def project(lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Project lon/lat to Web Mercator world coordinates in [0, 1].

    ``x`` grows east and ``y`` grows south, as in map pixel coordinates.
    """
    lat_rad = np.radians(np.clip(lat, -MAX_LAT, MAX_LAT))
    x = lon / 360 + 0.5
    y = 0.5 - np.log(np.tan(np.pi / 4 + lat_rad / 2)) / (2 * np.pi)
    return x, y


def unproject(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Convert Web Mercator world coordinates back to lon/lat."""
    lon = (x - 0.5) * 360
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * y))))
    return lon, lat


def merge_clusters(
    x: np.ndarray, y: np.ndarray, count: np.ndarray, radius: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Merge clusters within ``radius`` of each other, Supercluster style.

    Visit clusters in input order. Each unvisited cluster absorbs every
    unvisited cluster within ``radius`` (in world units), and the merged
    cluster sits at the count-weighted centroid of its members. Returns
    the merged clusters' ``x``, ``y`` and ``count``.
    """
    tree = cKDTree(np.column_stack([x, y]))
    visited = np.zeros(len(x), dtype=bool)
    merged_x: list[float] = []
    merged_y: list[float] = []
    merged_count: list[int] = []
    for i in range(len(x)):
        if visited[i]:
            continue
        # Query one cluster at a time: a batch query would build neighbor
        # lists that grow with the square of a dense group's size.
        neighbors = np.array(tree.query_ball_point([x[i], y[i]], radius))
        members = neighbors[~visited[neighbors]]
        visited[members] = True
        weights = count[members]
        total = int(weights.sum())
        merged_x.append(float(x[members] @ weights) / total)
        merged_y.append(float(y[members] @ weights) / total)
        merged_count.append(total)
    return np.array(merged_x), np.array(merged_y), np.array(merged_count)


def build_clusters(
    lon: np.ndarray, lat: np.ndarray, max_zoom: int, radius_px: int
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Return clusters for each zoom below ``max_zoom``, indexed by zoom.

    Each core starts as a cluster of count 1. Working down from
    ``max_zoom - 1`` to 0, merge the previous zoom's clusters, so clusters
    nest across zooms. Each entry holds the clusters' ``lon``, ``lat`` and
    ``count``.
    """
    x, y = project(lon, lat)
    count = np.ones(len(x), dtype=int)
    levels: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for zoom in range(max_zoom - 1, -1, -1):
        radius = radius_px / (TILE_PX * 2**zoom)
        x, y, count = merge_clusters(x, y, count, radius)
        levels.append((*unproject(x, y), count))
        log.info(f"Zoom {zoom}: {len(count):,} clusters")
    return levels[::-1]


def write_cluster_file(
    levels: list[tuple[np.ndarray, np.ndarray, np.ndarray]],
    min_zoom: int,
    max_zoom: int,
    path: Path,
) -> None:
    """Write clusters for zooms ``min_zoom`` up to ``max_zoom`` to JSON.

    Layout: ``{"min_zoom": M, "max_zoom": N, "zooms": [[[lon, lat, count],
    ...], ...]}``, where ``zooms[i]`` holds zoom ``M + i``.
    """
    zooms = [
        # 5 decimal places is about 1 m
        [
            [round(c_lon, 5), round(c_lat, 5), c_count]
            for c_lon, c_lat, c_count in zip(lon.tolist(), lat.tolist(), count.tolist())
        ]
        for lon, lat, count in levels[min_zoom:max_zoom]
    ]
    write_json({"min_zoom": min_zoom, "max_zoom": max_zoom, "zooms": zooms}, path)
    cluster_count = sum(len(zoom) for zoom in zooms)
    log.info(f"Wrote {cluster_count:,} clusters to {path} ({size_text(path)})")


def write_clusters(
    cores: pd.DataFrame, out_dir: Path, max_zoom: int, radius_px: int
) -> None:
    """Write clusters for every zoom below ``max_zoom`` to two files.

    clusters_world.json holds zooms 0 to 2, so the first page load stays
    small; clusters.json holds zooms 3 and up.
    """
    levels = build_clusters(
        cores["lon"].to_numpy(), cores["lat"].to_numpy(), max_zoom, radius_px
    )
    # A worldwide view in a desktop browser opens at about zoom 1 to 2
    # (the world is 512 px wide at zoom 0).
    world_max_zoom = 3
    write_cluster_file(levels, 0, world_max_zoom, out_dir / "clusters_world.json")
    write_cluster_file(levels, world_max_zoom, max_zoom, out_dir / "clusters.json")


def main() -> None:
    """Run the preprocess pipeline."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    cores = keep_fields(sort_cores(read_cores(args.input_csv)))
    write_markers(cores, args.output_dir / "markers.fgb")
    codes, datasets = build_datasets(cores)
    datasets_path = args.output_dir / "datasets.json"
    write_json(datasets, datasets_path)
    log.info(f"Wrote {len(datasets):,} datasets ({size_text(datasets_path)})")
    write_cards(cores, codes, args.output_dir / "cards", CHUNK_SIZE)
    write_clusters(cores, args.output_dir, CLUSTER_MAX_ZOOM, CLUSTER_RADIUS_PX)
    log.info(f"Done: {args.output_dir} ({size_text(args.output_dir)})")


if __name__ == "__main__":
    main()

"""Convert Peat_DBase.csv into static files for the web map.

Outputs, under the output directory:

- markers.fgb: core points with ``core_id`` and a FlatGeobuf spatial index
- datasets.json: one entry per original dataset with its source notes
- cards/<n>.json: card rows for cores ``n * CHUNK_SIZE`` and up, with
  sample duplicates dropped and location group fields added
- clusters_world.json: clusters (GeoJSON points with zoom and count) for
  zooms 0 to 2, the first page load
- clusters.json: clusters in the same form for zooms 3 up to
  ``CLUSTER_MAX_ZOOM``

Usage: python preprocess.py INPUT_CSV OUTPUT_DIR
"""

import argparse
import ast
import json
import logging
import re
from pathlib import Path
from typing import NamedTuple

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


def drop_sample_duplicates(rows: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep one representative row per ``sample_id``.

    Sample duplicates are the same measurement ingested from two or more
    datasets; they share a ``sample_id``. The representative is the row
    with the most card fields filled (``depth_cm``, ``sample_date``,
    ``site_condition``, where "Not provided" counts as blank); for ties,
    the earliest citation year in
    ``original_dataset`` wins, and datasets without a year come last.

    Rows keep their input order. Each representative gets
    ``also_reported_in``: ``[dataset, original_entry_num]`` for each row it
    replaces, so a reader can look them up in the source release. Other
    rows get ``None``. Expects the ``dataset`` index column.
    """
    card_fields = rows[["depth_cm", "sample_date", "site_condition"]].replace(
        {"site_condition": {"Not provided": ""}}
    )
    filled = card_fields.ne("").sum(axis=1)
    year = rows["original_dataset"].str.extract(r"\((\d{4})")[0].astype(float)
    ranked = rows.assign(_filled=-filled, _year=year.fillna(np.inf)).sort_values(
        ["sample_id", "_filled", "_year"], kind="stable"
    )
    is_first = ~ranked.duplicated("sample_id")
    provenance: dict[str, list[list[int]]] = {}
    for row in ranked[~is_first].itertuples():
        provenance.setdefault(row.sample_id, []).append(
            [int(row.dataset), int(row.original_entry_num)]
        )
    kept = rows.loc[ranked.index[is_first.to_numpy()].sort_values()].reset_index(
        drop=True
    )
    kept["also_reported_in"] = [provenance.get(s) for s in kept["sample_id"]]
    log.info(
        f"Dropped {len(rows) - len(kept):,} sample duplicates from {len(rows):,} "
        f"rows; {len(provenance):,} representatives; {len(kept):,} rows left"
    )
    return kept


def sort_cores(cores: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Sort cores along a Hilbert curve and number them with ``core_id``.

    Ties in Hilbert distance sort by ``location_id``, so cores at the same
    coordinates get consecutive ``core_id``s.
    """
    keys = pd.DataFrame(
        {"distance": cores.hilbert_distance(), "location_id": cores["location_id"]}
    )
    order = keys.sort_values(["distance", "location_id"], kind="stable").index
    sorted_cores = cores.loc[order].reset_index(drop=True)
    sorted_cores.insert(0, "core_id", np.arange(len(sorted_cores)))
    log.info(f"Sorted {len(sorted_cores):,} cores by Hilbert distance")
    return sorted_cores


def value_range(values: pd.Series) -> list | None:
    """Return ``[min, max]`` of the values, or ``None`` if there are none."""
    values = values.dropna()
    if values.empty:
        return None
    return [values.min(), values.max()]


def add_location_groups(cores: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add location group fields to cores that share coordinates.

    Location duplicates are different cores at the same coordinates; they
    share a ``location_id``. Each core in a group of two or more gets
    ``location_first`` (the group's first ``core_id``) and
    ``location_count``. The group's first core also gets
    ``location_summary``: its datasets and its ``depth_cm`` and
    ``sample_date`` ranges. Cores alone at their location get ``None``.

    Raises ``ValueError`` if a group's ``core_id``s are not consecutive,
    which the web map's pager relies on.
    """
    groups = cores.groupby("location_id")["core_id"]
    first = groups.transform("min")
    count = groups.transform("size")
    if ((groups.transform("max") - first + 1) != count).any():
        raise ValueError("Location groups do not have consecutive core_ids")

    shared = count > 1
    cores = cores.copy()
    # Object dtype keeps None; a plain list of ints and None becomes floats.
    cores["location_first"] = pd.Series(
        [int(f) if s else None for f, s in zip(first, shared, strict=True)],
        index=cores.index,
        dtype=object,
    )
    cores["location_count"] = pd.Series(
        [int(c) if s else None for c, s in zip(count, shared, strict=True)],
        index=cores.index,
        dtype=object,
    )
    summaries: list[dict | None] = [None] * len(cores)
    depth = pd.to_numeric(cores["depth_cm"], errors="coerce").astype(float)
    dates = cores["sample_date"].where(cores["sample_date"] != "")
    for _, group in cores[shared].groupby("location_id"):
        summaries[int(group["core_id"].min())] = {
            "datasets": sorted(int(d) for d in group["dataset"].unique()),
            "depth_cm": value_range(depth[group.index]),
            "sample_date": value_range(dates[group.index]),
        }
    cores["location_summary"] = summaries
    group_count = cores.loc[shared, "location_id"].nunique()
    log.info(
        f"Kept {int(shared.sum()):,} location duplicates in {group_count:,} "
        "location groups"
    )
    return cores


def prune_fields(cores: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep only fields the map draws with or the detail card shows.

    Drawing needs ``core_id``, the point, and ``error_found`` (error
    color). The card shows the rest, per the spec's "detail card should
    show" list, plus the duplicate fields from ``drop_sample_duplicates``
    and ``add_location_groups``. ``dataset`` indexes datasets.json.
    """
    keep = [
        "core_id",
        "sample_id",
        "dataset",
        "lat",
        "lon",
        "depth_cm",
        "sample_date",
        "site_condition",
        "peat_measurement",
        "error_found",
        "also_reported_in",
        "location_first",
        "location_count",
        "location_summary",
        "geometry",
    ]
    log.info(f"Keep fields {','.join(keep)}, plus geometry")
    return cores[keep]


def write_markers(cores: gpd.GeoDataFrame, path: Path) -> None:
    """Write core points with a spatial index.

    Each point has ``core_id``, plus ``peat_measurement`` and
    ``error_found`` as booleans, which the map colors cores by.
    """
    markers = cores[["core_id", "geometry"]].assign(
        peat_measurement=cores["peat_measurement"] == "True",
        error_found=cores["error_found"] == "True",
    )
    markers.to_file(path, driver="FlatGeobuf", engine="pyogrio", SPATIAL_INDEX="YES")
    log.info(f"Wrote {len(cores):,} markers to {path} ({size_text(path)})")


def clean_source_notes(note: str) -> str:
    """Turn raw source notes into one citation per line.

    Notes read ``Source Citation: [...]``, optionally followed by
    ``Subsource Citation: [...]`` parts. Each bracketed part is a Python
    list of quoted citations, or one unquoted citation that may contain
    brackets of its own. Subsource lines start with "Subsource: ".
    Repeated citations appear once.
    """
    parts = re.split(r"\b(Source|Subsource) Citation\s*:\s*", note)
    lines: list[str] = []
    # re.split puts the text before the first label first, then alternates
    # label and body.
    for label, body in zip(parts[1::2], parts[2::2], strict=True):
        body = body.strip()
        try:
            citations = ast.literal_eval(body)
        except (ValueError, SyntaxError):
            citations = None
        if not isinstance(citations, list):
            citations = [body.removeprefix("[").removesuffix("]")]
        prefix = "Subsource: " if label == "Subsource" else ""
        for citation in citations:
            line = f"{prefix}{str(citation).strip()}"
            if line not in lines:
                lines.append(line)
    return "\n".join(lines) if lines else note


def build_datasets(cores: pd.DataFrame) -> tuple[np.ndarray, list[dict]]:
    """Return each core's dataset index and the list of datasets.

    Each dataset keeps the source notes of its first core, cleaned by
    ``clean_source_notes``. Run on all rows before
    ``drop_sample_duplicates``, so datasets.json also lists the datasets
    of dropped rows.
    """
    codes, names = pd.factorize(cores["original_dataset"])
    notes = cores.groupby(codes)["original_dataset_source_notes"].first()
    datasets = [
        {"name": name, "source_notes": clean_source_notes(notes[i])}
        for i, name in enumerate(names)
    ]
    return codes, datasets


def write_json(data: object, path: Path) -> None:
    """Write compact JSON."""
    with path.open("w") as f:
        json.dump(data, f, separators=(",", ":"))


def write_cards(cores: pd.DataFrame, out_dir: Path, chunk_size: int) -> None:
    """Write card rows in chunks of ``chunk_size`` by ``core_id``.

    Card ``core_id`` sits at index ``core_id % chunk_size`` of chunk
    ``core_id // chunk_size``. Fields set to ``None`` (duplicate fields of
    cores without duplicates) are left out of the row.
    """
    cards = cores.drop(columns=["geometry"])
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk_count = 0
    for start in range(0, len(cards), chunk_size):
        records = cards.iloc[start : start + chunk_size].to_dict(orient="records")
        rows = [
            {key: value for key, value in record.items() if value is not None}
            for record in records
        ]
        write_json(rows, out_dir / f"{start // chunk_size}.json")
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


class Level(NamedTuple):
    """Clusters at one zoom level.

    Each field is an array with one value per cluster; index ``i`` across
    the fields describes cluster ``i``. Arrays let each step work on a
    whole zoom at once.

    ``west``, ``south``, ``east`` and ``north`` bound each cluster's cores,
    so clicking a cluster zooms the map to fit them. ``single_before``
    marks single cores that were already single at the zoom below, so they
    can be stored only once.
    """

    lon: np.ndarray
    lat: np.ndarray
    count: np.ndarray
    west: np.ndarray
    south: np.ndarray
    east: np.ndarray
    north: np.ndarray
    single_before: np.ndarray


def merge_clusters(
    x: np.ndarray, y: np.ndarray, count: np.ndarray, radius: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Merge clusters within ``radius`` of each other, Supercluster style.

    Visit clusters in input order. Each unvisited cluster absorbs every
    unvisited cluster within ``radius`` (in world units), and the merged
    cluster sits at the count-weighted centroid of its members. Returns
    the merged clusters' ``x``, ``y`` and ``count``, plus ``parent``: the
    index of the merged cluster each input cluster joined.
    """
    tree = cKDTree(np.column_stack([x, y]))
    visited = np.zeros(len(x), dtype=bool)
    parent = np.empty(len(x), dtype=int)
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
        parent[members] = len(merged_count)
        weights = count[members]
        total = int(weights.sum())
        merged_x.append(float(x[members] @ weights) / total)
        merged_y.append(float(y[members] @ weights) / total)
        merged_count.append(total)
    return np.array(merged_x), np.array(merged_y), np.array(merged_count), parent


def build_clusters(
    lon: np.ndarray, lat: np.ndarray, max_zoom: int, radius_px: int
) -> list[Level]:
    """Return clusters for each zoom below ``max_zoom``, indexed by zoom.

    Each core starts as a cluster of count 1. Working down from
    ``max_zoom - 1`` to 0, merge the previous zoom's clusters, so clusters
    nest across zooms: each cluster at zoom ``z`` is made of whole
    clusters from zoom ``z + 1``.

    Bounds: each core starts as its own bounds; a merged cluster's bounds
    span its members' bounds.
    """
    x, y = project(lon, lat)
    count = np.ones(len(x), dtype=int)
    west, south, east, north = lon.copy(), lat.copy(), lon.copy(), lat.copy()
    levels: list[Level] = []
    for zoom in range(max_zoom - 1, -1, -1):
        radius = radius_px / (TILE_PX * 2**zoom)
        merged_x, merged_y, merged_count, parent = merge_clusters(x, y, count, radius)
        size = len(merged_count)
        merged_west = np.full(size, np.inf)
        merged_south = np.full(size, np.inf)
        merged_east = np.full(size, -np.inf)
        merged_north = np.full(size, -np.inf)
        np.minimum.at(merged_west, parent, west)
        np.minimum.at(merged_south, parent, south)
        np.maximum.at(merged_east, parent, east)
        np.maximum.at(merged_north, parent, north)
        if levels:
            # A single core at zoom + 1 whose cluster here is also a single
            # core was already alone at this zoom. Single cores stay alone,
            # at the same position, at every higher zoom, since clusters
            # only split as zoom grows.
            single_before = (count == 1) & (merged_count[parent] == 1)
            levels[-1] = levels[-1]._replace(single_before=single_before)
        x, y, count = merged_x, merged_y, merged_count
        west, south, east, north = merged_west, merged_south, merged_east, merged_north
        # Zoom 0 has no zoom below it, so nothing there was single before.
        levels.append(
            Level(
                *unproject(x, y),
                count,
                west,
                south,
                east,
                north,
                np.zeros(size, bool),
            )
        )
        # Count distribution of clusters of two or more cores; single cores
        # draw as core markers, so they are reported separately.
        multi = count[count > 1]
        low, median, p90, high = (
            np.percentile(multi, [0, 50, 90, 100]) if len(multi) else (0, 0, 0, 0)
        )
        log.info(
            f"Zoom {zoom}: {len(count):,} clusters, {len(count) - len(multi):,} "
            f"single cores. cluster sizes: min {low:,.0f}, "
            f"median {median:,.0f}, p90 {p90:,.0f}, max {high:,.0f}"
        )
    return levels[::-1]


def write_cluster_file(
    levels: list[Level], min_zoom: int, max_zoom: int, path: Path
) -> None:
    """Write clusters for zooms ``min_zoom`` up to ``max_zoom`` as GeoJSON.

    The web map loads the file directly as a GeoJSON source: one point
    feature per cluster, with ``zoom`` and ``count`` properties. Clusters
    of two or more cores also carry ``west``, ``south``, ``east`` and
    ``north``, the bounds of their cores, which the map zooms to on click;
    a single core's bounds are its own point.

    Single cores are written once, at the lowest zoom in the file where
    they stand alone; the map shows a single core at its ``zoom`` and
    every higher zoom. Without this, a core alone from zoom 7 would repeat
    at zooms 8, 9 and 10. Each file starts with all single cores at its
    ``min_zoom``, so it does not depend on the other file.

    Coordinates and bounds use 4 decimal places, about 11 m, finer than a
    pixel (about 76 m at the equator) at zoom 10, the highest pre-built
    zoom.
    """
    features: list[dict] = []
    for zoom, level in enumerate(levels[min_zoom:max_zoom], start=min_zoom):
        for c_lon, c_lat, c_count, west, south, east, north, single_before in zip(
            level.lon.tolist(),
            level.lat.tolist(),
            level.count.tolist(),
            level.west.tolist(),
            level.south.tolist(),
            level.east.tolist(),
            level.north.tolist(),
            level.single_before.tolist(),
        ):
            if single_before and zoom != min_zoom:
                continue
            properties = {"zoom": zoom, "count": c_count}
            if c_count > 1:
                properties.update(
                    west=round(west, 4),
                    south=round(south, 4),
                    east=round(east, 4),
                    north=round(north, 4),
                )
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(c_lon, 4), round(c_lat, 4)],
                    },
                    "properties": properties,
                }
            )
    write_json({"type": "FeatureCollection", "features": features}, path)
    log.info(f"Wrote {len(features):,} clusters to {path} ({size_text(path)})")


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
    # TODO: decide if it's worth splitting more
    write_cluster_file(levels, world_max_zoom, max_zoom, out_dir / "clusters.json")


def main() -> None:
    """Run the preprocess pipeline."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = read_cores(args.input_csv)
    codes, datasets = build_datasets(rows)
    rows["dataset"] = codes
    datasets_path = args.output_dir / "datasets.json"
    write_json(datasets, datasets_path)
    log.info(
        f"Wrote {len(datasets):,} datasets to {datasets_path} "
        f"({size_text(datasets_path)})"
    )
    cores = drop_sample_duplicates(rows)
    cores = sort_cores(cores)
    cores = add_location_groups(cores)
    cores = prune_fields(cores)
    write_markers(cores, args.output_dir / "markers.fgb")
    write_cards(cores, args.output_dir / "cards", CHUNK_SIZE)
    write_clusters(cores, args.output_dir, CLUSTER_MAX_ZOOM, CLUSTER_RADIUS_PX)
    log.info(f"Done: {args.output_dir} ({size_text(args.output_dir)})")


if __name__ == "__main__":
    main()

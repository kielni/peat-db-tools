"""Tests for preprocess.py on a small fixture CSV."""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest

import preprocess

HEADER = (
    "original_dataset,original_entry_num,lat,lon,depth_cm,sample_date,"
    "site_condition,original_dataset_source_notes,peat_measurement,"
    "error_found,investigation_notes,location_is_duplicate,location_id,"
    "sample_duplication_flag,sample_id"
)
ROWS = [
    'A,1,60.0,25.0,100,2001,bog,"Smith 2001,\nmulti-line note",C,,,,L1,,S1',
    "A,2,60.1,25.1,120,2002,bog,Smith 2001,C,,,,L2,,S2",
    "B,1,-3.0,115.0,300,2010,swamp,Jones 2010,C,,,,L3,,S3",
    "B,2,-3.1,115.1,310,2011,swamp,Jones 2010,C,,,,L4,,S4",
    "C,1,45.0,-80.0,50,1999,fen,Lee 1999,C,,,,L5,,S5",
    # invalid rows: missing lat, latitude out of range
    "C,2,,-80.0,50,1999,fen,Lee 1999,C,,,,L6,,S6",
    "C,3,95.0,-80.0,50,1999,fen,Lee 1999,C,,,,L7,,S7",
]


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    """Write the fixture CSV and return its path."""
    path = tmp_path / "peat.csv"
    path.write_text("\n".join([HEADER, *ROWS]) + "\n")
    return path


@pytest.fixture
def cores(csv_path: Path) -> gpd.GeoDataFrame:
    """Return the fixture cores, read, sorted and limited to kept fields."""
    return preprocess.keep_fields(
        preprocess.sort_cores(preprocess.read_cores(csv_path))
    )


def test_keep_fields_drops_unused_columns(cores: gpd.GeoDataFrame) -> None:
    for column in [
        "original_entry_num",
        "investigation_notes",
        "location_is_duplicate",
        "location_id",
        "sample_duplication_flag",
    ]:
        assert column not in cores.columns
    assert {"core_id", "sample_id", "error_found", "geometry"} <= set(cores.columns)


def test_read_cores_parses_multiline_and_drops_invalid(csv_path: Path) -> None:
    raw = preprocess.read_cores(csv_path)
    assert len(raw) == 5
    assert "multi-line note" in raw["original_dataset_source_notes"].iloc[0]
    assert set(raw["sample_id"]) == {"S1", "S2", "S3", "S4", "S5"}


def test_sort_cores_follows_hilbert_order(cores: gpd.GeoDataFrame) -> None:
    assert cores["core_id"].tolist() == list(range(len(cores)))
    distance = cores.hilbert_distance().tolist()
    assert distance == sorted(distance)
    # nearby cores sit next to each other
    positions = {s: i for i, s in enumerate(cores["sample_id"])}
    assert abs(positions["S1"] - positions["S2"]) == 1
    assert abs(positions["S3"] - positions["S4"]) == 1


def test_write_markers(cores: gpd.GeoDataFrame, tmp_path: Path) -> None:
    path = tmp_path / "markers.fgb"
    preprocess.write_markers(cores, path)
    markers = gpd.read_file(path)
    assert list(markers.columns) == ["core_id", "geometry"]
    assert sorted(markers["core_id"]) == cores["core_id"].tolist()


def test_cards_chunks_and_dataset_lookup(
    cores: gpd.GeoDataFrame, tmp_path: Path
) -> None:
    codes, datasets = preprocess.build_datasets(cores)
    preprocess.write_cards(cores, codes, tmp_path / "cards", chunk_size=2)

    assert sorted(p.name for p in (tmp_path / "cards").iterdir()) == [
        "0.json",
        "1.json",
        "2.json",
    ]
    for core in cores.itertuples():
        chunk = json.loads(
            (tmp_path / "cards" / f"{core.core_id // 2}.json").read_text()
        )
        card = chunk[core.core_id % 2]
        assert card["core_id"] == core.core_id
        assert card["sample_id"] == core.sample_id
        assert set(card) == {
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
        }
        assert datasets[card["dataset"]]["name"] == core.original_dataset
    assert sorted(d["name"] for d in datasets) == ["A", "B", "C"]


def test_project_round_trip() -> None:
    lon = np.array([-170.0, 0.0, 25.0, 115.0])
    lat = np.array([-60.0, 0.0, 60.0, -3.0])
    x, y = preprocess.project(lon, lat)
    assert np.all((x >= 0) & (x <= 1) & (y >= 0) & (y <= 1))
    # y grows south
    assert y[2] < y[1] < y[0]
    back_lon, back_lat = preprocess.unproject(x, y)
    assert np.allclose(back_lon, lon)
    assert np.allclose(back_lat, lat)


def test_merge_clusters_within_radius() -> None:
    x = np.array([0.0, 0.5, 10.0])
    y = np.array([0.0, 0.0, 0.0])
    count = np.array([3, 1, 2])
    merged_x, merged_y, merged_count, parent = preprocess.merge_clusters(
        x, y, count, radius=1.0
    )
    # the first two merge at their count-weighted centroid; the far one stays
    assert merged_count.tolist() == [4, 2]
    assert merged_x.tolist() == [0.125, 10.0]
    assert merged_y.tolist() == [0.0, 0.0]
    assert parent.tolist() == [0, 0, 1]


def test_merge_clusters_visits_in_order() -> None:
    # 0 absorbs 1, so 1 cannot also absorb 2, which is only near 1
    x = np.array([0.0, 0.9, 1.8])
    merged_x, _, merged_count, parent = preprocess.merge_clusters(
        x, np.zeros(3), np.ones(3, dtype=int), radius=1.0
    )
    assert merged_count.tolist() == [2, 1]
    assert merged_x.tolist() == [0.45, 1.8]
    assert parent.tolist() == [0, 0, 1]


def test_build_clusters_nest_across_zooms() -> None:
    lon = np.array([25.0, 25.1, 115.0, 115.1, -80.0])
    lat = np.array([60.0, 60.1, -3.0, -3.1, 45.0])
    levels = preprocess.build_clusters(lon, lat, max_zoom=12, radius_px=40)
    assert len(levels) == 12
    sizes = [len(level.count) for level in levels]
    for level in levels:
        assert level.count.sum() == len(lon)
    # zooming out never adds clusters
    assert sizes == sorted(sizes)
    # each close pair is one cluster at zoom 0 and two by zoom 11
    assert sizes[0] == 3
    assert sizes[11] == 5
    # a dense pair merges at its centroid
    zoom_0_lon = levels[0].lon
    assert np.isclose(zoom_0_lon, 25.05).any()


def test_build_clusters_bounds_and_single_cores() -> None:
    # about 0.011 degrees apart at the equator: 33 px at zoom 11, 65 px at
    # zoom 12, so the pair is one cluster through zoom 11 and splits at 12.
    lon = np.array([0.0, 0.01])
    lat = np.array([0.0, 0.005])
    levels = preprocess.build_clusters(lon, lat, max_zoom=14, radius_px=40)
    for zoom in range(12):
        level = levels[zoom]
        assert level.count.tolist() == [2]
        # the cluster's bounds span both cores
        assert (level.west[0], level.south[0]) == (0.0, 0.0)
        assert (level.east[0], level.north[0]) == (0.01, 0.005)
    for zoom in (12, 13):
        level = levels[zoom]
        assert level.count.tolist() == [1, 1]
        # a single core's bounds are its own point
        assert level.west.tolist() == level.east.tolist()
        assert level.south.tolist() == level.north.tolist()
    # alone from zoom 12, so already single before only at zoom 13
    assert levels[12].single_before.tolist() == [False, False]
    assert levels[13].single_before.tolist() == [True, True]


def test_build_clusters_bounds_span_merged_clusters() -> None:
    lon = np.array([25.0, 25.1, 115.0, 115.1, -80.0])
    lat = np.array([60.0, 60.1, -3.0, -3.1, 45.0])
    levels = preprocess.build_clusters(lon, lat, max_zoom=12, radius_px=40)
    for level in levels:
        # bounds contain each cluster's centroid (to within the rounding of
        # projecting and unprojecting it)
        assert np.all(level.west <= level.lon + 1e-9)
        assert np.all(level.lon <= level.east + 1e-9)
        assert np.all(level.south <= level.lat + 1e-9)
        assert np.all(level.lat <= level.north + 1e-9)
    # at zoom 0 the Borneo pair is one cluster spanning both cores
    borneo = levels[0].count == 2
    borneo &= levels[0].lon > 100
    assert levels[0].west[borneo].tolist() == [115.0]
    assert levels[0].east[borneo].tolist() == [115.1]
    assert levels[0].south[borneo].tolist() == [-3.1]
    assert levels[0].north[borneo].tolist() == [-3.0]


def shown_at(features: list[dict], zoom: int) -> list[dict]:
    """Return the features a map layer for ``zoom`` shows.

    Mirrors the web map's filter: clusters at ``zoom``, plus single cores
    first stored at a lower zoom.
    """
    return [
        f
        for f in features
        if f["properties"]["zoom"] == zoom
        or (f["properties"]["count"] == 1 and f["properties"]["zoom"] < zoom)
    ]


def test_write_clusters(cores: gpd.GeoDataFrame, tmp_path: Path) -> None:
    preprocess.write_clusters(cores, tmp_path, max_zoom=12, radius_px=40)
    world = json.loads((tmp_path / "clusters_world.json").read_text())
    rest = json.loads((tmp_path / "clusters.json").read_text())
    for collection in (world, rest):
        assert collection["type"] == "FeatureCollection"
        for feature in collection["features"]:
            assert feature["geometry"]["type"] == "Point"
            properties = feature["properties"]
            if properties["count"] == 1:
                assert set(properties) == {"zoom", "count"}
            else:
                assert set(properties) == {
                    "zoom",
                    "count",
                    "west",
                    "south",
                    "east",
                    "north",
                }

    # each file stands alone: every zoom it covers shows every core
    for features, zooms in (
        (world["features"], range(3)),
        (rest["features"], range(3, 12)),
    ):
        for zoom in zooms:
            shown = shown_at(features, zoom)
            assert sum(f["properties"]["count"] for f in shown) == len(cores)

    # the isolated Canada core is stored once per file, at the file's
    # lowest zoom
    canada = [
        f
        for f in world["features"] + rest["features"]
        if f["geometry"]["coordinates"] == [-80.0, 45.0]
    ]
    assert [f["properties"]["zoom"] for f in canada] == [0, 3]

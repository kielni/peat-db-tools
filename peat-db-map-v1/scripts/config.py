"""Tuning settings for preprocess.py.

The web map mirrors these values; change them together.

Sizes below come from a full run on Peat_DBase.csv (304,945 rows, 565
datasets, no invalid coordinates), keeping only the map and card fields.
MB and KB are decimal (1,000,000 and 1,000 bytes), as in the preprocess
log.

- markers.fgb: 37.4 MB, about 123 bytes per core with only ``core_id`` and
  the point (10.1 MB gzipped, but served uncompressed for range requests).
- datasets.json: 197.4 KB, 9.8 KB gzipped.
"""

# Cores per card file. At 1000: 305 files, 61.5 MB total, about 200 KB
# each; 5.0 MB gzipped total, about 16 KB each (cards/0.json: 12.3 KB).
# Before dropping unused fields: 99 MB total, about 320 KB each.
CHUNK_SIZE = 1000

# Lowest zoom that shows individual markers; clusters cover zooms below it.
# At 11 with a 40 px radius, 197,719 clusters in two files:
#   clusters_world.json, zooms 0-2: 462 features, 79.5 KB, 13.6 KB gzipped
#   clusters.json, zooms 3-10: 132,770 features, 20.5 MB, 2.6 MB gzipped
#   (GeoJSON with cluster bounds, single cores stored once; without
#   bounds 16.7 MB, 1.2 MB gzipped; repeating single cores at every zoom was
#   22.7 MB, 1.8 MB gzipped; the compact array layout was 4.3 MB, 1.5 MB)
# Clusters per zoom:
#   z0: 40, z1: 113, z2: 325, z3: 895, z4: 2322, z5: 5584, z6: 11970,
#   z7: 22666, z8: 36720, z9: 51356, z10: 65728
# Chosen from views centered on the densest clusters (Scotland): the
# densest view at the switch loads 7,275 cores (0.9 MB of markers.fgb) at
# 11, against 20,190 (2.5 MB) at 10. Each zoom past 11 adds about 0.5 MB
# gzipped to clusters.json but barely shrinks the densest view, whose
# cores sit at nearly the same coordinates. See the implementation plan's
# measurements section.
CLUSTER_MAX_ZOOM = 11

# Clusters within this many screen pixels of each other merge.
# Supercluster's default is 40 px at a 512 px tile. File size barely
# depends on it (clusters.json gzipped: 1.71 MB at 30 px, 1.50 MB at 40,
# 1.34 MB at 50, 1.03 MB at 80), so the circle style decides it: the
# largest circle and its count label should fit within this radius.
CLUSTER_RADIUS_PX = 40

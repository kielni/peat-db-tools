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
# At 10 with a 40 px radius, 132,877 clusters in two files:
#   clusters_world.json, zooms 0-2: 479 clusters, 11.4 KB, 5.3 KB gzipped
#   clusters.json, zooms 3-9: 132,398 clusters, 2.9 MB, 1.0 MB gzipped
# Clusters per zoom:
#   z0: 42, z1: 111, z2: 326, z3: 894, z4: 2316, z5: 5581, z6: 11981,
#   z7: 22668, z8: 36750, z9: 52208
# z8 and z9 hold two thirds of the clusters; dropping them would shrink
# clusters.json to under 1 MB.
CLUSTER_MAX_ZOOM = 10

# Clusters within this many screen pixels of each other merge.
# Supercluster's default is 40 px at a 512 px tile.
CLUSTER_RADIUS_PX = 40

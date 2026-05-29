# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-05-29

### Fixed
- Table-existence lookups now quote identifiers (`to_regclass(format('%I.%I',
  …))`), so tables/schemas with mixed-case or special-character names resolve
  correctly instead of reporting "not found". Affects `describe_table`,
  `sample_table`, `check_geometry_validity`, the spatial existence checks, and
  the layer `_meta` check.

[0.2.2]: https://github.com/psychonaut0/mcp-postgis/releases/tag/v0.2.2

## [0.2.1] - 2026-05-29

### Fixed
- Circular import that prevented importing tool/resource submodules before
  `mcp_postgis.server` (`ServerContext` moved to `mcp_postgis.context`). The
  server entry point was unaffected; this fixes library-style imports.

[0.2.1]: https://github.com/psychonaut0/mcp-postgis/releases/tag/v0.2.1

## [0.2.0] - 2026-05-29

A read-only round-out of the toolkit.

### Added
- **Geometry operations** (inline WKT/GeoJSON): `transform_srid`, `centroid`,
  `point_on_surface`, `area`, `length`, `simplify`, `is_valid`, `make_valid`,
  `bbox`.
- **`check_geometry_validity`** — read-only scan for invalid / out-of-range
  geometries in a table, with a capped sample of offenders.
- **Export**: `export_geojson` (table or SELECT → FeatureCollection),
  `export_wkt`.
- **`create_layer` `geometry_type` filter** — publish single-type layers; a
  warning is returned when an unfiltered layer mixes geometry types.
- **MCP resources**: `postgis://schemas`, `postgis://schema/{schema}/{table}`,
  `postgis://layers`.
- **MCP prompts**: `analyze-layer`, `nearest-things`, `within-radius`,
  `compare-layers`.

[0.2.0]: https://github.com/psychonaut0/mcp-postgis/releases/tag/v0.2.0

## [0.1.0] - 2026-05-29

Initial release — a Model Context Protocol server for PostGIS.

### Added
- Three safety modes (`read_only` / `read_write` / `admin`) with a
  `pglast`-based statement classifier, per-transaction `statement_timeout`,
  and row-count caps. DDL in non-admin modes is whitelisted to the layer schema.
- **Introspection** tools: `list_schemas`, `list_tables`, `describe_table`,
  `list_geometry_columns`, `list_spatial_indexes`, `list_extensions`.
- **Query** tools: `execute_sql`, `explain`, `sample_table`.
- **Spatial analysis** tools: `features_in_bbox`, `features_in_polygon`,
  `nearest_features`, `within_distance`, `buffer`, `intersect_layers`.
- **Layer publishing** (the QGIS bridge): `create_layer`, `refresh_layer`,
  `list_layers`, `describe_layer`, `drop_layer` — results land as
  views/materialized views in a dedicated `mcp_layers` schema that QGIS reads.
- Configuration via environment variables (and an optional TOML override).
- Docs: README quick start, security/role-provisioning guide, QGIS setup,
  Claude Desktop and Claude Code setup.

[0.1.0]: https://github.com/psychonaut0/mcp-postgis/releases/tag/v0.1.0

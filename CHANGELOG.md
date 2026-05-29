# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

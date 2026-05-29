# Getting started

This is a 5-minute walkthrough end to end: container, server, Claude
Desktop, QGIS.

## Prerequisites

- Docker
- Claude Desktop (≥ MCP-supporting build)
- QGIS 3.x (for the layer-publishing demo)
- `uv` (`pipx install uv`)

## 1. Start PostGIS

```bash
docker run --name postgis -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 -d postgis/postgis:16-3.4
```

Seed a couple of tables (you can paste this into `psql`):

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE cities (
  id serial PRIMARY KEY, name text,
  geom geometry(Point, 4326)
);
INSERT INTO cities (name, geom) VALUES
  ('Rome',  ST_SetSRID(ST_MakePoint(12.4964, 41.9028), 4326)),
  ('Milan', ST_SetSRID(ST_MakePoint(9.19, 45.4642), 4326));
```

## 2. Install the server

```bash
uv tool install mcp-postgis
```

## 3. Wire Claude Desktop

See [claude-desktop-setup.md](claude-desktop-setup.md). Use mode
`read_write` so you can publish layers.

## 4. Try the headline flow

In Claude Desktop:

> List the geometry columns you can see. Then publish a view named
> `cities_north` containing every city north of latitude 43.

Claude should call `list_geometry_columns`, then `create_layer`. Open
QGIS, refresh your PostGIS connection, find `mcp_layers.cities_north`,
load it. The Milan point appears.

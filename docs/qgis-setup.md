# QGIS setup

mcp-postgis publishes layers as views/materialized views in a dedicated
schema (`mcp_layers` by default). QGIS reads them via the PostGIS provider.

## One-time connection

1. **Browser panel → PostgreSQL → New Connection.**
2. Connection name: anything (e.g. `mcp-postgis-dev`).
3. Host / port / database: same as `MCP_POSTGIS_DATABASE_URL`.
4. **Authentication** → Basic → user/password. Use a role that has
   `USAGE` on `mcp_layers` and `SELECT` on its objects. The
   `mcp_postgis_rw` role from [security.md](security.md) works.
5. Tick **Use estimated table metadata** (much faster on big tables).
6. Save, then test connection.

## Picking up new layers

After Claude creates a new layer, in QGIS:

- **Browser panel → your connection → schemas → `mcp_layers`.**
- Right-click `mcp_layers` → **Refresh**.
- The new view shows up. Double-click to load.

If the layer doesn't appear:

- Confirm the view exists: `SELECT * FROM mcp_layers._meta;`
- Confirm your QGIS role has `SELECT` on it. The `mcp_postgis_rw` role
  owns the schema, so granting `USAGE ON SCHEMA mcp_layers` + `SELECT ON
  ALL TABLES IN SCHEMA mcp_layers` to your QGIS role is enough.

## Materialized vs plain views

- **Plain view**: always live. Slow for expensive queries.
- **Materialized view**: a snapshot. Faster, but stale until you call
  `refresh_layer`. Created automatically with a GIST index on its
  geometry column, so QGIS pans/zooms are fast.

Ask Claude to make heavy analytical layers materialized; lightweight
filters can stay as plain views.

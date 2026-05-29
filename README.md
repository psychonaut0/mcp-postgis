# mcp-postgis

[![CI](https://github.com/psychonaut0/mcp-postgis/actions/workflows/ci.yml/badge.svg)](https://github.com/psychonaut0/mcp-postgis/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-postgis.svg)](https://pypi.org/project/mcp-postgis/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-postgis.svg)](https://pypi.org/project/mcp-postgis/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes PostGIS to MCP-aware clients (Claude Desktop, Claude Code).

Lets Claude introspect your spatial database, run safe spatial queries, and **publish results as views that QGIS picks up automatically** — so an analyst can describe a layer in plain language and have it land in their QGIS browser.

## Install

```bash
pip install mcp-postgis     # or `uv tool install mcp-postgis`
```

## Quick start (Docker PostGIS + Claude Desktop)

1. Run PostGIS:
   ```bash
   docker run --name postgis -e POSTGRES_PASSWORD=postgres \
     -p 5432:5432 -d postgis/postgis:16-3.4
   ```
2. Create a dedicated read-only role (see [docs/security.md](docs/security.md) for read_write and admin variants):
   ```sql
   CREATE ROLE mcp_postgis_ro LOGIN PASSWORD 'change-me';
   GRANT CONNECT ON DATABASE mydb TO mcp_postgis_ro;
   GRANT USAGE ON SCHEMA public, app TO mcp_postgis_ro;
   GRANT SELECT ON ALL TABLES IN SCHEMA public, app TO mcp_postgis_ro;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public, app
       GRANT SELECT ON TABLES TO mcp_postgis_ro;
   ```
3. Add to your `claude_desktop_config.json`:
   ```json
   {
     "mcpServers": {
       "postgis": {
         "command": "uvx",
         "args": ["mcp-postgis"],
         "env": {
           "MCP_POSTGIS_DATABASE_URL": "postgresql://mcp_postgis_ro:change-me@localhost:5432/mydb",
           "MCP_POSTGIS_MODE": "read_only"
         }
       }
     }
   }
   ```
4. Restart Claude Desktop. The server's tools appear in the model picker.

## Using with Claude Code (Linux/macOS/Windows)

Claude Desktop is macOS/Windows only — on Linux (or anywhere you use the CLI),
register the server with Claude Code instead:

```bash
claude mcp add --transport stdio \
  --env MCP_POSTGIS_DATABASE_URL="postgresql://mcp_postgis_ro:change-me@localhost:5432/mydb" \
  --env MCP_POSTGIS_MODE=read_only \
  --scope user \
  postgis \
  -- uvx mcp-postgis
```

Restart `claude`, then `/mcp` lists the server and its tools. Use `--scope user`
(not `project`) so your connection string isn't committed to a repo.

## Modes

| Mode         | What it can do                                                                                  |
| ------------ | ----------------------------------------------------------------------------------------------- |
| `read_only`  | Default. Introspection + SELECT + spatial analysis. No writes, no DDL.                          |
| `read_write` | Above + create/refresh/drop views in `MCP_POSTGIS_LAYER_SCHEMA` (`mcp_layers` by default).      |
| `admin`      | Anything the connected role can do. Use only for one-off admin tasks; the role still gates.     |

## QGIS integration

After running the server in `read_write` mode and asking Claude to "publish that as a layer named `hotels_near_coast`", point QGIS at the same database (or use the same role). In the QGIS Browser → PostGIS → your connection, right-click → Refresh; the `mcp_layers` schema appears with `hotels_near_coast` inside it.

See [docs/qgis-setup.md](docs/qgis-setup.md) for screenshots.

## License

MIT. Contributions welcome — please open an issue first to discuss bigger changes.

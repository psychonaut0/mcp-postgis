# Securing mcp-postgis

The server's `MCP_POSTGIS_MODE` is a *soft* guardrail. The *load-bearing*
guardrail is the Postgres role you connect with. Use one of the three role
recipes below.

## read_only role

```sql
CREATE ROLE mcp_postgis_ro LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE :DB TO mcp_postgis_ro;
GRANT USAGE ON SCHEMA public TO mcp_postgis_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_postgis_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO mcp_postgis_ro;
-- repeat for every schema you want the role to see
```

Pair with `MCP_POSTGIS_MODE=read_only`.

## read_write role (layer publishing)

The role needs to read your data and write to the layer schema only.

```sql
CREATE ROLE mcp_postgis_rw LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE :DB TO mcp_postgis_rw;

-- read everywhere it should be able to query
GRANT USAGE ON SCHEMA public TO mcp_postgis_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_postgis_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO mcp_postgis_rw;

-- own the layer schema so it can CREATE/DROP views there
CREATE SCHEMA IF NOT EXISTS mcp_layers AUTHORIZATION mcp_postgis_rw;
```

Pair with `MCP_POSTGIS_MODE=read_write`.

## admin role

For one-off DBA tasks. Use a normal superuser-ish login and set
`MCP_POSTGIS_MODE=admin`. Don't leave a Claude Desktop server running with
this mode.

## Statement timeout

The server sets `statement_timeout` per transaction
(`MCP_POSTGIS_STATEMENT_TIMEOUT_MS`, default 30 000). To enforce
defence-in-depth, also set it at the role level:

```sql
ALTER ROLE mcp_postgis_ro SET statement_timeout = '30s';
```

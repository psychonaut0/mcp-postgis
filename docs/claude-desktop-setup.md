# Claude Desktop setup

1. Install with `uv tool install mcp-postgis` (or `pip install mcp-postgis`).
2. Open Claude Desktop → Settings → Developer → "Edit Config".
3. Replace or merge:

   ```json
   {
     "mcpServers": {
       "postgis": {
         "command": "uvx",
         "args": ["mcp-postgis"],
         "env": {
           "MCP_POSTGIS_DATABASE_URL": "postgresql://user:pass@host:5432/db",
           "MCP_POSTGIS_MODE": "read_only"
         }
       }
     }
   }
   ```
4. Restart Claude Desktop. You should see `postgis` listed under "Tools".

## Useful env knobs

- `MCP_POSTGIS_ALLOWED_SCHEMAS=public,app` — restrict what the model sees.
- `MCP_POSTGIS_MAX_ROWS=200` — keep responses small for chat-friendly output.
- `MCP_POSTGIS_STATEMENT_TIMEOUT_MS=10000` — fail-fast on expensive queries.

## Troubleshooting

- The server logs to stderr; Claude Desktop's Developer logs surface them.
- If tools don't appear, run `uvx mcp-postgis` manually with the same env
  vars and look for connection errors.

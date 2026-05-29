# Contributing to mcp-postgis

Thanks for your interest! Bug reports, feature ideas, and PRs are all welcome.
For anything larger than a small fix, please open an issue first so we can
discuss the approach.

## Development setup

You need Python 3.11+, [uv](https://docs.astral.sh/uv/), and Docker (the
integration tests spin up a real PostGIS container via
[testcontainers](https://testcontainers.com/)).

```bash
uv sync --extra dev
```

## Running the checks

CI runs all three of these on Python 3.11 and 3.12 — please make sure they pass
locally before opening a PR:

```bash
uv run ruff check src tests        # lint
uv run mypy src/mcp_postgis        # types (strict)
uv run pytest                      # full suite — needs Docker running
uv run pytest -m "not integration" # unit only, no Docker
```

## Adding a tool

Tools are grouped by family in `src/mcp_postgis/tools/<family>.py`. Each tool is
a module-level `async def` whose first parameter is `ctx: Context`; it's
registered via the module's `register(mcp)` function, which `server.py` calls.

A few rules the existing tools follow:

- Resolve the request context with
  `srv: ServerContext = ctx.request_context.lifespan_context`.
- Use `psycopg.sql.Identifier` / `SQL` for anything in identifier position;
  parameterise values with `%s`. Never f-string user input into SQL.
- Gate any user-supplied SQL through `safety.ensure_allowed(...)`.
- Convert internal exceptions to structured errors with `errors.translate(...)`.
- Add an integration test against the seed fixture in `tests/fixtures/seed.sql`.

## Conventions

- Conventional commit messages: `feat:`, `fix:`, `docs:`, `chore:`, `test:`.
- Keep `ruff` and `mypy --strict` clean — CI enforces both.
- By contributing, you agree that your contributions are licensed under the
  project's [MIT license](LICENSE).

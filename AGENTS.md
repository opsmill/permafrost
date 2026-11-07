# Permafrost

Permafrost is a Python library that stores deeply nested, read-only data across many dimensions, such as branches, and stores each identical piece of data only once.

`SPEC.md` is the reference for what the library does and how it is built. Read it before changing code. When a change contradicts the spec, update the spec in the same change or ask first.

## Project setup

- Python 3.12 or later, managed with `uv`.
- Source code is in `src/permafrost/`.
- Run the tests with `uv run pytest`. They include the type check, which you can also run alone with `uv run ty check`.
- Lint and format with `uv run ruff check` and `uv run ruff format`. Do not use `typing.Any`: declare an exact type, or `object` where the type is open.
- Give every function, method and class in `src/permafrost/` a docstring, including internal ones whose names start with `_`. `ruff check` reports a missing docstring on public names only, so check internal ones yourself.

## Test coverage

- When adding or changing behavior that uses the external cache, add or update a real Redis integration test in `tests/integration/test_redis.py`, even if the Redis backend implementation itself is unchanged. Unit tests using `MemoryBackend`, mocks, or fakeredis do not replace this coverage.
- Exercise the changed behavior through the public `Store` API. For behavior shared between stores, use the existing Redis backend and peer fixtures with separate `Process` contexts so shared in-memory nodes cannot hide missing Redis behavior.
- Include the integration coverage in OpenSpec designs and tasks. Run `uv run pytest tests/integration` with Docker available, and report any inability to run it explicitly. Passing existing Redis tests alone does not validate a new cache behavior.

# Vrooem Gateway Agent Instructions

This repo is the FastAPI supplier gateway for Vrooem Car Rentals.

## Related Projects
- Main Laravel app: `C:\laragon\www\CarRental`
- Mobile app: `C:\laragon\www\vrooem-mobile`

## Stack
- Python 3.11, FastAPI, Pydantic v2, httpx, Redis, SQLAlchemy async, asyncpg, aiomysql, Alembic, PyYAML, pytest, ruff.

## Architecture
- Entry point: `app/main.py`.
- Internal routers: `app/api/v1/health.py`, `suppliers.py`, `locations.py`, `search.py`, `bookings.py`.
- External provider API is mounted at `/provider` via `app/api/v1/provider.py`.
- Supplier adapters live in `app/adapters/` and register through the adapter registry.
- Supplier config lives in `config/suppliers/`.
- Location data and refresh workflows live under `data/` and `scripts/`.
- Laravel internal bridge routes live in `C:\laragon\www\CarRental\routes\api.php` under `/internal` and `/internal/provider` with `gateway.token` middleware.

## Work Rules
- Read `C:\laragon\www\CarRental\CLAUDE.md` for the overall project workflow.
- Use Codex as the primary agent for this repo. Gateway work is backend/API/provider logic and should get Codex implementation/review by default.
- Use `fastapi-templates` for Python/FastAPI changes.
- Use `redis-best-practices` for cache changes and `supabase-postgres-best-practices` for PostgreSQL/query work when relevant.
- Keep adapters isolated by supplier. Follow the shape of existing adapters before adding new abstractions.
- Do not hardcode credentials; use settings/env/config patterns already in place.

## Automatic Task Router
- Codex primary: supplier adapters, API contracts, cache/database logic, tests, security, and reviews.
- Claude only supports frontend-facing documentation or partner-facing copy when needed.
- Supplier/adapter work: inspect `app/adapters/`, `app/adapters/registry.py`, matching `config/suppliers/*.yaml`, then use `fastapi-templates`.
- Search/booking contract work: inspect Laravel callers in `C:\laragon\www\CarRental` and verify response shapes.
- Cache work: use `redis-best-practices`.
- Database/query work: use `supabase-postgres-best-practices` when Postgres/Supabase is involved.
- Current library/API uncertainty: use Ref official docs first; use Exa/web only when discovery is needed.
- Complex provider integrations: use subagents for independent adapter/config/Laravel-contract review.

## Common Commands
- Run tests: `pytest`
- Lint: `ruff check app/`
- Format check: `ruff format app/ --check`
- Local services: `docker-compose up -d`
- Location refresh: `bash scripts/local-refresh-locations.sh`

## Verification Expectations
- Adapter or schema changes need targeted tests or documented manual API checks.
- Search/booking contract changes need Laravel caller review.
- Provider API changes need OpenAPI/response shape review because external partners depend on it.

## Task Memory Policy
- For significant completed gateway work, update `C:\laragon\www\CarRental\docs\implementation-log.md`.
- Log date, task summary, supplier/API contract decisions, checks run, and follow-ups.
- Keep entries short. Do not log secrets or `.env` values.

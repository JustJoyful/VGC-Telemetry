# VGC Telemetry

Headless, production-grade REST API serving competitive **Pokémon Champions** tournament meta data — team compositions, movesets, abilities, EV spreads, species usage, and team-core (synergy) data — pulled from verified tournament results and exposed over a versioned public API.

> ⚠️ **Domain Context:** Pokémon Champions is a standalone game released April 8, 2026. This project is **Champions exclusively** — never Scarlet & Violet. There is no Terastallization mechanic. Mega Evolution & Z Mega Evolution are the marquee mechanics. Legal pools and regulations are cumulative.

## Tech Stack
- **Framework:** FastAPI (Python 3.12+)
- **Validation:** Pydantic v2 (strict data contracts)
- **Database:** Amazon DynamoDB (Single-table design, Provisioned capacity mode ≤25 WCU/RCU)
- **Storage:** Amazon S3 (Raw tournament data lake & DLQ quarantine)
- **Compute:** AWS Lambda via Function URLs (Reserved concurrency protection)
- **Deployment:** Multi-stage Docker container images for all Lambdas

## Development Setup

```bash
# Install dependencies with uv
uv sync

# Run tests
uv run pytest -v

# Run linting
uv run ruff check .
```

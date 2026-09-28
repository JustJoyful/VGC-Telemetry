from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.middleware.rate_limit import DynamoDBRateLimitMiddleware
from app.models.responses import HealthResponse
from app.routers import meta_router, players_router, teams_router
from core.config import get_active_regulation

app = FastAPI(
    title="VGC Telemetry API",
    description=(
        "Headless, production-grade REST API serving "
        "competitive Pokémon Champions tournament meta data"
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(DynamoDBRateLimitMiddleware)

app.include_router(teams_router)
app.include_router(meta_router)
app.include_router(players_router)


@app.get("/health", response_model=HealthResponse, tags=["System"])
@app.get("/", response_model=HealthResponse, tags=["System"])
def health_check() -> HealthResponse:
    """Return system status and active Pokémon Champions regulation snapshot."""
    active_reg = get_active_regulation()
    return HealthResponse(
        status="ok",
        regulation=active_reg.get("regulation_id", "M-C"),
        format="Pokémon Champions VGC",
    )

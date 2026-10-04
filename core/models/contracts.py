from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.config import is_legal_z_mega_stone, is_valid_form


class EVSpread(BaseModel):
    """Competitive EV spread across six stats.

    Legacy /v1 contract: each stat has 0 to 252 EVs, total at most 508.
    Native Champions Stat Points are defined separately in champions.py.
    'def' is aliased to 'def_' because 'def' is a reserved Python keyword.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    hp: int = Field(ge=0, le=252)
    atk: int = Field(ge=0, le=252)
    def_: int = Field(ge=0, le=252, alias="def")
    spa: int = Field(ge=0, le=252)
    spd: int = Field(ge=0, le=252)
    spe: int = Field(ge=0, le=252)

    @model_validator(mode="after")
    def validate_total_evs(self) -> Self:
        total = self.hp + self.atk + self.def_ + self.spa + self.spd + self.spe
        if total > 508:
            raise ValueError(f"Total EVs cannot exceed 508 (got {total})")
        return self

    def canonical(self) -> str:
        """Returns canonical formatted string HP/Atk/Def/SpA/SpD/Spe."""
        return f"{self.hp}/{self.atk}/{self.def_}/{self.spa}/{self.spd}/{self.spe}"


class CompetitivePokemon(BaseModel):
    """A single Pokémon entry on a verified tournament team roster.

    Validates structural integrity, closed form vocabulary, and Mega/Z-Mega consistency.
    No Terastallization fields or stats are permitted.
    """

    model_config = ConfigDict(extra="forbid")

    species: str
    form: str | None = None
    item: str | None = None
    ability: str
    moves: list[str] = Field(min_length=1, max_length=4)
    evs: EVSpread

    @field_validator("moves")
    @classmethod
    def validate_moves_structure(cls, v: list[str]) -> list[str]:
        for move in v:
            if not move or not move.strip():
                raise ValueError("Moves cannot contain empty strings")
        if len(v) != len(set(v)):
            raise ValueError(f"Team entry contains duplicate moves: {v}")
        return v

    @field_validator("form")
    @classmethod
    def validate_form_vocabulary(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_form(v):
            raise ValueError(f"Form '{v}' is not in the confirmed form vocabulary")
        return v

    @model_validator(mode="after")
    def validate_mega_evolution_coherence(self) -> Self:
        # If holding a Z Mega Stone, form must be 'Mega-Z'
        if self.item and is_legal_z_mega_stone(self.item):
            if self.form != "Mega-Z":
                raise ValueError(
                    f"Pokémon holding Z Mega Stone '{self.item}' must have "
                    f"form='Mega-Z' (got '{self.form}')"
                )
        return self


class TeamRoster(BaseModel):
    """A full 6-Pokémon team roster from a verified tournament."""

    model_config = ConfigDict(extra="forbid")

    player_id: str
    player_name: str
    standing: int = Field(ge=1)
    record: str
    pokemon: list[CompetitivePokemon] = Field(min_length=6, max_length=6)


class MatchResult(BaseModel):
    """Match result sourced from verified tournament export (e.g. Limitless)."""

    model_config = ConfigDict(extra="forbid")

    round: int
    match_id: str
    player1_id: str
    player2_id: str
    winner_id: str
    score: str | None = None


class TournamentMetadata(BaseModel):
    """Tournament-level metadata."""

    model_config = ConfigDict(extra="forbid")

    tid: str
    name: str
    date: str
    regulation: str
    tier: str
    format: str = "Pokémon Champions VGC"


class TournamentExport(BaseModel):
    """Full tournament payload matching the verified data contract."""

    model_config = ConfigDict(extra="forbid")

    metadata: TournamentMetadata
    teams: list[TeamRoster]
    matches: list[MatchResult]

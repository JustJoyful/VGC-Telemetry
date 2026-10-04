"""Normalize explicitly published build fields without inferring private information."""

from pydantic import ValidationError

from core.catalog import resolve_id
from core.models.champions import ChampionsPokemon, Diagnostic, ItemSlot, StatPoints


def pokemon_from_fields(row, catalog, evidence, path, diagnostics):
    identifier = row.get("id") or row.get("species") or row.get("name")
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("No published species identity")
    key = resolve_id(identifier, "species", catalog)
    ref = catalog.species.get(key)
    base = ref.base_species_id if ref else key
    fields = {
        "species_id": base,
        "form_id": key if key != base else None,
        "field_evidence": {"species_id": [evidence]},
    }
    for incoming, field, collection in [
        ("ability", "ability_id", "abilities"),
        ("nature", "nature_id", "natures"),
    ]:
        if row.get(incoming) is not None:
            if isinstance(row[incoming], str) and row[incoming].strip():
                fields[field] = resolve_id(row[incoming], collection, catalog)
                fields["field_evidence"][field] = [evidence]
            else:
                diagnostics.append(
                    Diagnostic(
                        code="invalid_attribute",
                        path=path + "." + field,
                        message="Published attribute must be a nonempty string",
                        evidence=[evidence],
                    )
                )
    if "item" in row and row["item"] is not None:
        if isinstance(row["item"], str):
            fields["item"] = (
                ItemSlot(state="empty")
                if row["item"] == ""
                else ItemSlot(state="held", item_id=resolve_id(row["item"], "items", catalog))
            )
            fields["field_evidence"]["item"] = [evidence]
        else:
            diagnostics.append(
                Diagnostic(
                    code="invalid_attribute",
                    path=path + ".item",
                    message="Item must be a string or null",
                    evidence=[evidence],
                )
            )
    attacks = row.get("attacks", row.get("moves"))
    if attacks is not None:
        if (
            not isinstance(attacks, list)
            or len(attacks) > 4
            or any(not isinstance(m, str) or not m.strip() for m in attacks)
        ):
            diagnostics.append(
                Diagnostic(
                    code="invalid_attribute",
                    path=path + ".moves",
                    message="Published moves must be up to four nonempty strings",
                    evidence=[evidence],
                )
            )
        elif attacks:
            moves = [resolve_id(move, "moves", catalog) for move in attacks]
            if len(set(moves)) != len(moves):
                diagnostics.append(
                    Diagnostic(
                        code="invalid_attribute",
                        path=path + ".moves",
                        message="Duplicate published moves",
                        evidence=[evidence],
                    )
                )
            else:
                fields["moves"] = moves
                fields["moves_complete"] = len(attacks) == 4
                fields["field_evidence"]["moves"] = [evidence]
    if row.get("stat_points") is not None:
        try:
            fields["stat_points"] = StatPoints.model_validate(row["stat_points"])
            fields["field_evidence"]["stat_points"] = [evidence]
        except ValidationError as error:
            diagnostics.append(
                Diagnostic(
                    code="invalid_stat_points",
                    path=path + ".stat_points",
                    message=str(error),
                    evidence=[evidence],
                )
            )
    if row.get("evs") is not None or row.get("ivs") is not None or row.get("tera") is not None:
        diagnostics.append(
            Diagnostic(
                code="unsupported_legacy_fields",
                path=path,
                message="Traditional EV/IV/tera fields are not Champions native allocations",
                evidence=[evidence],
            )
        )
    return ChampionsPokemon(**fields)

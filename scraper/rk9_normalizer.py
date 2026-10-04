"""RK9 public team-sheet extraction; masked identities are never promoted to player IDs."""

import re

from pydantic import ValidationError

from core.catalog import to_id, validate_known_fields
from core.models.catalog import CatalogSnapshot
from core.models.champions import (
    ChampionsTournament,
    Diagnostic,
    NormalizationResult,
    SourceCapture,
    TeamRosterV2,
)
from core.source_admission import SourceAdmission, classify_event, document_evidence
from scraper.html import Node, Tree, public_team_links
from scraper.pokemon_fields import pokemon_from_fields


def sheet_fields(body):
    tree = Tree(body).root
    blocks = [n for n in tree.nodes() if n.attrs.get("id") == "lang-EN"]
    if len(blocks) != 1:
        raise ValueError("Expected one English team-sheet section")
    pokemon = list(blocks[0].nodes(class_name="pokemon"))
    if not pokemon:
        raise ValueError("No structured Pokemon slots")
    fields = []
    for mon in pokemon:
        # Species is direct text preceding the language marker, not nickname/translated headings.
        texts = []
        for child in mon.children:
            if isinstance(child, Node) and child.tag == "b":
                break
            if isinstance(child, str):
                texts.append(child)
        species = re.sub(r"\s+", " ", "".join(texts)).strip()
        row = {"species": species, "attacks": [n.text() for n in mon.nodes("span", "badge")]}
        labels = {"Ability:": "ability", "Held Item:": "item", "Stat Alignment:": "nature"}
        for i, child in enumerate(mon.children):
            if isinstance(child, Node) and child.tag == "b" and child.text() in labels:
                following = []
                for sibling in mon.children[i + 1 :]:
                    if isinstance(sibling, Node) and sibling.tag in ["b", "br", "h5"]:
                        break
                    following.append(sibling.text() if isinstance(sibling, Node) else sibling)
                value = re.sub(r"\s+", " ", "".join(following)).strip()
                row[labels[child.text()]] = value
        fields.append(row)
    return fields


def normalize_rk9_capture(
    capture: SourceCapture, admission: SourceAdmission, catalog: CatalogSnapshot
) -> NormalizationResult:
    identified = classify_event(capture, admission)
    if identified.status != "verified":
        return NormalizationResult(
            admission_status=identified.status, diagnostics=identified.diagnostics
        )
    diagnostics = []
    teams = []

    def diagnose(code, path, message, key=None):
        diagnostics.append(
            Diagnostic(
                code=code,
                path=path,
                message=message,
                evidence=[document_evidence(capture, key)] if key else [],
            )
        )

    roster = capture.documents.get("roster")
    if roster is None or roster.status != 200:
        return NormalizationResult(
            admission_status="pending",
            diagnostics=[
                Diagnostic(
                    code="roster_unavailable",
                    path="roster",
                    message="Public entrant document unavailable",
                )
            ],
        )
    root = Tree(roster.body).root
    headers = [n.text().lower() for n in root.nodes("th")]
    # Captured RK9 roster uses first/last name, country, division, trainer, sheet, standing.
    rows = [r for r in root.nodes("tr") if any(r.nodes("td"))]
    if not rows:
        diagnose("unexpected_roster_markup", "roster", "No entrant table rows", "roster")
    entrant_count = 0
    seen = set()
    for i, row in enumerate(rows):
        cells = [n for n in row.children if isinstance(n, Node) and n.tag == "td"]
        if not cells:
            continue
        entrant_count += 1
        links = public_team_links_from_row(row, capture.source_event_id)
        if not links:
            diagnose(
                "missing_entry_identity",
                f"teams.{i}",
                "No stable public event-entry ID; participant row does not prove a published team",
                "roster",
            )
            continue
        if len(links) != 1:
            diagnose(
                "ambiguous_entry_identity",
                f"teams.{i}",
                "Multiple public sheet IDs in one entrant row",
                "roster",
            )
            continue
        entry = next(iter(links))
        if entry in seen:
            diagnose("duplicate_entry", f"teams.{i}", "Repeated public sheet ID", "roster")
            continue
        seen.add(entry)
        key = "team:" + entry
        ev = document_evidence(capture, "roster")
        pokemon = []
        if key not in capture.documents:
            diagnose(
                "team_not_captured",
                f"teams.{i}",
                "Published sheet was not captured (bounded collection)",
                "roster",
            )
        elif capture.documents[key].status != 200:
            diagnose(
                "team_unavailable",
                f"teams.{i}",
                "Published sheet returned an access/HTTP refusal",
                key,
            )
        elif capture.documents[key].url != links[entry]:
            diagnose(
                "team_origin_conflict",
                f"teams.{i}",
                "Captured sheet URL does not match the public entrant link",
                key,
            )
        else:
            try:
                fields = sheet_fields(capture.documents[key].body)
                for slot, fields_row in enumerate(fields):
                    try:
                        pokemon.append(
                            pokemon_from_fields(
                                fields_row,
                                catalog,
                                document_evidence(capture, key),
                                f"teams.{i}.pokemon.{slot}",
                                diagnostics,
                            )
                        )
                    except (ValueError, TypeError, ValidationError) as error:
                        diagnose("invalid_pokemon", f"teams.{i}.pokemon.{slot}", str(error), key)
            except ValueError as error:
                diagnose("unexpected_team_markup", f"teams.{i}", str(error), key)
        # Header layout is checked rather than inferring unknown cells from participant order.
        if len(cells) >= 8 and any("division" in h for h in headers):
            name = " ".join(c.text() for c in cells[1:3]).strip() or None
            division = to_id(cells[4].text()) or None
            standing = (
                int(cells[-1].text())
                if cells[-1].text().isdigit() and int(cells[-1].text()) > 0
                else None
            )
        else:
            name = division = standing = None
            diagnose(
                "unexpected_roster_markup", f"teams.{i}", "Unknown entrant table layout", "roster"
            )
        try:
            teams.append(
                TeamRosterV2(
                    entry_id=f"rk9:{capture.source_event_id}:{entry}",
                    player_id=None,
                    player_name=name,
                    division_id=division,
                    standing=standing,
                    pokemon=pokemon,
                    publication_status="complete"
                    if len(pokemon) == 6
                    else ("partial" if pokemon else "absent"),
                    field_evidence={"entry_id": [ev], "division_id": [ev]},
                )
            )
        except ValidationError as error:
            diagnose("invalid_entry", f"teams.{i}", str(error), "roster")
    identified.metadata.entrant_count = entrant_count
    pairings = capture.documents.get("pairings")
    if pairings:
        diagnose(
            "unlinked_pairings",
            "matches",
            "Pairings page uses lazy round loading and display names; no verified "
            "player-ID linkage",
            "pairings",
        )
    if capture.documents.get("robots"):
        diagnose(
            "robots_policy_override",
            "source",
            "Collection used explicit operator public-scraping authorization",
            "robots",
        )
    result = validate_known_fields(
        ChampionsTournament(metadata=identified.metadata, teams=teams), catalog
    )
    result.diagnostics = diagnostics + result.diagnostics
    return result


def public_team_links_from_row(row, event_id):
    # Serialize only link attributes back into the shared URL validator.
    from html import escape

    anchors = "".join(
        f'<a href="{escape(n.attrs.get("href") or "", quote=True)}"></a>' for n in row.nodes("a")
    )
    return public_team_links(anchors, "https://rk9.gg", event_id)

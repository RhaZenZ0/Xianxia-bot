from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Callable, Mapping


RandBelow = Callable[[int], int]


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def manual_is_forbidden(manual: Mapping[str, Any] | None) -> bool:
    manual = manual or {}
    alignment = str(manual.get("alignment", "")).casefold()
    tags = {str(tag).casefold() for tag in manual.get("tags", [])}
    return alignment == "demonic" or bool(tags.intersection({"forbidden", "demonic", "evil"}))


def technique_is_forbidden(
    technique: Mapping[str, Any] | None,
    manual: Mapping[str, Any] | None = None,
) -> bool:
    """Classify techniques before applying social/crime consequences.

    Older code applied the forbidden-art hook to every manual technique.  The
    classification deliberately accepts multiple content signals so authored
    and generated catalogs both behave correctly.
    """
    technique = technique or {}
    tags = {str(tag).casefold() for tag in technique.get("tags", [])}
    if tags.intersection({"forbidden", "demonic", "evil", "sacrificial", "soul_devouring"}):
        return True
    if int(technique.get("karma_cost", 0)) > 0:
        return True
    return manual_is_forbidden(manual)


def manual_study_karma_cost(manual: Mapping[str, Any] | None) -> int:
    return 1 if manual_is_forbidden(manual) else 0


def forbidden_use_karma_cost(technique: Mapping[str, Any] | None) -> int:
    return max(0, int((technique or {}).get("karma_cost", 0)))


def exposure_score(technique: Mapping[str, Any] | None) -> int:
    return _clamp(max(1, int((technique or {}).get("exposure", 1))), 1, 10)


def witness_chance_percent(exposure: int, *, concealment_active: bool) -> int:
    if not concealment_active:
        return 100
    return _clamp(max(5, int(exposure) * 8), 5, 95)


def resolve_witnessed(
    exposure: int,
    *,
    concealment_active: bool,
    randbelow: RandBelow = secrets.randbelow,
) -> bool:
    chance = witness_chance_percent(exposure, concealment_active=concealment_active)
    if chance >= 100:
        return True
    return int(randbelow(100)) < chance


def effective_exposure(
    exposure: int,
    *,
    witnessed: bool,
    world_rules: Mapping[str, Any] | None = None,
) -> int:
    severity = _clamp(max(1, int(exposure)), 1, 10)
    forbidden_rules = dict((world_rules or {}).get("forbidden_arts", {}))
    concealed_reduces = bool(forbidden_rules.get("concealed_use_reduces_exposure", True))
    if not witnessed and concealed_reduces:
        severity = max(1, severity // 3)
    return severity


def reaction_policy(
    world_rules: Mapping[str, Any] | None,
    *,
    sect_alignment: str | None,
) -> dict[str, int]:
    """Resolve content-driven political impact values.

    world.json stores separate orthodox/demonic public-use profiles.  Fall back
    to the legacy flat keys so older content remains compatible.
    """
    forbidden_rules = dict((world_rules or {}).get("forbidden_arts", {}))
    alignment = str(sect_alignment or "Neutral").casefold()
    profile_key = "demonic_public_use" if alignment == "demonic" else "orthodox_public_use"
    profile = forbidden_rules.get(profile_key)
    if not isinstance(profile, Mapping):
        profile = forbidden_rules
    return {
        "karma_multiplier": max(0, int(profile.get("karma_multiplier", 1))),
        "regional_unrest": max(0, int(profile.get("regional_unrest", 2))),
        "family_stability_loss": max(0, int(profile.get("family_stability_loss", 2))),
        "sect_cohesion_loss": max(0, int(profile.get("sect_cohesion_loss", 2))),
    }


def crime_evidence(exposure: int) -> int:
    return _clamp(45 + max(1, int(exposure)) * 8, 50, 100)


def crime_severity(exposure: int, karma_cost: int) -> int:
    return _clamp(max(1, int(exposure)) + max(0, int(karma_cost)), 1, 10)


def reputation_deltas(exposure: int) -> dict[str, int]:
    exposure = max(1, int(exposure))
    return {
        "Orthodox Society": -max(2, exposure * 2),
        "Demonic Circles": max(1, exposure),
    }


@dataclass(frozen=True)
class ForbiddenUseResult:
    forbidden: bool
    karma_score: int
    karma_cost: int = 0
    exposure: int = 0
    witnessed: bool = False
    world_reaction: dict[str, Any] | None = None
    crime: dict[str, Any] | None = None

    @property
    def impacts(self) -> list[str]:
        return list((self.world_reaction or {}).get("impacts") or [])


class ForbiddenArtsService:
    """Application service for forbidden-manual and forbidden-technique consequences.

    Combat remains responsible for paying resources and resolving damage.  This
    service owns the cross-system consequences: karma, witnesses, world
    simulation, crime/evidence, bounties, and faction reputation.
    """

    def __init__(self, db: Any, simulator: Any):
        self.db = db
        self.simulator = simulator

    async def record_manual_study(
        self,
        *,
        user_id: int,
        manual_id: str,
        manual: Mapping[str, Any],
        first_study: bool,
        current_karma: int,
    ) -> int:
        cost = manual_study_karma_cost(manual) if first_study else 0
        if not cost:
            return int(current_karma)
        return int(
            await self.db.adjust_karma(
                int(user_id),
                -cost,
                reason=f"studied_forbidden_manual:{manual_id}",
            )
        )

    async def resolve_technique_use(
        self,
        *,
        user_id: int,
        technique_id: str,
        technique: Mapping[str, Any],
        manual: Mapping[str, Any] | None,
        location: str,
        game_minute: int,
        concealment_active: bool,
        current_karma: int,
        randbelow: RandBelow = secrets.randbelow,
    ) -> ForbiddenUseResult:
        if not technique_is_forbidden(technique, manual):
            return ForbiddenUseResult(forbidden=False, karma_score=int(current_karma))

        karma_cost = forbidden_use_karma_cost(technique)
        karma_score = int(current_karma)
        if karma_cost:
            karma_score = int(
                await self.db.adjust_karma(
                    int(user_id),
                    -karma_cost,
                    reason=f"forbidden_technique:{technique_id}",
                )
            )
        exposure = exposure_score(technique)
        witnessed = resolve_witnessed(
            exposure,
            concealment_active=bool(concealment_active),
            randbelow=randbelow,
        )
        reaction = await self.simulator.apply_forbidden_art_use(
            user_id=int(user_id),
            technique_id=str(technique_id),
            technique_name=str(technique.get("name", technique_id)),
            location=str(location),
            game_minute=int(game_minute),
            exposure=exposure,
            karma_cost=karma_cost,
            witnessed=witnessed,
        )
        crime = None
        if witnessed:
            crime = await self.db.record_crime(
                int(user_id),
                jurisdiction=str(location),
                crime_type="forbidden_cultivation",
                severity=crime_severity(exposure, karma_cost),
                evidence=crime_evidence(exposure),
                description=f"Witnessed use of forbidden technique {technique.get('name', technique_id)}",
                game_minute=int(game_minute),
                witness_type="public",
                witness_key=f"witnesses:{location}",
            )
            for faction, delta in reputation_deltas(exposure).items():
                await self.db.adjust_reputation(
                    int(user_id),
                    faction,
                    delta,
                    reason=f"witnessed forbidden art: {technique.get('name', technique_id)}",
                )
        return ForbiddenUseResult(
            forbidden=True,
            karma_score=karma_score,
            karma_cost=karma_cost,
            exposure=exposure,
            witnessed=witnessed,
            world_reaction=reaction,
            crime=crime,
        )

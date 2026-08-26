from __future__ import annotations

from dataclasses import dataclass
from typing import Any


RECRUITMENT_RETRY_COOLDOWN_MINUTES = 1440
RECOMMENDATION_RETRY_COOLDOWN_MINUTES = 1440


@dataclass(frozen=True)
class TrialProfile:
    sect_name: str
    location: str
    examiner: str
    trial_name: str
    primary_attribute: str
    secondary_attribute: str
    base_tn: int
    description: str
    recommendation_bonus: int


def recruitment_definition(sects: dict[str, dict[str, Any]], sect_name: str) -> dict[str, Any] | None:
    sect = sects.get(str(sect_name))
    if not sect:
        return None
    rec = sect.get("recruitment")
    return dict(rec) if isinstance(rec, dict) else None


def trial_profile(sects: dict[str, dict[str, Any]], sect_name: str) -> TrialProfile | None:
    rec = recruitment_definition(sects, sect_name)
    if not rec:
        return None
    return TrialProfile(
        sect_name=str(sect_name),
        location=str(rec.get("location") or ""),
        examiner=str(rec.get("examiner") or "Sect Examiner"),
        trial_name=str(rec.get("trial_name") or "Entrance Examination"),
        primary_attribute=str(rec.get("primary_attribute") or "spirit"),
        secondary_attribute=str(rec.get("secondary_attribute") or "will"),
        base_tn=max(8, int(rec.get("base_tn", 14))),
        description=str(rec.get("description") or "A formal sect entrance examination."),
        recommendation_bonus=max(0, int(rec.get("recommendation_bonus", 2))),
    )


def _root_matches(spiritual_root: str, affinities: list[Any]) -> bool:
    root = str(spiritual_root or "").casefold()
    return any(str(item).casefold() in root for item in affinities)


def alignment_adjustment(character: dict[str, Any], rec: dict[str, Any], *, has_recommendation: bool = False) -> tuple[int, str | None]:
    """Return a trial modifier adjustment and optional hard rejection reason.

    Recommendations can vouch for an otherwise suspicious applicant, but they do
    not erase the alignment consequences entirely.
    """
    karma = int(character.get("karma_score", 0))
    preference = str(rec.get("karma_preference") or "neutral").lower()
    if preference == "righteous":
        if karma <= -200 and not has_recommendation:
            return 0, "Your karmic record is too notorious for this orthodox sect to admit you without a trusted sponsor."
        if karma <= -50:
            return -2, None
        if karma >= 50:
            return 1, None
    elif preference == "demonic":
        if karma >= 200 and not has_recommendation:
            return 0, "Your strongly righteous reputation makes this demonic sect unwilling to expose its inner gate without a trusted sponsor."
        if karma >= 50:
            return -2, None
        if karma <= -50:
            return 1, None
    return 0, None


def trial_modifier(
    character: dict[str, Any],
    rec: dict[str, Any],
    *,
    attribute: str,
    family: dict[str, Any] | None = None,
    recommendation_bonus: int = 0,
) -> tuple[int, list[str], str | None]:
    attrs = dict(character.get("attributes") or {})
    modifier = int(attrs.get(str(attribute), 0))
    notes = [f"{str(attribute).title()} {modifier:+d}"]

    path = str(character.get("path") or "")
    path_bonus = int((rec.get("path_bonuses") or {}).get(path, 0))
    if path_bonus:
        modifier += path_bonus
        notes.append(f"{path} fit {path_bonus:+d}")

    affinities = list(rec.get("root_affinities") or [])
    if affinities and _root_matches(str(character.get("spiritual_root") or ""), affinities):
        modifier += 1
        notes.append("root affinity +1")

    family_arch = str((family or {}).get("archetype") or (family or {}).get("id") or "")
    family_bonus = int((rec.get("family_archetype_bonus") or {}).get(family_arch, 0))
    if family_bonus:
        modifier += family_bonus
        notes.append(f"family tradition {family_bonus:+d}")

    alignment_bonus, rejection = alignment_adjustment(
        character, rec, has_recommendation=bool(recommendation_bonus)
    )
    if rejection:
        return modifier, notes, rejection
    if alignment_bonus:
        modifier += alignment_bonus
        notes.append(f"karma/alignment {alignment_bonus:+d}")

    if recommendation_bonus:
        modifier += int(recommendation_bonus)
        notes.append(f"NPC recommendation +{int(recommendation_bonus)}")

    return modifier, notes, None


def recommendation_modifier(
    character: dict[str, Any],
    *,
    faction_reputation: int = 0,
    family: dict[str, Any] | None = None,
    sect_alignment: str = "Neutral",
) -> tuple[int, list[str]]:
    attrs = dict(character.get("attributes") or {})
    base = int(attrs.get("presence", 0))
    notes = [f"Presence {base:+d}"]

    rep_bonus = max(-3, min(3, int(faction_reputation) // 20))
    if rep_bonus:
        base += rep_bonus
        notes.append(f"sect reputation {rep_bonus:+d}")

    influence = int((family or {}).get("influence", 0))
    family_bonus = min(2, max(0, influence // 40))
    if family_bonus:
        base += family_bonus
        notes.append(f"family influence +{family_bonus}")

    karma = int(character.get("karma_score", 0))
    alignment = str(sect_alignment or "Neutral").lower()
    karma_bonus = 0
    if alignment == "orthodox":
        karma_bonus = 1 if karma >= 50 else -1 if karma <= -100 else 0
    elif alignment == "demonic":
        karma_bonus = 1 if karma <= -50 else -1 if karma >= 100 else 0
    if karma_bonus:
        base += karma_bonus
        notes.append(f"karmic reputation {karma_bonus:+d}")

    return base, notes


def trial_outcome(primary_margin: int, secondary_margin: int, *, has_recommendation: bool) -> str:
    successes = int(primary_margin >= 0) + int(secondary_margin >= 0)
    combined = int(primary_margin) + int(secondary_margin)
    if successes == 2 or combined >= 2:
        return "pass"
    if has_recommendation and successes >= 1 and combined >= -2:
        return "conditional_pass"
    return "fail"

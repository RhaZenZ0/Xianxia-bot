from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


RealmName = Callable[[int, str | None], str]
RealmWorld = Callable[[int], str]


def spiritual_sense_stats(character: Mapping[str, Any]) -> dict[str, int]:
    """Return derived Spiritual Sense power, precision, and range.

    Realm/stage growth is deterministic; persistent character bonuses are
    additive.  Keeping this calculation pure makes perception mechanics usable
    from Discord, tests, simulation, and future NPC AI without duplicating rules.
    """
    attrs = character.get("attributes", {}) or {}
    realm_index = int(character.get("realm_index", 0))
    stage = int(character.get("phase", 1))
    spirit = int(attrs.get("spirit", 0))
    insight = int(attrs.get("insight", 0))
    will = int(attrs.get("will", 0))

    realm_factor = realm_index * 6
    stage_factor = max(0, stage - 1)
    power = 4 + spirit * 3 + will + realm_factor + stage_factor
    precision = 3 + insight * 3 + spirit + realm_index * 4 + stage_factor // 2

    if realm_index == 0:
        base_range = 2 + stage * 2
    elif realm_index == 1:
        base_range = 15 + stage * 5
    elif realm_index == 2:
        base_range = 70 + stage * 15
    elif realm_index == 3:
        base_range = 250 + stage * 70
    elif realm_index == 4:
        base_range = 1000 + stage * 450
    elif realm_index == 5:
        base_range = 5000 + stage * 2000
    else:
        base_range = int(25000 * (1.55 ** (realm_index - 6)) * (1 + stage * 0.08))

    power += int(character.get("sense_power_bonus", 0))
    precision += int(character.get("sense_precision_bonus", 0))
    base_range += int(character.get("sense_range_bonus", 0))
    return {
        "power": max(1, power),
        "precision": max(1, precision),
        "range_m": max(1, base_range),
    }


def concealment_power(character: Mapping[str, Any]) -> int:
    """Return the target number contribution for reading a character's aura."""
    attrs = character.get("attributes", {}) or {}
    realm_index = int(character.get("realm_index", 0))
    stage = int(character.get("phase", 1))
    base = int(attrs.get("will", 0)) + int(attrs.get("spirit", 0)) + realm_index * 5 + stage // 2
    base += int(character.get("concealment_bonus", 0))
    if not bool(character.get("concealment_active", 0)):
        # A visible aura still requires precision for an exact read.
        return max(2, base // 3)
    return max(3, base + 8)


def sense_result_tier(margin: int) -> str:
    if margin >= 16:
        return "overwhelming"
    if margin >= 9:
        return "strong"
    if margin >= 3:
        return "success"
    if margin >= -3:
        return "partial"
    if margin >= -9:
        return "failure"
    return "critical_failure"


def sense_precision_check(
    character: Mapping[str, Any],
    *,
    die1: int,
    die2: int,
    target_realm_index: int = 0,
    extra_tn: int = 0,
) -> dict[str, int | str]:
    """Resolve how accurately a probe interprets an aura it can touch."""
    precision = spiritual_sense_stats(character)["precision"]
    tn = 10 + max(0, int(target_realm_index)) * 2 + max(0, int(extra_tn))
    total = int(die1) + int(die2) + precision
    margin = total - tn
    return {
        "total": total,
        "tn": tn,
        "margin": margin,
        "tier": sense_result_tier(margin),
    }


def approximate_realm(
    realm_index: int,
    stage: int,
    *,
    precision_tier: str,
    realm_name: RealmName,
    realm_world: RealmWorld,
    gender: str | None = None,
) -> str:
    name = realm_name(int(realm_index), gender)
    if precision_tier == "overwhelming":
        return f"{name} — Stage {int(stage)}"
    if precision_tier == "strong":
        low = max(1, int(stage) - 1)
        high = min(9, int(stage) + 1)
        return f"{name} — approximately Stage {low}-{high}"
    if precision_tier == "success":
        return name
    return f"a cultivator of the {realm_world(int(realm_index))}"


def hidden_npc_names(
    hidden_masters: Mapping[str, Mapping[str, Any]],
    location: str | None = None,
) -> list[str]:
    return [
        str(name)
        for name, npc in hidden_masters.items()
        if not location or npc.get("location") == location
    ]


def sense_hidden_npc(
    character: Mapping[str, Any],
    npc_name: str,
    roll_total: int,
    *,
    hidden_masters: Mapping[str, Mapping[str, Any]],
    realm_name: RealmName,
    realm_world: RealmWorld,
) -> dict[str, Any]:
    """Resolve a hidden/fake-master probe without leaking secret state by default."""
    npc = hidden_masters[str(npc_name)]
    meta = npc["hidden_master"]
    stats = spiritual_sense_stats(character)
    target = int(meta.get("concealment", 0)) + int(meta.get("deception", 0)) // 2 + 10
    margin = int(roll_total) - target
    tier = sense_result_tier(margin)
    kind = str(meta.get("kind", "real"))
    true_ri = int(meta.get("true_realm_index", 0))
    true_stage = int(meta.get("true_stage", 1))
    dice_total = int(roll_total) - int(stats["power"])
    precision_total = dice_total + int(stats["precision"])
    precision_tn = 10 + max(0, true_ri) * 2
    precision_margin = precision_total - precision_tn
    precision_tier = sense_result_tier(precision_margin)

    result: dict[str, Any] = {
        "tier": tier,
        "target": target,
        "margin": margin,
        "kind": kind,
        "public_name": str(npc_name),
        "public_realm": npc.get("realm", "Unknown"),
        "precision_tier": precision_tier,
        "precision_total": precision_total,
        "precision_tn": precision_tn,
    }
    if tier in {"critical_failure", "failure"}:
        result["reading"] = str(meta.get("sense_false_reading", npc.get("realm", "Nothing unusual")))
        if kind == "fake" and meta.get("projected_realm_index") is not None:
            result["reading"] = approximate_realm(
                int(meta["projected_realm_index"]),
                int(meta.get("projected_stage", 1)),
                precision_tier="success",
                realm_name=realm_name,
                realm_world=realm_world,
            )
        result["reveal"] = "none"
        return result
    if tier == "partial":
        result["reading"] = (
            "The aura does not behave naturally. Something is being concealed, "
            "projected, or deliberately suppressed."
        )
        result["reveal"] = "suspicion"
        return result
    if kind == "fake":
        if tier == "success":
            result["reading"] = (
                "The impressive aura is inconsistent. Its rhythm does not match "
                "the cultivator's dantian."
            )
            result["reveal"] = "projection"
        else:
            if precision_tier in {"success", "strong", "overwhelming"}:
                true_reading = approximate_realm(
                    true_ri,
                    true_stage,
                    precision_tier=precision_tier,
                    realm_name=realm_name,
                    realm_world=realm_world,
                )
            else:
                true_reading = "clearly far weaker than the projected aura, but the exact realm remains unclear"
            result["reading"] = (
                f"The projected aura collapses under scrutiny. True reading: {true_reading}. "
                f"A concealed {meta.get('fraud_item', 'device')} is producing the false pressure."
            )
            result["reveal"] = "fake"
        return result

    realm_gap = true_ri - int(character.get("realm_index", 0))
    if tier == "success":
        result["reading"] = (
            "Your Spiritual Sense meets a seamless void. The absence itself is too "
            "perfect to be natural."
        )
        result["reveal"] = "concealed_expert"
    elif (tier == "strong" or precision_tier in {"critical_failure", "failure", "partial"}) and realm_gap > 5:
        result["reading"] = (
            "For one instant you glimpse an aura vastly beyond your realm, then it "
            "disappears. You cannot determine the exact cultivation."
        )
        result["reveal"] = "vastly_stronger"
    else:
        result["reading"] = (
            "You penetrate part of the concealment: "
            f"{approximate_realm(true_ri, true_stage, precision_tier=precision_tier, realm_name=realm_name, realm_world=realm_world)}. "
            "The target immediately feels your probing sense."
        )
        result["reveal"] = "realm"
    return result

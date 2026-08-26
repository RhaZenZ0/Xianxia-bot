from __future__ import annotations

from dataclasses import dataclass

MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24
DAYS_PER_MONTH = 30
MONTHS_PER_YEAR = 12
MINUTES_PER_DAY = MINUTES_PER_HOUR * HOURS_PER_DAY
MINUTES_PER_MONTH = MINUTES_PER_DAY * DAYS_PER_MONTH
MINUTES_PER_YEAR = MINUTES_PER_MONTH * MONTHS_PER_YEAR

SEASONS = ("Spring", "Summer", "Autumn", "Winter")

# Canonical daily qi cycle. World time now changes actual training efficiency
# rather than serving only as scene flavor. Multipliers stay deliberately modest
# so players are encouraged to plan around time without feeling forced to wait.
PERIOD_CULTIVATION_MULTIPLIERS: dict[str, tuple[float, float]] = {
    "Dawn": (1.10, 1.05),
    "Morning": (1.05, 1.10),
    "Afternoon": (1.00, 1.10),
    "Evening": (1.08, 1.00),
    "Night": (1.15, 0.95),
}

# Seasonal resonance is root-specific. It stacks multiplicatively with the daily
# cycle. Roots not listed here simply receive the daily-cycle modifier.
SEASON_ROOT_AFFINITIES: dict[str, tuple[str, ...]] = {
    "Spring": ("Wood", "Wind", "Wood Root", "Wind Root"),
    "Summer": ("Fire", "Lightning", "Fire Root", "Lightning Root"),
    "Autumn": ("Metal", "Earth", "Metal Root", "Earth Root"),
    "Winter": ("Water", "Ice", "Water Root", "Ice Root"),
}


@dataclass(frozen=True)
class WorldDateTime:
    total_minutes: int
    year: int
    month: int
    day: int
    hour: int
    minute: int
    season: str
    period: str

    @property
    def clock(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    @property
    def display(self) -> str:
        return (
            f"Year {self.year}, Month {self.month}, Day {self.day} — "
            f"{self.clock} ({self.period}, {self.season})"
        )


def period_for_hour(hour: int) -> str:
    hour %= 24
    if 5 <= hour < 7:
        return "Dawn"
    if 7 <= hour < 12:
        return "Morning"
    if 12 <= hour < 17:
        return "Afternoon"
    if 17 <= hour < 20:
        return "Evening"
    return "Night"


def from_game_minutes(total_minutes: int) -> WorldDateTime:
    total_minutes = max(0, int(total_minutes))
    year0, rem = divmod(total_minutes, MINUTES_PER_YEAR)
    month0, rem = divmod(rem, MINUTES_PER_MONTH)
    day0, rem = divmod(rem, MINUTES_PER_DAY)
    hour, minute = divmod(rem, MINUTES_PER_HOUR)
    season = SEASONS[min(3, month0 // 3)]
    return WorldDateTime(
        total_minutes=total_minutes,
        year=year0 + 1,
        month=month0 + 1,
        day=day0 + 1,
        hour=hour,
        minute=minute,
        season=season,
        period=period_for_hour(hour),
    )


def cultivation_speed_modifiers(
    wt: WorldDateTime,
    spiritual_root: str | None = None,
) -> dict[str, float | str | bool]:
    """Return canonical Qi/body cultivation multipliers for the current world time.

    Seasonal root resonance affects Qi cultivation only; Body cultivation follows
    the physical daily cycle. These modifiers are intentionally separate from
    temporary world events such as Qi Storms and from item/effect modifiers.
    """
    qi_mult, body_mult = PERIOD_CULTIVATION_MULTIPLIERS.get(wt.period, (1.0, 1.0))
    root_resonance = bool(
        spiritual_root
        and spiritual_root in SEASON_ROOT_AFFINITIES.get(wt.season, ())
    )
    if root_resonance:
        qi_mult *= 1.10
    return {
        "qi_mult": round(qi_mult, 4),
        "body_mult": round(body_mult, 4),
        "period": wt.period,
        "season": wt.season,
        "root_resonance": root_resonance,
    }


def cultivation_cycle_summary(
    wt: WorldDateTime,
    spiritual_root: str | None = None,
) -> str:
    mods = cultivation_speed_modifiers(wt, spiritual_root)
    lines = [
        f"Qi cultivation: **x{float(mods['qi_mult']):.2f}**",
        f"Body cultivation: **x{float(mods['body_mult']):.2f}**",
    ]
    if mods["root_resonance"]:
        lines.append(f"🌿 Seasonal resonance: **{spiritual_root}** harmonizes with **{wt.season}** (+10% Qi cultivation).")
    return "\n".join(lines)


def advance_anchor(anchor_game_minute: int, amount_minutes: int) -> int:
    return max(0, int(anchor_game_minute) + int(amount_minutes))

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AddressResult:
    title: str
    pinyin: str | None
    hanzi: str | None
    translation: str
    reason: str

    @property
    def display(self) -> str:
        """Default player-facing display: English first, with no Chinese terminology."""
        return f"**{self.translation}**\n{self.reason}"

    @property
    def display_chinese(self) -> str:
        """Optional extended display for players who want pinyin/Hanzi too."""
        chinese = ""
        if self.pinyin and self.hanzi:
            chinese = f" — **{self.pinyin} {self.hanzi}**"
        return f"**{self.translation}**{chinese}\n{self.reason}"


def _style_term(style: str, *, senior: bool) -> tuple[str, str | None, str | None]:
    style = (style or "neutral").lower()
    if style == "masculine":
        return (
            ("Senior Brother", "Shixiong", "师兄")
            if senior
            else ("Junior Brother", "Shidi", "师弟")
        )
    if style == "feminine":
        return (
            ("Senior Sister", "Shijie", "师姐")
            if senior
            else ("Junior Sister", "Shimei", "师妹")
        )
    return (("Senior Martial Sibling", None, None) if senior else ("Junior Martial Sibling", None, None))


def resolve_address(
    observer: dict[str, Any],
    target: dict[str, Any],
    *,
    observer_membership: dict[str, Any] | None,
    target_membership: dict[str, Any] | None,
    observer_master: dict[str, Any] | None,
    target_master: dict[str, Any] | None,
    observer_grandmaster: dict[str, Any] | None,
    observer_master_master: dict[str, Any] | None,
    sibling_rows: list[dict[str, Any]],
    master_sibling_rows: list[dict[str, Any]],
) -> AddressResult:
    """Resolve how observer should formally address target.

    The database supplies concrete lineage rows; this function never invents lineage.
    """
    oid = int(observer["user_id"])
    tid = int(target["user_id"])
    style = str(target.get("address_style") or "neutral")

    if oid == tid:
        return AddressResult("self", None, None, "Self", "A cultivator does not use a sect kinship title for themselves.")

    if observer_master and int(observer_master["user_id"]) == tid:
        return AddressResult("shifu", "Shifu", "师父", "Master", "This cultivator is your direct master.")

    if observer_master_master and int(observer_master_master["user_id"]) == tid:
        return AddressResult(
            "shigong",
            "Shigong / Shiye",
            "师公 / 师爷",
            "Grandmaster",
            "This cultivator is your master's master.",
        )

    # Same direct master: seniority is determined by when each disciple entered that lineage.
    target_sibling = next((r for r in sibling_rows if int(r["user_id"]) == tid), None)
    if target_sibling:
        senior = float(target_sibling["accepted_at"]) < float(observer["lineage_accepted_at"])
        translation, pinyin, hanzi = _style_term(style, senior=senior)
        return AddressResult(
            "sect_sibling",
            pinyin,
            hanzi,
            translation,
            "You share the same master; seniority follows entry into the lineage.",
        )

    # Master's sect-siblings become martial uncles/aunts.
    target_master_sibling = next((r for r in master_sibling_rows if int(r["user_id"]) == tid), None)
    if target_master_sibling and observer_master:
        if style == "feminine":
            return AddressResult(
                "shigu", "Shigu", "师姑", "Martial Aunt", "This cultivator is a sect-sibling of your master."
            )
        target_accepted = float(target_master_sibling["accepted_at"])
        master_accepted = float(observer_master.get("lineage_accepted_at") or 0)
        if style == "masculine" and target_accepted < master_accepted:
            return AddressResult(
                "shibo", "Shibo", "师伯", "Senior Martial Uncle", "This cultivator entered your grandmaster's lineage before your master."
            )
        return AddressResult(
            "shishu",
            "Shishu",
            "师叔",
            "Martial Uncle / Uncle-Master",
            "This cultivator is a sect-sibling of your master. Shishu can also be used generically in this relationship.",
        )

    # If target's master is one of observer's siblings, target is observer's martial nephew/niece.
    if target_master and any(int(r["user_id"]) == int(target_master["user_id"]) for r in sibling_rows):
        return AddressResult(
            "shizhi",
            "Shizhi",
            "师侄",
            "Martial Nephew / Martial Niece",
            "This cultivator is the disciple of one of your sect-siblings.",
        )

    # Target is observer's own disciple.
    if target_master and int(target_master["user_id"]) == oid:
        return AddressResult("disciple", None, None, "Disciple", "This cultivator is your direct disciple.")

    same_sect = bool(
        observer_membership
        and target_membership
        and observer_membership.get("sect_name") == target_membership.get("sect_name")
    )

    # Fallback Senior/Junior is relative and considers sect rank before realm attainment.
    if same_sect:
        o_rank = int(observer_membership.get("rank_level", 0))
        t_rank = int(target_membership.get("rank_level", 0))
        if t_rank != o_rank:
            if t_rank > o_rank:
                return AddressResult("senior", "Qianbei", "前辈", "Senior", "Their formal sect rank is above yours.")
            return AddressResult("junior", "Wanbei", "晚辈", "Junior", "Their formal sect rank is below yours.")

    o_power = (int(observer.get("realm_index", 0)), int(observer.get("phase", 1)))
    t_power = (int(target.get("realm_index", 0)), int(target.get("phase", 1)))
    if t_power > o_power:
        return AddressResult("senior", "Qianbei", "前辈", "Senior", "Their demonstrated cultivation generation/expertise is above yours.")
    if t_power < o_power:
        return AddressResult("junior", "Wanbei", "晚辈", "Junior", "Their demonstrated cultivation generation/expertise is below yours.")

    return AddressResult(
        "peer",
        None,
        None,
        "Fellow Cultivator",
        "No direct lineage title or clear seniority applies.",
    )


TERMINOLOGY_PROMPT = """
SECT KINSHIP AND FORMS OF ADDRESS
- Shigong/Shiye 师公/师爷: master's master; Grandmaster / Martial Grandfather.
- Shifu 师父: direct master.
- Shibo 师伯: master's senior male sect-brother.
- Shishu 师叔: master's junior male sect-brother; may also be used generically for a master's sect-sibling.
- Shigu 师姑: master's female sect-sibling; Martial Aunt.
- Shizhi 师侄: disciple of one's sect-brother/sister; applies to martial nephews and nieces.
- Shixiong 师兄 / Shijie 师姐: senior male/female sibling under the same master.
- Shidi 师弟 / Shimei 师妹: junior male/female sibling under the same master.
- Qianbei 前辈: Senior; Wanbei 晚辈: Junior. These are relative and can reflect generation, status, expertise, or rank.
- Lao 老 + surname can be used respectfully as 'Elder X' when established by status/context.
- Xiao 小 + given name and -er 儿 are familiar/endearing forms and should only be used when relationship/context supports them.
- Never infer an epithet such as 'Fatty X' from appearance. Use such nicknames only when explicitly established in canonical context.
- Do not invent master/disciple relationships. Use a lineage title only when canonical sect relationship context supports it.
- In normal English narration and dialogue, use the English address as the default: Master, Grandmaster, Senior Brother, Junior Brother, Senior Sister, Junior Sister, Martial Uncle, Martial Aunt, Martial Nephew/Niece, Senior, or Junior.
- Pinyin/Hanzi terms are flavor only. Use them sparingly when a character explicitly prefers them or when the scene is teaching terminology.
""".strip()

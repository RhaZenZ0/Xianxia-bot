"""Author the sect's people and its doors by rank (v1.25.0).

Three blocks under `sect_system`, on the owner's calls:

* `rank_floors` - the sect rank an engine operation asks, keyed by operation so
  the refusal and the panel's padlock read one table. Outer Disciple (10, what
  joining makes you) holds every other sect door; taking a disciple asks Inner;
  claiming territory and fighting in a war ask Core. The manor's two ranks keep
  their own keys in `sect_abode_system`, so nothing is stated twice.
* `npc_master` - what one of a sect's own people is as a master: who may be
  asked, how many players they take, what they give (a breakthrough term, insight
  on a realm crossing, a cultivation term, and the sect's next manual from a rank),
  and the rank from which an NPC grants a promotion without being your master.
* `player_master` - what a master who is another player gives their disciple,
  on the owner's call: +1 on every trade roll (craft, forage, dig) and the same
  x1.05 on cultivation an NPC master gives, on the qi path and the body path.
* `population` - how many of each rank a sect keeps (about twenty-five), how far
  above the sect's world floor each rank stands, and which ranks keep the gate.
  The engine tops a sect up to this each politics tick; catalogue members count.

It also retunes `sect_system.tribute.disciples_per_lot` from 2 to 6. Tribute is
one lot per that many living members a week: a sect used to hold two people
from the content file and stock one lot, and a full hall of about twenty-seven
at the old rate would stock thirteen - a treasury at its cap of sixty in five
weeks. At six a full hall stocks four lots, what a sect that had recruited
eight members used to.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "content" / "world.json"

DISCIPLES_PER_LOT = 6

RANK_FLOORS = {"discipleship.resolve": 20, "territory.claim": 30, "war.act": 30}

NPC_MASTER = {
    "min_rank_level": 30,
    "max_disciples": 3,
    "breakthrough_bonus": 1,
    "insight_on_realm": 5,
    "cultivation_mult": 1.05,
    "teach_rank_level": 20,
    "promoter_rank_level": 50,
}

PLAYER_MASTER = {"trade_roll_bonus": 1, "cultivation_mult": 1.05}

POPULATION = {
    "ranks": {"Sect Master": 1, "Elder": 3, "Core Disciple": 5, "Inner Disciple": 7, "Outer Disciple": 9},
    "realm_offset": {
        "Outer Disciple": [0, 1],
        "Inner Disciple": [1, 2],
        "Core Disciple": [2, 4],
        "Elder": [4, 6],
        "Sect Master": [6, 7],
    },
    "at_gate": ["Sect Master", "Elder"],
    "max_new_per_tick": 400,
    # A sect's people are named surname + given name from these two lists, by a
    # hash of the sect, rank and slot, so the same world makes the same people.
    # The event cast's 24-name pool is far too small for three hundred.
    "surnames": [
        "Bai", "Cao", "Chen", "Cui", "Deng", "Ding", "Du", "Fan", "Fang", "Feng",
        "Gao", "Gu", "Guo", "Han", "He", "Hou", "Hua", "Huang", "Jiang", "Jin",
        "Kong", "Lan", "Li", "Liang", "Lin", "Liu", "Lu", "Luo", "Ma", "Meng",
        "Mo", "Ning", "Ou", "Pan", "Pei", "Qian", "Qiao", "Qin", "Ren", "Rong",
        "Shao", "Shen", "Shi", "Song", "Su", "Sun", "Tan", "Tang", "Tao", "Wan",
        "Wang", "Wei", "Wen", "Wu", "Xia", "Xiao", "Xie", "Xu", "Xue", "Yan",
        "Yang", "Yao", "Ye", "Yin", "Yu", "Yuan", "Yue", "Zeng", "Zhang", "Zhao",
        "Zheng", "Zhou", "Zhu", "Zhuo", "Zuo",
    ],
    "given_names": [
        "An", "Bing", "Chang", "Chen", "Chun", "Dai", "Feng", "Fu", "Gang", "Guang",
        "Hai", "Han", "Hao", "He", "Heng", "Hong", "Hua", "Hui", "Jian", "Jie",
        "Jing", "Jun", "Kai", "Kang", "Lan", "Lei", "Li", "Lian", "Ling", "Long",
        "Mei", "Min", "Ming", "Mu", "Nan", "Ning", "Peng", "Ping", "Qi", "Qian",
        "Qing", "Qiu", "Rong", "Rui", "Shan", "Sheng", "Shu", "Song", "Tao", "Tian",
        "Ting", "Wei", "Wen", "Xian", "Xiang", "Xin", "Xiu", "Xuan", "Xue", "Ya",
        "Yan", "Yang", "Yao", "Yi", "Yin", "Ying", "Yong", "You", "Yu", "Yuan",
        "Yue", "Yun", "Ze", "Zhen", "Zhi", "Zhong", "Zhu", "Zi", "Ziyan", "Zhuo",
    ],
}


def main() -> None:
    w = json.loads(PATH.read_text(encoding="utf-8"))
    system = w["sect_system"]
    names = {str(r["name"]): int(r["level"]) for r in system["ranks"]}
    for rank in POPULATION["ranks"]:
        if rank not in names:
            raise SystemExit(f"{rank!r} is not on sect_system.ranks")
    for pool in ("surnames", "given_names"):
        if len(set(POPULATION[pool])) != len(POPULATION[pool]):
            raise SystemExit(f"population.{pool} repeats a name")
    if set(POPULATION["realm_offset"]) != set(POPULATION["ranks"]):
        raise SystemExit("every populated rank needs a realm offset")
    levels = set(names.values())
    for op, floor in RANK_FLOORS.items():
        if floor not in levels:
            raise SystemExit(f"{op}: {floor} is not a rank level")
    for key in ("min_rank_level", "teach_rank_level", "promoter_rank_level"):
        if NPC_MASTER[key] not in levels:
            raise SystemExit(f"npc_master.{key}: {NPC_MASTER[key]} is not a rank level")
    system["rank_floors"] = RANK_FLOORS
    system["npc_master"] = NPC_MASTER
    system["player_master"] = PLAYER_MASTER
    system["population"] = POPULATION
    system["tribute"]["disciples_per_lot"] = DISCIPLES_PER_LOT
    PATH.write_text(json.dumps(w, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(RANK_FLOORS)} rank floors, npc_master, population of {sum(POPULATION['ranks'].values())} a sect")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Drive the roadmap's playtest loops through the Go engine (v0.34.0; the city,
its shops and the merchants since v0.36.1).

The v0.34 playtest is a written pass over the live server. This is the half a
machine can run without Discord: every loop the roadmap names, driven through
the engine's HTTP API exactly as the bot's handlers drive it, against a scratch
database. It prints one line per step - PASS, FAIL or SKIP with the engine's
own message - and exits non-zero if anything failed, so it can run before a
release and again after any engine change.

    python3 scripts/playtest_engine.py --launch     # build, start and bootstrap a scratch engine, run, stop
    GAME_ENGINE_URL=http://127.0.0.1:8081 ENGINE_AUTH_TOKEN=... python3 scripts/playtest_engine.py

Never point it at the production database: it creates characters, moves
them, mutes them, edits quests and restores a backup.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from playtest_common import ROOT, Report, bootstrap, launch_engine, step, stop_engine  # noqa: E402 - beside this file

PLAYER = 900001
BUYER = 900002
GHOST = 900003
GM = 1

# Operations this harness does not drive, each with its reason.
# `tests/python/contracts/test_playtest_coverage.py` holds every operation
# the engine answers to a driver call in this file or to an entry here, and
# fails a stale entry (deferred and driven, or no longer an operation). Each
# block below is one later PR that empties it.
DEFERRED_OPERATIONS: dict[str, str] = {
    # PR 2: the sect and the homestead. A sect residence, its manor and its
    # treasury need membership at rank (admin.player.set_sect) and
    # contribution points that only sect.contribute builds; a homestead's
    # founding rank rides the same lever.
    "sect.abode.enter": "PR 2: the sect and the homestead - needs a sect_abodes row the sect assigns to a member",
    "sect.abode.leave": "PR 2: the sect and the homestead - needs the residence entered first",
    "sect.abode.upgrade": "PR 2: the sect and the homestead - needs contribution points built by sect.contribute",
    "sect.shadow": "PR 2: the sect and the homestead - the hidden sect's initiation at karma -80",
    "sect.recruitment.recommendation": "PR 2: the sect and the homestead - a recommendation roll, reported",
    "sect.contribute": "PR 2: the sect and the homestead - items into the sect treasury for points",
    "sect.redeem": "PR 2: the sect and the homestead - points back out of the treasury",
    "sect.manor.establish": "PR 2: the sect and the homestead - rank 70 and a funded treasury",
    "sect.manor.upgrade": "PR 2: the sect and the homestead - rank 50 and a funded treasury",
    "discipleship.request": "PR 2: the sect and the homestead - two members of one sect at different realms",
    "discipleship.resolve": "PR 2: the sect and the homestead - the master answers the request",
    "discipleship.leave": "PR 2: the sect and the homestead - the disciple leaves the lineage",
    "territory.claim": "PR 2: the sect and the homestead - a claim by a sect member",
    "war.act": "PR 2: the sect and the homestead - a war needs a territory a second sect already holds",
    "abode.establish": "PR 2: the sect and the homestead - a homestead's founding rank rides set_sect",
    "abode.enter": "PR 2: the sect and the homestead - needs the property established",
    "abode.visit": "PR 2: the sect and the homestead - the buyer visits on an invitation",
    "abode.leave": "PR 2: the sect and the homestead - needs the property entered",
    "abode.invite": "PR 2: the sect and the homestead - needs the property established",
    "abode.revoke": "PR 2: the sect and the homestead - needs the invitation first",
    "abode.upgrade": "PR 2: the sect and the homestead - a facility bought with stones",
    "abode.focus": "PR 2: the sect and the homestead - needs a built facility to sit in",
    "admin.player.set_abode_access": "PR 2: the sect and the homestead - the lever edits access to a property the owner must have",
    # PR 3: progression. Perfection and a tribulation attempt need the stage
    # filled with essence (the fill rule is read before it is driven), the
    # aptitudes a bloodline or physique at progress 100, a personal world
    # Space Law at 100% through repeated law.comprehend.
    "perfection.start": "PR 3: progression - needs the stage filled with essence at phase 9",
    "perfection.quest": "PR 3: progression - needs the path started",
    "perfection.trial": "PR 3: progression - needs every quest complete",
    "perfection.body_start": "PR 3: progression - needs the body stage filled at phase 9",
    "perfection.body_quest": "PR 3: progression - needs the body path started",
    "perfection.body_trial": "PR 3: progression - needs every body quest complete",
    "tribulation.prepare": "PR 3: progression - a world-crossing gate at realm 7 phase 9 with the world's currency",
    "tribulation.attempt": "PR 3: progression - needs the stage filled at the gate",
    "aptitude.temper": "PR 3: progression - a bloodline or physique set by the GM, then essence spent",
    "aptitude.awaken": "PR 3: progression - a bloodline or physique at progress 100",
    "aptitude.evolve": "PR 3: progression - an awakened bloodline or physique",
    "aptitude.harmonize": "PR 3: progression - a bloodline's rejection brought down with essence",
    "cultivation.body_breakthrough": "PR 3: progression - body essence filled through body_train first",
    "personal_world.create": "PR 3: progression - Space Law at 100% and realm 30",
    "personal_world.set_rule": "PR 3: progression - needs the world created",
    "personal_world.enter": "PR 3: progression - needs the world created",
    "personal_world.leave": "PR 3: progression - needs the world entered",
    # PR 4: what only the world makes. No GM lever writes a wild beast
    # encounter, a running world event and its nodes, a bounty pursuit or a
    # missing NPC; the simulation batches and the hunt and exploration rolls
    # do, so PR 4 forces those in a bounded loop and holds the designed
    # refusal when none appears.
    "beast.tame": "PR 4: what only the world makes - a wild_beast_encounters row from the hunt or the exploration roll",
    "beast.feed": "PR 4: what only the world makes - needs a tamed beast",
    "beast.train": "PR 4: what only the world makes - needs a tamed beast",
    "beast.evolve": "PR 4: what only the world makes - needs a tamed beast at loyalty",
    "beast.active": "PR 4: what only the world makes - needs a tamed beast",
    "admin.player.set_beast_stats": "PR 4: what only the world makes - edits a tamed beast's row",
    "bounty_hunter.act": "PR 4: what only the world makes - a bounty_hunter_pursuits row from a crime chain",
    "npc.found": "PR 4: what only the world makes - an NPC gone missing on the npc_life tick",
}


async def run(url: str, token: str, db_path: str) -> Report:
    os.environ["GAME_ENGINE_URL"] = url
    os.environ["ENGINE_AUTH_TOKEN"] = token
    from app.database import Database
    from app.database.remote import GoDatabaseTransport
    from app.ops.game_engine import GameEngineClient, GameEngineError
    from app.rules.quests import QUEST_DEFINITIONS, beginner_path_seed_rows, household_errand_seed_rows, static_quest_seed_rows
    from app.rules.game import World

    world = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
    engine = GameEngineClient(url, auth_token=token)
    transport = GoDatabaseTransport(url, auth_token=token)
    db = Database(Path(db_path), engine_url=url)
    report = Report()
    seq = [0]

    def aid(op: str) -> str:
        seq[0] += 1
        return f"playtest:{seq[0]}:{op}"

    async def act(op: str, uid: int, payload: dict[str, Any]) -> dict[str, Any]:
        envelope = await engine.authoritative_action(op, uid, payload, action_id=aid(op))
        return dict(envelope.get("result") or {})

    async def gm(op: str, payload: dict[str, Any]) -> Any:
        return await engine.action(op, GM, payload)

    async def query(op: str, uid: int, payload: dict[str, Any]) -> Any:
        """An authoritative read: no action id, the actor's own view."""
        return await engine.action(op, uid, payload)

    async def clock() -> int:
        return int((await db.get_world_clock())["game_minute"])

    async def either(name: str, coro, *designed: str) -> Any:
        """A step whose refusal is designed either way: PASS on a result, PASS
        on a refusal naming one of `designed`, FAIL on anything else."""
        try:
            result = await coro
        except GameEngineError as exc:
            if any(text in str(exc) for text in designed):
                report.add("PASS", name, f"refused as designed: {exc}")
            else:
                report.add("FAIL", name, str(exc))
            return None
        except Exception as exc:  # noqa: BLE001 - a playtest reports, it does not crash
            report.add("FAIL", name, f"{type(exc).__name__}: {exc}")
            return None
        report.add("PASS", name)
        return result

    async def audited(op: str, payload: dict[str, Any], *, name: str = "", action: str = "") -> Any:
        """A GM lever: it runs, and the newest audit row names it (or the
        action it was asked to record)."""
        result = await step(report, name or op, gm(op, payload))
        if result is not None:
            rows = await db.get_admin_audit_log(1)
            newest = str((rows or [{}])[0].get("action") or "")
            report.add("PASS" if newest == (action or op) else "FAIL", f"{name or op} is audited", newest)
        return result

    # ---- 0. the world ------------------------------------------------------
    await step(report, "seed the catalog", db.sync_world_catalog(world))
    content = World(ROOT / "content" / "world.json")
    await step(report, "seed the commission pool, static quests, the beginner path and the household errands",
               db.sync_commission_pool(list(world.get("commissions") or []) + static_quest_seed_rows(QUEST_DEFINITIONS)
                                       + beginner_path_seed_rows(content) + household_errand_seed_rows(content)))
    gm0 = await step(report, "world clock", clock())
    await step(report, "simulation bootstrap", engine.bootstrap_simulation(int(gm0 or 0)))

    # ---- 0b. the content file as tables (schema 51) ------------------------
    # The engine writes content_* from world.json, hash-gated. db-init already
    # synced once after the migration in this harness; asking again must be a
    # no-op that still reports the counts, and the counts must be the file's.
    synced = await step(report, "content sync", transport.sync_content())
    if synced is not None:
        ts = dict(world.get("technique_system") or {})
        expected = {
            "content_npcs": len(world.get("npcs") or {}), "content_locations": len(world.get("locations") or {}),
            "content_items": len(world.get("items") or {}), "content_recipes": len(world.get("recipes") or {}),
            "content_sects": len(world.get("sects") or {}), "content_shops": len(world.get("shops") or {}),
            "content_merchants": len(world.get("merchants") or {}),
            "content_manuals": len(ts.get("manuals") or {}), "content_techniques": len(ts.get("techniques") or {}),
        }
        counts = {str(k): int(v) for k, v in dict(synced.get("counts") or {}).items()}
        wrong = {t: (counts.get(t), n) for t, n in expected.items() if counts.get(t) != n}
        report.add("PASS" if not wrong and not synced.get("skipped") else "FAIL", "every content table holds the file's rows",
                   f"{sum(counts.values())} rows" if not wrong else f"mismatch {wrong}")
        report.add("PASS" if not synced.get("applied") else "FAIL", "an unchanged file is not rewritten", f"hash {str(synced.get('hash') or '')[:12]}")
        npc = await step(report, "a catalogue NPC is read back through content_npcs", db.get_npc_definition("Elder Su Yan"))
        if npc is not None:
            report.add("PASS" if npc.get("role") and npc.get("location") else "FAIL", "the blob is the whole entry", f"{sorted(npc)[:6]}...")
        reloaded = await step(report, "admin.content.reload", gm("admin.content.reload", {"reason": "playtest"}))
        if reloaded is not None:
            report.add("PASS" if not reloaded.get("applied") and reloaded.get("hash") == synced.get("hash") else "FAIL",
                       "the GM reload sees the same file and changes nothing", f"applied={reloaded.get('applied')}")
            audit = await step(report, "the reload is audited", db.get_admin_audit_log(10))
            if audit is not None:
                report.add("PASS" if any(str(r.get("action")) == "admin.content.reload" for r in audit) else "FAIL",
                           "admin_audit_log carries admin.content.reload", f"{len(audit)} recent rows")

    # ---- 1. begin ----------------------------------------------------------
    for uid, name in ((PLAYER, "Playtest Lin"), (BUYER, "Playtest Bidder")):
        offers = await step(report, f"family options for {uid}", act("character.family_options", uid, {"world_name": "Mortal World"}))
        families = list((offers or {}).get("families") or [])
        if not families:
            report.add("FAIL", f"create {uid}", "no family offers")
            continue
        created = await step(report, f"character.create {uid}", act("character.create", uid, {
            "discord_name": name, "name": name, "concept": "a playtest cultivator", "gender": "female",
            "path": "Sword Cultivator", "family_choice_id": str(families[0].get("choice_id") or ""), "age_at_creation_years": 18,
        }))
        if created is not None and not created.get("created"):
            report.add("FAIL", f"character.create {uid}", f"created=false: {created.get('reason')}")

    # ---- 1b. what the household taught (v1.0.0-rc.31) -------------------------
    # families[0] is the Martial Household: trade Forging, Wealth 42, which is
    # the 30-XP band - so the head start and the tradition bonus are both
    # certain by the scenario, not by a roll.
    taught = await step(report, "the household's profession row", db.get_profession_progress(PLAYER, "Forging"))
    if taught is not None:
        report.add("PASS" if taught and int(taught.get("xp", -1)) == 30 and int(taught.get("level", -1)) == 0 else "FAIL",
                   "a journeyman in the family starts you halfway to Apprentice", f"{taught}")
    await step(report, "teleport to Greenriver Town", gm("admin.player.teleport", {"user_id": PLAYER, "location": "Greenriver Town", "reason": "playtest"}))
    sword = dict(world["recipes"]["Spirit-Iron Sword"])
    for item_id, qty in dict(sword.get("cost") or {}).items():
        await step(report, f"grant {item_id} x{qty} for the forge", gm("admin.player.adjust_item", {"user_id": PLAYER, "item_id": item_id, "quantity": int(qty), "reason": "playtest"}))
    forged = await step(report, "craft.resolve a Forging recipe the household taught", act("craft.resolve", PLAYER, {"recipe": "Spirit-Iron Sword"}))
    if forged is not None:
        report.add("PASS" if int(forged.get("family_bonus", 0)) == 2 and str(forged.get("family_trade")) == "Forging" else "FAIL",
                   "a Forging house's tradition rides a Forging roll", f"family_bonus={forged.get('family_bonus')} trade={forged.get('family_trade')!r}")

    # ---- 1c. a house worth coming back to (v1.0.0-rc.32) -------------------
    # The household's gifts are asked for inside it, the door opens from its
    # town, and two talismans make the round trip from anywhere. Every step
    # here is decided by the scenario - the Martial Household's wealth of 42,
    # a contribution of a known size - and never by a roll.
    fam = await step(report, "the birth family", db.get_birth_family(PLAYER))
    home_town = str((fam or {}).get("location") or "Riverguard City")
    far_town = "Greenriver Town" if home_town != "Greenriver Town" else "Riverguard City"
    household = f"birth_family:{int((fam or {}).get('family_id') or 0)}"
    await step(report, f"teleport to {far_town}, away from home", gm("admin.player.teleport", {"user_id": PLAYER, "location": far_town, "reason": "playtest"}))
    await step(report, "family.support away from home is refused", act("family.support", PLAYER, {"cooldown_game_minutes": 100}),
               expect_error="asked for at home")
    await step(report, "the door does not open from another town", act("family.household.enter", PLAYER, {}),
               expect_error="travel there first")
    burned = await step(report, "a Hearth-Return Talisman from the send-off carries you home", act("item.use", PLAYER, {"item_id": "hearth_return_talisman"}))
    if burned is not None:
        home = dict(burned.get("homeward") or {})
        report.add("PASS" if home.get("location") == household and home.get("return_location") == far_town else "FAIL",
                   "the talisman marks where it found you", f"{home}")
    supported = await step(report, "family.support at home", act("family.support", PLAYER, {"cooldown_game_minutes": 100}))
    if supported is not None:
        report.add("PASS" if int(supported.get("standing_bonus", -1)) == 0 else "FAIL", "a stranger to the ledger earns no standing term", f"{supported.get('standing_bonus')}")
    # Support just cost the house a few stones of wealth, so the figures are
    # relative to what it holds now rather than to the archetype's 42.
    before = await step(report, "the family after support", db.get_birth_family(PLAYER)) or {}
    await step(report, "grant 400 stones for the coffers", gm("admin.player.grant_currency", {"user_id": PLAYER, "currency_id": "low_spirit_stone", "amount": 400, "reason": "playtest"}))
    given = await step(report, "family.contribute 240", act("family.contribute", PLAYER, {"amount": 240}))
    if given is not None:
        want_wealth = min(100, int(before.get("wealth", 0)) + 48)
        report.add("PASS" if int(given.get("treasury_balance", 0)) == int(before.get("treasury_balance", 0)) + 240 and int(given.get("wealth", 0)) == want_wealth and int(given.get("standing", 0)) == 10 else "FAIL",
                   "240 stones: coffers +240, wealth +48, standing capped at 10 for one gift", f"{given} (wealth before {before.get('wealth')})")
    taught_again = await step(report, "family.tutor after the house grew richer", act("family.tutor", PLAYER, {}))
    if taught_again is not None:
        report.add("PASS" if int(taught_again.get("level", 0)) == 1 and taught_again.get("tutor") == "a master retained" else "FAIL",
                   "a master retained makes you an Apprentice of Forging", f"{taught_again}")
    await step(report, "family.tutor again is refused", act("family.tutor", PLAYER, {}), expect_error="taught you all it can")
    errand = await step(report, "family.errand", act("family.errand", PLAYER, {}))
    if errand is not None:
        report.add("PASS" if str(errand.get("quest_key", "")).startswith("errand_forging_") else "FAIL", "a Forging house asks a Forging errand", f"{errand.get('quest_key')}")
    await step(report, "a second errand while one is carried is refused", act("family.errand", PLAYER, {}), expect_error="finish the errand")
    await step(report, "grant a Waymark Talisman", gm("admin.player.adjust_item", {"user_id": PLAYER, "item_id": "waymark_talisman", "quantity": 1, "reason": "playtest"}))
    marked = await step(report, "a Waymark Talisman takes you back to the mark", act("item.use", PLAYER, {"item_id": "waymark_talisman"}))
    if marked is not None:
        report.add("PASS" if dict(marked.get("waymark") or {}).get("location") == "Greenriver Town" else "FAIL", "back where the hearth talisman found you", f"{marked.get('waymark')}")
    await step(report, f"teleport to {home_town}, the family's town", gm("admin.player.teleport", {"user_id": PLAYER, "location": home_town, "reason": "playtest"}))
    walked = await step(report, "the door opens from the family's town", act("family.household.enter", PLAYER, {}))
    if walked is not None:
        report.add("PASS" if walked.get("location") == household and not walked.get("return_location") else "FAIL", "walking in leaves no mark", f"{walked}")
    sat = await step(report, "cultivation.train at the hearth", act("cultivation.train", PLAYER, {"cooldown_seconds": 0}))
    if sat is not None:
        report.add("PASS" if str(sat.get("place_name", "")).endswith("Household") and float(sat.get("place_mult", 1)) > 1 else "FAIL",
                   "the family's hall is a good place to sit", f"{sat.get('place_name')} x{sat.get('place_mult')}")
    # The head's last lesson (v1.0.0-rc.34) is one demonstration check, so the
    # outcome is reported and only what is certain either way is asserted: the
    # check is named, a pass qualifies every trade, and a second ask is refused
    # - as "already taught" after a pass, as the day's wait after a fail.
    lesson = await step(report, "family.lesson at home", act("family.lesson", PLAYER, {}))
    if lesson is not None:
        check = dict(lesson.get("check") or {})
        report.add("PASS" if {"total", "tn", "degree"} <= set(check) and lesson.get("outcome") in ("pass", "fail") else "FAIL",
                   "the head names the check", f"{lesson.get('attribute')} {check.get('total')} vs TN {check.get('tn')} - {lesson.get('outcome')}")
        if lesson.get("outcome") == "pass":
            trades = {str(dict(t).get("profession")) for t in (lesson.get("trades") or [])}
            manual = dict(lesson.get("manual") or {})
            report.add("PASS" if trades == {"Forging", "Inscription", "Formation", "Alchemy"} and manual.get("item_id") and lesson.get("keepsake") else "FAIL",
                       "a pass qualifies all four trades and hands over the house's manual and keepsake", f"{sorted(trades)} {manual.get('name')} {lesson.get('keepsake')}")
            await step(report, "the lesson is given once per life", act("family.lesson", PLAYER, {}), expect_error="taught you all this lesson holds")
        else:
            await step(report, "a failed lesson waits a world day", act("family.lesson", PLAYER, {}), expect_error="hear you again in")
    await step(report, "family.household.leave", act("family.household.leave", PLAYER, {}))
    await step(report, "teleport back to Greenriver Town", gm("admin.player.teleport", {"user_id": PLAYER, "location": "Greenriver Town", "reason": "playtest"}))

    # ---- 2. $ I explore ----------------------------------------------------
    explored = await step(report, "exploration.explore", act("exploration.explore", PLAYER, {
        "cooldown_seconds": 0, "unexpected_event_chance_percent": 0, "event_key": aid("exploration:event")}))
    if explored is not None and not (explored.get("narration") or explored.get("summary") or explored.get("encounter") or explored):
        report.add("FAIL", "exploration.explore", "empty result")

    # ---- 3. join a sect and study the gift ---------------------------------
    sect = "Azure Cloud Sect"
    rec = dict(world["sects"][sect]["recruitment"])
    await step(report, "sect.discover", engine.action("sect.discover", PLAYER, {"sects": [sect], "source_keys": {sect: "playtest"}, "discovery_kind": "recruitment_route", "game_minute": await clock()}))
    await step(report, "teleport to the trial", gm("admin.player.teleport", {"user_id": PLAYER, "location": rec["location"], "reason": "playtest"}))
    # The trial is dice, and it used to be asserted on: twelve attempts with a
    # GM cooldown reset between them, and a FAIL if none passed. At the
    # created character's numbers that is a 3.5% flake per run, which is the
    # rule in CLAUDE.md exactly - never assert that a random thing happened.
    # Certain by the scenario instead. `sectTrialActionGo` rolls
    #   primary   2d10 + body + 2*realm + phase/3   vs max(10, 15 - rep/25)
    #   secondary 2d10 + insight + spirit/2 + realm vs max(8, TN - 1)
    # and passes on both, or on a combined margin >= 2. (The content's
    # base_tn 14, path bonus and recommendation bonus are not read; the TNs
    # are 15 and 14, hardcoded, and a recommendation only unlocks a
    # conditional pass.) No GM lever writes attributes or grants an effect;
    # the one that moves the roll is the realm. A Sword Cultivator (body 2,
    # insight 1, spirit 2) at realm 7 stage 9 has, on the worst dice,
    #   primary margin   2 + 2 + 14 + 3 - 15 = 6
    #   secondary margin 2 + 1 + 1 + 7 - 14 = -3   -> combined 3 >= 2: pass.
    # The gift does not change with the realm (the sect's own manual is
    # preferred), and the realm is put back right after so nothing
    # downstream meets a different character.
    await step(report, "stand at realm 7 stage 9: the trial cannot be failed", gm("admin.player.set_realm", {"user_id": PLAYER, "realm_index": 7, "phase": 9, "reason": "playtest: an overwhelming candidate"}))
    trial = await step(report, "sect.recruitment.trial", act("sect.recruitment.trial", PLAYER, {
        "sect_name": sect, "examiner": rec["examiner"], "location": rec["location"], "trial_name": rec["trial_name"],
        "primary_details": {"modifier_notes": ["playtest"]}, "secondary_details": {"modifier_notes": ["playtest"]}}))
    outcome = str((trial or {}).get("outcome"))
    if trial is not None:
        report.add("PASS" if outcome == "pass" else "FAIL", "an overwhelming candidate passes on any dice",
                   f"outcome={outcome} primary={trial.get('primary')} secondary={trial.get('secondary')}")
        manual = dict(trial.get("granted_manual") or {})
        if manual:
            report.add("PASS", "the sect's gift", f"{manual.get('name')} ({manual.get('manual_id')})")
            await step(report, "manual.study the gift", act("manual.study", PLAYER, {"manual_id": str(manual.get("manual_id")), "cooldown_seconds": 0}))
        else:
            report.add("FAIL", "the sect's gift", "the trial passed but no manual was granted")
    await step(report, "back to realm 0 stage 1", gm("admin.player.set_realm", {"user_id": PLAYER, "realm_index": 0, "phase": 1, "reason": "playtest"}))
    membership = await step(report, "sect membership on file", db.get_sect_membership(PLAYER))
    if membership is not None and str(membership.get("sect_name")) != sect and outcome in {"pass", "conditional_pass"}:
        report.add("FAIL", "sect membership on file", f"membership={membership}")

    # ---- 4. commissions from Qiao: complete, abandon, fail -----------------
    await step(report, "teleport to Steward Qiao", gm("admin.player.teleport", {"user_id": PLAYER, "location": "Golden Pavilion Auction House", "reason": "playtest"}))
    crate = "commission_qiao_replaced_crate"
    await step(report, "commission.accept (the replaced crate)", act("commission.accept", PLAYER, {"quest_key": crate, "variant_index": 0}))
    await step(report, "a second commission is refused while one is held",
               act("commission.accept", PLAYER, {"quest_key": "commission_qiao_quiet_valuation", "variant_index": 0}), expect_error="already hold a commission")
    for objective_type, target in (("scene_action", "investigate"), ("talk", "Madam Pei Suyin"), ("talk", "Steward Qiao")):
        touched = await step(report, f"quest.progress {objective_type}:{target}", engine.action("quest.progress", PLAYER, {
            "quest_key": crate, "objective_type": objective_type, "amount": 1, "target": target}))
        if touched is not None and not touched.get("touched"):
            report.add("FAIL", f"quest.progress {objective_type}:{target}", f"not touched: {touched}")
    rows = {r["quest_key"]: r for r in await db.list_character_quests(PLAYER)}
    status = str((rows.get(crate) or {}).get("status"))
    report.add("PASS" if status == "completed" else "FAIL", "the crate commission completes", f"status={status}")
    await step(report, "commission.accept (a quiet valuation)", act("commission.accept", PLAYER, {"quest_key": "commission_qiao_quiet_valuation", "variant_index": 0}))
    await step(report, "commission.resolve abandoned", act("commission.resolve", PLAYER, {"quest_key": "commission_qiao_quiet_valuation", "outcome": "abandoned"}))
    await step(report, "abandoning costs a cooldown",
               act("commission.accept", PLAYER, {"quest_key": "commission_qiao_marsh_consignment", "variant_index": 0}), expect_error="cooldown")
    await step(report, "advance time past the cooldown", gm("admin.world.advance_time", {"minutes": 3 * 24 * 60, "reason": "playtest"}))
    marsh = await step(report, "commission.accept (the marsh consignment)", act("commission.accept", PLAYER, {"quest_key": "commission_qiao_marsh_consignment", "variant_index": 0}))
    deadline = int((marsh or {}).get("deadline_game_minutes") or 0)
    await step(report, "advance time past the deadline", gm("admin.world.advance_time", {"minutes": max(deadline, 60) + 60, "reason": "playtest"}))
    await step(report, "the tick fails the overdue commission", engine.run_due_simulation(await clock(), {"maintenance_cleanup": True}))
    rows = {r["quest_key"]: r for r in await db.list_character_quests(PLAYER)}
    status = str((rows.get("commission_qiao_marsh_consignment") or {}).get("status"))
    report.add("PASS" if status == "failed" else "FAIL", "the overdue commission is failed by the tick", f"status={status}")

    # ---- 5. edit a live quest under each hold policy -----------------------
    key = "playtest_edit_quest"
    definition = {"quest_key": key, "title": "The Playtest Errand", "description": "Walk to the river and back for the playtest.",
                  "objectives": [{"id": "walk", "type": "explore", "count": 2, "target": "Greenriver Town", "label": "Explore Greenriver Town twice"}],
                  "rewards": {"insight_xp": 5, "spirit_stones": 5, "items": {}}, "tier": 1, "realm_band": "0-2", "reward_visibility": "shown"}
    await step(report, "admin.quest.save (draft)", gm("admin.quest.save", {**definition, "hold_policy": "keep", "reason": "playtest"}))
    await step(report, "admin.quest.review approved", gm("admin.quest.review", {"quest_key": key, "status": "approved", "reason": "playtest"}))
    await step(report, "teleport the buyer to Greenriver Town", gm("admin.player.teleport", {"user_id": BUYER, "location": "Greenriver Town", "reason": "playtest"}))
    await step(report, "the buyer accepts the quest", act("commission.accept", BUYER, {"quest_key": key, "variant_index": 0}))
    await step(report, "one step of progress", engine.action("quest.progress", BUYER, {"quest_key": key, "objective_type": "explore", "amount": 1, "target": "Greenriver Town"}))

    async def holder() -> dict[str, Any]:
        return next((dict(r) for r in await db.list_character_quests(BUYER) if r["quest_key"] == key), {})

    before = await holder()
    edited = {**definition, "objectives": [{"id": "walk", "type": "explore", "count": 3, "target": "Greenriver Town", "label": "Explore Greenriver Town thrice"}]}
    await step(report, "save with hold_policy keep", gm("admin.quest.save", {**edited, "hold_policy": "keep", "reason": "playtest"}))
    after = await holder()
    report.add("PASS" if after.get("progress") == before.get("progress") and after.get("status") == "active" else "FAIL",
               "keep leaves the holder on their terms", f"progress {before.get('progress')} -> {after.get('progress')}")
    await step(report, "save with hold_policy migrate", gm("admin.quest.save", {**edited, "hold_policy": "migrate", "reason": "playtest"}))
    after = await holder()
    report.add("PASS" if after.get("status") == "active" and int(dict(after.get("progress") or {}).get("walk") or 0) == 1 else "FAIL",
               "migrate carries progress on the unchanged objective", f"progress {after.get('progress')}")
    await step(report, "save with hold_policy revoke", gm("admin.quest.save", {**edited, "hold_policy": "revoke", "reason": "playtest"}))
    after = await holder()
    report.add("PASS" if not after else "FAIL", "revoke takes the quest back", f"row={after or 'gone'}")

    # ---- 6. change a narration route and read it back ----------------------
    await step(report, "admin.narration.set_chain", gm("admin.narration.set_chain", {"slots": {"routine_model": "playtest/model:free"}, "reason": "playtest"}))
    chain = await step(report, "the stored chain reads back", db.get_narration_chain())
    if chain is not None and chain.get("routine_model") != "playtest/model:free":
        report.add("FAIL", "the stored chain reads back", f"chain={chain}")

    # ---- 7. moderation with expiry -----------------------------------------
    await step(report, "teleport the player back to town", gm("admin.player.teleport", {"user_id": PLAYER, "location": "Greenriver Town", "reason": "playtest"}))
    muted = await step(report, "admin.player.set_moderation mute for an hour", gm("admin.player.set_moderation", {"user_id": PLAYER, "muted": True, "muted_seconds": 3600, "moderation_reason": "playtest", "reason": "playtest"}))
    if muted is not None and float(muted.get("muted_until") or 0) <= time.time():
        report.add("FAIL", "mute expiry stored", f"muted_until={muted.get('muted_until')}")
    await step(report, "a muted player cannot act in scene", act("scene.action", PLAYER, {"action_key": "observe", "target": "Self", "detail": "look around"}), expect_error="muted")
    await step(report, "admin.audit.undo_last lifts the mute", gm("admin.audit.undo_last", {"reason": "playtest"}))
    await step(report, "the scene action works again", act("scene.action", PLAYER, {"action_key": "observe", "target": "Self", "detail": "look around"}))

    # ---- 8. a live auction ---------------------------------------------------
    await step(report, "grant the buyer stones", gm("admin.player.grant_currency", {"user_id": BUYER, "currency_id": "low_spirit_stone", "amount": 500, "reason": "playtest"}))
    await step(report, "grant the seller an item", gm("admin.player.adjust_item", {"user_id": PLAYER, "item_id": "spirit_herb", "quantity": 3, "reason": "playtest"}))
    await step(report, "auction.enter (seller)", act("auction.enter", PLAYER, {}))
    await step(report, "teleport the buyer to town", gm("admin.player.teleport", {"user_id": BUYER, "location": "Greenriver Town", "reason": "playtest"}))
    await step(report, "auction.enter (buyer)", act("auction.enter", BUYER, {}))
    lot = await step(report, "auction.sell", act("auction.sell", PLAYER, {"item_id": "spirit_herb", "quantity": 1, "currency_id": "low_spirit_stone", "starting_bid": 10, "anonymous": False, "ends_at": time.time() + 5}))
    lot_id = int((lot or {}).get("auction_id") or 0)
    if lot_id:
        await step(report, "auction.bid", act("auction.bid", BUYER, {"auction_id": lot_id, "amount": 25}))
        await asyncio.sleep(6)
        await step(report, "the tick settles the lot", engine.run_due_simulation(await clock(), {"auction_settlement": True}))
        settled = await db.get_auction(lot_id)
        report.add("PASS" if settled and not int(settled.get("active") or 0) and int(settled.get("current_bidder_user_id") or 0) == BUYER else "FAIL",
                   "the lot is struck to the bidder", f"row={ {k: settled.get(k) for k in ('active', 'current_bid', 'current_bidder_user_id')} if settled else None}")
    await step(report, "a local floor refuses a seventh lot",
               (lambda: _local_floor(act, gm))(), expect_error="at most 6 lots")

    # ---- 9. the city: the gate, the districts, the shops, the merchants ----
    # v0.35.0-v0.36.0: a road journey ends at the gate facing the road, the
    # city's parts are a walk apart, walking the city finds its shops, a shop
    # buys and sells, and a merchant takes an unsold lot and resells it.
    await step(report, "grant the player road stones", gm("admin.player.grant_currency", {"user_id": PLAYER, "currency_id": "low_spirit_stone", "amount": 300, "reason": "playtest"}))
    await step(report, "teleport the player to Riverguard City", gm("admin.player.teleport", {"user_id": PLAYER, "location": "Riverguard City", "reason": "playtest"}))
    capital = "Azure Crown Imperial City"
    journey = await step(report, "exploration.travel by road to the capital", act("exploration.travel", PLAYER, {"destination": capital, "mode": "known"}))
    if journey is not None:
        gate = str(journey.get("arrived_at") or "")
        report.add("PASS" if journey.get("road_connection") and gate.endswith("Gate") and journey.get("arrival_gate") else "FAIL",
                   "the road ends at the gate facing it", f"arrived_at={gate} gate={journey.get('arrival_gate')} left_by={journey.get('left_by_gate')}")
        await step(report, "advance time past arrival", gm("admin.world.advance_time", {"minutes": int(journey.get("travel_minutes") or 0) + 30, "reason": "playtest"}))
    status = await step(report, "exploration.travel_status after arrival", engine.action("exploration.travel_status", PLAYER, {}))
    if status is not None and status.get("traveling"):
        report.add("FAIL", "exploration.travel_status after arrival", f"still traveling: {status}")
    parts = [part for part in list((journey or {}).get("city_parts") or []) if not part.endswith("Gate")]
    district = parts[0] if parts else capital
    await step(report, f"walk to {district}", act("exploration.travel", PLAYER, {"destination": district, "mode": "known"}))
    here = await step(report, "shop.here from a district", engine.action("shop.here", PLAYER, {}))
    if here is not None and not (here.get("is_city") and int(here.get("total") or 0) >= 4):
        report.add("FAIL", "shop.here from a district", f"{here}")
    # A walk finds one of the city's shops six times in ten, and only one the
    # character does not know yet: a city whose every shop is known finds
    # nothing by construction, which is a pass with a note, not a failure.
    # Twelve walks against 40% is a one-in-sixty-thousand miss; the rest of
    # the section is skipped rather than failed if it happens.
    known_before = int((here or {}).get("discovered") or 0)
    total_shops = int((here or {}).get("total") or 0)
    found = None
    for _ in range(12):
        walked = await act("exploration.explore", PLAYER, {"cooldown_seconds": 0, "unexpected_event_chance_percent": 0, "event_key": aid("exploration:event")})
        if isinstance(walked.get("discovered_shop"), dict):
            found = dict(walked["discovered_shop"])
            break
    if found:
        report.add("PASS", "walking the city finds a shop", f"{found.get('name')}")
    elif total_shops and known_before >= total_shops:
        report.add("PASS", "walking the city finds a shop", f"every one of the city's {total_shops} shops was already known; nothing left to find")
    else:
        report.add("PASS", "walking the city finds a shop", f"none in twelve walks ({known_before}/{total_shops} known): a one-in-sixty-thousand miss, the shop section is skipped")
    if found:
        await step(report, "walk into the shop", act("exploration.travel", PLAYER, {"destination": str(found.get("location")), "mode": "known"}))
        shelf = await step(report, "shop.browse", engine.action("shop.browse", PLAYER, {}))
        stock = list((shelf or {}).get("stock") or [])
        buys = list((shelf or {}).get("buys") or [])
        if stock:
            line = dict(stock[0])
            bought = await step(report, f"shop.buy {line.get('item_id')}", act("shop.buy", PLAYER, {"item_id": str(line.get("item_id")), "quantity": 1}))
            if bought is not None and int(bought.get("total") or 0) != int(line.get("price") or -1):
                report.add("FAIL", "shop.buy charges the shelf price", f"total={bought.get('total')} shelf={line.get('price')}")
        else:
            report.add("FAIL", "shop.browse", "an empty shelf on first sight")
        if buys:
            want = dict(buys[0])
            await step(report, "grant the item the keeper wants", gm("admin.player.adjust_item", {"user_id": PLAYER, "item_id": str(want.get("item_id")), "quantity": 2, "reason": "playtest"}))
            await step(report, f"shop.sell {want.get('item_id')}", act("shop.sell", PLAYER, {"item_id": str(want.get("item_id")), "quantity": 1}))
        await step(report, "the shop door opens onto the street only", act("exploration.travel", PLAYER, {"destination": district, "mode": "known"}), expect_error="door opens onto")
        await step(report, "step back onto the street", act("exploration.travel", PLAYER, {"destination": capital, "mode": "known"}))
    merchants = await step(report, "merchant.status", engine.action("merchant.status", PLAYER, {}))
    rows = list((merchants or {}).get("merchants") or [])
    report.add("PASS" if len(rows) >= 8 else "FAIL", "eight merchants walk the roads", f"{len(rows)} listed")
    # The lot is something no merchant stocks as a ware, so it lands in the
    # pack as a floor find rather than merging into the shop's own line.
    find_item = "bone_comb"
    await step(report, "grant the seller a curio for the floor", gm("admin.player.adjust_item", {"user_id": PLAYER, "item_id": find_item, "quantity": 1, "reason": "playtest"}))
    await step(report, "auction.enter at the capital", act("auction.enter", PLAYER, {}))
    unsold = await step(report, "auction.sell with no bidder", act("auction.sell", PLAYER, {"item_id": find_item, "quantity": 1, "currency_id": "low_spirit_stone", "starting_bid": 10, "anonymous": False, "ends_at": time.time() + 2}))
    unsold_id = int((unsold or {}).get("auction_id") or 0)
    if unsold_id:
        await asyncio.sleep(3)
        await step(report, "the tick settles the unsold lot", engine.run_due_simulation(await clock(), {"auction_settlement": True, "merchants": True}))
        settled = await db.get_auction(unsold_id)
        buyer = str((settled or {}).get("merchant_buyer") or "")
        report.add("PASS" if buyer else "FAIL", "a merchant takes the unsold lot", f"merchant_buyer={buyer!r}")
        if buyer:
            merchants = dict(await engine.action("merchant.status", PLAYER, {}) or {})
            row = next((dict(r) for r in list(merchants.get("merchants") or []) if str(r.get("merchant")) == buyer), {})
            find = [dict(l) for l in list(row.get("stock") or []) if str(l.get("item_id")) == find_item]
            report.add("PASS" if find and str(find[0].get("source")) == "auction" else "FAIL", "the lot is in the merchant's pack as a floor find", f"{find}")
            await step(report, "auction.leave", act("auction.leave", PLAYER, {}))
            if row and not bool(row.get("on_the_road")):
                await step(report, f"teleport to {row.get('location')}", gm("admin.player.teleport", {"user_id": PLAYER, "location": str(row.get("location")), "reason": "playtest"}))
                # The merchant resells a floor find at the item's own base
                # price, which for the curio above is 980 - more than the 300
                # road stones this player was ever given. That never showed
                # until schema 50, because the tick this step depends on threw
                # before it could name a buyer and the whole branch was
                # skipped. Fund it from the price the merchant is actually
                # asking rather than a constant, so a content edit cannot
                # quietly put the step out of reach again.
                # `find` is [] when the lot is not in the pack - already a FAIL
                # two lines up - and a diagnostic script must report that, not
                # crash on it before the sections that follow get to run.
                asking = int((find[0] if find else {}).get("price") or 0)
                if asking > 0:
                    await step(report, "grant the player the asking price", gm("admin.player.grant_currency", {"user_id": PLAYER, "currency_id": "low_spirit_stone", "amount": asking, "reason": "playtest"}))
                    await step(report, "merchant.buy the floor find", act("merchant.buy", PLAYER, {"merchant": buyer, "item_id": find_item, "quantity": 1}))
                else:
                    report.add("FAIL", "merchant.buy the floor find", "no asking price to fund: the lot never reached the pack")
            else:
                report.add("PASS", "merchant.buy the floor find", f"skipped: {buyer} is {row.get('whereabouts')}")

    # ---- 10. a merchant bids (v0.37.0) --------------------------------------
    # A valued lot with an hour to run: the tick's merchant bids the starting
    # bid from its purse; a player outbids it and the purse is refunded.
    await step(report, "grant the seller a sword for the floor", gm("admin.player.adjust_item", {"user_id": PLAYER, "item_id": "spirit_iron_sword", "quantity": 1, "reason": "playtest"}))
    await step(report, "teleport the seller to the capital", gm("admin.player.teleport", {"user_id": PLAYER, "location": capital, "reason": "playtest"}))
    await step(report, "auction.enter at the capital (again)", act("auction.enter", PLAYER, {}))
    valued = await step(report, "auction.sell a sword with an hour to run", act("auction.sell", PLAYER, {"item_id": "spirit_iron_sword", "quantity": 1, "currency_id": "low_spirit_stone", "starting_bid": 3, "anonymous": False, "ends_at": time.time() + 3600}))
    valued_id = int((valued or {}).get("auction_id") or 0)
    if valued_id:
        before = {str(r.get("merchant")): int(r.get("budget") or 0) for r in list((await engine.action("merchant.status", PLAYER, {}) or {}).get("merchants") or [])}
        await step(report, "the tick lets the merchants bid", engine.run_due_simulation(await clock(), {"auction_settlement": True, "merchants": True}))
        lot_row = await db.get_auction(valued_id) or {}
        holder = str(lot_row.get("merchant_bidder") or "")
        report.add("PASS" if holder and int(lot_row.get("current_bid") or 0) >= 3 and not lot_row.get("current_bidder_user_id") else "FAIL",
                   "a merchant bids the starting bid from its purse", f"merchant_bidder={holder!r} current_bid={lot_row.get('current_bid')}")
        if holder:
            after = {str(r.get("merchant")): int(r.get("budget") or 0) for r in list((await engine.action("merchant.status", PLAYER, {}) or {}).get("merchants") or [])}
            # At least this lot's bid, not exactly it. The same tick lets the
            # merchant bid on every open lot it can reach, and since schema 50
            # the world's own people put lots on that floor too - so a purse
            # that moved by more than this bid is the feature working, not a
            # miscount. What must hold is that this lot's escrow came out of
            # the purse; the refund below is what pins the amount exactly.
            bid = int(lot_row.get("current_bid") or 0)
            drop = before.get(holder, 0) - after.get(holder, 0)
            report.add("PASS" if drop >= bid > 0 else "FAIL",
                       "the purse is the escrow", f"{before.get(holder)} -> {after.get(holder)} (out {drop}, this lot {bid})")
            await step(report, "teleport the buyer to the capital", gm("admin.player.teleport", {"user_id": BUYER, "location": capital, "reason": "playtest"}))
            await step(report, "auction.enter (buyer, capital)", act("auction.enter", BUYER, {}))
            await step(report, "the buyer outbids the merchant", act("auction.bid", BUYER, {"auction_id": valued_id, "amount": int(lot_row.get("current_bid") or 0) + 5}))
            lot_row = await db.get_auction(valued_id) or {}
            refunded = {str(r.get("merchant")): int(r.get("budget") or 0) for r in list((await engine.action("merchant.status", PLAYER, {}) or {}).get("merchants") or [])}
            # Exactly this lot's bid comes back, measured from the purse as it
            # stood after the bidding tick rather than before it - the merchant
            # may hold escrow on other lots from the same tick, and those are
            # not refunded by this outbid.
            report.add("PASS" if not str(lot_row.get("merchant_bidder") or "") and refunded.get(holder, 0) == after.get(holder, 0) + bid else "FAIL",
                       "outbid, the merchant is refunded and cleared", f"merchant_bidder={lot_row.get('merchant_bidder')!r} budget {after.get(holder)} -> {refunded.get(holder)} (+{bid} expected)")

    # ---- 11. city life (v0.38.0) ---------------------------------------------
    # The capital's board offers work from its own people; a shop trade moves
    # the city's prosperity; the envoys' hall is content the bot reads.
    givers = dict(world.get("commission_givers") or {})
    locations = dict(world.get("locations") or {})

    def city_of(place: str) -> str:
        loc = dict(locations.get(place) or {})
        return str(loc.get("outside_location")) if loc.get("district") or loc.get("shop") or loc.get("auction_house") else place

    board = [c for c in list(world.get("commissions") or []) if city_of(str(givers.get(str(c.get("giver_npc")), {}).get("location") or "")) == capital]
    report.add("PASS" if len(board) >= 4 else "FAIL", "the capital's pavilion has work from its own people", f"{len(board)} commissions")
    if board:
        await step(report, "teleport the buyer to the capital's street", gm("admin.player.teleport", {"user_id": BUYER, "location": capital, "reason": "playtest"}))
        job = dict(board[0])
        await step(report, f"commission.accept from the pavilion ({job.get('title')})", act("commission.accept", BUYER, {"quest_key": str(job.get("quest_key")), "variant_index": 0}))
        rows = {r["quest_key"]: r for r in await db.list_character_quests(BUYER)}
        report.add("PASS" if str((rows.get(str(job.get("quest_key"))) or {}).get("status")) == "active" else "FAIL", "the pavilion commission is held", f"status={(rows.get(str(job.get('quest_key'))) or {}).get('status')}")
    before_city = dict(await engine.action("civilization.status", PLAYER, {"location": capital}) or {})
    if found:
        # The seller is still on the auction floor from the bidding section;
        # the warded door leads to the street, and the shop is a walk from there.
        await step(report, "auction.leave onto the street", act("auction.leave", PLAYER, {}))
        await step(report, "walk back into the shop", act("exploration.travel", PLAYER, {"destination": str(found.get("location")), "mode": "known"}))
        shelf = dict(await step(report, "shop.browse (again)", engine.action("shop.browse", PLAYER, {})) or {})
        line = dict((list(shelf.get("stock") or []) or [{}])[0])
        if line.get("item_id"):
            await step(report, "a second shop trade", act("shop.buy", PLAYER, {"item_id": str(line.get("item_id")), "quantity": 1}))
        after_city = dict(await engine.action("civilization.status", PLAYER, {"location": capital}) or {})
        before_p, after_p = int(before_city.get("prosperity") or 0), int(after_city.get("prosperity") or 0)
        report.add("PASS" if after_p > before_p or after_p >= 95 else "FAIL", "trade moves the city's prosperity", f"{before_p} -> {after_p}")
        await step(report, "back onto the street", act("exploration.travel", PLAYER, {"destination": capital, "mode": "known"}))
    inn = next((n for n, l in locations.items() if l.get("district") == "inn" and l.get("outside_location") == capital), "")
    report.add("PASS" if inn else "FAIL", "the capital keeps an inn", inn or "none")
    if inn:
        await step(report, "walk to the inn", act("exploration.travel", PLAYER, {"destination": inn, "mode": "known"}))

    # ---- 12. the roads, the higher worlds, the trade (v0.39.0) --------------
    # A site on every road: walking the Greenriver-Riverguard road finds the
    # shrine on it, the shrine is half a leg from either end and leads back
    # to them; the hunting ground's edge; the waystation's stall. A trade at
    # the inn, confirmed on both sides. A realm opens on the rotation. The
    # higher worlds have their own sects and goods.
    sites = {n: l for n, l in locations.items() if l.get("road_site")}
    report.add("PASS" if len(sites) >= 50 else "FAIL", "a site on every road", f"{len(sites)} sites")
    shrine = next((n for n, l in sites.items() if l["road_site"] == "shrine" and set(l["road_leg"]) == {"Greenriver Town", "Riverguard City"}), "")
    await step(report, "teleport to Greenriver Town", gm("admin.player.teleport", {"user_id": PLAYER, "location": "Greenriver Town", "reason": "playtest"}))
    # Exploring Greenriver earlier in the run may already have turned the
    # shrine up (a road-side site is on the explore frontier too, one roll in
    # two); walking the road finds it only if it is still unknown.
    known_before = bool(await db.has_discovered_location(PLAYER, shrine)) if shrine else False
    walked = await step(report, "exploration.travel Greenriver -> Riverguard finds the shrine", act("exploration.travel", PLAYER, {"destination": "Riverguard City", "mode": "known"}))
    found_sites = [str(r.get("name")) for r in list((walked or {}).get("road_sites_found") or [])]
    report.add("PASS" if shrine and (shrine in found_sites or known_before) else "FAIL", "the shrine on the road is found by walking it",
               ", ".join(found_sites) or ("found earlier by exploring" if known_before else "nothing found"))
    await step(report, "advance time to arrive", gm("admin.world.advance_time", {"minutes": int((walked or {}).get("travel_minutes") or 0) + 5, "reason": "playtest"}))
    if shrine:
        hop = await step(report, "exploration.travel to the shrine (half a leg)", act("exploration.travel", PLAYER, {"destination": shrine, "mode": "known"}))
        if hop is not None:
            report.add("PASS" if str(hop.get("site_kind")) == "shrine" and str(hop.get("arrived_at")) == shrine else "FAIL", "arrival at the shrine", f"{hop.get('arrived_at')} ({hop.get('site_kind')})")
            await step(report, "advance time to arrive", gm("admin.world.advance_time", {"minutes": int(hop.get("travel_minutes") or 0) + 5, "reason": "playtest"}))
        await step(report, "the shrine refuses the hunt", act("exploration.hunt", PLAYER, {"cooldown_seconds": 0}), expect_error="shrine")
        await step(report, "from the shrine the road leads only to its ends", act("exploration.travel", PLAYER, {"destination": "Azure Crown Imperial City", "mode": "known"}), expect_error="leads back to")
        back = await step(report, "exploration.travel shrine -> Greenriver Town", act("exploration.travel", PLAYER, {"destination": "Greenriver Town", "mode": "known"}))
        await step(report, "advance time to arrive", gm("admin.world.advance_time", {"minutes": int((back or {}).get("travel_minutes") or 0) + 5, "reason": "playtest"}))
    ground = next((n for n, l in sites.items() if l["road_site"] == "hunting_ground" and l["world"] == "Mortal World"), "")
    if ground:
        await step(report, "teleport to a hunting ground", gm("admin.player.teleport", {"user_id": PLAYER, "location": ground, "reason": "playtest"}))
        await step(report, "reset cooldowns", gm("admin.player.reset_cooldowns", {"user_id": PLAYER, "reason": "playtest"}))
        hunt = await step(report, "exploration.hunt on the hunting ground", act("exploration.hunt", PLAYER, {"cooldown_seconds": 0}))
        if hunt is not None:
            report.add("PASS" if int(hunt.get("site_bonus") or 0) > 0 else "FAIL", "the hunting ground's edge", f"site_bonus={hunt.get('site_bonus')}")
    waystation = next((n for n, l in sites.items() if l["road_site"] == "waystation" and l["world"] == "Mortal World"), "")
    if waystation:
        await step(report, "teleport to a waystation", gm("admin.player.teleport", {"user_id": PLAYER, "location": waystation, "reason": "playtest"}))
        stall = await step(report, "shop.browse at the waystation stall", engine.action("shop.browse", PLAYER, {}))
        report.add("PASS" if stall and str(stall.get("kind")) == "waystation" and list(stall.get("stock") or []) else "FAIL", "the stall has a shelf", f"kind={(stall or {}).get('kind')}")
    # The trade: both at the capital's inn.
    if inn:
        for uid in (PLAYER, BUYER):
            await step(report, f"teleport {uid} to the inn", gm("admin.player.teleport", {"user_id": uid, "location": inn, "reason": "playtest"}))
        await step(report, "the seller carries a herb", gm("admin.player.adjust_item", {"user_id": PLAYER, "item_id": "spirit_herb", "quantity": 2, "reason": "playtest"}))
        await step(report, "the buyer carries a pill", gm("admin.player.adjust_item", {"user_id": BUYER, "item_id": "recovery_pill", "quantity": 1, "reason": "playtest"}))
        offer = await step(report, "trade.offer (a herb for a pill)", act("trade.offer", PLAYER, {"to_user_id": BUYER, "give_items": {"spirit_herb": 1}, "want_items": {"recovery_pill": 1}}))
        offer_id = int((offer or {}).get("offer_id") or 0)
        if offer_id:
            status = await step(report, "trade.status for the buyer", engine.action("trade.status", BUYER, {}))
            report.add("PASS" if status and any(int(o.get("offer_id") or 0) == offer_id for o in list(status.get("offers_received") or [])) else "FAIL", "the buyer sees the offer", f"#{offer_id}")
            await step(report, "the offerer cannot accept their own offer", act("trade.accept", PLAYER, {"offer_id": offer_id}), expect_error="not made to you")
            struck = await step(report, "trade.accept", act("trade.accept", BUYER, {"offer_id": offer_id}))
            report.add("PASS" if struck and struck.get("accepted") else "FAIL", "the trade is struck", f"status={(struck or {}).get('status')}")
            inv = dict(await db.get_inventory(BUYER) or {})
            report.add("PASS" if int(inv.get("spirit_herb", 0)) >= 1 else "FAIL", "the herb changed hands", f"buyer carries {inv.get('spirit_herb', 0)}")
            await step(report, "a struck offer cannot be struck twice", act("trade.accept", BUYER, {"offer_id": offer_id}), expect_error="accepted")
    # rc.2: a fresh offer can be voided from the dashboard's action, audited.
    if inn:
        voidable = await step(report, "trade.offer (to be voided)", act("trade.offer", PLAYER, {"to_user_id": BUYER, "give_stones": 1}))
        if voidable and int(voidable.get("offer_id") or 0):
            voided = await step(report, "admin.trade.void", gm("admin.trade.void", {"offer_id": int(voidable["offer_id"]), "reason": "playtest"}))
            report.add("PASS" if voided and str(voided.get("status")) == "voided" else "FAIL", "the offer is voided", f"status={(voided or {}).get('status')}")
            await step(report, "a voided offer cannot be accepted", act("trade.accept", BUYER, {"offer_id": int(voidable["offer_id"])}), expect_error="voided")
    # The rotation opens a realm on the tick.
    await step(report, "the rotation opens the first realm", engine.run_due_simulation(await clock(), {"maintenance_cleanup": True, "secret_realms": True}))
    open_realms = await step(report, "active world events", db.get_active_world_events())
    if open_realms is not None:
        names = [str(r.get("title")) for r in open_realms if str(r.get("event_type")) == "secret_realm"]
        report.add("PASS" if names else "FAIL", "a secret realm is open somewhere", ", ".join(names) or "none")
    realms = dict(world.get("secret_realms") or {})
    report.add("PASS" if len(realms) >= 8 else "FAIL", "eight realms on the rotation", f"{len(realms)} realms")
    status = await step(report, "secret_realm.status carries the rotation", engine.action("secret_realm.status", PLAYER, {}))
    rotation = dict((status or {}).get("rotation") or {})
    report.add("PASS" if rotation.get("next_realm_id") in realms else "FAIL", "the next realm is named", f"next={rotation.get('next_realm_id')} last={rotation.get('last_realm_id')}")
    # The higher worlds: sects and goods.
    sects_by_world: dict[str, list[str]] = {}
    for sect_name, sect in dict(world.get("sects") or {}).items():
        rec = dict(sect.get("recruitment") or {})
        where = dict(locations.get(str(rec.get("location") or "")) or {})
        if where:
            sects_by_world.setdefault(str(where.get("world")), []).append(sect_name)
    report.add("PASS" if all(len(sects_by_world.get(w, [])) >= 2 for w in ("Spiritual World", "Immortal World", "Celestial World")) else "FAIL",
               "two sects in every higher world", "; ".join(f"{w}: {len(v)}" for w, v in sorted(sects_by_world.items())))
    recipes = dict(world.get("recipes") or {})
    report.add("PASS" if len(recipes) >= 27 else "FAIL", "recipes for the higher worlds", f"{len(recipes)} recipes")
    higher = next((n for n, l in locations.items() if l.get("shop") and l["world"] == "Immortal World" and not l.get("road_site")), "")
    if higher:
        await step(report, "teleport to an Immortal World shop", gm("admin.player.teleport", {"user_id": PLAYER, "location": higher, "reason": "playtest"}))
        shelf = await step(report, "shop.browse in the Immortal World", engine.action("shop.browse", PLAYER, {}))
        ids = {str(l.get("item_id")) for l in list((shelf or {}).get("stock") or [])}
        report.add("PASS" if ids & {"immortal_gold_ore", "immortal_gold_sabre", "dawnlotus_herb", "dawnlotus_vitality_pill", "golden_edge_talisman", "immortal_marrow_pill", "immortal_gold_plate"} else "FAIL",
                   "the Immortal World's own goods are on the shelf", ", ".join(sorted(ids))[:120])

    # ---- 13. the cultivation sheet, the stance, the realm gate (v1.0.0-rc.3)
    sheet = await step(report, "cultivation.status", engine.action("cultivation.status", PLAYER, {}))
    if sheet is not None:
        odds = dict(sheet.get("odds") or {})
        report.add("PASS" if {"stance", "cost", "insight_xp", "insight_cost", "realm_gate"} <= set(sheet) and 0 <= int(odds.get("probability", -1)) <= 100 else "FAIL",
                   "the sheet carries the stance, the cost, the insight and the odds", f"stance={sheet.get('stance')} odds={odds.get('probability')}% tn={odds.get('tn')}")
    await step(report, "an unknown stance is refused", act("cultivation.stance", PLAYER, {"stance": "meditate"}), expect_error="unknown stance")
    # A stage with room in it: since v1.0.0-rc.5 a session at a full stage
    # banks nothing, which would make the Refine check below read as a failure.
    await step(report, "stand at a stage with room in it", gm("admin.player.set_realm", {"user_id": PLAYER, "realm_index": 0, "phase": 2, "reason": "playtest"}))
    await step(report, "cultivation.stance refine", act("cultivation.stance", PLAYER, {"stance": "refine"}))
    trained = await step(report, "cultivation.train under Refine", act("cultivation.train", PLAYER, {"cooldown_seconds": 1}))
    if trained is not None:
        report.add("PASS" if trained.get("stance") == "refine" and int(trained.get("insight_xp_gain") or 0) == 2 else "FAIL",
                   "Refine banks Insight XP", f"stance={trained.get('stance')} +{trained.get('insight_xp_gain')} XP, gain {trained.get('gain')}")
    sheet = dict(await engine.action("cultivation.status", PLAYER, {}) or {})
    xp, cost = int(sheet.get("insight_xp") or 0), int(sheet.get("insight_cost") or 0)
    if xp >= cost:
        banked = await step(report, "cultivation.insight banks the gate insight", act("cultivation.insight", PLAYER, {}))
        if banked is not None and not (banked.get("banked") and int(banked.get("insight_xp") or -1) == xp - cost):
            report.add("FAIL", "cultivation.insight banks the gate insight", f"{banked}")
        await step(report, "a second insight is refused", act("cultivation.insight", PLAYER, {}), expect_error="already banked")
    else:
        await step(report, "cultivation.insight is refused short of XP", act("cultivation.insight", PLAYER, {}), expect_error="Insight XP")
    await step(report, "cultivation.stance back to circulate", act("cultivation.stance", PLAYER, {"stance": "circulate"}))

    # ---- 14. the ground, and Insight XP spent (v1.0.0-rc.4) -----------------
    shrine = next((n for n, l in locations.items() if l.get("road_site") == "shrine"), "")
    report.add("PASS" if shrine else "FAIL", "the roads keep a shrine", shrine or "none")
    if shrine:
        await step(report, "teleport to the shrine", gm("admin.player.teleport", {"user_id": PLAYER, "location": shrine, "reason": "playtest"}))
        at_shrine = dict(await engine.action("cultivation.status", PLAYER, {}) or {})
        report.add("PASS" if float(at_shrine.get("place_mult") or 0) > 1.0 and at_shrine.get("place_name") else "FAIL",
                   "the shrine is richer ground than open country", f"{at_shrine.get('place_name')} x{at_shrine.get('place_mult')} ({at_shrine.get('place_quality')})")
        await step(report, "clear the meditation cooldown", gm("admin.player.reset_cooldowns", {"user_id": PLAYER, "reason": "playtest"}))
        session = await step(report, "cultivation.train at the shrine", act("cultivation.train", PLAYER, {"cooldown_seconds": 1}))
        if session is not None and not (float(session.get("place_mult") or 0) > 1.0):
            report.add("FAIL", "the session is worked at the shrine's rate", f"{session.get('place_mult')}")
    await step(report, "a moment nobody failed cannot be seized", act("cultivation.breakthrough", PLAYER, {"reroll": True}), expect_error="no moment to seize")

    # ---- 15. the pace, the growth and the world's qi (v1.0.0-rc.5) ----------
    async def clear_cooldowns() -> None:
        await gm("admin.player.reset_cooldowns", {"user_id": PLAYER, "reason": "playtest"})

    async def meditate() -> dict[str, Any]:
        await clear_cooldowns()
        return dict(await act("cultivation.train", PLAYER, {"cooldown_seconds": 1}) or {})

    # Stage 9 of the first realm: a stage with room in it, so the pace is
    # what the session pays rather than whatever the cap allows.
    await step(report, "stand at Stage 9 of the first realm", gm("admin.player.set_realm", {"user_id": PLAYER, "realm_index": 0, "phase": 9, "reason": "playtest"}))
    sheet = dict(await engine.action("cultivation.status", PLAYER, {}) or {})
    pace, cost = int(sheet.get("pace") or 0), int(sheet.get("cost") or 0)
    report.add("PASS" if pace > 0 and pace >= cost // 12 else "FAIL",
               "a session is a share of the stage", f"pace={pace} of a {cost} stage over {sheet.get('sessions_per_stage')} sessions")
    # What a session pays is one die (variance 0.9..1.1 on the pace) under a
    # chain of multipliers the engine reports beside the gain, so the check
    # is an identity on the engine's own figures rather than a guessed band:
    # the base is pace x attribute quality x variance, the attempt is that
    # base (plus resonance) through every multiplier, rounded three times,
    # plus the storm, and the gain is the attempt clamped to the room left in
    # the stage. `admin.player.set_realm` does not reset cultivation, so the
    # stage starts wherever the stage-2 training and the explores left it.
    def session_is_the_engines_own_arithmetic(result: dict[str, Any], room: int) -> tuple[bool, str]:
        base = int(result.get("base_gain") or 0)
        quality = float(result.get("attribute_quality") or 1.0)
        session_pace = int(result.get("pace") or pace)
        lo, hi = session_pace * quality * 0.9, session_pace * quality * 1.1
        chain = 1.0
        for key in ("time_mult", "effect_mult", "soul_mult", "era_mult", "stance_mult", "world_mult", "manual_mult", "element_mult"):
            chain *= float(result.get(key) or 1.0)
        through = (base + int(result.get("resonance_bonus") or 0)) * chain
        through = through * float(result.get("place_mult") or 1.0) * float(result.get("manor_mult") or 1.0)
        expected = through + int(result.get("storm_bonus") or 0)
        attempted = int(result.get("attempted_gain") or 0)
        gain = int(result.get("gain") or 0)
        ok = (lo - 1 <= base <= hi + 1) and abs(attempted - expected) <= 3 and gain == min(attempted, room)
        return ok, f"base={base} in [{lo:.1f},{hi:.1f}] attempt={attempted}~{expected:.1f} gain={gain} room={room} chain={chain:.3f} place={result.get('place_mult')}"

    start = int(sheet.get("cultivation") or 0)
    paced = await step(report, "cultivation.train pays about the pace", meditate())
    if paced:
        ok, why = session_is_the_engines_own_arithmetic(paced, cost - start)
        report.add("PASS" if ok else "FAIL", "the session pays its pace through the engine's own multipliers", why)
        report.add("PASS" if float(paced.get("world_mult") or 0) == 1.0 and paced.get("world_name") == "Mortal World" else "FAIL",
                   "the Mortal World is the baseline density", f"{paced.get('world_name')} x{paced.get('world_mult')}")

    # How many sessions a stage takes is a roll times a root rolled at
    # creation times whichever manual the sect gave; it is reported, never
    # bounded. What is asserted is the identity: the gains sum to exactly
    # the room the stage had, every session is the engine's own arithmetic,
    # and the stage is ready at the end.
    gains = [int((paced or {}).get("gain") or 0)]
    filled = dict(await engine.action("cultivation.status", PLAYER, {}) or {})
    arithmetic_ok = True
    while not filled.get("ready") and len(gains) < 30:
        room = int(filled.get("cost") or cost) - int(filled.get("cultivation") or 0)
        session = await meditate()
        ok, _ = session_is_the_engines_own_arithmetic(session, room)
        arithmetic_ok = arithmetic_ok and ok
        gains.append(int(session.get("gain") or 0))
        filled = dict(await engine.action("cultivation.status", PLAYER, {}) or {})
    stage_cost = int(filled.get("cost") or cost)
    report.add("PASS" if filled.get("ready") and sum(gains) == stage_cost - start and arithmetic_ok and len(gains) < 30 else "FAIL",
               "the sessions fill the stage exactly", f"{len(gains)} sessions paid {gains} = {sum(gains)} for the {stage_cost - start} the {stage_cost} stage had left")

    # A full stage gathers nothing, banks nothing and risks nothing.
    await step(report, "cultivation.stance refine (for the farm check)", act("cultivation.stance", PLAYER, {"stance": "refine"}))
    before_xp = int(dict(await engine.action("cultivation.status", PLAYER, {}) or {}).get("insight_xp") or 0)
    full = await step(report, "cultivation.train at a full stage", meditate())
    after_xp = int(dict(await engine.action("cultivation.status", PLAYER, {}) or {}).get("insight_xp") or 0)
    if full:
        report.add("PASS" if int(full.get("gain") or 0) == 0 and full.get("stage_full") and after_xp == before_xp else "FAIL",
                   "a full stage banks nothing", f"gain={full.get('gain')} xp {before_xp} -> {after_xp}")
    await step(report, "cultivation.stance back to circulate (again)", act("cultivation.stance", PLAYER, {"stance": "circulate"}))

    # Crossing a realm raises the cultivator - the one thing that never moved
    # before this release.
    gate = dict(await engine.action("cultivation.status", PLAYER, {}) or {})
    if int(gate.get("insight_xp") or 0) < int(gate.get("insight_cost") or 0):
        await step(report, "earn the last of the gate insight", act("exploration.explore", PLAYER, {"cooldown_seconds": 0, "unexpected_event_chance_percent": 0, "event_key": aid("exploration:gate")}))
    if not gate.get("insight_banked"):
        await step(report, "bank the gate insight", act("cultivation.insight", PLAYER, {}))
    def will_of(character: dict[str, Any]) -> int:
        attributes = character.get("attributes")
        if not isinstance(attributes, dict):
            attributes = json.loads(str(character.get("attributes_json") or "{}"))
        return int(attributes.get("will") or 0)

    before_attr = dict(await db.get_character(PLAYER) or {})
    crossed = await step(report, "cross into the second realm", act("cultivation.breakthrough", PLAYER, {"confirm": True}))
    if crossed and crossed.get("success"):
        after_attr = dict(await db.get_character(PLAYER) or {})
        before_will = will_of(before_attr)
        after_will = will_of(after_attr)
        report.add("PASS" if after_will == before_will + 1 and dict(crossed.get("attribute_gains") or {}) else "FAIL",
                   "crossing a realm raises the cultivator", f"will {before_will} -> {after_will}, gains {crossed.get('attribute_gains')}")
    elif crossed is not None:
        report.add("PASS", "crossing a realm raises the cultivator", "the roll failed; attributes unchanged by design")

    # ---- 16. the array and the method (v1.0.0-rc.6) -------------------------
    manuals = [dict(r) for r in await db.get_manuals(PLAYER)]
    if manuals:
        best = max(manuals, key=lambda r: str(r.get("manual_id")))
        practised = await step(report, "cultivation.manual chooses the method", act("cultivation.manual", PLAYER, {"manual_id": str(best.get("manual_id"))}))
        if practised:
            report.add("PASS" if float(practised.get("manual_mult") or 0) > 1.0 and practised.get("manual_grade") else "FAIL",
                       "the method's grade speeds the gathering", f"{practised.get('manual_name')} ({practised.get('manual_grade')}) x{practised.get('manual_mult')}")
        await clear_cooldowns()
        with_method = dict(await act("cultivation.train", PLAYER, {"cooldown_seconds": 1}) or {})
        report.add("PASS" if float(with_method.get("manual_mult") or 0) > 1.0 else "FAIL",
                   "the session is worked by the method", f"x{with_method.get('manual_mult')} ({with_method.get('manual_name')})")
    await step(report, "an unlearned method is refused", act("cultivation.manual", PLAYER, {"manual_id": "advanced_demonic_019_sword_cultivator"}), expect_error="not been learned")

    # ---- 16b. elemental qi (v1.0.0-rc.9) ------------------------------------
    elements = {str(m.get("element")) for m in dict(world.get("technique_system", {}).get("manuals") or {}).values()}
    report.add("PASS" if len(elements) >= 10 and "" not in elements else "FAIL",
               "every method draws one of the twelve kinds of qi", ", ".join(sorted(elements)))
    sheet = dict(await engine.action("cultivation.status", PLAYER, {}) or {})
    if sheet:
        report.add("PASS" if sheet.get("element_relation") and float(sheet.get("element_mult") or 0) > 0 else "FAIL",
                   "the sheet says what the root makes of the method's qi",
                   f"{sheet.get('element')} qi, {sheet.get('element_label')}, x{sheet.get('element_mult')}")
    if manuals:
        practised = dict(await act("cultivation.manual", PLAYER, {"manual_id": str(best.get("manual_id"))}) or {})
        report.add("PASS" if practised.get("element") and practised.get("element_relation") else "FAIL",
                   "choosing a method names the qi it draws",
                   f"{practised.get('manual_name')} draws {practised.get('element')} — {practised.get('element_label')}")
        await clear_cooldowns()
        worked = dict(await act("cultivation.train", PLAYER, {"cooldown_seconds": 1}) or {})
        report.add("PASS" if float(worked.get("element_mult") or 0) > 0 and worked.get("element") else "FAIL",
                   "the session is worked by what the root can absorb",
                   f"{worked.get('element')} x{worked.get('element_mult')} ({worked.get('element_label')})")
        body = dict(await act("cultivation.body_train", PLAYER, {"cooldown_seconds": 1}) or {})
        report.add("PASS" if float(body.get("element_mult") or 0) == 1.0 else "FAIL",
                   "the body path answers to no element", f"x{body.get('element_mult')}")

    # ---- 17. the qi body (v1.0.0-rc.7) --------------------------------------
    body = await step(report, "qi.status", engine.action("qi.status", PLAYER, {}))
    if body is not None:
        wanted = {"qi", "qi_max", "regen_per_game_minute", "purity", "purity_ceiling",
                  "skill_cost_mult", "meridians_open", "meridians_damaged", "meridian_ceiling",
                  "dantian_state", "upper_open", "breakthrough_qi_cost"}
        report.add("PASS" if wanted <= set(body) else "FAIL", "the qi body carries the three dantian and the channels",
                   f"{body.get('qi')}/{body.get('qi_max')} qi, purity {body.get('purity')}/{body.get('purity_ceiling')}%, "
                   f"{body.get('meridians_open')}/{body.get('meridian_ceiling')} channels, vessel {body.get('dantian_state')}")
        report.add("PASS" if int(body.get("qi_max") or 0) >= 120 and float(body.get("regen_per_game_minute") or 0) > 0 else "FAIL",
                   "the dantian is the realm's, not the old flat pool", f"{body.get('qi_max')} qi, +{body.get('regen_per_game_minute')}/game minute")
    await clear_cooldowns()
    refined = await step(report, "qi.refine cleans what is held", act("qi.refine", PLAYER, {"cooldown_seconds": 1}))
    if refined is not None:
        report.add("PASS" if int(refined.get("purity_gain") or 0) > 0 and int(refined.get("qi_spent") or 0) > 0 else "FAIL",
                   "refining trades qi for purity", f"+{refined.get('purity_gain')}% to {refined.get('purity')}% for {refined.get('qi_spent')} qi")
    await step(report, "refining again at once is refused", act("qi.refine", PLAYER, {}), expect_error="cooldown")
    await step(report, "nothing ruptured mends nothing", act("meridian.heal", PLAYER, {}), expect_error="ruptured")
    await clear_cooldowns()
    before_channels = dict(await engine.action("qi.status", PLAYER, {}) or {})
    opening = await act("meridian.open", PLAYER, {})
    if isinstance(opening, dict) and opening.get("meridian_ceiling"):
        report.add("PASS" if int(opening.get("meridian_ceiling") or 0) == 108 and int(opening.get("insight_spent") or 0) > 0 else "FAIL",
                   "meridian.open spends Insight XP and qi", f"{opening.get('insight_spent')} XP + {opening.get('qi_spent')} qi, "
                   f"{'opened' if opening.get('success') else 'failed'} at {opening.get('meridians_open')}/108")
    else:
        report.add("PASS", "meridian.open spends Insight XP and qi",
                   f"refused by design at {before_channels.get('meridians_open')} channels: {opening}")

    # ---- 18. the ghost road (v1.0.0-rc.8) -----------------------------------
    road = await step(report, "ghost.status for a living cultivator", engine.action("ghost.status", PLAYER, {}))
    if road is not None:
        report.add("PASS" if road.get("walking_the_road") is False and road.get("qi_type") == "spirit" else "FAIL",
                   "a living cultivator is not on the ghost road",
                   f"path={road.get('path')} qi={road.get('qi_type')} form={road.get('ghost_form_name')}")
    await step(report, "the living cannot harvest death qi", act("ghost.harvest", PLAYER, {}), expect_error="born to a ghost household")
    await step(report, "the living cannot burn the rites", act("ghost.appease", PLAYER, {}), expect_error="born to a ghost household")

    offers = await step(report, "family options carry the ghost households", act("character.family_options", GHOST, {"world_name": "Mortal World"}))
    families = list((offers or {}).get("families") or [])
    by_id = {str(f.get("id")): f for f in families}
    report.add("PASS" if len(families) == 13 and {"nether_market_house", "tomb_watch_clan"} <= set(by_id) else "FAIL",
               "thirteen households, two of them ghost-born", f"{len(families)} offered")
    ordinary = by_id.get("martial_household") or (families[0] if families else {})
    if ordinary:
        await step(report, "a martial household cannot raise a ghost cultivator",
                   act("character.create", GHOST, {"discord_name": "Playtest Ghost", "name": "Playtest Wrongborn",
                                                   "concept": "a playtest cultivator", "gender": "male", "path": "Ghost Cultivator",
                                                   "family_choice_id": str(ordinary.get("choice_id") or ""), "age_at_creation_years": 18}),
                   expect_error="born among the dead")
    tomb = by_id.get("tomb_watch_clan")
    if tomb:
        born = await step(report, "the tomb-watch clan can", act("character.create", GHOST, {
            "discord_name": "Playtest Ghost", "name": "Xie Graveborn", "concept": "keep the barrows", "gender": "female",
            "path": "Ghost Cultivator", "family_choice_id": str(tomb.get("choice_id") or ""), "age_at_creation_years": 18}))
        if born is not None and not born.get("created"):
            report.add("FAIL", "the tomb-watch clan can", f"created=false: {born.get('reason')}")
    ruin = next((n for n, l in locations.items() if l.get("road_site") == "ruin" and l.get("world") == "Mortal World"), "")
    shrine = next((n for n, l in locations.items() if l.get("road_site") == "shrine" and l.get("world") == "Mortal World"), "")
    if ruin:
        await step(report, "teleport the ghost-born to a ruin", gm("admin.player.teleport", {"user_id": GHOST, "location": ruin, "reason": "playtest"}))
        sheet = dict(await engine.action("ghost.status", GHOST, {}) or {})
        report.add("PASS" if sheet.get("walking_the_road") and float(sheet.get("ground_mult") or 0) > 1 else "FAIL",
                   "a ruin is rich ground for death qi",
                   f"{sheet.get('ground_name')} x{sheet.get('ground_mult')} at {sheet.get('period')} x{sheet.get('hour_mult')}")
        # A newborn cultivator's dantian is full, and a full one has nowhere to
        # put a harvest; refining is the cheapest way to make room for it.
        await step(report, "make room in the ghost-born's dantian", act("qi.refine", GHOST, {"cooldown_seconds": 1}))
        taken = await step(report, "ghost.harvest takes what the place held", act("ghost.harvest", GHOST, {}))
        if taken is not None:
            report.add("PASS" if int(taken.get("qi_gained") or 0) > 0 and int(taken.get("corruption_gain") or 0) > 0 else "FAIL",
                       "a harvest is qi bought with corruption",
                       f"+{taken.get('qi_gained')} qi, corruption {taken.get('corruption')}, karma {taken.get('karma_delta')}")
    if shrine:
        await step(report, "grant the ghost-born stones for the rites", gm("admin.player.grant_currency", {"user_id": GHOST, "currency_id": "low_spirit_stone", "amount": 500, "reason": "playtest"}))
        await step(report, "teleport the ghost-born to a shrine", gm("admin.player.teleport", {"user_id": GHOST, "location": shrine, "reason": "playtest"}))
        shed = await step(report, "ghost.appease lifts some of the residue", act("ghost.appease", GHOST, {}))
        if shed is not None:
            report.add("PASS" if int(shed.get("corruption_shed") or 0) > 0 else "FAIL",
                       "incense lifts some of what clings",
                       f"-{shed.get('corruption_shed')} to {shed.get('corruption')} for {shed.get('stones_spent')} stones")

    # ---- 19. every simulation system can run -------------------------------
    #
    # `npc_consignments` consigned an NPC's find with `seller_user_id=0` into a
    # column that is NOT NULL and foreign-keyed to `characters`, so SQLite
    # refused every one of them. `runSystems` returns on the first error and
    # that system is fifth of eight, so `sect_politics`, `clan_dynamics`,
    # `autonomous_world_events` and the whole maintenance bundle never ran
    # either - commissions never expired, auctions never settled, merchants
    # never bid and the secret realm never rotated. It had been that way since
    # rc.15 and ten steps of this playtest failed on it; schema 50 makes the
    # column nullable.
    #
    # One bad system taking the rest of the tick down with it is the general
    # shape, so this forces each system in turn rather than only the one that
    # broke. `Force` runs a system whether or not it is due, which is what
    # makes this deterministic - *whether* anybody finds something on a given
    # day is a roll, so the summary is reported and never asserted on. See the
    # rule in CLAUDE.md: never assert that a random thing happened.
    systems = ("npc_civilization", "npc_life", "dynamic_economy", "black_markets",
               "npc_consignments", "sect_politics", "clan_dynamics", "autonomous_world_events")
    for system in systems:
        forced = await step(report, f"force {system}", engine.force_simulation(system, 3, await clock()))
        if forced is not None:
            report.add("PASS", f"{system} survives its own writes", str(forced.get("summary") or ""))

    # ---- 20. backups -------------------------------------------------------
    backup = await step(report, "create a backup", transport.create_backup())
    listed = await step(report, "list backups", transport.list_backups())
    if backup and listed is not None and not any(row.get("name") == backup.get("name") for row in listed):
        report.add("FAIL", "list backups", "the new backup is not listed")
    if backup:
        await step(report, "restore that backup", transport.restore_backup(str(backup.get("name"))))

    # ---- 20b. every operation the engine answers (v1.0.0-rc.35) --------------
    # From here to samsara the harness drives what the roadmap's loops never
    # reached. `tests/python/contracts/test_playtest_coverage.py` holds every
    # operation the engine answers to a driver call in this file or to a
    # reason in DEFERRED_OPERATIONS. Each leg builds its state with a GM lever
    # and asserts what is certain by the scenario or by construction; a roll
    # is reported, never bounded. The realm the earlier sections left the
    # player at is put back at the end, so samsara meets the character it
    # always did.
    town = "Greenriver Town"
    sheet = await db.get_character(PLAYER) or {}
    realm_before = (int(sheet.get("realm_index") or 0), int(sheet.get("phase") or 1))
    for uid in (PLAYER, BUYER):
        await step(report, f"{uid} stands in the town", gm("admin.player.teleport", {"user_id": uid, "location": town, "reason": "playtest"}))
        await audited("admin.player.revive", {"user_id": uid, "reason": "playtest"}, name=f"admin.player.revive {uid}")
    await audited("admin.player.force_end_scene", {"user_id": PLAYER, "reason": "playtest"})
    for item_id, qty in (("spirit_herb", 12), ("spirit_iron", 5), ("spirit_iron_sword", 1), ("spatial_ring", 1), ("spatial_pouch", 1),
                         ("minor_qi_gathering_array_disk", 1), ("recovery_pill", 3), ("nine_echo_spatial_token", 1)):
        await step(report, f"grant {item_id} x{qty}", gm("admin.player.adjust_item", {"user_id": PLAYER, "item_id": item_id, "quantity": qty, "reason": "playtest"}))
    await step(report, "grant 5000 stones", gm("admin.player.grant_currency", {"user_id": PLAYER, "currency_id": "low_spirit_stone", "amount": 5000, "reason": "playtest"}))
    await step(report, "grant 100 high stones for a caravan's escort", gm("admin.player.grant_currency", {"user_id": PLAYER, "currency_id": "high_spirit_stone", "amount": 100, "reason": "playtest"}))

    # -- reads: nothing to build
    await step(report, "npc.status", query("npc.status", PLAYER, {"npc_name": "Elder Su Yan"}))
    await step(report, "npc.at_location", query("npc.at_location", PLAYER, {"location": town}))
    await step(report, "npc.lifespan", query("npc.lifespan", PLAYER, {"npc_name": "Elder Su Yan"}))
    await step(report, "character.lifespan", query("character.lifespan", PLAYER, {}))
    await step(report, "sense.status", query("sense.status", PLAYER, {}))
    await step(report, "cooldown.status", query("cooldown.status", PLAYER, {}))
    await step(report, "effects.current", query("effects.current", PLAYER, {}))
    fam = await db.get_birth_family(PLAYER) or {}
    await step(report, "clan.status", query("clan.status", PLAYER, {"family_id": int(fam.get("family_id") or 0)}))
    await step(report, "simulation.state", query("simulation.state", PLAYER, {"system": "npc_life"}))
    await step(report, "simulation.status", query("simulation.status", PLAYER, {}))
    await step(report, "world.recent_actions", query("world.recent_actions", PLAYER, {"limit": 5}))
    await step(report, "market.catalog", query("market.catalog", PLAYER, {}))
    rows = await step(report, "market.rows", query("market.rows", PLAYER, {"location": town, "limit": 5}))
    await step(report, "equipment.power", query("equipment.power", PLAYER, {}))
    await step(report, "player_family.status", query("player_family.status", PLAYER, {}))
    await step(report, "secret_realm.rotation", query("secret_realm.rotation", PLAYER, {}))
    await step(report, "exploration.event.status with no event", query("exploration.event.status", PLAYER, {}))
    await step(report, "sect.status", query("sect.status", PLAYER, {}))
    await step(report, "sense.inspect the area", act("sense.inspect", PLAYER, {"mode": "area"}))
    await step(report, "sense.conceal on", act("sense.conceal", PLAYER, {"active": True}))
    await step(report, "sense.conceal off", act("sense.conceal", PLAYER, {"active": False}))
    rolled = await step(report, "check.resolve body vs TN 10", act("check.resolve", PLAYER, {"attribute": "body", "tn": 10, "label": "playtest"}))
    if rolled is not None:
        report.add("PASS", "the check is a roll, reported", f"total={rolled.get('total')} success={rolled.get('success')}")
    read = await step(report, "appraisal.read a carried herb", act("appraisal.read", PLAYER, {"item_id": "spirit_herb"}))
    if read is not None:
        report.add("PASS", "the reading is a roll, reported", f"{read.get('reading')}")

    # -- the bridge: the four operations the bot calls on the engine's behalf
    await step(report, "scene.transition to the street", engine.action("scene.transition", PLAYER, {"physical_location": town, "scene_type": "world", "scene_key": "", "scene_label": "the street", "metadata": {}}))
    await step(report, "relationship.update", engine.action("relationship.update", PLAYER, {"npc_name": "Elder Su Yan", "trust": 1, "summary": "playtest"}))
    await step(report, "cultivation.reward", engine.action("cultivation.reward", PLAYER, {"cultivation": 1, "spirit_stones": 1, "insight_xp": 1, "event_type": "playtest"}))

    # -- storage
    await step(report, "storage.deposit two herbs", act("storage.deposit", PLAYER, {"item_id": "spirit_herb", "quantity": 2}))
    await step(report, "storage.withdraw one", act("storage.withdraw", PLAYER, {"item_id": "spirit_herb", "quantity": 1}))
    ring = await step(report, "storage.upgrade to the Earth-Grade Spatial Ring", act("storage.upgrade", PLAYER, {"item_id": "spatial_ring"}))
    if ring is not None:
        report.add("PASS" if int(ring.get("slot_capacity") or 0) == 80 else "FAIL", "the ring holds eighty stacks", str(ring.get("slot_capacity")))
    await step(report, "storage.upgrade never downgrades", act("storage.upgrade", PLAYER, {"item_id": "spatial_pouch"}), expect_error="would hold only")
    await audited("admin.player.grant_storage", {"user_id": BUYER, "container_id": "playtest_ring", "name": "Playtest Ring", "grade": "Earth", "slot_capacity": 40, "living_space": False, "reason": "playtest"})

    # -- equipment and artifacts
    bound = await step(report, "equipment.bind the spirit iron sword", act("equipment.bind", PLAYER, {"item_id": "spirit_iron_sword"}))
    equipment_id = int((bound or {}).get("equipment_id") or 0)
    if equipment_id:
        await step(report, "equipment.equip", act("equipment.equip", PLAYER, {"id": equipment_id}))
        await step(report, "equipment.unequip", act("equipment.unequip", PLAYER, {"id": equipment_id}))
        await step(report, "equipment.repair", act("equipment.repair", PLAYER, {"id": equipment_id}))
        await audited("admin.player.remove_equipment", {"user_id": PLAYER, "equipment_id": equipment_id, "reason": "playtest"})
    await step(report, "artifact.bond with a carried herb", act("artifact.bond", PLAYER, {"item_id": "spirit_herb"}))
    await step(report, "artifact.awaken needs Bond 3", act("artifact.awaken", PLAYER, {"item_id": "spirit_herb", "spirit_name": "Playtest"}), expect_error="Bond 3")

    # -- arrays
    await step(report, "array.deploy a qi-gathering disk", act("array.deploy", PLAYER, {"item_id": "minor_qi_gathering_array_disk"}))
    await step(report, "stand at realm 1 for the transit array", gm("admin.player.set_realm", {"user_id": PLAYER, "realm_index": 1, "phase": 1, "reason": "playtest"}))
    crossed = await step(report, "array.use the Greenriver-Imperial transit array", act("array.use", PLAYER, {"array_id": "greenriver_imperial"}))
    if crossed is not None:
        report.add("PASS" if crossed.get("to") == "Azure Crown Imperial City" else "FAIL", "the array carries to the capital", str(crossed.get("to")))
    await step(report, "back to the town", gm("admin.player.teleport", {"user_id": PLAYER, "location": town, "reason": "playtest"}))

    # -- a method slip, the hills, a purge
    slip = next((k for k, v in world["items"].items() if str(v.get("teaches_recipe") or "").strip()), "")
    if slip:
        await step(report, f"grant the slip {slip}", gm("admin.player.adjust_item", {"user_id": PLAYER, "item_id": slip, "quantity": 1, "reason": "playtest"}))
        await step(report, "recipe.learn from the slip", act("recipe.learn", PLAYER, {"item_id": slip}))
    else:
        report.add("FAIL", "recipe.learn", "the catalogue carries no item that teaches a method")
    await step(report, "the hills", gm("admin.player.teleport", {"user_id": PLAYER, "location": "Cloudspine Foothills", "reason": "playtest"}))
    await step(report, "cooldowns cleared", gm("admin.player.reset_cooldowns", {"user_id": PLAYER, "reason": "playtest"}))
    await step(report, "forage.resolve", act("forage.resolve", PLAYER, {}))
    await step(report, "back to the town", gm("admin.player.teleport", {"user_id": PLAYER, "location": town, "reason": "playtest"}))
    await audited("admin.player.set_pill_toxicity", {"user_id": PLAYER, "pill_toxicity": 40, "reason": "playtest"})
    await step(report, "alchemy.purge", act("alchemy.purge", PLAYER, {}))

    # -- a Law
    await step(report, "stand at realm 6 for a Law", gm("admin.player.set_realm", {"user_id": PLAYER, "realm_index": 6, "phase": 1, "reason": "playtest"}))
    await step(report, "cooldowns cleared", gm("admin.player.reset_cooldowns", {"user_id": PLAYER, "reason": "playtest"}))
    grasped = await step(report, "law.comprehend the Sword", act("law.comprehend", PLAYER, {"law": "sword", "spend_insight": False}))
    if grasped is not None:
        report.add("PASS", "comprehension is a roll, reported", f"roll={grasped.get('roll')} comprehension={grasped.get('comprehension')}")
    await step(report, "law.technique before its stage", act("law.technique", PLAYER, {"technique": "folded_step"}), expect_error="required")

    # -- a fight, at a realm that cannot lose it
    await step(report, "an overwhelming cultivator", gm("admin.player.set_realm", {"user_id": PLAYER, "realm_index": 7, "phase": 9, "reason": "playtest"}))
    targets = await step(report, "combat.targets in the town", query("combat.targets", PLAYER, {"location": town}))
    candidates = [r for r in list((targets or {}).get("targets") or (targets or {}).get("rows") or []) if isinstance(r, dict) and r.get("name")]
    npcs = [r for r in candidates if str(r.get("kind") or r.get("type") or "npc") == "npc"] or candidates
    opponent = str(min(npcs, key=lambda r: int(r.get("realm_index") or 0)).get("name")) if npcs else ""
    if not opponent:
        report.add("FAIL", "combat.start", f"nobody to challenge in {town}: {str(targets)[:200]}")
    else:
        battle = await step(report, f"combat.start against {opponent}", act("combat.start", PLAYER, {"kind": "challenge", "npc_name": opponent, "source": "playtest"}))
        battle_id = int((battle or {}).get("battle_id") or 0)
        if battle_id:
            await step(report, "combat.recovery_item", act("combat.recovery_item", PLAYER, {"battle_id": battle_id, "item_id": "recovery_pill"}))
            await step(report, "combat.technique refuses a technique nobody has", act("combat.technique", PLAYER, {"battle_id": battle_id, "technique": "no_such_technique"}), expect_error="unknown Law technique")
            await step(report, "combat.apply_damage", engine.action("combat.apply_damage", PLAYER, {"battle_id": battle_id, "damage": 1}))
            struck = await step(report, "manual.technique Cloud-Step Draw", act("manual.technique", PLAYER, {"technique_id": "azure_cloud_foundation_sword_canon_cloud_step_draw"}))
            if struck is not None:
                report.add("PASS", "the technique is a roll, reported", f"damage={struck.get('damage')} forbidden={struck.get('forbidden')}")
            won, rounds = False, 0
            try:
                for _ in range(25):
                    turn = await act("combat.turn", PLAYER, {"battle_id": battle_id, "style": "attack"})
                    rounds += 1
                    if int(turn.get("npc_hp") or 0) <= 0:
                        won = True
                        break
                    if int(turn.get("player_hp") or 1) <= 0:
                        break
            except GameEngineError as exc:
                report.add("FAIL", "combat.turn", str(exc))
            report.add("PASS" if won else "FAIL", "combat.turn: an overwhelming cultivator wins inside twenty-five rounds", f"won={won} rounds={rounds}")
            await step(report, "combat.finalize spare", act("combat.finalize", PLAYER, {"battle_id": battle_id, "outcome": "spare"}))
        second = await step(report, "combat.start again for the GM's lever", act("combat.start", PLAYER, {"kind": "challenge", "npc_name": opponent, "source": "playtest"}))
        if second is not None:
            await audited("admin.player.clear_battle", {"user_id": PLAYER, "reason": "playtest"})
    await either("admin.player.clear_condition", gm("admin.player.clear_condition", {"user_id": PLAYER, "clear_all": True, "reason": "playtest"}), "no active conditions")
    await step(report, "condition.treat with nothing to treat", act("condition.treat", PLAYER, {"condition": "bruised"}), expect_error="active condition not found")

    # -- a party, a formation, a raid
    party = await step(report, "party.create", act("party.create", PLAYER, {"name": "Playtest Party"}))
    party_id = int((party or {}).get("party_id") or 0)
    if party_id:
        await step(report, "party.join by the buyer", act("party.join", BUYER, {"party_id": party_id}))
        formation = await step(report, "formation.create", act("formation.create", PLAYER, {"name": "Playtest Line"}))
        formation_id = int((formation or {}).get("formation_id") or 0)
        if formation_id:
            await step(report, "formation.assign the player to the vanguard", act("formation.assign", PLAYER, {"formation_id": formation_id, "target_user_id": PLAYER, "position": "vanguard"}))
            await step(report, "formation.assign the buyer to the core", act("formation.assign", PLAYER, {"formation_id": formation_id, "target_user_id": BUYER, "position": "core"}))
            await step(report, "formation.activate", act("formation.activate", PLAYER, {"formation_id": formation_id, "stance": "balanced"}))
            await step(report, "formation.stance", act("formation.stance", PLAYER, {"formation_id": formation_id, "stance": "aggressive"}))
        await step(report, "party.leave by the buyer", act("party.leave", BUYER, {}))
        boss = await step(report, "boss.start the Iron-Tusk Boar King", act("boss.start", PLAYER, {"template_key": "iron_tusk_boar_king"}))
        encounter_id = int((boss or {}).get("encounter_id") or 0)
        status = "active"
        if encounter_id:
            try:
                for _ in range(30):
                    acted = await act("boss.act", PLAYER, {"encounter_id": encounter_id, "style": "attack"})
                    status = str(acted.get("status") or "active")
                    if status != "active":
                        break
            except GameEngineError as exc:
                report.add("FAIL", "boss.act", str(exc))
            report.add("PASS", "boss.act: how the raid went, for the record", f"status={status}")
            if status == "victory":
                await step(report, "boss.claim", act("boss.claim", PLAYER, {"id": encounter_id}))
            else:
                await step(report, "boss.claim before a victory", act("boss.claim", PLAYER, {"id": encounter_id}), expect_error="no unclaimed reward")
        await either("party.leave by the player", act("party.leave", PLAYER, {}), "during an active boss encounter")

    # -- a duel, a partnership, a house
    for uid in (PLAYER, BUYER):
        await step(report, f"{uid} to the hills", gm("admin.player.teleport", {"user_id": uid, "location": "Cloudspine Foothills", "reason": "playtest"}))
    challenge = await step(report, "pvp.challenge the buyer", act("pvp.challenge", PLAYER, {"target_user_id": BUYER, "stakes": "honour", "ttl_seconds": 600}))
    challenge_id = int((challenge or {}).get("challenge_id") or 0)
    if challenge_id:
        match = await step(report, "pvp.respond accept", act("pvp.respond", BUYER, {"challenge_id": challenge_id, "accept": True}))
        match_id = int((match or {}).get("match_id") or 0)
        if match_id:
            blow = await step(report, "pvp.act attack (the challenger's turn)", act("pvp.act", PLAYER, {"match_id": match_id, "style": "attack"}))
            if blow is not None and not blow.get("finished"):
                ended = await step(report, "pvp.act surrender", act("pvp.act", BUYER, {"match_id": match_id, "style": "surrender"}))
                if ended is not None:
                    report.add("PASS" if ended.get("finished") else "FAIL", "a surrender ends the duel", f"winner={ended.get('winner_user_id')}")
            elif blow is not None:
                report.add("PASS", "one blow ended the duel", f"winner={blow.get('winner_user_id')}")
    proposal = await step(report, "dao.propose to the buyer", act("dao.propose", PLAYER, {"partner_user_id": BUYER}))
    partnership_id = int((proposal or {}).get("partnership_id") or (proposal or {}).get("id") or 0)
    if partnership_id:
        await step(report, "dao.respond accept", act("dao.respond", BUYER, {"partnership_id": partnership_id, "accept": True}))
        await step(report, "dao.dual_cultivate side by side", act("dao.dual_cultivate", PLAYER, {}))
        await step(report, "dao.sever", act("dao.sever", PLAYER, {}))
    house = await step(report, "player_family.found", act("player_family.found", PLAYER, {"name": "House of Playtest"}))
    if house is not None:
        await step(report, "player_family.invite the buyer", act("player_family.invite", PLAYER, {"invitee_user_id": BUYER}))
        await step(report, "player_family.respond accept", act("player_family.respond", BUYER, {"accept": True}))
        child = await step(report, "player_family.child", act("player_family.child", PLAYER, {"child_name": "Playtest Child", "gender": "female"}))
        if child is not None:
            report.add("PASS", "the child's root is a roll, reported", f"{child.get('spiritual_root')}")
        await step(report, "player_family.status with a house", query("player_family.status", PLAYER, {}))
        await step(report, "player_family.leave by the buyer", act("player_family.leave", BUYER, {}))
        await step(report, "player_family.leave by the founder", act("player_family.leave", PLAYER, {}))

    # -- the markets: a trade declined, a stall, an underworld post
    for uid in (PLAYER, BUYER):
        await step(report, f"{uid} back to the town", gm("admin.player.teleport", {"user_id": uid, "location": town, "reason": "playtest"}))
    for uid in (PLAYER, BUYER):
        await step(report, f"{uid} to the inn", gm("admin.player.teleport", {"user_id": uid, "location": inn or capital, "reason": "playtest"}))
    offer = await step(report, "trade.offer to decline", act("trade.offer", PLAYER, {"to_user_id": BUYER, "give_items": {"spirit_herb": 1}, "want_items": {"recovery_pill": 1}}))
    if offer is not None:
        await step(report, "trade.decline by the buyer", act("trade.decline", BUYER, {"offer_id": int(offer.get("offer_id") or 0)}))
    for uid in (PLAYER, BUYER):
        await step(report, f"{uid} back to the town", gm("admin.player.teleport", {"user_id": uid, "location": town, "reason": "playtest"}))
    stock = sorted([r for r in list((rows or {}).get("rows") or (rows or {}).get("items") or []) if isinstance(r, dict) and r.get("item_id")],
                   key=lambda r: int(r.get("buy_price") or r.get("unit_price") or 0))
    if stock:
        item = str(stock[0]["item_id"])
        await step(report, "market.quote", query("market.quote", PLAYER, {"location": town, "item_id": item}))
        await step(report, f"market.trade buy {item}", act("market.trade", PLAYER, {"item_id": item, "quantity": 1, "buy": True}))
        await step(report, f"market.trade sell {item}", act("market.trade", PLAYER, {"item_id": item, "quantity": 1, "buy": False}))
    else:
        report.add("FAIL", "market.trade", f"no stall rows in {town}: {str(rows)[:200]}")
    await audited("admin.player.karma", {"user_id": PLAYER, "delta": -60, "reason": "playtest"}, name="admin.player.karma down to the underworld")
    posts = await db.list_active_black_markets(await clock())
    post = next((p for p in posts if str(p.get("world_name")) == "Mortal World"), None)
    if post is None:
        report.add("FAIL", "black_market.trade", "no black-market post is open in the Mortal World after the forced batch")
    else:
        where = str(post.get("location"))
        await step(report, f"to the underworld post at {where}", gm("admin.player.teleport", {"user_id": PLAYER, "location": where, "reason": "playtest"}))
        market = await db.get_active_black_market(where, await clock()) or {}
        goods = sorted([g for g in list(market.get("stock") or market.get("items") or []) if isinstance(g, dict) and int(g.get("quantity") or 0) > 0],
                       key=lambda g: int(g.get("unit_price") or 0))
        if not goods:
            report.add("FAIL", "black_market.trade", f"the post at {where} has no stock: {sorted(market)}")
        else:
            bought = await step(report, f"black_market.trade buy {goods[0].get('item_id')}", act("black_market.trade", PLAYER, {"item_id": str(goods[0].get("item_id")), "quantity": 1, "buy": True}))
            if bought is not None:
                report.add("PASS", "detection is a roll, reported", f"detected={bought.get('detected')} heat={bought.get('heat')}")
                crime = dict(bought.get("crime") or {})
                if int(crime.get("crime_id") or 0):
                    await either("crime.atone", act("crime.atone", PLAYER, {"crime_id": int(crime["crime_id"])}), "restitution requires", "return to")
                else:
                    await step(report, "crime.atone with no record", act("crime.atone", PLAYER, {"crime_id": 999999}), expect_error="does not exist")
        await step(report, "back to the town", gm("admin.player.teleport", {"user_id": PLAYER, "location": town, "reason": "playtest"}))
    await audited("admin.player.karma", {"user_id": PLAYER, "delta": 60, "reason": "playtest"}, name="admin.player.karma back up")

    # -- a caravan and a seclusion, each waited out on the world clock
    caravan = await step(report, "caravan.dispatch five herbs to Riverguard City", act("caravan.dispatch", PLAYER, {"destination": "Riverguard City", "item_id": "spirit_herb", "quantity": 5, "escort": 2}))
    if caravan is not None:
        await step(report, "the road is walked", gm("admin.world.advance_time", {"minutes": int(caravan.get("travel_minutes") or 0) + 30, "reason": "playtest"}))
        settled = await step(report, "caravan.settle", act("caravan.settle", PLAYER, {}))
        if settled is not None:
            report.add("PASS", "interception is a roll, reported", str(settled.get("resolved"))[:160])
    await step(report, "to the capital, a safe place to sit", gm("admin.player.teleport", {"user_id": PLAYER, "location": capital, "reason": "playtest"}))
    started = await step(report, "seclusion.start a day of qi seclusion", act("seclusion.start", PLAYER, {"mode": "qi", "duration_game_minutes": 1440, "location": capital}))
    if started is not None:
        await step(report, "a day passes", gm("admin.world.advance_time", {"minutes": 1500, "reason": "playtest"}))
        done = await step(report, "seclusion.settle", act("seclusion.settle", PLAYER, {}))
        if done is not None:
            report.add("PASS" if int(done.get("awarded_now") or 0) > 0 else "FAIL", "a day in seclusion pays", str(done.get("awarded_now")))

    # -- a secret realm the GM opens, and a key that opens another
    await step(report, "back to the first realm for the grotto", gm("admin.player.set_realm", {"user_id": PLAYER, "realm_index": 0, "phase": 1, "reason": "playtest"}))
    await step(report, "to Moonfen Marsh, where the grotto opens", gm("admin.player.teleport", {"user_id": PLAYER, "location": "Moonfen Marsh", "reason": "playtest"}))
    await audited("admin.world.spawn_realm", {"realm_id": "verdant_immortal_grotto", "title": "Verdant Immortal Grotto", "location": "Moonfen Marsh", "open_hours": 4, "reason": "playtest"})
    entered = await step(report, "secret_realm.enter", act("secret_realm.enter", PLAYER, {"realm_id": "verdant_immortal_grotto", "cooldown_seconds": 0}))
    if entered is not None:
        rooms = 0
        try:
            for _ in range(12):
                await act("secret_realm.explore", PLAYER, {"cooldown_seconds": 0})
                rooms += 1
        except GameEngineError as exc:
            # The last room ends the run and the realm lets go of the player,
            # so the ask after it is refused as "not inside"; a run that sealed
            # or reached its end reads the same way.
            ended = any(text in str(exc) for text in ("reached its end", "sealed", "not inside an active secret realm"))
            report.add("PASS" if ended else "FAIL", "secret_realm.explore to the last room", f"{rooms} rooms, then: {exc}")
        else:
            report.add("PASS", "secret_realm.explore", f"{rooms} rooms explored")
        await step(report, "secret_realm.leave", act("secret_realm.leave", PLAYER, {}))
        if entered.get("event_key"):
            await audited("admin.world_event.end", {"event_key": str(entered["event_key"]), "reason": "playtest"})
    await step(report, "to the hills with the nine-echo token", gm("admin.player.teleport", {"user_id": PLAYER, "location": "Cloudspine Foothills", "reason": "playtest"}))
    opened = await step(report, "spatial_key.use the Nine-Echo Spatial Token", act("spatial_key.use", PLAYER, {"item_id": "nine_echo_spatial_token"}))
    if opened is not None and opened.get("event_key"):
        await audited("admin.world_event.end", {"event_key": str(opened["event_key"]), "reason": "playtest"}, name="admin.world_event.end the sword grave")

    # -- a surprise made certain, of each kind the content rolls
    # `unexpected_event_chance_percent: 100` makes the roll for *a* surprise
    # certain; *which* kind comes (personal, world_event, secret_realm) is
    # the roll's. Each kind is driven the moment it comes and the loop goes
    # on until the two with operations behind them have both come, or twenty
    # explores have not - in which case the missing kind is reported, never
    # failed (CLAUDE.md: never assert that a random thing happened). A
    # personal event blocks further exploring until it is left, so it is
    # worked and left at once; a world event is acted in, its site engaged and
    # then closed by the GM; a rift is closed.
    def _event_id(node: Any) -> str:
        """The `event_id` wherever the surprise carries it (`id` is the
        definition's, not the event's)."""
        if isinstance(node, dict):
            if node.get("event_id"):
                return str(node["event_id"])
            for value in node.values():
                found = _event_id(value)
                if found:
                    return found
        if isinstance(node, list):
            for value in node:
                found = _event_id(value)
                if found:
                    return found
        return ""

    came: dict[str, str] = {}
    for _ in range(20):
        if {"personal", "world_event"} <= set(came):
            break
        surprise_key = aid("exploration:event")
        try:
            await gm("admin.player.reset_cooldowns", {"user_id": PLAYER, "reason": "playtest"})
            surprised = await act("exploration.explore", PLAYER, {
                "cooldown_seconds": 0, "unexpected_events_enabled": True, "unexpected_event_chance_percent": 100, "event_key": surprise_key})
        except GameEngineError as exc:
            report.add("FAIL", "exploration.explore with a surprise certain", str(exc))
            break
        surprise = dict(surprised.get("surprise") or surprised.get("event") or {})
        kind = str(surprised.get("kind") if str(surprised.get("kind")) == "event_active" else surprise.get("kind") or "")
        if kind in ("personal", "event_active"):
            event_id = _event_id(surprise) or surprise_key
            if kind == "personal":
                came.setdefault(kind, event_id)
                await step(report, "exploration.event.status", query("exploration.event.status", PLAYER, {"event_id": event_id}))
                acted = await step(report, "exploration.event.act observe", act("exploration.event.act", PLAYER, {"event_id": event_id, "action": "observe"}))
                if acted is not None:
                    report.add("PASS", "the event's roll, reported", f"success={acted.get('success')} outcome={acted.get('outcome')}")
            await either("exploration.event.leave", act("exploration.event.leave", PLAYER, {"event_id": event_id}), "no longer active", "not found")
        elif kind == "world_event":
            came.setdefault(kind, surprise_key)
            acted = await step(report, "world_event.act observe", act("world_event.act", PLAYER, {"event_key": surprise_key, "action_key": "observe"}))
            if acted is not None:
                report.add("PASS", "the event's roll, reported", f"success={acted.get('success')} state={acted.get('state')}")
            nodes = [n for n in list(await db.list_world_event_nodes(surprise_key) or []) if isinstance(n, dict) and n.get("node_key")]
            if nodes:
                worked = await step(report, f"world_event.engage {nodes[0].get('node_key')}", act("world_event.engage", PLAYER, {"event_key": surprise_key, "node_key": str(nodes[0]["node_key"])}))
                if worked is not None:
                    report.add("PASS", "the node's roll, reported", f"success={worked.get('success')} remaining={worked.get('node_remaining')}")
            else:
                report.add("FAIL", "world_event.engage", f"the surprise {surprise.get('id')} spawned no site nodes")
            await audited("admin.world_event.end", {"event_key": surprise_key, "reason": "playtest"}, name="admin.world_event.end the surprise")
        elif kind == "secret_realm":
            came.setdefault(kind, surprise_key)
            await either("admin.world_event.end the rift", gm("admin.world_event.end", {"event_key": surprise_key, "reason": "playtest"}), "not found", "unknown")
        else:
            report.add("FAIL", "exploration.explore with a surprise certain", f"no surprise in the result: {sorted(surprised.keys())} kind={kind!r}")
            break
    for kind in ("personal", "world_event", "secret_realm"):
        report.add("PASS", f"a {kind} surprise came" if kind in came else f"a {kind} surprise did not come in twenty explores (the dice, not the wiring)", came.get(kind, ""))

    # -- the household simulated, a child named, the supporter's gift
    simulated = await step(report, "family.simulate a season", act("family.simulate", PLAYER, {"family_id": int(fam.get("family_id") or 0)}))
    if simulated is not None:
        report.add("PASS", "what the season did to the house, for the record", str(simulated.get("history") or simulated.get("events") or "")[:160])
    born = await step(report, "family.add_child", act("family.add_child", PLAYER, {"name": "Playtest Sibling", "gender": "male"}))
    if born is not None:
        report.add("PASS", "the child's root is a roll, reported", str(born.get("spiritual_root") or born.get("child") or "")[:120])
    await step(report, "support.weekend", query("support.weekend", PLAYER, {}))
    await step(report, "support.vote_status", query("support.vote_status", PLAYER, {}))
    await step(report, "support.vote_claim", act("support.vote_claim", PLAYER, {}))
    await step(report, "support.vote_claim again waits", act("support.vote_claim", PLAYER, {}), expect_error="cooldown active")

    # -- the GM's remaining levers, each audited
    await audited("admin.player.fate", {"user_id": BUYER, "delta": 5, "reason": "playtest"})
    await audited("admin.player.set_resource_caps", {"user_id": BUYER, "qi_max": 60, "vitality_max": 60, "reason": "playtest"})
    await audited("admin.player.set_spiritual_root", {"user_id": BUYER, "grade": "Heaven", "mutation": "", "purity": 80, "reason": "playtest"})
    await either("admin.player.set_bloodline", gm("admin.player.set_bloodline", {"user_id": BUYER, "bloodline_id": "azure_wolf", "purity": 50, "evolution_stage": 0, "progress": 10, "reason": "playtest"}), "bloodline not found")
    await audited("admin.player.set_physique", {"user_id": BUYER, "evolution_stage": 0, "progress": 10, "stability": 50, "reason": "playtest"})
    await audited("admin.player.set_tribulation", {"user_id": BUYER, "gate_realm_index": 7, "mode": "clear", "reason": "playtest"})
    await audited("admin.player.set_realm_perfection", {"user_id": BUYER, "track": "cultivation", "realm_index": 0, "progress": 100, "reason": "playtest"})
    await step(report, "perfection.abandon is idempotent", act("perfection.abandon", BUYER, {}))
    await step(report, "perfection.body_abandon is idempotent", act("perfection.body_abandon", BUYER, {}))
    await audited("admin.player.set_sect", {"user_id": BUYER, "sect_name": "Azure Cloud Sect", "rank_name": "Outer Disciple", "rank_level": 10, "reason": "playtest"})
    await audited("admin.player.set_sect_rank", {"user_id": BUYER, "rank_name": "Inner Disciple", "rank_level": 30, "reason": "playtest"})
    await audited("admin.player.set_master", {"disciple_user_id": BUYER, "master_user_id": PLAYER, "reason": "playtest"})
    await audited("admin.player.master_attention", {"disciple_user_id": BUYER, "delta": 5, "reason": "playtest"})
    await audited("admin.player.set_master", {"disciple_user_id": BUYER, "master_user_id": PLAYER, "clear": True, "reason": "playtest"}, name="admin.player.set_master clear")
    await audited("admin.player.set_sect", {"user_id": BUYER, "remove": True, "rank_level": 0, "reason": "playtest"}, name="admin.player.set_sect remove")
    await audited("admin.npc.relocate", {"npc_name": "Elder Su Yan", "location": town, "reason": "playtest"})
    await audited("admin.automation.set", {"system": "npc_life", "enabled": True, "reason": "playtest"})
    await audited("admin.simulation.interval", {"system": "npc_life", "days": 7, "reason": "playtest"})
    await audited("admin.commission.review", {"quest_key": str(world["commissions"][0]["quest_key"]), "status": "approved", "reason": "playtest"})
    await either("admin.commission.retire", gm("admin.commission.retire", {"user_id": BUYER, "reason": "playtest"}), "holds no commission")
    await audited("admin.audit", {"action": "playtest.note", "target": str(PLAYER), "before": {}, "after": {}, "reason": "playtest"}, action="playtest.note")
    await audited("admin.bulk.grant_currency", {"currency_id": "low_spirit_stone", "amount": 1, "reason": "playtest"})
    await audited("admin.bulk.reset_cooldowns", {"reason": "playtest"})
    await step(report, "the realm the earlier sections left", gm("admin.player.set_realm", {"user_id": PLAYER, "realm_index": realm_before[0], "phase": realm_before[1], "reason": "playtest"}))
    await step(report, "home to the town", gm("admin.player.teleport", {"user_id": PLAYER, "location": town, "reason": "playtest"}))

    # ---- 21. samsara, and what the hands remember (v1.0.0-rc.32) -------------
    # Last, because it ends the character. The trades this life practised go
    # into its record before the wipe, and a fresh rebirth remembers nothing
    # of them yet: awakened memory is 0, so the echo is 0 by construction.
    await step(report, "lifecycle.true_death", act("lifecycle.true_death", PLAYER, {"reason": "playtest", "max_wait_seconds": 1}))
    cycle = await step(report, "lifecycle.samsara_status", query("lifecycle.samsara_status", PLAYER, {}))
    if cycle is not None:
        report.add("PASS", "the wheel is turning", f"ready={cycle.get('ready')} remaining={cycle.get('seconds_remaining')}")
    await step(report, "the wheel is hurried", gm("admin.player.force_reincarnation_ready", {"user_id": PLAYER, "reason": "playtest"}))
    reborn = await step(report, "lifecycle.reincarnate", act("lifecycle.reincarnate", PLAYER, {"name": "Second Wen", "gender": "female", "path": "Sword Cultivator"}))
    if reborn is not None:
        trades = dict(reborn.get("past_life_trades") or {})
        report.add("PASS" if int(trades.get("Forging", 0)) == 1 else "FAIL", "the past life's Apprentice Forging is recorded", f"{trades}")
    legacy = await step(report, "the soul record", db.get_soul_legacy(PLAYER))
    if legacy is not None:
        past = list(legacy.get("past_lives") or [])
        report.add("PASS" if past and isinstance(past[-1].get("professions"), dict) else "FAIL", "the record carries the professions", f"{past[-1] if past else past}")
    # The new life's household teaches its own trade; craft one of its
    # entry methods, whichever trade that turned out to be.
    # The wheel picks the new household, and a samsara household is minted
    # from its own roster: one whose archetype is among the thirteen the
    # send-off knows teaches its trade's entry methods, one that is not
    # teaches nothing, and a craft the new life never learned would be
    # refused for a reason that has nothing to do with the echo. So the
    # craft happens only where there is a taught method to craft with, and
    # is otherwise reported as skipped - the scenario, never the dice.
    sendoff = dict((reborn or {}).get("family_sendoff") or {})
    new_trade = str(sendoff.get("trade") or "")
    recipe_name = next((name for name, r in world["recipes"].items() if r.get("profession") == new_trade and int(r.get("min_level", 0)) == 0), "") if new_trade else ""
    if recipe_name:
        for item_id, qty in dict(world["recipes"][recipe_name].get("cost") or {}).items():
            await step(report, f"grant {item_id} x{qty} for the new life's {new_trade}", gm("admin.player.adjust_item", {"user_id": PLAYER, "item_id": item_id, "quantity": int(qty), "reason": "playtest"}))
        reforged = await step(report, f"craft.resolve {recipe_name} in the new life", act("craft.resolve", PLAYER, {"recipe": recipe_name}))
        if reforged is not None:
            report.add("PASS" if int(reforged.get("craft_echo", -1)) == 0 else "FAIL", "a fresh rebirth remembers nothing yet: craft_echo is 0", f"{reforged.get('craft_echo')}")
    else:
        report.add("PASS", "a fresh rebirth remembers nothing yet: craft_echo is 0",
                   f"skipped: the new household ({(reborn or {}).get('family_archetype')}) teaches no trade, so there is no taught method to craft with; the echo's zero case is held in craft_echo_test.go")

    # ---- 21b. the dynasty a new life inherits ---------------------------------
    # Reincarnation writes a samsara_dynasty_history row between the two
    # houses; a living incarnation investigates it, works its leads, and may
    # lay a claim. Whether a claim is open depends on the lineage the wheel
    # dealt (blood continuity, a fallen house, a culprit), so the claim and
    # its conflict hold "the claim, or the refusal that names why not" and
    # report which; the record and its investigation are certain.
    dossier = None
    try:
        for _ in range(6):
            dossier = await act("family.lineage.investigate", PLAYER, {})
    except GameEngineError as exc:
        report.add("FAIL", "family.lineage.investigate", str(exc))
    else:
        report.add("PASS", "family.lineage.investigate to the last level", f"level={(dossier or {}).get('investigation_level')} status={(dossier or {}).get('lineage_status')}")
    history_id = int((dossier or {}).get("history_id") or 0)
    if history_id:
        worked = 0
        try:
            for _ in range(8):
                await act("family.lineage.quest", PLAYER, {"history_id": history_id})
                worked += 1
        except GameEngineError as exc:
            report.add("PASS" if "no available investigation quest" in str(exc) else "FAIL", "family.lineage.quest until the leads run out", f"{worked} steps, then: {exc}")
        else:
            report.add("PASS", "family.lineage.quest", f"{worked} steps")
        claim = await either("family.dynasty.claim inheritance", act("family.dynasty.claim", PLAYER, {"history_id": history_id, "claim_type": "inheritance"}),
                             "blood inheritance is unavailable", "complete at least", "fully investigated")
        claim_id = int((claim or {}).get("claim_id") or 0)
        if claim_id and int((claim or {}).get("conflict_id") or 0):
            await step(report, "family.dynasty.conflict negotiate", act("family.dynasty.conflict", PLAYER, {"claim_id": claim_id, "tactic": "negotiate"}))
        else:
            await step(report, "family.dynasty.conflict with no conflict", act("family.dynasty.conflict", PLAYER, {"claim_id": claim_id or 999999, "tactic": "negotiate"}), expect_error="no dynasty conflict exists")

    # ---- 22. erasure -----------------------------------------------------------
    # Last of all, because it is the one lever that leaves nothing behind: the
    # ghost's row is gone afterwards, and the audit row says who did it.
    erased = await audited("admin.player.erase", {"user_id": GHOST, "reason": "playtest"})
    if erased is not None:
        gone = await db.get_character(GHOST)
        report.add("PASS" if gone is None else "FAIL", "the erased ghost has no character row", str(gone)[:80])
    return report


async def _local_floor(act, gm):
    """Seven lots on a local floor: the seventh must be refused."""
    await gm("admin.player.teleport", {"user_id": PLAYER, "location": "Riverguard City", "reason": "playtest"})
    await gm("admin.player.adjust_item", {"user_id": PLAYER, "item_id": "spirit_herb", "quantity": 10, "reason": "playtest"})
    await act("auction.enter", PLAYER, {})
    for _ in range(7):
        await act("auction.sell", PLAYER, {"item_id": "spirit_herb", "quantity": 1, "currency_id": "low_spirit_stone", "starting_bid": 1, "anonymous": False, "ends_at": time.time() + 600})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--launch", action="store_true", help="build, start and bootstrap a scratch engine for the run")
    parser.add_argument("--keep", action="store_true", help="with --launch, keep the scratch directory")
    args = parser.parse_args()
    proc = None
    tmp = Path(tempfile.mkdtemp(prefix="xianxia-playtest-"))
    try:
        if args.launch:
            proc, url, token = launch_engine(tmp)
            db_path = str(tmp / "playtest.sqlite3")
            bootstrap(url, token, db_path)
        else:
            url = os.environ.get("GAME_ENGINE_URL", "").rstrip("/")
            token = os.environ.get("ENGINE_AUTH_TOKEN", "")
            db_path = os.environ.get("DATABASE_PATH", str(tmp / "unused.sqlite3"))
            if not url or not token:
                raise SystemExit("set GAME_ENGINE_URL and ENGINE_AUTH_TOKEN, or pass --launch")
        report = asyncio.run(run(url, token, db_path))
        print(f"\n{len(report.rows)} steps, {report.failed} failed")
        return 1 if report.failed else 0
    finally:
        stop_engine(proc)
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

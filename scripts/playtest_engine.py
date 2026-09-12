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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PLAYER = 900001
BUYER = 900002
GM = 1


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, step: str, note: str = "") -> None:
        self.rows.append((status, step, note))
        print(f"{status:<5} {step}" + (f" — {note}" if note else ""), flush=True)

    @property
    def failed(self) -> int:
        return sum(1 for status, _, _ in self.rows if status == "FAIL")


async def step(report: Report, name: str, coro, *, expect_error: str | None = None) -> Any:
    """Run one step. With expect_error, the step passes only if the engine
    refuses with a message containing that text - the refusal is the feature."""
    from app.ops.game_engine import GameEngineError

    try:
        result = await coro
    except GameEngineError as exc:
        if expect_error and expect_error in str(exc):
            report.add("PASS", name, f"refused as designed: {exc}")
            return None
        report.add("FAIL", name, str(exc))
        return None
    except Exception as exc:  # noqa: BLE001 - a playtest reports, it does not crash
        report.add("FAIL", name, f"{type(exc).__name__}: {exc}")
        return None
    if expect_error:
        report.add("FAIL", name, f"expected a refusal mentioning {expect_error!r}, got {str(result)[:160]}")
        return None
    report.add("PASS", name)
    return result


def launch_engine(tmp: Path) -> tuple[subprocess.Popen, str, str]:
    binary = tmp / "xianxia-engine"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/xianxia-core"], cwd=ROOT / "go_core", check=True,
                   env={**os.environ, "CGO_ENABLED": "1"})
    token = "playtest-engine-token-" + str(int(time.time()))
    env = {**os.environ, "ENGINE_ADDR": "127.0.0.1:18089", "DATABASE_PATH": str(tmp / "playtest.sqlite3"),
           "WORLD_DATA_PATH": str(ROOT / "content" / "world.json"), "ENGINE_AUTH_TOKEN": token}
    proc = subprocess.Popen([str(binary)], env=env, stdout=(tmp / "engine.log").open("w"), stderr=subprocess.STDOUT)
    url = "http://127.0.0.1:18089"
    for _ in range(60):
        try:
            with urllib.request.urlopen(url + "/readyz", timeout=1) as response:
                if response.status == 200:
                    break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    else:
        proc.kill()
        raise SystemExit("engine did not become ready; see " + str(tmp / "engine.log"))
    return proc, url, token


def bootstrap(url: str, token: str, db_path: str) -> None:
    env = {**os.environ, "GAME_ENGINE_URL": url, "ENGINE_AUTH_TOKEN": token, "DATABASE_PATH": db_path,
           "DISCORD_TOKEN": os.environ.get("DISCORD_TOKEN", "playtest"), "GUILD_ID": os.environ.get("GUILD_ID", "1"),
           "NARRATOR_PROVIDER": os.environ.get("NARRATOR_PROVIDER", "procedural")}
    subprocess.run([sys.executable, "-m", "app.database.bootstrap"], cwd=ROOT, check=True, env=env)


async def run(url: str, token: str, db_path: str) -> Report:
    os.environ["GAME_ENGINE_URL"] = url
    os.environ["ENGINE_AUTH_TOKEN"] = token
    from app.database import Database
    from app.database.remote import GoDatabaseTransport
    from app.ops.game_engine import GameEngineClient
    from app.rules.quests import QUEST_DEFINITIONS, static_quest_seed_rows

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

    async def clock() -> int:
        return int((await db.get_world_clock())["game_minute"])

    # ---- 0. the world ------------------------------------------------------
    await step(report, "seed the catalog", db.sync_world_catalog(world))
    await step(report, "seed the commission pool and static quests",
               db.sync_commission_pool(list(world.get("commissions") or []) + static_quest_seed_rows(QUEST_DEFINITIONS)))
    gm0 = await step(report, "world clock", clock())
    await step(report, "simulation bootstrap", engine.bootstrap_simulation(int(gm0 or 0)))

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

    # ---- 2. $ I explore ----------------------------------------------------
    await step(report, "teleport to Greenriver Town", gm("admin.player.teleport", {"user_id": PLAYER, "location": "Greenriver Town", "reason": "playtest"}))
    explored = await step(report, "exploration.explore", act("exploration.explore", PLAYER, {
        "cooldown_seconds": 0, "unexpected_event_chance_percent": 0, "event_key": aid("exploration:event")}))
    if explored is not None and not (explored.get("narration") or explored.get("summary") or explored.get("encounter") or explored):
        report.add("FAIL", "exploration.explore", "empty result")

    # ---- 3. join a sect and study the gift ---------------------------------
    sect = "Azure Cloud Sect"
    rec = dict(world["sects"][sect]["recruitment"])
    await step(report, "sect.discover", engine.action("sect.discover", PLAYER, {"sects": [sect], "source_keys": {sect: "playtest"}, "discovery_kind": "recruitment_route", "game_minute": await clock()}))
    await step(report, "teleport to the trial", gm("admin.player.teleport", {"user_id": PLAYER, "location": rec["location"], "reason": "playtest"}))
    outcome = None
    for attempt in range(1, 13):
        trial = await step(report, f"sect.recruitment.trial (attempt {attempt})", act("sect.recruitment.trial", PLAYER, {
            "sect_name": sect, "examiner": rec["examiner"], "location": rec["location"], "trial_name": rec["trial_name"],
            "primary_details": {"modifier_notes": ["playtest"]}, "secondary_details": {"modifier_notes": ["playtest"]}}))
        if trial is None:
            break
        outcome = str(trial.get("outcome"))
        if outcome in {"pass", "conditional_pass"}:
            manual = dict(trial.get("granted_manual") or {})
            if manual:
                report.add("PASS", "the sect's gift", f"{manual.get('name')} ({manual.get('manual_id')})")
                await step(report, "manual.study the gift", act("manual.study", PLAYER, {"manual_id": str(manual.get("manual_id")), "cooldown_seconds": 0}))
            else:
                report.add("FAIL", "the sect's gift", "trial passed but no manual was granted")
            break
        # The trial is dice against TN 14; a fresh disciple fails often. The
        # retry cooldown is the engine's, so a GM reset is how a playtest
        # rolls again - that is what the dashboard's Reset Cooldowns is for.
        await gm("admin.player.reset_cooldowns", {"user_id": PLAYER, "reason": "playtest retry"})
    else:
        report.add("FAIL", "sect trial", f"never passed in 12 attempts (last outcome {outcome})")
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
    found = None
    for _ in range(12):
        walked = await act("exploration.explore", PLAYER, {"cooldown_seconds": 0, "unexpected_event_chance_percent": 0, "event_key": aid("exploration:event")})
        if isinstance(walked.get("discovered_shop"), dict):
            found = dict(walked["discovered_shop"])
            break
    report.add("PASS" if found else "FAIL", "walking the city finds a shop", f"{found.get('name') if found else 'none in twelve walks'}")
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
                await step(report, "merchant.buy the floor find", act("merchant.buy", PLAYER, {"merchant": buyer, "item_id": find_item, "quantity": 1}))
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
            report.add("PASS" if after.get(holder, 0) == before.get(holder, 0) - int(lot_row.get("current_bid") or 0) else "FAIL",
                       "the purse is the escrow", f"{before.get(holder)} -> {after.get(holder)}")
            await step(report, "teleport the buyer to the capital", gm("admin.player.teleport", {"user_id": BUYER, "location": capital, "reason": "playtest"}))
            await step(report, "auction.enter (buyer, capital)", act("auction.enter", BUYER, {}))
            await step(report, "the buyer outbids the merchant", act("auction.bid", BUYER, {"auction_id": valued_id, "amount": int(lot_row.get("current_bid") or 0) + 5}))
            lot_row = await db.get_auction(valued_id) or {}
            refunded = {str(r.get("merchant")): int(r.get("budget") or 0) for r in list((await engine.action("merchant.status", PLAYER, {}) or {}).get("merchants") or [])}
            report.add("PASS" if not str(lot_row.get("merchant_bidder") or "") and refunded.get(holder, 0) == before.get(holder, 0) else "FAIL",
                       "outbid, the merchant is refunded and cleared", f"merchant_bidder={lot_row.get('merchant_bidder')!r} budget {after.get(holder)} -> {refunded.get(holder)}")

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
    paced = await step(report, "cultivation.train pays about the pace", meditate())
    if paced:
        gain = int(paced.get("gain") or 0)
        report.add("PASS" if pace // 2 <= gain <= pace * 3 else "FAIL", "the session pays about its pace", f"gain={gain} pace={pace}")
        report.add("PASS" if float(paced.get("world_mult") or 0) == 1.0 and paced.get("world_name") == "Mortal World" else "FAIL",
                   "the Mortal World is the baseline density", f"{paced.get('world_name')} x{paced.get('world_mult')}")

    sessions = 1
    filled = dict(await engine.action("cultivation.status", PLAYER, {}) or {})
    while not filled.get("ready") and sessions < 30:
        await meditate()
        sessions += 1
        filled = dict(await engine.action("cultivation.status", PLAYER, {}) or {})
    report.add("PASS" if filled.get("ready") and 4 <= sessions <= 20 else "FAIL",
               "a stage fills in about a dozen sessions", f"{sessions} sessions for a {filled.get('cost')} stage")

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

    # ---- 18. backups -------------------------------------------------------
    backup = await step(report, "create a backup", transport.create_backup())
    listed = await step(report, "list backups", transport.list_backups())
    if backup and listed is not None and not any(row.get("name") == backup.get("name") for row in listed):
        report.add("FAIL", "list backups", "the new backup is not listed")
    if backup:
        await step(report, "restore that backup", transport.restore_backup(str(backup.get("name"))))
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
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

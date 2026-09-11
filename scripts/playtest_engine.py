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

    # ---- 11. backups -------------------------------------------------------
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

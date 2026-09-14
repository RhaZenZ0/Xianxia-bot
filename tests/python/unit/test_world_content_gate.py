"""The content the narrator and /explore read is complete (v0.21.5).

The 1.0 roadmap's v0.25 gate, landed early: every location has sense
hints, every location /explore can be used in has encounters (the engine
hard-errors on an empty list - `exploration_actions.go`: "current location
has no exploration encounters"), and every NPC the narrator is handed has
the fields it reads - personality, speech, want, fear, secret. Before this
release six locations hard-errored, seven had no hints, and the four world
rulers shared one copy-pasted speech/want/fear.
"""
from __future__ import annotations

import json
import re
import unittest

from app.rules.progression_systems import PROFESSIONS
from tests.support import PROJECT_ROOT

WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
NARRATOR_NPC_FIELDS = ("personality", "speech", "want", "fear", "secret")


class LocationContentTests(unittest.TestCase):
    def test_every_location_has_sense_hints(self):
        for name, loc in WORLD["locations"].items():
            with self.subTest(location=name):
                hints = loc.get("sense_hints") or []
                self.assertGreaterEqual(len(hints), 1, f"{name} has no sense_hints")
                for hint in hints:
                    self.assertGreater(len(hint.strip()), 20)

    def test_every_location_has_encounters(self):
        for name, loc in WORLD["locations"].items():
            with self.subTest(location=name):
                encounters = loc.get("encounters") or []
                self.assertGreaterEqual(len(encounters), 3, f"/explore hard-errors at {name}: no encounters")
                self.assertEqual(len(set(encounters)), len(encounters), "duplicate encounter")
                for text in encounters:
                    self.assertGreater(len(text.strip()), 20)

    def test_every_travel_endpoint_has_ground_and_weather(self):
        # `terrain` prices every road journey (the fallback branch is a flat
        # 75 minutes for a jade terrace and a volcanic pass alike) and both it
        # and `climate` now reach the narrator and the `/sense area` sweep.
        # An interior has no weather, so only the places a road can end at
        # are held to this.
        for name, loc in WORLD["locations"].items():
            if loc.get("district") or loc.get("shop") or loc.get("auction_house"):
                continue
            with self.subTest(location=name):
                self.assertTrue(str(loc.get("terrain") or "").strip(), f"{name} has no terrain")
                self.assertTrue(str(loc.get("climate") or "").strip(), f"{name} has no climate")

    def test_every_location_has_at_least_one_npc(self):
        # A location the narrator can be asked about needs someone to voice
        # it. The three samsara arrival grounds were the last without one.
        homes = {npc["location"] for npc in WORLD["npcs"].values()}
        for name in WORLD["locations"]:
            with self.subTest(location=name):
                self.assertIn(name, homes, f"{name} has no NPC")

    def test_safe_zone_encounters_do_not_start_violence(self):
        # A protected interior forbids violence (system prompt rule 15); its
        # encounters must be things that happen around you, not attacks on you.
        attack_words = ("attacks you", "ambushes you", "lunges at you", "strikes you")
        for name, loc in WORLD["locations"].items():
            if not loc.get("safe_zone"):
                continue
            for text in loc.get("encounters") or []:
                with self.subTest(location=name, encounter=text[:40]):
                    self.assertFalse(any(w in text.lower() for w in attack_words), text)


class AuctionHouseContentTests(unittest.TestCase):
    def test_every_city_has_an_auction_house_and_capitals_have_grand_ones(self):
        # v0.33.1: a house per city. A capital's is grand, with a live channel
        # of its own; an ordinary city's is local - smaller, and its world's
        # local floors share one channel, so a world is one channel, not twelve.
        from app.rules.realm_hubs import REALM_HUBS

        houses = WORLD["auction_houses"]
        by_entrance = {str(h.get("entrance_location")): h for h in houses.values()}
        for name, loc in WORLD["locations"].items():
            if loc.get("auction_house") or loc.get("private") or loc.get("district") or loc.get("shop"):
                continue
            if "City" in name or "Town" in name or loc.get("realm_hub"):
                with self.subTest(city=name):
                    self.assertIn(name, by_entrance, f"{name} has no auction house")
        for world, hub in REALM_HUBS.items():
            with self.subTest(world=world):
                self.assertEqual(by_entrance[hub["location"]].get("size"), "grand")
        grand = [h for h in houses.values() if h.get("size") == "grand"]
        local = [h for h in houses.values() if h.get("size") == "local"]
        self.assertEqual(len(grand) + len(local), len(houses), "every house is grand or local")
        grand_channels = [h["channel_name"] for h in grand]
        self.assertEqual(len(grand_channels), len(set(grand_channels)), "grand houses have channels of their own")
        local_channels = {WORLD["locations"][h["location"]]["world"]: set() for h in local}
        for h in local:
            local_channels[WORLD["locations"][h["location"]]["world"]].add(h["channel_name"])
        for world, names in local_channels.items():
            with self.subTest(world=world):
                self.assertEqual(len(names), 1, f"the local floors of {world} share one channel")
        self.assertTrue(set(grand_channels).isdisjoint(set().union(*local_channels.values())))
        for key, house in houses.items():
            with self.subTest(house=key):
                interior = WORLD["locations"][house["location"]]
                self.assertTrue(interior.get("safe_zone"), "an auction floor is protected")
                self.assertEqual(interior.get("auction_house"), key)
                self.assertEqual(interior.get("outside_location"), house["entrance_location"])
                self.assertIn(house["default_currency"], WORLD["currencies"])
                self.assertTrue(house.get("channel_name"))
                if house["size"] == "local":
                    self.assertLess(int(house["max_active_lots"]), 10, "a local floor is small")
                    self.assertLessEqual(int(house["max_lot_minutes"]), 720, "a local lot is short")
                else:
                    self.assertGreaterEqual(int(house["max_active_lots"]), 20)


class WorldCrossingContentTests(unittest.TestCase):
    """v1.0.0-rc.15: the gates between the worlds are payable and run both ways."""

    def test_every_array_charges_the_world_it_departs_from(self):
        # Three of the four crossings charged the DESTINATION world's tier-1
        # currency, which no reward path grants and no exchange converts - so
        # they could only be paid by someone who had already arrived. Dead
        # content, wired end to end.
        tier_one = {c["world"]: key for key, c in WORLD["currencies"].items() if int(c.get("tier") or 0) == 1}
        for key, array in WORLD["teleport_arrays"].items():
            with self.subTest(array=key):
                origin = WORLD["locations"][array["from"]]
                self.assertEqual(array["currency"], tier_one[origin["world"]], "an array must be payable where it stands")
                self.assertGreater(int(array["cost"]), 0)
                self.assertIn(array["to"], WORLD["locations"])

    def test_no_array_is_a_one_way_trap(self):
        pairs = {(a["from"], a["to"]) for a in WORLD["teleport_arrays"].values()}
        for origin, destination in sorted(pairs):
            with self.subTest(leg=f"{origin} -> {destination}"):
                self.assertIn((destination, origin), pairs, "no way back")

    def test_a_capital_is_a_city(self):
        # death_qi.go applies a city's gathering penalty only where
        # settlement_type is set, so a capital without one is quietly exempt.
        for name, loc in WORLD["locations"].items():
            if loc.get("realm_hub"):
                with self.subTest(capital=name):
                    self.assertEqual(loc.get("settlement_type"), "city")


class PricedLikeSomethingTests(unittest.TestCase):
    """v1.0.0-rc.15: every item is worth a decided number.

    Five separate valuation sites fall back to `max(8, sect_value*8)` when an
    item has no `base_price`, so an unpriced item is not merely undecided -
    it is priced by an accident. 37 items had no price at all and a dozen
    shared the exact placeholder 8, which made a Spirit-Iron Sword, a Recovery
    Pill and a set of Formation Flags worth the same.
    """

    def test_every_item_has_a_price_of_its_own(self):
        for key, item in WORLD["items"].items():
            with self.subTest(item=key):
                self.assertGreater(int(item.get("base_price") or 0), 0, f"{key} is priced by a fallback")
                self.assertGreater(int(item.get("sect_value") or 0), 0)

    def test_a_sword_is_not_a_pill(self):
        items = WORLD["items"]
        self.assertGreater(int(items["spirit_iron_sword"]["sect_value"]), int(items["recovery_pill"]["sect_value"]))

    def test_the_auction_floors_are_not_one_floor_copied(self):
        # 43 of the 48 were byte-identical, and the consignment tick reads the
        # lot cap to decide whether the world's finders can list anything.
        shapes = {
            (h.get("size"), int(h["max_active_lots"]), int(h["max_lot_minutes"]))
            for h in WORLD["auction_houses"].values()
        }
        self.assertGreaterEqual(len(shapes), 5, "every floor is the same floor")
        for key, house in WORLD["auction_houses"].items():
            with self.subTest(house=key):
                if house.get("size") == "local":
                    self.assertLess(int(house["max_active_lots"]), 10)
                    self.assertLessEqual(int(house["max_lot_minutes"]), 720)


class ProfessionsAreAllLiveTests(unittest.TestCase):
    """Every declared profession makes something.

    "Appraisal" sat in the list for releases with nothing granting it, and
    "Inscription" sat beside it in exactly the same state - eight professions,
    six of them real.
    """

    def test_every_crafting_profession_has_recipes(self):
        professions = {str(r.get("profession")) for r in WORLD["recipes"].values()}
        for craft in ("Alchemy", "Forging", "Formation", "Inscription"):
            with self.subTest(profession=craft):
                self.assertIn(craft, professions, f"{craft} is declared and makes nothing")

    def test_the_talismans_belong_to_the_inscribers(self):
        for key, recipe in WORLD["recipes"].items():
            if "Talisman" in key:
                with self.subTest(recipe=key):
                    self.assertEqual(recipe.get("profession"), "Inscription")


class BirthFamilySendoffTests(unittest.TestCase):
    """v1.0.0-rc.15: a household that can afford to does not send a child out
    to walk. Thirteen archetypes, thirteen heirlooms, no two the same."""

    SENDOFF = WORLD["birth_family_sendoff"]

    def _archetypes(self):
        import re

        source = (PROJECT_ROOT / "go_core" / "internal" / "game" / "birth_family_actions.go").read_text(encoding="utf-8")
        found = set(re.findall(r'\{"([a-z_]+)", "(?:martial|ghost)",', source))
        self.assertEqual(len(found), 13, "the archetype table changed shape")
        return found

    def test_every_household_sends_its_child_out_with_something(self):
        self.assertEqual(set(self.SENDOFF), self._archetypes(), "an archetype with no send-off")

    def test_no_two_households_give_the_same_thing(self):
        items = [str(entry["item"]) for entry in self.SENDOFF.values()]
        self.assertEqual(len(set(items)), len(items), "two households hand out the same object")

    def test_every_heirloom_is_a_real_described_flying_artifact(self):
        for archetype, entry in self.SENDOFF.items():
            with self.subTest(archetype=archetype):
                item = WORLD["items"].get(str(entry["item"]))
                self.assertIsNotNone(item, f"{archetype} sends out an item that does not exist")
                self.assertEqual(item.get("type"), "flight")
                self.assertGreaterEqual(int(item.get("flight") or 0), 3, "an heirloom that does not fly")
                self.assertTrue(str(item.get("flight_name") or "").strip())
                self.assertGreater(len(str(item.get("description") or "").strip()), 40)
                self.assertGreater(int(item.get("base_price") or 0), 0)
                self.assertTrue(item.get("market_excluded"), "an heirloom is inherited, not stocked")
                self.assertGreater(len(str(entry.get("line") or "").strip()), 20, "no words for the handover")

    def test_the_rich_houses_send_the_better_artifact(self):
        # The gift tracks the Wealth already written beside each household:
        # flight 5 above 60, flight 3 below it. A flight-3 heirloom goes quiet
        # once the owner's own realm reaches Core Formation; a flight-5 one
        # keeps carrying them to Ascension.
        from app.rules.birthfamily import FAMILY_ARCHETYPES

        wealth_of = {str(a["id"]): int(a["wealth"]) for a in FAMILY_ARCHETYPES}
        for archetype, entry in self.SENDOFF.items():
            wealth = wealth_of[archetype]
            flight = int(WORLD["items"][str(entry["item"])]["flight"])
            with self.subTest(archetype=archetype, wealth=wealth):
                self.assertEqual(flight, 5 if wealth >= 60 else 3)


class TreasureContentTests(unittest.TestCase):
    """v1.0.0-rc.15: a treasure the engine can read is a treasure somebody can
    hold. Both halves had been missing for the spatial keys - they named
    secret realms that do not exist, and nothing sold them."""

    def test_every_spatial_key_names_a_real_secret_realm(self):
        keys = {k: v for k, v in WORLD["items"].items() if v.get("spatial_key")}
        self.assertGreaterEqual(len(keys), 3)
        for key, item in keys.items():
            with self.subTest(item=key):
                rid = str(item["spatial_key"].get("secret_realm_id") or "")
                self.assertIn(rid, WORLD["secret_realms"], f"{key} opens nothing")
                self.assertGreater(int(item["spatial_key"].get("open_hours") or 0), 0)

    def test_every_spatial_key_is_sold_in_the_world_its_realm_is_in(self):
        for key, item in WORLD["items"].items():
            if not item.get("spatial_key"):
                continue
            realm = WORLD["secret_realms"][str(item["spatial_key"]["secret_realm_id"])]
            world = WORLD["locations"][realm["location"]]["world"]
            shelves = [
                shop for shop in WORLD["shops"].values()
                if any(line["item_id"] == key for line in shop["sells"])
            ]
            with self.subTest(item=key):
                self.assertTrue(shelves, f"{key} is sold nowhere")
                self.assertTrue(any(shop["world"] == world for shop in shelves),
                                f"{key} is only sold outside {world}")


class SectTributeContentTests(unittest.TestCase):
    """v1.0.0-rc.18: a sect's own disciples stock its storehouse.

    `sect_treasury` had one writer, `sect.contribute`, and it needed a player
    holding the goods - so a sect with no player members stocked nothing, ever,
    and `sect_system.resource_policy` ("contribution points can be exchanged for
    stocked sect resources") described nothing that happened. What the disciples
    bring is content, and these are the two things the engine cannot check for
    itself: that the refs resolve on every tier, and that what they resolve to
    is a real item.
    """

    @property
    def tribute(self) -> dict:
        return WORLD["sect_system"]["tribute"]

    def test_the_tribute_roster_is_present_and_sized(self):
        self.assertTrue(self.tribute["materials"], "the disciples bring nothing")
        self.assertGreaterEqual(int(self.tribute["disciples_per_lot"]), 1)
        self.assertGreaterEqual(int(self.tribute["cap"]), 1)

    def test_every_material_resolves_on_every_tier_to_a_real_item(self):
        tiers = WORLD["event_sites"]["tier_materials"]
        self.assertGreaterEqual(len(tiers), 4, "the tier table lost a world")
        for ref in self.tribute["materials"]:
            for world, table in tiers.items():
                with self.subTest(ref=ref, world=world):
                    item = table[ref[1:]] if ref.startswith("@") else ref
                    self.assertTrue(item, f"{ref} resolves to nothing in {world}")
                    self.assertIn(item, WORLD["items"], f"{ref} is not an item in {world}")

    def test_every_sect_that_takes_tribute_stands_in_a_world_with_a_tier(self):
        """A gate in a world the tier table does not cover would stock nothing.

        The hidden sect is the one exception and is excluded by the engine
        rather than by content: its disciples have no counter to hand things
        in at.
        """
        tiers = WORLD["event_sites"]["tier_materials"]
        public = {name: s for name, s in WORLD["sects"].items() if not s.get("hidden")}
        self.assertGreaterEqual(len(public), 12)
        for name, sect in public.items():
            with self.subTest(sect=name):
                gate = str((sect.get("recruitment") or {}).get("location") or "")
                self.assertTrue(gate, f"{name} has no gate to stand at")
                world = WORLD["locations"][gate]["world"]
                self.assertIn(world, tiers, f"{name} stands in {world}, which has no tier")


class NpcContentTests(unittest.TestCase):
    def test_every_npc_has_the_narrator_fields(self):
        for name, npc in WORLD["npcs"].items():
            for field in NARRATOR_NPC_FIELDS:
                with self.subTest(npc=name, field=field):
                    self.assertTrue(str(npc.get(field) or "").strip(), f"{name} lacks {field}")

    def test_the_world_rulers_are_not_one_template(self):
        rulers = [n for n, npc in WORLD["npcs"].items() if str(npc.get("role", "")).startswith("Public ruler of")]
        self.assertEqual(len(rulers), 4)
        for field in ("personality", "speech", "want", "fear", "secret"):
            values = {WORLD["npcs"][n][field] for n in rulers}
            with self.subTest(field=field):
                self.assertEqual(len(values), 4, f"rulers share a {field}")

    def test_steward_qiao_is_ready_to_be_a_giver(self):
        qiao = WORLD["npcs"]["Steward Qiao"]
        self.assertIn("commission", qiao["secret"].lower())
        self.assertEqual(qiao["location"], "Golden Pavilion Auction House")


# --- the professions the engine itself names -------------------------------
#
# Two scanners over `go_core`, so the gates below are answerable from the code
# rather than from a list somebody has to remember to update. Both resolve Go
# string constants, because the grant sites stopped being bare literals when
# rc.19 gave them names.

GO_GAME_ROOT = PROJECT_ROOT / "go_core" / "internal" / "game"


def _profession_go_sources() -> dict:
    return {
        path: path.read_text(encoding="utf-8")
        for path in GO_GAME_ROOT.rglob("*.go")
        if not path.name.endswith("_test.go")
    }


def _go_string_consts(sources: dict) -> dict:
    """Every `name = "value"` constant in the package, for resolving arguments."""
    out = {}
    for text in sources.values():
        for name, value in re.findall(r'(\w+)\s*=\s*"([^"]*)"', text):
            out.setdefault(name, value)
    return out


def _resolve_go_arg(token: str, consts: dict) -> str:
    token = token.strip()
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    return consts.get(token, "")


def engine_granted_professions() -> set:
    """Professions written by a hardcoded `advanceProfessionTx` argument.

    A call passing a variable - the craft path passes the recipe's own
    profession - is deliberately not resolved here. Those are covered by the
    recipe half of the gates, which is already data-driven.
    """
    sources = _profession_go_sources()
    consts = _go_string_consts(sources)
    found = set()
    for text in sources.values():
        for arg in re.findall(r"advanceProfessionTx\(\s*conn\s*,\s*userID\s*,\s*([^,]+),", text):
            name = _resolve_go_arg(arg, consts)
            if name:
                found.add(name)
    return found


def engine_level_readers() -> set:
    """Professions whose *level* the engine reads by name.

    Two shapes: `professionLevelTx(conn, userID, X)`, and any line that reads
    `level` out of `profession_progress` while naming a profession on the same
    line. The craft path reads the level for whatever profession a recipe
    names, generically, so recipe professions are not found here and are
    allowed for separately by the callers.
    """
    sources = _profession_go_sources()
    consts = _go_string_consts(sources)
    found = set()
    for text in sources.values():
        for arg in re.findall(r"professionLevelTx\(\s*conn\s*,\s*userID\s*,\s*([^)]+)\)", text):
            name = _resolve_go_arg(arg, consts)
            if name:
                found.add(name)
        for line in text.splitlines():
            if "profession_progress" not in line or "level" not in line:
                continue
            for quoted, ident in re.findall(r"'([^']+)'|\b([A-Za-z_]\w*Profession)\b", line):
                name = _resolve_go_arg(f'"{quoted}"' if quoted else ident, consts)
                if name:
                    found.add(name)
    return found


class CraftedGoodsHaveAMarketTests(unittest.TestCase):
    """What a profession makes, somebody buys (v1.0.0-rc.19).

    Thirteen crafted goods had no buyer anywhere: nine of the eleven alchemy
    pills, the three armours above tier 1 - while every blade sold - and one
    talisman. Every one of them was already *sold* by shops, so the world
    stocked things it would not take back, and a crafter's whole late career
    produced goods only another player would take.

    The rule is the one the two working pills already followed: a shop that
    sells a thing buys it back, at the quarter of base price every keeper in
    the catalogue already pays.
    """

    def _buyers(self) -> dict:
        buyers = {}
        for shop in WORLD["shops"].values():
            for item_id in (shop.get("buys") or {}):
                buyers[item_id] = buyers.get(item_id, 0) + 1
        return buyers

    def test_every_crafted_output_has_a_buyer(self):
        buyers = self._buyers()
        for name, recipe in WORLD["recipes"].items():
            for output in (recipe.get("output") or {}):
                with self.subTest(recipe=name, item=output):
                    self.assertGreater(buyers.get(output, 0), 0,
                                       f"{output} is crafted by {name} and no shop buys it")

    def test_armour_sells_as_readily_as_blades(self):
        """The sharpest case: every blade had a buyer and no armour above tier 1 did."""
        buyers = self._buyers()
        for armour in ("spirit_iron_armor", "spirit_crystal_mail", "immortal_gold_plate", "starsteel_aegis"):
            with self.subTest(item=armour):
                self.assertGreater(buyers.get(armour, 0), 0, f"{armour} has no buyer")

    def test_a_crafted_good_can_be_sold_in_the_world_it_is_stocked_in(self):
        """The per-world form of the rule, which is the one this world follows.

        Not "every shop buys back what it sells" - a provisioner stocks pills
        and an apothecary is who buys them, which is right. What matters is
        that a crafter is never made to cross worlds to find a counter: a good
        on sale in a world is bought in that same world.
        """
        crafted = {out for r in WORLD["recipes"].values() for out in (r.get("output") or {})}
        sold, bought = {}, {}
        for shop in WORLD["shops"].values():
            for line in shop.get("sells", []):
                if line["item_id"] in crafted:
                    sold.setdefault(line["item_id"], set()).add(shop["world"])
            for item_id in (shop.get("buys") or {}):
                if item_id in crafted:
                    bought.setdefault(item_id, set()).add(shop["world"])
        for item_id in sorted(crafted):
            with self.subTest(item=item_id):
                stranded = sold.get(item_id, set()) - bought.get(item_id, set())
                self.assertEqual(stranded, set(),
                                 f"{item_id} is stocked in {sorted(stranded)} and bought by nobody there")


class DeployableArrayTests(unittest.TestCase):
    """A disk that deploys nothing is the same fault one layer down.

    `array_deploy` on an item is resolved against `deployedArrayDefs`, a map in
    `property_storage_actions.go`. An item carrying the field with no entry
    there is refused at deploy time with "formation disk has no valid
    deployment definition" - so it would craft, sell, and do nothing. rc.19
    added four disks to refill the Formation ladder and this is what holds them
    (and the next four) to being real.
    """

    def _deployment_definitions(self) -> set:
        source = (PROJECT_ROOT / "go_core" / "internal" / "game" / "property_storage_actions.go").read_text(encoding="utf-8")
        block = source[source.index("deployedArrayDefs = map[string]deployedArrayDef{"):]
        block = block[: block.index("\n}\n")]
        return set(re.findall(r'^\s*"([^"]+)":', block, re.MULTILINE))

    def test_every_deployable_item_has_a_definition(self):
        defined = self._deployment_definitions()
        self.assertGreaterEqual(len(defined), 2, f"the definition scanner found only {defined}")
        for item_id, item in WORLD["items"].items():
            ref = str(item.get("array_deploy") or "")
            if not ref:
                continue
            with self.subTest(item=item_id):
                self.assertIn(ref, defined, f"{item_id} deploys {ref}, which no definition covers")

    def test_every_definition_belongs_to_a_real_item(self):
        """And the other direction: a definition nothing can reach is dead code."""
        deployable = {str(i.get("array_deploy")) for i in WORLD["items"].values() if i.get("array_deploy")}
        for ref in self._deployment_definitions():
            with self.subTest(ref=ref):
                self.assertIn(ref, deployable, f"{ref} is defined and no item deploys it")


class FormationLadderTests(unittest.TestCase):
    """Formation makes something at every tier (v1.0.0-rc.19).

    rc.15 moved six talisman recipes out of Formation into Inscription, which
    was right, and left the profession with two recipes at the same TN and
    nothing above the Mortal World - while eight array workshops stood in all
    four worlds with only Mortal goods to trade.
    """

    def _formation(self) -> dict:
        return {k: v for k, v in WORLD["recipes"].items() if v.get("profession") == "Formation"}

    def test_formation_spans_a_real_ladder(self):
        tns = sorted(int(r["tn"]) for r in self._formation().values())
        self.assertGreaterEqual(len(tns), 6, "Formation is thin again")
        self.assertGreaterEqual(tns[-1] - tns[0], 12, f"Formation's TNs barely move: {tns}")

    def test_no_crafting_profession_is_left_far_behind(self):
        """The comparison that found this: one profession with a sixth of another's ladder."""
        spans = {}
        for recipe in WORLD["recipes"].values():
            spans.setdefault(str(recipe["profession"]), []).append(int(recipe["tn"]))
        counts = {p: len(t) for p, t in spans.items()}
        fewest, most = min(counts.values()), max(counts.values())
        self.assertGreaterEqual(fewest * 2, most,
                                f"one profession has less than half another's recipes: {counts}")

    def test_every_tier_material_reaches_a_formation_recipe(self):
        """A ladder that stops at the Mortal World is the fault this replaced."""
        costs = {item for r in self._formation().values() for item in r["cost"]}
        for ore in ("spirit_crystal_ore", "immortal_gold_ore", "starsteel_ore"):
            with self.subTest(material=ore):
                self.assertIn(ore, costs, f"no Formation recipe reaches {ore}")


class TheLearningStepTests(unittest.TestCase):
    """A method is taught and then worked (v1.0.0-rc.20).

    Every recipe used to be craftable by anyone from character creation while
    `/craft`'s description said "from a known recipe" and nothing tracked
    knowledge. Now a household teaches its own craft and everything else is a
    jade slip - which means two new ways to ship something dead: a recipe no
    slip teaches, and a slip no shop sells.
    """

    RECIPES = WORLD["recipes"]
    ITEMS = WORLD["items"]
    SHOPS = WORLD["shops"]
    PROFESSION_SHOP = {"Alchemy": "apothecary", "Forging": "weaponsmith",
                       "Inscription": "talisman", "Formation": "array"}

    def _slips(self) -> dict:
        return {i: v["teaches_recipe"] for i, v in self.ITEMS.items() if v.get("teaches_recipe")}

    def test_every_recipe_has_a_slip_that_teaches_it(self):
        """Including the entry methods: a household teaches only its own craft,
        so another craft's basics have to be buyable or they are unreachable."""
        taught = set(self._slips().values())
        for name in self.RECIPES:
            with self.subTest(recipe=name):
                self.assertIn(name, taught, f"no method slip teaches {name}")

    def test_every_slip_teaches_a_real_recipe(self):
        for slip, recipe in self._slips().items():
            with self.subTest(slip=slip):
                self.assertIn(recipe, self.RECIPES, f"{slip} teaches a method this world does not have")

    def test_every_slip_is_actually_obtainable(self):
        """A slip nobody sells is a recipe nobody can learn."""
        for slip in self._slips():
            sellers = [k for k, shop in self.SHOPS.items()
                       if any(line["item_id"] == slip for line in shop.get("sells", []))]
            with self.subTest(slip=slip):
                self.assertTrue(sellers, f"{slip} is sold nowhere")

    def test_a_slip_is_sold_by_the_trade_that_uses_it(self):
        for slip, recipe in self._slips().items():
            want = self.PROFESSION_SHOP[self.RECIPES[recipe]["profession"]]
            kinds = {shop["kind"] for shop in self.SHOPS.values()
                     if any(line["item_id"] == slip for line in shop.get("sells", []))}
            with self.subTest(slip=slip):
                self.assertIn(want, kinds, f"{slip} is not sold by any {want}")

    def test_every_recipe_states_a_level_its_profession_can_reach(self):
        """advanceProfessionTx caps a profession at 6; a method above that is
        one nobody could ever work."""
        for name, recipe in self.RECIPES.items():
            with self.subTest(recipe=name):
                level = int(recipe.get("min_level", -1))
                self.assertGreaterEqual(level, 0, f"{name} has no min_level")
                self.assertLessEqual(level, 6, f"{name} asks for a level no profession reaches")

    def test_every_craft_keeps_an_entry_method(self):
        """Crafting is the only source of profession experience, so a craft whose
        easiest method is gated behind a level could never be practised into."""
        by_profession = {}
        for recipe in self.RECIPES.values():
            by_profession.setdefault(recipe["profession"], []).append(int(recipe["min_level"]))
        for profession, levels in sorted(by_profession.items()):
            with self.subTest(profession=profession):
                self.assertIn(0, levels, f"{profession} has no method anyone could start on")



class TheHouseholdsTradeTests(unittest.TestCase):
    """What a family teaches, and that it is teachable.

    A household teaches its own craft and nothing else, so an archetype without
    a trade would send its children out knowing nothing at all.
    """

    SENDOFF = WORLD["birth_family_sendoff"]
    PROFESSIONS = {r["profession"] for r in WORLD["recipes"].values()}

    def test_every_household_passes_on_a_craft(self):
        self.assertGreaterEqual(len(self.SENDOFF), 13)
        for archetype, entry in self.SENDOFF.items():
            with self.subTest(archetype=archetype):
                trade = str(entry.get("trade") or "")
                self.assertTrue(trade, f"{archetype} teaches nothing")
                self.assertIn(trade, self.PROFESSIONS, f"{archetype} teaches {trade!r}, which is not a craft")

    def test_the_trades_are_not_all_one_craft(self):
        """Every household teaching Forging would make three crafts unlearnable
        at home, which is the shape of a mapping written without looking."""
        trades = {a: e["trade"] for a, e in self.SENDOFF.items()}
        self.assertGreaterEqual(len(set(trades.values())), 4, f"households cover only {set(trades.values())}")

    def test_every_craft_is_some_households_trade(self):
        covered = {e["trade"] for e in self.SENDOFF.values()}
        for profession in sorted(self.PROFESSIONS):
            with self.subTest(profession=profession):
                self.assertIn(profession, covered, f"no household passes on {profession}")


class ProfessionRosterTests(unittest.TestCase):
    """Every declared profession is granted, read and gifted.

    This is the gate rc.15 should have left behind. "Appraisal" sat in the
    roster for releases with nothing granting it and "Inscription" beside it in
    the same state; rc.15 repaired both and left three checks that could not
    have caught either - one iterating a four-name tuple written inline, one
    asserting three strings are members of a tuple, and one that passes
    vacuously if every talisman is deleted.

    So this iterates `PROFESSIONS` itself and asks the three questions the
    repo's own bar asks: is it granted, does its level change anything, and
    does the content know it exists.
    """

    RECIPE_PROFESSIONS = {str(r["profession"]) for r in WORLD["recipes"].values() if r.get("profession")}

    def test_the_roster_is_the_one_in_the_code(self):
        self.assertEqual(len(PROFESSIONS), 8, "the roster changed size; every test below iterates it")
        self.assertEqual(len(set(PROFESSIONS)), len(PROFESSIONS), "a profession is listed twice")

    def test_every_profession_is_granted_somewhere(self):
        grantable = engine_granted_professions() | self.RECIPE_PROFESSIONS
        for profession in PROFESSIONS:
            with self.subTest(profession=profession):
                self.assertIn(profession, grantable,
                              f"{profession} is declared and nothing can grant it")

    def test_every_profession_level_changes_something(self):
        """A level nobody reads is a vanity counter.

        "Beast Taming" and "Artifact Refining" were written by six call sites
        and read by none until rc.19: every read went into the response payload
        for display, so a Grandmaster tamed no better than a first-timer. That
        is the `authenticity` / `tracking_strength` fault shape, and this is
        what stops the next one.
        """
        readers = engine_level_readers() | self.RECIPE_PROFESSIONS
        for profession in PROFESSIONS:
            with self.subTest(profession=profession):
                self.assertIn(profession, readers,
                              f"{profession} accumulates a level no rule reads")

    def test_every_profession_has_a_patron_gift(self):
        gift = WORLD["patron_gift"]["by_profession"]
        for profession in PROFESSIONS:
            with self.subTest(profession=profession):
                self.assertIn(profession, gift, f"{profession} has no patron gift")

    def test_the_scanners_still_resolve_both_argument_shapes(self):
        """A scanner that matches nothing passes forever.

        Both grant shapes must still resolve: a bare literal ("Foraging", in
        crafting_actions.go) and a named constant (the rest, since rc.19). If a
        refactor renamed the helper or changed the call shape these gates would
        go quiet rather than fail, so this asserts they are still finding things.
        """
        granted = engine_granted_professions()
        self.assertIn("Foraging", granted, "the literal-argument shape stopped resolving")
        self.assertIn("Appraisal", granted, "the named-constant shape stopped resolving")
        self.assertGreaterEqual(len(granted), 4, f"the grant scanner found only {granted}")
        readers = engine_level_readers()
        self.assertIn("Beast Taming", readers, "the professionLevelTx shape stopped resolving")
        self.assertIn("Foraging", readers, "the inline-SQL shape stopped resolving")


if __name__ == "__main__":
    unittest.main()


class TravellingMerchantContentTests(unittest.TestCase):
    """v0.34.1: a merchant is content - a loop of cities in one world, a
    purse in that world's low currency, and an NPC that is its face."""

    def test_every_merchant_walks_real_cities_of_one_world_and_has_a_face(self):
        merchants = WORLD["merchants"]
        self.assertGreaterEqual(len(merchants), 8)
        locations = WORLD["locations"]
        faces = {str(npc.get("merchant")): name for name, npc in WORLD["npcs"].items() if npc.get("merchant")}
        for key, m in merchants.items():
            with self.subTest(merchant=key):
                self.assertIn(m["home"], m["route"], "home must be a route stop")
                self.assertGreaterEqual(len(m["route"]), 3)
                self.assertEqual(len(set(m["route"])), len(m["route"]), "a loop visits each stop once")
                for stop in m["route"]:
                    self.assertIn(stop, locations)
                    self.assertEqual(locations[stop]["world"], m["world"])
                    self.assertFalse(locations[stop].get("private"))
                self.assertGreater(int(m["budget"]), 0)
                self.assertGreaterEqual(int(m["markup_percent"]), 100)
                self.assertGreater(int(m["dwell_minutes"]), 0)
                self.assertIn(m["currency"], WORLD["currencies"])
                self.assertEqual(WORLD["currencies"][m["currency"]]["world"], m["world"])
                self.assertIn(key, faces, "no NPC fronts this merchant")
                self.assertEqual(WORLD["npcs"][faces[key]]["location"], m["home"])

    def test_every_merchant_keeps_a_shop_of_ordinary_tradeable_goods(self):
        # v0.34.2: the shop is content - real items, market-tradeable, not
        # auction-grade, each with a quantity and a price in the merchant's currency.
        items = WORLD["items"]
        for key, m in WORLD["merchants"].items():
            with self.subTest(merchant=key):
                wares = m.get("wares") or []
                self.assertGreaterEqual(len(wares), 3, "a shop needs a few lines")
                self.assertEqual(len({w["item_id"] for w in wares}), len(wares), "one line per item")
                for ware in wares:
                    item = items[ware["item_id"]]
                    self.assertFalse(item.get("market_excluded"), ware["item_id"])
                    self.assertFalse(item.get("auction_interest"), f"{ware['item_id']} is auction-grade, not shop stock")
                    self.assertNotEqual(item.get("type"), "manual", ware["item_id"])
                    self.assertGreater(int(ware["quantity"]), 0)
                    self.assertGreater(int(ware["price"]), 0)

    def test_every_world_with_an_auction_house_has_a_merchant_on_its_floors(self):
        houses = WORLD["auction_houses"]
        locations = WORLD["locations"]
        route_stops = {m["world"]: set() for m in WORLD["merchants"].values()}
        for m in WORLD["merchants"].values():
            route_stops[m["world"]].update(m["route"])
        for house_id, house in houses.items():
            city = house["entrance_location"]
            world = locations[city]["world"]
            with self.subTest(house=house_id):
                self.assertIn(world, route_stops, f"{world} has no merchant at all")
        # Every grand house (a capital) lies on at least one merchant loop, so
        # an unsold lot in a capital always has a buyer with a route there.
        for house_id, house in houses.items():
            if house.get("size") != "grand":
                continue
            city = house["entrance_location"]
            with self.subTest(grand_house=house_id):
                self.assertIn(city, route_stops[locations[city]["world"]], f"no merchant passes {city}")


class CityShopContentTests(unittest.TestCase):
    """v0.35.0: every city has shops; a shop is an interior location with a
    keeper, a shelf of real tradeable goods and a board of what it buys,
    and the cities differ - in kind, in tier by world, in stock."""

    def test_every_city_has_shops_and_capitals_have_four(self):
        shops = WORLD["shops"]
        cities = {h["entrance_location"] for h in WORLD["auction_houses"].values()}
        by_city = {}
        for key, shop in shops.items():
            by_city.setdefault(shop["city"], []).append(key)
        for city in cities:
            with self.subTest(city=city):
                self.assertIn(city, by_city, f"{city} has no shops")
                want = 4 if WORLD["locations"][city].get("realm_hub") else 2
                self.assertGreaterEqual(len(by_city[city]), want)
                kinds = [shops[k]["kind"] for k in by_city[city]]
                self.assertEqual(len(set(kinds)), len(kinds), "two shops of one kind in one city")

    def test_every_shop_is_a_kept_interior_with_a_real_shelf(self):
        items, locations, npcs = WORLD["items"], WORLD["locations"], WORLD["npcs"]
        tier_of = {"Mortal World": 1, "Spiritual World": 2, "Immortal World": 3, "Celestial World": 4}
        for key, shop in WORLD["shops"].items():
            with self.subTest(shop=key):
                interior = locations[shop["location"]]
                self.assertEqual(interior.get("shop"), key)
                if shop["kind"] == "waystation":
                    # A waystation's stall (v0.39.0) is the waystation itself:
                    # no door, no city, the road either side.
                    self.assertEqual(shop["location"], shop["city"])
                    self.assertEqual(interior.get("road_site"), "waystation")
                else:
                    self.assertEqual(interior.get("outside_location"), shop["city"])
                self.assertTrue(interior.get("safe_zone"), "a shop is a protected interior")
                self.assertEqual(interior["world"], shop["world"])
                # v0.36.0: a capital's shops are a tier better and dearer.
                capital = bool(WORLD["locations"][shop["city"]].get("realm_hub"))
                self.assertEqual(shop["tier"], tier_of[shop["world"]] + (1 if capital else 0))
                self.assertEqual(npcs[shop["keeper"]]["location"], shop["location"])
                self.assertEqual(npcs[shop["keeper"]].get("shop"), key)
                self.assertGreaterEqual(len(shop["sells"]), 2)
                self.assertEqual(len({line["item_id"] for line in shop["sells"]}), len(shop["sells"]))
                for line in shop["sells"]:
                    item = items[line["item_id"]]
                    self.assertFalse(item.get("market_excluded"), line["item_id"])
                    self.assertFalse(item.get("auction_interest"), f"{line['item_id']} is auction-grade, not shelf stock")
                    self.assertGreater(int(line["quantity"]), 0)
                    self.assertGreater(int(line["price"]), 0)
                self.assertGreaterEqual(len(shop["buys"]), 2)
                for item_id, price in shop["buys"].items():
                    self.assertIn(item_id, items)
                    self.assertGreater(int(price), 0)
                self.assertIn(shop["currency"], WORLD["currencies"])
                self.assertEqual(WORLD["currencies"][shop["currency"]]["world"], shop["world"])
                self.assertGreater(int(shop["restock_minutes"]), 0)

    def test_a_smithy_makes_its_own_blades(self):
        # "the products they make": a made-here line is the keeper's craft,
        # and every weaponsmith and apothecary has at least one.
        for key, shop in WORLD["shops"].items():
            if shop["kind"] not in ("weaponsmith", "apothecary", "talisman", "array"):
                continue
            with self.subTest(shop=key):
                self.assertTrue(any(line.get("made_here") for line in shop["sells"]), f"{key} makes nothing")


class CityDistrictContentTests(unittest.TestCase):
    """v0.36.0: every walled city has a gate per compass side that has a
    road, both ends of a road agree on the compass, the capitals have four
    compass districts and the other cities one, and every part has people."""

    OPPOSITE = {"North": "South", "South": "North", "East": "West", "West": "East"}

    def test_every_road_has_a_gate_on_each_end_and_the_compass_agrees(self):
        locations = WORLD["locations"]
        cities = {h["entrance_location"] for h in WORLD["auction_houses"].values()}
        for city in sorted(cities):
            loc = locations[city]
            roads = sorted(loc.get("roads") or [])
            gates = loc.get("gates") or {}
            with self.subTest(city=city):
                if not roads:
                    self.assertEqual(gates, {}, f"{city} has gates but no roads")
                    continue
                faced = sorted(n for names in gates.values() for n in names)
                self.assertEqual(faced, roads, "every road neighbour is faced by exactly one gate")
                for direction, names in gates.items():
                    gate_name = f"{city} {direction} Gate"
                    self.assertIn(gate_name, locations, f"{city} lacks its {direction} gate location")
                    gate = locations[gate_name]
                    self.assertEqual(gate.get("district"), "gate")
                    self.assertEqual(gate.get("gate"), direction)
                    self.assertEqual(gate.get("outside_location"), city)
                    self.assertTrue(gate.get("safe_zone"), "a gate is guarded")
                    for neighbour in names:
                        back = locations[neighbour].get("gates") or {}
                        self.assertIn(city, back.get(self.OPPOSITE[direction], []), f"{neighbour} should face {city} by its {self.OPPOSITE[direction]} gate")

    def test_capitals_have_four_compass_districts_and_cities_one(self):
        locations = WORLD["locations"]
        cities = {h["entrance_location"] for h in WORLD["auction_houses"].values()}
        for city in sorted(cities):
            districts = [n for n, l in locations.items() if l.get("district") not in (None, "", "gate", "inn") and l.get("outside_location") == city]
            with self.subTest(city=city):
                if locations[city].get("realm_hub"):
                    self.assertEqual(len(districts), 4, districts)
                    self.assertEqual({n.split()[-2] for n in districts}, {"North", "East", "South", "West"})
                elif city == "Greenriver Town":
                    self.assertEqual(districts, [], "a town is one place")
                else:
                    self.assertEqual(len(districts), 1, districts)

    def test_every_district_has_its_own_people(self):
        homes = {}
        for name, npc in WORLD["npcs"].items():
            homes.setdefault(npc["location"], []).append(name)
        for name, loc in WORLD["locations"].items():
            if not loc.get("district"):
                continue
            with self.subTest(district=name):
                want = 1 if loc["district"] in ("gate", "inn") else 2
                self.assertGreaterEqual(len(homes.get(name, [])), want, f"{name} is empty")
                for npc in homes.get(name, []):
                    self.assertEqual(WORLD["npcs"][npc].get("district"), name)


class CityLifeContentTests(unittest.TestCase):
    """v0.38.0: every city has an inn with a keeper, and a board of
    commissions given by people who live in the city's parts - the
    capitals' quest pavilion, every other city's gate notice."""

    def test_every_city_has_an_inn_with_a_keeper(self):
        locations, npcs = WORLD["locations"], WORLD["npcs"]
        cities = {h["entrance_location"] for h in WORLD["auction_houses"].values()}
        homes = {}
        for name, npc in npcs.items():
            homes.setdefault(npc["location"], []).append(name)
        for city in sorted(cities):
            inns = [n for n, l in locations.items() if l.get("district") == "inn" and l.get("outside_location") == city]
            with self.subTest(city=city):
                self.assertEqual(len(inns), 1, inns)
                self.assertTrue(locations[inns[0]].get("safe_zone"))
                self.assertTrue(any(n.startswith(("Landlord", "Landlady")) for n in homes.get(inns[0], [])), f"{inns[0]} has no keeper")

    def test_every_city_has_a_board_and_the_capitals_a_pavilion(self):
        locations, npcs = WORLD["locations"], WORLD["npcs"]
        givers = WORLD["commission_givers"]
        cities = {h["entrance_location"] for h in WORLD["auction_houses"].values()}

        def city_of(location):
            loc = locations.get(location) or {}
            return str(loc.get("outside_location")) if loc.get("district") or loc.get("shop") or loc.get("auction_house") else location

        by_city = {}
        for c in WORLD["commissions"]:
            if not str(c["quest_key"]).startswith("commission_city_"):
                continue
            giver = c["giver_npc"]
            self.assertIn(giver, npcs, giver)
            self.assertIn(giver, givers, f"{giver} gives a commission but is not a giver")
            self.assertEqual(givers[giver]["location"], npcs[giver]["location"])
            by_city.setdefault(city_of(npcs[giver]["location"]), []).append(c)
            for objective in c["objectives"]:
                self.assertIn(objective["type"], ("explore", "talk", "scene_action"))
                if objective["type"] == "explore":
                    self.assertIn(objective["target"], locations)
                if objective["type"] == "talk":
                    self.assertIn(objective["target"], npcs)
        for city in sorted(cities):
            with self.subTest(city=city):
                want = 4 if locations[city].get("realm_hub") else (1 if city == "Greenriver Town" else 2)
                self.assertGreaterEqual(len(by_city.get(city, [])), want, f"{city} board is thin")
                self.assertEqual(len({c["quest_key"] for c in by_city.get(city, [])}), len(by_city.get(city, [])))


class PatronGiftContentTests(unittest.TestCase):
    """The gift a patron leaves a cultivator for supporting the server.

    The roster is content rather than a Go switch, which is the point: a new
    path or profession should be a content edit. That only holds while the
    content actually covers every path and every profession the engine can
    write, so this is the check that makes the claim true.
    """

    # Derived from the Go sources rather than typed out (v1.0.0-rc.19). This
    # was a hand-written set of three, its comment miscounting both halves -
    # recipes name four professions, not three, and the engine hardcodes four,
    # not three. "Appraisal" was the fourth, added by rc.15 and never added
    # here, so this gate - the one whose docstring promises to cover "every
    # profession the engine can write" - could not see that Appraisal had no
    # patron gift at all. A set read off the code cannot drift from it.
    ENGINE_PROFESSIONS = engine_granted_professions()
    MATERIAL_REFS = {"@herb", "@ore", "@core"}

    def test_every_cultivation_path_has_a_gift(self):
        gift = WORLD["patron_gift"]
        for path in WORLD["paths"]:
            with self.subTest(path=path):
                self.assertIn(path, gift["by_path"], f"{path} has no patron gift")

    def test_every_profession_the_engine_can_write_has_a_gift(self):
        gift = WORLD["patron_gift"]
        professions = {str(r["profession"]) for r in WORLD["recipes"].values() if r.get("profession")}
        for profession in sorted(professions | self.ENGINE_PROFESSIONS):
            with self.subTest(profession=profession):
                self.assertIn(profession, gift["by_profession"])

    def test_every_gift_resolves_to_a_real_item_in_every_world(self):
        gift = WORLD["patron_gift"]
        tiers = WORLD["event_sites"]["tier_materials"]
        refs = set(gift["by_path"].values()) | set(gift["by_profession"].values()) | {gift["default"]}
        self.assertTrue(refs)
        for ref in sorted(refs):
            with self.subTest(ref=ref):
                if ref not in self.MATERIAL_REFS:
                    # A literal item id is allowed, and must exist.
                    self.assertIn(ref, WORLD["items"], f"{ref} is neither a tier material nor an item")
                    continue
                for world, materials in tiers.items():
                    item = materials.get(ref[1:])
                    self.assertTrue(item, f"{ref} is not written for {world}")
                    self.assertIn(item, WORLD["items"], f"{ref} in {world} is not a real item")

    def test_an_untiered_material_is_one_the_tier_table_really_does_not_tier(self):
        # The list exists so a material that is the same item in every world
        # can be scaled by number instead. An entry that *does* tier would be
        # paid twice over.
        gift = WORLD["patron_gift"]
        tiers = WORLD["event_sites"]["tier_materials"]
        for ref in gift.get("untiered", []):
            with self.subTest(ref=ref):
                self.assertIn(ref, self.MATERIAL_REFS)
                items = {materials.get(ref[1:]) for materials in tiers.values()}
                self.assertEqual(len(items), 1, f"{ref} names {items} - it tiers, so it must not be listed as untiered")
        # And the other way: a ref that is the same everywhere must be listed,
        # or a Celestial cultivator is quietly given a Mortal World gift.
        for ref in self.MATERIAL_REFS:
            with self.subTest(ref=ref):
                items = {materials.get(ref[1:]) for materials in tiers.values()}
                if len(items) == 1:
                    self.assertIn(ref, gift.get("untiered", []), f"{ref} is the same item in every world but is not listed as untiered")

    def test_the_default_is_a_material_every_world_carries(self):
        # A path or profession nobody wrote still has to pay something, so the
        # default cannot be a literal item that only one world trades in.
        self.assertIn(WORLD["patron_gift"]["default"], self.MATERIAL_REFS)


class RoadSideSiteContentTests(unittest.TestCase):
    """v0.39.0: a place on every road between two cities."""

    KINDS = ("waystation", "hunting_ground", "ruin", "shrine")

    def _sites(self):
        return {name: loc for name, loc in WORLD["locations"].items() if loc.get("road_site")}

    def test_every_road_carries_exactly_one_site_of_a_known_kind(self):
        locations = WORLD["locations"]
        edges = {tuple(sorted((name, road))) for name, loc in locations.items() if not loc.get("road_site") for road in loc.get("roads", [])}
        by_leg = {}
        for name, loc in self._sites().items():
            with self.subTest(site=name):
                self.assertIn(loc["road_site"], self.KINDS)
                leg = tuple(sorted(str(x) for x in loc.get("road_leg") or []))
                self.assertEqual(len(leg), 2, "a site lies on one road between two cities")
                self.assertIn(leg, edges, f"{name} lies on no road")
                self.assertEqual({locations[leg[0]]["world"], locations[leg[1]]["world"]}, {loc["world"]})
                self.assertEqual(int(loc.get("min_realm_index") or 0), max(int(locations[x].get("min_realm_index") or 0) for x in leg))
                self.assertFalse(loc.get("roads"), "a site is reached by its leg, not by roads of its own")
                self.assertFalse(loc.get("outside_location") or loc.get("district") or loc.get("auction_house"))
                by_leg.setdefault(leg, []).append(name)
        for edge in edges:
            with self.subTest(road=edge):
                self.assertEqual(len(by_leg.get(edge, [])), 1, f"the {edge[0]}-{edge[1]} road should carry one site")

    def test_each_world_has_every_kind_and_the_safe_ones_are_safe(self):
        by_world = {}
        for name, loc in self._sites().items():
            by_world.setdefault(loc["world"], set()).add(loc["road_site"])
            with self.subTest(site=name):
                self.assertEqual(bool(loc.get("safe_zone")), loc["road_site"] in ("waystation", "shrine"))
        for world, kinds in by_world.items():
            with self.subTest(world=world):
                self.assertEqual(kinds, set(self.KINDS))

    def test_a_waystation_keeps_a_stall_and_the_others_do_not(self):
        shops, npcs = WORLD["shops"], WORLD["npcs"]
        for name, loc in self._sites().items():
            with self.subTest(site=name):
                if loc["road_site"] != "waystation":
                    self.assertFalse(loc.get("shop"))
                    continue
                shop = shops[loc["shop"]]
                self.assertEqual(shop["kind"], "waystation")
                self.assertEqual(shop["location"], name)
                self.assertEqual(shop["city"], name)
                self.assertEqual(npcs[shop["keeper"]]["location"], name)
                self.assertGreaterEqual(len(shop["sells"]), 3)
                self.assertGreaterEqual(len(shop["buys"]), 2)


"""What is actually inside a world event.

Reported: the event system "is just empty ... there are no beasts, no herbs, no
mines, no quests".

It was a fair reading of the code. An event's action menu rolled 2d10 and moved
four integers (contribution, investigation, support, interference); the only
reward in an entire scene was a single first-participation claim, taken once and
never again; "Gather Resources" granted no item at any point; and the Battle
button fought a severity-scaled "hostile manifestation" with no identity and no
loot. The scene panel itself showed four fields of text explaining what its own
buttons did - not the event's description, not the stakes, not one thing a
player could point at.

Schema 42 gives every event a *site*: a finite roster of beasts, herb and ore
nodes, relics and tasks spawned from the category's template in
content/world.json, depleting as players work it. These tests hold the content
honest, the tier materials real, and the panel pointed at the site rather than
at itself.
"""

import json
import unittest

from tests.support import PROJECT_ROOT, bot_class_source, bot_package_source

WORLD_JSON = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
SITES = WORLD_JSON.get("event_sites") or {}
ITEMS = WORLD_JSON.get("items") or {}
WORLD_EVENTS = [e for e in WORLD_JSON.get("unexpected_events", []) if e.get("kind") == "world_event"]
NODE_TYPES = {"beast", "herb", "ore", "relic", "task"}


class EventSiteContentTests(unittest.TestCase):
    def test_the_content_carries_a_site_section_with_a_default(self):
        self.assertTrue(SITES, "content/world.json has no event_sites section")
        self.assertTrue(SITES.get("categories"), "no event-site categories")
        self.assertTrue((SITES.get("default") or {}).get("nodes"), "no default roster to fall back on")

    def test_every_world_event_category_has_a_roster(self):
        # An event whose category has no template still gets the default one,
        # so this is about deliberate coverage rather than about crashing.
        categories = set(SITES.get("categories") or {})
        missing = sorted({str(e.get("category")) for e in WORLD_EVENTS} - categories)
        self.assertEqual(missing, [], f"world-event categories with no site of their own: {missing}")

    def test_no_roster_is_empty_and_every_node_is_well_formed(self):
        templates = dict(SITES.get("categories") or {})
        templates["default"] = SITES.get("default") or {}
        for name, template in templates.items():
            with self.subTest(category=name):
                nodes = list(template.get("nodes") or [])
                self.assertGreaterEqual(len(nodes), 2, f"{name} has {len(nodes)} nodes")
                self.assertTrue(str(template.get("objective") or "").strip(), f"{name} has no objective")
                keys = [n.get("key") for n in nodes]
                self.assertEqual(len(keys), len(set(keys)), f"{name} has duplicate node keys")
                for node in nodes:
                    self.assertIn(node.get("type"), NODE_TYPES, f"{name}/{node.get('key')}")
                    self.assertTrue(str(node.get("name") or "").strip())
                    count = list(node.get("count") or [])
                    self.assertEqual(len(count), 2, f"{name}/{node.get('key')} count is not [min,max]")
                    self.assertGreaterEqual(count[0], 1)
                    self.assertGreaterEqual(count[1], count[0])
                    self.assertGreater(int(node.get("tn") or 0), 0)
                    self.assertIn(node.get("attribute"), {"body", "insight", "heart", "spirit"})

    def test_every_node_pays_something(self):
        """A node that hands back nothing is the empty room again, one level down."""
        templates = list((SITES.get("categories") or {}).values()) + [SITES.get("default") or {}]
        for template in templates:
            for node in template.get("nodes") or []:
                with self.subTest(node=node.get("key")):
                    payout = (
                        int(node.get("cultivation") or 0)
                        + int(node.get("spirit_stones") or 0)
                        + (int(node.get("item_qty") or 0) if node.get("item") else 0)
                    )
                    self.assertGreater(payout, 0, f"{node.get('key')} pays nothing")

    def test_every_tier_material_is_a_real_item(self):
        """@herb/@ore/@core resolve per world tier; a typo there would grant a
        phantom item, so every mapping is checked against the item catalogue."""
        tiers = SITES.get("tier_materials") or {}
        self.assertTrue(tiers, "no tier materials")
        worlds = {v.get("world") for v in (WORLD_JSON.get("locations") or {}).values()}
        self.assertEqual(set(tiers) & worlds, set(tiers), "a tier material names a world that does not exist")
        for world, materials in tiers.items():
            for ref, item_id in materials.items():
                with self.subTest(world=world, ref=ref):
                    self.assertIn(item_id, ITEMS, f"{world}/@{ref} -> {item_id} is not in the item catalogue")

    def test_every_symbolic_item_reference_is_one_the_tiers_define(self):
        refs = {r for materials in (SITES.get("tier_materials") or {}).values() for r in materials}
        templates = list((SITES.get("categories") or {}).values()) + [SITES.get("default") or {}]
        for template in templates:
            for node in template.get("nodes") or []:
                item = str(node.get("item") or "")
                if item.startswith("@"):
                    with self.subTest(node=node.get("key")):
                        self.assertIn(item[1:], refs, f"{node.get('key')} wants {item}, which no tier defines")
                elif item:
                    with self.subTest(node=node.get("key")):
                        self.assertIn(item, ITEMS, f"{node.get('key')} wants a literal item that does not exist")

    def test_the_beast_categories_actually_field_beasts(self):
        """'No beasts' was the complaint; a beast tide with no beast node would
        be the same bug wearing the new schema."""
        for category in ("Beast Tide", "Demon Invasion", "Demon Incident"):
            with self.subTest(category=category):
                nodes = ((SITES.get("categories") or {}).get(category) or {}).get("nodes") or []
                self.assertTrue([n for n in nodes if n.get("type") == "beast"], f"{category} fields no beasts")

    def test_harvestable_material_exists_across_the_rosters(self):
        """'No herbs, no mines' - so herb and ore nodes have to exist somewhere."""
        templates = list((SITES.get("categories") or {}).values()) + [SITES.get("default") or {}]
        types = {n.get("type") for t in templates for n in (t.get("nodes") or [])}
        for wanted in ("herb", "ore", "task"):
            self.assertIn(wanted, types, f"no {wanted} node in any roster")


class EventCastContentTests(unittest.TestCase):
    """Reported after the site landed: "the needed npc are also missing".

    The site gave an event beasts, herbs, ore and tasks, but nobody to report
    to, ask, or blame - and the panel's own Talk button answered "No named
    persistent NPC is mechanically present here right now" whenever the region
    had wandered empty. Every category now brings its own cast.
    """

    def _templates(self):
        return list((SITES.get("categories") or {}).values()) + [SITES.get("default") or {}]

    def test_every_category_brings_people(self):
        templates = dict(SITES.get("categories") or {})
        templates["default"] = SITES.get("default") or {}
        for name, template in templates.items():
            with self.subTest(category=name):
                cast = list(template.get("npcs") or [])
                self.assertGreaterEqual(len(cast), 1, f"{name} brings nobody")
                keys = [p.get("key") for p in cast]
                self.assertEqual(len(keys), len(set(keys)), f"{name} has duplicate cast keys")

    def test_every_cast_member_is_worth_talking_to(self):
        """Role, manner and a stated want are what reach the narrator; without
        them a cast member renders as an anonymous local cultivator."""
        for template in self._templates():
            for person in template.get("npcs") or []:
                with self.subTest(npc=person.get("key")):
                    for field in ("title", "role", "personality", "speech", "want", "descriptor"):
                        self.assertTrue(str(person.get(field) or "").strip(), f"{person.get('key')} has no {field}")

    def test_there_is_a_name_pool_big_enough_to_go_around(self):
        pool = list(SITES.get("name_pool") or [])
        self.assertGreaterEqual(len(pool), 12, "too few names to keep two live events apart")
        self.assertEqual(len(pool), len(set(pool)), "duplicate names in the pool")
        biggest = max(len(t.get("npcs") or []) for t in self._templates())
        self.assertGreater(len(pool), biggest * 2, "the pool cannot name two concurrent events")


class WorldHelperTests(unittest.TestCase):
    def test_the_objective_lookup_falls_back_rather_than_returning_nothing(self):
        from app.rules.game import World

        world = World(PROJECT_ROOT / "content" / "world.json")
        self.assertTrue(world.event_site_objective("Beast Tide"))
        # An unwritten category still reads as something to do.
        self.assertTrue(world.event_site_objective("Category Nobody Wrote"))


class EventPanelTests(unittest.TestCase):
    """The panel used to describe its own buttons. It has to describe the event."""

    def setUp(self):
        self.view = bot_class_source("EventSceneView")

    def test_the_panel_renders_the_site_and_the_objective(self):
        self.assertIn("async def render(", self.view)
        self.assertIn("_site_state", self.view)
        self.assertIn("list_world_event_nodes", self.view)
        self.assertIn("world_event_site_progress", self.view)
        self.assertIn("event_site_objective", self.view)

    def test_the_panel_shows_the_event_description(self):
        # Persisted on the event now, with the catalogue as the fallback for
        # events spawned before the description was stored.
        self.assertIn('payload.get("description")', self.view)
        # A property, not a call: `World.unexpected_events` returns the list,
        # and calling it raised on every event site read (rc.35).
        self.assertIn("for candidate in WORLD.unexpected_events:", self.view)

    def test_the_panel_no_longer_leads_with_chrome(self):
        for chrome in (
            "**Systems** exposes real mechanics available here",
            "**Participants** shows players and mechanically present NPCs",
        ):
            self.assertNotIn(chrome, self.view, "the panel still explains its own buttons instead of the event")

    def test_the_site_menu_is_rebuilt_from_live_state(self):
        source = bot_package_source()
        self.assertIn("class EventSiteSelect", source)
        self.assertIn("_refresh_site_select", self.view)
        self.assertIn("world_event.engage", self.view)

    def test_an_event_battle_names_a_real_beast_from_the_roster(self):
        self.assertIn("_next_beast", self.view)
        # The node key rides the combat source so the kill takes that beast off
        # the site and pays what it carries.
        self.assertIn("|node:", self.view)


class EventCastWiringTests(unittest.TestCase):
    def test_the_engine_spawns_the_cast_with_the_site(self):
        go = (PROJECT_ROOT / "go_core" / "internal" / "game" / "world_event_sites.go").read_text(encoding="utf-8")
        self.assertIn("func spawnWorldEventCastTx(", go)
        self.assertIn("spawnWorldEventCastTx(conn, catalog", go)
        # The name has to identify exactly one person across live events.
        self.assertIn("SELECT 1 FROM world_event_npcs WHERE name=?", go)

    def test_a_cast_member_answers_the_ordinary_npc_lookup(self):
        core = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
        self.assertIn("async def get_event_npc_definition(", core)
        self.assertIn("return await self.get_event_npc_definition(name)", core)
        # Only while the event is running - that is why the rows need no cleanup.
        self.assertIn("e.active=1 AND e.ends_at>?", core)

    def test_the_cast_is_not_added_to_the_permanent_npc_catalogue(self):
        """An eight-hour militia captain must not be aged, married and buried
        by the NPC life simulation."""
        go = (PROJECT_ROOT / "go_core" / "internal" / "game" / "world_event_sites.go").read_text(encoding="utf-8")
        self.assertNotIn("catalog_npcs", go)
        self.assertNotIn("npc_civilization_state", go)

    def test_the_narrator_gives_a_cast_member_their_character(self):
        context = (PROJECT_ROOT / "app" / "ai" / "narrator_context.py").read_text(encoding="utf-8")
        self.assertIn("async def _public_npc(", context)
        self.assertIn("get_event_npc_definition", context)
        self.assertNotIn('(getattr(self.world, "npcs", {}) or {}).get(effective_focus_npc, {}) or {}', context)

    def test_the_slash_command_can_find_a_cast_member(self):
        """The cast are not in the permanent catalogue, so the ordinary NPC
        autocomplete would never turn them up."""
        locations = (PROJECT_ROOT / "app" / "bot" / "locations.py").read_text(encoding="utf-8")
        self.assertIn("list_active_event_npcs", locations)

    def test_the_panel_offers_the_cast(self):
        view = bot_class_source("EventSceneView")
        self.assertIn("_event_cast", view)
        self.assertIn("list_world_event_npcs", view)
        self.assertIn("Here for this event", view)
        self.assertIn("Who is here", view)


class EngineWiringTests(unittest.TestCase):
    def test_the_engine_owns_spawning_and_engagement(self):
        go = (PROJECT_ROOT / "go_core" / "internal" / "game" / "world_event_sites.go").read_text(encoding="utf-8")
        self.assertIn("func SpawnWorldEventNodes(", go)
        self.assertIn("func worldEventEngageAction(", go)
        # remaining>0 in the UPDATE is what stops two players taking the last one.
        self.assertIn("AND remaining>0", go)

    def test_the_operation_is_registered_as_authoritative(self):
        authoritative = (PROJECT_ROOT / "go_core" / "internal" / "game" / "authoritative.go").read_text(encoding="utf-8")
        self.assertIn('"world_event.engage"', authoritative)
        self.assertIn("worldEventEngageAction(conn, catalog", authoritative)

    def test_every_world_event_spawn_path_fills_the_site(self):
        """An event that reaches a player empty is the original bug."""
        exploration = (PROJECT_ROOT / "go_core" / "internal" / "game" / "exploration_actions.go").read_text(encoding="utf-8")
        simulation = (PROJECT_ROOT / "go_core" / "internal" / "simulation" / "world.go").read_text(encoding="utf-8")
        self.assertIn("SpawnWorldEventNodes(", exploration)
        self.assertIn("game.SpawnWorldEventNodes(", simulation)
        # The description is persisted at both spawn sites so the panel can show it.
        self.assertIn('"description": event.Description', exploration)
        self.assertIn('"description": pick.event.Description', simulation)

    def test_a_beast_kill_depletes_the_roster(self):
        combat = (PROJECT_ROOT / "go_core" / "internal" / "game" / "combat_actions.go").read_text(encoding="utf-8")
        self.assertIn("depleteWorldEventNodeTx(", combat)
        self.assertIn('"|node:"', combat)


if __name__ == "__main__":
    unittest.main()

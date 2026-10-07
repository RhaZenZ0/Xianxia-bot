"""A secret realm's scene has something in it (v1.31.1).

Reported from play: the Stygian Lantern Tomb, opened by the rotation, showed
"Event Actions" and a stance menu and nothing to fight, pick or carry out.
All four doors that open a realm wrote the event and spawned no site. The
engine now spawns every realm's threshold from one template; this file holds
the content and the panel to the same word for it.
"""

import json
import re
import unittest

from tests.support import PROJECT_ROOT

WORLD_JSON = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
GO_SITES = (PROJECT_ROOT / "go_core" / "internal" / "game" / "world_event_sites.go").read_text(encoding="utf-8")


class ASecretRealmHasASiteTests(unittest.TestCase):
    def test_the_python_word_is_the_engines(self):
        from app.rules.game import SECRET_REALM_SITE_CATEGORY

        match = re.search(r'const SecretRealmSiteCategory = "([^"]+)"', GO_SITES)
        self.assertIsNotNone(match, "the engine no longer states SecretRealmSiteCategory; the reader is broken")
        self.assertEqual(SECRET_REALM_SITE_CATEGORY, match.group(1))

    def test_the_content_carries_the_template_with_work_in_it(self):
        from app.rules.game import SECRET_REALM_SITE_CATEGORY

        template = WORLD_JSON["event_sites"]["categories"].get(SECRET_REALM_SITE_CATEGORY) or {}
        types = {n.get("type") for n in template.get("nodes") or []}
        # Without its own template a realm would get the default "disturbance"
        # roster, which is something to do but not a realm's threshold.
        self.assertIn("task", types, "a realm's threshold has no task")
        self.assertTrue(template.get("npcs"), "nobody stands at a realm's threshold")

    def test_the_panel_asks_the_realms_objective(self):
        from app.rules.game import World

        world = World(PROJECT_ROOT / "content" / "world.json")
        realm = WORLD_JSON["event_sites"]["categories"]["Secret Realm"]["objective"]
        # The rotation files its realms under "Rotation", which has no template.
        self.assertEqual(world.event_site_objective("Rotation", "secret_realm"), realm)
        self.assertNotEqual(world.event_site_objective("Rotation"), realm)
        source = (PROJECT_ROOT / "app" / "bot" / "ui" / "event_scene.py").read_text(encoding="utf-8")
        self.assertIn("event_site_objective(self.category, self.event_type)", source)


if __name__ == "__main__":
    unittest.main()

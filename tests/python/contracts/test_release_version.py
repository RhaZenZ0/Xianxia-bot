from tests.support import PROJECT_ROOT
from pathlib import Path
import unittest

from app import __version__
from app.ops.health import HealthState


class ReleaseVersionTests(unittest.TestCase):
    # These assertions read the expected release from app/version.py instead of
    # repeating a literal.  The previous version hardcoded "0.18" in eight
    # places, which is exactly how VERSION drifted to 0.19 while app/version.py,
    # the Dockerfile and docker-compose.yml stayed on 0.18: the test failed
    # rather than the stamps being fixed.  A release bump now touches
    # app/version.py plus the three files checked here, and the single literal
    # below is the only place the number is spelled out in the suite.
    def test_release_version_is_stamped_consistently_everywhere(self):
        root = PROJECT_ROOT
        release = __version__
        self.assertRegex(release, r"^\d+\.\d+(\.\d+)?$")
        self.assertEqual((root / "VERSION").read_text(encoding="utf-8").strip(), release)
        self.assertIn(
            f'org.opencontainers.image.version="{release}"',
            (root / "Dockerfile").read_text(encoding="utf-8"),
        )
        self.assertIn(
            f'xianxia.release: "{release}"',
            (root / "docker-compose.yml").read_text(encoding="utf-8"),
        )

    def test_release_is_the_expected_version(self):
        self.assertEqual(__version__, "1.0.0")

    def test_the_readme_and_the_changelog_announce_the_stamped_release(self):
        """rc bar: the README title and the changelog's "Release status" line
        match app/version.py. The README announced v0.21.0 for eleven minors
        once; this is what stops it drifting again."""
        root = PROJECT_ROOT
        readme = (root / "README.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(readme, f"# Xianxia RP Discord Bot v{__version__}")
        versions = (root / "VERSIONS.md").read_text(encoding="utf-8")
        self.assertIn(f"## Release status — v{__version__}", versions)
        self.assertIn(f"- Current release: v{__version__}", versions)
        from app.database import SCHEMA_VERSION
        self.assertIn(f"The current schema is **{SCHEMA_VERSION}**", (root / "README.md").read_text(encoding="utf-8"))

    def test_the_go_version_is_the_same_everywhere(self):
        """rc bar: the README's stated Go minimum is go.mod's go directive, and
        the engine's build image is at least that. (DEVELOPMENT.md, which the
        roadmap named, was folded into the README's Development section.)"""
        import re
        root = PROJECT_ROOT
        directive = re.search(r"^go (\d+\.\d+)", (root / "go_core" / "go.mod").read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(directive)
        wanted = directive.group(1)
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"Go {wanted}+", readme)
        image = re.search(r"FROM golang:(\d+\.\d+)", (root / "go_core" / "Dockerfile").read_text(encoding="utf-8"))
        self.assertIsNotNone(image)
        self.assertGreaterEqual(tuple(int(x) for x in image.group(1).split(".")), tuple(int(x) for x in wanted.split(".")))

    def test_health_metadata_exposes_release_version(self):
        state = HealthState(supported_schema_version=4)
        self.assertEqual(state.snapshot()["version"], __version__)
        self.assertIn(
            f'xianxia_build_info{{version="{__version__}"}} 1', state.prometheus_metrics()
        )


if __name__ == "__main__":
    unittest.main()

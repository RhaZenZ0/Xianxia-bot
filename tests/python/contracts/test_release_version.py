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
        self.assertEqual(__version__, "0.36.1")

    def test_health_metadata_exposes_release_version(self):
        state = HealthState(supported_schema_version=4)
        self.assertEqual(state.snapshot()["version"], __version__)
        self.assertIn(
            f'xianxia_build_info{{version="{__version__}"}} 1', state.prometheus_metrics()
        )


if __name__ == "__main__":
    unittest.main()

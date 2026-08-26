from tests.support import PROJECT_ROOT
from pathlib import Path
import unittest

from app import __version__
from app.health import HealthState


class ReleaseVersionTests(unittest.TestCase):
    def test_release_is_marked_v0_11_everywhere(self):
        root = PROJECT_ROOT
        self.assertEqual(__version__, "0.13")
        self.assertEqual((root / "VERSION").read_text(encoding="utf-8").strip(), "0.13")
        self.assertIn('org.opencontainers.image.version="0.13"', (root / "Dockerfile").read_text(encoding="utf-8"))
        self.assertIn('xianxia.release: "0.13"', (root / "docker-compose.yml").read_text(encoding="utf-8"))

    def test_health_metadata_exposes_release_version(self):
        state = HealthState(supported_schema_version=4)
        self.assertEqual(state.snapshot()["version"], "0.13")
        self.assertIn('xianxia_build_info{version="0.13"} 1', state.prometheus_metrics())


if __name__ == "__main__":
    unittest.main()

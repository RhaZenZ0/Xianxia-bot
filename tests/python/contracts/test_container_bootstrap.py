from tests.support import PROJECT_ROOT
import unittest


class ContainerBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.root = PROJECT_ROOT
        self.compose = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
        self.startup = (self.root / "startup.sh").read_text(encoding="utf-8")
        self.stop = (self.root / "stop.sh").read_text(encoding="utf-8")

    def test_database_bootstrap_is_a_required_one_shot_gate(self):
        self.assertIn("xianxia-db-init:", self.compose)
        self.assertIn('command: ["python", "-m", "app.database.bootstrap"]', self.compose)
        self.assertIn("xianxia-db-init:\n        condition: service_completed_successfully", self.compose)

    def test_cpu_only_nas_stack_has_no_local_llm_service(self):
        self.assertNotIn("ollama:", self.compose)
        self.assertNotIn("local-llm", self.compose)
        self.assertNotIn("OLLAMA_", self.compose)
        self.assertFalse((self.root / "QNAP_QWEN_SETUP.sh").exists())

    def test_qnap_scripts_manage_engine_bot_and_optional_dashboard(self):
        self.assertIn("docker compose --profile dashboard up", self.startup)
        self.assertIn("OPENROUTER_API_KEY", self.startup)
        self.assertIn("DASHBOARD_TOKEN", self.startup)
        self.assertIn('BOT_CONTROL_URL: "http://xianxia-bot:8080"', self.compose)
        self.assertIn('test: ["CMD", "python", "-m", "app.ops.healthcheck"]', self.compose)
        self.assertNotIn("ollama", self.startup.casefold())
        self.assertIn("docker compose --profile dashboard down", self.stop)
        self.assertNotIn("ollama", self.stop.casefold())


if __name__ == "__main__":
    unittest.main()

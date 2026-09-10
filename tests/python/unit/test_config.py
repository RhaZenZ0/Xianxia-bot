import os
import unittest
from unittest.mock import patch

from tests.support import install_dotenv_shim

install_dotenv_shim()

from app.ops.config import Settings


class ConfigTests(unittest.TestCase):
    def base_env(self):
        return {
            "DISCORD_TOKEN": "test-token",
            "GUILD_ID": "123456789012345678",
            "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
            "DATABASE_PATH": "data/test.sqlite3",
        }

    def test_minimal_valid_config_uses_safe_cpu_nas_defaults(self):
        with patch.dict(os.environ, self.base_env(), clear=True):
            settings = Settings.from_env()
        self.assertFalse(settings.message_content_intent)
        self.assertFalse(settings.auto_narrate)
        self.assertFalse(settings.auto_narrate_event_threads)
        self.assertEqual(settings.narrator_provider, "procedural")
        self.assertEqual(settings.narrator_context_max_chars, 4000)
        self.assertEqual(settings.rag_context_cache_seconds, 4.0)
        self.assertEqual(settings.rag_canon_cache_seconds, 120.0)
        self.assertEqual(settings.unexpected_event_chance_percent, 28)
        self.assertEqual(settings.health_host, "127.0.0.1")  # loopback off Docker since v0.29.0
        self.assertEqual(settings.health_port, 8082)  # 8080 is the QNAP QTS admin port
        self.assertEqual(settings.slow_query_ms, 100.0)
        self.assertIsNone(settings.alert_webhook_url)
        self.assertEqual(settings.alert_cooldown_seconds, 300)
        self.assertEqual(settings.game_engine_url, "http://127.0.0.1:8081")
        self.assertEqual(settings.game_engine_timeout_seconds, 30.0)

    def test_the_ten_dollar_switch_picks_the_daily_allowance(self):
        # v0.31.0: 50 a day under ten dollars of credit, 1000 at ten or more;
        # an explicit cap still wins over both.
        with patch.dict(os.environ, self.base_env(), clear=True):
            settings = Settings.from_env()
        self.assertFalse(settings.openrouter_credits_topped_up)
        self.assertEqual(settings.openrouter_max_requests_per_day, 50)
        with patch.dict(os.environ, {**self.base_env(), "OPENROUTER_CREDITS_TOPPED_UP": "true"}, clear=True):
            settings = Settings.from_env()
        self.assertTrue(settings.openrouter_credits_topped_up)
        self.assertEqual(settings.openrouter_max_requests_per_day, 1000)
        env = {**self.base_env(), "OPENROUTER_CREDITS_TOPPED_UP": "true", "OPENROUTER_MAX_REQUESTS_PER_DAY": "300"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(Settings.from_env().openrouter_max_requests_per_day, 300)

    def test_openrouter_key_selects_cloud_router_by_default(self):
        env = self.base_env()
        env["OPENROUTER_API_KEY"] = "sk-or-test"
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.narrator_provider, "openrouter")
        self.assertEqual(settings.openrouter_routine_model, "google/gemma-4-31b-it:free")
        # v0.26.1: no default second hop - OpenRouter withdrew z-ai/glm-5.2:free
        # and 404s it, so the shipped default failed every narration.
        self.assertEqual(settings.openrouter_routine_fallback_model, "")
        self.assertEqual(settings.openrouter_epic_model, "google/gemma-4-31b-it:free")
        self.assertEqual(settings.openrouter_epic_fallback_model, "")
        self.assertTrue(settings.openrouter_disable_reasoning)
        self.assertEqual(settings.openrouter_dynamic_free_model, "openrouter/free")
        # v0.26.0: the direct Google route is opt-in. No key means the router
        # behaves exactly as it did before the feature existed.
        self.assertIsNone(settings.google_ai_studio_api_key)
        self.assertEqual(settings.google_ai_studio_model, "aistudio/gemini-3.8-flash")
        self.assertEqual(settings.openrouter_max_requests_per_minute, 20)
        self.assertEqual(settings.openrouter_timeout_seconds, 30.0)
        self.assertEqual(settings.openrouter_epic_timeout_seconds, 60.0)
        self.assertTrue(settings.openrouter_require_free)

    def test_gemini_api_key_is_accepted_as_an_alias(self):
        # Google's own quickstart says `export GEMINI_API_KEY`, so an operator
        # who followed it must not have to find a second spelling.
        env = self.base_env()
        env.update({"OPENROUTER_API_KEY": "sk-or-test", "GEMINI_API_KEY": "ai-studio-key"})
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.google_ai_studio_api_key, "ai-studio-key")

    def test_the_explicit_name_wins_over_the_alias(self):
        env = self.base_env()
        env.update({
            "OPENROUTER_API_KEY": "sk-or-test",
            "GOOGLE_AI_STUDIO_API_KEY": "explicit",
            "GEMINI_API_KEY": "alias",
        })
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.google_ai_studio_api_key, "explicit")

    def test_a_google_model_without_the_prefix_is_refused(self):
        env = self.base_env()
        env.update({
            "OPENROUTER_API_KEY": "sk-or-test",
            "GOOGLE_AI_STUDIO_API_KEY": "k",
            "GOOGLE_AI_STUDIO_MODEL": "gemini-3.8-flash",
        })
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "aistudio/"):
                Settings.from_env()

    def test_the_free_guard_does_not_reject_the_google_route(self):
        # OPENROUTER_REQUIRE_FREE is about OpenRouter's catalogue; an aistudio/
        # id has no ":free" suffix and must not be caught by it.
        env = self.base_env()
        env.update({
            "OPENROUTER_API_KEY": "sk-or-test",
            "OPENROUTER_REQUIRE_FREE": "true",
            "GOOGLE_AI_STUDIO_API_KEY": "k",
        })
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.google_ai_studio_model, "aistudio/gemini-3.8-flash")

    def test_openrouter_free_guard_rejects_paid_route(self):
        env = self.base_env()
        env.update({
            "OPENROUTER_API_KEY": "sk-or-test",
            "OPENROUTER_ROUTINE_MODEL": "google/gemma-4-31b-it",
        })
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENROUTER_ROUTINE_MODEL"):
                Settings.from_env()

    def test_openrouter_free_router_is_allowed_by_free_guard(self):
        env = self.base_env()
        env.update({
            "OPENROUTER_API_KEY": "sk-or-test",
            "OPENROUTER_DYNAMIC_FREE_FALLBACK": "openrouter/free",
        })
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.openrouter_dynamic_free_model, "openrouter/free")

    def test_openrouter_rate_limit_and_timeouts_are_validated(self):
        env = self.base_env()
        env.update({
            "OPENROUTER_API_KEY": "sk-or-test",
            "OPENROUTER_MAX_REQUESTS_PER_MINUTE": "17",
            "OPENROUTER_TIMEOUT_SECONDS": "25",
            "OPENROUTER_EPIC_TIMEOUT_SECONDS": "55",
            "OPENROUTER_FAILURE_COOLDOWN_SECONDS": "15",
        })
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.openrouter_max_requests_per_minute, 17)
        self.assertEqual(settings.openrouter_timeout_seconds, 25.0)
        self.assertEqual(settings.openrouter_epic_timeout_seconds, 55.0)
        self.assertEqual(settings.openrouter_failure_cooldown_seconds, 15.0)

    def test_game_engine_settings_are_validated(self):
        env = self.base_env()
        env.update({
            "GAME_ENGINE_URL": "http://xianxia-engine:8081/",
            "GAME_ENGINE_TIMEOUT_SECONDS": "45",
        })
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.game_engine_url, "http://xianxia-engine:8081")
        self.assertEqual(settings.game_engine_timeout_seconds, 45.0)

        env["GAME_ENGINE_URL"] = "not-a-url"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GAME_ENGINE_URL"):
                Settings.from_env()

    def test_narrator_provider_validation_rejects_unsupported_values(self):
        # The validator is a whitelist - openrouter, procedural, disabled - so a
        # .env naming a provider this build does not carry fails loudly at
        # startup rather than silently narrating procedurally.
        env = self.base_env()
        env["NARRATOR_PROVIDER"] = "some-retired-provider"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "NARRATOR_PROVIDER"):
                Settings.from_env()

    def test_invalid_health_port_is_rejected(self):
        env = self.base_env()
        env["HEALTH_PORT"] = "70000"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "HEALTH_PORT"):
                Settings.from_env()

    def test_negative_observability_settings_are_rejected(self):
        env = self.base_env()
        env["SLOW_QUERY_MS"] = "-1"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "SLOW_QUERY_MS"):
                Settings.from_env()
        env = self.base_env()
        env["ALERT_COOLDOWN_SECONDS"] = "-1"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ALERT_COOLDOWN_SECONDS"):
                Settings.from_env()

    def test_invalid_guild_id_has_clear_error(self):
        env = self.base_env()
        env["GUILD_ID"] = "not-a-number"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GUILD_ID"):
                Settings.from_env()

    def test_invalid_event_chance_is_rejected(self):
        env = self.base_env()
        env["UNEXPECTED_EVENT_CHANCE_PERCENT"] = "101"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "between 0 and 100"):
                Settings.from_env()

    def test_invalid_thread_archive_value_is_rejected(self):
        env = self.base_env()
        env["EVENT_THREAD_AUTO_ARCHIVE_MINUTES"] = "30"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "must be one of"):
                Settings.from_env()

    def test_legacy_reincarnation_env_alias_is_ignored(self):
        env = self.base_env()
        env["REINCARNATION_FAMILY_YEARS_PER_WINDOW"] = "999"
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.reincarnation_base_samsara_years, 320)

    def test_removed_channel_fallback_envs_are_not_settings(self):
        env = self.base_env()
        env.update({
            "EVENT_CHANNEL_ID": "111",
            "EVENT_THREAD_CHANNEL_ID": "222",
            "HOME_THREAD_CHANNEL_ID": "333",
        })
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertFalse(hasattr(settings, "event_channel_id"))
        self.assertFalse(hasattr(settings, "event_thread_channel_id"))
        self.assertFalse(hasattr(settings, "home_thread_channel_id"))


if __name__ == "__main__":
    unittest.main()

"""The release channel (v0.20.4): app/ops/release_channel.py, the settings
that drive it, the bot's check (through its source, since discord.py is not
importable here), update.sh's network modes, and the workflow that produces
the assets both of them read.
"""
import json
import os
import re
import subprocess
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT, install_dotenv_shim
install_dotenv_shim()

from app.ops.config import Settings
from app.ops.release_channel import (
    CHANNELS,
    DEFAULT_REPOSITORY,
    Release,
    announcement,
    api_url,
    is_prerelease_tag,
    newer_than_installed,
    newest_for_channel,
    parse_releases,
    parse_version,
)

UPDATE_SH = (PROJECT_ROOT / "update.sh").read_text(encoding="utf-8")
WORKFLOW = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
BOT = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
ENV_EXAMPLE = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")


def _entry(tag, version, *, prerelease=False, draft=False, assets=True, body=""):
    entry = {
        "tag_name": tag, "draft": draft, "prerelease": prerelease, "body": body,
        "html_url": f"https://github.com/{DEFAULT_REPOSITORY}/releases/tag/{tag}",
        "assets": [],
    }
    if assets:
        base = f"https://github.com/{DEFAULT_REPOSITORY}/releases/download/{tag}"
        entry["assets"] = [
            {"name": f"xianxia_rp_v{version}.zip", "browser_download_url": f"{base}/xianxia_rp_v{version}.zip"},
            {"name": f"xianxia_rp_v{version}.zip.sha256", "browser_download_url": f"{base}/xianxia_rp_v{version}.zip.sha256"},
        ]
    return entry


LISTING = [
    _entry("v0.21.0-beta.1", "0.21.0", prerelease=True, body="# v0.21.0-beta.1\n\nFirst beta of the authority milestone."),
    _entry("v0.20.9", "0.20.9", body="**0.20.9** fixes the thing."),
    _entry("v0.20.8", "0.20.8", draft=True),
    _entry("v0.20.7", "0.20.7", assets=False),
    _entry("v0.20.3", "0.20.3"),
    {"tag_name": "not-a-version", "assets": []},
]


class VersionTests(unittest.TestCase):
    def test_tags_and_plain_versions_parse_and_the_suffix_is_dropped(self):
        self.assertEqual(parse_version("0.20.4"), (0, 20, 4))
        self.assertEqual(parse_version("v0.20.4"), (0, 20, 4))
        self.assertEqual(parse_version("v0.21.0-beta.1"), (0, 21, 0))
        self.assertEqual(parse_version("v1.0.0-rc.2"), (1, 0, 0))
        for bad in ("0.20", "v0.20.4.1", "latest", ""):
            with self.assertRaises(ValueError, msg=bad):
                parse_version(bad)

    def test_a_suffix_means_prerelease(self):
        self.assertTrue(is_prerelease_tag("v0.21.0-beta.1"))
        self.assertFalse(is_prerelease_tag("v0.21.0"))

    def test_numeric_not_lexicographic(self):
        self.assertGreater(parse_version("0.20.10"), parse_version("0.20.9"))


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.releases = parse_releases(json.dumps(LISTING))

    def test_drafts_assetless_and_unparseable_entries_are_skipped(self):
        self.assertEqual([r.tag for r in self.releases], ["v0.21.0-beta.1", "v0.20.9", "v0.20.3"])

    def test_stable_ignores_prereleases_and_beta_sees_everything(self):
        self.assertEqual(newest_for_channel(self.releases, "stable").tag, "v0.20.9")
        self.assertEqual(newest_for_channel(self.releases, "beta").tag, "v0.21.0-beta.1")
        with self.assertRaises(ValueError):
            newest_for_channel(self.releases, "nightly")
        self.assertEqual(CHANNELS, ("stable", "beta"))

    def test_a_suffixed_tag_is_beta_even_if_the_checkbox_was_missed(self):
        releases = parse_releases([_entry("v0.22.0-rc.1", "0.22.0", prerelease=False)])
        self.assertTrue(releases[0].prerelease)
        self.assertIsNone(newest_for_channel(releases, "stable"))

    def test_only_a_strictly_newer_release_counts(self):
        stable = newest_for_channel(self.releases, "stable")
        self.assertIsNotNone(newer_than_installed(stable, "0.20.3"))
        self.assertIsNone(newer_than_installed(stable, "0.20.9"))
        self.assertIsNone(newer_than_installed(stable, "0.21.0"))
        self.assertIsNone(newer_than_installed(None, "0.20.3"))

    def test_assets_are_the_archive_and_its_sidecar(self):
        release = newest_for_channel(self.releases, "stable")
        self.assertTrue(release.archive_url.endswith("/v0.20.9/xianxia_rp_v0.20.9.zip"))
        self.assertTrue(release.checksum_url.endswith("/xianxia_rp_v0.20.9.zip.sha256"))
        self.assertEqual(release.version_text, "0.20.9")
        self.assertEqual(release.channel, "stable")

    def test_the_api_url_is_the_repositorys_release_listing(self):
        self.assertEqual(api_url(), f"https://api.github.com/repos/{DEFAULT_REPOSITORY}/releases?per_page=20")
        self.assertEqual(api_url("someone/fork", per_page=5), "https://api.github.com/repos/someone/fork/releases?per_page=5")

    def test_the_announcement_names_version_channel_and_the_updater_commands(self):
        release = newest_for_channel(self.releases, "stable")
        text = announcement(release, "0.20.3", "stable")
        self.assertIn("**0.20.9** is available on the **stable** channel (installed: 0.20.3)", text)
        self.assertIn("fixes the thing", text)
        self.assertIn(release.page_url, text)
        self.assertIn("./update.sh --fetch", text)
        self.assertLess(len(text), 1600, "post_server_log truncates at 1600")
        beta = newest_for_channel(self.releases, "beta")
        self.assertIn("First beta of the authority milestone", announcement(beta, "0.20.3", "beta"))


class SettingsTests(unittest.TestCase):
    def base_env(self):
        return {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678", "OPENAI_API_KEY": "", "DATABASE_PATH": "data/test.sqlite3"}

    def test_defaults_check_the_stable_channel_daily(self):
        with patch.dict(os.environ, self.base_env(), clear=True):
            settings = Settings.from_env()
        self.assertTrue(settings.update_check_enabled)
        self.assertEqual(settings.update_channel, "stable")
        self.assertEqual(settings.update_repository, DEFAULT_REPOSITORY)
        self.assertEqual(settings.update_check_hours, 24)

    def test_beta_and_off_are_honoured_and_junk_is_rejected(self):
        env = {**self.base_env(), "UPDATE_CHANNEL": "Beta", "UPDATE_CHECK_ENABLED": "false", "UPDATE_CHECK_HOURS": "6"}
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.update_channel, "beta")
        self.assertFalse(settings.update_check_enabled)
        self.assertEqual(settings.update_check_hours, 6)
        for key, value in (("UPDATE_CHANNEL", "nightly"), ("UPDATE_CHECK_HOURS", "0")):
            with patch.dict(os.environ, {**self.base_env(), key: value}, clear=True):
                with self.assertRaises(ValueError, msg=key):
                    Settings.from_env()

    def test_env_example_documents_all_four(self):
        for key in ("UPDATE_CHECK_ENABLED=true", "UPDATE_CHANNEL=stable", f"UPDATE_REPOSITORY={DEFAULT_REPOSITORY}", "UPDATE_CHECK_HOURS=24"):
            self.assertIn(key, ENV_EXAMPLE, key)


class BotWorkerTests(unittest.TestCase):
    """discord.py is absent here, so the bot's side is checked in source."""

    def test_the_worker_is_started_only_when_enabled_and_cancelled_on_close(self):
        self.assertIn("if SETTINGS.update_check_enabled:", BOT)
        self.assertIn("self.update_check_task = asyncio.create_task(self.update_check_worker())", BOT)
        self.assertIn('for task_name in ("event_expiry_task", "operational_health_task", "update_check_task"):', BOT)

    def test_the_check_uses_the_shared_module_and_announces_once_per_version(self):
        body = BOT[BOT.index("async def check_for_release"):BOT.index("async def update_check_worker")]
        self.assertIn("parse_releases(response.text)", body)
        self.assertIn("newest_for_channel(releases, channel)", body)
        self.assertIn("newer_than_installed(newest, RELEASE_VERSION)", body)
        self.assertIn("announcement(available, RELEASE_VERSION, channel)", body)
        self.assertIn("if self.announced_release != available.version_text:", body)
        self.assertIn('await post_server_log(self.get_guild(SETTINGS.guild_id), "Update available", text)', body)
        # An unreachable channel is a health check, never an exception out of the worker.
        self.assertIn('self.health_state.set_check("release_channel", False', body)
        self.assertIn("return None", body)

    def test_the_worker_waits_out_the_startup_and_uses_the_configured_cadence(self):
        body = BOT[BOT.index("async def update_check_worker"):BOT.index("async def close_event_scene")]
        self.assertIn("await asyncio.sleep(60)", body)
        self.assertIn("await asyncio.sleep(SETTINGS.update_check_hours * 3600)", body)
        self.assertIn("except asyncio.CancelledError:\n                    raise", body)


class UpdaterTests(unittest.TestCase):
    def test_the_script_parses(self):
        subprocess.run(["sh", "-n", str(PROJECT_ROOT / "update.sh")], check=True)

    def test_network_is_opt_in(self):
        # The default mode never touches the network; the three that do are
        # explicit. http_get is only reachable from resolve_release/fetch_release.
        self.assertIn('    "") MODE=local ;;', UPDATE_SH)
        for mode in ("--check) MODE=remote_check", "--fetch) MODE=fetch", "--upgrade) MODE=upgrade"):
            self.assertIn(mode, UPDATE_SH)
        self.assertIn("Without --check/--fetch/--upgrade no network access is used.", UPDATE_SH)
        # Every http_get call sits inside resolve_release() or fetch_release(),
        # which only the three network modes reach.
        current = None
        callers = set()
        for line in UPDATE_SH.splitlines():
            if re.match(r"^\w+\(\) \{", line):
                current = line.split("(")[0]
            elif line.startswith("}"):
                current = None
            elif "http_get " in line and "http_get()" not in line:
                callers.add(current)
        self.assertEqual(callers, {"resolve_release", "fetch_release"})
        for mode in ("remote_check", "fetch", "upgrade"):
            self.assertIn(f'[ "$MODE" = {mode} ]', UPDATE_SH, mode)

    def test_channel_selection_mirrors_the_python_module(self):
        # stable = GitHub's /releases/latest (no drafts, no prereleases);
        # beta = newest in the listing. Same rule as newest_for_channel().
        self.assertIn('/releases/latest" "$listing"', UPDATE_SH)
        self.assertIn('/releases?per_page=10" "$listing"', UPDATE_SH)
        self.assertIn("case \"$UPDATE_CHANNEL\" in stable|beta) ;;", UPDATE_SH)
        self.assertIn('UPDATE_CHANNEL=${CHANNEL_OVERRIDE:-${XIANXIA_UPDATE_CHANNEL:-$(env_value UPDATE_CHANNEL)}}', UPDATE_SH)
        self.assertIn('[ -n "$UPDATE_REPOSITORY" ] || UPDATE_REPOSITORY="RhaZenZ0/Xianxia-bot"', UPDATE_SH)

    def test_a_fetched_archive_is_verified_before_it_is_accepted(self):
        body = UPDATE_SH[UPDATE_SH.index("fetch_release() {"):UPDATE_SH.index('if [ "$MODE" = remote_check ]')]
        self.assertIn("SHA-256 mismatch", body)
        self.assertIn('refusing an unverifiable archive', body)
        self.assertIn('[ "$inner" = "$RELEASE_VERSION" ]', body)
        # The .part file is what gets verified; only a verified file is renamed into ./updates.
        self.assertLess(body.index('sha256_of "$partial"'), body.index('mv -f "$partial" "$target"'))
        self.assertIn('"$UPDATES_DIR/xianxia_rp_v$RELEASE_VERSION.zip"', body)

    def test_upgrade_hands_the_fetched_archive_to_the_existing_install_path(self):
        self.assertIn("MODE=install; REQUESTED_ARCHIVE=$FETCHED_ARCHIVE", UPDATE_SH)
        # ... which still verifies the release manifest before touching the install.
        self.assertIn("verify_release_manifest", UPDATE_SH)

    def test_works_with_wget_alone(self):
        body = UPDATE_SH[UPDATE_SH.index("http_get() {"):UPDATE_SH.index("sha256_of() {")]
        self.assertIn("command -v curl", body)
        self.assertIn("command -v wget", body)
        self.assertNotIn("jq", UPDATE_SH)


class WorkflowTests(unittest.TestCase):
    def test_runs_on_version_tags_and_can_write_releases(self):
        self.assertIn('tags: ["v*"]', WORKFLOW)
        self.assertIn("contents: write", WORKFLOW)

    def test_the_tag_must_match_the_stamped_version(self):
        self.assertIn('version="${version%%-*}"', WORKFLOW)
        self.assertIn('if [ "$version" != "$stamped" ]; then', WORKFLOW)
        self.assertIn("case \"$tag\" in *-*) prerelease=true ;; esac", WORKFLOW)

    def test_it_runs_the_same_checks_as_ci_plus_the_manifest(self):
        for step in ("python -m ruff check app scripts", "python -m pytest -q", "python scripts/release_manifest.py --verify",
                     "CGO_ENABLED=1 go vet ./... && CGO_ENABLED=1 go test ./..."):
            self.assertIn(step, WORKFLOW, step)

    def test_the_assets_are_what_the_updater_and_the_bot_look_for(self):
        self.assertIn('echo "asset=xianxia_rp_v${version}.zip"', WORKFLOW)
        self.assertIn('sha256sum "$asset" > "$asset.sha256"', WORKFLOW)
        self.assertIn("/tmp/${{ steps.version.outputs.asset }}.sha256", WORKFLOW)
        self.assertIn("prerelease: ${{ steps.version.outputs.prerelease }}", WORKFLOW)
        # The archive is built exactly as the hand-made releases were (same
        # exclusions), and .github stays in: the manifest lists it and
        # update.sh verifies the manifest against the extracted tree.
        self.assertIn("-x '*/__pycache__/*' '*.pyc' '.env' 'data/*' '.git/*'", WORKFLOW)
        self.assertNotIn("'.github/*'", WORKFLOW)


if __name__ == "__main__":
    unittest.main()

"""migrate_env.sh must carry an operator's values onto a new .env.example.

A release adds keys and rewrites .env.example; update.sh preserves .env
untouched, because that is where the tokens are. The two drift, and
`startup.sh --check-env` then refuses an install over a key the operator has
never seen - with the only remedy being to diff the two files by hand, at the
point in an upgrade where a mistyped DISCORD_TOKEN costs another restart.

These run the real script. The values it moves are secrets, so the tests care
about two things above all: that a value arrives byte for byte, and that the
backup it leaves behind cannot reach git, a release archive or the manifest.
"""
from tests.support import PROJECT_ROOT
import os
import re
import shutil
import stat
import subprocess
import unittest

SCRIPT = PROJECT_ROOT / "migrate_env.sh"

# One value per awkward shape the real .env.example already contains or invites:
# a bare `$` (TYPED_PLAY_PREFIX), a space (OPENROUTER_APP_NAME), and the ones a
# token generator can produce. A backslash is the one that matters most - both
# BusyBox's and dash's `echo` expand escapes, so a script using echo instead of
# printf corrupts this value and nothing else.
AWKWARD = r'''a\b%c$d "quoted" =equals= tail'''


def value_of(text: str, key: str) -> str | None:
    match = re.search(rf"^{key}=(.*)$", text, re.M)
    return match.group(1) if match else None


class EnvMigrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        shutil.copy2(SCRIPT, self.dir / "migrate_env.sh")
        (self.dir / ".env.example").write_text(
            "# header\nDISCORD_TOKEN=\nTYPED_PLAY_PREFIX=$\n"
            "\n# --- section ---\nKEPT_DEFAULT=4\nBRAND_NEW_KEY=on\n",
            encoding="utf-8",
        )
        (self.dir / ".env").write_text(
            f"DISCORD_TOKEN={AWKWARD}\nTYPED_PLAY_PREFIX=!\nKEPT_DEFAULT=9\nRETIRED_KEY=mine\n",
            encoding="utf-8",
        )

    def run_script(self, *args):
        return subprocess.run(
            ["sh", "./migrate_env.sh", *args],
            cwd=self.dir, capture_output=True, text=True,
        )

    def env(self) -> str:
        return (self.dir / ".env").read_text(encoding="utf-8")

    def test_a_value_survives_byte_for_byte(self):
        """The whole point. A token that arrives altered is worse than no script."""
        self.run_script()
        self.assertEqual(value_of(self.env(), "DISCORD_TOKEN"), AWKWARD)
        self.assertEqual(value_of(self.env(), "TYPED_PLAY_PREFIX"), "!")
        self.assertEqual(value_of(self.env(), "KEPT_DEFAULT"), "9",
                         "the operator's value must win over the template's default")

    def test_a_new_key_arrives_at_the_release_default(self):
        self.run_script()
        self.assertEqual(value_of(self.env(), "BRAND_NEW_KEY"), "on")

    def test_a_key_the_release_dropped_is_kept_not_deleted(self):
        """There is no undo for deleting a value an operator set."""
        result = self.run_script()
        self.assertEqual(value_of(self.env(), "RETIRED_KEY"), "mine")
        self.assertIn("RETIRED_KEY", result.stdout, "a kept-but-retired key must be reported")

    def test_the_template_shape_is_what_the_new_file_takes(self):
        self.run_script()
        text = self.env()
        self.assertIn("# header", text)
        self.assertIn("# --- section ---", text, "the template's sections are the point of rebuilding")

    def test_no_value_is_ever_printed(self):
        """The report is meant to be safe to paste into an issue."""
        result = self.run_script()
        self.assertNotIn(AWKWARD, result.stdout + result.stderr)
        self.assertNotIn("mine", result.stdout + result.stderr)

    def test_dry_run_writes_nothing(self):
        before = self.env()
        result = self.run_script("--dry-run")
        self.assertEqual(self.env(), before)
        self.assertIn("BRAND_NEW_KEY", result.stdout, "--dry-run must still say what it would do")
        self.assertFalse(list(self.dir.glob(".env.bak.*")), "--dry-run must not leave a backup")

    def test_the_previous_file_is_backed_up_and_the_new_one_is_private(self):
        self.run_script()
        backups = list(self.dir.glob(".env.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(value_of(backups[0].read_text(encoding="utf-8"), "DISCORD_TOKEN"), AWKWARD)
        mode = stat.S_IMODE(os.stat(self.dir / ".env").st_mode)
        self.assertEqual(mode, 0o600, f"a file of tokens was left at {mode:o}")

    def test_running_it_twice_changes_nothing_and_leaves_one_backup(self):
        self.run_script()
        settled = self.env()
        result = self.run_script()
        self.assertEqual(self.env(), settled)
        self.assertEqual(len(list(self.dir.glob(".env.bak.*"))), 1,
                         "a second run with nothing to do must not write another backup")
        self.assertIn("Nothing written", result.stdout)

    def test_it_refuses_rather_than_inventing_an_env(self):
        (self.dir / ".env").unlink()
        self.assertEqual(self.run_script().returncode, 1)


class EnvBackupIsTreatedAsASecretTests(unittest.TestCase):
    """`.env.bak.<stamp>` holds exactly the tokens `.env` does.

    Every place `.env` is already held back - git, the release archive, the
    manifest, and the updater's preserve list - has to know about the backup
    too, or introducing one quietly creates a way for a token to escape. The
    updater's list is the odd one out: there it is not about secrecy but about
    not deleting the operator's backup on the next upgrade.
    """

    def test_git_will_not_stage_one(self):
        result = subprocess.run(
            ["git", "check-ignore", "-q", ".env.bak.20260101-000000"],
            cwd=PROJECT_ROOT, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, ".env.bak.* is not in .gitignore")

    def test_the_release_manifest_never_lists_one(self):
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        try:
            import release_manifest
        finally:
            sys.path.pop(0)
        self.assertTrue(
            ".env.bak.20260101-000000".startswith(release_manifest.EXCLUDED_PREFIXES),
            "release_manifest.py would hash an .env backup into the manifest",
        )

    def test_the_release_archive_never_packages_one(self):
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        zip_line = next(line for line in workflow.splitlines() if "zip -qr" in line)
        self.assertIn("'.env.bak.*'", zip_line)

    def test_the_updater_preserves_one_across_an_upgrade(self):
        update = (PROJECT_ROOT / "update.sh").read_text(encoding="utf-8")
        skip_lists = [line for line in update.splitlines() if 'case "$name" in .env' in line]
        self.assertTrue(skip_lists)
        for line in skip_lists:
            self.assertIn(".env.bak.*", line,
                          f"an .env backup would be deleted by this loop: {line.strip()}")


if __name__ == "__main__":
    unittest.main()

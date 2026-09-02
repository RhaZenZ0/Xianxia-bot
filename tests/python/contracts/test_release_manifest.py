"""RELEASE_MANIFEST.sha256 must actually describe the tree it ships with.

The shipped v0.19 archive carried a manifest with 42 wrong hashes, 14 entries
pointing at files that had since moved into docs/, and 22 packaged files missing
from it entirely - while update.sh never checked it at all. A manifest in that
state is worse than none: it looks like an integrity guarantee and isn't one.

This is a release gate. If it fails, the fix is to regenerate:

    python3 scripts/release_manifest.py --write
"""
from tests.support import PROJECT_ROOT
import subprocess
import sys
import unittest

SCRIPT = PROJECT_ROOT / "scripts" / "release_manifest.py"
MANIFEST = PROJECT_ROOT / "RELEASE_MANIFEST.sha256"


class ReleaseManifestTests(unittest.TestCase):
    def test_manifest_exists_and_is_well_formed(self):
        self.assertTrue(MANIFEST.is_file(), "RELEASE_MANIFEST.sha256 is missing")
        lines = [line for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertGreater(len(lines), 150, "manifest is implausibly short")
        for line in lines:
            digest, separator, name = line.partition("  ")
            self.assertEqual(separator, "  ", f"not in sha256sum -c format: {line!r}")
            self.assertEqual(len(digest), 64, f"not a sha256 digest: {line!r}")
            self.assertTrue(name.strip(), f"empty path: {line!r}")

    def test_manifest_never_lists_itself_or_secrets(self):
        names = {
            line.partition("  ")[2]
            for line in MANIFEST.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        self.assertNotIn("RELEASE_MANIFEST.sha256", names, "a manifest cannot hash itself")
        self.assertNotIn(".env", names, ".env is per-deployment and must never be packaged")
        for name in names:
            self.assertFalse(name.endswith(".pyc"), f"compiled artifact listed: {name}")
            self.assertFalse(name.startswith("data/"), f"runtime data listed: {name}")

    def test_manifest_matches_the_tree(self):
        """The gate itself: every hash correct, nothing missing, nothing unlisted."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--verify", "--root", str(PROJECT_ROOT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            "RELEASE_MANIFEST.sha256 is out of date. Regenerate it with:\n"
            "    python3 scripts/release_manifest.py --write\n\n" + result.stdout + result.stderr,
        )

    def test_manifest_check_uses_only_portable_checksum_flags(self):
        """The updater runs on a QNAP NAS, where sha256sum is BusyBox, not GNU.

        --quiet and --strict are GNU coreutils extensions. BusyBox rejects them
        with "unrecognized option" and exits non-zero, which the updater read as
        a failed integrity check - so a perfectly good v0.19.8 archive was
        refused on the target hardware, in the one code path whose entire job is
        to distinguish a corrupt package from a sound one. Only -c is portable.
        """
        source = (PROJECT_ROOT / "update.sh").read_text(encoding="utf-8")
        start = source.index("verify_release_manifest()")
        block = source[start : source.index("verify_release_manifest\n", start)]
        # Strip comments first: the comment above the check names the offending
        # flags to explain why they are banned, and would otherwise trip its own
        # rule - the same way a docstring quoting a bad pattern trips a grep.
        code = "\n".join(
            line for line in block.splitlines() if not line.lstrip().startswith("#")
        )
        gnu_only = [flag for flag in ("--quiet", "--strict", "--status", "--warn") if flag in code]
        self.assertEqual(
            gnu_only,
            [],
            f"GNU-only checksum flag(s) {gnu_only} in the manifest check; BusyBox "
            f"rejects these and the updater reports a false integrity failure.",
        )
        self.assertIn("sha256sum -c RELEASE_MANIFEST.sha256", block)
        self.assertIn("shasum -a 256 -c RELEASE_MANIFEST.sha256", block)

    def test_manifest_failure_output_reads_the_stream_the_tools_actually_use(self):
        """sha256sum prints FAILED lines on stdout, so stderr alone shows nothing useful."""
        source = (PROJECT_ROOT / "update.sh").read_text(encoding="utf-8")
        start = source.index("verify_release_manifest()")
        block = source[start : source.index("verify_release_manifest\n", start)]
        self.assertIn("2>&1", block, "the check must capture stdout and stderr together")

    def test_updater_verifies_the_manifest_before_installing(self):
        source = (PROJECT_ROOT / "update.sh").read_text(encoding="utf-8")
        self.assertIn("verify_release_manifest", source, "update.sh must verify the manifest")
        # The check has to happen before the installed tree is disturbed, which
        # in this script means before the old files are removed.
        verify_at = source.index("verify_release_manifest\n")
        install_at = source.index('echo "Installing Xianxia RP')
        self.assertLess(verify_at, install_at, "manifest check must run before install begins")


if __name__ == "__main__":
    unittest.main()

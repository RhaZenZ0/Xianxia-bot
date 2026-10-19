"""The engine image carries every file its test suite reads from outside go_core (v1.34.0).

`go_core/Dockerfile` runs `go test ./...` inside the build, on a context that
copies `go_core` and `content` and nothing else. v1.34.0's fixture gate reads
production's DDL out of `app/database/core.py` by walking three directories up
from its own path, as the content readers do, and it fails rather than skips
when the file is missing - so the `containers` job went red on a suite that was
green everywhere else. The file now comes along, and this holds the shape: every
repository path a Go test reaches through `../../../` is the source of a `COPY`
in the engine Dockerfile, or sits under one.
"""
from __future__ import annotations

import re
import unittest

from tests.support import PROJECT_ROOT

GO_CORE = PROJECT_ROOT / "go_core"
DOCKERFILE = (GO_CORE / "Dockerfile").read_text(encoding="utf-8")
# A path argument climbing out of go_core/internal/<pkg>/, in either spelling
# the suite uses: the one literal `"../../../content/world.json"` handed to a
# call, or `filepath.Join("..", "..", "..", "content", "world.json")`. The
# literal must be an argument (after `(` or `,`), not text inside a larger
# string: the server's path-traversal test sends `{"name":"../../../etc/passwd"}`
# as a request body and reads nothing.
CLIMB = re.compile(r'[(,]\s*"(?:\.\./){3}([A-Za-z0-9_./-]+)"')
JOINED = re.compile(r'filepath\.Join\(\s*"\.\.",\s*"\.\.",\s*"\.\.",\s*((?:"[A-Za-z0-9_.-]+",?\s*)+)\)')


def paths_tests_read() -> set[str]:
    out = set()
    for test in GO_CORE.glob("internal/**/*_test.go"):
        text = test.read_text(encoding="utf-8")
        for m in CLIMB.finditer(text):
            out.add(m.group(1).rstrip("/"))
        for m in JOINED.finditer(text):
            out.add("/".join(re.findall(r'"([^"]+)"', m.group(1))))
    return out


def copied_sources() -> set[str]:
    # The build stage ends at the second FROM; the runtime stage's COPY lines
    # (--from=build, the runtime world file) are not the test's context.
    build_stage = DOCKERFILE.split("\nFROM ", 2)[1] if DOCKERFILE.count("\nFROM ") >= 2 else DOCKERFILE
    return {m.group(1).rstrip("/") for m in re.finditer(r"^COPY\s+(\S+)\s+\S+", build_stage, re.M)}


class TheEngineImageCarriesWhatItsTestsRead(unittest.TestCase):
    def test_the_readers_work(self):
        read, copied = paths_tests_read(), copied_sources()
        self.assertIn("content/world.json", read, "no Go test reads the world file by climbing out; the path reader is broken")
        self.assertIn("app/database/core.py", read, "the fixture gate no longer reads production's DDL; the path reader is broken")
        self.assertIn("content", copied, "the Dockerfile reader found no COPY content line")

    def test_every_path_a_test_climbs_to_is_copied_into_the_build(self):
        copied = copied_sources()
        for path in sorted(paths_tests_read()):
            with self.subTest(path=path):
                carried = any(path == src or path.startswith(src + "/") for src in copied)
                self.assertTrue(
                    carried,
                    f"a Go test reads {path} from outside go_core and go_core/Dockerfile does not COPY it into "
                    "the build, so `go test ./...` inside the image fails on a file that is not there "
                    "(the containers job, v1.34.0)",
                )


if __name__ == "__main__":
    unittest.main()

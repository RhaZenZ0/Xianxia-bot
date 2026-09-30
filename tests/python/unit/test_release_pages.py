"""`#updates` links a page, so the page has to exist (v1.8.2).

The post in `#updates` used to link `releases/tag/v<version>`, and a GitHub
Release exists only for a version somebody tagged: 1.7.3 to 1.7.10 and 1.8.0
had none, so a server catching up across them got a row of 404s. It links the
version's page on the GitHub Pages site now, which
`scripts/build_release_pages.py` builds from `VERSIONS.md` and
the `pages` job in `.github/workflows/ci.yml` publishes.

The link and the page are made in two places, so what holds them together is
held here: the builder writes a page for every version the bot can announce,
at the path the bot's link names, and the workflow publishes the directory the
builder writes. And an entry is prose the builder turns into HTML, so markup in
it is escaped rather than obeyed.
"""
from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from app.rules.changelog import changelog_entries, page_path, pages_url, version_key
from app.version import RELEASE_VERSION
from tests.support import PROJECT_ROOT

VERSIONS = (PROJECT_ROOT / "VERSIONS.md").read_text(encoding="utf-8")
WORKFLOW = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_release_pages", PROJECT_ROOT / "scripts" / "build_release_pages.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class EveryAnnouncedVersionHasAPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.labels = _builder().build(VERSIONS, self.out)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_reader_found_the_changelog(self):
        # Asserted before anything is trusted (rc.57): an empty parse would make
        # every assertion after it pass for nothing.
        self.assertIn(RELEASE_VERSION, self.labels, "the builder did not find the running release; the parse is broken")
        self.assertGreater(len(self.labels), 20)

    def test_every_entry_the_bot_can_announce_has_a_page_where_the_link_points(self):
        missing = [label for label, _ in changelog_entries(VERSIONS)
                   if not (self.out / page_path(label) / "index.html").is_file()]
        self.assertEqual(missing, [], "a version the bot can announce would link a page that was never built")

    def test_the_link_the_bot_posts_is_the_page_the_builder_wrote(self):
        url = pages_url("RhaZenZ0/Xianxia-bot", RELEASE_VERSION)
        self.assertEqual(url, f"https://rhazenz0.github.io/Xianxia-bot/v{RELEASE_VERSION}/")
        relative = url.removeprefix(pages_url("RhaZenZ0/Xianxia-bot"))
        self.assertTrue((self.out / relative / "index.html").is_file(), relative)

    def test_the_index_lists_every_release_newest_first(self):
        index = (self.out / "index.html").read_text(encoding="utf-8")
        listed = re.findall(r'<a class="version" href="v([^/"]+)/">', index)
        self.assertEqual(sorted(listed, key=version_key, reverse=True), listed)
        self.assertEqual(set(listed), set(self.labels))

    def test_a_page_carries_the_whole_entry(self):
        page = (self.out / page_path(RELEASE_VERSION) / "index.html").read_text(encoding="utf-8")
        entry = dict(changelog_entries(VERSIONS))[RELEASE_VERSION]
        paragraphs = [block for block in entry.split("\n\n") if block.strip()]
        self.assertEqual(page.count("<p>") + page.count("<ul>"), len(paragraphs))


class AnEntryCannotInjectMarkup(unittest.TestCase):
    def test_markup_in_an_entry_is_escaped_and_the_subset_renders(self):
        builder = _builder()
        html = builder.render("**9.9.9** adds <script>alert(1)</script> and `a<b` with *care*.\n\n"
                              "- one [link](https://example.com)\n- two")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("<code>a&lt;b</code>", html)
        self.assertIn("<strong>9.9.9</strong>", html)
        self.assertIn("<em>care</em>", html)
        self.assertIn('<a href="https://example.com">link</a>', html)
        self.assertEqual(html.count("<li>"), 2)

    def test_only_http_links_become_anchors(self):
        html = _builder().render("a [trap](javascript:alert(1)) here")
        self.assertNotIn("<a ", html)


class _Tags(HTMLParser):
    """Every tag the page carries, with its attributes, as a browser parses it."""

    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class NothingInAnEntryPutsMarkupOnTheSite(unittest.TestCase):
    """v1.12.3: code spans kept a raw quote, and their placeholder was swapped
    back after the link's URL was escaped, so a code span inside a link's
    parentheses broke out of the href."""

    ALLOWED_TAGS = {"p", "ul", "li", "strong", "em", "code", "a"}

    def _tags(self, entry):
        parser = _Tags()
        parser.feed(_builder().render(entry))
        return parser.tags

    def test_a_code_span_inside_a_link_cannot_break_out_of_the_href(self):
        tags = self._tags('[x](https://a/`" onmouseover="alert(1)`)')
        for tag, attrs in tags:
            self.assertNotIn("onmouseover", attrs, f"an attribute was injected on <{tag}>")
        self.assertFalse([t for t, _ in tags if t == "a"], "a link was made out of a URL holding a code span")

    def test_a_quote_in_a_link_or_a_span_stays_text(self):
        for entry in ('[x](https://a/?q="onmouseover="alert(1))', 'see `a" onmouseover="b` here',
                      'plain " onmouseover="x" text'):
            tags = self._tags(entry)
            self.assertEqual({t for t, _ in tags} - self.ALLOWED_TAGS, set())
            for tag, attrs in tags:
                self.assertEqual(set(attrs) - {"href"}, set(), f"{entry!r} put attributes on <{tag}>")

    def test_only_the_tags_the_subset_makes_appear(self):
        html = _builder().render("<img src=x onerror=alert(1)> and `<b>` and [a](https://e.com/<i>)")
        self.assertEqual({t for t, _ in self._tags("<img src=x onerror=alert(1)> and `<b>`")} - self.ALLOWED_TAGS, set())
        self.assertNotIn("<img", html)

    def test_a_url_is_escaped_exactly_once(self):
        html = _builder().render("[q](https://example.com/?a=1&b=2)")
        self.assertIn('href="https://example.com/?a=1&amp;b=2"', html)
        self.assertNotIn("&amp;amp;", html)


class TheWorkflowPublishesWhatTheBuilderWrites(unittest.TestCase):
    def test_it_builds_into_the_directory_it_uploads(self):
        built = re.search(r"build_release_pages\.py --out (\S+)", WORKFLOW)
        uploaded = re.search(r"upload-pages-artifact@\S+\s+with:\s+path:\s*(\S+)", WORKFLOW)
        self.assertIsNotNone(built, "the workflow no longer runs the builder")
        self.assertIsNotNone(uploaded, "the workflow no longer uploads a Pages artifact")
        self.assertEqual(built.group(1), uploaded.group(1))

    def test_it_publishes_from_main_after_the_checks_and_writes_nothing_else(self):
        job = WORKFLOW[WORKFLOW.index("\n  pages:"):WORKFLOW.index("\n  release:")]
        self.assertIn("github.ref == 'refs/heads/main'", job)
        self.assertIn("github.event_name == 'push'", job)
        self.assertIn("needs: [python, go, containers]", job)
        self.assertIn("pages: write", job)
        self.assertIn("actions/deploy-pages@", job)
        self.assertNotIn("contents: write", job)


if __name__ == "__main__":
    unittest.main()

# v0.25.0 — The dashboard, remade

Built from the design canvas approved before this release. No endpoint changed, no query changed,
and no data was added or removed except one small new block on `/api/overview`.

## What was wrong, measured

Two numbers describe the whole problem:

- **23 navigation tabs in a single flat row.** The row was `position:sticky`, and between roughly
  900px and 1400px it wrapped onto three lines — so on a laptop it ate about 120px of every screen,
  permanently, before any content was drawn.
- **102 stacked full-width tables** across the views. Cultivation was eleven tables one under
  another on one page. Crafting & Assets was eleven more. Exploration nine, Samsara eight,
  Economy seven. Reaching the last one meant scrolling past the other ten every time, and nothing
  on the page said what was down there.

Everything else followed from those. Each of the 23 views hand-rolled its own markup, so a table on
Economy and a table on Conflicts did not agree about padding, empty states, or whether a row was
clickable. A view that failed took the whole page with it. And table headers used
`position:sticky` inside a wrapper that had no height, which means **they have never once stuck** —
a bug that survived four releases because nobody scrolled a long table and thought to check.

## The two moves

### A grouped sidebar

Twenty-three views in five groups — World, Players, Content & Economy, Systems, Admin — with a
jump-to filter (`/` focuses it, Enter opens the first match). The two views that can change the
world sit apart from the twenty-one that only read it.

Below 1280px the sidebar collapses to a 52px icon rail; below 900px it becomes a drawer behind a
menu button. The old stylesheet had one breakpoint, at 900px, and nothing between.

### One page template

Every view now gets the same page: a header with the title, a sentence saying what the page is for,
and a density toggle; then the metric strip; then — when a view has three or more sections — tabs;
then the panels.

`sectionize` is the whole mechanism, and it is worth describing because of what it does *not* do.
It runs after a loader has rendered, and it **moves the nodes the loader produced** into a header
and a set of sections. It never re-serialises them. That means every click handler a loader attached
survives the reorganisation, and not one of the twenty-three loaders had to be rewritten to gain
tabs. A section whose entire content is the metric strip is folded into the page summary rather than
becoming an empty first tab.

Cultivation is the proof: eleven tables became ten tabs and a summary strip, over the same single
request.

## Also in the rebuild

- **One table component.** Sticky header that actually sticks, a row count under every table
  (`5 rows`, or `showing 300 of 4,000` — those two used to be indistinguishable), horizontal scroll
  for wide column sets, and empty states that say what would put something there.
- **A density toggle**, comfortable or compact, remembered per browser. Compact swaps the cell
  padding and font size wholesale through CSS variables, so a thirteen-column table tightens without
  every table hard-coding two sets of numbers.
- **A view that fails now fails alone**, in a panel with the error and a retry button, instead of
  replacing the page.
- **Deep links.** `#cultivation/4` opens Cultivation on its fifth tab.
- **Page actions** are declared by a view through `#pageActions` and hoisted into the shared header,
  so no view draws a second title bar of its own.

## The one new thing: "Wants your attention"

Overview gained a short list of conditions that already existed on other pages and that a GM had no
reason to go looking for: quest drafts waiting for review, a simulation system that has missed its
own tick, commissions past the deadline their giver set, and players frozen or muted with no reason
recorded. Each row names the page it lives on and offers the obvious next action.

It is deliberately boring. Nothing in it is new data. It reports only conditions with a clear next
step, it never invents a severity the underlying row does not have, and anything it cannot read it
simply omits — an attention feed that failed loudly on a missing table would be worse than the
absence it is fixing.

Two judgements are worth naming because they are the difference between a useful feed and a nagging
one. **Lag is only reported when a system is further behind than its own interval** — a
4,320-minute system 500 minutes behind is early, not late, and reporting raw lag would make the feed
cry wolf on every slow system. And **a frozen player is only reported when no reason was recorded**;
a GM who wrote down why does not need to be told about it every time they open the dashboard.

## What deliberately did not change

The palette, the serif headings and the drawer are exactly what they were — this should read as the
same product, better organised, and if it looked like a different one that would be a bug. Every
endpoint, every query and every column is unchanged. The coverage gate passes unchanged. The Quests
workbench keeps its shape; it already followed this template and is what the rest was pulled toward.
And nothing here touches the authority split: the dashboard still reads through Go-owned query
sessions and every write is still an engine action.

## Upgrading

No schema change. Three files changed in `dashboard/` and one method was added to the dashboard
store. Clear the browser cache if the old stylesheet is pinned.

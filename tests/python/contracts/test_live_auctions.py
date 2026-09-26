"""v0.33.1: live auction channels and the main menu.

A lot listed in any auction house is posted to that house's channel the
moment it exists, its card follows every bid, and it is struck when the
engine's tick settles it. The feed writes nothing but Discord message ids;
the lot is the engine's row. The channels are created by the dashboard's
Setup/Repair beside the realm capitals and only bound by the /admin path,
like the capitals themselves.
"""
from __future__ import annotations

import ast
import importlib
import os
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
FEED = (BOT / "auction_feed.py").read_text(encoding="utf-8")
ECONOMY = (BOT / "commands" / "economy.py").read_text(encoding="utf-8")
BOT_PY = (BOT / "bot.py").read_text(encoding="utf-8")
CHANNELS = (BOT / "channels.py").read_text(encoding="utf-8")
SETUP = (BOT / "admin" / "server_setup.py").read_text(encoding="utf-8")
SURFACE = (BOT / "surface.py").read_text(encoding="utf-8")

_ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
        "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _surface_module():
    with patch.dict(os.environ, _ENV):
        return importlib.import_module("app.bot.surface")


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheFeedFollowsTheLot(unittest.TestCase):
    def test_listing_posts_and_bidding_refreshes(self):
        sell = _body(ECONOMY, "auction_sell")
        self.assertIn("announce_lot(interaction.guild,house_id,", sell)
        # The engine write comes first; the card follows a lot that exists.
        self.assertLess(sell.index('authoritative_action("auction.sell"'), sell.index("announce_lot("))
        bid = _body(ECONOMY, "auction_bid")
        self.assertIn("refresh_lot(interaction.guild,int(auction_id))", bid)
        self.assertLess(bid.index('authoritative_action("auction.bid"'), bid.index("refresh_lot("))

    def test_settlement_follows_the_tick(self):
        worker = _body(BOT_PY, "event_expiry_worker")
        self.assertIn("sync_lots(self.get_guild(SETTINGS.guild_id))", worker)
        self.assertLess(worker.index("SIM.run_due("), worker.index("sync_lots("))

    def test_the_feed_writes_only_message_ids(self):
        # No engine call, no gameplay table: the feed reads the lot and keeps
        # a message in step with it.
        for forbidden in ("authoritative_action(", "ENGINE.", "INSERT INTO", "UPDATE auctions"):
            self.assertNotIn(forbidden, FEED, forbidden)
        for allowed in ("DB.get_auction(", "DB.remember_auction_lot_message(", "DB.forget_auction_lot_message(", "DB.list_auction_lot_messages("):
            self.assertIn(allowed, FEED, allowed)

    def test_a_struck_lot_reads_sold_or_unsold(self):
        settle = _body(FEED, "sync_lots")
        # v0.34.1: a merchant that took the lot is a sale too - the seller was paid.
        self.assertIn('(lot.get("current_bidder_user_id") or lot.get("merchant_buyer"))', settle)
        self.assertIn('"sold" if struck else "unsold"', settle)
        self.assertIn('int(lot.get("active") or 0)', settle)
        self.assertIn("forget_auction_lot_message", settle)

    def test_an_anonymous_bidder_stays_anonymous_on_the_card(self):
        embed = _body(FEED, "lot_embed")
        self.assertIn('"Anonymous" if anonymous and bidder_id', embed)


class TheChannelsAreDashboardOwned(unittest.TestCase):
    def test_setup_creates_and_the_slash_path_binds(self):
        complete = _body(SETUP, "_run_complete_server_setup")
        self.assertIn("ensure_auction_house_channels(guild, category_name=SERVER_AUCTION_CATEGORY, create_missing=create_missing)", complete)
        realmhubs = _body(SETUP, "admin_realm_hubs")
        # v1.0.0-rc.51: this path used to forward the *realm-hub* category
        # parameter to the auctions, so a GM typing a category name into
        # `/admin server realmhubs` re-bound the auction rows to it.
        self.assertIn("ensure_auction_house_channels(guild, category_name=SERVER_AUCTION_CATEGORY)", realmhubs)
        self.assertNotIn("create_missing=True", realmhubs)

    def test_the_auctions_have_a_category_of_their_own(self):
        """Nine channels (five grand houses, four shared local floors) against
        four realm capitals: sharing one category made the capitals the
        minority in the category named after them."""
        self.assertIn('SERVER_AUCTION_CATEGORY = "', SETUP)
        auction_calls = [line for line in SETUP.splitlines() if "ensure_auction_house_channels(" in line]
        self.assertTrue(auction_calls, "nothing provisions the auction channels any more")
        for line in auction_calls:
            self.assertNotIn("SERVER_REALM_CATEGORY", line, "an auction channel is still made a capital's")
            self.assertNotIn("category_name=category_name", line, "a GM's typed category still re-homes the auctions")
        ensure = _body(CHANNELS, "ensure_auction_house_channels")
        self.assertNotIn("Realm Capitals", ensure, "the helper's own default still names the capitals")

    def test_an_existing_channel_is_moved_not_merely_rebound(self):
        """`category=` is read only on creation, so without this a deployed
        server keeps its auction channels under the capitals for ever and the
        split reaches a fresh guild only. Behind `can_create`, because Discord
        layout is dashboard-owned; once per channel, because forty-eight houses
        share nine of them and `category_id` is read from a cache the edit
        updates by gateway event."""
        ensure = _body(CHANNELS, "ensure_auction_house_channels")
        self.assertIn(
            "if can_create and category is not None and channel.id not in moved "
            "and channel.category_id != category.id:", ensure)
        self.assertIn("await channel.edit(category=category", ensure)

    def test_one_channel_per_house_gated_by_the_worlds_access_role(self):
        ensure = _body(CHANNELS, "ensure_auction_house_channels")
        self.assertIn("for house_id, house in WORLD.auction_houses.items():", ensure)
        self.assertIn("_ensure_realm_access_roles(guild)", ensure)
        self.assertIn("ensure_realm_hub_overwrites(guild, channel, access_roles.get(world))", ensure)
        self.assertIn("DB.set_auction_house_channel(", ensure)

    def test_the_dashboard_snapshot_lists_the_halls(self):
        self.assertIn('"auction_halls": auction_halls,', SETUP)
        app_js = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertIn("d.auction_halls||[]", app_js)
        self.assertIn("<h2>Auction Houses</h2>", app_js)

    def test_teardown_forgets_the_channels_and_cards(self):
        core = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
        clear = _body(core, "clear_discord_bindings")
        self.assertIn("DELETE FROM auction_house_channels WHERE guild_id=?", clear)
        self.assertIn("DELETE FROM auction_lot_messages WHERE guild_id=?", clear)


class TheMenuOpensEveryHub(unittest.TestCase):
    def test_menu_is_a_registered_root_on_the_tree(self):
        """Read off the tuple, never spelled out (v1.0.9).

        This asserted the tuple's exact literal text, so it broke the day
        `/locked` was added to it - a gate failing on correct code because the
        line it pins grew a member, which says nothing about whether `menu` is
        still registered. rc.43 already made this call for
        `test_commands_reach_a_player.py`: the tree tuple is read out of
        `surface.py` by AST rather than copied, because a copy is free to drift
        and a spelling is not the rule.
        """
        self.assertIn('name="menu"', SURFACE)
        # Imported rather than parsed since v1.0.12 - the tuple is a name now.
        # Still asserted before it is trusted (rc.57): an emptied tuple would
        # make the assertion below vacuous rather than red.
        registered = set(_surface_module().TREE_COMMANDS)
        self.assertTrue(registered, "the tree tuple is empty; the gate is broken, not the tree")
        self.assertIn(
            "menu", registered,
            "/menu is no longer registered on the command tree, so the one door into every hub "
            "is not a slash command any more",
        )

    def test_it_lists_every_hub_and_gates_admin(self):
        select = _body(SURFACE, "MenuSelect")
        self.assertIn("for definition in _HUB_DEFINITIONS", select)
        self.assertIn("if is_admin:", select)
        self.assertIn("await admin_panel.callback(interaction)", select)
        self.assertIn("send_hub(interaction, definition, status_provider=provider)", select)

    def test_the_menu_is_owner_only(self):
        view = _body(SURFACE, "MenuView")
        self.assertIn("interaction_check", view)
        self.assertIn("!= self.owner_id", view)


if __name__ == "__main__":
    unittest.main()

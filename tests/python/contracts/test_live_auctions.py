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
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
FEED = (BOT / "auction_feed.py").read_text(encoding="utf-8")
ECONOMY = (BOT / "commands" / "economy.py").read_text(encoding="utf-8")
BOT_PY = (BOT / "bot.py").read_text(encoding="utf-8")
CHANNELS = (BOT / "channels.py").read_text(encoding="utf-8")
SETUP = (BOT / "admin" / "server_setup.py").read_text(encoding="utf-8")
SURFACE = (BOT / "surface.py").read_text(encoding="utf-8")


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
        self.assertIn("settle_lots(self.get_guild(SETTINGS.guild_id))", worker)
        self.assertLess(worker.index("SIM.run_due("), worker.index("settle_lots("))

    def test_the_feed_writes_only_message_ids(self):
        # No engine call, no gameplay table: the feed reads the lot and keeps
        # a message in step with it.
        for forbidden in ("authoritative_action(", "ENGINE.", "INSERT INTO", "UPDATE auctions"):
            self.assertNotIn(forbidden, FEED, forbidden)
        for allowed in ("DB.get_auction(", "DB.remember_auction_lot_message(", "DB.forget_auction_lot_message(", "DB.list_auction_lot_messages("):
            self.assertIn(allowed, FEED, allowed)

    def test_a_struck_lot_reads_sold_or_unsold(self):
        settle = _body(FEED, "settle_lots")
        self.assertIn('"sold" if lot.get("current_bidder_user_id")', settle)
        self.assertIn('int(lot.get("active") or 0)', settle)
        self.assertIn("forget_auction_lot_message", settle)

    def test_an_anonymous_bidder_stays_anonymous_on_the_card(self):
        embed = _body(FEED, "lot_embed")
        self.assertIn('"Anonymous" if anonymous and bidder_id', embed)


class TheChannelsAreDashboardOwned(unittest.TestCase):
    def test_setup_creates_and_the_slash_path_binds(self):
        complete = _body(SETUP, "_run_complete_server_setup")
        self.assertIn("ensure_auction_house_channels(guild, category_name=SERVER_REALM_CATEGORY, create_missing=create_missing)", complete)
        realmhubs = _body(SETUP, "admin_realm_hubs")
        self.assertIn("ensure_auction_house_channels(guild, category_name=category_name)", realmhubs)
        self.assertNotIn("create_missing=True", realmhubs)

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
        self.assertIn('name="menu"', SURFACE)
        self.assertIn('("begin", "me", "quests", "action", "check", "admin", "menu")', SURFACE)

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

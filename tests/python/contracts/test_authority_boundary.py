from pathlib import Path
import ast

from tests.support import PROJECT_ROOT, bot_class_source, bot_package_source


BOT_DIR = PROJECT_ROOT / "app" / "bot"
# Phase 1 of the main.py split (v0.19.33): SOURCE is the whole package and the
# class bodies below are located by name, so the creation/exploration UI
# moving out of main.py (plan phases 8-9) cannot silently drop these checks.
SOURCE = bot_package_source()

FUNCTIONS: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
FUNCTION_SOURCES: dict[str, str] = {}
for path in sorted(BOT_DIR.rglob("*.py")):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            FUNCTIONS[node.name] = node
            FUNCTION_SOURCES[node.name] = source


def _source(name: str) -> str:
    node = FUNCTIONS[name]
    return ast.get_source_segment(FUNCTION_SOURCES[name], node) or ""


def _calls(name: str, call_name: str) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == call_name
        for child in ast.walk(FUNCTIONS[name])
    )


def _strings(name: str) -> set[str]:
    return {
        child.value
        for child in ast.walk(FUNCTIONS[name])
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def test_python_go_authority_boundary_only_delegates_migrated_mechanics():
    expected_operations = {
        "check": "check.resolve",
        "_resolve_scene_action": "scene.action",
        "perfect_start": "perfection.start",
        "perfect_quest": "perfection.quest",
        "perfect_trial": "perfection.trial",
        "perfect_abandon": "perfection.abandon",
        "bodyperfect_start": "perfection.body_start",
        "bodyperfect_quest": "perfection.body_quest",
        "bodyperfect_trial": "perfection.body_trial",
        "bodyperfect_abandon": "perfection.body_abandon",
        "law_comprehend": "law.comprehend",
        "condition_treat": "condition.treat",
        "tribulation_prepare": "tribulation.prepare",
        "tribulation_attempt": "tribulation.attempt",
        "sense_command": "sense.inspect",
        "conceal_command": "sense.conceal",
        "explore": "exploration.explore",
        "hunt": "exploration.hunt",
        "travel": "exploration.travel",
        "realmhub_go": "exploration.travel",
        "secret_status": "secret_realm.status",
        "secret_enter": "secret_realm.enter",
        "secret_explore": "secret_realm.explore",
        "secret_leave": "secret_realm.leave",
        "begin": "character.family_options",
        "birth_family_enter": "family.household.enter",
        "birth_family_leave": "family.household.leave",
        "alchemy_forage": "forage.resolve",
        "_run_crafting": "craft.resolve",
        "beast_tame": "beast.tame",
        "beast_feed": "beast.feed",
        "beast_train": "beast.train",
        "beast_evolve": "beast.evolve",
        "beast_active": "beast.active",
        "artifact_bond": "artifact.bond",
        "artifact_awaken": "artifact.awaken",
        "birth_family_child": "family.add_child",
        "use_item_command": "item.use",  # v0.21.0
    }
    for function_name, operation in expected_operations.items():
        assert function_name in FUNCTIONS, function_name
        assert operation in _strings(function_name), (function_name, operation)

    no_python_rolls = {
        "check",
        "_resolve_scene_action",
        "perfect_start",
        "perfect_quest",
        "perfect_trial",
        "perfect_abandon",
        "bodyperfect_start",
        "bodyperfect_quest",
        "bodyperfect_trial",
        "bodyperfect_abandon",
        "law_comprehend",
        "condition_treat",
        "tribulation_prepare",
        "tribulation_attempt",
        "sense_command",
        "conceal_command",
        "explore",
        "hunt",
        "secret_enter",
        "secret_explore",
        "secret_leave",
        "birth_family_enter",
        "birth_family_leave",
        "alchemy_forage",
        "_run_crafting",
        "beast_tame",
        "beast_feed",
        "beast_train",
        "beast_evolve",
        "beast_active",
        "artifact_bond",
        "artifact_awaken",
        "birth_family_child",
    }
    for function_name in no_python_rolls:
        assert not _calls(function_name, "roll_2d10"), function_name

    forbidden_by_handler = {
        "explore": (
            "WORLD.random_encounter",
            "WORLD.random_explore_rewards",
            "WORLD.roll_unexpected_event",
            "_discover_next_location",
            "CULTIVATION.reward",
            "DB.set_cooldown",
            "secrets.",
        ),
        "hunt": (
            "WORLD.random_hunt",
            "CULTIVATION.reward",
            "DB.set_cooldown",
            "DB.add_spirit_beast",
            "DB.create_wild_beast_encounter",
            "secrets.",
        ),
        "travel": ("DB.set_location",),
        "realmhub_go": ("DB.set_location",),
        "secret_enter": (
            "CULTIVATION.reward",
            "DB.start_secret_realm_run",
            "DB.advance_secret_realm_run",
            "DB.set_secret_realm_danger",
            "DB.grant_inheritance",
            "DB.leave_secret_realm",
            "DB.set_cooldown",
        ),
        "secret_explore": (
            "CULTIVATION.reward",
            "DB.start_secret_realm_run",
            "DB.advance_secret_realm_run",
            "DB.set_secret_realm_danger",
            "DB.grant_inheritance",
            "DB.leave_secret_realm",
            "DB.set_cooldown",
        ),
        "secret_leave": (
            "CULTIVATION.reward",
            "DB.start_secret_realm_run",
            "DB.advance_secret_realm_run",
            "DB.set_secret_realm_danger",
            "DB.grant_inheritance",
            "DB.leave_secret_realm",
            "DB.set_cooldown",
        ),
        "secret_status": ("DB.get_secret_realm_run", "DB.get_active_world_events"),
        "birth_family_enter": ("DB.set_location",),
        "birth_family_leave": ("DB.set_location",),
        "alchemy_forage": (
            "SIM.alchemy_forage_profile",
            "DB.get_abode_by_location",
            "DB.can_access_abode",
            "DB.get_birth_family",
            '"context_bonus":',
            '"effect_bonus":',
            '"location":',
            '"realm_index":',
            '"garden_level":',
            '"cooldown_seconds":',
            '"tn":',
            '"spirit_resources":',
            '"loot":',
            '"rare_found":',
            "secrets.",
        ),
        "_run_crafting": (
            "current_effect_modifiers(",
            "DB.get_abode_by_location",
            "DB.can_access_abode",
            "DB.get_birth_family",
            "DB.get_member_sect_manor",
            "DB.get_profession_progress",
            '"context_bonus":',
            '"location":',
            '"game_minute":',
        ),
        "beast_tame": (
            "DB.cooldown_remaining",
            "current_world_time",
            "DB.get_wild_beast_encounters",
            '"game_minute":',
            '"cooldown_seconds":',
            '"location":',
            '"context_bonus":',
        ),
        "beast_feed": (
            "DB.get_spirit_beasts",
            "DB.cooldown_remaining",
            '"cooldown_seconds":',
            '"context_bonus":',
        ),
        "beast_train": (
            "DB.cooldown_remaining",
            "DB.get_abode_by_location",
            "DB.can_access_abode",
            '"context_bonus":',
            '"cooldown_seconds":',
            '"beast_pen_level":',
        ),
        "beast_evolve": (
            "DB.get_spirit_beasts",
            "DB.cooldown_remaining",
            '"game_minute":',
            '"location":',
            '"context_bonus":',
        ),
        "beast_active": (
            "DB.get_spirit_beasts",
            '"game_minute":',
            '"location":',
            '"context_bonus":',
        ),
        "artifact_bond": (
            "DB.get_inventory",
            "DB.cooldown_remaining",
            '"cooldown_seconds":',
            '"context_bonus":',
            '"game_minute":',
            '"location":',
        ),
        "artifact_awaken": (
            "DB.get_artifact_bonds",
            '"game_minute":',
            '"location":',
            '"context_bonus":',
        ),
        "birth_family_child": (
            "inherited_root",
            '"spiritual_root":',
            '"realm_index":',
            '"phase":',
            "secrets.",
        ),
    }
    for function_name, forbidden_tokens in forbidden_by_handler.items():
        body = _source(function_name)
        for token in forbidden_tokens:
            assert token not in body, (function_name, token)

    forage_body = _source("alchemy_forage")
    assert "current_effect_modifiers(" not in forage_body
    assert '"effect_bonus"' not in forage_body

    effect_reader_body = _source("current_effect_modifiers")
    assert 'ENGINE.action("effects.current"' in effect_reader_body
    for forbidden in (
        "DB.get_alchemy_state",
        "DB.apply_effect",
        "DB.remove_effect",
    ):
        assert forbidden not in effect_reader_body

    # v0.23.0: the Python mirror of the toxicity curve is gone entirely, not
    # merely unused by this one reader. The engine settles the shared effect
    # row wherever it reads the effect table, so nothing on the Discord side
    # needs to remember to sync it - and nothing may start again.
    assert "sync_pill_toxicity_effect" not in SOURCE
    assert "medicine_toxicity_effect" not in SOURCE

    assert "async def _discover_next_location" not in SOURCE
    assert "sense.status" in SOURCE
    assert "await DB.set_concealment" not in SOURCE
    assert "spiritual_sense_stats(c)" not in SOURCE
    # Character creation must consume an opaque Go-generated family offer.
    begin_body = _source("begin")
    modal_body = bot_class_source("CharacterModal")
    assert "generate_family_options" not in begin_body
    assert '"family_choice_id"' in modal_body
    assert '"family": family' not in modal_body
    assert "offer_state_version" in modal_body

    # Personal exploration-event controls may present Go outcomes, but must not
    # reintroduce Python-side rolls, rewards, effects, Karma, or Fate authority.
    event_view_body = bot_class_source("ExplorationEventView")
    assert "exploration.event.act" in event_view_body
    assert "exploration.event.leave" in event_view_body
    assert "exploration.event.status" in event_view_body
    for token in (
        "roll_2d10(",
        "CULTIVATION.reward",
        "DB.apply_effect",
        "DB.adjust_karma",
        "DB.adjust_fate",
    ):
        assert token not in event_view_body, token




def test_python_gameplay_purge_is_a_one_way_authority_boundary():
    database_source = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
    database_tree = ast.parse(database_source)
    database_class = next(node for node in database_tree.body if isinstance(node, ast.ClassDef) and node.name == "Database")
    database_methods = {
        node.name for node in database_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    removed_go_owned_mutators = {
        "create_character", "discover_location", "learn_manual", "practice_manual",
        "record_world_event_action", "apply_battle_damage", "train_aptitude", "harmonize_aptitude",
        "spend_fate", "reward_master_for_disciple_breakthrough", "start_seclusion", "advance_seclusion",
        "record_true_death", "reincarnate_character", "set_condition_severity",
        "add_tribulation_preparation", "record_tribulation_attempt", "record_profession_practice",
        "record_alchemy_batch", "resolve_crime", "add_spirit_beast", "train_spirit_beast",
        "evolve_spirit_beast", "set_active_spirit_beast", "create_wild_beast_encounter",
        "resolve_wild_beast_taming", "bond_artifact", "awaken_artifact", "create_pvp_challenge",
        "respond_pvp_challenge", "apply_pvp_turn", "damage_equipment",
        "advance_territory_occupations", "advance_caravans", "advance_world_era",
        "set_concealment", "add_spiritual_sense_bonus", "reward_body_cultivation", "reward",
        "consume_and_create", "apply_breakthrough", "apply_body_breakthrough", "upsert_npc_mind_state",
        "set_player_scene_state", "update_npc_relationship", "update_quest_progress",
        "start_perfection", "add_perfection_training", "add_perfection_preparation",
        "complete_perfection_quest", "reduce_perfection_progress", "complete_perfection",
        "abandon_perfection", "start_body_perfection", "add_body_perfection_training",
        "add_body_perfection_preparation", "complete_body_perfection_quest",
        "reduce_body_perfection_progress", "complete_body_perfection", "abandon_body_perfection",
        "claim_world_event", "start_secret_realm_run", "advance_secret_realm_run",
        "set_secret_realm_danger", "leave_secret_realm", "grant_inheritance",
        "apply_vitality_damage", "save_root_profile", "save_bloodline_profile",
        "save_physique_profile", "add_law_comprehension", "update_battle",
        "claim_battle_finalization", "finalize_expired_auctions", "add_auction_door_risk",
        "get_pending_auction_door_risks", "rotate_black_market", "consume_auction_door_risk", "create_player_family", "invite_player_family_member",
        "accept_player_family_invite", "decline_player_family_invite",
        "reorder_player_family_member", "leave_player_family", "set_life_status",
        "create_family_child", "set_child_awakening", "end_seclusion", "advance_samsara_time",
        "awaken_soul_memory", "apply_condition", "add_grudge", "adjust_social_state",
        "ensure_territory", "settle_player_caravans", "start_world_era",
        "advance_territory_wars", "advance_advanced_world_systems", "ensure_sect",
        "claim_periodic_bucket", "spend_currency", "adjust_karma", "add_currency",
        "admin_teleport_character", "admin_revive_character", "admin_clear_battle",
        "advance_world_clock", "set_automation_setting", "adjust_fate", "adjust_reputation", "record_crime",
    }
    assert removed_go_owned_mutators.isdisjoint(database_methods)

    simulation_source = (PROJECT_ROOT / "app" / "simulation" / "world.py").read_text(encoding="utf-8")
    simulation_tree = ast.parse(simulation_source)
    world_class = next(node for node in simulation_tree.body if isinstance(node, ast.ClassDef) and node.name == "WorldSimulator")
    world_methods = {
        node.name for node in world_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    removed_python_simulation_mutators = {
        "_simulate_black_markets", "_mark_system_run", "_simulate_civilization", "_simulate_npc_lives",
        "_simulate_economy", "_simulate_sects", "_simulate_clans", "apply_player_action",
        "apply_forbidden_art_use", "apply_random_event", "market_trade",
        "alchemy_forage_profile",
    }
    assert removed_python_simulation_mutators.isdisjoint(world_methods)
    assert "ensure_all_clans" not in world_methods
    assert "_npc_mood" not in simulation_source
    assert "secrets." not in simulation_source
    birthfamily_source = (PROJECT_ROOT / "app" / "rules" / "birthfamily.py").read_text(encoding="utf-8")
    assert "def inherited_root" not in birthfamily_source
    init_node = next(node for node in world_class.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "initialize")
    init_source = ast.get_source_segment(simulation_source, init_node) or ""
    assert "bootstrap_simulation" in init_source
    assert "._connect(" not in init_source
    interval_node = next(node for node in world_class.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "set_interval_days")
    interval_source = ast.get_source_segment(simulation_source, interval_node) or ""
    assert "admin.simulation.interval" in interval_source
    assert "._connect(" not in interval_source
    for entrypoint in ("run_due", "force_run"):
        node = next(node for node in world_class.body if isinstance(node, ast.AsyncFunctionDef) and node.name == entrypoint)
        body = ast.get_source_segment(simulation_source, node) or ""
        assert "requires the Go engine for simulation mutations" in body
        assert "._connect(" not in body

    init_method = next(node for node in world_class.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    engine_arg = next(arg for arg in init_method.args.kwonlyargs if arg.arg == "engine")
    engine_index = init_method.args.kwonlyargs.index(engine_arg)
    assert init_method.args.kw_defaults[engine_index] is None

    engine_source = (PROJECT_ROOT / "app" / "ops" / "game_engine.py").read_text(encoding="utf-8")
    engine_tree = ast.parse(engine_source)
    engine_class = next(node for node in engine_tree.body if isinstance(node, ast.ClassDef) and node.name == "GameEngineClient")
    bootstrap = next(node for node in engine_class.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "bootstrap_simulation")
    bootstrap_source = ast.get_source_segment(engine_source, bootstrap) or ""
    assert '"game_minute"' in bootstrap_source
    assert '"npcs"' not in bootstrap_source
    assert '"sects"' not in bootstrap_source

    core_services_source = (PROJECT_ROOT / "app" / "ops" / "core_services.py").read_text(encoding="utf-8")
    assert "class CultivationService" not in core_services_source
    assert "class SectService" not in core_services_source

    bot_source = SOURCE
    assert "_apply_event_participation" not in bot_source
    assert "CultivationService" not in bot_source
    assert "SectService" not in bot_source
    assert "ForbiddenArtsService" not in bot_source
    assert "FORBIDDEN_ARTS" not in bot_source
    assert not (PROJECT_ROOT / "app" / "forbidden_arts.py").exists()
    assert '"caravan.settle"' in bot_source
    for operation in (
        "admin.player.karma", "admin.player.grant_currency", "admin.world.advance_time",
        "admin.player.teleport", "admin.player.revive", "admin.player.clear_battle",
        "admin.automation.set",
    ):
        assert operation in bot_source

    test_support = (PROJECT_ROOT / "tests" / "support.py").read_text(encoding="utf-8")
    assert "async def seed_character(" in test_support
    for test_path in (PROJECT_ROOT / "tests").rglob("*.py"):
        if test_path.name == "support.py" or test_path == Path(__file__):
            continue
        assert ".create_character(" not in test_path.read_text(encoding="utf-8"), test_path


def test_database_methods_do_not_call_missing_database_methods():
    database_source = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
    database_tree = ast.parse(database_source)
    database_class = next(node for node in database_tree.body if isinstance(node, ast.ClassDef) and node.name == "Database")
    methods = {
        node.name for node in database_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing_calls: list[tuple[str, str]] = []
    for node in database_class.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "self"
                and child.func.attr not in methods
                and not child.func.attr.startswith("_")
            ):
                missing_calls.append((node.name, child.func.attr))
    assert missing_calls == []


def test_wild_beast_encounter_reader_is_read_only_and_filters_expiry():
    database_source = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
    database_tree = ast.parse(database_source)
    database_class = next(
        node for node in database_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    method = next(
        node for node in database_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "get_wild_beast_encounters"
    )
    method_source = ast.get_source_segment(database_source, method) or ""
    assert "UPDATE wild_beast_encounters" not in method_source
    assert ".commit(" not in method_source
    assert "expires_game_minute>?" in method_source


def _authoritative_action_payload_keys(name: str) -> set[str]:
    # Only the dict literal actually passed as the payload argument to
    # ENGINE.authoritative_action - not every string constant in the
    # function, which would also catch legitimate dict-key reads of Go's
    # *returned* roll data (e.g. roll.get('modifier'), roll.get('tn')) for
    # display and false-flag on those.
    keys: set[str] = set()
    for node in ast.walk(FUNCTIONS[name]):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "authoritative_action"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    keys.update(
                        k.value for k in arg.keys if isinstance(k, ast.Constant)
                    )
    return keys


def test_sect_recruitment_does_not_send_caller_computed_rolls():
    # sect.recruitment.recommendation and sect.recruitment.trial used to accept
    # a client-supplied modifier/tn/bonus (recommendation) and
    # primary_modifier/secondary_modifier/base_tn (trial), letting a forged
    # authoritative_action call guarantee passing any sect trial. Go now
    # derives all of these from canonical character/reputation state; the
    # caller sends only identifiers and flavor-text details.
    forbidden = {
        "modifier", "tn", "bonus",
        "primary_modifier", "secondary_modifier", "base_tn",
    }
    for fn in ("sect_recruitment_recommendation", "sect_recruitment_trial"):
        hit = forbidden & _authoritative_action_payload_keys(fn)
        assert not hit, f"{fn} still sends a forged-roll payload field: {hit}"


# ---------------------------------------------------------------------------
# v0.21 gate (docs/ROADMAP_1_0.md, "Authority I"): every DB mutator call site
# reachable from app/bot/ and app/ops/ is listed here. PLAYER_MUTATIONS is the
# milestone's backlog - each entry names the engine action that replaces it,
# and the milestone closes when the dict is empty. BOOKKEEPING_METHODS are the
# writes that are not gameplay outcomes (narration history, RAG memory, thread
# and channel ids, ops telemetry, the GM's quest-draft review) and stay in
# Python. A mutator that is in neither set fails the gate: a new gameplay
# write cannot be added from Python without appearing here, and a migrated one
# cannot stay here once its row is deleted.
# ---------------------------------------------------------------------------

import re

APP_DIR = PROJECT_ROOT / "app"
WRITE_SQL = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.I | re.M)

# Empty since v0.23.0: the Authority I milestone is closed. Nothing under
# app/bot or app/ops writes a gameplay table any more, and this dict exists to
# keep it that way - a new gameplay write from Python fails the gate below
# rather than quietly joining a backlog.
#
# How it emptied, for anyone reading the shape of the migration:
#   v0.21.0  the five use_item_command rows -> `item.use`
#   v0.22.0  accept_quest -> `commission.accept`
#   v0.23.0  four alchemy/toxicity rows -> `alchemy.purge`, plus an engine-side
#            settle wherever the effect table is read
#            eight admin rows -> `admin.player.*` / `admin.world.spawn_realm`
#            set_gender -> `character.set_gender`
#            two discover_sect rows -> a batched `sect.discover`
#            abode set_location -> `sect.abode.enter` / `sect.abode.leave`
#            the law technique effect -> `law.technique`
#            four `/sect shadow` rows -> `sect.shadow`
PLAYER_MUTATIONS: dict[tuple[str, str, str], str] = {}

BOOKKEEPING_METHODS = {
    # narration and memory
    "add_history", "add_rag_memory", "add_npc_player_memory", "set_npc_memory", "record_world_history_event",
    # reads whose bodies also expire stale rows
    "get_active_world_events", "get_alchemy_state", "get_secret_realm_run", "get_social_state", "get_world_clock",
    "list_npc_player_memories",
    # Discord ids: channels, messages, threads
    "set_channel_message", "set_server_channels", "set_info_message_id", "set_bugs_channel_id", "set_realm_hub_channel",
    "set_expedition_thread", "set_birth_family_household_thread", "set_sect_abode_thread", "set_abode_thread",
    "register_event_thread", "close_event_thread", "ensure_sect_abode", "update_expedition_location",
    "clear_discord_bindings",  # v0.21.2 teardown: forgets channel/message ids, touches no gameplay column
    # ops telemetry, startup, maintenance
    "init", "sync_world_catalog", "sync_rag_canon", "record_startup_event", "record_operational_alert",
    # v0.22.0: seeds the authored commission pool from content/world.json into
    # quest_definitions at startup, insert-only. Content, not player state -
    # the same class of write as sync_world_catalog beside it.
    "sync_commission_pool",
    "flush_slow_query_log", "maintenance_cleanup", "log_admin_action",
    # cosmetic / GM review of drafts (no gameplay table)
    "set_address_style", "set_quest_definition_status",
}


def _database_mutators() -> set[str]:
    names = set()
    for path in sorted((APP_DIR / "database").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for fn in cls.body:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                    isinstance(c, ast.Constant) and isinstance(c.value, str) and WRITE_SQL.search(c.value)
                    for c in ast.walk(fn)
                ):
                    names.add(fn.name)
    return names


def _mutator_call_sites(mutators: set[str]) -> set[tuple[str, str, str]]:
    sites = set()
    for package in ("bot", "ops"):
        for path in sorted((APP_DIR / package).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(fn):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                        continue
                    if node.func.attr not in mutators:
                        continue
                    base = node.func.value
                    via_db = (isinstance(base, ast.Name) and base.id in ("DB", "db")) or (
                        isinstance(base, ast.Attribute) and base.attr in ("db", "_db", "database")
                    )
                    if via_db:
                        sites.add((str(path.relative_to(APP_DIR)).replace("\\", "/"), fn.name, node.func.attr))
    return sites


def test_the_mutator_scan_still_sees_the_database_layer():
    mutators = _database_mutators()
    assert len(mutators) >= 60, sorted(mutators)
    for name in ("consume_item", "apply_effect", "set_npc_memory", "add_history"):
        assert name in mutators


def test_v0_21_gate_every_db_write_from_bot_and_ops_is_allowlisted():
    sites = _mutator_call_sites(_database_mutators())
    gameplay = {site for site in sites if site[2] not in BOOKKEEPING_METHODS}
    unlisted = gameplay - set(PLAYER_MUTATIONS)
    assert not unlisted, (
        "DB writes from Python that are neither a listed v0.21 row nor bookkeeping "
        "(gameplay outcomes belong in an engine action):\n" + "\n".join(f"  {s}" for s in sorted(unlisted))
    )
    gone = set(PLAYER_MUTATIONS) - gameplay
    assert not gone, (
        "v0.21 rows no longer present in the code - delete them from PLAYER_MUTATIONS "
        "(and record the engine action that replaced them in the release notes):\n"
        + "\n".join(f"  {s}" for s in sorted(gone))
    )
    stale = BOOKKEEPING_METHODS - {site[2] for site in sites}
    assert not stale, f"bookkeeping methods no longer called from bot/ops: {sorted(stale)}"


def test_the_v0_21_backlog_stays_closed():
    # The milestone closed at v0.23.0 with this empty, and empty is now the
    # contract rather than a target: a Python-side gameplay write cannot be
    # added back by listing it here, only by moving it into the engine. The
    # scan above is what enforces that; this is the statement of intent.
    assert PLAYER_MUTATIONS == {}, (
        "Authority I is closed - a new Python-side gameplay write belongs in an "
        "engine action, not in this allowlist: " + ", ".join(map(str, PLAYER_MUTATIONS))
    )

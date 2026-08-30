from pathlib import Path
import ast

from tests.support import PROJECT_ROOT


SOURCE = (PROJECT_ROOT / "app" / "bot" / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
FUNCTIONS = {
    node.name: node
    for node in ast.walk(TREE)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}


def _source(name: str) -> str:
    node = FUNCTIONS[name]
    return ast.get_source_segment(SOURCE, node) or ""


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
        "sync_pill_toxicity_effect",
    ):
        assert forbidden not in effect_reader_body

    assert "async def _discover_next_location" not in SOURCE
    assert "sense.status" in SOURCE
    assert "await DB.set_concealment" not in SOURCE
    assert "spiritual_sense_stats(c)" not in SOURCE
    # Character creation must consume an opaque Go-generated family offer.
    begin_body = _source("begin")
    modal_body = SOURCE[SOURCE.index("class CharacterModal"):SOURCE.index("def _birth_family_preview_embed")]
    assert "generate_family_options" not in begin_body
    assert '"family_choice_id"' in modal_body
    assert '"family": family' not in modal_body
    assert "offer_state_version" in modal_body

    # Personal exploration-event controls may present Go outcomes, but must not
    # reintroduce Python-side rolls, rewards, effects, Karma, or Fate authority.
    event_view_body = SOURCE[
        SOURCE.index("class ExplorationEventView"):
        SOURCE.index('@registered_root_command(name="explore"')
    ]
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
    birthfamily_source = (PROJECT_ROOT / "app" / "birthfamily.py").read_text(encoding="utf-8")
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

    engine_source = (PROJECT_ROOT / "app" / "game_engine.py").read_text(encoding="utf-8")
    engine_tree = ast.parse(engine_source)
    engine_class = next(node for node in engine_tree.body if isinstance(node, ast.ClassDef) and node.name == "GameEngineClient")
    bootstrap = next(node for node in engine_class.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "bootstrap_simulation")
    bootstrap_source = ast.get_source_segment(engine_source, bootstrap) or ""
    assert '"game_minute"' in bootstrap_source
    assert '"npcs"' not in bootstrap_source
    assert '"sects"' not in bootstrap_source

    core_services_source = (PROJECT_ROOT / "app" / "core_services.py").read_text(encoding="utf-8")
    assert "class CultivationService" not in core_services_source
    assert "class SectService" not in core_services_source

    bot_source = (PROJECT_ROOT / "app" / "bot" / "main.py").read_text(encoding="utf-8")
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

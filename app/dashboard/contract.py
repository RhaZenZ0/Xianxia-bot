from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable

# This module is deliberately stdlib-only so the dashboard implementation gate can
# run before the bot/dashboard dependencies are installed.  Keep the browser/API,
# schema-review, and newer-system coverage contract in one place.
DASHBOARD_API_VERSION = 2
DASHBOARD_REVIEWED_SCHEMA_VERSION = 35

DASHBOARD_GET_API_PATHS = frozenset({
    "/api/overview", "/api/capabilities", "/api/timeline", "/api/npcs", "/api/npc",
    "/api/families", "/api/sects", "/api/conflicts", "/api/events", "/api/players", "/api/player",
    "/api/cultivation", "/api/crafting", "/api/exploration", "/api/commissions", "/api/quests",
    "/api/economy", "/api/dynasties",
    "/api/party", "/api/pvp", "/api/conditions", "/api/threads",
    "/api/rag", "/api/decisions", "/api/admin", "/api/discord", "/api/health",
    "/api/narration",
    "/api/ai_routing",
})

DASHBOARD_POST_API_PATHS = frozenset({
    "/api/admin/action",
    "/api/discord/action",
    "/api/narration/action",
})

DASHBOARD_VIEW_ENDPOINTS = {
    "overview": "/api/overview",
    "timeline": "/api/timeline",
    "npcs": "/api/npcs",
    "families": "/api/families",
    "sects": "/api/sects",
    "conflicts": "/api/conflicts",
    "events": "/api/events",
    "players": "/api/players",
    "cultivation": "/api/cultivation",
    "crafting": "/api/crafting",
    "exploration": "/api/exploration",
    "commissions": "/api/commissions",
    "quests": "/api/quests",
    "economy": "/api/economy",
    "dynasties": "/api/dynasties",
    "party": "/api/party",
    "pvp": "/api/pvp",
    "conditions": "/api/conditions",
    "threads": "/api/threads",
    "rag": "/api/rag",
    "decisions": "/api/decisions",
    "discord": "/api/discord",
    "ai_routing": "/api/ai_routing",
    "narration": "/api/narration",
    "admin": "/api/admin",
}

# Tables belonging to newer gameplay systems that must be deliberately surfaced
# by the matching dashboard view.  A schema bump also trips the review marker
# above, forcing this registry to be reviewed before release.
DASHBOARD_SYSTEM_TABLES = {
    "families": (
        "birth_family_household_threads",
    ),
    "cultivation": (
        "character_spiritual_roots", "character_bloodlines", "character_physiques", "dao_progress",
        "law_progress", "tribulation_state", "tribulation_attempts", "realm_perfection",
        "body_realm_perfection", "seclusion_sessions",
    ),
    "crafting": (
        "profession_progress", "alchemy_state", "alchemy_batches", "spirit_beasts", "artifact_bonds",
        "cave_abodes", "sect_abodes", "personal_worlds", "deployed_location_arrays", "equipment_instances",
    ),
    "exploration": (
        "exploration_events", "exploration_event_participants", "secret_realm_runs",
        "character_location_discoveries", "wild_beast_encounters", "caravans", "caravan_operations",
        "expedition_threads",
    ),
    # Commissions (v0.22.0, schema 29). This view owns the player-side table -
    # which commission someone is carrying, on what terms, and until when. The
    # definition table moved to `quests` in v0.24.0.
    "commissions": (
        "character_quests",
    ),
    # Quests (v0.24.0, schema 32). The workbench owns the definition table -
    # every producer of a quest, in one place, with the same review controls -
    # so `quest_definitions` moves here from `exploration`, which showed forged
    # ones read-only and could not act on any of them. `character_quests` stays
    # registered to `commissions`, which owns the player-side view of a held
    # one; this view reads it only to count who is carrying what.
    "quests": (
        "quest_definitions",
    ),
    "economy": (
        "economy_markets", "economy_events", "auctions", "auction_bids", "black_market_posts",
        "black_market_stock", "crime_records",
    ),
    "dynasties": (
        "reincarnation_state", "soul_legacy", "samsara_dynasty_history", "samsara_ancestral_leads",
        "samsara_investigation_quests", "samsara_dynasty_claims", "samsara_dynasty_conflicts",
    ),
    "party": (
        "parties", "party_members", "party_formations", "formation_positions",
    ),
    "pvp": (
        "pvp_challenges", "pvp_matches",
    ),
    "conditions": (
        "character_conditions",
    ),
}


def _api_refs(js: str) -> set[str]:
    return {
        match.split("?", 1)[0]
        for match in re.findall(r"/api/[A-Za-z0-9_./?=&${}-]+", js)
    }


def _loader_map(js: str) -> dict[str, str]:
    match = re.search(r"const\s+loaders\s*=\s*\{([^}]+)\}\s*;", js, flags=re.DOTALL)
    if not match:
        return {}
    return dict(re.findall(r"([a-z_]+)\s*:\s*(load[A-Za-z0-9_]+)", match.group(1)))


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _javascript_async_function_segments(js: str) -> dict[str, str]:
    matches = list(re.finditer(r"async\s+function\s+([A-Za-z0-9_]+)\s*\(", js))
    segments: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(js)
        segments[match.group(1)] = js[match.start():end]
    return segments


def _store_method_sources(server: str) -> dict[str, str]:
    tree = ast.parse(server)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ReadOnlyDashboardStore":
            methods: dict[str, str] = {}
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[child.name] = ast.get_source_segment(server, child) or ""
            return methods
    return {}


def dashboard_implementation_issues(root: Path, *, schema_version: int) -> list[str]:
    """Return release-blocking dashboard implementation gaps.

    This is the standard static gate for dashboard work.  It intentionally checks
    both directions: browser -> API and API/view registry -> browser, plus actual
    HTTP route branches and the schema review marker.  Any schema bump forces an
    explicit dashboard review instead of silently letting new systems ship unseen.
    """
    root = Path(root)
    html_path = root / "dashboard" / "index.html"
    js_path = root / "dashboard" / "app.js"
    server_path = root / "app" / "dashboard" / "server.py"
    missing_files = [str(p.relative_to(root)) for p in (html_path, js_path, server_path) if not p.is_file()]
    if missing_files:
        return ["missing dashboard contract file: " + name for name in missing_files]

    html = html_path.read_text(encoding="utf-8")
    js = js_path.read_text(encoding="utf-8")
    server = server_path.read_text(encoding="utf-8")
    issues: list[str] = []

    if int(schema_version) != DASHBOARD_REVIEWED_SCHEMA_VERSION:
        issues.append(
            "schema/dashboard review mismatch: "
            f"schema={schema_version}, dashboard_reviewed={DASHBOARD_REVIEWED_SCHEMA_VERSION}; "
            "review dashboard coverage for the new schema and update DASHBOARD_REVIEWED_SCHEMA_VERSION"
        )

    view_keys = set(DASHBOARD_VIEW_ENDPOINTS)
    view_endpoints = set(DASHBOARD_VIEW_ENDPOINTS.values())
    if not view_endpoints <= DASHBOARD_GET_API_PATHS:
        issues.append("dashboard view endpoints missing from GET registry: " + ", ".join(sorted(view_endpoints - DASHBOARD_GET_API_PATHS)))

    system_keys = set(DASHBOARD_SYSTEM_TABLES)
    if not system_keys <= view_keys:
        issues.append("newer-system coverage has no dashboard view: " + ", ".join(sorted(system_keys - view_keys)))
    empty_systems = sorted(name for name, tables in DASHBOARD_SYSTEM_TABLES.items() if not tables)
    if empty_systems:
        issues.append("dashboard system table registry is empty for: " + ", ".join(empty_systems))
    duplicate_tables = _duplicates(table for tables in DASHBOARD_SYSTEM_TABLES.values() for table in tables)
    if duplicate_tables:
        issues.append("dashboard system tables assigned to multiple views: " + ", ".join(duplicate_tables))

    refs = _api_refs(js)
    get_refs = refs - DASHBOARD_POST_API_PATHS
    post_refs = refs & DASHBOARD_POST_API_PATHS
    unknown_get = sorted(get_refs - DASHBOARD_GET_API_PATHS)
    if unknown_get:
        issues.append("frontend references unregistered GET APIs: " + ", ".join(unknown_get))
    unknown_post = sorted(post_refs - DASHBOARD_POST_API_PATHS)
    if unknown_post:
        issues.append("frontend references unregistered POST APIs: " + ", ".join(unknown_post))
    unconsumed_views = sorted(view_endpoints - get_refs)
    if unconsumed_views:
        issues.append("registered dashboard views have no frontend consumer: " + ", ".join(unconsumed_views))

    nav_views = set(re.findall(r'data-view="([a-z_]+)"', html))
    loaders = _loader_map(js)
    loader_views = set(loaders)
    if nav_views != loader_views:
        missing_loaders = sorted(nav_views - loader_views)
        dead_loaders = sorted(loader_views - nav_views)
        if missing_loaders:
            issues.append("dashboard navigation has no loader: " + ", ".join(missing_loaders))
        if dead_loaders:
            issues.append("dashboard loaders have no navigation tab: " + ", ".join(dead_loaders))
    if nav_views != view_keys:
        missing_registry = sorted(nav_views - view_keys)
        dead_registry = sorted(view_keys - nav_views)
        if missing_registry:
            issues.append("dashboard tabs missing from view registry: " + ", ".join(missing_registry))
        if dead_registry:
            issues.append("dashboard view registry entries missing from navigation: " + ", ".join(dead_registry))

    loader_functions = set(re.findall(r"async\s+function\s+(load[A-Za-z0-9_]+)\s*\(", js))
    missing_functions = sorted(set(loaders.values()) - loader_functions)
    if missing_functions:
        issues.append("loader map references undefined functions: " + ", ".join(missing_functions))

    function_segments = _javascript_async_function_segments(js)
    for view, endpoint in DASHBOARD_VIEW_ENDPOINTS.items():
        loader = loaders.get(view)
        if loader and endpoint not in function_segments.get(loader, ""):
            issues.append(f"dashboard loader {loader} does not request its registered endpoint {endpoint}")

    routed_get = set(re.findall(r'if\s+path\s*==\s*"(/api/[A-Za-z0-9_./-]+)"', server)) - DASHBOARD_POST_API_PATHS
    missing_get_routes = sorted(DASHBOARD_GET_API_PATHS - routed_get)
    if missing_get_routes:
        issues.append("GET API registry entries have no HTTP route implementation: " + ", ".join(missing_get_routes))
    unregistered_get_routes = sorted(routed_get - DASHBOARD_GET_API_PATHS)
    if unregistered_get_routes:
        issues.append("HTTP GET API routes are missing from the dashboard registry: " + ", ".join(unregistered_get_routes))

    store_methods = _store_method_sources(server)
    for system, tables in DASHBOARD_SYSTEM_TABLES.items():
        source = store_methods.get(system, "")
        if not source:
            issues.append(f"newer-system dashboard view has no ReadOnlyDashboardStore.{system} implementation")
            continue
        missing_table_usage = sorted(table for table in tables if table not in source)
        if missing_table_usage:
            issues.append(
                f"ReadOnlyDashboardStore.{system} does not implement registered table coverage: "
                + ", ".join(missing_table_usage)
            )

    # POST actions share a guarded dispatcher; require every declared action path
    # to be present in that dispatcher as well as in the browser.
    for path in sorted(DASHBOARD_POST_API_PATHS):
        if path not in server:
            issues.append(f"POST API registry entry has no HTTP route implementation: {path}")
        if path not in js:
            issues.append(f"POST API registry entry has no frontend implementation: {path}")

    return issues

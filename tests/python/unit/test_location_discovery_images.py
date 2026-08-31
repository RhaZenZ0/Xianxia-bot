from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BOT_MAIN = ROOT / "app" / "bot" / "main.py"
CAPITAL_ART = ROOT / "assets" / "locations" / "azure_crown_imperial_city.png"


def test_mortal_capital_discovery_art_is_packaged() -> None:
    assert CAPITAL_ART.is_file()
    assert CAPITAL_ART.stat().st_size > 100_000


def test_mortal_capital_art_is_bound_to_azure_crown() -> None:
    source = BOT_MAIN.read_text(encoding="utf-8")
    assert '"Azure Crown Imperial City": ROOT / "assets" / "locations" / "azure_crown_imperial_city.png"' in source
    assert 'title=f"🏙️ First Sight — {location}"' in source


def test_first_discovery_delivery_covers_creation_exploration_and_travel() -> None:
    source = BOT_MAIN.read_text(encoding="utf-8")

    # Birthplace does not gate the feature. Starting in any illustrated location
    # counts as that character's first discovery, while other birthplaces reach
    # the same image through exploration/travel below.
    assert 'if location in LOCATION_DISCOVERY_IMAGES:' in source
    assert 'if location == "Azure Crown Imperial City":' not in source
    assert 'discovery_art = location_discovery_image_path(location)' in source
    assert 'file=discord.File(discovery_art, filename=filename)' in source

    # Exploration discovery is authoritative and only returns a newly inserted location.
    assert 'if discovered_location:' in source
    assert 'interaction, discovered_location, thread=expedition_thread' in source

    # Direct/hub/road travel checks canonical discovery state before travel so art is not repeated.
    assert 'previously_discovered = await DB.has_discovered_location' in source
    assert 'undiscovered_image_locations = {' in source
    assert 'def travel_first_discovers_location(' in source
    assert 'target in route' in source

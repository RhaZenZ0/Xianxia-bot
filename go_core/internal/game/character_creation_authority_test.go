package game

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func setupCharacterCreationAuthorityDB(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "character_creation_authority.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE characters(
    user_id INTEGER PRIMARY KEY, discord_name TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, origin TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL, spiritual_root TEXT NOT NULL, concept TEXT NOT NULL DEFAULT '', gender TEXT NOT NULL DEFAULT 'neutral',
    age_at_creation_years INTEGER NOT NULL DEFAULT 18, created_game_minute INTEGER NOT NULL DEFAULT 0,
    natural_lifespan_years INTEGER NOT NULL DEFAULT 75, life_extension_years INTEGER NOT NULL DEFAULT 0, life_status TEXT NOT NULL DEFAULT 'alive',
    realm_index INTEGER NOT NULL DEFAULT 0, phase INTEGER NOT NULL DEFAULT 1, cultivation INTEGER NOT NULL DEFAULT 0, karma_score INTEGER NOT NULL DEFAULT 0,
    qi INTEGER NOT NULL DEFAULT 0, qi_max INTEGER NOT NULL DEFAULT 0, vitality INTEGER NOT NULL DEFAULT 0, vitality_max INTEGER NOT NULL DEFAULT 0,
    spirit_stones INTEGER NOT NULL DEFAULT 0, insight_xp INTEGER NOT NULL DEFAULT 0, location TEXT NOT NULL DEFAULT '',
    attributes_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE inventory(user_id INTEGER NOT NULL,item_id TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,item_id));
CREATE TABLE currency_wallets(user_id INTEGER NOT NULL,currency_id TEXT NOT NULL,balance INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,currency_id));
CREATE TABLE storage_containers(user_id INTEGER NOT NULL,container_id TEXT NOT NULL,name TEXT NOT NULL,grade TEXT NOT NULL,slot_capacity INTEGER NOT NULL,living_space INTEGER NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(user_id,container_id));
CREATE TABLE character_location_discoveries(user_id INTEGER NOT NULL,location TEXT NOT NULL,discovery_kind TEXT NOT NULL,discovered_game_minute INTEGER NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(user_id,location));
CREATE TABLE birth_families(
    family_id INTEGER PRIMARY KEY AUTOINCREMENT,family_name TEXT NOT NULL,surname TEXT NOT NULL,archetype TEXT NOT NULL,
    tier INTEGER NOT NULL,wealth INTEGER NOT NULL,influence INTEGER NOT NULL,stability INTEGER NOT NULL,alignment_bias INTEGER NOT NULL,
    location TEXT NOT NULL,head_name TEXT NOT NULL,head_gender TEXT NOT NULL,head_title TEXT NOT NULL,head_realm_index INTEGER NOT NULL,
    head_phase INTEGER NOT NULL,treasury_balance INTEGER NOT NULL,generation INTEGER NOT NULL,created_game_minute INTEGER NOT NULL,
    last_simulated_game_minute INTEGER NOT NULL,history_json TEXT NOT NULL,line_status TEXT NOT NULL DEFAULT 'active',
    extinct_afterlife_minute INTEGER,clan_structure TEXT NOT NULL,bloodline_name TEXT NOT NULL,bloodline_affinity TEXT NOT NULL,
    bloodline_trait TEXT NOT NULL,bloodline_purity INTEGER NOT NULL,branch_count INTEGER NOT NULL,retainer_count INTEGER NOT NULL,
    confederacy_name TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,starter_key TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX idx_birth_families_starter_key ON birth_families(starter_key) WHERE starter_key<>'';
CREATE TABLE character_birth_family(user_id INTEGER PRIMARY KEY,family_id INTEGER NOT NULL,birth_order INTEGER NOT NULL,generation INTEGER NOT NULL,last_support_game_minute INTEGER NOT NULL);
CREATE TABLE birth_family_npcs(npc_id INTEGER PRIMARY KEY AUTOINCREMENT,family_id INTEGER NOT NULL,name TEXT NOT NULL,relation TEXT NOT NULL,gender TEXT NOT NULL,age_at_creation INTEGER NOT NULL,birth_game_minute INTEGER NOT NULL,natural_lifespan_years INTEGER NOT NULL,status TEXT NOT NULL,spiritual_root TEXT NOT NULL,realm_index INTEGER NOT NULL,phase INTEGER NOT NULL,personality TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE character_spiritual_roots(user_id INTEGER PRIMARY KEY,grade TEXT NOT NULL,purity INTEGER NOT NULL,elements_json TEXT NOT NULL,mutation TEXT NOT NULL,stability INTEGER NOT NULL,refinement_progress INTEGER NOT NULL,compatibility INTEGER NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE character_bloodlines(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,bloodline_id TEXT NOT NULL,name TEXT NOT NULL,affinity TEXT NOT NULL,purity INTEGER NOT NULL,state TEXT NOT NULL,evolution_stage INTEGER NOT NULL,progress INTEGER NOT NULL,rejection INTEGER NOT NULL,mutation TEXT NOT NULL,primary_lineage INTEGER NOT NULL,source_family_id INTEGER,unlocked_techniques_json TEXT NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE character_physiques(user_id INTEGER PRIMARY KEY,physique_id TEXT NOT NULL,name TEXT NOT NULL,state TEXT NOT NULL,evolution_stage INTEGER NOT NULL,progress INTEGER NOT NULL,stability INTEGER NOT NULL,instability INTEGER NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE event_log(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE player_scene_state(user_id INTEGER PRIMARY KEY,physical_location TEXT NOT NULL DEFAULT '',scene_type TEXT NOT NULL DEFAULT 'world',scene_key TEXT NOT NULL DEFAULT '',scene_label TEXT NOT NULL DEFAULT '',channel_id INTEGER,metadata_json TEXT NOT NULL DEFAULT '{}',updated_at REAL NOT NULL);
CREATE TABLE character_creation_family_options(option_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,ordinal INTEGER NOT NULL,family_json TEXT NOT NULL,created_game_minute INTEGER NOT NULL DEFAULT 0,expires_at REAL NOT NULL,consumed_at REAL,created_at REAL NOT NULL,family_id INTEGER);
CREATE INDEX idx_character_creation_family_options_user ON character_creation_family_options(user_id,ordinal);
CREATE TABLE world_state(key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_at REAL NOT NULL DEFAULT 0);
CREATE TABLE authoritative_actor_versions(actor_id INTEGER PRIMARY KEY,state_version INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL);
CREATE TABLE authoritative_action_receipts(action_id TEXT PRIMARY KEY,actor_id INTEGER NOT NULL,operation TEXT NOT NULL,state_version INTEGER NOT NULL,result_json TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE authoritative_entity_versions(domain TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,state_version INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,PRIMARY KEY(domain,entity_type,entity_id));
CREATE TABLE domain_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT,event_uid TEXT NOT NULL UNIQUE,domain TEXT NOT NULL,event_type TEXT NOT NULL,actor_id INTEGER,entity_type TEXT NOT NULL DEFAULT '',entity_id TEXT NOT NULL DEFAULT '',subject_type TEXT NOT NULL DEFAULT '',subject_id TEXT NOT NULL DEFAULT '',game_minute INTEGER NOT NULL DEFAULT 0,state_version INTEGER NOT NULL DEFAULT 0,payload_json TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL);
`); err != nil {
		t.Fatal(err)
	}
	return path
}

func creationApply(t *testing.T, path, world, actionID, op string, actor int64, expected *int64, payload map[string]any) ActionResponse {
	t.Helper()
	if rawMinute, ok := payload["game_minute"]; ok {
		batch4SetCanonicalGameMinute(t, path, storage.ParseInt(rawMinute))
		payload = clonePayloadWithoutGameMinute(payload)
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: actionID, Operation: op, ActorID: actor, ExpectedVersion: expected, Payload: raw})
	if err != nil {
		t.Fatalf("%s: %v", op, err)
	}
	return out
}

func TestCharacterFamilyOptionsAreGoGeneratedPersistedAndVersioned(t *testing.T) {
	path := setupCharacterCreationAuthorityDB(t)
	world := batch4WorldPath(t)
	zero := int64(0)
	out := creationApply(t, path, world, "creation-family-options-1", "character.family_options", 77, &zero, map[string]any{"world_name": "Mortal World", "game_minute": 123})
	if out.StateVersion != 1 {
		t.Fatalf("state_version=%d", out.StateVersion)
	}
	result := batch4Result(t, out)
	families, ok := result["families"].([]familyOffer)
	if !ok {
		t.Fatalf("families type=%T value=%v", result["families"], result["families"])
	}
	if len(families) != len(birthFamilyArchetypes) {
		t.Fatalf("families=%d", len(families))
	}
	seen := map[string]bool{}
	for _, offer := range families {
		if len(offer.ChoiceID) != 32 {
			t.Fatalf("bad choice id %q", offer.ChoiceID)
		}
		if seen[offer.ChoiceID] {
			t.Fatalf("duplicate choice id %q", offer.ChoiceID)
		}
		seen[offer.ChoiceID] = true
		if offer.ID == "" || offer.FamilyName == "" || len(offer.Relatives) != 3 {
			t.Fatalf("incomplete offer=%+v", offer)
		}
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM character_creation_family_options WHERE user_id=77")); got != int64(len(families)) {
		t.Fatalf("stored offers=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM domain_events WHERE actor_id=77 AND event_type='birth_family_options_generated'")); got != 1 {
		t.Fatalf("domain events=%d", got)
	}
}

func TestCharacterCreateRejectsClientSuppliedFamilyPayload(t *testing.T) {
	path := setupCharacterCreationAuthorityDB(t)
	world := batch4WorldPath(t)
	zero := int64(0)
	offers := creationApply(t, path, world, "creation-family-options-forge", "character.family_options", 77, &zero, map[string]any{"world_name": "Mortal World", "game_minute": 123})
	expected := offers.StateVersion
	batch4SetCanonicalGameMinute(t, path, 124)
	raw, _ := json.Marshal(map[string]any{
		"name": "Cheater", "path": "Sword Cultivator", "gender": "male", "family_choice_id": "anything",
		"family": map[string]any{"family_name": "Forged Heavenly Clan", "wealth": 1000000},
	})
	_, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "creation-forged-family", Operation: "character.create", ActorID: 77, ExpectedVersion: &expected, Payload: raw})
	if err == nil || !strings.Contains(err.Error(), "client-supplied family payload is forbidden") {
		t.Fatalf("err=%v", err)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM characters WHERE user_id=77")); got != 0 {
		t.Fatalf("character rows=%d", got)
	}
}

func TestCharacterCreateConsumesCanonicalOfferAndPersistsExactFamily(t *testing.T) {
	path := setupCharacterCreationAuthorityDB(t)
	world := batch4WorldPath(t)
	zero := int64(0)
	optionsOut := creationApply(t, path, world, "creation-family-options-commit", "character.family_options", 77, &zero, map[string]any{"world_name": "Mortal World", "game_minute": 200})
	optionsResult := batch4Result(t, optionsOut)
	offers := optionsResult["families"].([]familyOffer)
	selected := offers[len(offers)-1]
	expected := optionsOut.StateVersion
	createdOut := creationApply(t, path, world, "creation-character-commit", "character.create", 77, &expected, map[string]any{
		"discord_name": "Tester", "name": "Lin Authority", "concept": "Prove the Dao", "gender": "female",
		"path": "Sword Cultivator", "family_choice_id": selected.ChoiceID, "game_minute": 201, "age_at_creation_years": 18,
	})
	if createdOut.StateVersion != 2 {
		t.Fatalf("state_version=%d", createdOut.StateVersion)
	}
	result := batch4Result(t, createdOut)
	createdFamily, ok := result["family"].(BirthFamily)
	if !ok {
		t.Fatalf("family type=%T value=%v", result["family"], result["family"])
	}
	if createdFamily.FamilyName != selected.FamilyName || createdFamily.Wealth != selected.Wealth || createdFamily.BloodlineName != selected.BloodlineName {
		t.Fatalf("created family changed: preview=%+v created=%+v", selected.BirthFamily, createdFamily)
	}
	if got := fmt.Sprint(actionScalar(t, path, "SELECT family_name FROM birth_families WHERE family_id=?", storage.ParseInt(result["family_id"]))); got != selected.FamilyName {
		t.Fatalf("persisted family=%q preview=%q", got, selected.FamilyName)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT wealth FROM birth_families WHERE family_id=?", storage.ParseInt(result["family_id"]))); got != selected.Wealth {
		t.Fatalf("persisted wealth=%d preview=%d", got, selected.Wealth)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM character_creation_family_options WHERE user_id=77")); got != 0 {
		t.Fatalf("unconsumed offer rows=%d", got)
	}
}

func TestStarterHouseholdsAreSharedStableAndCoLocatedPlayersCanMeet(t *testing.T) {
	path := setupCharacterCreationAuthorityDB(t)
	world := batch4WorldPath(t)
	zero := int64(0)

	firstOptions := creationApply(t, path, world, "shared-household-options-1", "character.family_options", 101, &zero, map[string]any{"world_name": "Mortal World", "game_minute": 300})
	secondOptions := creationApply(t, path, world, "shared-household-options-2", "character.family_options", 202, &zero, map[string]any{"world_name": "Mortal World", "game_minute": 300})
	firstOffers := batch4Result(t, firstOptions)["families"].([]familyOffer)
	secondOffers := batch4Result(t, secondOptions)["families"].([]familyOffer)
	if len(firstOffers) == 0 || len(secondOffers) == 0 {
		t.Fatal("missing starter household offers")
	}
	first := firstOffers[0]
	second := secondOffers[0]
	if first.ID != "martial_household" || second.ID != "martial_household" {
		t.Fatalf("unexpected starter archetypes: %q %q", first.ID, second.ID)
	}
	if first.FamilyName != "Han Family" || second.FamilyName != "Han Family" {
		t.Fatalf("starter household name is not stable: %q %q", first.FamilyName, second.FamilyName)
	}
	if first.FamilyID <= 0 || first.FamilyID != second.FamilyID {
		t.Fatalf("starter household was not shared: %d %d", first.FamilyID, second.FamilyID)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM birth_families")); got != int64(len(birthFamilyArchetypes)) {
		t.Fatalf("canonical starter households=%d", got)
	}

	v1 := firstOptions.StateVersion
	creationApply(t, path, world, "shared-household-create-1", "character.create", 101, &v1, map[string]any{
		"discord_name": "One", "name": "Han One", "path": "Sword Cultivator", "gender": "male", "family_choice_id": first.ChoiceID, "game_minute": 301,
	})
	v2 := secondOptions.StateVersion
	creationApply(t, path, world, "shared-household-create-2", "character.create", 202, &v2, map[string]any{
		"discord_name": "Two", "name": "Han Two", "path": "Sword Cultivator", "gender": "female", "family_choice_id": second.ChoiceID, "game_minute": 301,
	})
	if a := storage.ParseInt(actionScalar(t, path, "SELECT family_id FROM character_birth_family WHERE user_id=101")); a != first.FamilyID {
		t.Fatalf("first player family=%d want=%d", a, first.FamilyID)
	}
	if b := storage.ParseInt(actionScalar(t, path, "SELECT family_id FROM character_birth_family WHERE user_id=202")); b != first.FamilyID {
		t.Fatalf("second player family=%d want=%d", b, first.FamilyID)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM birth_families")); got != int64(len(birthFamilyArchetypes)) {
		t.Fatalf("character creation duplicated shared households: %d", got)
	}

	three := int64(2)
	enteredOne := creationApply(t, path, world, "shared-household-enter-1", "family.household.enter", 101, &three, map[string]any{"game_minute": 302})
	enteredTwo := creationApply(t, path, world, "shared-household-enter-2", "family.household.enter", 202, &three, map[string]any{"game_minute": 302})
	location := birthFamilyHouseholdLocation(first.FamilyID)
	if got := fmt.Sprint(actionScalar(t, path, "SELECT location FROM characters WHERE user_id=101")); got != location {
		t.Fatalf("first location=%q want=%q", got, location)
	}
	if got := fmt.Sprint(actionScalar(t, path, "SELECT location FROM characters WHERE user_id=202")); got != location {
		t.Fatalf("second location=%q want=%q", got, location)
	}
	players := batch4Result(t, enteredTwo)["players_present"].([]map[string]any)
	if len(players) != 2 {
		t.Fatalf("players_present=%v", players)
	}
	if batch4Result(t, enteredOne)["family_name"] != "Han Family" {
		t.Fatalf("enter result=%v", batch4Result(t, enteredOne))
	}

	four := int64(3)
	left := creationApply(t, path, world, "shared-household-leave-1", "family.household.leave", 101, &four, map[string]any{"game_minute": 303})
	if got := fmt.Sprint(batch4Result(t, left)["location"]); got != first.Location {
		t.Fatalf("leave location=%q want=%q", got, first.Location)
	}
	if got := fmt.Sprint(actionScalar(t, path, "SELECT location FROM characters WHERE user_id=202")); got != location {
		t.Fatalf("second player unexpectedly moved: %q", got)
	}
}

func TestFamilyAddChildOwnsInheritedRootAndRejectsClientRoot(t *testing.T) {
	path := setupCharacterCreationAuthorityDB(t)
	world := batch4WorldPath(t)
	zero := int64(0)
	optionsOut := creationApply(t, path, world, "child-family-options", "character.family_options", 77, &zero, map[string]any{"world_name": "Mortal World", "game_minute": 500})
	offers := batch4Result(t, optionsOut)["families"].([]familyOffer)
	expected := optionsOut.StateVersion
	createdOut := creationApply(t, path, world, "child-character-create", "character.create", 77, &expected, map[string]any{
		"discord_name": "Parent", "name": "Lin Parent", "concept": "Raise a lineage", "gender": "female",
		"path": "Sword Cultivator", "family_choice_id": offers[0].ChoiceID, "game_minute": 501, "age_at_creation_years": 25,
	})
	expected = createdOut.StateVersion
	batch4SetCanonicalGameMinute(t, path, 502)
	forged, _ := json.Marshal(map[string]any{"name": "Forged Child", "gender": "male", "spiritual_root": "Void"})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "child-forged-root", Operation: "family.add_child", ActorID: 77, ExpectedVersion: &expected, Payload: forged}); err == nil || !strings.Contains(err.Error(), "client-supplied spiritual_root is forbidden") {
		t.Fatalf("forged child err=%v", err)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM birth_family_npcs WHERE relation='Child of user 77'")); got != 0 {
		t.Fatalf("forged child rows=%d", got)
	}

	childOut := creationApply(t, path, world, "child-authoritative-root", "family.add_child", 77, &expected, map[string]any{"name": "Canonical Child", "gender": "male", "game_minute": 503})
	result := batch4Result(t, childOut)
	root := fmt.Sprint(result["spiritual_root"])
	if root == "" {
		t.Fatal("missing authoritative spiritual root")
	}
	if got := fmt.Sprint(actionScalar(t, path, "SELECT spiritual_root FROM birth_family_npcs WHERE npc_id=?", storage.ParseInt(result["child_id"]))); got != root {
		t.Fatalf("persisted root=%q result root=%q", got, root)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='family.add_child'")); got != 1 {
		t.Fatalf("child receipts=%d", got)
	}
}

package game

import (
	"encoding/json"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// Reported from play (v1.2.1): "it says loyalty should be 110, but I can't go
// over 100". The requirement climbed ten a stage past the cap feed and train
// clamp loyalty at, so stage five asked for a number no action could produce.
func evolveBeast(t *testing.T, path, world string, seq int) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"beast_id": 1})
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "evolve-cap-" + string(rune('a'+seq)), Operation: "beast.evolve", ActorID: 42, Payload: raw})
	if err != nil {
		return nil, err
	}
	return out.Result.(map[string]any), nil
}

func TestABeastAtTheLoyaltyCapCanAlwaysEvolve(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupStage4CompanionTables(t, path)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO spirit_beasts(
		user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
		loyalty,contract_type,active,techniques_json,created_at,updated_at
	) VALUES(42,'Cloudpaw','Wind Lynx',6,'Wind',10,'bonded','Common',5,100,'equality',1,'[]',0,0)`)
	got, err := evolveBeast(t, path, world, 0)
	if err != nil {
		t.Fatalf("a beast at the loyalty cap was refused: %v", err)
	}
	beast, _ := got["beast"].(map[string]any)
	if storage.ParseInt(beast["evolution_stage"]) != 6 {
		t.Fatalf("evolution_stage=%v want 6", beast["evolution_stage"])
	}
}

func TestTheEvolutionRequirementNeverNamesANumberAboveTheCap(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	setupStage4CompanionTables(t, path)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO spirit_beasts(
		user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
		loyalty,contract_type,active,techniques_json,created_at,updated_at
	) VALUES(42,'Cloudpaw','Wind Lynx',6,'Wind',10,'bonded','Common',5,99,'equality',1,'[]',0,0)`)
	_, err := evolveBeast(t, path, world, 1)
	if err == nil {
		t.Fatal("loyalty 99 evolved a stage-5 beast")
	}
	if !strings.Contains(err.Error(), "requirement 100") || strings.Contains(err.Error(), "110") {
		t.Fatalf("the refusal must ask for the cap and nothing above it: %v", err)
	}
}

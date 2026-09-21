package game

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The levers a human types into (v1.0.11).
//
// Both findings are one sentence from two sides: **the writer a human drives
// is the one nothing held.**
//
//   - `admin.player.set_spiritual_root` held its grade to a *hand-written copy*
//     of `spiritual_root_system.grades`. The copy agreed with the content the
//     day it was written, so it could not be wrong in an interesting way until
//     a rung was renamed or added - at which point the lever refuses the real
//     grade and accepts a stale one. `gradeIndex` answers 0 for a name it does
//     not know, which is Mortal, and since rc.55 that is a real 0.88x
//     cultivation multiplier and -1 on every breakthrough, for life.
//   - `admin.player.set_physique` moved a physique's stage, progress and
//     stability and **never its identity**: the only writers of `physique_id`
//     were character creation and samsara, so a GM could not hand somebody
//     `nine_yang_solar_body`, correct one rolled wrong, or stage one.
//
// Both drive the production dispatch rather than the functions directly -
// rc.38's finding was a handler that answered correctly and persisted nothing,
// which only the switch path can catch. They go through `applyAdminRaw`, which
// hands it the real content file, because `Apply` passes an empty world path
// and under an empty catalogue a content-backed check refuses everything it is
// handed - a fixture that cannot fail the way production fails.

// worldWithAnExtraRung writes a copy of the shipped content carrying a
// seventh spiritual-root grade.
//
// This is the whole gate. The hand-written copy the lever used to hold its
// grade to names exactly the six rungs the file carries today, so every
// assertion about the *shipped* ladder passes against the copy as well - a
// gate that cannot see the thing it forbids is decoration (rc.47). What
// separates them is a rung the file has and the copy does not, which is also
// precisely the day the fault would first cost somebody something.
func worldWithAnExtraRung(t *testing.T, rung string) string {
	t.Helper()
	raw, err := os.ReadFile(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatal(err)
	}
	rootSystem, ok := payload["spiritual_root_system"].(map[string]any)
	if !ok {
		t.Fatal("spiritual_root_system missing from the content file; the fixture is broken, not the tree")
	}
	grades, ok := rootSystem["grades"].([]any)
	if !ok || len(grades) == 0 {
		t.Fatal("spiritual_root_system.grades missing from the content file; the fixture is broken, not the tree")
	}
	top, ok := grades[len(grades)-1].(map[string]any)
	if !ok {
		t.Fatal("the top rung of the ladder is not an object; the fixture is broken, not the tree")
	}
	added := map[string]any{}
	for k, v := range top {
		added[k] = v
	}
	added["name"] = rung
	rootSystem["grades"] = append(grades, added)
	encoded, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "world_extra_rung.json")
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestTheRootGradeLeverAsksTheLadderRatherThanACopy(t *testing.T) {
	const rung = "Primordial"
	world := worldWithAnExtraRung(t, rung)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	if !rootGradeOnTheLadder(catalog, rung) {
		t.Fatalf("the fixture did not add %q to the ladder; the fixture is broken, not the tree", rung)
	}

	path := setupAdminDB(t)
	raw, _ := json.Marshal(map[string]any{"user_id": 42, "grade": rung, "purity": 50, "mutation": "", "reason": "test"})
	if _, err := ApplyWithWorld(path, world, ActionRequest{Operation: "admin.player.set_spiritual_root", Payload: raw}); err != nil {
		t.Fatalf("the content file carries a %q rung and the lever refused it: %v\n"+
			"That is the fault: the grade was held to a hand-written copy of the ladder, which "+
			"agrees with the file until somebody renames or adds a rung - and then refuses the "+
			"real grade while still accepting a stale one.", rung, err)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT grade FROM character_spiritual_roots WHERE user_id=42")); got != rung {
		t.Fatalf("grade is %q after setting %q", got, rung)
	}
}

func TestTheRootGradeLeverRefusesWhatTheLadderDoesNotCarry(t *testing.T) {
	// "Heavenly" is the exact name rc.55 found seeded in this repo's own
	// fixtures for releases - a grade no rung has.
	path := setupAdminDB(t)
	err := applyAdminErr(t, path, "admin.player.set_spiritual_root", map[string]any{
		"user_id": 42, "grade": "Heavenly", "purity": 80, "mutation": "", "reason": "test",
	})
	if err == nil {
		t.Fatal(`the lever accepted the grade "Heavenly", which the ladder does not carry`)
	}
	for _, rung := range districtCatalog(t).SpiritualRootSystem.Grades {
		if !strings.Contains(err.Error(), rung.Name) {
			t.Fatalf("the refusal does not name %q, so a GM cannot tell what they may type: %v", rung.Name, err)
		}
	}
}

func TestEveryRungTheLadderCarriesIsAccepted(t *testing.T) {
	// The other direction. A gate that only proved a bad grade is refused would
	// pass just as well for a lever that refuses everything.
	catalog := districtCatalog(t)
	for _, rung := range catalog.SpiritualRootSystem.Grades {
		path := setupAdminDB(t)
		applyAdmin(t, path, "admin.player.set_spiritual_root", map[string]any{
			"user_id": 42, "grade": rung.Name, "purity": 50, "mutation": "", "reason": "test",
		})
		if got := fmt.Sprint(scalar(t, path, "SELECT grade FROM character_spiritual_roots WHERE user_id=42")); got != rung.Name {
			t.Fatalf("grade is %q after setting %q", got, rung.Name)
		}
	}
}

// seedOrdinaryPhysique gives user 42 the row every real character has.
// `setupAdminDB` deliberately makes the table and no row, because one existing
// test deletes it to drive the "physique not found" refusal.
func seedOrdinaryPhysique(t *testing.T, path string) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(`INSERT INTO character_physiques(user_id,physique_id,name,state,evolution_stage,progress,stability,updated_at) VALUES(42,'ordinary_mortal_body','Ordinary Mortal Body','ordinary',0,0,100,0)`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

func grantablePhysique(t *testing.T) string {
	t.Helper()
	best := ""
	for id := range districtCatalog(t).Physiques {
		if id != "ordinary_mortal_body" && (best == "" || id < best) {
			best = id
		}
	}
	if best == "" {
		t.Fatal("the shipped content carries no physique to grant; the fixture is broken, not the tree")
	}
	return best
}

func TestAGMCanHandSomebodyAPhysique(t *testing.T) {
	target := grantablePhysique(t)
	path := setupAdminDB(t)
	seedOrdinaryPhysique(t, path)
	applyAdmin(t, path, "admin.player.set_physique", map[string]any{
		"user_id": 42, "physique_id": target, "evolution_stage": 1, "progress": 10, "stability": 90, "reason": "test",
	})
	if got := fmt.Sprint(scalar(t, path, "SELECT physique_id FROM character_physiques WHERE user_id=42")); got != target {
		t.Fatalf("physique_id is %q after the grant, want %q. Before v1.0.11 the only writers of this "+
			"column were character creation and samsara, so a GM could not correct one rolled wrong.", got, target)
	}
	if got := fmt.Sprint(scalar(t, path, "SELECT name FROM character_physiques WHERE user_id=42")); got == "" {
		t.Fatal("the grant left the physique's name empty, so the sheet names nothing")
	}
}

func TestAnUndoPutsTheReplacedPhysiqueBack(t *testing.T) {
	// The snapshot carried only the three numbers it could restore, so an undo
	// of a grant would have left the new identity standing and called itself
	// an undo.
	target := grantablePhysique(t)
	path := setupAdminDB(t)
	seedOrdinaryPhysique(t, path)
	before := fmt.Sprint(scalar(t, path, "SELECT physique_id FROM character_physiques WHERE user_id=42"))
	applyAdmin(t, path, "admin.player.set_physique", map[string]any{
		"user_id": 42, "physique_id": target, "evolution_stage": 1, "progress": 10, "stability": 90, "reason": "test",
	})
	undoLast(t, path)
	if got := fmt.Sprint(scalar(t, path, "SELECT physique_id FROM character_physiques WHERE user_id=42")); got != before {
		t.Fatalf("after the undo the physique is %q, want %q back. An undo that restores only the "+
			"three numbers leaves a granted identity standing.", got, before)
	}
}

func TestAPhysiqueTheCatalogueDoesNotCarryIsRefused(t *testing.T) {
	// An id nothing carries contributes no modifiers at all, so it would be a
	// physique that exists only as a string on the sheet.
	path := setupAdminDB(t)
	seedOrdinaryPhysique(t, path)
	err := applyAdminErr(t, path, "admin.player.set_physique", map[string]any{
		"user_id": 42, "physique_id": "no_such_body", "evolution_stage": 1, "progress": 10, "stability": 90, "reason": "test",
	})
	if err == nil || !strings.Contains(err.Error(), "no_such_body") {
		t.Fatalf("an uncatalogued physique was accepted (err=%v)", err)
	}
}

func TestTheNumbersStillMoveWithoutAnIdentity(t *testing.T) {
	// Every existing caller sends no `physique_id`, so the lever must still be
	// the three-number edit it has always been.
	path := setupAdminDB(t)
	seedOrdinaryPhysique(t, path)
	before := fmt.Sprint(scalar(t, path, "SELECT physique_id FROM character_physiques WHERE user_id=42"))
	applyAdmin(t, path, "admin.player.set_physique", map[string]any{
		"user_id": 42, "evolution_stage": 3, "progress": 55, "stability": 70, "reason": "test",
	})
	if got := fmt.Sprint(scalar(t, path, "SELECT physique_id FROM character_physiques WHERE user_id=42")); got != before {
		t.Fatalf("a call with no physique_id changed the identity to %q; every existing caller sends none", got)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT progress FROM character_physiques WHERE user_id=42")); got != 55 {
		t.Fatalf("progress is %d, want 55", got)
	}
}

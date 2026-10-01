package game

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"math"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// A cultivation path plays differently (v1.13.0).
//
// These drive the shipped catalogue: each ability's numbers are content, and a
// fixture that wrote its own would be proving the fixture. Each ability is held
// from both sides - the path gets it, another path does not - because a test
// that only proved the Sword Cultivator strikes harder passes just as well for
// a tree where everybody does.

// TestEveryPathHasATrait: the sheet prints a path's ability off its trait, so
// a path with no name is a path the sheet says nothing about.
func TestEveryPathHasATrait(t *testing.T) {
	catalog := shippedCatalog(t)
	if len(catalog.Paths) != 7 {
		t.Fatalf("the content carries %d paths; the reader is broken, not the tree", len(catalog.Paths))
	}
	for name := range catalog.Paths {
		trait, ok := pathTrait(catalog, name)
		if !ok || strings.TrimSpace(trait.Name) == "" || strings.TrimSpace(trait.Summary) == "" {
			t.Errorf("%s carries no trait name or summary: %+v", name, trait)
		}
	}
	if _, ok := pathTrait(catalog, "Rogue Cultivator"); ok {
		t.Error("a path the catalogue does not carry must answer no trait")
	}
}

// TestEachAbilityIsItsPathsAlone walks every helper over every path. Drill:
// drop the path check in any one of them and it names the path that got it.
func TestEachAbilityIsItsPathsAlone(t *testing.T) {
	catalog := shippedCatalog(t)
	for name := range catalog.Paths {
		if got := swordIntentCap(catalog, name); (name == pathSword) != (got > 0) {
			t.Errorf("%s holds sword intent up to %d", name, got)
		}
		if got := bodyRecoveryMult(catalog, name); (name == pathBody) != (got > 1) {
			t.Errorf("%s mends at x%.2f", name, got)
		}
		if got := defeatWoundSeverity(catalog, name, 3); (name == pathBody) != (got < 3) {
			t.Errorf("%s takes a severity-3 defeat wound at %d", name, got)
		}
		if got := heartWaveBonus(catalog, name); (name == pathSoul) != (got > 0) {
			t.Errorf("%s gains %d on the Heart wave", name, got)
		}
		if got := senseGainMult(catalog, name); (name == pathSoul) != (got > 1) {
			t.Errorf("%s builds its sense at x%.2f", name, got)
		}
		if got := arrayDurationMult(catalog, name); (name == pathFormation) != (got > 1) {
			t.Errorf("%s's arrays hold x%.2f", name, got)
		}
		if got := freeDeploysPerDay(catalog, name); (name == pathFormation) != (got > 0) {
			t.Errorf("%s deploys %d arrays a day for free", name, got)
		}
		if _, ok := stanceForPath(catalog, qiRefinerStanceKey, name); (name == pathQi) != ok {
			t.Errorf("%s may take the refined circulation: %v", name, ok)
		}
	}
}

// TestABodyRefinersWoundIsLighterAndNeverNothing: one severity lighter, never
// under 1, because a lost fight must still leave a mark.
func TestABodyRefinersWoundIsLighterAndNeverNothing(t *testing.T) {
	catalog := shippedCatalog(t)
	for _, c := range []struct{ in, want int64 }{{3, 2}, {2, 1}, {1, 1}} {
		if got := defeatWoundSeverity(catalog, pathBody, c.in); got != c.want {
			t.Errorf("a Body Refiner's severity-%d wound is written at %d, want %d", c.in, got, c.want)
		}
	}
}

// TestEveryPathGrowsTwoAttributesARealm is the balance claim: every path's
// highest starting attribute is a tie of two, so every path grows two a realm.
// Formation Adept was the one path with no tie (spirit 2, insight 3) and its
// spread moved for it; this is what keeps a later content edit from undoing it.
func TestEveryPathGrowsTwoAttributesARealm(t *testing.T) {
	catalog := shippedCatalog(t)
	for name := range catalog.Paths {
		if got := pathGrowthAttributes(catalog, name); len(got) != 2 {
			t.Errorf("%s grows %v a realm; every path grows its two tied attributes", name, got)
		}
	}
	if got := pathGrowthAttributes(catalog, "Rogue Cultivator"); len(got) != 1 || got[0] != "will" {
		t.Errorf("an unknown path grows %v, want will", got)
	}
}

// --- Own-path manuals --------------------------------------------------------

func TestAnOwnPathManualGathersMoreAndPractisesFaster(t *testing.T) {
	catalog := shippedCatalog(t)
	sword, ok := catalog.TechniqueSystem.Manuals["blood_sea_scripture"]
	if !ok || sword.Path != pathSword {
		t.Fatalf("the fixture manual is not a Sword Cultivator's: %+v", sword)
	}
	own := manualGatheringMult(catalog, sword, 2, pathSword)
	other := manualGatheringMult(catalog, sword, 2, pathQi)
	if want := round4(other * catalog.PathSystem.OwnManualGatheringMult); math.Abs(own-want) > 0.0002 || own <= other {
		t.Fatalf("a Sword Cultivator's own manual gathers x%.4f against another path's x%.4f; want x%.4f", own, other, want)
	}
	if got := manualPracticeGain(catalog, sword, pathSword); got != cultivationPracticeGain+catalog.PathSystem.OwnManualPracticeBonus || got <= cultivationPracticeGain {
		t.Fatalf("an own-path session practises %d", got)
	}
	if got := manualPracticeGain(catalog, sword, pathQi); got != cultivationPracticeGain {
		t.Fatalf("another path's session practises %d, want %d", got, cultivationPracticeGain)
	}
	// "Any" is everybody's and nobody's.
	canon, ok := catalog.TechniqueSystem.Manuals["azure_cloud_foundation_sword_canon"]
	if !ok || canon.Path != "Any" {
		t.Fatalf("the fixture canon is not a path-free manual: %+v", canon)
	}
	if manualGatheringMult(catalog, canon, 1, pathSword) != manualGatheringMult(catalog, canon, 1, pathQi) {
		t.Fatal("a manual for Any must be worth the same to every path")
	}
}

// --- Sword Intent -----------------------------------------------------------

func setupIntentDB(t *testing.T, path string, intent int64, withColumn bool) string {
	t.Helper()
	db := setupBugslayerCombatTurnDB(t, 901, false)
	script := `UPDATE characters SET path='` + path + `' WHERE user_id=901;`
	if withColumn {
		script += "\nALTER TABLE characters ADD COLUMN path_resource INTEGER NOT NULL DEFAULT 0;"
		script += "\nUPDATE characters SET path_resource=" + itoa(intent) + " WHERE user_id=901;"
	}
	conn, err := storage.Open(db)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(script); err != nil {
		t.Fatal(err)
	}
	return db
}

func intentTurn(t *testing.T, db, style string) (map[string]any, error) {
	t.Helper()
	conn, err := storage.Open(db)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"battle_id": 1, "style": style, "game_minute": 100})
	mut, err := combatTurnAction(conn, shippedCatalog(t), 901, raw)
	if err != nil {
		_ = conn.Rollback()
		return nil, err
	}
	if conn.InTransaction() {
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return mut.Result.(map[string]any), nil
}

// TestAnIntentStrikeSpendsIntentHitsHarderAndIsNotAnswered is the ability.
// Drills: drop `atk += trait.IntentStrikeBonus` and the bonus is gone; drop
// the `intentStrike` branch before the counter and the opponent answers.
func TestAnIntentStrikeSpendsIntentHitsHarderAndIsNotAnswered(t *testing.T) {
	catalog := shippedCatalog(t)
	trait, _ := pathTrait(catalog, pathSword)
	plain, err := intentTurn(t, setupIntentDB(t, pathSword, 2, true), "attack")
	if err != nil {
		t.Fatal(err)
	}
	db := setupIntentDB(t, pathSword, 2, true)
	strike, err := intentTurn(t, db, "intent")
	if err != nil {
		t.Fatal(err)
	}
	if strike["intent_strike"] != true {
		t.Fatalf("the strike was not an Intent Strike: %v", strike)
	}
	if got, want := rollModifier(t, strike, "player_roll")-rollModifier(t, plain, "player_roll"), trait.IntentStrikeBonus; got != want || want <= 0 {
		t.Fatalf("an Intent Strike rolled %d above a plain attack, want the path's %d", got, want)
	}
	if strike["counter_suppressed"] != true || strike["counter_roll"] != nil {
		t.Fatalf("an Intent Strike must not be answered: counter_suppressed=%v counter_roll=%v", strike["counter_suppressed"], strike["counter_roll"])
	}
	if got := i64(actionScalar(t, db, `SELECT path_resource FROM characters WHERE user_id=901`)); got != 1 {
		t.Fatalf("an Intent Strike spent intent 2 -> %d, want 1", got)
	}
}

// TestAnIntentStrikeIsRefusedBeforeAnythingIsSpent: another path, no intent
// banked, and a world before schema 72 each refuse, and none costs a turn.
func TestAnIntentStrikeIsRefusedBeforeAnythingIsSpent(t *testing.T) {
	for _, c := range []struct {
		name, path string
		intent     int64
		column     bool
		want       string
	}{
		{"another path", pathQi, 3, true, "only a Sword Cultivator"},
		{"nothing banked", pathSword, 0, true, "no sword intent banked"},
		{"before the column", pathSword, 0, false, "no sword intent banked"},
	} {
		db := setupIntentDB(t, c.path, c.intent, c.column)
		if _, err := intentTurn(t, db, "intent"); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: an Intent Strike answered %v, want %q", c.name, err, c.want)
		}
		if got := i64(actionScalar(t, db, `SELECT npc_hp FROM battles WHERE battle_id=1`)); got != 999 {
			t.Errorf("%s: a refused strike still struck (npc_hp %d)", c.name, got)
		}
	}
}

// TestAWinBanksIntentUpToTheCap. Drill: drop the MIN from the UPDATE and five
// wins bank five.
func TestAWinBanksIntentUpToTheCap(t *testing.T) {
	catalog := shippedCatalog(t)
	capacity := swordIntentCap(catalog, pathSword)
	if capacity <= 0 {
		t.Fatal("the content gives a Sword Cultivator no intent to hold")
	}
	db := setupIntentDB(t, pathSword, 0, true)
	var held int64
	for i := int64(0); i < capacity+2; i++ {
		if err := crossingApply(t, db, func(conn *storage.Conn) error {
			held = bankSwordIntentTx(conn, catalog, 901)
			return nil
		}); err != nil {
			t.Fatal(err)
		}
	}
	if held != capacity {
		t.Fatalf("%d wins banked %d intent; it stops at %d", capacity+2, held, capacity)
	}
	other := setupIntentDB(t, pathQi, 0, true)
	if err := crossingApply(t, other, func(conn *storage.Conn) error {
		held = bankSwordIntentTx(conn, catalog, 901)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if held != 0 {
		t.Fatalf("a Qi Refiner banked %d sword intent", held)
	}
}

// --- Body Refiner: Iron Flesh -----------------------------------------------

// TestABodyRefinerMendsFaster drives the real settle. Drill: drop the
// bodyRecoveryMult block and a Body Refiner mends 3, like anybody.
func TestABodyRefinerMendsFaster(t *testing.T) {
	catalog := vitalityRecoveryCatalog()
	catalog.Paths = shippedCatalog(t).Paths
	mult := bodyRecoveryMult(catalog, pathBody)
	for _, c := range []struct {
		path string
		want int64
	}{{pathSword, 3}, {pathBody, int64(math.Round(25*mult)) * 12 / 100}} {
		db := setupVitalityRecoveryDB(t, 0, 12, 1000)
		batch4Exec(t, db, `UPDATE characters SET path=? WHERE user_id=42`, c.path)
		var gain int64
		if err := crossingApply(t, db, func(conn *storage.Conn) error {
			var e error
			gain, e = settleVitalityRecoveryTx(conn, catalog, 42, 1000+testRecoveryDay)
			return e
		}); err != nil {
			t.Fatal(err)
		}
		if gain != c.want {
			t.Errorf("a %s mended %d in a world day, want %d", c.path, gain, c.want)
		}
	}
}

// --- Soul Cultivator: Still Heart -------------------------------------------

func TestASoulCultivatorsSenseBuildsFaster(t *testing.T) {
	catalog := crossingCatalog(t)
	rules := spiritSenseRules(catalog)
	base := rules.Gains["craft"] + 2 // the fixture's spirit 8 / 4
	for _, c := range []struct {
		path string
		want int64
	}{{pathSword, base}, {pathSoul, int64(math.Round(float64(base) * senseGainMult(catalog, pathSoul)))}} {
		db := setupSpiritSenseDB(t)
		// Realm 0, stage 1: no stage behind it, so a path's edge (v1.14.0)
		// adds nothing to spirit and the gain is the trait's alone.
		batch4Exec(t, db, `UPDATE characters SET path=?,realm_index=0,phase=1 WHERE user_id=42`, c.path)
		if out := senseGain(t, db, "craft", false, 0); out == nil || i64(out["gain"]) != c.want {
			t.Errorf("a %s's craft built %v, want %d", c.path, out, c.want)
		}
	}
	if heartWaveBonus(catalog, pathSoul) <= 0 {
		t.Fatal("the content gives a Soul Cultivator nothing on the Heart wave")
	}
}

// --- Formation Adept: Enduring Arrays --------------------------------------

// TestAFormationAdeptDeploysOneFreeADay: one a world day, counted per day.
// Drill: drop the COUNT comparison and every deploy is free.
func TestAFormationAdeptDeploysOneFreeADay(t *testing.T) {
	catalog := shippedCatalog(t)
	allowance := freeDeploysPerDay(catalog, pathFormation)
	if allowance <= 0 {
		t.Fatal("the content gives a Formation Adept no free deploy")
	}
	db := filepath.Join(t.TempDir(), "free_deploy.sqlite3")
	batch4Exec(t, db, `CREATE TABLE event_log(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at REAL NOT NULL)`)
	deploy := func(path string, minute int64) bool {
		var free bool
		if err := crossingApply(t, db, func(conn *storage.Conn) error {
			free = formationFreeDeployTx(conn, catalog, 42, path, minute, 1)
			return nil
		}); err != nil {
			t.Fatal(err)
		}
		return free
	}
	for i := int64(0); i < allowance; i++ {
		if !deploy(pathFormation, 100) {
			t.Fatalf("deploy %d of the day was not free", i+1)
		}
	}
	if deploy(pathFormation, 200) {
		t.Fatal("a deploy past the day's allowance was free")
	}
	if !deploy(pathFormation, 100+1440) {
		t.Fatal("the next world day's first deploy was not free")
	}
	if deploy(pathSword, 5000) {
		t.Fatal("a Sword Cultivator deployed an array for free")
	}
}

// --- Qi Refiner: Refined Circulation ----------------------------------------

// TestTheRefinedCirculationIsAQiRefinersAlone. The path check in
// refinedCirculationStance is belt-and-braces today: only the Qi Refiner's trait
// carries a `stance_gain_mult`, so dropping the check alone leaves this green
// (rc.53's `!ok`). Drill both - the check and the `<= 0` - and the Sword
// Cultivator takes the stance; add the key to applyStanceToTraining's force
// case and a deviation lands.
func TestTheRefinedCirculationIsAQiRefinersAlone(t *testing.T) {
	path := setupCultivationDB(t)
	world := batch4WorldPath(t)
	catalog := crossingCatalog(t)
	trait, _ := pathTrait(catalog, pathQi)
	batch4Exec(t, path, `UPDATE characters SET realm_index=1,phase=3,cultivation=0 WHERE user_id=42`)
	if _, err := batch4ApplyErr(path, world, "cultivation.stance", 42, 1, map[string]any{"stance": qiRefinerStanceKey}); err == nil || !strings.Contains(err.Error(), "only a Qi Refiner") {
		t.Fatalf("a Sword Cultivator took the refined circulation: %v", err)
	}
	batch4Exec(t, path, `UPDATE characters SET path=? WHERE user_id=42`, pathQi)
	set := batch4Result(t, batch4Apply(t, path, world, "cultivation.stance", 2, map[string]any{"stance": qiRefinerStanceKey}))
	if set["stance"] != qiRefinerStanceKey || set["gain_mult"] != trait.StanceGainMult {
		t.Fatalf("a Qi Refiner's stance: %v", set)
	}
	// The dice that would make a Force session deviate every time.
	defer gamerng.UseRoller(func(int) int { return 0 })()
	trained := batch4Result(t, batch4Apply(t, path, world, "cultivation.train", 3, map[string]any{"game_minute": 600}))
	if trained["stance"] != qiRefinerStanceKey || trained["stance_mult"] != trait.StanceGainMult {
		t.Fatalf("training under the refined stance: %v", trained)
	}
	if dev, _ := trained["deviation"].(map[string]any); len(dev) != 0 || storage.ParseInt(trained["insight_xp_gain"]) != 0 {
		t.Fatalf("the refined stance risks nothing and banks nothing: deviation=%v insight=%v", trained["deviation"], trained["insight_xp_gain"])
	}
	if status := cultivationQuery(t, path, world, "cultivation.status", 42); len(status["stances"].([]map[string]any)) != 4 {
		t.Fatalf("a Qi Refiner is offered %v; the three every path takes and their own", status["stances"])
	}
	// A stored stance is not a licence: a cultivator who no longer walks the
	// path circulates.
	batch4Exec(t, path, `UPDATE characters SET path=? WHERE user_id=42`, pathSword)
	status := cultivationQuery(t, path, world, "cultivation.status", 42)
	if status["stance"] != stanceCirculate {
		t.Fatalf("a Sword Cultivator still holds %v", status["stance"])
	}
	if offered := status["stances"].([]map[string]any); len(offered) != 3 {
		t.Fatalf("a Sword Cultivator is offered %v", offered)
	}
}

// --- The hooks ---------------------------------------------------------------

// TestEveryPathAbilityIsAppliedWhereItHappens reads the call sites by AST. A
// helper's own test passes against a tree nothing calls it from, which is the
// shape TestEveryGoodDeedIsPaidWhereItHappens exists for.
func TestEveryPathAbilityIsAppliedWhereItHappens(t *testing.T) {
	want := map[string][]string{
		"combatTurnAction":            {"swordIntentCap", "defeatWoundSeverity"},
		"combatTechniqueAction":       {"defeatWoundSeverity"},
		"combatFinalizeAction":        {"bankSwordIntentTx"},
		"pvpActAction":                {"bankSwordIntentTx"},
		"settleVitalityRecoveryTx":    {"bodyRecoveryMult"},
		"tribulationAttemptAction":    {"heartWaveBonus"},
		"spiritSenseGainTx":           {"senseGainMult"},
		"deployArrayActionGo":         {"arrayDurationMult", "formationFreeDeployTx"},
		"cultivationStanceAction":     {"stanceForPath"},
		"loadCultivationStance":       {"stanceForPath"},
		"manualCultivationMultiplier": {"manualGatheringMult"},
		"cultivationManualAction":     {"manualGatheringMult"},
		"practiseCultivatedManualTx":  {"manualPracticeGain"},
	}
	calls := map[string]map[string]int{}
	fset := token.NewFileSet()
	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatal(err)
	}
	for _, file := range files {
		if strings.HasSuffix(file, "_test.go") {
			continue
		}
		parsed, err := parser.ParseFile(fset, file, nil, 0)
		if err != nil {
			t.Fatalf("%s: %v", file, err)
		}
		for _, decl := range parsed.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok || want[fn.Name.Name] == nil {
				continue
			}
			seen := map[string]int{}
			ast.Inspect(fn, func(n ast.Node) bool {
				if call, ok := n.(*ast.CallExpr); ok {
					if ident, ok := call.Fun.(*ast.Ident); ok {
						seen[ident.Name]++
					}
				}
				return true
			})
			calls[fn.Name.Name] = seen
		}
	}
	if len(calls) != len(want) {
		t.Fatalf("found %d of the %d hook functions; the reader is broken, not the tree", len(calls), len(want))
	}
	for fn, helpers := range want {
		for _, helper := range helpers {
			if calls[fn][helper] == 0 {
				t.Errorf("%s no longer calls %s, so that path ability does nothing", fn, helper)
			}
		}
	}
	// Both defeat branches of both fight paths: a fate rescue and a lost fight.
	for _, fn := range []string{"combatTurnAction", "combatTechniqueAction"} {
		if got := calls[fn]["defeatWoundSeverity"]; got != 2 {
			t.Errorf("%s lightens %d of its two defeat wounds", fn, got)
		}
	}
	if got := calls["pvpActAction"]["bankSwordIntentTx"]; got != 2 {
		t.Errorf("a duel is won two ways and %d of them bank intent", got)
	}
}

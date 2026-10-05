package game

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"testing"

	"xianxia/core/internal/storage"
)

// v1.28.0: a surrender to a bounty hunter closed the crime and the bounty for
// nothing, while atoning for the same crime cost restitution.

func bountyFixture(t *testing.T, stones int64) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS crime_records(crime_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, jurisdiction TEXT NOT NULL DEFAULT '', crime_type TEXT NOT NULL DEFAULT 'theft', severity INTEGER NOT NULL DEFAULT 1, evidence INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open', description TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS bounties(bounty_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, jurisdiction TEXT NOT NULL DEFAULT '', amount INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', reason TEXT NOT NULL DEFAULT '', source_crime_id INTEGER, created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS bounty_hunter_pursuits(pursuit_id INTEGER PRIMARY KEY AUTOINCREMENT, bounty_id INTEGER NOT NULL, user_id INTEGER NOT NULL, hunter_name TEXT NOT NULL DEFAULT 'Constable', hunter_power INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'tracking', pressure INTEGER NOT NULL DEFAULT 0, escape_progress INTEGER NOT NULL DEFAULT 0, capture_progress INTEGER NOT NULL DEFAULT 0, next_action_game_minute INTEGER NOT NULL DEFAULT 0, created_game_minute INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS item_provenance(provenance_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1, source_type TEXT NOT NULL DEFAULT '', source_key TEXT NOT NULL DEFAULT '', ownership_mark TEXT NOT NULL DEFAULT '', legal_status TEXT NOT NULL DEFAULT 'clean', authenticity INTEGER NOT NULL DEFAULT 100, tracking_strength INTEGER NOT NULL DEFAULT 0, acquired_game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS equipment_instances(equipment_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL, slot TEXT NOT NULL DEFAULT 'weapon', durability INTEGER NOT NULL DEFAULT 100, max_durability INTEGER NOT NULL DEFAULT 100, quality INTEGER NOT NULL DEFAULT 100, equipped INTEGER NOT NULL DEFAULT 0, bound_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0)`)
	batch4Exec(t, path, `UPDATE characters SET spirit_stones=? WHERE user_id=42`, stones)
	syncPurse(t, path)
	batch4Exec(t, path, `INSERT INTO crime_records(user_id,jurisdiction,severity,evidence) VALUES(42,'Greenriver Town',4,60)`)
	batch4Exec(t, path, `INSERT INTO bounties(user_id,jurisdiction,amount,source_crime_id) VALUES(42,'Greenriver Town',300,1)`)
	batch4Exec(t, path, `INSERT INTO bounty_hunter_pursuits(bounty_id,user_id,status) VALUES(1,42,'engaged')`)
	return path
}

func TestASurrenderPaysWhatAtoningWouldHave(t *testing.T) {
	path := bountyFixture(t, 1000)
	world := batch4WorldPath(t)
	out, err := batch4ApplyErr(path, world, "bounty_hunter.act", 42, 1, map[string]any{"pursuit_id": 1, "action": "surrender"})
	if err != nil {
		t.Fatalf("the surrender refused: %v", err)
	}
	want := crimeRestitutionFine(4, 60) // 4*25 + 6*5 = 130
	if want != 130 {
		t.Fatalf("the fine formula moved: %d", want)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)); got != 1000-want {
		t.Fatalf("a surrender left %d stones of 1000, want %d: it paid nothing", got, 1000-want)
	}
	result, _ := out.Result.(map[string]any)
	settlement, _ := result["settlement"].(map[string]any)
	if fmt.Sprint(settlement["paid"]) != fmt.Sprint(want) {
		t.Fatalf("the reply does not say what was paid: %v", result["settlement"])
	}
}

func TestAPoorFugitiveCanStillSurrender(t *testing.T) {
	path := bountyFixture(t, 40)
	if _, err := batch4ApplyErr(path, batch4WorldPath(t), "bounty_hunter.act", 42, 1, map[string]any{"pursuit_id": 1, "action": "surrender"}); err != nil {
		t.Fatalf("a fugitive with 40 stones could not surrender: %v", err)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)); got != 0 {
		t.Fatalf("the purse held %d after a surrender it could not cover, want 0", got)
	}
}

func TestACaptureCostsMoreThanASurrender(t *testing.T) {
	path := bountyFixture(t, 1000)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	catalog := eventScopeCatalog(t)
	out, err := SettleBountyTx(conn, catalog, 42, 1, true, 1)
	if err != nil {
		t.Fatal(err)
	}
	if storage.ParseInt(out["paid"]) != 195 {
		t.Fatalf("a capture paid %v, want 195 (half again the 130 a surrender pays)", out["paid"])
	}
	karma := storage.ParseInt(firstRowMap(mustExec(t, conn, `SELECT karma_score FROM characters WHERE user_id=42`))["karma_score"])
	if karma != 45 {
		t.Fatalf("karma after a capture is %d, want 45", karma)
	}
	rep := firstRowMap(mustExec(t, conn, `SELECT score FROM faction_reputation WHERE user_id=42 AND faction_key='Orthodox Society'`))
	if rep == nil || storage.ParseInt(rep["score"]) != -5 {
		t.Fatalf("Orthodox standing after a capture is %v, want -5", rep)
	}
}

// v1.28.0: Feed and Train raised a beast's intelligence and no rule read it.
func TestATrainedBeastFightsBetter(t *testing.T) {
	conn, err := storage.Open(t.TempDir() + "/beast.sqlite3")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE spirit_beasts(beast_id INTEGER PRIMARY KEY, user_id INTEGER, rank INTEGER, evolution_stage INTEGER, loyalty INTEGER, intelligence INTEGER NOT NULL DEFAULT 0, active INTEGER);
CREATE TABLE artifact_bonds(user_id INTEGER NOT NULL, item_id TEXT NOT NULL, bond_level INTEGER NOT NULL DEFAULT 0, awakened INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,item_id));
INSERT INTO spirit_beasts(user_id,rank,evolution_stage,loyalty,intelligence,active) VALUES(42,0,0,0,10,1);
`); err != nil {
		t.Fatal(err)
	}
	before, err := combatCompanionBonus(conn, 42)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`UPDATE spirit_beasts SET intelligence=80 WHERE user_id=42`, nil); err != nil {
		t.Fatal(err)
	}
	after, err := combatCompanionBonus(conn, 42)
	if err != nil {
		t.Fatal(err)
	}
	if after-before != 3 {
		t.Fatalf("training a beast from 10 to 80 intelligence moved its fight bonus by %d, want 3", after-before)
	}
	if beastIntelligenceBonus(1000) != beastIntelligenceCap {
		t.Fatalf("intelligence is uncapped: %d", beastIntelligenceBonus(1000))
	}
}

// v1.28.0: the hunt roll carries what steadies a fighter - `combat_bonus`
// effects and the companion beast - read by AST, because the hunt's dice are
// the content's and the shape is what this release changed.
func TestTheHuntRollCarriesTheFightersEdge(t *testing.T) {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "exploration_actions.go", nil, 0)
	if err != nil {
		t.Fatalf("cannot parse the hunt: %v", err)
	}
	var hunt *ast.FuncDecl
	for _, decl := range file.Decls {
		if fn, ok := decl.(*ast.FuncDecl); ok && fn.Name.Name == "explorationHuntAction" {
			hunt = fn
		}
	}
	if hunt == nil {
		t.Fatal("explorationHuntAction is gone; the gate is broken, not the tree")
	}
	edge, companion, combatBonus := false, false, false
	ast.Inspect(hunt, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		name := ""
		if id, ok := call.Fun.(*ast.Ident); ok {
			name = id.Name
		}
		if name == "canonicalAdditiveEffectBonus" && len(call.Args) == 6 {
			if lit, ok := call.Args[5].(*ast.BasicLit); ok && lit.Value == `"combat_bonus"` {
				combatBonus = true
			}
		}
		if name == "rollCheck" && len(call.Args) > 0 {
			ast.Inspect(call.Args[0], func(m ast.Node) bool {
				if id, ok := m.(*ast.Ident); ok {
					edge = edge || id.Name == "huntEdge"
					companion = companion || id.Name == "companion"
				}
				return true
			})
		}
		return true
	})
	if !combatBonus || !edge || !companion {
		t.Fatalf("the hunt roll: combat_bonus read=%v, effect added=%v, companion added=%v", combatBonus, edge, companion)
	}
}

func TestInfluenceAndAttentionAreWorthSomething(t *testing.T) {
	if sectInfluenceWarPower(45) != 2 || sectInfluenceWarPower(1000) != 3 || sectInfluenceWarPower(-5) != 0 {
		t.Fatalf("influence war power: 45->%d 1000->%d -5->%d", sectInfluenceWarPower(45), sectInfluenceWarPower(1000), sectInfluenceWarPower(-5))
	}
	if masterAttentionInsight(100) != 5 || masterAttentionInsight(10000) != 15 {
		t.Fatalf("attention insight: 100->%d 10000->%d", masterAttentionInsight(100), masterAttentionInsight(10000))
	}
}

// v1.28.0: the bot printed family influence and karmic reputation as terms of
// a sponsor's roll that the engine never rolled.
func TestASponsorWeighsTheHouseholdAndTheKarma(t *testing.T) {
	conn, err := storage.Open(t.TempDir() + "/rec.sqlite3")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE characters(user_id INTEGER PRIMARY KEY, karma_score INTEGER NOT NULL DEFAULT 0);
CREATE TABLE birth_families(family_id INTEGER PRIMARY KEY, influence INTEGER NOT NULL DEFAULT 0);
CREATE TABLE character_birth_family(user_id INTEGER PRIMARY KEY, family_id INTEGER NOT NULL);
INSERT INTO characters(user_id,karma_score) VALUES(42,60);
INSERT INTO birth_families(family_id,influence) VALUES(1,85);
INSERT INTO character_birth_family(user_id,family_id) VALUES(42,1);
`); err != nil {
		t.Fatal(err)
	}
	catalog := eventScopeCatalog(t)
	orthodox := ""
	for name, def := range catalog.Sects {
		if def.Alignment == "Orthodox" {
			orthodox = name
			break
		}
	}
	if orthodox == "" {
		t.Fatal("no orthodox sect in the shipped content")
	}
	got := map[string]int64{}
	for _, term := range recommendationTermsTx(conn, catalog, 42, 3, 2, 200, orthodox) {
		got[term["name"].(string)] = storage.ParseInt(term["value"])
	}
	want := map[string]int64{"presence": 3, "realm": 4, "sect standing": 3, "family influence": 2, "karmic reputation": 1}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("the sponsor weighed %v, want %v", got, want)
	}
}

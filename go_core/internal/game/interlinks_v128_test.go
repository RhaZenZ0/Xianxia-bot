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

// --- from interlinks_v127_test.go ---

// v1.27.0: a player's claim on neutral ground wrote no history, while the
// world's own sects wrote `territory_claimed` for theirs. One statement now.
func TestAPlayersClaimIsHeardLikeASects(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL,contribution_points INTEGER NOT NULL DEFAULT 0,influence INTEGER NOT NULL DEFAULT 0,contribution_earned INTEGER NOT NULL DEFAULT 0)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS territory_state(territory_key TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL, controller_type TEXT NOT NULL DEFAULT 'neutral', controller_key TEXT NOT NULL DEFAULT '', resource_type TEXT NOT NULL DEFAULT 'mixed', prosperity INTEGER NOT NULL DEFAULT 50, defense INTEGER NOT NULL DEFAULT 50, unrest INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS world_history_events(history_id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT UNIQUE, event_type TEXT, title TEXT, summary TEXT, significance INTEGER, visibility TEXT, location TEXT, world_name TEXT, faction TEXT, actor_type TEXT, actor_key TEXT, actor_name TEXT, target_type TEXT, target_key TEXT, target_name TEXT, related_user_id INTEGER, related_npc_name TEXT, tags TEXT, game_minute INTEGER, metadata_json TEXT, created_at REAL, updated_at REAL)`)
	batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(42,'Azure Cloud Sect','Deacon',40,0)`)
	batch4Exec(t, path, `INSERT INTO territory_state(territory_key,name,region,updated_at) VALUES('Riverguard City','Riverguard City','Riverguard City',0)`)
	batch4Exec(t, path, `UPDATE characters SET location='Riverguard City' WHERE user_id=42`)
	if _, err := batch4ApplyErr(path, world, "territory.claim", 42, 1, map[string]any{"territory_key": "Riverguard City"}); err != nil {
		t.Fatalf("the claim refused: %v", err)
	}
	row := fmt.Sprint(actionScalar(t, path, `SELECT event_type||'|'||significance||'|'||actor_key FROM world_history_events WHERE location='Riverguard City'`))
	if row != "territory_claimed|60|Azure Cloud Sect" {
		t.Fatalf("a player's claim left history %q, want the world's own territory_claimed row", row)
	}
	if n := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM world_history_events`)); n != 1 {
		t.Fatalf("%d history rows, want 1", n)
	}
	// v1.28.0: and the member who raised the banner is paid one war act.
	if got := storage.ParseInt(actionScalar(t, path, `SELECT contribution_earned FROM sect_membership WHERE user_id=42`)); got != WarRules(eventScopeCatalog(t)).ActPoints || got <= 0 {
		t.Fatalf("a claim on neutral ground paid %d contribution, want one war act (%d)", got, WarRules(eventScopeCatalog(t)).ActPoints)
	}
}

// v1.27.0: an awakened artifact bond counted in every fight whether or not
// the cultivator still had the artifact.
func TestABondCountsOnlyWhileTheArtifactIsHeld(t *testing.T) {
	conn, err := storage.Open(t.TempDir() + "/bond.sqlite3")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE spirit_beasts(beast_id INTEGER PRIMARY KEY, user_id INTEGER, rank INTEGER, evolution_stage INTEGER, loyalty INTEGER, intelligence INTEGER NOT NULL DEFAULT 0, active INTEGER);
CREATE TABLE artifact_bonds(user_id INTEGER NOT NULL, item_id TEXT NOT NULL, bond_level INTEGER NOT NULL DEFAULT 0, resonance INTEGER NOT NULL DEFAULT 0, awakened INTEGER NOT NULL DEFAULT 0, spirit_name TEXT NOT NULL DEFAULT '', temperament TEXT NOT NULL DEFAULT 'dormant', created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(user_id,item_id));
CREATE TABLE inventory(user_id INTEGER NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,item_id));
CREATE TABLE equipment_instances(equipment_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL, slot TEXT NOT NULL DEFAULT 'weapon', durability INTEGER NOT NULL DEFAULT 10, max_durability INTEGER NOT NULL DEFAULT 10, quality INTEGER NOT NULL DEFAULT 100, equipped INTEGER NOT NULL DEFAULT 0, bound_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0);
INSERT INTO artifact_bonds(user_id,item_id,bond_level,awakened) VALUES(42,'spirit_iron_sword',8,1);
`); err != nil {
		t.Fatal(err)
	}
	bonus := func() int64 {
		t.Helper()
		b, err := combatCompanionBonus(conn, 42)
		if err != nil {
			t.Fatal(err)
		}
		return b
	}
	if b := bonus(); b != 0 {
		t.Fatalf("a bond with a sword the cultivator no longer has is worth %d in a fight, want 0", b)
	}
	if _, err := conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_iron_sword@high',1)`, nil); err != nil {
		t.Fatal(err)
	}
	if b := bonus(); b != 3 {
		t.Fatalf("carrying the sword at a grade, the bond is worth %d, want 3", b)
	}
	if err := conn.ExecScript(`DELETE FROM inventory; INSERT INTO equipment_instances(user_id,item_id,equipped) VALUES(42,'spirit_iron_sword',1);`); err != nil {
		t.Fatal(err)
	}
	if b := bonus(); b != 3 {
		t.Fatalf("bound as equipment, the bond is worth %d, want 3", b)
	}
}

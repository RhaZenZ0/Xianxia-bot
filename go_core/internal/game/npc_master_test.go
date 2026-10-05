package game

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A sect's own people as masters, and a rank granted by somebody (v1.25.0).
// Driven through the production dispatch against the shipped catalogue,
// because the master's gifts, the rank bars and the promotion ladder are all
// content. No die is rolled anywhere here: a request, a teaching and a
// promotion are each a set of conditions.

const npcMasterSchema = `
CREATE TABLE IF NOT EXISTS sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL,contribution_points INTEGER NOT NULL DEFAULT 0,influence INTEGER NOT NULL DEFAULT 0,contribution_earned INTEGER NOT NULL DEFAULT 0,FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS sect_lineage(disciple_user_id INTEGER PRIMARY KEY,master_user_id INTEGER NOT NULL,accepted_at REAL NOT NULL,attention INTEGER NOT NULL DEFAULT 0,FOREIGN KEY(disciple_user_id) REFERENCES characters(user_id) ON DELETE CASCADE,FOREIGN KEY(master_user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS disciple_requests(request_id INTEGER PRIMARY KEY AUTOINCREMENT,disciple_user_id INTEGER NOT NULL,master_user_id INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at REAL NOT NULL,resolved_at REAL);
CREATE TABLE IF NOT EXISTS npc_mentorships(disciple_user_id INTEGER PRIMARY KEY,master_npc_name TEXT NOT NULL,sect_name TEXT NOT NULL,accepted_game_minute INTEGER NOT NULL DEFAULT 0,attention INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,FOREIGN KEY(disciple_user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS npc_civilization_state(npc_name TEXT PRIMARY KEY,home_location TEXT,current_location TEXT,world_name TEXT,profession TEXT,faction TEXT,wealth INTEGER,influence INTEGER,ambition INTEGER,realm_index INTEGER,phase INTEGER,status TEXT,activity TEXT,missing_since_game_minute INTEGER DEFAULT 0,last_game_minute INTEGER,updated_at REAL);
CREATE TABLE IF NOT EXISTS npc_life_state(npc_name TEXT PRIMARY KEY,birth_game_minute INTEGER,age_at_creation_years INTEGER,natural_lifespan_years INTEGER,health INTEGER,injury TEXT,injury_severity INTEGER,sect_rank TEXT,career_progress INTEGER,relationship_status TEXT,spouse_name TEXT,children_count INTEGER,last_social_game_minute INTEGER,last_cultivation_game_minute INTEGER,updated_at REAL);
CREATE TABLE IF NOT EXISTS event_log(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS character_manuals(user_id INTEGER NOT NULL,manual_id TEXT NOT NULL,mastery INTEGER NOT NULL DEFAULT 0,practice INTEGER NOT NULL DEFAULT 0,learned_at REAL NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(user_id,manual_id));
CREATE TABLE IF NOT EXISTS item_provenance(provenance_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,item_id TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 1,source_type TEXT NOT NULL DEFAULT 'unknown',source_key TEXT NOT NULL DEFAULT '',ownership_mark TEXT NOT NULL DEFAULT '',legal_status TEXT NOT NULL DEFAULT 'clean',authenticity INTEGER NOT NULL DEFAULT 100,tracking_strength INTEGER NOT NULL DEFAULT 0,acquired_game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL);
`

const masterHall = "Cloudblade City"

// npcMasterDB stands character 42 (realm 0, stage 9) in an Azure Cloud hall
// beside three of the sect's people: an Elder and a Core Disciple who stand
// above them, and an Outer Disciple who does not.
func npcMasterDB(t *testing.T, rankName string, rankLevel, earned int64) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(npcMasterSchema); err != nil {
		_ = conn.Close()
		t.Fatal(err)
	}
	_ = conn.Close()
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id IN (42,43)`, masterHall)
	batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at,contribution_earned) VALUES(42,'Azure Cloud Sect',?,?,0,?)`, rankName, rankLevel, earned)
	for _, npc := range []struct {
		name, sect, rank string
		realm            int64
	}{
		{"Elder Test Qiu", "Azure Cloud Sect", "Elder", 4},
		{"Core Test Lan", "Azure Cloud Sect", "Core Disciple", 2},
		{"Outer Test Mo", "Azure Cloud Sect", "Outer Disciple", 0},
		{"Furnace Test Ren", "Crimson Furnace Sect", "Elder", 5},
	} {
		batch4Exec(t, path, `INSERT INTO npc_civilization_state(npc_name,home_location,current_location,world_name,profession,faction,wealth,influence,ambition,realm_index,phase,status,activity,last_game_minute,updated_at)
            VALUES(?,?,?,'Mortal World','',?,10,10,10,?,5,'alive','',0,0)`, npc.name, masterHall, masterHall, npc.sect, npc.realm)
		batch4Exec(t, path, `INSERT INTO npc_life_state(npc_name,birth_game_minute,age_at_creation_years,natural_lifespan_years,health,injury,injury_severity,sect_rank,career_progress,relationship_status,spouse_name,children_count,last_social_game_minute,last_cultivation_game_minute,updated_at)
            VALUES(?,0,40,90,100,'',0,?,0,'single','',0,0,0,0)`, npc.name, npc.rank)
	}
	return path
}

func masterOpWorld(t *testing.T, path, op string, seq int, payload map[string]any) (map[string]any, error) {
	t.Helper()
	out, err := batch4ApplyErr(path, batch4WorldPath(t), op, 42, seq, payload)
	if err != nil {
		return nil, err
	}
	res, _ := out.Result.(map[string]any)
	return res, nil
}

func TestAMemberTakesOneOfTheSectsPeopleAsMaster(t *testing.T) {
	path := npcMasterDB(t, "Outer Disciple", 10, 0)
	out, err := masterOpWorld(t, path, "discipleship.npc_request", 1, map[string]any{"npc_name": "Elder Test Qiu"})
	if err != nil {
		t.Fatalf("an Elder standing beside a weaker member refused them: %v", err)
	}
	if out["master_npc_name"] != "Elder Test Qiu" {
		t.Fatalf("the bond named %v", out["master_npc_name"])
	}
	if got := actionScalar(t, path, `SELECT master_npc_name FROM npc_mentorships WHERE disciple_user_id=42`); fmt.Sprint(got) != "Elder Test Qiu" {
		t.Fatalf("no bond was written: %v", got)
	}
	if _, err := masterOpWorld(t, path, "discipleship.npc_request", 2, map[string]any{"npc_name": "Core Test Lan"}); err == nil || !strings.Contains(err.Error(), "already have a recorded master") {
		t.Fatalf("a second master was taken: %v", err)
	}
	// Leaving severs the NPC bond the way it severs a player one.
	if _, err := masterOpWorld(t, path, "discipleship.leave", 3, map[string]any{}); err != nil {
		t.Fatalf("leaving an NPC master refused: %v", err)
	}
	if got := actionScalar(t, path, `SELECT COUNT(*) FROM npc_mentorships WHERE disciple_user_id=42`); fmt.Sprint(got) != "0" {
		t.Fatalf("leaving left %v bond(s)", got)
	}
}

func TestAMasterIsAskedOnlyWhereTheConditionsHold(t *testing.T) {
	cases := []struct {
		name, npc, refusal string
		setup              string
	}{
		{"of another sect", "Furnace Test Ren", "is not one of the Azure Cloud Sect's people", ""},
		{"below the rank", "Outer Test Mo", "a master is a Core Disciple or above", ""},
		{"no stronger", "Core Test Lan", "does not stand above you", `UPDATE characters SET realm_index=5 WHERE user_id=42`},
		{"elsewhere", "Elder Test Qiu", "is not here", `UPDATE npc_civilization_state SET current_location='Greenriver Town' WHERE npc_name='Elder Test Qiu'`},
		{"dead", "Elder Test Qiu", "is dead", `UPDATE npc_civilization_state SET status='dead' WHERE npc_name='Elder Test Qiu'`},
		{"nobody", "Nobody At All", "is not one of", ""},
	}
	for i, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			path := npcMasterDB(t, "Outer Disciple", 10, 0)
			if tc.setup != "" {
				batch4Exec(t, path, tc.setup)
			}
			_, err := masterOpWorld(t, path, "discipleship.npc_request", i+1, map[string]any{"npc_name": tc.npc})
			if err == nil || !strings.Contains(err.Error(), tc.refusal) {
				t.Fatalf("want a refusal naming %q, got %v", tc.refusal, err)
			}
		})
	}
}

func TestAMasterKeepsOnlySoManyDisciples(t *testing.T) {
	path := npcMasterDB(t, "Outer Disciple", 10, 0)
	catalog := crossingCatalog(t)
	limit := npcMasterRuleGo(catalog).MaxDisciples
	if limit <= 0 {
		t.Fatal("the shipped npc_master.max_disciples is unset; the test needs a cap to fill")
	}
	// Fill the Elder's cap with character 43 and copies of them: the count is
	// what the rule reads.
	batch4Exec(t, path, `INSERT INTO npc_mentorships(disciple_user_id,master_npc_name,sect_name,created_at) VALUES(43,'Elder Test Qiu','Azure Cloud Sect',0)`)
	for i := int64(1); i < limit; i++ {
		batch4Exec(t, path, `INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,cultivation,body_realm_index,body_phase,body_cultivation,life_status,karma_score,qi,qi_max,vitality,vitality_max)
            SELECT ?,name||?,gender,path,spiritual_root,location,attributes_json,realm_index,phase,cultivation,body_realm_index,body_phase,body_cultivation,life_status,karma_score,qi,qi_max,vitality,vitality_max FROM characters WHERE user_id=43`, 9000+i, fmt.Sprint(i))
		batch4Exec(t, path, `INSERT INTO npc_mentorships(disciple_user_id,master_npc_name,sect_name,created_at) VALUES(?,'Elder Test Qiu','Azure Cloud Sect',0)`, 9000+i)
	}
	if _, err := masterOpWorld(t, path, "discipleship.npc_request", 1, map[string]any{"npc_name": "Elder Test Qiu"}); err == nil || !strings.Contains(err.Error(), "will take no more") {
		t.Fatalf("a master past their cap took another disciple: %v", err)
	}
}

func TestAMastersDeathEndsTheBond(t *testing.T) {
	path := npcMasterDB(t, "Outer Disciple", 10, 0)
	if _, err := masterOpWorld(t, path, "discipleship.npc_request", 1, map[string]any{"npc_name": "Elder Test Qiu"}); err != nil {
		t.Fatal(err)
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := ReleaseNPCBondsTx(conn, "Elder Test Qiu", 10, 0); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()
	if got := actionScalar(t, path, `SELECT COUNT(*) FROM npc_mentorships`); fmt.Sprint(got) != "0" {
		t.Fatalf("a dead master still keeps %v disciple(s)", got)
	}
}

func TestAMasterHelpsTheBreakthroughAndTheCultivation(t *testing.T) {
	path := npcMasterDB(t, "Outer Disciple", 10, 0)
	catalog := crossingCatalog(t)
	rule := npcMasterRuleGo(catalog)
	if rule.BreakthroughBonus <= 0 || rule.CultivationMult <= 1 || rule.InsightOnRealm <= 0 {
		t.Fatalf("the shipped npc_master gives nothing (%+v); the test needs every gift", rule)
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if bonus, _ := npcMasterBreakthroughBonusTx(conn, catalog, 42); bonus != 0 {
		t.Fatalf("a member with no master had a breakthrough bonus of %d", bonus)
	}
	if _, err := conn.Execute(`INSERT INTO npc_mentorships(disciple_user_id,master_npc_name,sect_name,created_at) VALUES(42,'Elder Test Qiu','Azure Cloud Sect',0)`, nil); err != nil {
		t.Fatal(err)
	}
	bonus, name := npcMasterBreakthroughBonusTx(conn, catalog, 42)
	if bonus != rule.BreakthroughBonus || name != "Elder Test Qiu" {
		t.Fatalf("the master's breakthrough term is %d from %q, want %d", bonus, name, rule.BreakthroughBonus)
	}
	odds := cultivationOddsResult(mechanicsCharacter{Attributes: map[string]int64{"will": 5}}, catalog, resolvedModifiers{}, false, false, bonus, name)
	without := cultivationOddsResult(mechanicsCharacter{Attributes: map[string]int64{"will": 5}}, catalog, resolvedModifiers{}, false, false, 0, "")
	if i64(odds["modifier"])-i64(without["modifier"]) != bonus {
		t.Fatalf("the odds card added %d for the master, want %d", i64(odds["modifier"])-i64(without["modifier"]), bonus)
	}
	if mult, _ := npcMasterCultivationMultTx(conn, catalog, 42); mult != rule.CultivationMult {
		t.Fatalf("the master's cultivation term is %v, want %v", mult, rule.CultivationMult)
	}
	if carried := loadSeclusionCarried(conn, catalog, 42, 10, "qi"); carried.Master != rule.CultivationMult || carried.MasterName != "Elder Test Qiu" {
		t.Fatalf("a retreat carried the master as %v (%q)", carried.Master, carried.MasterName)
	}
	got, err := npcMasterRealmInsightTx(conn, catalog, 42, 0)
	if err != nil || got == nil || i64(got["insight_xp"]) <= 0 {
		t.Fatalf("a realm crossing under a master paid %v (%v)", got, err)
	}
	// A dead master gives nothing, whatever a lagging cleanup left behind.
	if _, err := conn.Execute(`UPDATE npc_civilization_state SET status='dead' WHERE npc_name='Elder Test Qiu'`, nil); err != nil {
		t.Fatal(err)
	}
	if bonus, _ := npcMasterBreakthroughBonusTx(conn, catalog, 42); bonus != 0 {
		t.Fatalf("a dead master still helped the breakthrough by %d", bonus)
	}
}

func TestTheMasterTeachesTheSectsArtOncePerLife(t *testing.T) {
	catalog := crossingCatalog(t)
	teach := npcMasterRuleGo(catalog).TeachRankLevel
	if teach <= 10 {
		t.Fatalf("the shipped teach rank is %d; the test stands an Outer Disciple below it", teach)
	}
	path := npcMasterDB(t, "Outer Disciple", 10, 0)
	if _, err := masterOpWorld(t, path, "sect.master.teach", 1, map[string]any{}); err == nil || !strings.Contains(err.Error(), "no living master") {
		t.Fatalf("somebody with no master was taught: %v", err)
	}
	batch4Exec(t, path, `INSERT INTO npc_mentorships(disciple_user_id,master_npc_name,sect_name,created_at) VALUES(42,'Elder Test Qiu','Azure Cloud Sect',0)`)
	if _, err := masterOpWorld(t, path, "sect.master.teach", 2, map[string]any{}); err == nil || !strings.Contains(err.Error(), sectRankName(catalog, teach)) {
		t.Fatalf("an Outer Disciple was taught below %s: %v", sectRankName(catalog, teach), err)
	}
	batch4Exec(t, path, `UPDATE sect_membership SET rank_level=?,rank_name=? WHERE user_id=42`, teach, sectRankName(catalog, teach))
	out, err := masterOpWorld(t, path, "sect.master.teach", 3, map[string]any{})
	if err != nil {
		t.Fatalf("a %s with a master was not taught: %v", sectRankName(catalog, teach), err)
	}
	if m, ok := catalog.TechniqueSystem.Manuals[fmt.Sprint(out["manual_id"])]; !ok || !strings.EqualFold(m.Sect, "Azure Cloud Sect") {
		t.Fatalf("the master taught %v, which is not the Azure Cloud's own", out["manual_id"])
	}
	if got := actionScalar(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id=?`, out["item_id"]); fmt.Sprint(got) != "1" {
		t.Fatalf("the manual is not in the bag: %v", got)
	}
	if _, err := masterOpWorld(t, path, "sect.master.teach", 4, map[string]any{}); err == nil || !strings.Contains(err.Error(), "already taught you") {
		t.Fatalf("a master taught twice in one life: %v", err)
	}
}

func TestARankIsGrantedByAskingAndOnlyByWhoMay(t *testing.T) {
	catalog := crossingCatalog(t)
	ladder := catalog.SectExchange().Promotion
	inner := ladder[0]
	path := npcMasterDB(t, "Outer Disciple", 10, inner.Earned-1)
	if _, err := masterOpWorld(t, path, "sect.promote", 1, map[string]any{"npc_name": "Elder Test Qiu"}); err == nil || !strings.Contains(err.Error(), fmt.Sprintf("asks %d contribution earned", inner.Earned)) {
		t.Fatalf("a member a point short was promoted: %v", err)
	}
	batch4Exec(t, path, `UPDATE sect_membership SET contribution_earned=? WHERE user_id=42`, inner.Earned)
	// A Core Disciple who is not their master is not somebody who grants ranks.
	if _, err := masterOpWorld(t, path, "sect.promote", 2, map[string]any{"npc_name": "Core Test Lan"}); err == nil || !strings.Contains(err.Error(), "granted by your master or") {
		t.Fatalf("a Core Disciple who is not the member's master granted a rank: %v", err)
	}
	batch4Exec(t, path, `UPDATE npc_civilization_state SET current_location='Greenriver Town' WHERE npc_name='Elder Test Qiu'`)
	if _, err := masterOpWorld(t, path, "sect.promote", 3, map[string]any{"npc_name": "Elder Test Qiu"}); err == nil || !strings.Contains(err.Error(), "is not here") {
		t.Fatalf("an Elder granted a rank from another city: %v", err)
	}
	batch4Exec(t, path, `UPDATE npc_civilization_state SET current_location=? WHERE npc_name='Elder Test Qiu'`, masterHall)
	out, err := masterOpWorld(t, path, "sect.promote", 4, map[string]any{"npc_name": "Elder Test Qiu"})
	if err != nil {
		t.Fatalf("an Elder beside an eligible member refused the rank: %v", err)
	}
	if out["promoted_to"] != sectRankName(catalog, inner.RankLevel) {
		t.Fatalf("promoted to %v", out["promoted_to"])
	}
	if got := actionScalar(t, path, `SELECT rank_level FROM sect_membership WHERE user_id=42`); fmt.Sprint(got) != fmt.Sprint(inner.RankLevel) {
		t.Fatalf("the rank is %v", got)
	}
	// A master grants too, from below the promoter rank - but never a rank at
	// or above their own.
	core := ladder[1]
	batch4Exec(t, path, `UPDATE sect_membership SET contribution_earned=? WHERE user_id=42`, core.Earned)
	batch4Exec(t, path, `INSERT INTO npc_mentorships(disciple_user_id,master_npc_name,sect_name,created_at) VALUES(42,'Core Test Lan','Azure Cloud Sect',0)`)
	if _, err := masterOpWorld(t, path, "sect.promote", 5, map[string]any{"npc_name": "Core Test Lan"}); err == nil || !strings.Contains(err.Error(), "cannot raise anybody to") {
		t.Fatalf("a Core Disciple master raised a disciple to their own rank: %v", err)
	}
}

func TestPromotionNeedsTheMigratedTableOnlyForAMaster(t *testing.T) {
	// The request reads npc_mentorships; without it the refusal is a sentence,
	// not a SQL error.
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, `CREATE TABLE sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL)`)
	raw, _ := json.Marshal(map[string]any{"npc_name": "Elder Test Qiu"})
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		_, err := npcMasterRequestAction(conn, crossingCatalog(t), 42, raw)
		return err
	})
	if err == nil || !strings.Contains(err.Error(), "migrated") {
		t.Fatalf("want the migration refusal, got %v", err)
	}
}

// Every gift is applied where it happens: the helpers' own tests above pass
// against a tree nothing calls them from (TestEveryGoodDeedIsPaidWhereItHappens'
// reason).
func TestEveryMasterGiftIsAppliedWhereItHappens(t *testing.T) {
	want := map[string][]string{
		"cultivationTrain":        {"npcMasterCultivationMultTx"},
		"loadSeclusionCarried":    {"npcMasterCultivationMultTx"},
		"cultivationBreakthrough": {"npcMasterBreakthroughBonusTx", "npcMasterRealmInsightTx"},
		"cultivationStatusQuery":  {"npcMasterBreakthroughBonusTx"},
	}
	found := map[string]map[string]bool{}
	fset := token.NewFileSet()
	for _, file := range []string{"cultivation_actions.go", "root_worth.go", "cultivation_stance.go"} {
		parsed, err := parser.ParseFile(fset, file, nil, 0)
		if err != nil {
			t.Fatalf("%s: %v", file, err)
		}
		for _, decl := range parsed.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok {
				continue
			}
			if _, wanted := want[fn.Name.Name]; !wanted {
				continue
			}
			found[fn.Name.Name] = map[string]bool{}
			ast.Inspect(fn, func(n ast.Node) bool {
				if call, ok := n.(*ast.CallExpr); ok {
					if ident, ok := call.Fun.(*ast.Ident); ok {
						found[fn.Name.Name][ident.Name] = true
					}
				}
				return true
			})
		}
	}
	for fn, calls := range want {
		if found[fn] == nil {
			t.Errorf("%s was not found; the walk is broken, not the tree", fn)
			continue
		}
		for _, call := range calls {
			if !found[fn][call] {
				t.Errorf("%s does not call %s: an NPC master's gift is not applied there", fn, call)
			}
		}
	}
}

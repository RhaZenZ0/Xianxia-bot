package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"strings"
	"testing"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The war door (v1.24.0): what fighting pays, the walls, the truce, the
// occupation, and who may fight beside whom. war.act is driven through the
// production dispatch on the shipped content; the resolution and the
// declaration are driven on the door itself.

const warFixtureSchema = `
CREATE TABLE IF NOT EXISTS equipment_instances (
    equipment_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL, slot TEXT NOT NULL,
    durability INTEGER NOT NULL, max_durability INTEGER NOT NULL, quality INTEGER NOT NULL DEFAULT 100,
    equipped INTEGER NOT NULL DEFAULT 0, bound_at REAL NOT NULL, updated_at REAL NOT NULL,
    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS sect_membership (
    user_id INTEGER PRIMARY KEY, sect_name TEXT NOT NULL, rank_name TEXT NOT NULL DEFAULT 'Disciple',
    rank_level INTEGER NOT NULL DEFAULT 0, joined_at REAL NOT NULL, contribution_points INTEGER NOT NULL DEFAULT 0,
    influence INTEGER NOT NULL DEFAULT 0, contribution_earned INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS sect_relations (
    sect_a TEXT NOT NULL, sect_b TEXT NOT NULL, relation_score INTEGER NOT NULL DEFAULT 0, relation_type TEXT NOT NULL DEFAULT 'neutral',
    treaty_status TEXT NOT NULL DEFAULT 'none', updated_at REAL NOT NULL, PRIMARY KEY(sect_a,sect_b));
CREATE TABLE IF NOT EXISTS sect_manors (
    sect_name TEXT PRIMARY KEY, name TEXT NOT NULL, base_location TEXT NOT NULL, qi_array_level INTEGER NOT NULL DEFAULT 0,
    alchemy_hall_level INTEGER NOT NULL DEFAULT 0, forge_pavilion_level INTEGER NOT NULL DEFAULT 0,
    defense_array_level INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS territory_state (
    territory_key TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL,
    controller_type TEXT NOT NULL DEFAULT 'neutral', controller_key TEXT NOT NULL DEFAULT '',
    resource_type TEXT NOT NULL DEFAULT 'mixed', prosperity INTEGER NOT NULL DEFAULT 50,
    defense INTEGER NOT NULL DEFAULT 50, unrest INTEGER NOT NULL DEFAULT 0,
    updated_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS territory_wars (
    war_id INTEGER PRIMARY KEY AUTOINCREMENT, attacker_key TEXT NOT NULL, defender_key TEXT NOT NULL,
    territory_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    attacker_score INTEGER NOT NULL DEFAULT 0, defender_score INTEGER NOT NULL DEFAULT 0,
    created_game_minute INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    FOREIGN KEY(territory_key) REFERENCES territory_state(territory_key) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS territory_war_operations (
    war_id INTEGER PRIMARY KEY, siege_progress INTEGER NOT NULL DEFAULT 0, attacker_morale INTEGER NOT NULL DEFAULT 100,
    defender_morale INTEGER NOT NULL DEFAULT 100, attacker_force INTEGER NOT NULL DEFAULT 0, defender_force INTEGER NOT NULL DEFAULT 0,
    last_tick_game_minute INTEGER NOT NULL DEFAULT 0, winner_key TEXT NOT NULL DEFAULT '', resolution TEXT NOT NULL DEFAULT '',
    occupation_until_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
    FOREIGN KEY(war_id) REFERENCES territory_wars(war_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS territory_war_actions (
    action_id INTEGER PRIMARY KEY AUTOINCREMENT, war_id INTEGER NOT NULL, user_id INTEGER, side TEXT NOT NULL, tactic TEXT NOT NULL,
    power INTEGER NOT NULL DEFAULT 0, siege_delta INTEGER NOT NULL DEFAULT 0, morale_delta INTEGER NOT NULL DEFAULT 0,
    game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
    FOREIGN KEY(war_id) REFERENCES territory_wars(war_id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS world_history_events (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL,
    title TEXT NOT NULL, summary TEXT NOT NULL, significance INTEGER NOT NULL DEFAULT 50,
    visibility TEXT NOT NULL DEFAULT 'public', location TEXT NOT NULL DEFAULT '', world_name TEXT NOT NULL DEFAULT '',
    faction TEXT NOT NULL DEFAULT '', actor_type TEXT NOT NULL DEFAULT '', actor_key TEXT NOT NULL DEFAULT '',
    actor_name TEXT NOT NULL DEFAULT '', target_type TEXT NOT NULL DEFAULT '', target_key TEXT NOT NULL DEFAULT '',
    target_name TEXT NOT NULL DEFAULT '', related_user_id INTEGER, related_npc_name TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '', game_minute INTEGER NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
`

// warDB is the batch-4 cultivator (42) sworn to the Attacking Sect, a second
// (43) to the Holding Sect, a third (44) to an Allied Sect bound to the
// holders by a marriage pact, and one war over the ford.
func warDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(warFixtureSchema); err != nil {
		t.Fatal(err)
	}
	for _, uid := range []int64{44, 45} {
		if _, err := conn.Execute(`CREATE TEMP TABLE IF NOT EXISTS war_clone AS SELECT * FROM characters WHERE 0`, nil); err != nil {
			t.Fatal(err)
		}
		for _, sql := range []string{
			`DELETE FROM war_clone`,
			`INSERT INTO war_clone SELECT * FROM characters WHERE user_id=42`,
			fmt.Sprintf(`UPDATE war_clone SET user_id=%d,name='Fighter %d'`, uid, uid),
			`INSERT INTO characters SELECT * FROM war_clone`,
		} {
			if _, err := conn.Execute(sql, nil); err != nil {
				t.Fatal(err)
			}
		}
	}
	for _, sql := range []string{
		// Core Disciples (v1.25.0): fighting in a war asks sect_system.rank_floors["war.act"].
		`INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(42,'Attacking Sect','Core Disciple',30,0),(43,'Holding Sect','Core Disciple',30,0),(44,'Allied Sect','Core Disciple',30,0),(45,'Stranger Sect','Core Disciple',30,0)`,
		`INSERT INTO sect_relations(sect_a,sect_b,relation_score,relation_type,updated_at) VALUES('Allied Sect','Holding Sect',20,'marriage_pact',0),('Attacking Sect','Allied Sect',0,'neutral',0),('Attacking Sect','Holding Sect',0,'neutral',0)`,
		`INSERT INTO territory_state(territory_key,name,region,controller_type,controller_key,defense,updated_at) VALUES('the_ford','The Ford','Greenriver Town','sect','Holding Sect',50,0)`,
	} {
		if _, err := conn.Execute(sql, nil); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := DeclareWarTx(conn, worlddata.Catalog{}, "Attacking Sect", "Holding Sect", "the_ford", 0, 0); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

func warAct(t *testing.T, path, world string, actor int64, seq int, tactic string) (map[string]any, error) {
	t.Helper()
	batch4Exec(t, path, `DELETE FROM cooldowns`)
	out, err := batch4ApplyErr(path, world, "war.act", actor, seq, map[string]any{"war_id": 1, "tactic": tactic})
	if err != nil {
		return nil, err
	}
	return batch4Result(t, out), nil
}

func shippedWarRules(t *testing.T, world string) WarRuleSet {
	t.Helper()
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	return WarRules(catalog)
}

func TestAWarActPaysTheFighterUpToTheCap(t *testing.T) {
	path := warDB(t)
	world := batch4WorldPath(t)
	rules := shippedWarRules(t, world)
	acts := int(rules.WarPointsCap/rules.ActPoints) + 2
	for i := 1; i <= acts; i++ {
		// Fortify as the attacker steadies its own morale and moves no
		// siege, so the war cannot end under the test.
		if _, err := warAct(t, path, world, 42, i, "fortify"); err != nil {
			t.Fatalf("act %d: %v", i, err)
		}
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT contribution_earned FROM sect_membership WHERE user_id=42`)); got != rules.WarPointsCap {
		t.Fatalf("%d acts earned %d points; the cap is %d", acts, got, rules.WarPointsCap)
	}
}

func TestTheHoldersFortifyRaisesTheWalls(t *testing.T) {
	path := warDB(t)
	world := batch4WorldPath(t)
	rules := shippedWarRules(t, world)
	out, err := warAct(t, path, world, 43, 1, "fortify")
	if err != nil {
		t.Fatal(err)
	}
	want := 50 + rules.FortifyDefenseGain
	if got := storage.ParseInt(actionScalar(t, path, `SELECT defense FROM territory_state`)); got != want {
		t.Fatalf("a defender's fortify left the walls at %d, want %d", got, want)
	}
	if storage.ParseInt(out["territory_defense"]) != want {
		t.Fatalf("the reply says the walls stand at %v", out["territory_defense"])
	}
	// An attacker's fortify is its own morale, not the enemy's walls.
	if _, err := warAct(t, path, world, 42, 2, "fortify"); err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT defense FROM territory_state`)); got != want {
		t.Fatalf("the attacker's fortify raised the defender's walls to %d", got)
	}
}

func TestAnAllyFightsBesideItsAlly(t *testing.T) {
	path := warDB(t)
	world := batch4WorldPath(t)
	rules := shippedWarRules(t, world)
	out, err := warAct(t, path, world, 44, 1, "repel")
	if err != nil {
		t.Fatalf("a member of a sect in a marriage pact with the holders was refused: %v", err)
	}
	if out["side"] != "defender" || out["fights_for"] != "Holding Sect" || out["ally"] != true || out["ally_joined"] != true {
		t.Fatalf("the ally fought as %v for %v (ally=%v joined=%v)", out["side"], out["fights_for"], out["ally"], out["ally_joined"])
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT relation_score FROM sect_relations WHERE sect_a='Attacking Sect' AND sect_b='Allied Sect'`)); got != -rules.AllyRelationDrop {
		t.Fatalf("joining the war left the ally's standing with the enemy at %d", got)
	}
	out, err = warAct(t, path, world, 44, 2, "repel")
	if err != nil {
		t.Fatal(err)
	}
	if out["ally_joined"] != false {
		t.Fatal("an ally joined the same war twice")
	}
	if got := storage.ParseInt(actionScalar(t, path, `SELECT relation_score FROM sect_relations WHERE sect_a='Attacking Sect' AND sect_b='Allied Sect'`)); got != -rules.AllyRelationDrop {
		t.Fatalf("a second act cost standing again: %d", got)
	}
	// A sect allied to nobody in the war is refused.
	if _, err := warAct(t, path, world, 45, 3, "assault"); err == nil || !strings.Contains(err.Error(), "nor allied to one") {
		t.Fatalf("a stranger fought in somebody else's war: %v", err)
	}
	// And a sect allied to both may not choose.
	batch4Exec(t, path, `UPDATE sect_relations SET relation_score=? WHERE sect_a='Attacking Sect' AND sect_b='Allied Sect'`, rules.AllyMinRelation)
	if _, err := warAct(t, path, world, 44, 4, "repel"); err == nil || !strings.Contains(err.Error(), "allied to both") {
		t.Fatalf("a sect allied to both sides took one: %v", err)
	}
}

func warConn(t *testing.T, path string) *storage.Conn {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	return conn
}

func TestWinningPaysEveryVictorOnce(t *testing.T) {
	path := warDB(t)
	conn := warConn(t, path)
	cat := worlddata.Catalog{}
	rules := WarRules(cat)
	for _, row := range [][]any{{42, "attacker"}, {42, "attacker"}, {44, "defender"}, {43, "defender"}} {
		if _, err := conn.Execute(`INSERT INTO territory_war_actions(war_id,user_id,side,tactic,created_at) VALUES(1,?,?,'assault',0)`, row); err != nil {
			t.Fatal(err)
		}
	}
	spoils, ended, err := ResolveWarTx(conn, cat, 1, "Holding Sect", "defender_holds", 5000, 0)
	if err != nil || !ended {
		t.Fatalf("ended=%v err=%v", ended, err)
	}
	paid := map[int64]int64{}
	for _, s := range spoils {
		paid[s.UserID] = s.Points
	}
	if len(paid) != 2 || paid[43] != rules.VictoryPoints || paid[44] != rules.VictoryPoints {
		t.Fatalf("the holders and their ally were paid %v; want 43 and 44 at %d each, and nobody else", paid, rules.VictoryPoints)
	}
	if _, ended, _ = ResolveWarTx(conn, cat, 1, "Attacking Sect", "attacker_occupation", 5001, 0); ended {
		t.Fatal("a resolved war was resolved again")
	}
	if n := storage.ParseInt(scalarOn(t, conn, `SELECT COUNT(*) FROM world_history_events WHERE event_type='territory_war_resolved'`)); n != 1 {
		t.Fatalf("the world heard %d endings", n)
	}
	if d := storage.ParseInt(scalarOn(t, conn, `SELECT defense FROM territory_state`)); d != 50+rules.HoldDefenseGain {
		t.Fatalf("a held siege left the walls at %d", d)
	}
}

func TestAFailedAttackBuysATruceAndAFallAnOccupation(t *testing.T) {
	path := warDB(t)
	conn := warConn(t, path)
	cat := worlddata.Catalog{}
	rules := WarRules(cat)
	if _, _, err := ResolveWarTx(conn, cat, 1, "Holding Sect", "defender_holds", 1000, 0); err != nil {
		t.Fatal(err)
	}
	_, err := DeclareWarTx(conn, cat, "Attacking Sect", "Holding Sect", "the_ford", 1000+warMinutesPerDay, 0)
	if !errors.Is(err, errWarTruce) {
		t.Fatalf("a sect thrown back declared again the next day: %v", err)
	}
	after := 1000 + rules.TruceDays*warMinutesPerDay
	warID, err := DeclareWarTx(conn, cat, "Attacking Sect", "Holding Sect", "the_ford", after, 0)
	if err != nil {
		t.Fatalf("the truce outlived its %d days: %v", rules.TruceDays, err)
	}
	// This time it falls, and the old holder may strike straight back: an
	// occupation binds the occupier, not the dispossessed.
	if _, _, err := ResolveWarTx(conn, cat, warID, "Attacking Sect", "attacker_occupation", after+10, 0); err != nil {
		t.Fatal(err)
	}
	if d := storage.ParseInt(scalarOn(t, conn, `SELECT defense FROM territory_state`)); d != rules.FallDefense {
		t.Fatalf("a fallen territory's walls stand at %d", d)
	}
	if from := WarOccupiedFromTx(conn, "the_ford", after+20); from != "Holding Sect" {
		t.Fatalf("the ford is occupied from %q", from)
	}
	retake, err := DeclareWarTx(conn, cat, "Holding Sect", "Attacking Sect", "the_ford", after+20, 0)
	if err != nil {
		t.Fatalf("the dispossessed holder could not strike back during the occupation: %v", err)
	}
	if _, _, err := ResolveWarTx(conn, cat, retake, "Holding Sect", "attacker_occupation", after+30, 0); err != nil {
		t.Fatal(err)
	}
	// The retaken ground's old occupation is over, so it cannot come due and
	// hand the ford back.
	if r := fmt.Sprint(scalarOn(t, conn, fmt.Sprintf(`SELECT resolution FROM territory_war_operations WHERE war_id=%d`, warID))); r != "occupation_lost" {
		t.Fatalf("the first occupation reads %q after the ford was retaken", r)
	}
}

func scalarOn(t *testing.T, conn *storage.Conn, sql string) any {
	t.Helper()
	r, err := conn.Execute(sql, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(r.Rows) == 0 || len(r.Rows[0]) == 0 {
		t.Fatalf("no row for %s", sql)
	}
	return r.Rows[0][0]
}

// The fronts read answers what war.act would take, and no more.
func TestTheFrontsOfferOnlyWarsTheActWouldTake(t *testing.T) {
	path := warDB(t)
	world := batch4WorldPath(t)
	fronts := func(actor int64) []any {
		t.Helper()
		out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, Operation: "war.fronts", ActorID: actor, Payload: []byte(`{}`)})
		if err != nil {
			t.Fatal(err)
		}
		// Through the wire, the way the bot reads it.
		raw, _ := json.Marshal(batch4Result(t, out)["wars"])
		var wars []any
		if err := json.Unmarshal(raw, &wars); err != nil {
			t.Fatal(err)
		}
		return wars
	}
	for actor, want := range map[int64]string{42: "attacker", 43: "defender", 44: "defender"} {
		wars := fronts(actor)
		if len(wars) != 1 {
			t.Fatalf("actor %d is offered %d war(s)", actor, len(wars))
		}
		if side := wars[0].(map[string]any)["side"]; side != want {
			t.Fatalf("actor %d is offered the %v side, war.act puts them on %s", actor, side, want)
		}
	}
	if wars := fronts(45); len(wars) != 0 {
		t.Fatalf("a stranger is offered %d war(s) war.act would refuse", len(wars))
	}
}

// Peace (v1.24.0): sued for by a ranked member of a warring sect, once the war
// is old enough, at a price; the terms follow the siege, nobody is paid a
// victory, the side that gave way is bound, and the two sects warm.
func peaceReady(t *testing.T, path string, siege int64) *storage.Conn {
	t.Helper()
	batch4Exec(t, path, `UPDATE sect_membership SET rank_level=40,contribution_points=500 WHERE user_id IN (42,43)`)
	batch4Exec(t, path, `UPDATE territory_war_operations SET siege_progress=? WHERE war_id=1`, siege)
	return warConn(t, path)
}

func TestPeaceIsSuedOnTheTermsTheSiegeGives(t *testing.T) {
	cat := worlddata.Catalog{}
	rules := WarRules(cat)
	ready := rules.PeaceMinDays * warMinutesPerDay
	t.Run("the holder keeps the ground below the line", func(t *testing.T) {
		conn := peaceReady(t, warDB(t), rules.PeaceCedeSiege-1)
		out, err := warPeaceActionGo(conn, cat, 42, 1, ready)
		if err != nil {
			t.Fatal(err)
		}
		if out["resolution"] != "peace" || out["winner_key"] != "Holding Sect" {
			t.Fatalf("terms below the line: %v won by %v", out["resolution"], out["winner_key"])
		}
		if got := fmt.Sprint(scalarOn(t, conn, `SELECT controller_key FROM territory_state`)); got != "Holding Sect" {
			t.Fatalf("a peace handed the ford to %s", got)
		}
		if p := storage.ParseInt(scalarOn(t, conn, `SELECT contribution_points FROM sect_membership WHERE user_id=42`)); p != 500-rules.PeaceCostPoints {
			t.Fatalf("suing cost %d, want %d", 500-p, rules.PeaceCostPoints)
		}
		if s := storage.ParseInt(scalarOn(t, conn, `SELECT relation_score FROM sect_relations WHERE sect_a='Attacking Sect' AND sect_b='Holding Sect'`)); s != -rules.DeclareRelationDrop+rules.PeaceRelationGain {
			t.Fatalf("a peace left the two at standing %d", s)
		}
		if _, err := DeclareWarTx(conn, cat, "Attacking Sect", "Holding Sect", "the_ford", ready+warMinutesPerDay, 0); !errors.Is(err, errWarTruce) {
			t.Fatalf("the attacker that made peace declared again the next day: %v", err)
		}
	})
	t.Run("the ground is ceded at the line, and the holder is bound", func(t *testing.T) {
		conn := peaceReady(t, warDB(t), rules.PeaceCedeSiege)
		out, err := warPeaceActionGo(conn, cat, 43, 1, ready)
		if err != nil {
			t.Fatal(err)
		}
		if out["resolution"] != "ceded" || out["winner_key"] != "Attacking Sect" {
			t.Fatalf("terms at the line: %v won by %v", out["resolution"], out["winner_key"])
		}
		if got := fmt.Sprint(scalarOn(t, conn, `SELECT controller_key FROM territory_state`)); got != "Attacking Sect" {
			t.Fatalf("a cession left the ford with %s", got)
		}
		if WarOccupiedFromTx(conn, "the_ford", ready+1) != "" {
			t.Fatal("ground ceded at a table is under occupation")
		}
		if _, err := DeclareWarTx(conn, cat, "Holding Sect", "Attacking Sect", "the_ford", ready+warMinutesPerDay, 0); !errors.Is(err, errWarTruce) {
			t.Fatalf("the holder that ceded came straight back: %v", err)
		}
	})
	t.Run("nobody is paid a victory at a table", func(t *testing.T) {
		conn := peaceReady(t, warDB(t), 0)
		if _, err := conn.Execute(`INSERT INTO territory_war_actions(war_id,user_id,side,tactic,created_at) VALUES(1,43,'defender','repel',0)`, nil); err != nil {
			t.Fatal(err)
		}
		if _, err := warPeaceActionGo(conn, cat, 42, 1, ready); err != nil {
			t.Fatal(err)
		}
		if e := storage.ParseInt(scalarOn(t, conn, `SELECT contribution_earned FROM sect_membership WHERE user_id=43`)); e != 0 {
			t.Fatalf("a peace paid the holders' fighter %d", e)
		}
	})
}

func TestPeaceIsRefusedToWhoeverHasNoStandingToMakeIt(t *testing.T) {
	cat := worlddata.Catalog{}
	rules := WarRules(cat)
	ready := rules.PeaceMinDays * warMinutesPerDay
	for _, c := range []struct {
		name, setup, want string
		user, gm          int64
	}{
		{"an ally", ``, "no standing at the table", 44, ready},
		{"a junior member", `UPDATE sect_membership SET rank_level=10 WHERE user_id=42`, "peace is made by a", 42, ready},
		{"a war too young", ``, "not ready for terms", 42, ready - 1},
		{"an empty purse", `UPDATE sect_membership SET contribution_points=0 WHERE user_id=42`, "costs", 42, ready},
	} {
		t.Run(c.name, func(t *testing.T) {
			path := warDB(t)
			conn := peaceReady(t, path, 0)
			if c.setup != "" {
				if _, err := conn.Execute(c.setup, nil); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := warPeaceActionGo(conn, cat, c.user, 1, c.gm); err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("want a refusal naming %q, got %v", c.want, err)
			}
			if s := fmt.Sprint(scalarOn(t, conn, `SELECT status FROM territory_wars WHERE war_id=1`)); s != "active" {
				t.Fatalf("a refused peace ended the war: %s", s)
			}
		})
	}
}

func TestTheWorldsOwnSectsSueWhenTheirMoraleBreaks(t *testing.T) {
	cat := worlddata.Catalog{}
	rules := WarRules(cat)
	ready := rules.PeaceMinDays * warMinutesPerDay
	if _, _, ok := NPCSuesForPeace(cat, "A", "D", 0, ready-1, 10, rules.NPCPeaceMorale, 100); ok {
		t.Fatal("a war too young was ended at a table")
	}
	if _, _, ok := NPCSuesForPeace(cat, "A", "D", 0, ready, 10, rules.NPCPeaceMorale+1, rules.NPCPeaceMorale+1); ok {
		t.Fatal("two sides in good heart made peace")
	}
	if w, how, ok := NPCSuesForPeace(cat, "A", "D", 0, ready, rules.PeaceCedeSiege, 100, rules.NPCPeaceMorale); !ok || how != "ceded" || w != "A" {
		t.Fatalf("a broken defender behind a breached wall: %v %v %v", w, how, ok)
	}
}

// --- from world_feedback_gates_test.go ---

// callsIn answers, for each named function in a file, the names of the
// functions it calls in source order - a plain identifier or a selector's
// last part.
func callsIn(t *testing.T, file string, funcs ...string) map[string][]string {
	t.Helper()
	parsed, err := parser.ParseFile(token.NewFileSet(), file, nil, 0)
	if err != nil {
		t.Fatalf("cannot parse %s: %v", file, err)
	}
	want := map[string]bool{}
	for _, f := range funcs {
		want[f] = true
	}
	out := map[string][]string{}
	for _, decl := range parsed.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || !want[fn.Name.Name] {
			continue
		}
		out[fn.Name.Name] = []string{}
		ast.Inspect(fn, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			switch f := call.Fun.(type) {
			case *ast.Ident:
				out[fn.Name.Name] = append(out[fn.Name.Name], f.Name)
			case *ast.SelectorExpr:
				out[fn.Name.Name] = append(out[fn.Name.Name], f.Sel.Name)
			}
			return true
		})
	}
	for _, f := range funcs {
		if _, ok := out[f]; !ok {
			t.Fatalf("%s no longer declares %s; the gate is broken, not the tree", file, f)
		}
	}
	return out
}

func indexOf(calls []string, name string) int {
	for i, c := range calls {
		if c == name {
			return i
		}
	}
	return -1
}

// v1.29.0: a manor lends its sect nothing while a rival holds its ground, at
// every door a manor bonus is read through. A helper's own test passes against
// a tree nothing calls it from (TestEveryGoodDeedIsPaidWhereItHappens).
func TestEveryManorBonusAsksWhoHoldsTheGround(t *testing.T) {
	for file, fn := range map[string]string{
		"cultivation_actions.go":   "manorCultivationMultiplier",
		"crafting_actions.go":      "canonicalCraftManorBonus",
		"seclusion_environment.go": "seclusionEnvironmentGo",
	} {
		if indexOf(callsIn(t, file, fn)[fn], "ManorGroundTakenTx") < 0 {
			t.Errorf("%s (%s) reads a manor's bonus without asking whether a rival holds its ground", fn, file)
		}
	}
}

// v1.29.0: a player's kill marks the place and the sect through the one
// statement the world's own killings use, and reads the dead's kin before the
// widowing takes the spouse's name off the row it reads.
func TestAKillReadsTheKinBeforeTheWidowing(t *testing.T) {
	calls := callsIn(t, "combat_aftermath.go", "applyCombatAftermathTx")["applyCombatAftermathTx"]
	kin, release, remember := indexOf(calls, "kinOfTx"), indexOf(calls, "ReleaseNPCBondsTx"), indexOf(calls, "RememberTheKillerTx")
	if kin < 0 || remember < 0 {
		t.Fatal("a player's kill no longer tells the dead's kin who killed them")
	}
	if release < 0 || kin > release {
		t.Fatal("the kin are read after ReleaseNPCBondsTx, which has already widowed the spouse - so a widow never remembers")
	}
	if indexOf(calls, "MarkKillingTx") < 0 {
		t.Fatal("a player's kill marks its region and sect without MarkKillingTx; the world's own killings and a player's would drift")
	}
}

// v1.29.0: a market counter is trade like any shelf, and moves its city.
func TestAMarketTradeMovesItsCity(t *testing.T) {
	if indexOf(callsIn(t, "economy_actions.go", "marketTradeAction")["marketTradeAction"], "nudgeCityProsperityTx") < 0 {
		t.Fatal("market.trade sells and buys without moving the city's prosperity, the one sale in the game that does not")
	}
}

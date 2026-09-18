package game

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func crossingCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load(batch4WorldPath(t))
	if err != nil {
		t.Fatal(err)
	}
	return catalog
}

func crossingDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS world_history_events(source_key TEXT PRIMARY KEY,event_type TEXT NOT NULL,title TEXT NOT NULL,summary TEXT NOT NULL,significance INTEGER NOT NULL DEFAULT 1,visibility TEXT NOT NULL DEFAULT 'public',location TEXT NOT NULL DEFAULT '',world_name TEXT NOT NULL DEFAULT '',faction TEXT NOT NULL DEFAULT '',actor_type TEXT NOT NULL DEFAULT '',actor_key TEXT NOT NULL DEFAULT '',actor_name TEXT NOT NULL DEFAULT '',target_type TEXT NOT NULL DEFAULT '',target_key TEXT NOT NULL DEFAULT '',target_name TEXT NOT NULL DEFAULT '',related_user_id INTEGER,related_npc_name TEXT NOT NULL DEFAULT '',tags TEXT NOT NULL DEFAULT '',game_minute INTEGER NOT NULL DEFAULT 0,metadata_json TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS quest_definitions(quest_key TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',source_type TEXT NOT NULL DEFAULT 'forge',source_key TEXT NOT NULL DEFAULT '',objectives_json TEXT NOT NULL DEFAULT '[]',rewards_json TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'draft',created_at REAL NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0,giver_npc TEXT NOT NULL DEFAULT '',realm_band TEXT NOT NULL DEFAULT '',tier INTEGER NOT NULL DEFAULT 1,deadline_game_minutes INTEGER NOT NULL DEFAULT 0,variants_json TEXT NOT NULL DEFAULT '[]',seed_json TEXT NOT NULL DEFAULT '{}')`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS character_quests(user_id INTEGER NOT NULL,quest_key TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',progress_json TEXT NOT NULL DEFAULT '{}',accepted_game_minute INTEGER NOT NULL DEFAULT 0,completed_game_minute INTEGER,created_at REAL NOT NULL,updated_at REAL NOT NULL,commission INTEGER NOT NULL DEFAULT 0,deadline_game_minute INTEGER,variant_index INTEGER NOT NULL DEFAULT 0,resolved_game_minute INTEGER,terms_json TEXT NOT NULL DEFAULT '',PRIMARY KEY(user_id,quest_key))`)
	syncPurse(t, path)
	return path
}

// A cleared tribulation hands over the quest the content authored for that
// crossing (v1.0.0-rc.44). No dice are lent: character 42 carries every
// attribute at 100 against tribulation wave TNs in the high teens, so the
// scenario makes the outcome certain and there is nothing random to assert.
func TestAClearedTribulationHandsOverTheCrossingQuest(t *testing.T) {
	path := crossingDB(t)
	catalog := crossingCatalog(t)
	key := catalog.WorldCrossing.Quests["Mortal World"].QuestKey
	if key == "" {
		t.Fatal("content authors no ascension quest out of the Mortal World")
	}
	batch4Exec(t, path, `UPDATE characters SET realm_index=7,phase=9,cultivation=10000000 WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,created_at,updated_at) VALUES(?,'The Seam Above You',0,0)`, key)

	var out map[string]any
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		mutation, err := tribulationAttemptAction(conn, catalog, 42, json.RawMessage(`{"game_minute":500,"path":"qi"}`))
		out, _ = mutation.Result.(map[string]any)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if out["success"] != true {
		t.Fatalf("a hundred in every attribute did not survive three waves: %+v", out)
	}
	if out["quest_granted"] != key {
		t.Fatalf("the crossing quest was not handed over: %+v", out)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM character_quests WHERE user_id=42 AND quest_key=?`, key)); n != 1 {
		t.Fatalf("character_quests rows=%d", n)
	}
	// The row is the memory: a second cleared gate out of the same world
	// cannot hand it over twice, and the definition is a quest rather than a
	// commission, so it occupies no commission slot.
	if commission := i64(actionScalar(t, path, `SELECT commission FROM character_quests WHERE user_id=42 AND quest_key=?`, key)); commission != 0 {
		t.Fatalf("handed over as a commission: %d", commission)
	}
}

// A quest that is a commission is never handed over behind the player's back -
// grantOrdinaryQuestTx refuses a giver by design, and this is the path that
// would break it if somebody authored one into the crossing block.
func TestACrossingQuestWithAGiverIsRefused(t *testing.T) {
	path := crossingDB(t)
	catalog := crossingCatalog(t)
	key := catalog.WorldCrossing.Quests["Mortal World"].QuestKey
	batch4Exec(t, path, `UPDATE characters SET realm_index=7,phase=9,cultivation=10000000 WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO quest_definitions(quest_key,title,giver_npc,created_at,updated_at) VALUES(?,'The Seam Above You','Elder Mu',0,0)`, key)
	var out map[string]any
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		mutation, err := tribulationAttemptAction(conn, catalog, 42, json.RawMessage(`{"game_minute":500,"path":"qi"}`))
		out, _ = mutation.Result.(map[string]any)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if out["success"] != true || out["quest_granted"] != "" {
		t.Fatalf("a giver must cost the quest and nothing else: %+v", out)
	}
}

// crossingApply runs one mutation on its own connection and commits it, so
// every read-back below happens outside the transaction that wrote it. That is
// the shape `npc_found_test.go` learned to use the hard way: a handler that
// never commits reads back perfectly inside its own implicit transaction.
func crossingApply(t *testing.T, path string, fn func(conn *storage.Conn) error) error {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	actionErr := fn(conn)
	if conn.InTransaction() {
		if actionErr != nil {
			_ = conn.Rollback()
		} else if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	return actionErr
}

func walletOf(t *testing.T, path, currency string, userID int64) int64 {
	t.Helper()
	return i64(actionScalar(t, path, `SELECT COALESCE((SELECT balance FROM currency_wallets WHERE user_id=? AND currency_id=?),0)`, userID, currency))
}

// The ladder is the content's, and the crossing is one rung of it.
func TestEveryWorldDeclaresTheSameRungAndTheCrossingReadsIt(t *testing.T) {
	catalog := crossingCatalog(t)
	for _, world := range []string{"Mortal World", "Spiritual World", "Immortal World", "Celestial World"} {
		if rung := worldLadderRung(catalog, world); rung != 100 {
			t.Fatalf("%s: rung=%d, want the tier-2 base_ratio of 100", world, rung)
		}
	}
	// A world the file does not carry keeps the hundred rather than 1, which
	// would make a crossing free, or 0, which would divide by zero.
	if rung := worldLadderRung(catalog, "Nowhere"); rung != defaultLadderRung {
		t.Fatalf("an unknown world: rung=%d want %d", rung, defaultLadderRung)
	}
	if factor := ladderFactor(100, 2); factor != 10000 {
		t.Fatalf("two worlds up: %d", factor)
	}
	if factor := ladderFactor(100, 0); factor != 1 {
		t.Fatalf("no crossing at all: %d", factor)
	}
}

func TestCrossingUpDividesAtTheRungAndLeavesTheRemainderBehind(t *testing.T) {
	path := crossingDB(t)
	catalog := crossingCatalog(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=8 WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_stone',12345)
		ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=excluded.balance`)

	var report map[string]any
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		out, err := moveCharacterTx(conn, catalog, 42, "Spirit Jade Capital", 1)
		report = out
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if report == nil {
		t.Fatal("a move between worlds must report the exchange")
	}
	if report["from_currency"] != "low_spirit_stone" || report["to_currency"] != "low_spirit_crystal" {
		t.Fatalf("the two moneys: %+v", report)
	}
	if got := i64(report["converted"]); got != 123 {
		t.Fatalf("12345 stones at a hundred to one is 123 crystals, got %d", got)
	}
	if got := i64(report["remainder"]); got != 45 {
		t.Fatalf("the 45 that would not divide stays where it was, got %d", got)
	}
	if got := walletOf(t, path, "low_spirit_crystal", 42); got != 123 {
		t.Fatalf("crystals in the purse: %d", got)
	}
	if got := walletOf(t, path, "low_spirit_stone", 42); got != 45 {
		t.Fatalf("stones left behind: %d", got)
	}
	// The sheet is the purse's mirror, and after a crossing it must read in
	// the money of the world the character is now standing in - not in the
	// fortune they no longer hold.
	if got := i64(actionScalar(t, path, `SELECT spirit_stones FROM characters WHERE user_id=42`)); got != 123 {
		t.Fatalf("the sheet still reads in the old world's money: %d", got)
	}
}

func TestCrossingDownMultipliesAndTakesTheWholeBalance(t *testing.T) {
	path := crossingDB(t)
	catalog := crossingCatalog(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=8,location='Spirit Jade Capital' WHERE user_id=42`)
	batch4Exec(t, path, `DELETE FROM currency_wallets WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_crystal',7)`)

	var report map[string]any
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		out, err := moveCharacterTx(conn, catalog, 42, "Azure Crown Imperial City", 1)
		report = out
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if got := i64(report["converted"]); got != 700 {
		t.Fatalf("seven crystals come home as seven hundred stones, got %d", got)
	}
	if got := i64(report["remainder"]); got != 0 {
		t.Fatalf("nothing can be left over on the way down, got %d", got)
	}
	if got := walletOf(t, path, "low_spirit_crystal", 42); got != 0 {
		t.Fatalf("crystals kept: %d", got)
	}
	if got := walletOf(t, path, "low_spirit_stone", 42); got != 700 {
		t.Fatalf("stones arrived with: %d", got)
	}
}

// A move inside one world is not a crossing, and a move into a key the
// catalogue does not carry - a household interior, a personal world - is not
// one either. Converting at either would empty a purse on the way through a
// front door.
func TestAMoveInsideAWorldConvertsNothing(t *testing.T) {
	path := crossingDB(t)
	catalog := crossingCatalog(t)
	for _, destination := range []string{"Azure Crown Imperial City", "birth_family:1"} {
		batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
		before := walletOf(t, path, "low_spirit_stone", 42)
		var report map[string]any
		if err := crossingApply(t, path, func(conn *storage.Conn) error {
			out, err := moveCharacterTx(conn, catalog, 42, destination, 1)
			report = out
			return err
		}); err != nil {
			t.Fatal(err)
		}
		if report != nil {
			t.Fatalf("%s reported an exchange: %+v", destination, report)
		}
		if got := walletOf(t, path, "low_spirit_stone", 42); got != before {
			t.Fatalf("%s moved money: %d -> %d", destination, before, got)
		}
	}
}

func raiseGate(t *testing.T, path string, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	t.Helper()
	var out map[string]any
	err := crossingApply(t, path, func(conn *storage.Conn) error {
		mutation, err := ascensionGateAction(conn, catalog, userID, json.RawMessage(`{"game_minute":900}`))
		out, _ = mutation.Result.(map[string]any)
		return err
	})
	return out, err
}

func TestASeamIsNotAnchoredUntilItsTribulationIsSurvived(t *testing.T) {
	path := crossingDB(t)
	catalog := crossingCatalog(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=8 WHERE user_id=42`)

	if _, err := raiseGate(t, path, catalog, 42); err == nil || !strings.Contains(err.Error(), "Mortal Ascension Tribulation") {
		t.Fatalf("an unearned seam: err=%v", err)
	}
	// Cleared, but standing somewhere the world does not know: there is no
	// ground to anchor it to.
	batch4Exec(t, path, `INSERT INTO tribulation_state(user_id,gate_realm_index,cleared,updated_at) VALUES(42,7,1,0)`)
	batch4Exec(t, path, `UPDATE characters SET location='birth_family:1' WHERE user_id=42`)
	if _, err := raiseGate(t, path, catalog, 42); err == nil || !strings.Contains(err.Error(), "stand in a real place") {
		t.Fatalf("a seam anchored to a household: err=%v", err)
	}
	// And cleared, standing somewhere real, but unable to pay for it.
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	batch4Exec(t, path, `UPDATE currency_wallets SET balance=5 WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	if _, err := raiseGate(t, path, catalog, 42); err == nil || !strings.Contains(err.Error(), "which you do not have") {
		t.Fatalf("an unpayable seam: err=%v", err)
	}
	if n := i64(actionScalar(t, path, `SELECT COUNT(*) FROM world_crossings`)); n != 0 {
		t.Fatalf("a refused gate left %d rows behind", n)
	}
}

func TestTheGateStandsWhereTheLightningFellAndBorrowsTheAuthoredRoad(t *testing.T) {
	path := crossingDB(t)
	catalog := crossingCatalog(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=8 WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO tribulation_state(user_id,gate_realm_index,cleared,updated_at) VALUES(42,7,1,0)`)
	batch4Exec(t, path, `UPDATE currency_wallets SET balance=9000 WHERE user_id=42 AND currency_id='low_spirit_stone'`)

	out, err := raiseGate(t, path, catalog, 42)
	if err != nil {
		t.Fatal(err)
	}
	// The authored Mortal crossing is `imperial_spirit`: 200 low spirit
	// stones, min realm 8, landing at Spirit Jade Capital. The raised gate
	// takes all three, so nobody can tear open a cheaper road than the world
	// already has - and it costs ten times the fare to anchor.
	if out["destination"] != "Spirit Jade Capital" || i64(out["fare"]) != 200 || i64(out["min_realm_index"]) != 8 {
		t.Fatalf("the borrowed road: %+v", out)
	}
	if i64(out["cost"]) != 2000 || i64(out["balance"]) != 7000 {
		t.Fatalf("anchoring cost: %+v", out)
	}
	if out["location"] != "Greenriver Town" || out["to_world"] != "Spiritual World" {
		t.Fatalf("the gate stands where the storm did: %+v", out)
	}
	if out["name"] != "Lin Test's Ascension Gate" {
		t.Fatalf("the gate is named after whoever tore it: %+v", out)
	}
	// It is public: written where the world can see it, at a significance the
	// Quest Forge can reach.
	if significance := i64(actionScalar(t, path, `SELECT significance FROM world_history_events WHERE source_key='crossing_raised:Greenriver Town'`)); significance != 88 {
		t.Fatalf("history significance=%d", significance)
	}
	if visibility := actionScalar(t, path, `SELECT visibility FROM world_history_events WHERE source_key='crossing_raised:Greenriver Town'`); visibility != "public" {
		t.Fatalf("a torn sky is not a secret: %v", visibility)
	}
}

func TestOneSeamPerCultivatorPerWorld(t *testing.T) {
	path := crossingDB(t)
	catalog := crossingCatalog(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=8 WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO tribulation_state(user_id,gate_realm_index,cleared,updated_at) VALUES(42,7,1,0)`)
	batch4Exec(t, path, `UPDATE currency_wallets SET balance=90000 WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	if _, err := raiseGate(t, path, catalog, 42); err != nil {
		t.Fatal(err)
	}
	// Somewhere else in the same world, by the same cultivator.
	batch4Exec(t, path, `UPDATE characters SET location='Azure Crown Imperial City' WHERE user_id=42`)
	if _, err := raiseGate(t, path, catalog, 42); err == nil || !strings.Contains(err.Error(), "already anchored your crossing") {
		t.Fatalf("a second seam: err=%v", err)
	}
	// And somebody else's gate cannot stand on top of this one.
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town',realm_index=8 WHERE user_id=43`)
	batch4Exec(t, path, `INSERT INTO tribulation_state(user_id,gate_realm_index,cleared,updated_at) VALUES(43,7,1,0)`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(43,'low_spirit_stone',90000)
		ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=excluded.balance`)
	if _, err := raiseGate(t, path, catalog, 43); err == nil || !strings.Contains(err.Error(), "already stands here") {
		t.Fatalf("two gates on one spot: err=%v", err)
	}
}

// The whole point of a raised gate: `array.use` carries somebody through it
// exactly as it carries them through one of the authored eight, and the purse
// converts on the way.
func TestARaisedGateIsAnOrdinaryArrayToWhoeverUsesIt(t *testing.T) {
	path := crossingDB(t)
	catalog := crossingCatalog(t)
	batch4Exec(t, path, `UPDATE characters SET realm_index=8 WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO tribulation_state(user_id,gate_realm_index,cleared,updated_at) VALUES(42,7,1,0)`)
	batch4Exec(t, path, `UPDATE currency_wallets SET balance=60000 WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	gate, err := raiseGate(t, path, catalog, 42)
	if err != nil {
		t.Fatal(err)
	}

	// Somebody else's cultivator walks through it. 60000 - 2000 anchored
	// - 200 fare = 57800 stones, which is 578 crystals with nothing over.
	var out map[string]any
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		mutation, err := teleportArrayActionGo(conn, catalog, 42,
			json.RawMessage(`{"array_id":"`+fmtArrayID(gate)+`","game_minute":1000}`))
		out, _ = mutation.Result.(map[string]any)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if out["to"] != "Spirit Jade Capital" || out["raised"] != true {
		t.Fatalf("the transit: %+v", out)
	}
	exchange, _ := out["exchange"].(map[string]any)
	if exchange == nil || i64(exchange["converted"]) != 578 {
		t.Fatalf("the exchange on the way through: %+v", out)
	}
	if got := actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`); got != "Spirit Jade Capital" {
		t.Fatalf("still standing at %v", got)
	}
	if uses := i64(actionScalar(t, path, `SELECT player_uses FROM world_crossings WHERE location_key='Greenriver Town'`)); uses != 1 {
		t.Fatalf("the gate counted %d uses", uses)
	}
	// A key that names no gate is refused rather than resolved to nothing.
	if err := crossingApply(t, path, func(conn *storage.Conn) error {
		_, err := teleportArrayActionGo(conn, catalog, 42, json.RawMessage(`{"array_id":"crossing:Nowhere At All"}`))
		return err
	}); err == nil || !strings.Contains(err.Error(), "unknown teleportation array") {
		t.Fatalf("an invented gate: err=%v", err)
	}
}

func fmtArrayID(gate map[string]any) string {
	if gate == nil {
		return ""
	}
	return strings.TrimSpace(gate["array_id"].(string))
}

// The gate for the gate: a world is left by one door.
//
// This is `TestThePurseHasOneDoor`'s shape (v1.0.0-rc.43) applied to the other
// half of a crossing. The purse converts inside `moveCharacterTx`, so any
// statement that writes `characters.location` without going through it is a
// door out of a world that keeps the money of the world behind - and which
// half of a player's fortune survives would depend on how they travelled.
func TestAWorldIsLeftByOneDoor(t *testing.T) {
	// The one write outside `moveCharacterTx`, with the reason it may.
	allowed := map[string]string{
		"world_crossing.go": "moveCharacterTx itself - the door every other path goes through",
		"admin_undo.go":     "restoring a location snapshot is not a journey: the money was never converted, so putting somebody back must not convert it either",
	}
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	offenders := []string{}
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		body, err := os.ReadFile(filepath.Clean(name))
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(body), "characters SET location=") {
			continue
		}
		if _, ok := allowed[name]; ok {
			continue
		}
		offenders = append(offenders, name)
	}
	if len(offenders) > 0 {
		t.Fatalf("these move a character without converting what they carry: %v — "+
			"call moveCharacterTx, or name the file in `allowed` with the reason it may not", offenders)
	}
	for name := range allowed {
		body, err := os.ReadFile(filepath.Clean(name))
		if err != nil {
			t.Fatalf("%s is named in `allowed` and does not exist: %v", name, err)
		}
		if !strings.Contains(string(body), "characters SET location=") {
			t.Fatalf("%s is named in `allowed` and no longer writes a location; remove the entry", name)
		}
	}
}

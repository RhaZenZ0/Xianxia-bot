package game

// The world_state rows a player owns, and the three doors that clear them
// (v1.34.0).
//
// `world_state` holds seven keys that are one person's - `<prefix>:<user_id>` -
// and no schema walk sees them, so a rebirth, a reset and an erasure each
// passed them over: the next body inherited the last one's banked insight and
// its lifespan clock's `PausedGameMinutes`, and an erased account's id stayed
// in the key of a row nobody owned. The fixtures write every key the engine's
// own key functions produce, for the subject and for a neighbour and the
// world's own `world_clock`, and hold each door to taking the first and
// leaving the other two.

import (
	"fmt"
	"sort"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// playerKeyWriters is every function in the engine that spells a player's
// world_state key. It is written out here, not read off the list under test:
// a test that took its keys from `playerWorldStatePrefixes` would pass against
// a list that had forgotten one.
var playerKeyWriters = map[string]func(int64) string{
	"cultivationInsightKey":  cultivationInsightKey,
	"cultivationStanceKey":   cultivationStanceKey,
	"cultivationManualKey":   cultivationManualKey,
	"cultivationLastFailKey": cultivationLastFailKey,
	"cultivationRerollKey":   cultivationRerollKey,
	"playerLifespanStateKey": playerLifespanStateKey,
	"roadTransitStateKey":    roadTransitStateKey,
}

func TestEveryPlayerWorldStateKeyIsInTheList(t *testing.T) {
	var spelled []string
	for name, write := range playerKeyWriters {
		key := write(424242)
		prefix, ok := strings.CutSuffix(key, ":424242")
		if !ok {
			t.Fatalf("%s writes %q, which is not <prefix>:<user_id>", name, key)
		}
		spelled = append(spelled, prefix)
	}
	listed := append([]string{}, playerWorldStatePrefixes...)
	sort.Strings(spelled)
	sort.Strings(listed)
	if strings.Join(spelled, ",") != strings.Join(listed, ",") {
		t.Fatalf("the player-keyed world_state prefixes the engine writes are %v but the list the three doors clear is %v: "+
			"a key on one side and not the other is a row a rebirth, a reset or an erasure leaves behind", spelled, listed)
	}
}

// writeShelf puts one row per player-keyed writer under the user, a
// neighbour's rows beside them, and the world's own clock.
func writeShelf(t *testing.T, path string, subject, neighbour int64) {
	t.Helper()
	var statement strings.Builder
	for _, write := range playerKeyWriters {
		for _, uid := range []int64{subject, neighbour} {
			fmt.Fprintf(&statement, "INSERT INTO world_state(key,value_json,updated_at) VALUES('%s','{}',1);\n", write(uid))
		}
	}
	statement.WriteString("INSERT OR IGNORE INTO world_state(key,value_json,updated_at) VALUES('world_clock','{}',1);\n")
	resetExec(t, path, statement.String())
}

func shelfOf(t *testing.T, path string, uid int64) int64 {
	t.Helper()
	return resetScalarI(t, path, `SELECT COUNT(*) FROM world_state WHERE key LIKE '%:'||?`, uid)
}

func holdsTheShelfRight(t *testing.T, door, path string, subject, neighbour int64) {
	t.Helper()
	if got := shelfOf(t, path, subject); got != 0 {
		t.Errorf("%s left %d player-keyed world_state row(s) under the subject: the next body, or an account that was erased, still carries them", door, got)
	}
	if got := shelfOf(t, path, neighbour); got != int64(len(playerKeyWriters)) {
		t.Errorf("%s left %d of the neighbour's %d rows: it is scoped to one person", door, got, len(playerKeyWriters))
	}
	if got := resetScalarI(t, path, `SELECT COUNT(*) FROM world_state WHERE key='world_clock'`); got != 1 {
		t.Errorf("%s took the world's own clock", door)
	}
}

func TestARebirthTakesThePlayerKeyedShelfWithIt(t *testing.T) {
	path := setupIncarnationDB(t, false)
	writeShelf(t, path, 42, 43)
	if err := wipeFor(t, path, 42); err != nil {
		t.Fatal(err)
	}
	holdsTheShelfRight(t, "a rebirth", path, 42, 43)
}

func TestAResetTakesThePlayerKeyedShelfWithIt(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Lin Shelf", 200)
	writeShelf(t, path, 77, 88)
	if _, err := resetCultivator(t, path, world, 77, "shelf-reset"); err != nil {
		t.Fatal(err)
	}
	holdsTheShelfRight(t, "a reset", path, 77, 88)
}

func TestAnErasureTakesThePlayerKeyedShelfWithIt(t *testing.T) {
	path, world := escrowWorld(t)
	writeShelf(t, path, 77, 88)
	if err := eraseWith(t, path, world, 1, 77); err != nil {
		t.Fatal(err)
	}
	holdsTheShelfRight(t, "an erasure", path, 77, 88)
}

// A bank carries the realm it was bought at and opens that gate alone.
func TestABankedInsightOpensOnlyTheGateItWasBoughtAt(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	open := func(realm int64) (bool, bool) {
		var gate, via bool
		if err := crossingApply(t, path, func(conn *storage.Conn) error {
			var err error
			gate, via, err = realmGateOpen(conn, 42, realm)
			return err
		}); err != nil {
			t.Fatal(err)
		}
		return gate, via
	}
	if gate, _ := open(1); gate {
		t.Fatal("a gate is open for a cultivator who banked nothing")
	}
	batch4Exec(t, path, `INSERT INTO world_state(key,value_json,updated_at) VALUES('cultivation_insight:42','{"banked":true,"realm_index":1,"cost":10}',1)`)
	if gate, via := open(1); !gate || !via {
		t.Fatal("an insight banked at realm 1 does not open the gate out of realm 1")
	}
	if gate, _ := open(2); gate {
		t.Fatal("an insight banked at realm 1 opened the gate out of realm 2: a bank from another realm is not a licence for this one")
	}
	batch4Exec(t, path, `UPDATE world_state SET value_json='{"banked":true}' WHERE key='cultivation_insight:42'`)
	if gate, _ := open(0); gate {
		t.Fatal("a bank that does not say its realm opened the gate out of realm 0: absent is not realm zero")
	}
	if gate, _ := open(1); gate {
		t.Fatal("a bank that does not say its realm opened a gate")
	}
	batch4Exec(t, path, `INSERT INTO realm_perfection(user_id,realm_index,completed,updated_at) VALUES(42,2,1,1)`)
	if gate, via := open(2); !gate || via {
		t.Fatal("a completed Realm Perfection stopped opening its gate, or spent a bank it did not need")
	}
}

package game

// What erasure has to get right, and what it must refuse to do.
//
// The completeness half of this - "is every user column in the real 182-table
// schema classified" - is not here, because the fixtures in this package build
// a schema by hand and would only ever prove the fixture is classified. That
// check bootstraps the real database and lives in
// tests/python/contracts/test_privacy_erasure.py.
//
// What is here is the behaviour: the three dispositions, the idempotency an
// operator answering a request depends on, and the two refusals.

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"xianxia/core/internal/storage"
)

func setupErasureDB(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "erasure.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// One table per shape the real schema presents: a plain subject row, a row
	// keyed by another name, a shared row with a nullable link, a shared row
	// whose link is NOT NULL, the audit log, and a table with no user column
	// at all that must be left entirely alone.
	schema := `
CREATE TABLE characters(user_id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE scene_history(id INTEGER PRIMARY KEY, channel_id INTEGER, user_id INTEGER, content TEXT);
CREATE TABLE rag_memories(memory_id INTEGER PRIMARY KEY, user_id INTEGER, summary TEXT);
CREATE TABLE trade_offers(id INTEGER PRIMARY KEY, from_user_id INTEGER, to_user_id INTEGER);
CREATE TABLE world_history_events(history_id INTEGER PRIMARY KEY, title TEXT, related_user_id INTEGER);
CREATE TABLE player_families(family_id INTEGER PRIMARY KEY, name TEXT, founder_user_id INTEGER NOT NULL);
CREATE TABLE quest_definitions(quest_id INTEGER PRIMARY KEY, title TEXT, owner_user_id INTEGER);
CREATE TABLE admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT, admin_user_id INTEGER NOT NULL,
    action TEXT, target TEXT, before_json TEXT, after_json TEXT, reason TEXT, created_at REAL);
CREATE TABLE content_locations(location_key TEXT PRIMARY KEY, name TEXT);

INSERT INTO characters(user_id,name) VALUES(42,'Li Wei'),(7,'Another Cultivator');
INSERT INTO scene_history(channel_id,user_id,content) VALUES(1,42,'something the player typed'),(1,7,'somebody else');
INSERT INTO rag_memories(user_id,summary) VALUES(42,'a memory of the player'),(7,'not theirs');
INSERT INTO trade_offers(from_user_id,to_user_id) VALUES(42,7),(7,42),(7,7);
INSERT INTO world_history_events(title,related_user_id) VALUES('A duel',42),('Unrelated',7);
INSERT INTO player_families(name,founder_user_id) VALUES('The Li Clan',42);
INSERT INTO quest_definitions(title,owner_user_id) VALUES('A drafted quest',42);
INSERT INTO admin_audit_log(admin_user_id,action,target,created_at) VALUES(42,'admin.player.karma','user:7',1.0);
INSERT INTO content_locations(location_key,name) VALUES('greenriver','Greenriver Town');
`
	if err := conn.ExecScript(schema); err != nil {
		t.Fatal(err)
	}
	return path
}

func erasePlayer(t *testing.T, path string, admin, subject int64) (map[string]any, error) {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"user_id": subject, "reason": "the player asked"})
	if err != nil {
		t.Fatal(err)
	}
	out, err := Apply(path, ActionRequest{
		Operation: "admin.player.erase",
		ActorID:   admin,
		Payload:   raw,
	})
	if err != nil {
		return nil, err
	}
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("admin.player.erase result type %T", out.Result)
	}
	return result, nil
}

func erasureScalar(t *testing.T, path, query string) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(query, nil)
	if err != nil {
		t.Fatalf("%s: %v", query, err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return 0
	}
	return storage.ParseInt(res.Rows[0][0])
}

// The point of the whole action: the person's own rows are gone, under every
// column name they were filed under.
func TestErasureRemovesTheSubjectsOwnRowsWhateverTheColumnIsCalled(t *testing.T) {
	path := setupErasureDB(t)
	if _, err := erasePlayer(t, path, 1, 42); err != nil {
		t.Fatal(err)
	}
	for _, check := range []struct {
		what  string
		query string
	}{
		{"the character", `SELECT COUNT(*) FROM characters WHERE user_id=42`},
		{"their typed messages", `SELECT COUNT(*) FROM scene_history WHERE user_id=42`},
		{"their RAG memories", `SELECT COUNT(*) FROM rag_memories WHERE user_id=42`},
		{"trades they sent", `SELECT COUNT(*) FROM trade_offers WHERE from_user_id=42`},
		{"trades they were sent", `SELECT COUNT(*) FROM trade_offers WHERE to_user_id=42`},
	} {
		if got := erasureScalar(t, path, check.query); got != 0 {
			t.Errorf("%s: %d rows survived erasure", check.what, got)
		}
	}
}

// Nobody else's world may be taken away to satisfy one person's request.
func TestErasureLeavesEveryOtherCultivatorAlone(t *testing.T) {
	path := setupErasureDB(t)
	if _, err := erasePlayer(t, path, 1, 42); err != nil {
		t.Fatal(err)
	}
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM characters WHERE user_id=7`); got != 1 {
		t.Errorf("the other cultivator's character = %d rows, want 1", got)
	}
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM scene_history WHERE user_id=7`); got != 1 {
		t.Errorf("the other cultivator's messages = %d rows, want 1", got)
	}
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM trade_offers WHERE from_user_id=7 AND to_user_id=7`); got != 1 {
		t.Errorf("a trade between two other people = %d rows, want 1", got)
	}
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM content_locations`); got != 1 {
		t.Errorf("world content = %d rows, want 1 - erasure must not touch the catalogue", got)
	}
}

// Shared world state keeps its row and loses its link. The duel happened; the
// sect and the family outlive whoever founded them.
func TestErasureAnonymisesSharedWorldStateRatherThanDeletingIt(t *testing.T) {
	path := setupErasureDB(t)
	result, err := erasePlayer(t, path, 1, 42)
	if err != nil {
		t.Fatal(err)
	}
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM world_history_events`); got != 2 {
		t.Fatalf("world history = %d rows, want 2 - the events must survive", got)
	}
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE related_user_id=42`); got != 0 {
		t.Errorf("%d world history rows still name the erased player", got)
	}
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM world_history_events WHERE related_user_id IS NULL`); got != 1 {
		t.Errorf("the duel's link should be NULL on a nullable column")
	}
	// player_families.founder_user_id is NOT NULL, so it takes the sentinel
	// instead. Setting NULL there would abort the whole erasure mid-way.
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM player_families`); got != 1 {
		t.Fatalf("the family = %d rows, want 1 - it outlives its founder", got)
	}
	if got := erasureScalar(t, path, `SELECT founder_user_id FROM player_families`); got != erasedUserSentinel {
		t.Errorf("founder_user_id = %d, want the %d sentinel on a NOT NULL column", got, erasedUserSentinel)
	}
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM quest_definitions`); got != 1 {
		t.Errorf("a drafted quest = %d rows, want 1 - other players may be mid-way through it", got)
	}
	if anonymised, ok := result["rows_anonymised"].(int64); ok && anonymised != 3 {
		t.Errorf("rows_anonymised = %d, want 3", anonymised)
	}
}

// The record that the request was honoured cannot be the thing the request
// deletes - and it must not quote what was erased.
func TestErasureWritesAnAuditRowAndDoesNotEraseTheAuditTrail(t *testing.T) {
	path := setupErasureDB(t)
	if _, err := erasePlayer(t, path, 1, 42); err != nil {
		t.Fatal(err)
	}
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE admin_user_id=42`); got != 1 {
		t.Errorf("the pre-existing audit row written by user 42 as an admin = %d, want 1 kept", got)
	}
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.player.erase' AND target='user:42'`); got != 1 {
		t.Fatalf("the erasure wrote %d audit rows, want exactly 1", got)
	}
	// The character's name is on the operator's receipt but must never reach
	// the audit row: a record of an erasure that quotes the erased data is not
	// an erasure.
	if got := erasureScalar(t, path,
		`SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.player.erase'
		 AND (before_json LIKE '%Li Wei%' OR after_json LIKE '%Li Wei%' OR target LIKE '%Li Wei%')`); got != 0 {
		t.Errorf("the audit row quotes the erased character's name")
	}
}

// An operator answering a request has no way to know whether a first attempt
// got halfway, so running it twice has to be safe and has to succeed.
func TestErasureIsIdempotent(t *testing.T) {
	path := setupErasureDB(t)
	first, err := erasePlayer(t, path, 1, 42)
	if err != nil {
		t.Fatal(err)
	}
	second, err := erasePlayer(t, path, 1, 42)
	if err != nil {
		t.Fatalf("a second erasure failed: %v", err)
	}
	if first["had_character"] != true {
		t.Errorf("the first run should report a character was there")
	}
	if second["had_character"] != false {
		t.Errorf("the second run should report there was nothing left")
	}
	if got, ok := second["rows_deleted"].(int64); ok && got != 0 {
		t.Errorf("the second run deleted %d rows, want 0", got)
	}
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.player.erase'`); got != 2 {
		t.Errorf("audit rows = %d, want 2 - both requests are recorded", got)
	}
}

// Erasing a user who was never here is a success, not an error: the operator
// is answering a request, and "there was nothing" is a valid answer to give.
func TestErasingSomeoneWithNoDataSucceeds(t *testing.T) {
	path := setupErasureDB(t)
	result, err := erasePlayer(t, path, 1, 999)
	if err != nil {
		t.Fatal(err)
	}
	if result["had_character"] != false {
		t.Errorf("had_character = %v, want false", result["had_character"])
	}
}

func TestErasureRefusesTheNonsenseAndTheSelfInflicted(t *testing.T) {
	path := setupErasureDB(t)
	for _, bad := range []int64{0, -1} {
		if _, err := erasePlayer(t, path, 1, bad); err == nil {
			t.Errorf("erasing user_id %d was accepted", bad)
		}
	}
	// Erasing the GM would take the audit trail's author with it and leave
	// nobody able to run the console.
	if _, err := erasePlayer(t, path, 42, 42); err == nil {
		t.Errorf("an administrator erased themselves through the console")
	}
	if got := erasureScalar(t, path, `SELECT COUNT(*) FROM characters WHERE user_id=42`); got != 1 {
		t.Errorf("the refused self-erasure still removed the character")
	}
}

// Discovery is what keeps this correct as the schema grows, so it is worth
// pinning that it reads the schema rather than a list.
func TestErasureDiscoversItsTargetsFromTheSchema(t *testing.T) {
	path := setupErasureDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	targets, err := erasureTargets(conn)
	if err != nil {
		t.Fatal(err)
	}
	found := map[string]erasureDisposition{}
	for _, target := range targets {
		found[erasureKey(target.Table, target.Column)] = target.Disposition
	}
	for key, want := range map[string]erasureDisposition{
		"characters.user_id":                   erasureDelete,
		"trade_offers.from_user_id":            erasureDelete,
		"trade_offers.to_user_id":              erasureDelete,
		"world_history_events.related_user_id": erasureAnonymiseRow,
		"player_families.founder_user_id":      erasureAnonymiseRow,
	} {
		got, ok := found[key]
		if !ok {
			t.Errorf("%s was not discovered as an erasure target", key)
			continue
		}
		if got != want {
			t.Errorf("%s disposition = %v, want %v", key, got, want)
		}
	}
	if _, ok := found["admin_audit_log.admin_user_id"]; ok {
		t.Errorf("the audit log was offered up as an erasure target")
	}
	if _, ok := found["content_locations.location_key"]; ok {
		t.Errorf("a table with no user column became a target")
	}
	// A NOT NULL anonymise column has to be recognised as such, or the UPDATE
	// would try NULL and take the whole erasure down with it.
	for _, target := range targets {
		if erasureKey(target.Table, target.Column) == "player_families.founder_user_id" && !target.NotNull {
			t.Errorf("player_families.founder_user_id was not seen as NOT NULL")
		}
	}
	if len(targets) == 0 {
		t.Fatal("no targets discovered at all")
	}
}

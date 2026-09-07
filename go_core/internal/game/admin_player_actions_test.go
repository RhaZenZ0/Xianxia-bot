package game

// v0.23.0 regression tests for the admin writes that moved out of Python.
//
// Two properties matter here and neither was available before. The first is
// rule 6 in CLAUDE.md: every GM action writes an audit row. In Python the
// change and its audit entry were two separate calls, so a crash between them
// left the world altered with nothing in the log to say who did it - these
// tests assert the audit row exists *and* that a refused action leaves none.
// The second is that the validations survived the move: Python's set_master
// refused self-mastery, cross-sect lineage and cycles, and the engine must
// refuse the same things rather than quietly widening what a GM can do.

import (
	"encoding/json"
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

func setupAdminPlayerDB(t *testing.T) string {
	t.Helper()
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE sect_lineage(disciple_user_id INTEGER PRIMARY KEY,master_user_id INTEGER NOT NULL,accepted_at REAL NOT NULL,attention INTEGER NOT NULL DEFAULT 0);
CREATE TABLE storage_containers(user_id INTEGER PRIMARY KEY,container_id TEXT NOT NULL,name TEXT NOT NULL,grade TEXT NOT NULL,slot_capacity INTEGER NOT NULL,living_space INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL);
CREATE TABLE storage_inventory(user_id INTEGER NOT NULL,item_id TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,item_id));
DROP TABLE world_events;
CREATE TABLE world_events(event_key TEXT PRIMARY KEY,dedupe_key TEXT NOT NULL DEFAULT '',event_type TEXT NOT NULL,title TEXT NOT NULL,location TEXT NOT NULL,payload_json TEXT NOT NULL DEFAULT '{}',active INTEGER NOT NULL DEFAULT 1,starts_at REAL NOT NULL,ends_at REAL NOT NULL);
INSERT INTO characters(user_id,name,life_status,location,qi,qi_max,realm_index,phase,updated_at) VALUES(43,'Shifu Test','alive','Greenriver Town',30,30,4,1,0);
INSERT INTO characters(user_id,name,life_status,location,qi,qi_max,realm_index,phase,updated_at) VALUES(44,'Third Test','alive','Greenriver Town',30,30,3,1,0);
`); err != nil {
		t.Fatal(err)
	}
	return path
}

func gmApply(t *testing.T, path, op string, admin int64, payload map[string]any) (map[string]any, error) {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	out, err := Apply(path, ActionRequest{Operation: op, ActorID: admin, Payload: raw})
	if err != nil {
		return nil, err
	}
	result, ok := out.Result.(map[string]any)
	if !ok {
		t.Fatalf("%s result type %T", op, out.Result)
	}
	return result, nil
}

func adminScalar(t *testing.T, path, sql string, args ...any) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return 0
	}
	return storage.ParseInt(res.Rows[0][0])
}

func gmAuditRows(t *testing.T, path, action string) int64 {
	t.Helper()
	return adminScalar(t, path, `SELECT COUNT(*) FROM admin_audit_log WHERE action=?`, action)
}

// ---------------------------------------------------------------- set_master

func TestSettingAMasterRecordsTheLineageAndItsAuditTogether(t *testing.T) {
	path := setupAdminPlayerDB(t)
	result, err := gmApply(t, path, "admin.player.set_master", 7,
		map[string]any{"disciple_user_id": 44, "master_user_id": 43, "reason": "gm"})
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["master_user_id"]); got != 43 {
		t.Fatalf("master_user_id=%d, want 43", got)
	}
	if got := adminScalar(t, path,
		`SELECT master_user_id FROM sect_lineage WHERE disciple_user_id=44`); got != 43 {
		t.Fatalf("stored master=%d, want 43", got)
	}
	if got := gmAuditRows(t, path, "admin.player.set_master"); got != 1 {
		t.Fatalf("audit rows=%d, want 1", got)
	}
}

func TestClearingAMasterRemovesTheLineageAndAuditsIt(t *testing.T) {
	path := setupAdminPlayerDB(t)
	if _, err := gmApply(t, path, "admin.player.set_master", 7,
		map[string]any{"disciple_user_id": 44, "master_user_id": 43}); err != nil {
		t.Fatal(err)
	}
	if _, err := gmApply(t, path, "admin.player.set_master", 7,
		map[string]any{"disciple_user_id": 44, "clear": true}); err != nil {
		t.Fatal(err)
	}
	if got := adminScalar(t, path,
		`SELECT COUNT(*) FROM sect_lineage WHERE disciple_user_id=44`); got != 0 {
		t.Fatalf("lineage rows=%d, want none", got)
	}
	if got := gmAuditRows(t, path, "admin.player.set_master"); got != 2 {
		t.Fatalf("audit rows=%d, want one per change", got)
	}
}

func TestARefusedMasterAssignmentLeavesNoTraceAtAll(t *testing.T) {
	// The refusals Python made, each of which must still refuse - and none of
	// which may leave a half-written lineage or an audit row claiming a change
	// that did not happen.
	for name, payload := range map[string]map[string]any{
		"self-mastery":      {"disciple_user_id": 44, "master_user_id": 44},
		"unknown disciple":  {"disciple_user_id": 999, "master_user_id": 43},
		"unknown master":    {"disciple_user_id": 44, "master_user_id": 999},
		"missing master_id": {"disciple_user_id": 44},
	} {
		t.Run(name, func(t *testing.T) {
			path := setupAdminPlayerDB(t)
			if _, err := gmApply(t, path, "admin.player.set_master", 7, payload); err == nil {
				t.Fatal("the assignment was accepted")
			}
			if got := adminScalar(t, path, `SELECT COUNT(*) FROM sect_lineage`); got != 0 {
				t.Fatalf("lineage rows=%d after a refusal", got)
			}
			if got := gmAuditRows(t, path, "admin.player.set_master"); got != 0 {
				t.Fatalf("audit rows=%d after a refusal", got)
			}
		})
	}
}

func TestMasterAndDiscipleInDifferentSectsIsRefused(t *testing.T) {
	path := setupAdminPlayerDB(t)
	adminExec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(44,'Azure Cloud','Outer Disciple',1,0)`)
	adminExec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(43,'Iron Peak','Elder',5,0)`)

	if _, err := gmApply(t, path, "admin.player.set_master", 7,
		map[string]any{"disciple_user_id": 44, "master_user_id": 43}); err == nil {
		t.Fatal("a cross-sect master assignment was accepted")
	}
	if got := adminScalar(t, path, `SELECT COUNT(*) FROM sect_lineage`); got != 0 {
		t.Fatalf("lineage rows=%d after a refusal", got)
	}

	// One side sectless is allowed, the same as before the move.
	adminExec(t, path, `DELETE FROM sect_membership WHERE user_id=44`)
	if _, err := gmApply(t, path, "admin.player.set_master", 7,
		map[string]any{"disciple_user_id": 44, "master_user_id": 43}); err != nil {
		t.Fatalf("a sectless disciple was refused a master: %v", err)
	}
}

func TestALineageCycleIsRefused(t *testing.T) {
	// 43 already learns from 44. Making 44 learn from 43 closes the loop, and
	// every query that walks the chain would then run until it gave up.
	path := setupAdminPlayerDB(t)
	if _, err := gmApply(t, path, "admin.player.set_master", 7,
		map[string]any{"disciple_user_id": 43, "master_user_id": 44}); err != nil {
		t.Fatal(err)
	}
	if _, err := gmApply(t, path, "admin.player.set_master", 7,
		map[string]any{"disciple_user_id": 44, "master_user_id": 43}); err == nil {
		t.Fatal("a lineage cycle was accepted")
	}
	if got := adminScalar(t, path,
		`SELECT COUNT(*) FROM sect_lineage WHERE disciple_user_id=44`); got != 0 {
		t.Fatalf("the cycle was written anyway (rows=%d)", got)
	}
}

// ------------------------------------------------------------- set_sect_rank

func TestSettingASectRankRequiresAMembershipAndAuditsTheChange(t *testing.T) {
	path := setupAdminPlayerDB(t)
	if _, err := gmApply(t, path, "admin.player.set_sect_rank", 7,
		map[string]any{"user_id": 44, "rank_name": "Core Disciple", "rank_level": 3}); err == nil {
		t.Fatal("a rank was set on a cultivator with no sect")
	}
	if got := gmAuditRows(t, path, "admin.player.set_sect_rank"); got != 0 {
		t.Fatalf("audit rows=%d after a refusal", got)
	}

	adminExec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at) VALUES(44,'Azure Cloud','Outer Disciple',1,0)`)
	result, err := gmApply(t, path, "admin.player.set_sect_rank", 7,
		map[string]any{"user_id": 44, "rank_name": "Core Disciple", "rank_level": 3})
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(result["rank_name"]) != "Core Disciple" {
		t.Fatalf("rank_name=%v", result["rank_name"])
	}
	if got := adminScalar(t, path, `SELECT rank_level FROM sect_membership WHERE user_id=44`); got != 3 {
		t.Fatalf("stored rank_level=%d, want 3", got)
	}
	if got := gmAuditRows(t, path, "admin.player.set_sect_rank"); got != 1 {
		t.Fatalf("audit rows=%d, want 1", got)
	}
}

// ---------------------------------------------------------- master_attention

func TestMasterAttentionAdjustsAndFloorsAtZero(t *testing.T) {
	path := setupAdminPlayerDB(t)
	if _, err := gmApply(t, path, "admin.player.set_master", 7,
		map[string]any{"disciple_user_id": 44, "master_user_id": 43}); err != nil {
		t.Fatal(err)
	}
	result, err := gmApply(t, path, "admin.player.master_attention", 7,
		map[string]any{"disciple_user_id": 44, "delta": 30})
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["attention"]); got != 30 {
		t.Fatalf("attention=%d, want 30", got)
	}
	// Attention cannot go negative; a big enough cut lands on zero.
	result, err = gmApply(t, path, "admin.player.master_attention", 7,
		map[string]any{"disciple_user_id": 44, "delta": -100})
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["attention"]); got != 0 {
		t.Fatalf("attention=%d, want 0", got)
	}
}

func TestAdjustingAttentionForSomeoneWithNoMasterIsRefusedNotFaked(t *testing.T) {
	// Python updated nothing and reported 0, so a GM saw "✅ Master attention:
	// 0" for a disciple who had no master to pay them any.
	path := setupAdminPlayerDB(t)
	if _, err := gmApply(t, path, "admin.player.master_attention", 7,
		map[string]any{"disciple_user_id": 44, "delta": 10}); err == nil {
		t.Fatal("attention was adjusted for a cultivator with no master")
	}
	if got := gmAuditRows(t, path, "admin.player.master_attention"); got != 0 {
		t.Fatalf("audit rows=%d after a refusal", got)
	}
}

// ------------------------------------------------------------- grant_storage

func TestGrantingStorageNeverShrinksBelowWhatIsStored(t *testing.T) {
	// The replacement container must hold what the old one held. Python
	// computed the same floor; losing it would strand items in a container too
	// small to open.
	path := setupAdminPlayerDB(t)
	for i := 0; i < 12; i++ {
		adminExec(t, path,
			`INSERT INTO storage_inventory(user_id,item_id,quantity) VALUES(44,?,1)`,
			fmt.Sprintf("item_%d", i))
	}
	result, err := gmApply(t, path, "admin.player.grant_storage", 7,
		map[string]any{"user_id": 44, "name": "Jade Ring", "grade": "Earth", "slot_capacity": 4})
	if err != nil {
		t.Fatal(err)
	}
	if got := storage.ParseInt(result["slot_capacity"]); got != 12 {
		t.Fatalf("slot_capacity=%d, want the 12 slots already in use", got)
	}
	if got := storage.ParseInt(result["requested_slot_capacity"]); got != 4 {
		t.Fatalf("requested_slot_capacity=%d, want the 4 that was asked for", got)
	}
	if got := adminScalar(t, path, `SELECT slot_capacity FROM storage_containers WHERE user_id=44`); got != 12 {
		t.Fatalf("stored slot_capacity=%d, want 12", got)
	}
	if fmt.Sprint(result["container_id"]) != "jade_ring" {
		t.Fatalf("container_id=%v, want a slug of the name", result["container_id"])
	}
	if got := gmAuditRows(t, path, "admin.player.grant_storage"); got != 1 {
		t.Fatalf("audit rows=%d, want 1", got)
	}
}

func TestGrantingStorageToAMissingCharacterIsRefused(t *testing.T) {
	path := setupAdminPlayerDB(t)
	if _, err := gmApply(t, path, "admin.player.grant_storage", 7,
		map[string]any{"user_id": 999, "name": "Jade Ring", "slot_capacity": 80}); err == nil {
		t.Fatal("storage was granted to a user with no character")
	}
	if got := adminScalar(t, path, `SELECT COUNT(*) FROM storage_containers`); got != 0 {
		t.Fatalf("container rows=%d after a refusal", got)
	}
	if got := gmAuditRows(t, path, "admin.player.grant_storage"); got != 0 {
		t.Fatalf("audit rows=%d after a refusal", got)
	}
}

// ---------------------------------------------------------------- spawn_realm

func TestSpawningARealmOpensAnEventAndMintsItsKey(t *testing.T) {
	path := setupAdminPlayerDB(t)
	result, err := gmApply(t, path, "admin.world.spawn_realm", 7, map[string]any{
		"realm_id": "azure_tomb", "title": "The Azure Tomb",
		"location": "Greenriver Town", "open_hours": 8,
	})
	if err != nil {
		t.Fatal(err)
	}
	key := fmt.Sprint(result["event_key"])
	if key == "" || key == "<nil>" {
		t.Fatal("no event_key was returned")
	}
	// The key carries the realm and is minted engine-side, so two spawns of the
	// same realm are two events rather than one overwriting the other.
	second, err := gmApply(t, path, "admin.world.spawn_realm", 7, map[string]any{
		"realm_id": "azure_tomb", "title": "The Azure Tomb",
		"location": "Greenriver Town", "open_hours": 8,
	})
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(second["event_key"]) == key {
		t.Fatal("two spawns produced the same event_key")
	}
	if got := adminScalar(t, path, `SELECT COUNT(*) FROM world_events WHERE active=1`); got != 2 {
		t.Fatalf("active events=%d, want 2", got)
	}
	if got := gmAuditRows(t, path, "admin.world.spawn_realm"); got != 2 {
		t.Fatalf("audit rows=%d, want one per spawn", got)
	}
	if got := adminScalar(t, path,
		`SELECT ends_at > starts_at FROM world_events WHERE event_key=?`, key); got != 1 {
		t.Fatal("the realm closes no later than it opened")
	}
}

func TestSpawningARealmWithoutItsDetailsIsRefused(t *testing.T) {
	path := setupAdminPlayerDB(t)
	for name, payload := range map[string]map[string]any{
		"no realm_id":   {"title": "T", "location": "Greenriver Town", "open_hours": 8},
		"no location":   {"realm_id": "azure_tomb", "title": "T", "open_hours": 8},
		"zero hours":    {"realm_id": "azure_tomb", "title": "T", "location": "Greenriver Town", "open_hours": 0},
		"no open_hours": {"realm_id": "azure_tomb", "title": "T", "location": "Greenriver Town"},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := gmApply(t, path, "admin.world.spawn_realm", 7, payload); err == nil {
				t.Fatal("the spawn was accepted")
			}
		})
	}
	if got := adminScalar(t, path, `SELECT COUNT(*) FROM world_events`); got != 0 {
		t.Fatalf("world_events rows=%d after refusals", got)
	}
}

func adminExec(t *testing.T, path, sql string, args ...any) {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := conn.Execute(sql, args); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
}

// The bug the storage and spawn tests turned up while they were being written,
// pinned so it cannot come back. `fmt.Sprint` on a missing map key returns the
// four characters "<nil>", so every required-field guard written as
// `strings.TrimSpace(fmt.Sprint(p[key])) == ""` passed on exactly the payload
// it existed to reject - and the action carried on with "<nil>" as a currency
// id, an event key, or a location. 25 sites across the package read payload
// strings this way.
func TestAMissingPayloadStringReadsAsEmptyRatherThanTheWordNil(t *testing.T) {
	present := map[string]any{"name": "  Jade Ring  ", "empty": "   ", "null": nil, "number": 7}
	for name, tc := range map[string]struct {
		key  string
		want string
	}{
		"trims a real value": {"name", "Jade Ring"},
		"absent key":         {"missing", ""},
		"explicit null":      {"null", ""},
		"whitespace only":    {"empty", ""},
		"non-string value":   {"number", "7"},
	} {
		t.Run(name, func(t *testing.T) {
			if got := stringField(present, tc.key); got != tc.want {
				t.Fatalf("stringField(%q)=%q, want %q", tc.key, got, tc.want)
			}
		})
	}
}

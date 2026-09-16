package game

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/contentsync"
	"xianxia/core/internal/storage"
)

// The content tables exactly as the projection defines them - generated from
// contentsync.Sections, so this fixture cannot carry a column the code does
// not write - beside the two tables the action touches around them.
func contentReloadSchema() string {
	var b strings.Builder
	b.WriteString("CREATE TABLE world_state(key TEXT PRIMARY KEY, value_json TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL DEFAULT 0);\n")
	b.WriteString("CREATE TABLE admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT, admin_user_id INTEGER NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);\n")
	for _, s := range contentsync.Sections {
		b.WriteString("CREATE TABLE " + s.Table + "(name TEXT PRIMARY KEY")
		for _, c := range s.Columns {
			typ := "TEXT"
			if c.Kind != contentsync.Text {
				typ = "INTEGER"
			}
			b.WriteString(", " + c.Name + " " + typ)
		}
		b.WriteString(", data_json TEXT NOT NULL, updated_at REAL NOT NULL);\n")
	}
	return b.String()
}

const reloadWorld = `{"npcs": {"Elder Su Yan": {"role": "elder", "location": "Cloudspine Foothills"}}, "locations": {"Cloudspine Foothills": {"world": "Mortal World", "safe_zone": false, "min_realm_index": 0}}}`

func setupContentReload(t *testing.T) (dbPath, worldPath string) {
	t.Helper()
	dir := t.TempDir()
	dbPath = filepath.Join(dir, "reload.sqlite3")
	worldPath = filepath.Join(dir, "world.json")
	if err := os.WriteFile(worldPath, []byte(reloadWorld), 0o644); err != nil {
		t.Fatal(err)
	}
	conn, err := storage.Open(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(contentReloadSchema()); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return dbPath, worldPath
}

func reload(t *testing.T, dbPath, worldPath string, reason string) map[string]any {
	t.Helper()
	conn, err := storage.Open(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	raw, _ := json.Marshal(map[string]any{"reason": reason})
	result, err := adminContentReload(conn, worldPath, 7, raw)
	if err != nil {
		t.Fatalf("admin.content.reload: %v", err)
	}
	return result
}

// The GM half of "Sync world catalog": the apply and its audit row are one
// commit, an unchanged file is still an audited request, and a change shows
// up in the tables the next read uses.
func TestAdminContentReloadAppliesAndAuditsInOneCommit(t *testing.T) {
	dbPath, worldPath := setupContentReload(t)
	first := reload(t, dbPath, worldPath, "first look")
	if first["applied"] != true || first["skipped"] != false {
		t.Fatalf("first reload must apply: %+v", first)
	}
	if got := storage.ParseInt(actionScalar(t, dbPath, `SELECT COUNT(*) FROM content_npcs`)); got != 1 {
		t.Fatalf("content_npcs rows=%d want 1", got)
	}
	if got := fmt.Sprint(actionScalar(t, dbPath, `SELECT action||'|'||target||'|'||reason FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1`)); got != "admin.content.reload|"+contentsync.VersionKey+"|first look" {
		t.Fatalf("audit row=%q", got)
	}
	after := fmt.Sprint(actionScalar(t, dbPath, `SELECT after_json FROM admin_audit_log ORDER BY audit_id DESC LIMIT 1`))
	if !strings.Contains(after, fmt.Sprint(first["hash"])) || !strings.Contains(after, `"applied":true`) {
		t.Fatalf("the audit row must carry the hash it applied: %s", after)
	}

	second := reload(t, dbPath, worldPath, "nothing changed")
	if second["applied"] != false || second["hash"] != first["hash"] || second["previous_hash"] != first["hash"] {
		t.Fatalf("an unchanged file must not be rewritten: %+v", second)
	}
	if got := storage.ParseInt(actionScalar(t, dbPath, `SELECT COUNT(*) FROM admin_audit_log`)); got != 2 {
		t.Fatalf("an unchanged reload is still an audited request: %d rows", got)
	}

	edited := strings.Replace(reloadWorld, `"role": "elder"`, `"role": "hermit"`, 1)
	if err := os.WriteFile(worldPath, []byte(edited), 0o644); err != nil {
		t.Fatal(err)
	}
	third := reload(t, dbPath, worldPath, "the file changed")
	if third["applied"] != true || third["previous_hash"] != first["hash"] || third["hash"] == first["hash"] {
		t.Fatalf("an edit must be applied and the row must name what it replaced: %+v", third)
	}
	if got := actionScalar(t, dbPath, `SELECT role FROM content_npcs WHERE name='Elder Su Yan'`); got != "hermit" {
		t.Fatalf("role after reload=%v", got)
	}
}

func TestAdminContentReloadRefusesWithoutAWorldPath(t *testing.T) {
	dbPath, _ := setupContentReload(t)
	conn, err := storage.Open(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err := adminContentReload(conn, "", 7, json.RawMessage(`{}`)); err == nil {
		t.Fatal("no world path must be an error, not an empty apply")
	}
	if got := storage.ParseInt(actionScalar(t, dbPath, `SELECT COUNT(*) FROM admin_audit_log`)); got != 0 {
		t.Fatalf("a refused reload must not be audited as done: %d rows", got)
	}
}

// On a database the migration has not reached, the action reports skipped -
// and still writes the audit row, because the GM did ask.
func TestAdminContentReloadOnAnUnmigratedDatabaseIsSkippedNotFailed(t *testing.T) {
	dir := t.TempDir()
	dbPath := filepath.Join(dir, "bare.sqlite3")
	worldPath := filepath.Join(dir, "world.json")
	if err := os.WriteFile(worldPath, []byte(reloadWorld), 0o644); err != nil {
		t.Fatal(err)
	}
	conn, err := storage.Open(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript("CREATE TABLE world_state(key TEXT PRIMARY KEY, value_json TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL DEFAULT 0);\nCREATE TABLE admin_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT, admin_user_id INTEGER NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);"); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	result := reload(t, dbPath, worldPath, "too early")
	if result["skipped"] != true || result["applied"] != false {
		t.Fatalf("want skipped: %+v", result)
	}
	if got := storage.ParseInt(actionScalar(t, dbPath, `SELECT COUNT(*) FROM admin_audit_log WHERE action='admin.content.reload'`)); got != 1 {
		t.Fatalf("the request is audited even when there was nothing to write: %d", got)
	}
}

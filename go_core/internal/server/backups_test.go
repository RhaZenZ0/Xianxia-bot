package server

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"xianxia/core/internal/backupcrypt"
	"xianxia/core/internal/storage"
)

func namedBackup(when time.Time, size int64) backupInfo {
	name := "xianxia-" + when.UTC().Format("20060102-150405.000") + ".sqlite3"
	return backupInfo{Name: name, Size: size, ModifiedAt: float64(when.Unix()), when: backupTime(name, when)}
}

func backupNames(items []backupInfo) []string {
	out := make([]string, 0, len(items))
	for _, item := range items {
		out = append(out, item.Name)
	}
	return out
}

// Retention keeps every backup from the newest N days that have one, then
// the newest backup of each of the next M weeks, and drops the rest.
func TestRetainBackupsKeepsDailyWholeThenOnePerWeek(t *testing.T) {
	base := time.Date(2026, 9, 10, 12, 0, 0, 0, time.UTC)
	var items []backupInfo
	// Three backups today, two yesterday, one each of the eight days before
	// that, then one a week for ten weeks.
	for _, offset := range []time.Duration{0, 2 * time.Hour, 5 * time.Hour} {
		items = append(items, namedBackup(base.Add(-offset), 10))
	}
	for _, offset := range []time.Duration{24 * time.Hour, 30 * time.Hour} {
		items = append(items, namedBackup(base.Add(-offset), 10))
	}
	for day := 2; day < 10; day++ {
		items = append(items, namedBackup(base.Add(-time.Duration(day)*24*time.Hour), 10))
	}
	for week := 2; week < 12; week++ {
		items = append(items, namedBackup(base.Add(-time.Duration(week)*7*24*time.Hour), 10))
		items = append(items, namedBackup(base.Add(-time.Duration(week)*7*24*time.Hour-3*time.Hour), 10))
	}
	keep, drop := retainBackups(items, backupPolicy{KeepDaily: 3, KeepWeekly: 4})
	// Daily window: today (3), yesterday (2), the day before (1) = 6 files.
	for _, item := range keep[:6] {
		if base.Sub(item.when) > 3*24*time.Hour {
			t.Fatalf("daily window kept %s, which is too old", item.Name)
		}
	}
	// Weekly tier: four weeks, one file each, the newest of its week.
	weekly := keep[6:]
	if len(weekly) != 4 {
		t.Fatalf("weekly survivors=%v, want 4", backupNames(weekly))
	}
	seenWeeks := map[string]bool{}
	for _, item := range weekly {
		y, w := item.when.ISOWeek()
		key := fmt.Sprintf("%d-%d", y, w)
		if seenWeeks[key] {
			t.Fatalf("two survivors in one week: %v", backupNames(weekly))
		}
		seenWeeks[key] = true
	}
	if len(keep)+len(drop) != len(items) {
		t.Fatalf("keep %d + drop %d != %d", len(keep), len(drop), len(items))
	}
	if len(drop) == 0 {
		t.Fatal("nothing was dropped")
	}
	// The newest file is always first and always kept.
	if keep[0].when != base {
		t.Fatalf("newest kept=%s, want the base stamp", keep[0].Name)
	}
	// Within a surviving week the older sibling was the one dropped.
	for _, item := range drop {
		for _, kept := range weekly {
			y1, w1 := item.when.ISOWeek()
			y2, w2 := kept.when.ISOWeek()
			if y1 == y2 && w1 == w2 && item.when.After(kept.when) {
				t.Fatalf("dropped %s but kept the older %s in the same week", item.Name, kept.Name)
			}
		}
	}
}

func TestRetentionOffKeepsEverything(t *testing.T) {
	base := time.Now().UTC()
	var items []backupInfo
	for day := 0; day < 100; day++ {
		items = append(items, namedBackup(base.Add(-time.Duration(day)*24*time.Hour), 1))
	}
	keep, drop := retainBackups(items, backupPolicy{})
	if len(keep) != 100 || len(drop) != 0 {
		t.Fatalf("keep=%d drop=%d, want everything kept", len(keep), len(drop))
	}
}

// The size cap sheds the oldest survivors first and never the newest.
func TestSizeCapShedsOldestButNeverTheNewest(t *testing.T) {
	base := time.Date(2026, 9, 10, 12, 0, 0, 0, time.UTC)
	items := []backupInfo{
		namedBackup(base, 60), namedBackup(base.Add(-time.Hour), 30), namedBackup(base.Add(-2*time.Hour), 30),
	}
	keep, drop := retainBackups(items, backupPolicy{KeepDaily: 14, MaxBytes: 100})
	if len(keep) != 2 || keep[0].when != base || keep[1].when != base.Add(-time.Hour) {
		t.Fatalf("keep=%v", backupNames(keep))
	}
	if len(drop) != 1 || drop[0].when != base.Add(-2*time.Hour) {
		t.Fatalf("drop=%v", backupNames(drop))
	}
	// A cap smaller than the newest file still keeps the newest file.
	keep, drop = retainBackups(items, backupPolicy{MaxBytes: 10})
	if len(keep) != 1 || keep[0].when != base || len(drop) != 2 {
		t.Fatalf("under a tiny cap keep=%v drop=%v", backupNames(keep), backupNames(drop))
	}
}

func TestBackupNamesAndStamps(t *testing.T) {
	for _, name := range []string{"xianxia-20260910-120000.000.sqlite3", "xianxia-20260910-120000.000-2.sqlite3", "xianxia-20260910-120000.000.sqlite3.enc"} {
		if !isBackupName(name) {
			t.Fatalf("%s should be a backup name", name)
		}
	}
	for _, name := range []string{"notes.txt", "xianxia-20260910.sqlite3.bak", ".restore-xianxia-1.sqlite3", "xianxia.sqlite3"} {
		if isBackupName(name) {
			t.Fatalf("%s should not be a backup name", name)
		}
	}
	fallback := time.Date(2000, 1, 1, 0, 0, 0, 0, time.UTC)
	if got := backupTime("xianxia-20260910-120000.250-3.sqlite3.enc", fallback); got != time.Date(2026, 9, 10, 12, 0, 0, 250_000_000, time.UTC) {
		t.Fatalf("stamp=%v", got)
	}
	if got := backupTime("xianxia-garbage.sqlite3", fallback); got != fallback {
		t.Fatalf("unparseable stamp should fall back, got %v", got)
	}
}

// pruneBackups touches only files the engine would list: an operator's own
// file in the directory is never deleted.
func TestPruneBackupsLeavesForeignFilesAlone(t *testing.T) {
	dir := t.TempDir()
	base := time.Date(2026, 9, 10, 12, 0, 0, 0, time.UTC)
	for day := 0; day < 40; day++ {
		item := namedBackup(base.Add(-time.Duration(day)*24*time.Hour), 1)
		if err := os.WriteFile(filepath.Join(dir, item.Name), []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(dir, "operator-copy.sqlite3"), []byte("mine"), 0o644); err != nil {
		t.Fatal(err)
	}
	removed, err := pruneBackups(dir, backupPolicy{KeepDaily: 7, KeepWeekly: 2})
	if err != nil {
		t.Fatal(err)
	}
	if len(removed) == 0 {
		t.Fatal("nothing pruned")
	}
	if _, err := os.Stat(filepath.Join(dir, "operator-copy.sqlite3")); err != nil {
		t.Fatal("the operator's own file was removed")
	}
	left, _ := listBackups(dir)
	if len(left) != 7+2 {
		t.Fatalf("%d backups left, want 9: %v", len(left), backupNames(left))
	}
}

// The endpoint prunes after each backup under the server's policy.
func TestDbBackupsPrunesAfterEachCreate(t *testing.T) {
	engine := batchTestServer(t)
	engine.backups = backupPolicy{KeepDaily: 1, KeepWeekly: 0}
	if err := os.MkdirAll(engine.backupDir(), 0o755); err != nil {
		t.Fatal(err)
	}
	old := namedBackup(time.Now().UTC().Add(-72*time.Hour), 1)
	if err := os.WriteFile(filepath.Join(engine.backupDir(), old.Name), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	out := postBackupCreate(t, engine)
	pruned, _ := out["pruned"].([]any)
	if len(pruned) != 1 || pruned[0] != old.Name {
		t.Fatalf("pruned=%v, want [%s]", out["pruned"], old.Name)
	}
	if _, err := os.Stat(filepath.Join(engine.backupDir(), old.Name)); !os.IsNotExist(err) {
		t.Fatal("the old backup is still there")
	}
	if out["encrypted"] != false {
		t.Fatalf("encrypted=%v, want false without a key", out["encrypted"])
	}
}

// With a key, a backup is written sealed only, listed as such, and a
// restore from it round-trips through the key.
func TestEncryptedBackupIsSealedListedAndRestorable(t *testing.T) {
	engine := batchTestServer(t)
	engine.backups = backupPolicy{Key: "operator passphrase"}
	postBatch(t, engine, `{"statements":[{"sql":"INSERT INTO batch_probe(label) VALUES(?)","params":["keep"]}]}`)

	out := postBackupCreate(t, engine)
	name, _ := out["name"].(string)
	if !strings.HasSuffix(name, ".sqlite3"+backupcrypt.Suffix) || out["encrypted"] != true {
		t.Fatalf("backup should be sealed: %v", out)
	}
	entries, _ := os.ReadDir(engine.backupDir())
	for _, entry := range entries {
		if !backupcrypt.IsEncryptedName(entry.Name()) {
			t.Fatalf("a plain file was left beside the sealed backup: %s", entry.Name())
		}
	}
	sealed, _ := os.ReadFile(filepath.Join(engine.backupDir(), name))
	if strings.Contains(string(sealed), "SQLite format 3") {
		t.Fatal("the sealed backup carries the SQLite header in the clear")
	}
	if _, err := storage.Open(filepath.Join(engine.backupDir(), name)); err == nil {
		// Opening succeeds lazily for any file; a query is the real probe.
		conn, _ := storage.Open(filepath.Join(engine.backupDir(), name))
		if _, err := conn.Execute("SELECT COUNT(*) FROM batch_probe", nil); err == nil {
			t.Fatal("the sealed backup reads as a database")
		}
		conn.Close()
	}

	listReq := postListBackups(t, engine)
	rows, _ := listReq["backups"].([]any)
	if len(rows) != 1 || rows[0].(map[string]any)["encrypted"] != true {
		t.Fatalf("listing=%v", listReq)
	}
	policy, _ := listReq["policy"].(map[string]any)
	if policy["encrypted"] != true {
		t.Fatalf("policy should say encrypted: %v", policy)
	}

	postBatch(t, engine, `{"statements":[{"sql":"INSERT INTO batch_probe(label) VALUES(?)","params":["should_vanish"]}]}`)
	payload, _ := json.Marshal(map[string]any{"name": name})
	response := postRestore(t, engine, string(payload))
	if response.Code != 200 {
		t.Fatalf("restore: expected 200, got %d: %s", response.Code, response.Body.String())
	}
	if got := countProbeRows(t, engine); got != 1 {
		t.Fatalf("post-restore row count=%d, want 1", got)
	}
	entries, _ = os.ReadDir(engine.backupDir())
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), ".restore-") {
			t.Fatalf("the restore scratch file was left behind: %s", entry.Name())
		}
	}

	// Without the key the same restore is refused before anything is touched.
	engine.backups.Key = ""
	response = postRestore(t, engine, string(payload))
	if response.Code != 400 || !strings.Contains(response.Body.String(), "backup_key_required") {
		t.Fatalf("restore without key: %d %s", response.Code, response.Body.String())
	}
	engine.backups.Key = "the wrong one"
	response = postRestore(t, engine, string(payload))
	if response.Code != 400 || !strings.Contains(response.Body.String(), "backup_decrypt_failed") {
		t.Fatalf("restore with wrong key: %d %s", response.Code, response.Body.String())
	}
}

func TestBackupPolicyFromEnv(t *testing.T) {
	t.Setenv("XIANXIA_BACKUP_KEEP_DAILY", "")
	t.Setenv("XIANXIA_BACKUP_KEEP_WEEKLY", "")
	t.Setenv("XIANXIA_BACKUP_MAX_MB", "")
	t.Setenv("XIANXIA_BACKUP_KEY", "")
	policy := backupPolicyFromEnv()
	if policy.KeepDaily != defaultBackupKeepDaily || policy.KeepWeekly != defaultBackupKeepWeekly || policy.MaxBytes != 0 || policy.Key != "" {
		t.Fatalf("defaults: %+v", policy)
	}
	t.Setenv("XIANXIA_BACKUP_KEEP_DAILY", "0")
	t.Setenv("XIANXIA_BACKUP_KEEP_WEEKLY", "0")
	t.Setenv("XIANXIA_BACKUP_MAX_MB", "512")
	t.Setenv("XIANXIA_BACKUP_KEY", "  secret  ")
	policy = backupPolicyFromEnv()
	if policy.KeepDaily != 0 || policy.KeepWeekly != 0 || policy.MaxBytes != 512<<20 || policy.Key != "secret" {
		t.Fatalf("configured: %+v", policy)
	}
	t.Setenv("XIANXIA_BACKUP_KEEP_DAILY", "-3")
	if backupPolicyFromEnv().KeepDaily != defaultBackupKeepDaily {
		t.Fatal("a negative count should fall back to the default")
	}
}

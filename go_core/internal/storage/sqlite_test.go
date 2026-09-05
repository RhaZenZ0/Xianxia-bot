package storage

import (
	"path/filepath"
	"testing"
)

func scalar(t *testing.T, conn *Conn, sql string, params ...any) any {
	t.Helper()
	res, err := conn.Execute(sql, params)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 || len(res.Rows[0]) == 0 {
		return nil
	}
	return res.Rows[0][0]
}

func TestOpenConfiguresProductionSQLitePragmas(t *testing.T) {
	path := filepath.Join(t.TempDir(), "pragmas.sqlite3")
	conn, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	if got := scalar(t, conn, "PRAGMA foreign_keys"); ParseInt(got) != 1 {
		t.Fatalf("foreign_keys=%v", got)
	}
	if got := scalar(t, conn, "PRAGMA busy_timeout"); ParseInt(got) != 10000 {
		t.Fatalf("busy_timeout=%v", got)
	}
	if got := scalar(t, conn, "PRAGMA synchronous"); ParseInt(got) != 1 {
		t.Fatalf("synchronous=%v", got)
	}
	if got := scalar(t, conn, "PRAGMA cache_size"); ParseInt(got) != -32768 {
		t.Fatalf("cache_size=%v", got)
	}
	if got := scalar(t, conn, "PRAGMA wal_autocheckpoint"); ParseInt(got) != 1000 {
		t.Fatalf("wal_autocheckpoint=%v", got)
	}
	if got := scalar(t, conn, "PRAGMA journal_mode"); got != "wal" {
		t.Fatalf("journal_mode=%v", got)
	}
}

func TestCommittedWritesAreVisibleAcrossConnections(t *testing.T) {
	path := filepath.Join(t.TempDir(), "visibility.sqlite3")
	first, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	if err := first.ExecScript("CREATE TABLE state(k TEXT PRIMARY KEY,v TEXT);"); err != nil {
		t.Fatal(err)
	}
	if _, err := first.Execute("INSERT INTO state(k,v) VALUES(?,?)", []any{"key", "first"}); err != nil {
		t.Fatal(err)
	}
	if !first.InTransaction() {
		t.Fatal("write should hold an implicit transaction until commit")
	}
	if err := first.Commit(); err != nil {
		t.Fatal(err)
	}
	if first.InTransaction() {
		t.Fatal("commit should leave autocommit mode")
	}

	second, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	if got := scalar(t, second, "SELECT v FROM state WHERE k=?", "key"); got != "first" {
		t.Fatalf("visible value=%v", got)
	}
}

func TestRollbackDiscardsImplicitWriteTransaction(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollback.sqlite3")
	conn, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript("CREATE TABLE state(v INTEGER);"); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute("INSERT INTO state(v) VALUES(?)", []any{7}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Rollback(); err != nil {
		t.Fatal(err)
	}
	if got := ParseInt(scalar(t, conn, "SELECT COUNT(*) FROM state")); got != 0 {
		t.Fatalf("rows after rollback=%d", got)
	}
}

func TestBackupProducesReadableConsistentDatabase(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "source.sqlite3")
	backup := filepath.Join(dir, "backup.sqlite3")
	conn, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript("CREATE TABLE state(k TEXT PRIMARY KEY,v TEXT);"); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute("INSERT INTO state(k,v) VALUES(?,?)", []any{"keep", "jade"}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if err := conn.BackupTo(backup); err != nil {
		t.Fatal(err)
	}
	if err := conn.Close(); err != nil {
		t.Fatal(err)
	}

	copied, err := Open(backup)
	if err != nil {
		t.Fatal(err)
	}
	defer copied.Close()
	if got := scalar(t, copied, "SELECT v FROM state WHERE k='keep'"); got != "jade" {
		t.Fatalf("backup value=%v", got)
	}
}

// TestRestoreFromOverwritesLiveDatabaseWithBackupContent proves RestoreFrom is
// a true reverse of BackupTo: take a backup, mutate the live database further,
// then restore from the backup and confirm the live database reverts to
// exactly the backup's content - the post-backup mutation is gone, and rows
// present at backup time survive.
func TestRestoreFromOverwritesLiveDatabaseWithBackupContent(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "live.sqlite3")
	backup := filepath.Join(dir, "snapshot.sqlite3")
	conn, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript("CREATE TABLE state(k TEXT PRIMARY KEY,v TEXT);"); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute("INSERT INTO state(k,v) VALUES(?,?)", []any{"keep", "jade"}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if err := conn.BackupTo(backup); err != nil {
		t.Fatal(err)
	}
	// Mutate the live database after the backup was taken - this change must
	// not survive the restore below.
	if _, err := conn.Execute("INSERT INTO state(k,v) VALUES(?,?)", []any{"post_backup", "should_vanish"}); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if got := scalar(t, conn, "SELECT COUNT(*) FROM state"); got != int64(2) {
		t.Fatalf("pre-restore row count=%v, want 2", got)
	}

	if err := conn.RestoreFrom(backup); err != nil {
		t.Fatalf("RestoreFrom failed: %v", err)
	}
	if got := scalar(t, conn, "SELECT v FROM state WHERE k='keep'"); got != "jade" {
		t.Fatalf("post-restore value=%v, want jade", got)
	}
	if got := scalar(t, conn, "SELECT COUNT(*) FROM state"); got != int64(1) {
		t.Fatalf("post-restore row count=%v, want 1 (the post-backup insert must be gone)", got)
	}

	// The restore source is opened read-only, so the backup archive itself
	// must be untouched and still independently readable afterward.
	archive, err := Open(backup)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	if got := scalar(t, archive, "SELECT COUNT(*) FROM state"); got != int64(1) {
		t.Fatalf("backup archive row count=%v after restore, want unchanged 1", got)
	}
}

func TestSessionManagerKeepsTransactionsConnectionScoped(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sessions.sqlite3")
	manager := NewSessionManager(path)
	defer manager.CloseAll()
	seed, err := manager.Open()
	if err != nil {
		t.Fatal(err)
	}
	if err := seed.Conn.ExecScript("CREATE TABLE state(v INTEGER);"); err != nil {
		t.Fatal(err)
	}
	if err := manager.Close(seed.ID); err != nil {
		t.Fatal(err)
	}

	session, err := manager.Open()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := session.Conn.Execute("INSERT INTO state(v) VALUES(1)", nil); err != nil {
		t.Fatal(err)
	}
	if !session.Conn.InTransaction() {
		t.Fatal("session write should remain transactional")
	}
	if err := session.Conn.Commit(); err != nil {
		t.Fatal(err)
	}
	fetched, err := manager.Get(session.ID)
	if err != nil || fetched.Conn != session.Conn {
		t.Fatalf("session lookup mismatch: err=%v", err)
	}
}

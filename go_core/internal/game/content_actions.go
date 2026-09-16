package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/contentsync"
	"xianxia/core/internal/storage"
)

// adminContentReload (schema 51) re-reads content/world.json and brings the
// content_* tables up to it, and is the engine half of the GM's "Sync world
// catalog". It is the only way content reaches those tables on demand, and
// it writes admin_audit_log in the same commit as the rows - so the rewrite
// and the record of who asked for it cannot come apart. `worlddata.Load` is
// memoised on the file's stat, so the Go rules pick the edit up on their own;
// this is what makes the tables Python reads follow.
//
// An unchanged file is still an audited request: the row says the GM asked
// and nothing needed doing, which is a different fact from nobody asking.
func adminContentReload(conn *storage.Conn, worldPath string, adminUserID int64, raw json.RawMessage) (map[string]any, error) {
	if strings.TrimSpace(worldPath) == "" {
		return nil, errors.New("world path is required")
	}
	p, _ := decodeMap(raw)
	reason := ""
	if p != nil {
		reason = fmt.Sprint(p["reason"])
		if reason == "<nil>" {
			reason = ""
		}
	}
	if err := conn.ExecScript("BEGIN IMMEDIATE;"); err != nil {
		return nil, err
	}
	committed := false
	defer func() {
		if !committed {
			rollback(conn)
		}
	}()
	before, err := contentsync.StoredHash(conn)
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	result, err := contentsync.ApplyInTx(conn, worldPath, now)
	if err != nil {
		return nil, err
	}
	after := map[string]any{"hash": result.Hash, "applied": result.Applied, "skipped": result.Skipped, "size": result.Size, "counts": result.Counts}
	if err := auditAdmin(conn, adminUserID, "admin.content.reload", contentsync.VersionKey, map[string]any{"hash": before}, after, reason); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	committed = true
	return map[string]any{
		"hash": result.Hash, "previous_hash": before, "applied": result.Applied,
		"skipped": result.Skipped, "reason": result.Reason, "size": result.Size, "counts": result.Counts,
	}, nil
}

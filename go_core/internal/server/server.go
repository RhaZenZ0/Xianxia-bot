package server

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync/atomic"
	"time"

	"xianxia/core/internal/core"
	"xianxia/core/internal/game"
	"xianxia/core/internal/simulation"
	"xianxia/core/internal/storage"
)

type Server struct {
	databasePath string
	worldPath    string
	sessions     *storage.SessionManager
	simulation   *simulation.Runner
	requests     atomic.Uint64
}

func New(databasePath string, worldPath string) (*Server, error) {
	if strings.TrimSpace(databasePath) == "" {
		return nil, errors.New("database path is required")
	}
	if err := os.MkdirAll(filepath.Dir(databasePath), 0o755); err != nil {
		return nil, err
	}
	// Open once at boot, enable WAL persistently, and prove libsqlite is usable.
	conn, err := storage.Open(databasePath)
	if err != nil {
		return nil, err
	}
	if err := conn.ExecScript("PRAGMA journal_mode=WAL;"); err != nil {
		_ = conn.Close()
		return nil, err
	}
	_ = conn.Close()
	runner, err := simulation.NewRunner(databasePath, worldPath)
	if err != nil {
		return nil, fmt.Errorf("load world catalog: %w", err)
	}
	return &Server{databasePath: databasePath, worldPath: worldPath, sessions: storage.NewSessionManager(databasePath), simulation: runner}, nil
}

func (s *Server) Close() { s.sessions.CloseAll() }

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(payload); err != nil {
		log.Printf("encode response: %v", err)
	}
}

func decodeJSON(r *http.Request, dst any) error {
	decoder := json.NewDecoder(r.Body)
	decoder.UseNumber()
	if err := decoder.Decode(dst); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("request body must contain exactly one JSON value")
		}
		return err
	}
	return nil
}

func limitJSONBody(w http.ResponseWriter, r *http.Request, maxBytes int64) {
	r.Body = http.MaxBytesReader(w, r.Body, maxBytes)
}

func method(w http.ResponseWriter, r *http.Request, expected string) bool {
	if r.Method == expected {
		return true
	}
	writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
	return false
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/livez", s.livez)
	mux.HandleFunc("/readyz", s.readyz)
	mux.HandleFunc("/v1/game/action", s.gameAction)
	mux.HandleFunc("/v1/simulation/bootstrap", s.simulationBootstrap)
	mux.HandleFunc("/v1/simulation/run-due", s.simulationRunDue)
	mux.HandleFunc("/v1/simulation/force", s.simulationForce)
	mux.HandleFunc("/v1/db/session", s.dbSession)
	mux.HandleFunc("/v1/db/session/", s.dbSessionAction)
	mux.HandleFunc("/v1/db/batch", s.dbBatch)
	mux.HandleFunc("/v1/db/status", s.dbStatus)
	mux.HandleFunc("/v1/db/maintenance", s.dbMaintenance)
	mux.HandleFunc("/v1/db/backups", s.dbBackups)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.requests.Add(1)
		mux.ServeHTTP(w, r)
	})
}

func (s *Server) livez(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"status": "alive", "api_version": core.APIVersion, "role": "authoritative-game-engine"})
}

func (s *Server) readyz(w http.ResponseWriter, r *http.Request) {
	conn, err := storage.Open(s.databasePath)
	if err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"status": "not_ready", "error": err.Error()})
		return
	}
	defer conn.Close()
	result, err := conn.Execute("PRAGMA journal_mode", nil)
	if err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"status": "not_ready", "error": err.Error()})
		return
	}
	journal := "unknown"
	if len(result.Rows) > 0 && len(result.Rows[0]) > 0 {
		journal = fmt.Sprint(result.Rows[0][0])
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "ready", "journal_mode": journal, "database_path": s.databasePath})
}

func (s *Server) gameAction(w http.ResponseWriter, r *http.Request) {
	if !method(w, r, http.MethodPost) {
		return
	}
	defer r.Body.Close()
	limitJSONBody(w, r, 2<<20)
	var input game.ActionRequest
	if err := decodeJSON(r, &input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_json", "message": err.Error()})
		return
	}
	response, err := game.ApplyWithWorld(s.databasePath, s.worldPath, input)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "game_action_failed", "message": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) simulationBootstrap(w http.ResponseWriter, r *http.Request) {
	if !method(w, r, http.MethodPost) {
		return
	}
	defer r.Body.Close()
	limitJSONBody(w, r, 1<<20)
	var input simulation.BootstrapRequest
	if err := decodeJSON(r, &input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_json", "message": err.Error()})
		return
	}
	result, err := s.simulation.Bootstrap(input)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "simulation_bootstrap_failed", "message": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) simulationRunDue(w http.ResponseWriter, r *http.Request) {
	if !method(w, r, http.MethodPost) {
		return
	}
	defer r.Body.Close()
	limitJSONBody(w, r, 1<<20)
	var input simulation.RunDueRequest
	if err := decodeJSON(r, &input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_json", "message": err.Error()})
		return
	}
	runs, err := s.simulation.RunDue(input)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "simulation_failed", "message": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"runs": runs})
}

func (s *Server) simulationForce(w http.ResponseWriter, r *http.Request) {
	if !method(w, r, http.MethodPost) {
		return
	}
	defer r.Body.Close()
	limitJSONBody(w, r, 1<<20)
	var input simulation.ForceRequest
	if err := decodeJSON(r, &input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_json", "message": err.Error()})
		return
	}
	run, err := s.simulation.Force(input)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "simulation_failed", "message": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, run)
}

func (s *Server) dbSession(w http.ResponseWriter, r *http.Request) {
	if !method(w, r, http.MethodPost) {
		return
	}
	session, err := s.sessions.Open()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "db_open_failed", "message": err.Error()})
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"session_id": session.ID})
}

type executeRequest struct {
	SQL    string `json:"sql"`
	Params []any  `json:"params"`
}

type scriptRequest struct {
	SQL string `json:"sql"`
}

func (s *Server) dbSessionAction(w http.ResponseWriter, r *http.Request) {
	suffix := strings.TrimPrefix(r.URL.Path, "/v1/db/session/")
	parts := strings.Split(strings.Trim(suffix, "/"), "/")
	if len(parts) < 1 || parts[0] == "" {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "session_not_found"})
		return
	}
	id := parts[0]
	if len(parts) == 1 {
		if r.Method != http.MethodDelete {
			writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
			return
		}
		if err := s.sessions.Close(id); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "db_close_failed", "message": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"closed": true})
		return
	}
	session, err := s.sessions.Get(id)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "session_not_found", "message": err.Error()})
		return
	}
	action := parts[1]
	switch action {
	case "execute":
		if !method(w, r, http.MethodPost) {
			return
		}
		defer r.Body.Close()
		limitJSONBody(w, r, 4<<20)
		var input executeRequest
		if err := decodeJSON(r, &input); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_json"})
			return
		}
		result, err := session.Conn.Execute(input.SQL, input.Params)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "sqlite_execute_failed", "message": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, result)
	case "script":
		if !method(w, r, http.MethodPost) {
			return
		}
		defer r.Body.Close()
		limitJSONBody(w, r, 4<<20)
		var input scriptRequest
		if err := decodeJSON(r, &input); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_json"})
			return
		}
		if err := session.Conn.ExecScript(input.SQL); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "sqlite_script_failed", "message": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true})
	case "commit":
		if !method(w, r, http.MethodPost) {
			return
		}
		if err := session.Conn.Commit(); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "sqlite_commit_failed", "message": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true})
	case "rollback":
		if !method(w, r, http.MethodPost) {
			return
		}
		if err := session.Conn.Rollback(); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "sqlite_rollback_failed", "message": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true})
	default:
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "unknown_session_action"})
	}
}

type batchStatement struct {
	SQL    string `json:"sql"`
	Params []any  `json:"params"`
}
type batchRequest struct {
	Statements  []batchStatement `json:"statements"`
	Transaction bool             `json:"transaction"`
}

func (s *Server) dbBatch(w http.ResponseWriter, r *http.Request) {
	if !method(w, r, http.MethodPost) {
		return
	}
	defer r.Body.Close()
	limitJSONBody(w, r, 16<<20)
	var input batchRequest
	if err := decodeJSON(r, &input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_json"})
		return
	}
	if len(input.Statements) == 0 || len(input.Statements) > 5000 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_batch_size"})
		return
	}
	conn, err := storage.Open(s.databasePath)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "db_open_failed", "message": err.Error()})
		return
	}
	defer conn.Close()
	if input.Transaction {
		if err := conn.ExecScript("BEGIN IMMEDIATE;"); err != nil {
			writeJSON(w, http.StatusConflict, map[string]any{"error": "begin_failed", "message": err.Error()})
			return
		}
	}
	results := make([]storage.Result, 0, len(input.Statements))
	for _, stmt := range input.Statements {
		result, err := conn.Execute(stmt.SQL, stmt.Params)
		if err != nil {
			if input.Transaction {
				_ = conn.Rollback()
			} else if commitErr := conn.Commit(); commitErr != nil {
				// transaction:false means every statement stands on its own, so
				// the ones that already succeeded have to survive a later one
				// failing.  Without this commit the deferred Close below rolls
				// them back and "completed": N is a lie.
				_ = conn.Rollback()
				writeJSON(w, http.StatusConflict, map[string]any{"error": "commit_failed", "message": commitErr.Error(), "completed": len(results)})
				return
			}
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "batch_execute_failed", "message": err.Error(), "completed": len(results)})
			return
		}
		results = append(results, result)
	}
	// Commit unconditionally, not only when the caller asked for a transaction.
	// storage.Conn opens an implicit BEGIN ahead of every INSERT/UPDATE/DELETE/
	// REPLACE (maybeBeginImplicitLocked) and Conn.Close rolls back whatever is
	// still open, so returning 200 here without committing used to report
	// success and then discard every write as soon as the deferred Close ran.
	// Commit() is a no-op while the connection is in autocommit, so a read-only
	// batch is unaffected.
	if err := conn.Commit(); err != nil {
		_ = conn.Rollback()
		writeJSON(w, http.StatusConflict, map[string]any{"error": "commit_failed", "message": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"results": results, "count": len(results)})
}

func (s *Server) dbStatus(w http.ResponseWriter, r *http.Request) {
	if !method(w, r, http.MethodGet) {
		return
	}
	conn, err := storage.Open(s.databasePath)
	if err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": err.Error()})
		return
	}
	defer conn.Close()
	pragmas := map[string]any{}
	for _, name := range []string{"journal_mode", "synchronous", "foreign_keys", "busy_timeout", "wal_autocheckpoint"} {
		res, err := conn.Execute("PRAGMA "+name, nil)
		if err == nil && len(res.Rows) > 0 && len(res.Rows[0]) > 0 {
			pragmas[name] = res.Rows[0][0]
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"database_path": s.databasePath, "pragmas": pragmas, "requests": s.requests.Load()})
}

type maintenanceRequest struct {
	Action string `json:"action"`
}

func (s *Server) dbMaintenance(w http.ResponseWriter, r *http.Request) {
	if !method(w, r, http.MethodPost) {
		return
	}
	defer r.Body.Close()
	limitJSONBody(w, r, 64<<10)
	var input maintenanceRequest
	if err := decodeJSON(r, &input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_json"})
		return
	}
	conn, err := storage.Open(s.databasePath)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "db_open_failed", "message": err.Error()})
		return
	}
	defer conn.Close()
	switch strings.ToLower(strings.TrimSpace(input.Action)) {
	case "vacuum":
		_, _ = conn.Execute("PRAGMA wal_checkpoint(PASSIVE)", nil)
		if err := conn.ExecScript("VACUUM; PRAGMA optimize;"); err != nil {
			writeJSON(w, http.StatusConflict, map[string]any{"error": "vacuum_failed", "message": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "action": "vacuum"})
	case "optimize":
		if err := conn.ExecScript("PRAGMA optimize;"); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "optimize_failed", "message": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "action": "optimize"})
	default:
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "unknown_maintenance_action"})
	}
}

type backupInfo struct {
	Name       string  `json:"name"`
	Size       int64   `json:"size"`
	ModifiedAt float64 `json:"modified_at"`
}

func (s *Server) backupDir() string { return filepath.Join(filepath.Dir(s.databasePath), "backups") }

// reserveBackupPath returns a backup path that does not already exist.
//
// The name used to carry second resolution only, so two backups taken inside the
// same second - trivially reachable by double-clicking the dashboard button, and
// guaranteed whenever an automated backup coincides with a manual one - resolved
// to the same filename and the second silently overwrote the first, leaving the
// operator with one file where the UI claimed two. Milliseconds make that
// practically impossible and the numbered suffix makes it actually impossible,
// while keeping filenames lexicographically sortable by age.
//
// The file is created here (O_EXCL) rather than merely probed, so two concurrent
// backup requests cannot both settle on the same free name.
func reserveBackupPath(backupDir string, now time.Time) (string, error) {
	stamp := now.Format("20060102-150405.000")
	for attempt := 0; attempt < 1000; attempt++ {
		name := "xianxia-" + stamp + ".sqlite3"
		if attempt > 0 {
			name = fmt.Sprintf("xianxia-%s-%d.sqlite3", stamp, attempt+1)
		}
		destination := filepath.Join(backupDir, name)
		handle, err := os.OpenFile(destination, os.O_RDWR|os.O_CREATE|os.O_EXCL, 0o644)
		if err == nil {
			// Leave the zero-byte placeholder in place rather than removing it:
			// a zero-length file is a valid empty SQLite database, so BackupTo's
			// sqlite3_open_v2(..., CREATE) is happy to write into it, and
			// keeping it means a concurrent request can never claim this name in
			// the window between reserving and writing.
			_ = handle.Close()
			return destination, nil
		}
		if !os.IsExist(err) {
			return "", err
		}
	}
	return "", errors.New("could not find an unused backup filename")
}

func (s *Server) dbBackups(w http.ResponseWriter, r *http.Request) {
	backupDir := s.backupDir()
	switch r.Method {
	case http.MethodPost:
		if err := os.MkdirAll(backupDir, 0o755); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "backup_dir_failed", "message": err.Error()})
			return
		}
		destination, err := reserveBackupPath(backupDir, time.Now().UTC())
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "backup_name_failed", "message": err.Error()})
			return
		}
		source, err := storage.Open(s.databasePath)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "db_open_failed", "message": err.Error()})
			return
		}
		err = source.BackupTo(destination)
		_ = source.Close()
		if err != nil {
			_ = os.Remove(destination)
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "backup_failed", "message": err.Error()})
			return
		}
		info, err := os.Stat(destination)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "backup_stat_failed", "message": err.Error()})
			return
		}
		writeJSON(w, http.StatusCreated, backupInfo{Name: info.Name(), Size: info.Size(), ModifiedAt: float64(info.ModTime().UnixNano()) / 1e9})
	case http.MethodGet:
		entries, err := os.ReadDir(backupDir)
		if err != nil && !os.IsNotExist(err) {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "backup_list_failed", "message": err.Error()})
			return
		}
		items := make([]backupInfo, 0)
		for _, entry := range entries {
			if entry.IsDir() || !strings.HasPrefix(entry.Name(), "xianxia-") || !strings.HasSuffix(entry.Name(), ".sqlite3") {
				continue
			}
			info, err := entry.Info()
			if err != nil {
				continue
			}
			items = append(items, backupInfo{Name: info.Name(), Size: info.Size(), ModifiedAt: float64(info.ModTime().UnixNano()) / 1e9})
		}
		sort.Slice(items, func(i, j int) bool { return items[i].ModifiedAt > items[j].ModifiedAt })
		if len(items) > 50 {
			items = items[:50]
		}
		writeJSON(w, http.StatusOK, map[string]any{"backups": items})
	default:
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
	}
}

func (s *Server) StartReaper(stop <-chan struct{}) {
	ticker := time.NewTicker(30 * time.Second)
	go func() {
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				if n := s.sessions.Reap(2 * time.Minute); n > 0 {
					log.Printf("reaped %d idle db sessions", n)
				}
			case <-stop:
				return
			}
		}
	}()
}

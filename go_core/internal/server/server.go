package server

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"time"

	"xianxia/core/internal/backupcrypt"
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
	authToken    string
	// Held shared by anything that can write and exclusively by restore and
	// VACUUM, so maintenance never runs alongside traffic. See maintenance.go.
	maintenance maintenanceBarrier
	// Retention, size cap and the optional key for data/backups (backups.go).
	backups backupPolicy
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
	// The token is read here, not only in main, so that every way of
	// constructing a Server - the binary, a test, a future embedder - is held
	// to the same rule. Until v0.29.0 a blank token made authorized() answer
	// true for every request: the engine door stood open whenever the
	// variable was unset, and only startup.sh stood between an operator and
	// that state.
	token := strings.TrimSpace(os.Getenv("ENGINE_AUTH_TOKEN"))
	if len(token) < minEngineTokenLength {
		return nil, fmt.Errorf("ENGINE_AUTH_TOKEN must be set to at least %d characters", minEngineTokenLength)
	}
	return &Server{databasePath: databasePath, worldPath: worldPath, sessions: storage.NewSessionManager(databasePath), simulation: runner, authToken: token, backups: backupPolicyFromEnv()}, nil
}

// minEngineTokenLength is the shortest ENGINE_AUTH_TOKEN the engine will run
// with. startup.sh and cmd/xianxia-core/main.go state the same number.
const minEngineTokenLength = 20

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
	mux.HandleFunc("/v1/db/restore", s.dbRestore)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.requests.Add(1)
		if strings.HasPrefix(r.URL.Path, "/v1/") && !s.authorized(r) {
			writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "engine_auth_required"})
			return
		}
		// The maintenance barrier is taken here rather than inside each
		// handler, so a future endpoint cannot forget it.
		if exclusive, guarded := barrierFor(r.URL.Path); guarded {
			if exclusive {
				defer s.maintenance.enter()()
			} else {
				defer s.maintenance.begin()()
			}
		}
		mux.ServeHTTP(w, r)
	})
}

func (s *Server) authorized(r *http.Request) bool {
	// Fail closed. New() refuses to build a Server without a token, so this
	// branch is unreachable in practice; it exists so that a Server built any
	// other way (a zero value, a future constructor) denies rather than admits.
	if s.authToken == "" {
		return false
	}
	provided := r.Header.Get("X-Xianxia-Engine-Token")
	return len(provided) == len(s.authToken) && subtle.ConstantTimeCompare([]byte(provided), []byte(s.authToken)) == 1
}

func (s *Server) livez(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"status": "alive", "api_version": core.APIVersion, "role": "authoritative-game-engine"})
}

func (s *Server) readyz(w http.ResponseWriter, r *http.Request) {
	// Deliberately outside the barrier: a health check that blocks for the
	// length of a restore reads as an outage. It reports the maintenance
	// instead.
	if s.maintenance.busy() {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"status": "maintenance", "maintenance_in_progress": true})
		return
	}
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
	// Encrypted says the file is sealed with XIANXIA_BACKUP_KEY (v0.32.0) -
	// the name says so too, but a caller should not have to parse it.
	Encrypted bool `json:"encrypted"`
	// when is the stamp in the name, what retention sorts by; not exposed
	// because modified_at already is and the two agree for a file nothing
	// has touched.
	when time.Time
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
		// Sealing (v0.32.0): the plain file is written first because that is
		// what the SQLite backup API produces, then sealed beside it and the
		// plain copy removed. A failure to seal removes both - a backup the
		// operator asked to be encrypted is not left in the clear.
		if s.backups.Key != "" {
			sealed := destination + backupcrypt.Suffix
			if err := backupcrypt.EncryptFile(destination, sealed, s.backups.Key); err != nil {
				_ = os.Remove(destination)
				_ = os.Remove(sealed)
				writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "backup_encrypt_failed", "message": err.Error()})
				return
			}
			_ = os.Remove(destination)
			destination = sealed
		}
		info, err := os.Stat(destination)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "backup_stat_failed", "message": err.Error()})
			return
		}
		// Retention runs after every successful backup, so the directory is
		// bounded by the same act that grows it. The file just written is
		// the newest and is never among the pruned.
		pruned, pruneErr := pruneBackups(backupDir, s.backups)
		if pruneErr != nil {
			log.Printf("backup retention: %v", pruneErr)
		}
		response := backupInfoFor(info)
		writeJSON(w, http.StatusCreated, map[string]any{
			"name": response.Name, "size": response.Size, "modified_at": response.ModifiedAt, "encrypted": response.Encrypted,
			"pruned": pruned,
		})
	case http.MethodGet:
		items, err := listBackups(backupDir)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "backup_list_failed", "message": err.Error()})
			return
		}
		if len(items) > 50 {
			items = items[:50]
		}
		writeJSON(w, http.StatusOK, map[string]any{"backups": items, "policy": map[string]any{
			"keep_daily": s.backups.KeepDaily, "keep_weekly": s.backups.KeepWeekly,
			"max_bytes": s.backups.MaxBytes, "encrypted": s.backups.Key != "",
		}})
	default:
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
	}
}

type restoreRequest struct {
	Name string `json:"name"`
}

// dbRestore replaces the live database with the contents of a previously
// taken backup. This is the single most destructive operation this server
// exposes, so it never touches the live file without first taking its own
// "just in case" backup of the current state - a restore that turns out to
// be a mistake is then itself just one more restore away from being undone.
//
// Order matters, and it used to be wrong (v0.22.3, review finding #4). The
// safety backup was taken *first*, while ordinary requests were still
// committing; a mutation that landed in the gap was acknowledged to the
// player, then overwritten by the restore, and was in neither the live
// database nor the safety backup. The sequence is now:
//
//  1. the maintenance barrier, taken exclusively by the middleware, which
//     waits for every in-flight request and holds off every new one;
//  2. close every open db-session, since a session's connection outlives the
//     request that made it and can be sitting mid-transaction;
//  3. only then the safety backup - a snapshot of a database nothing is
//     writing to;
//  4. the restore itself.
//
// So an acknowledged write is either in the safety backup (it finished before
// the barrier) or in the live database (it ran after the restore). It is never
// in neither.
func (s *Server) dbRestore(w http.ResponseWriter, r *http.Request) {
	if !method(w, r, http.MethodPost) {
		return
	}
	defer r.Body.Close()
	limitJSONBody(w, r, 4<<10)
	var input restoreRequest
	if err := decodeJSON(r, &input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_json"})
		return
	}
	// filepath.Base collapses any "../" traversal down to a bare filename, and
	// the prefix/suffix check below matches exactly what dbBackups' own
	// listing accepts - so a restore can only ever target a file that
	// endpoint would itself have listed as a real backup.
	name := filepath.Base(strings.TrimSpace(input.Name))
	if name == "" || name == "." || name == string(filepath.Separator) || !isBackupName(name) {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_backup_name"})
		return
	}
	backupDir := s.backupDir()
	sourcePath := filepath.Join(backupDir, name)
	if info, err := os.Stat(sourcePath); err != nil || info.IsDir() {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "backup_not_found"})
		return
	}
	// A sealed backup (v0.32.0) is opened into a scratch file beside it that
	// lives only for this restore. The key check happens before anything is
	// quiesced, so a wrong key costs nothing.
	if backupcrypt.IsEncryptedName(name) {
		if s.backups.Key == "" {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "backup_key_required", "message": "this backup is encrypted; set XIANXIA_BACKUP_KEY on the engine to restore it"})
			return
		}
		opened := filepath.Join(backupDir, ".restore-"+strings.TrimSuffix(name, backupcrypt.Suffix))
		if err := backupcrypt.DecryptFile(sourcePath, opened, s.backups.Key); err != nil {
			_ = os.Remove(opened)
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "backup_decrypt_failed", "message": err.Error()})
			return
		}
		defer os.Remove(opened)
		sourcePath = opened
	}

	if err := os.MkdirAll(backupDir, 0o755); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "backup_dir_failed", "message": err.Error()})
		return
	}
	// Quiesce before snapshotting. The barrier (taken by the middleware) has
	// already drained in-flight requests; this closes the long-lived session
	// connections that outlive them.
	s.sessions.CloseAll()

	safetyPath, err := reserveBackupPath(backupDir, time.Now().UTC())
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "safety_backup_name_failed", "message": err.Error()})
		return
	}
	safetySource, err := storage.Open(s.databasePath)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "db_open_failed", "message": err.Error()})
		return
	}
	if err := safetySource.BackupTo(safetyPath); err != nil {
		_ = safetySource.Close()
		_ = os.Remove(safetyPath)
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "safety_backup_failed", "message": err.Error()})
		return
	}
	_ = safetySource.Close()
	safetyStat, err := os.Stat(safetyPath)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "safety_backup_stat_failed", "message": err.Error()})
		return
	}
	safetyInfo := backupInfo{Name: safetyStat.Name(), Size: safetyStat.Size(), ModifiedAt: float64(safetyStat.ModTime().UnixNano()) / 1e9}

	dest, err := storage.Open(s.databasePath)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "db_open_failed", "message": err.Error(), "safety_backup": safetyInfo})
		return
	}
	restoreErr := dest.RestoreFrom(sourcePath)
	_ = dest.Close()
	if restoreErr != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "restore_failed", "message": restoreErr.Error(), "safety_backup": safetyInfo})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "restored_from": name, "safety_backup": safetyInfo})
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

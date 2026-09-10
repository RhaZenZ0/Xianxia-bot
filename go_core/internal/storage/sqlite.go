package storage

/*
#cgo LDFLAGS: -lsqlite3
#include <sqlite3.h>
#include <stdlib.h>

static int bind_text_transient(sqlite3_stmt *stmt, int idx, const char *value, int n) {
    return sqlite3_bind_text(stmt, idx, value, n, SQLITE_TRANSIENT);
}
static int bind_blob_transient(sqlite3_stmt *stmt, int idx, const void *value, int n) {
    return sqlite3_bind_blob(stmt, idx, value, n, SQLITE_TRANSIENT);
}
*/
import "C"

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
	"sync"
	"unsafe"
)

type Row map[string]any

type Result struct {
	Columns      []string `json:"columns"`
	Rows         [][]any  `json:"rows"`
	LastInsertID int64    `json:"lastrowid"`
	RowsAffected int64    `json:"rowcount"`
}

type Conn struct {
	mu     sync.Mutex
	handle *C.sqlite3
	closed bool
}

func Open(path string) (*Conn, error) {
	cpath := C.CString(path)
	defer C.free(unsafe.Pointer(cpath))
	var handle *C.sqlite3
	flags := C.int(C.SQLITE_OPEN_READWRITE | C.SQLITE_OPEN_CREATE | C.SQLITE_OPEN_FULLMUTEX)
	if rc := C.sqlite3_open_v2(cpath, &handle, flags, nil); rc != C.SQLITE_OK {
		msg := "sqlite open failed"
		if handle != nil {
			msg = C.GoString(C.sqlite3_errmsg(handle))
			C.sqlite3_close_v2(handle)
		}
		return nil, errors.New(msg)
	}
	conn := &Conn{handle: handle}
	if err := conn.configure(); err != nil {
		conn.Close()
		return nil, err
	}
	return conn, nil
}

func (c *Conn) configure() error {
	// WAL is persistent at database level. The remaining pragmas are connection-local.
	// Apply busy_timeout before touching journal mode so concurrent connection
	// startup waits instead of producing transient lock failures.
	script := `PRAGMA busy_timeout=10000;
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;
PRAGMA cache_size=-32768;
PRAGMA wal_autocheckpoint=1000;`
	return c.ExecScript(script)
}

func (c *Conn) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed || c.handle == nil {
		return nil
	}
	if C.sqlite3_get_autocommit(c.handle) == 0 {
		_ = c.execScriptLocked("ROLLBACK;")
	}
	rc := C.sqlite3_close_v2(c.handle)
	c.closed = true
	c.handle = nil
	if rc != C.SQLITE_OK {
		return fmt.Errorf("sqlite close failed: %d", int(rc))
	}
	return nil
}

func (c *Conn) BackupTo(path string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed || c.handle == nil {
		return errors.New("sqlite connection closed")
	}
	cpath := C.CString(path)
	defer C.free(unsafe.Pointer(cpath))
	var dest *C.sqlite3
	flags := C.int(C.SQLITE_OPEN_READWRITE | C.SQLITE_OPEN_CREATE | C.SQLITE_OPEN_FULLMUTEX)
	if rc := C.sqlite3_open_v2(cpath, &dest, flags, nil); rc != C.SQLITE_OK {
		msg := "sqlite backup destination open failed"
		if dest != nil {
			msg = C.GoString(C.sqlite3_errmsg(dest))
			C.sqlite3_close_v2(dest)
		}
		return errors.New(msg)
	}
	defer C.sqlite3_close_v2(dest)
	main := C.CString("main")
	defer C.free(unsafe.Pointer(main))
	backup := C.sqlite3_backup_init(dest, main, c.handle, main)
	if backup == nil {
		return fmt.Errorf("sqlite backup init failed: %s", C.GoString(C.sqlite3_errmsg(dest)))
	}
	rc := C.sqlite3_backup_step(backup, -1)
	finishRC := C.sqlite3_backup_finish(backup)
	if rc != C.SQLITE_DONE {
		return fmt.Errorf("sqlite backup step failed: %d", int(rc))
	}
	if finishRC != C.SQLITE_OK {
		return fmt.Errorf("sqlite backup finish failed: %d", int(finishRC))
	}
	return nil
}

// RestoreFrom overwrites this connection's "main" database with the contents
// of the SQLite file at path, using the same online backup API as BackupTo
// but with source/destination reversed. The source file is opened read-only
// so a restore can never itself mutate the backup archive's actual page
// content. Like BackupTo, this runs under SQLite's own locking - a
// concurrent reader on this connection's database sees either the
// pre-restore or post-restore state, never a torn mix, and
// sqlite3_backup_step honours this connection's busy_timeout if the
// destination is momentarily locked by another writer.
func (c *Conn) RestoreFrom(path string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed || c.handle == nil {
		return errors.New("sqlite connection closed")
	}
	cpath := C.CString(path)
	defer C.free(unsafe.Pointer(cpath))
	var source *C.sqlite3
	flags := C.int(C.SQLITE_OPEN_READONLY)
	if rc := C.sqlite3_open_v2(cpath, &source, flags, nil); rc != C.SQLITE_OK {
		msg := "sqlite restore source open failed"
		if source != nil {
			msg = C.GoString(C.sqlite3_errmsg(source))
			C.sqlite3_close_v2(source)
		}
		return errors.New(msg)
	}
	main := C.CString("main")
	defer C.free(unsafe.Pointer(main))
	backup := C.sqlite3_backup_init(c.handle, main, source, main)
	if backup == nil {
		err := fmt.Errorf("sqlite restore init failed: %s", C.GoString(C.sqlite3_errmsg(c.handle)))
		C.sqlite3_close_v2(source)
		return err
	}
	rc := C.sqlite3_backup_step(backup, -1)
	finishRC := C.sqlite3_backup_finish(backup)
	C.sqlite3_close_v2(source)
	// BackupTo copies the source's page 1 header verbatim, which records
	// that the live (WAL-mode) database requests WAL journaling - so every
	// backup archive carries that flag too, even though nothing ever writes
	// to it. Opening it here, even read-only, makes SQLite provision the WAL
	// index it needs to read consistently, leaving -wal/-shm sidecar files
	// next to the archive. Clean those up so a backup stays the single
	// self-contained file a GM expects to find in the backups list.
	_ = os.Remove(path + "-wal")
	_ = os.Remove(path + "-shm")
	if rc != C.SQLITE_DONE {
		return fmt.Errorf("sqlite restore step failed: %d", int(rc))
	}
	if finishRC != C.SQLITE_OK {
		return fmt.Errorf("sqlite restore finish failed: %d", int(finishRC))
	}
	return nil
}

func (c *Conn) err(rc C.int) error {
	if c.handle == nil {
		return errors.New("sqlite connection closed")
	}
	return fmt.Errorf("sqlite error %d: %s", int(rc), C.GoString(C.sqlite3_errmsg(c.handle)))
}

func leadingOperation(sql string) string {
	trimmed := strings.TrimSpace(sql)
	if trimmed == "" {
		return ""
	}
	if strings.HasPrefix(trimmed, "--") {
		for strings.HasPrefix(trimmed, "--") {
			if idx := strings.IndexByte(trimmed, '\n'); idx >= 0 {
				trimmed = strings.TrimSpace(trimmed[idx+1:])
			} else {
				return ""
			}
		}
	}
	fields := strings.Fields(trimmed)
	if len(fields) == 0 {
		return ""
	}
	return strings.ToUpper(fields[0])
}

func (c *Conn) maybeBeginImplicitLocked(sql string) error {
	if C.sqlite3_get_autocommit(c.handle) == 0 {
		return nil
	}
	switch leadingOperation(sql) {
	case "INSERT", "UPDATE", "DELETE", "REPLACE":
		return c.execScriptLocked("BEGIN;")
	default:
		return nil
	}
}

func (c *Conn) Execute(sql string, params []any) (Result, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed || c.handle == nil {
		return Result{}, errors.New("sqlite connection closed")
	}
	if err := c.maybeBeginImplicitLocked(sql); err != nil {
		return Result{}, err
	}
	csql := C.CString(sql)
	defer C.free(unsafe.Pointer(csql))
	var stmt *C.sqlite3_stmt
	var tail *C.char
	if rc := C.sqlite3_prepare_v2(c.handle, csql, -1, &stmt, &tail); rc != C.SQLITE_OK {
		return Result{}, c.err(rc)
	}
	if stmt == nil {
		return Result{}, nil
	}
	defer C.sqlite3_finalize(stmt)
	if err := c.bindAll(stmt, params); err != nil {
		return Result{}, err
	}

	columnsCount := int(C.sqlite3_column_count(stmt))
	columns := make([]string, columnsCount)
	for i := 0; i < columnsCount; i++ {
		columns[i] = C.GoString(C.sqlite3_column_name(stmt, C.int(i)))
	}
	rows := make([][]any, 0)
	for {
		rc := C.sqlite3_step(stmt)
		if rc == C.SQLITE_ROW {
			row := make([]any, columnsCount)
			for i := 0; i < columnsCount; i++ {
				row[i] = columnValue(stmt, i)
			}
			rows = append(rows, row)
			continue
		}
		if rc == C.SQLITE_DONE {
			break
		}
		return Result{}, c.err(rc)
	}
	return Result{
		Columns:      columns,
		Rows:         rows,
		LastInsertID: int64(C.sqlite3_last_insert_rowid(c.handle)),
		RowsAffected: int64(C.sqlite3_changes(c.handle)),
	}, nil
}

func (c *Conn) bindAll(stmt *C.sqlite3_stmt, params []any) error {
	expected := int(C.sqlite3_bind_parameter_count(stmt))
	if expected != len(params) {
		return fmt.Errorf("sqlite bind count mismatch: expected %d got %d", expected, len(params))
	}
	for idx, value := range params {
		if err := c.bind(stmt, idx+1, value); err != nil {
			return err
		}
	}
	return nil
}

func (c *Conn) bind(stmt *C.sqlite3_stmt, idx int, value any) error {
	var rc C.int
	switch v := value.(type) {
	case nil:
		rc = C.sqlite3_bind_null(stmt, C.int(idx))
	case bool:
		n := int64(0)
		if v {
			n = 1
		}
		rc = C.sqlite3_bind_int64(stmt, C.int(idx), C.sqlite3_int64(n))
	case json.Number:
		if n, err := v.Int64(); err == nil {
			rc = C.sqlite3_bind_int64(stmt, C.int(idx), C.sqlite3_int64(n))
		} else if f, err := v.Float64(); err == nil {
			rc = C.sqlite3_bind_double(stmt, C.int(idx), C.double(f))
		} else {
			return fmt.Errorf("invalid json number %q", v.String())
		}
	case int:
		rc = C.sqlite3_bind_int64(stmt, C.int(idx), C.sqlite3_int64(v))
	case int64:
		rc = C.sqlite3_bind_int64(stmt, C.int(idx), C.sqlite3_int64(v))
	case int32:
		rc = C.sqlite3_bind_int64(stmt, C.int(idx), C.sqlite3_int64(v))
	case float64:
		if math.Trunc(v) == v && v >= math.MinInt64 && v <= math.MaxInt64 {
			rc = C.sqlite3_bind_int64(stmt, C.int(idx), C.sqlite3_int64(int64(v)))
		} else {
			rc = C.sqlite3_bind_double(stmt, C.int(idx), C.double(v))
		}
	case float32:
		rc = C.sqlite3_bind_double(stmt, C.int(idx), C.double(v))
	case string:
		cs := C.CString(v)
		rc = C.bind_text_transient(stmt, C.int(idx), cs, C.int(len([]byte(v))))
		C.free(unsafe.Pointer(cs))
	case []byte:
		if len(v) == 0 {
			rc = C.bind_blob_transient(stmt, C.int(idx), nil, 0)
		} else {
			ptr := C.CBytes(v)
			rc = C.bind_blob_transient(stmt, C.int(idx), ptr, C.int(len(v)))
			C.free(ptr)
		}
	case map[string]any:
		raw, ok := v["__blob_b64"].(string)
		if !ok {
			return fmt.Errorf("unsupported sqlite parameter object")
		}
		data, err := base64.StdEncoding.DecodeString(raw)
		if err != nil {
			return fmt.Errorf("invalid base64 blob: %w", err)
		}
		if len(data) == 0 {
			rc = C.bind_blob_transient(stmt, C.int(idx), nil, 0)
		} else {
			ptr := C.CBytes(data)
			rc = C.bind_blob_transient(stmt, C.int(idx), ptr, C.int(len(data)))
			C.free(ptr)
		}
	default:
		return fmt.Errorf("unsupported sqlite parameter type %T", value)
	}
	if rc != C.SQLITE_OK {
		return c.err(rc)
	}
	return nil
}

func columnValue(stmt *C.sqlite3_stmt, idx int) any {
	i := C.int(idx)
	switch C.sqlite3_column_type(stmt, i) {
	case C.SQLITE_INTEGER:
		return int64(C.sqlite3_column_int64(stmt, i))
	case C.SQLITE_FLOAT:
		return float64(C.sqlite3_column_double(stmt, i))
	case C.SQLITE_TEXT:
		ptr := C.sqlite3_column_text(stmt, i)
		n := C.sqlite3_column_bytes(stmt, i)
		if ptr == nil || n == 0 {
			return ""
		}
		return C.GoStringN((*C.char)(unsafe.Pointer(ptr)), n)
	case C.SQLITE_BLOB:
		ptr := C.sqlite3_column_blob(stmt, i)
		n := C.sqlite3_column_bytes(stmt, i)
		if ptr == nil || n == 0 {
			return map[string]string{"__blob_b64": ""}
		}
		data := C.GoBytes(ptr, n)
		return map[string]string{"__blob_b64": base64.StdEncoding.EncodeToString(data)}
	default:
		return nil
	}
}

func (c *Conn) ExecScript(sql string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed || c.handle == nil {
		return errors.New("sqlite connection closed")
	}
	return c.execScriptLocked(sql)
}

func (c *Conn) execScriptLocked(sql string) error {
	csql := C.CString(sql)
	defer C.free(unsafe.Pointer(csql))
	var errmsg *C.char
	rc := C.sqlite3_exec(c.handle, csql, nil, nil, &errmsg)
	if rc != C.SQLITE_OK {
		msg := "sqlite script failed"
		if errmsg != nil {
			msg = C.GoString(errmsg)
			C.sqlite3_free(unsafe.Pointer(errmsg))
		}
		return fmt.Errorf("%s (code %d)", msg, int(rc))
	}
	return nil
}

func (c *Conn) Commit() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed || c.handle == nil {
		return errors.New("sqlite connection closed")
	}
	if C.sqlite3_get_autocommit(c.handle) != 0 {
		return nil
	}
	return c.execScriptLocked("COMMIT;")
}

func (c *Conn) Rollback() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed || c.handle == nil {
		return errors.New("sqlite connection closed")
	}
	if C.sqlite3_get_autocommit(c.handle) != 0 {
		return nil
	}
	return c.execScriptLocked("ROLLBACK;")
}

func (c *Conn) InTransaction() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.handle != nil && C.sqlite3_get_autocommit(c.handle) == 0
}

func ParseInt(value any) int64 {
	switch v := value.(type) {
	case int64:
		return v
	case int:
		return int64(v)
	case float64:
		return int64(v)
	case json.Number:
		n, _ := v.Int64()
		return n
	case string:
		n, _ := strconv.ParseInt(v, 10, 64)
		return n
	default:
		return 0
	}
}

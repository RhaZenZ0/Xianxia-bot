// Package contentsync mirrors content/world.json into the nine derived
// content_* tables (schema 51), and is the only thing that writes them.
//
// The tables are a projection of the content file, not a second copy of the
// truth: each row carries the entry's exact bytes from the file in data_json,
// and a handful of typed, indexed columns beside it for the queries that want
// to filter or join. Readers that need the whole entry take the blob, exactly
// as they took catalog_*'s; the columns exist so that "every NPC in this
// district" or "every manual of this path" is one indexed query instead of a
// parse of 2.5 MB.
//
// Two decisions carry the design. The first is that the rows are RAW JSON.
// The plan for these tables widened Go's typed structs field by field and
// called that step "silent when wrong" - a field missed in Go is an empty
// column and nothing errors. Keeping the file's own bytes per entry makes the
// blob complete by construction, and the projection is checked against those
// bytes (TestProjectionMatchesTheRawEntries). The second is that Apply is
// gated on a hash of the file and is a wholesale DELETE + INSERT when it
// differs: the Python-side sync these tables replace never deleted, so a
// renamed NPC lived in catalog_npcs forever.
//
// Apply is safe to call at any time and from anywhere - engine start, the
// /v1/content/sync endpoint db-init and the bot call after migrations, and
// the GM's admin.content.reload - because it does nothing when the tables are
// not there yet (first boot: the engine is healthy before Python has run the
// migration) and nothing when the hash is unchanged.
package contentsync

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"

	"xianxia/core/internal/storage"
)

// VersionKey is the world_state row holding the hash of the file last applied.
const VersionKey = "content_version"

// Kind is how a projected column reads its JSON value.
type Kind int

const (
	// Text stores a string as-is and a number or bool formatted.
	Text Kind = iota
	// Integer stores a JSON number truncated, or a bool as 0/1.
	Integer
	// Flag stores 1 when the key is present at all - for the fields whose
	// value is a structure (a circuit list, a hidden-master block) and whose
	// presence is the fact worth indexing.
	Flag
)

// Column is one projected column: the SQL name, the JSON key (dotted for a
// nested path) and how to read it. Absent keys are stored as NULL, which is
// what lets the parity test count them.
type Column struct {
	Name string
	Key  string
	Kind Kind
}

// Section is one derived table and where its entries live in world.json.
type Section struct {
	Table   string
	Path    []string
	Columns []Column
}

// Sections is the whole contract: nine tables, their source path in the file,
// and the columns projected beside the blob. The Python migration that
// creates the tables is held to this list by a contract test, so a column
// added here without its DDL fails there rather than at boot.
var Sections = []Section{
	{Table: "content_npcs", Path: []string{"npcs"}, Columns: []Column{
		{"role", "role", Text},
		{"realm", "realm", Text},
		{"location", "location", Text},
		{"district", "district", Text},
		{"shop", "shop", Text},
		{"sect_affiliation", "sect_affiliation", Text},
		{"merchant", "merchant", Text},
		{"circuit", "circuit", Flag},
		{"hidden_master", "hidden_master", Flag},
	}},
	{Table: "content_locations", Path: []string{"locations"}, Columns: []Column{
		{"world", "world", Text},
		{"outside_location", "outside_location", Text},
		{"settlement_type", "settlement_type", Text},
		{"district", "district", Text},
		{"road_site", "road_site", Text},
		{"shop", "shop", Text},
		{"auction_house", "auction_house", Text},
		{"safe_zone", "safe_zone", Integer},
		{"private", "private", Integer},
		{"min_realm_index", "min_realm_index", Integer},
	}},
	{Table: "content_items", Path: []string{"items"}, Columns: []Column{
		{"display_name", "name", Text},
		{"type", "type", Text},
		{"legal_status", "legal_status", Text},
		{"auction_interest", "auction_interest", Text},
		{"manual_id", "manual_id", Text},
		{"base_price", "base_price", Integer},
		{"sect_value", "sect_value", Integer},
		{"market_excluded", "market_excluded", Integer},
	}},
	{Table: "content_recipes", Path: []string{"recipes"}, Columns: []Column{
		{"profession", "profession", Text},
		{"tn", "tn", Integer},
		{"min_level", "min_level", Integer},
	}},
	{Table: "content_sects", Path: []string{"sects"}, Columns: []Column{
		{"alignment", "alignment", Text},
		{"specialty", "specialty", Text},
		{"recruitment_location", "recruitment.location", Text},
		{"hidden", "hidden", Integer},
	}},
	{Table: "content_shops", Path: []string{"shops"}, Columns: []Column{
		{"display_name", "name", Text},
		{"kind", "kind", Text},
		{"city", "city", Text},
		{"world", "world", Text},
		{"location", "location", Text},
		{"keeper", "keeper", Text},
		{"currency", "currency", Text},
		{"tier", "tier", Integer},
	}},
	{Table: "content_merchants", Path: []string{"merchants"}, Columns: []Column{
		{"display_name", "name", Text},
		{"world", "world", Text},
		{"home", "home", Text},
		{"currency", "currency", Text},
		{"budget", "budget", Integer},
		{"markup_percent", "markup_percent", Integer},
	}},
	{Table: "content_manuals", Path: []string{"technique_system", "manuals"}, Columns: []Column{
		{"display_name", "name", Text},
		{"item_id", "item_id", Text},
		{"alignment", "alignment", Text},
		{"path", "path", Text},
		{"grade", "grade", Text},
		{"element", "element", Text},
		{"sect", "sect", Text},
		{"min_realm_index", "min_realm_index", Integer},
	}},
	{Table: "content_techniques", Path: []string{"technique_system", "techniques"}, Columns: []Column{
		{"display_name", "name", Text},
		{"manual", "manual", Text},
		{"min_mastery", "min_mastery", Integer},
		{"qi_cost", "qi_cost", Integer},
		{"karma_cost", "karma_cost", Integer},
		{"exposure", "exposure", Integer},
	}},
}

// Tables lists the nine table names in Sections order.
func Tables() []string {
	out := make([]string, 0, len(Sections))
	for _, s := range Sections {
		out = append(out, s.Table)
	}
	return out
}

// Snapshot is one parsed reading of the content file: its hash and, per
// table, every entry's raw bytes keyed by the entry's own key.
type Snapshot struct {
	Hash    string
	Size    int64
	Entries map[string]map[string]json.RawMessage
}

// Load reads and parses the file without touching the database.
func Load(path string) (*Snapshot, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(data)
	var top map[string]json.RawMessage
	if err := json.Unmarshal(data, &top); err != nil {
		return nil, fmt.Errorf("content file is not a JSON object: %w", err)
	}
	snap := &Snapshot{Hash: hex.EncodeToString(sum[:]), Size: int64(len(data)), Entries: map[string]map[string]json.RawMessage{}}
	for _, section := range Sections {
		node := top
		var raw json.RawMessage
		for i, step := range section.Path {
			var ok bool
			raw, ok = node[step]
			if !ok {
				raw = nil
				break
			}
			if i < len(section.Path)-1 {
				next := map[string]json.RawMessage{}
				if err := json.Unmarshal(raw, &next); err != nil {
					return nil, fmt.Errorf("%s: %s is not an object: %w", section.Table, strings.Join(section.Path[:i+1], "."), err)
				}
				node = next
			}
		}
		entries := map[string]json.RawMessage{}
		if raw != nil {
			if err := json.Unmarshal(raw, &entries); err != nil {
				return nil, fmt.Errorf("%s: %s is not an object keyed by id: %w", section.Table, strings.Join(section.Path, "."), err)
			}
		}
		snap.Entries[section.Table] = entries
	}
	return snap, nil
}

// Result says what Apply did.
type Result struct {
	// Skipped is true when the tables are not there to write - the migration
	// that creates them has not run yet. Not an error: it is the state every
	// first boot passes through.
	Skipped bool   `json:"skipped"`
	Reason  string `json:"reason,omitempty"`
	// Applied is true when the file's hash differed from the stored one and
	// the tables were rewritten.
	Applied bool             `json:"applied"`
	Hash    string           `json:"hash"`
	Size    int64            `json:"size"`
	Counts  map[string]int64 `json:"counts"`
}

// TablesPresent reports whether every content_* table exists.
func TablesPresent(conn *storage.Conn) (bool, error) {
	res, err := conn.Execute(`SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN (`+placeholders(len(Sections))+`)`, anyNames())
	if err != nil {
		return false, err
	}
	return len(res.Rows) > 0 && storage.ParseInt(res.Rows[0][0]) == int64(len(Sections)), nil
}

// StoredHash is the hash of the file last applied, or "" when none was.
func StoredHash(conn *storage.Conn) (string, error) {
	res, err := conn.Execute(`SELECT value_json FROM world_state WHERE key=?`, []any{VersionKey})
	if err != nil {
		return "", err
	}
	if len(res.Rows) == 0 {
		return "", nil
	}
	var stored struct {
		Hash string `json:"hash"`
	}
	if err := json.Unmarshal([]byte(fmt.Sprint(res.Rows[0][0])), &stored); err != nil {
		return "", nil // an unreadable marker means "re-apply", never "fail"
	}
	return stored.Hash, nil
}

// Counts is the row count of every content table.
func Counts(conn *storage.Conn) (map[string]int64, error) {
	out := map[string]int64{}
	for _, section := range Sections {
		res, err := conn.Execute(`SELECT COUNT(*) FROM `+section.Table, nil)
		if err != nil {
			return nil, err
		}
		if len(res.Rows) > 0 {
			out[section.Table] = storage.ParseInt(res.Rows[0][0])
		}
	}
	return out, nil
}

// Apply brings the content tables up to the file at path, in one
// transaction of its own, only when the file has changed since the last
// apply. The caller owns the connection and must not be inside a transaction.
func Apply(conn *storage.Conn, path string, now float64) (Result, error) {
	present, err := TablesPresent(conn)
	if err != nil {
		return Result{}, err
	}
	if !present {
		return Result{Skipped: true, Reason: "content tables are not migrated yet"}, nil
	}
	snap, err := Load(path)
	if err != nil {
		return Result{}, err
	}
	stored, err := StoredHash(conn)
	if err != nil {
		return Result{}, err
	}
	if stored == snap.Hash {
		counts, err := Counts(conn)
		if err != nil {
			return Result{}, err
		}
		return Result{Applied: false, Hash: snap.Hash, Size: snap.Size, Counts: counts}, nil
	}
	if err := conn.ExecScript("BEGIN IMMEDIATE;"); err != nil {
		return Result{}, err
	}
	committed := false
	defer func() {
		if !committed {
			_ = conn.Rollback()
		}
	}()
	result, err := writeSnapshot(conn, snap, path, now)
	if err != nil {
		return Result{}, err
	}
	if err := conn.Commit(); err != nil {
		return Result{}, err
	}
	committed = true
	return result, nil
}

// ApplyInTx is Apply inside a transaction the caller has already opened and
// will commit - for the admin action, whose audit row has to land in the same
// commit as the rows it describes. It never begins, commits or rolls back.
func ApplyInTx(conn *storage.Conn, path string, now float64) (Result, error) {
	present, err := TablesPresent(conn)
	if err != nil {
		return Result{}, err
	}
	if !present {
		return Result{Skipped: true, Reason: "content tables are not migrated yet"}, nil
	}
	snap, err := Load(path)
	if err != nil {
		return Result{}, err
	}
	stored, err := StoredHash(conn)
	if err != nil {
		return Result{}, err
	}
	if stored == snap.Hash {
		counts, err := Counts(conn)
		if err != nil {
			return Result{}, err
		}
		return Result{Applied: false, Hash: snap.Hash, Size: snap.Size, Counts: counts}, nil
	}
	return writeSnapshot(conn, snap, path, now)
}

// writeSnapshot is the rewrite itself: every table emptied and refilled in
// Sections order, then the version marker. Transaction handling is the
// caller's.
func writeSnapshot(conn *storage.Conn, snap *Snapshot, path string, now float64) (Result, error) {
	counts := map[string]int64{}
	for _, section := range Sections {
		if _, err := conn.Execute(`DELETE FROM `+section.Table, nil); err != nil {
			return Result{}, err
		}
		entries := snap.Entries[section.Table]
		keys := make([]string, 0, len(entries))
		for key := range entries {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		insert := insertSQL(section)
		for _, key := range keys {
			values, err := project(section, key, entries[key], now)
			if err != nil {
				return Result{}, fmt.Errorf("%s %q: %w", section.Table, key, err)
			}
			if _, err := conn.Execute(insert, values); err != nil {
				return Result{}, fmt.Errorf("%s %q: %w", section.Table, key, err)
			}
		}
		counts[section.Table] = int64(len(keys))
	}
	marker, _ := json.Marshal(map[string]any{"hash": snap.Hash, "size": snap.Size, "path": path, "applied_at": now, "counts": counts})
	if _, err := conn.Execute(`INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at`, []any{VersionKey, string(marker), now}); err != nil {
		return Result{}, err
	}
	return Result{Applied: true, Hash: snap.Hash, Size: snap.Size, Counts: counts}, nil
}

// ApplyPath is Apply on its own connection, for callers that hold none: the
// engine at start, the sync endpoint.
func ApplyPath(databasePath, worldPath string, now float64) (Result, error) {
	if strings.TrimSpace(worldPath) == "" {
		return Result{}, errors.New("world path is required")
	}
	conn, err := storage.Open(databasePath)
	if err != nil {
		return Result{}, err
	}
	defer conn.Close()
	return Apply(conn, worldPath, now)
}

func insertSQL(section Section) string {
	cols := []string{"name"}
	for _, c := range section.Columns {
		cols = append(cols, c.Name)
	}
	cols = append(cols, "data_json", "updated_at")
	return `INSERT INTO ` + section.Table + `(` + strings.Join(cols, ",") + `) VALUES(` + placeholders(len(cols)) + `)`
}

// project is one row: the key, every projected column read off the parsed
// entry (NULL when the key is absent), the entry's raw bytes, the timestamp.
func project(section Section, key string, raw json.RawMessage, now float64) ([]any, error) {
	var entry map[string]any
	if err := json.Unmarshal(raw, &entry); err != nil {
		return nil, fmt.Errorf("entry is not an object: %w", err)
	}
	values := []any{key}
	for _, column := range section.Columns {
		value, present := lookup(entry, column.Key)
		if !present || value == nil {
			values = append(values, nil)
			continue
		}
		switch column.Kind {
		case Flag:
			values = append(values, int64(1))
		case Integer:
			switch v := value.(type) {
			case float64:
				values = append(values, int64(v))
			case bool:
				if v {
					values = append(values, int64(1))
				} else {
					values = append(values, int64(0))
				}
			default:
				return nil, fmt.Errorf("%s: expected a number, got %T", column.Key, value)
			}
		default:
			switch v := value.(type) {
			case string:
				values = append(values, v)
			case float64, bool:
				values = append(values, fmt.Sprint(v))
			default:
				return nil, fmt.Errorf("%s: expected text, got %T", column.Key, value)
			}
		}
	}
	values = append(values, string(raw), now)
	return values, nil
}

// lookup walks a dotted key path. The second return is false when any step
// is missing, so an absent nested key is NULL like an absent flat one.
func lookup(entry map[string]any, key string) (any, bool) {
	parts := strings.Split(key, ".")
	var current any = entry
	for _, part := range parts {
		object, ok := current.(map[string]any)
		if !ok {
			return nil, false
		}
		current, ok = object[part]
		if !ok {
			return nil, false
		}
	}
	return current, true
}

func placeholders(n int) string {
	return strings.TrimSuffix(strings.Repeat("?,", n), ",")
}

func anyNames() []any {
	out := make([]any, 0, len(Sections))
	for _, s := range Sections {
		out = append(out, s.Table)
	}
	return out
}

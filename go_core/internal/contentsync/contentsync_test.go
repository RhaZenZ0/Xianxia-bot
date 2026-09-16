package contentsync

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// The tables exactly as the projection defines them. Generated from Sections
// rather than copied, so the fixture cannot describe columns the code does
// not write - that is the shape of mistake that let npc_consignments ship
// broken (see CLAUDE.md, Testing conventions).
func fixtureDDL() string {
	var b strings.Builder
	b.WriteString("CREATE TABLE world_state(key TEXT PRIMARY KEY, value_json TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL DEFAULT 0);\n")
	for _, s := range Sections {
		b.WriteString("CREATE TABLE " + s.Table + "(name TEXT PRIMARY KEY")
		for _, c := range s.Columns {
			typ := "TEXT"
			if c.Kind != Text {
				typ = "INTEGER"
			}
			b.WriteString(", " + c.Name + " " + typ)
		}
		b.WriteString(", data_json TEXT NOT NULL, updated_at REAL NOT NULL);\n")
	}
	return b.String()
}

const smallWorld = `{
  "world_name": "Test",
  "npcs": {
    "Elder Su Yan": {"role": "elder", "realm": "Foundation Establishment", "location": "Cloudspine Foothills", "personality": "stern", "circuit": ["A", "B"]},
    "Boatman Lu":   {"role": "boatman", "realm": "Mortal", "location": "Greenriver Town", "district": "Greenriver Docks", "shop": "lu_ferry"}
  },
  "locations": {
    "Greenriver Town": {"world": "Mortal World", "safe_zone": true, "min_realm_index": 0, "settlement_type": "town"},
    "Greenriver Docks": {"world": "Mortal World", "safe_zone": false, "min_realm_index": 0, "outside_location": "Greenriver Town", "district": "docks"}
  },
  "items": {"spirit_herb": {"name": "Spirit Herb", "base_price": 5, "sect_value": 2, "market_excluded": false}},
  "recipes": {"Recovery Pill": {"profession": "Alchemy", "tn": 10, "min_level": 0, "cost": {"spirit_herb": 2}}},
  "sects": {"Azure Cloud Sect": {"alignment": "Orthodox", "recruitment": {"location": "Cloudspine Foothills"}}},
  "shops": {"lu_ferry": {"name": "Lu's Ferry", "kind": "ferry", "city": "Greenriver Town", "world": "Mortal World", "location": "Greenriver Docks", "keeper": "Boatman Lu", "currency": "low_spirit_stone", "tier": 1}},
  "merchants": {"old_hu": {"name": "Old Hu", "world": "Mortal World", "home": "Greenriver Town", "currency": "low_spirit_stone", "budget": 400, "markup_percent": 160}},
  "technique_system": {
    "manuals": {"blood_sea": {"name": "Blood Sea Scripture", "item_id": "blood_sea_manual", "alignment": "Demonic", "path": "Blood", "grade": "Earth", "element": "Yin", "min_realm_index": 1}},
    "techniques": {"blood_palm": {"name": "Blood Sea Palm", "manual": "blood_sea", "min_mastery": 0, "qi_cost": 3, "karma_cost": 2, "exposure": 4}}
  }
}`

func setup(t *testing.T, world string) (dbPath, worldPath string) {
	t.Helper()
	dir := t.TempDir()
	dbPath = filepath.Join(dir, "content.sqlite3")
	worldPath = filepath.Join(dir, "world.json")
	if err := os.WriteFile(worldPath, []byte(world), 0o644); err != nil {
		t.Fatal(err)
	}
	conn, err := storage.Open(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(fixtureDDL()); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return dbPath, worldPath
}

func scalar(t *testing.T, dbPath, sql string, args ...any) any {
	t.Helper()
	conn, err := storage.Open(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatalf("%s: %v", sql, err)
	}
	if len(res.Rows) == 0 {
		return nil
	}
	return res.Rows[0][0]
}

func TestApplyFillsEveryTableFromTheFile(t *testing.T) {
	dbPath, worldPath := setup(t, smallWorld)
	result, err := ApplyPath(dbPath, worldPath, 100)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Applied || result.Skipped {
		t.Fatalf("first apply must write: %+v", result)
	}
	want := map[string]int64{"content_npcs": 2, "content_locations": 2, "content_items": 1, "content_recipes": 1, "content_sects": 1, "content_shops": 1, "content_merchants": 1, "content_manuals": 1, "content_techniques": 1}
	for table, n := range want {
		if got := storage.ParseInt(scalar(t, dbPath, `SELECT COUNT(*) FROM `+table)); got != n {
			t.Fatalf("%s: %d rows, want %d", table, got, n)
		}
		if result.Counts[table] != n {
			t.Fatalf("result.Counts[%s]=%d want %d", table, result.Counts[table], n)
		}
	}
	// The projection, one of each kind: text, nested text, integer-from-bool,
	// flag-from-list, and NULL for a key the entry does not carry.
	if got := scalar(t, dbPath, `SELECT location FROM content_npcs WHERE name='Boatman Lu'`); got != "Greenriver Town" {
		t.Fatalf("location=%v", got)
	}
	if got := scalar(t, dbPath, `SELECT recruitment_location FROM content_sects WHERE name='Azure Cloud Sect'`); got != "Cloudspine Foothills" {
		t.Fatalf("nested recruitment.location=%v", got)
	}
	if got := storage.ParseInt(scalar(t, dbPath, `SELECT safe_zone FROM content_locations WHERE name='Greenriver Town'`)); got != 1 {
		t.Fatalf("safe_zone true -> %d", got)
	}
	if got := storage.ParseInt(scalar(t, dbPath, `SELECT circuit FROM content_npcs WHERE name='Elder Su Yan'`)); got != 1 {
		t.Fatalf("circuit flag=%d", got)
	}
	if got := scalar(t, dbPath, `SELECT district FROM content_npcs WHERE name='Elder Su Yan'`); got != nil {
		t.Fatalf("an absent key must be NULL, got %v", got)
	}
	// The blob is the file's own bytes for that entry, not a re-encoding.
	var top map[string]json.RawMessage
	_ = json.Unmarshal([]byte(smallWorld), &top)
	var npcs map[string]json.RawMessage
	_ = json.Unmarshal(top["npcs"], &npcs)
	if got := scalar(t, dbPath, `SELECT data_json FROM content_npcs WHERE name='Elder Su Yan'`); got != string(npcs["Elder Su Yan"]) {
		t.Fatalf("data_json is not the raw entry:\n got %s\nwant %s", got, npcs["Elder Su Yan"])
	}
	if h := scalar(t, dbPath, `SELECT value_json FROM world_state WHERE key=?`, VersionKey); h == nil || !strings.Contains(h.(string), result.Hash) {
		t.Fatalf("version marker not written: %v", h)
	}
}

func TestAnUnchangedFileIsNotRewritten(t *testing.T) {
	dbPath, worldPath := setup(t, smallWorld)
	if _, err := ApplyPath(dbPath, worldPath, 100); err != nil {
		t.Fatal(err)
	}
	second, err := ApplyPath(dbPath, worldPath, 200)
	if err != nil {
		t.Fatal(err)
	}
	if second.Applied || second.Skipped {
		t.Fatalf("same bytes must be a no-op: %+v", second)
	}
	if got := scalar(t, dbPath, `SELECT updated_at FROM content_npcs WHERE name='Boatman Lu'`); storage.ParseInt(got) != 100 {
		t.Fatalf("rows were rewritten on an unchanged file: updated_at=%v", got)
	}
	if second.Counts["content_npcs"] != 2 {
		t.Fatalf("a no-op still reports the counts: %+v", second.Counts)
	}
}

func TestAnEditReplacesAndARemovalDeletes(t *testing.T) {
	dbPath, worldPath := setup(t, smallWorld)
	if _, err := ApplyPath(dbPath, worldPath, 100); err != nil {
		t.Fatal(err)
	}
	edited := strings.Replace(smallWorld, `"role": "boatman"`, `"role": "ferryman"`, 1)
	edited = strings.Replace(edited, `"Elder Su Yan": {"role": "elder", "realm": "Foundation Establishment", "location": "Cloudspine Foothills", "personality": "stern", "circuit": ["A", "B"]},`, ``, 1)
	if err := os.WriteFile(worldPath, []byte(edited), 0o644); err != nil {
		t.Fatal(err)
	}
	result, err := ApplyPath(dbPath, worldPath, 200)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Applied {
		t.Fatalf("a changed file must be applied: %+v", result)
	}
	if got := scalar(t, dbPath, `SELECT role FROM content_npcs WHERE name='Boatman Lu'`); got != "ferryman" {
		t.Fatalf("edit not applied: role=%v", got)
	}
	// This is the behaviour catalog_* never had: the Python sync only ever
	// upserted, so a renamed or removed entry lived on forever.
	if got := storage.ParseInt(scalar(t, dbPath, `SELECT COUNT(*) FROM content_npcs WHERE name='Elder Su Yan'`)); got != 0 {
		t.Fatalf("a removed entry survived the apply")
	}
}

func TestMissingTablesSkipRatherThanFail(t *testing.T) {
	dir := t.TempDir()
	dbPath := filepath.Join(dir, "bare.sqlite3")
	worldPath := filepath.Join(dir, "world.json")
	if err := os.WriteFile(worldPath, []byte(smallWorld), 0o644); err != nil {
		t.Fatal(err)
	}
	conn, err := storage.Open(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	conn.Close()
	result, err := ApplyPath(dbPath, worldPath, 100)
	if err != nil {
		t.Fatalf("a database the migration has not reached must not be an error: %v", err)
	}
	if !result.Skipped || result.Applied {
		t.Fatalf("want Skipped, got %+v", result)
	}
}

func TestABrokenFileWritesNothing(t *testing.T) {
	dbPath, worldPath := setup(t, smallWorld)
	if _, err := ApplyPath(dbPath, worldPath, 100); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(worldPath, []byte(`{"npcs": "not an object"`), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := ApplyPath(dbPath, worldPath, 200); err == nil {
		t.Fatal("a file that does not parse must be an error")
	}
	if got := storage.ParseInt(scalar(t, dbPath, `SELECT COUNT(*) FROM content_npcs`)); got != 2 {
		t.Fatalf("a failed apply must leave the previous content in place, got %d rows", got)
	}
}

// The parity test, against the shipped content, is the guard the plan asked
// for on its struct-widening sweep - stated here on the projection instead.
// For every projected column, the number of non-NULL cells must equal the
// number of entries in the file that carry the key. A misspelled key, a
// wrong path or a wrong kind shows up as a count that does not match, on the
// real 2.5 MB rather than on a fixture that happens to agree with the code.
func TestProjectionMatchesTheRawEntries(t *testing.T) {
	real := filepath.Join("..", "..", "..", "content", "world.json")
	if _, err := os.Stat(real); err != nil {
		t.Skip("shipped content not beside the package")
	}
	data, err := os.ReadFile(real)
	if err != nil {
		t.Fatal(err)
	}
	dbPath, worldPath := setup(t, string(data))
	result, err := ApplyPath(dbPath, worldPath, 100)
	if err != nil {
		t.Fatal(err)
	}
	snap, err := Load(worldPath)
	if err != nil {
		t.Fatal(err)
	}
	for _, section := range Sections {
		entries := snap.Entries[section.Table]
		if int64(len(entries)) != result.Counts[section.Table] || len(entries) == 0 {
			t.Fatalf("%s: %d entries in the file, %d rows written", section.Table, len(entries), result.Counts[section.Table])
		}
		for _, column := range section.Columns {
			carrying := 0
			for _, raw := range entries {
				var entry map[string]any
				if err := json.Unmarshal(raw, &entry); err != nil {
					t.Fatal(err)
				}
				if v, ok := lookup(entry, column.Key); ok && v != nil {
					carrying++
				}
			}
			got := storage.ParseInt(scalar(t, dbPath, `SELECT COUNT(*) FROM `+section.Table+` WHERE `+column.Name+` IS NOT NULL`))
			if got != int64(carrying) {
				t.Errorf("%s.%s: %d non-NULL cells but %d entries carry %q", section.Table, column.Name, got, carrying, column.Key)
			}
		}
	}
}

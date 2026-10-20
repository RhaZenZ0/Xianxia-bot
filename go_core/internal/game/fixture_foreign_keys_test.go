package game

import (
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"testing"
)

// The shared fixture is the schema most of this package's tests run on, and for
// seven tables it declared the columns without the keys production puts on
// them: inventory, currency_wallets, profession_progress, character_recipes,
// cave_abodes, character_birth_family and item_provenance. Every connection
// opens with foreign_keys=ON, so production refuses a row for a character who
// does not exist and the fixture took it - two tests passed on an orphan.
//
// This holds the fixture to production's DDL: every REFERENCES clause
// app/database/core.py writes for these tables is in the fixture, column,
// parent and action. The next table a fixture copies, or the next key
// production adds, cannot drift the same way.

var fixtureKeyedTables = []string{
	"inventory", "currency_wallets", "profession_progress", "character_recipes",
	"cave_abodes", "cave_abode_access", "character_birth_family", "item_provenance",
	// The batch-5 fixture's keyed tables (v1.35.0). event_log and
	// exploration_events carry no key in production, so they are not here.
	"equipment_instances", "character_item_appraisals", "character_location_discoveries",
	"event_claims", "exploration_event_participants", "exploration_event_actions",
	"spirit_beasts", "wild_beast_encounters", "secret_realm_runs", "inheritances",
}

var (
	tableKeyPattern  = regexp.MustCompile(`(?is)FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)\s*\(([^)]*)\)(\s+ON\s+DELETE\s+(?:CASCADE|SET\s+NULL|RESTRICT|NO\s+ACTION))?`)
	columnKeyPattern = regexp.MustCompile(`(?im)^\s*(\w+)\s+[^,\n]*?REFERENCES\s+(\w+)\s*\((\w+)\)(\s+ON\s+DELETE\s+(?:CASCADE|SET\s+NULL|RESTRICT|NO\s+ACTION))?`)
)

// tableBody returns what sits between the parentheses of one table's
// CREATE TABLE, matching nested parentheses; "" when the table is not there.
func tableBody(ddl, table string) string {
	header := regexp.MustCompile(`(?i)CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?` + table + `\s*\(`)
	loc := header.FindStringIndex(ddl)
	if loc == nil {
		return ""
	}
	depth := 1
	for i := loc[1]; i < len(ddl); i++ {
		switch ddl[i] {
		case '(':
			depth++
		case ')':
			depth--
			if depth == 0 {
				return ddl[loc[1]:i]
			}
		}
	}
	return ""
}

func normalizeKeyPart(s string) string {
	return strings.ToLower(strings.Join(strings.Fields(s), " "))
}

// referencesOf lists a table's keys as "column -> parent(column) action".
func referencesOf(ddl, table string) []string {
	body := tableBody(ddl, table)
	var out []string
	for _, m := range tableKeyPattern.FindAllStringSubmatch(body, -1) {
		out = append(out, normalizeKeyPart(m[1])+" -> "+normalizeKeyPart(m[2])+"("+normalizeKeyPart(m[3])+") "+normalizeKeyPart(m[4]))
	}
	// A column-level key is read once the table-level ones are taken out, or
	// "FOREIGN KEY(...) REFERENCES" would read as a column named FOREIGN.
	for _, m := range columnKeyPattern.FindAllStringSubmatch(tableKeyPattern.ReplaceAllString(body, ""), -1) {
		out = append(out, normalizeKeyPart(m[1])+" -> "+normalizeKeyPart(m[2])+"("+normalizeKeyPart(m[3])+") "+normalizeKeyPart(m[4]))
	}
	sort.Strings(out)
	return out
}

func TestTheFixtureKeepsProductionsForeignKeys(t *testing.T) {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot resolve test source path")
	}
	raw, err := os.ReadFile(filepath.Clean(filepath.Join(filepath.Dir(file), "../../../app/database/core.py")))
	if err != nil {
		t.Fatalf("production DDL: %v", err)
	}
	production := string(raw)
	total := 0
	for _, table := range fixtureKeyedTables {
		want := referencesOf(production, table)
		// A reader that finds nothing makes every comparison after it vacuous.
		if len(want) == 0 {
			t.Fatalf("%s: no REFERENCES read out of app/database/core.py; the reader is broken or the table is no longer keyed", table)
		}
		total += len(want)
		have := map[string]bool{}
		// Batch 5 is batch 4 plus its own tables, so both constants are read.
		for _, key := range referencesOf(batch4AuthoritySchema+"\n"+batch5AuthoritySchema, table) {
			have[key] = true
		}
		for _, key := range want {
			if !have[key] {
				t.Errorf("%s: production keys %q and the fixture does not - a test can insert a row production refuses; copy the REFERENCES clause from app/database/core.py and seed the parent rows the tests then miss", table, key)
			}
		}
	}
	if total < 9 {
		t.Fatalf("only %d production keys read for %d tables; the reader has stopped seeing them", total, len(fixtureKeyedTables))
	}
}

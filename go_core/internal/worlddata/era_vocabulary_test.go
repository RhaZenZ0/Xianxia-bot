package worlddata

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// Every era modifier reaches a rule (v1.0.7).
//
// This is `modifier_vocabulary_test.go` (rc.58) one roster over, and it exists
// because the same fault was waiting here. The old four-era cycle authored
// **eight** distinct modifier keys and production Go fetched **four**:
// `secret_realm_frequency`, `market_volatility`, `beast_encounter_rate` and
// `recovery_rate` each occurred exactly once in all of `go_core` - their own
// declaration in the `eraCycle` literal. So every era carried one live
// modifier and one dead one, and the Beast Tide Era, whose whole identity is
// beasts, did nothing whatever to beasts: mechanically it was "caravans are
// fifteen percent riskier".
//
// v1.0.7 authors twenty-four eras. Doing that on a vocabulary half of which
// reached nothing would have been manufacturing decoration at scale, which is
// the one thing this repository spends its length refusing - so the wiring came
// first and this gate is what keeps it true.
//
// **Fetched, not named** - the rc.58 distinction, and it matters here for the
// same reason: a key that appears only as a map literal key in the content
// file, or only inside a comment, is not a rule reading it. A read is the
// string in an argument position of one of the era readers, which a
// declaration cannot satisfy.

// eraModifierReaders are the functions through which a rule asks for an era
// term. A key is read when it is the `key` argument of one of these, or is
// indexed out of the map the sweep-wide readers return.
var eraModifierReaders = []string{"EraModifier", "currentEraModifierGo", "eraTermFor", "eraTerm"}

// unreadEraModifiers is not empty, and each entry says what it is waiting on.
// Tightening a rule nothing held reveals the backlog its absence created -
// rc.58's `REFUSAL_ONLY_OPERATIONS` precedent. An entry here is a decision
// deferred with a reason in docs/TODO.md, never a shrug; authoring a key into
// content that is named here fails `TestNoEraAuthorsAModifierNothingReads`
// below, so the allowlist cannot be used to smuggle one in.
var unreadEraModifiers = map[string]string{
	"secret_realm_frequency": "would weight the secret-realm branch of eligibleUnexpectedEvents, whose " +
		"weights rc.53 deliberately balanced per realm; changing them from an era is a mechanic, " +
		"not a wiring, and would undo that balance silently. Not authored by any era in v1.0.7.",
	"market_volatility": "would move shop and auction prices, which no rule varies today at all - " +
		"a price is content times a fixed markup. That is a mechanic of its own. Not authored " +
		"by any era in v1.0.7.",
}

func repoRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("cannot find the tree: %v; the gate is broken, not the tree", err)
	}
	for i := 0; i < 6; i++ {
		if _, err := os.Stat(filepath.Join(dir, "content", "world.json")); err == nil {
			return dir
		}
		dir = filepath.Dir(dir)
	}
	t.Fatal("cannot find content/world.json from the test's working directory; the gate is broken, not the tree")
	return ""
}

// authoredEraModifiers reads the content file as raw JSON rather than through
// the parsed catalogue, so a key no Go struct field matches still counts -
// which is the whole point, since `EraTemplate.Modifiers` is a bare map
// precisely so nothing is silently dropped.
func authoredEraModifiers(t *testing.T, root string) map[string][]string {
	t.Helper()
	// The content file is in the repository and always present, so a read that
	// fails means this gate cannot do its job: fatal, never a skip (rc.58's own
	// drill found that fault in itself).
	raw, err := os.ReadFile(filepath.Join(root, "content", "world.json"))
	if err != nil {
		t.Fatalf("cannot read the content file: %v; the gate is broken, not the tree", err)
	}
	var doc struct {
		Cycles map[string][]struct {
			Name         string             `json:"name"`
			DurationDays int64              `json:"duration_days"`
			Modifiers    map[string]float64 `json:"modifiers"`
		} `json:"world_era_cycles"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("cannot parse the content file: %v; the gate is broken, not the tree", err)
	}
	if len(doc.Cycles) == 0 {
		t.Fatal("no world_era_cycles in the content file; the gate is broken, not the tree")
	}
	out := map[string][]string{}
	for world, cycle := range doc.Cycles {
		for _, era := range cycle {
			for key := range era.Modifiers {
				out[key] = append(out[key], world+"/"+era.Name)
			}
		}
	}
	return out
}

// fetchedEraModifiers walks production Go for the string literals handed to an
// era reader, by AST. A substring scan would count a key named in a comment or
// declared in a test fixture; an argument position cannot be either.
func fetchedEraModifiers(t *testing.T, root string) map[string]bool {
	t.Helper()
	readers := map[string]bool{}
	for _, name := range eraModifierReaders {
		readers[name] = true
	}
	found := map[string]bool{}
	fset := token.NewFileSet()
	err := filepath.Walk(filepath.Join(root, "go_core"), func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return err
		}
		file, perr := parser.ParseFile(fset, path, nil, 0)
		if perr != nil {
			return nil
		}
		ast.Inspect(file, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			name := ""
			switch fn := call.Fun.(type) {
			case *ast.Ident:
				name = fn.Name
			case *ast.SelectorExpr:
				name = fn.Sel.Name
			}
			if !readers[name] {
				return true
			}
			for _, arg := range call.Args {
				if lit, ok := arg.(*ast.BasicLit); ok && lit.Kind == token.STRING {
					if v, e := strconv.Unquote(lit.Value); e == nil {
						found[v] = true
					}
				}
			}
			return true
		})
		return nil
	})
	if err != nil {
		t.Fatalf("cannot walk production Go: %v; the gate is broken, not the tree", err)
	}
	return found
}

func TestEveryAuthoredEraModifierReachesARule(t *testing.T) {
	root := repoRoot(t)
	authored := authoredEraModifiers(t, root)
	fetched := fetchedEraModifiers(t, root)

	// A reader is asserted before it is trusted (rc.57): a sweep that found no
	// keys at all would make the assertion below vacuous rather than red.
	if !fetched["war_pressure"] {
		t.Fatal(`the production walk did not find "war_pressure" handed to any era reader; the sweep is broken, not the tree`)
	}

	unread := []string{}
	for key, wheres := range authored {
		if fetched[key] {
			continue
		}
		sort.Strings(wheres)
		unread = append(unread, key+" (authored by "+strings.Join(wheres, ", ")+")")
	}
	sort.Strings(unread)
	if len(unread) > 0 {
		t.Fatalf("%d era modifier(s) are authored and read by no rule:\n  %s\n"+
			"An era carrying a modifier nothing fetches is prose with a number in it - the Beast Tide "+
			"carried beast_encounter_rate for its whole life and did nothing to beasts.",
			len(unread), strings.Join(unread, "\n  "))
	}
}

func TestNoEraAuthorsAModifierNothingReads(t *testing.T) {
	// The allowlist may not be used to smuggle a dead key into content: it
	// names what is *deferred*, and a deferred key must not be authored.
	root := repoRoot(t)
	authored := authoredEraModifiers(t, root)
	for key, reason := range unreadEraModifiers {
		if wheres, ok := authored[key]; ok {
			sort.Strings(wheres)
			t.Fatalf("%q is deferred (%s) but is authored by %s; wire it or do not author it",
				key, reason, strings.Join(wheres, ", "))
		}
	}
}

func TestEveryWorldsCycleRunsExactlyOneWorldYear(t *testing.T) {
	// `minutesPerYear` is 12 months x 30 days, so a world year is 360 days.
	// Stated as an assertion rather than trusted to look right, because the
	// whole point of six-times-sixty is that it closes.
	const worldYearDays = 360
	root := repoRoot(t)
	raw, err := os.ReadFile(filepath.Join(root, "content", "world.json"))
	if err != nil {
		t.Fatalf("cannot read the content file: %v; the gate is broken, not the tree", err)
	}
	var doc struct {
		Cycles map[string][]struct {
			Name         string `json:"name"`
			DurationDays int64  `json:"duration_days"`
		} `json:"world_era_cycles"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("cannot parse the content file: %v; the gate is broken, not the tree", err)
	}
	if len(doc.Cycles) < 4 {
		t.Fatalf("only %d world cycles; every world needs its own age", len(doc.Cycles))
	}
	names := map[string]string{}
	for world, cycle := range doc.Cycles {
		total := int64(0)
		for _, era := range cycle {
			if era.DurationDays <= 0 {
				t.Fatalf("%s/%s lasts %d days; an era of no length would spin the catch-up loop",
					world, era.Name, era.DurationDays)
			}
			total += era.DurationDays
			// A name is how `advanceWorldEra` finds a world's place in its own
			// cycle, so two worlds sharing one would put a world in the other's
			// age on the next tick.
			if prev, clash := names[era.Name]; clash {
				t.Fatalf("%q is an era in both %s and %s; the cycle position is found by name", era.Name, prev, world)
			}
			names[era.Name] = world
		}
		if total != worldYearDays {
			t.Fatalf("%s runs %d world days, not one world year (%d)", world, total, worldYearDays)
		}
	}
}

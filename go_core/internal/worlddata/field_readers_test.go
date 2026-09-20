package worlddata

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

// The fields nothing reads (v1.0.1).
//
// v1.0.0-rc.55 found `RootGrade.CultivationMult` and `RootGrade.BreakthroughBonus`
// parsed out of the content file and read by nothing: the grade decided
// everything about how a cultivator was made and nothing about what they were.
// It was found by hand. rc.58 then built exactly this gate one level down, for
// modifier *stats* (`modifier_vocabulary_test.go`, which requires each to be
// **fetched** by a rule rather than merely named) — and nobody built it for the
// fields themselves, so the class went on producing findings by inspection.
//
// This is that gate. Every field `worlddata` parses out of `content/world.json`
// must be read somewhere in production Go, or be named below with the reason
// it is not.
//
// **It is a floor, not a proof, and the reason is worth knowing before
// trusting it.** A read is an `*ast.SelectorExpr` whose selector is the field's
// Go name, which is what tells a field apart from the identically-spelled
// string in a SQL fragment — rc.58's lesson, and rc.52's "by AST, not by
// substring" one level down. What it cannot do without full type resolution is
// tell `PhysiqueDefinition.Name` from the forty other structs with a `Name`,
// so a field sharing its name with one anything reads passes unexamined. That
// makes the gate honest in one direction only: it never calls a read field
// unread, and it catches the uniquely-named orphan, which is the shape all of
// rc.55's and v1.0.1's findings had. Going further means `go/types`, which is
// a great deal of machinery for the fields this cannot already see.
//
// A composite-literal key is deliberately *not* a read. `Path{Skill: "Sword"}`
// writes the field; the question this gate asks is whether any rule ever reads
// it back, which is the whole distinction rc.55 turned on.

// fieldsReadByPresentation is a field the Go rules never touch and Python
// prints, with the file that prints it. This is a real category rather than a
// backlog: Python reads `content/world.json` directly through `WORLD`, so the
// content is live even where the Go struct field parsed from it is not. Worth
// knowing that each entry here is a Go field that could be deleted without
// changing any behaviour — the content stays, and Python keeps reading it.
var fieldsReadByPresentation = map[string]string{
	"PhysiqueDefinition.Advantage": "app/bot/commands/aptitude.py prints it on /aptitude physique",
	"PhysiqueDefinition.Drawback":  "app/bot/commands/aptitude.py prints it on /aptitude physique",
	"EventSiteTemplate.Objective":  "app/rules/game.py reads it off the template",
	"AuctionHouse.ChannelName":     "app/bot/channels.py names an auction channel from it",
}

// unreadContentFields is a field no rule and no surface reads, with the reason
// it is allowed none for now and where the decision is recorded. It is **not**
// empty on the day it was written, and rc.58's `REFUSAL_ONLY_OPERATIONS` is the
// precedent: tightening a rule nothing held reveals the backlog that the
// absence of the rule created. Each entry is a decision waiting in
// `docs/TODO.md`, not a shrug.
var unreadContentFields = map[string]string{
	"Path.Skill": "seven paths name a skill (Sword, Spiritual Arts, ...) and each string occurs " +
		"exactly once in the whole content file - its own declaration. What a path's skill is " +
		"(a line on a card, a bonus, or a field to delete) is content design; punch list, v1.0.1",
	"AuctionHouse.ProtectedInterior": "set true on all 48 houses. The door half of the fiction works " +
		"(auction_door_risks, consumed by auction.leave, standing a hunter outside); nothing can " +
		"attack a player who has not consented, so this may guard a mechanic the game lacks; " +
		"punch list, v1.0.1",
	"AuctionHouse.DoorRule": "set true on all 48 houses, so reading it would change nothing until " +
		"one says false; wire it or retire it; punch list, v1.0.1",
}

// parsedContentFields is every `worlddata` struct field carrying a json tag,
// keyed `Struct.Field`.
func parsedContentFields(t *testing.T) map[string]string {
	t.Helper()
	// Each file is parsed on its own rather than through `parser.ParseDir`,
	// which is deprecated as of Go 1.25 and which staticcheck refuses
	// (SA1019). It also matches `selectorsReadInProduction` below, so both
	// halves of this gate read the tree the same way.
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatalf("reading worlddata: %v", err)
	}
	out := map[string]string{}
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		fset := token.NewFileSet()
		file, perr := parser.ParseFile(fset, name, nil, 0)
		if perr != nil {
			t.Fatalf("parsing %s: %v", name, perr)
		}
		ast.Inspect(file, func(n ast.Node) bool {
			spec, ok := n.(*ast.TypeSpec)
			if !ok {
				return true
			}
			st, ok := spec.Type.(*ast.StructType)
			if !ok {
				return true
			}
			for _, f := range st.Fields.List {
				if f.Tag == nil {
					continue
				}
				tag := reflect.StructTag(strings.Trim(f.Tag.Value, "`"))
				if jsonName, ok := tag.Lookup("json"); !ok || jsonName == "-" || jsonName == "" {
					continue
				}
				for _, ident := range f.Names {
					if ident.IsExported() {
						out[spec.Name.Name+"."+ident.Name] = name
					}
				}
			}
			return true
		})
	}
	if len(out) < 300 {
		// A reader that silently finds nothing makes every assertion after it
		// vacuous (v1.0.0-rc.57). The catalogue is large; if this is small the
		// parse failed rather than the tree shrank.
		t.Fatalf("the worlddata sweep found only %d parsed fields", len(out))
	}
	return out
}

// selectorsReadInProduction is every field name production Go reads off
// something, as `x.Field`.
func selectorsReadInProduction(t *testing.T) map[string]bool {
	t.Helper()
	read := map[string]bool{}
	files := 0
	err := filepath.Walk("../..", func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		fset := token.NewFileSet()
		file, perr := parser.ParseFile(fset, path, nil, 0)
		if perr != nil {
			return perr
		}
		files++
		ast.Inspect(file, func(n ast.Node) bool {
			if sel, ok := n.(*ast.SelectorExpr); ok {
				read[sel.Sel.Name] = true
			}
			return true
		})
		return nil
	})
	if err != nil {
		t.Fatalf("walking production Go: %v", err)
	}
	if files < 50 {
		t.Fatalf("the production sweep found %d Go files", files)
	}
	return read
}

func TestEveryParsedContentFieldHasAReader(t *testing.T) {
	fields := parsedContentFields(t)
	read := selectorsReadInProduction(t)

	// Assert the reader before trusting it (v1.0.0-rc.57): a field the rules
	// demonstrably read must come back read, or the walk is finding nothing and
	// every "unread" below is noise.
	for _, known := range []string{"Grade", "Elements", "Location", "Severity"} {
		if !read[known] {
			t.Fatalf("the production walk did not find %q read anywhere; the sweep is broken, not the tree", known)
		}
	}

	var orphans []string
	for name := range fields {
		field := name[strings.IndexByte(name, '.')+1:]
		if read[field] {
			continue
		}
		if _, ok := fieldsReadByPresentation[name]; ok {
			continue
		}
		if _, ok := unreadContentFields[name]; ok {
			continue
		}
		orphans = append(orphans, name+"  ("+fields[name]+")")
	}
	sort.Strings(orphans)
	if len(orphans) > 0 {
		t.Fatalf("%d content field(s) are parsed and read by no rule:\n  %s\n\nWire one, or name it in "+
			"fieldsReadByPresentation (with the file that prints it) or unreadContentFields (with the "+
			"decision it waits on).", len(orphans), strings.Join(orphans, "\n  "))
	}
}

// An allowlist can only shrink honestly (v1.0.0-rc.35): an entry naming a field
// that is read, or one the catalogue no longer parses, is stale and fails here.
func TestTheFieldAllowlistsAreStillTrue(t *testing.T) {
	fields := parsedContentFields(t)
	read := selectorsReadInProduction(t)
	for _, list := range []map[string]string{fieldsReadByPresentation, unreadContentFields} {
		for name, reason := range list {
			if strings.TrimSpace(reason) == "" {
				t.Errorf("%s is allowed with no reason", name)
			}
			if _, ok := fields[name]; !ok {
				t.Errorf("%s is allowed but worlddata no longer parses it", name)
				continue
			}
			field := name[strings.IndexByte(name, '.')+1:]
			if read[field] {
				t.Errorf("%s is allowed as unread but production Go reads .%s; drop the entry", name, field)
			}
		}
	}
}

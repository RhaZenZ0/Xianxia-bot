package game

import (
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/worlddata"
)

// Every cultivation path the world offers can be practised (v1.0.3).
//
// `content/world.json` offers seven paths and `app/rules/advanced_catalog.py`
// named six, so **Ghost Cultivator had none of the 160 manuals** - while
// `death_qi_system`, a whole authored subsystem in Go and content alike, opens
// with `"path": "Ghost Cultivator"` and exists to serve it.
//
// This drives the shipped catalogue rather than a fixture, because the whole
// fault was content: a fixture that invented a ghost manual would prove
// nothing, which is the `lawTechniqueCatalog` lesson from rc.58.

func shippedTechniqueCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load(filepath.Join("..", "..", "..", "content", "world.json"))
	if err != nil {
		// The content file is in the repository and always present, so a read
		// that fails means the gate cannot do its job (rc.58's own drill).
		t.Fatalf("the shipped catalogue could not be read, so this gate proves nothing: %v", err)
	}
	if len(catalog.Paths) == 0 || len(catalog.TechniqueSystem.Manuals) == 0 {
		t.Fatalf("the shipped catalogue parsed to %d paths and %d manuals; the reader is broken, not the tree",
			len(catalog.Paths), len(catalog.TechniqueSystem.Manuals))
	}
	return catalog
}

// TestEveryPathHasAMethod. Drill: drop `_extend_uncovered_paths` from the
// generator, re-materialise, and this names Ghost Cultivator.
func TestEveryPathHasAMethod(t *testing.T) {
	catalog := shippedTechniqueCatalog(t)
	unwalkable := []string{}
	for path := range catalog.Paths {
		found := false
		for _, m := range catalog.TechniqueSystem.Manuals {
			if strings.EqualFold(strings.TrimSpace(m.Path), path) {
				found = true
				break
			}
		}
		if !found {
			unwalkable = append(unwalkable, path)
		}
	}
	if len(unwalkable) > 0 {
		t.Fatalf("%v is offered at character creation and named by no manual in the catalogue, "+
			"so nothing a player can reach teaches it", unwalkable)
	}
}

// TestTheHiddenSectCanServeEveryPath - somewhere on the ladder, which is the
// real invariant and not the one this test first claimed.
//
// v1.0.3's first version asserted every path was served at **realm 0**, and to
// make that true it gave `shadowInitiationManual` a fallback to another path's
// codex. CI caught it: `TestTheManualIsChosenByAlignmentPathAndReach` has said
// in as many words since it was written that "a path with no demonic manual
// gets nothing rather than someone else's", so the fallback overruled a
// documented decision to satisfy a claim invented one file away. The rule
// stands and the fallback is gone; what was actually wrong was that an empty
// hand said nothing, which `manual_absent` now fixes.
//
// The Ghost Cultivator's bug was never the realm-0 gap the other five paths
// have by design. It was having **no demonic manual at any realm at all**, so
// the cell could never serve that path however far its initiate cultivated.
// That is what this holds.
//
// Drill: drop `_extend_uncovered_paths` from the generator, re-materialise, and
// this names Ghost Cultivator.
func TestTheHiddenSectCanServeEveryPath(t *testing.T) {
	catalog := shippedTechniqueCatalog(t)
	topOfTheLadder := int64(len(catalog.Realms))
	never := []string{}
	for path := range catalog.Paths {
		if _, _, ok := shadowInitiationManual(catalog, path, topOfTheLadder); !ok {
			never = append(never, path)
		}
	}
	if len(never) > 0 {
		t.Fatalf("the hidden sect keeps no forbidden art of %v at any realm, so it can never serve "+
			"an initiate of those paths however far they cultivate", never)
	}
}

// TestTheCellStillRefusesSomeoneElsesArt guards the decision the reverted
// fallback would have erased, from this side too: a path served at the top of
// the ladder must be served *its own* art, never the nearest demonic one.
func TestTheCellStillRefusesSomeoneElsesArt(t *testing.T) {
	catalog := shippedTechniqueCatalog(t)
	for path := range catalog.Paths {
		id, manual, ok := shadowInitiationManual(catalog, path, int64(len(catalog.Realms)))
		if !ok {
			t.Fatalf("%s is served nothing at the top of the ladder", path)
		}
		if !strings.EqualFold(strings.TrimSpace(manual.Path), path) {
			t.Fatalf("%s was handed %s, whose path is %q; the cell hands out this path's art or none",
				path, id, manual.Path)
		}
	}
}

// TestAPathMatchIsStillPreferred - the fallback must not have flattened the
// rule it was added under. Every path with a demonic manual of its own inside
// reach must still be served that one.
func TestAPathMatchIsStillPreferred(t *testing.T) {
	catalog := shippedTechniqueCatalog(t)
	checked := 0
	for path := range catalog.Paths {
		// The realm ceiling: at the top of the ladder every path has its own.
		id, manual, ok := shadowInitiationManual(catalog, path, 31)
		if !ok {
			t.Fatalf("%s is served nothing even at realm 31", path)
		}
		if !strings.EqualFold(strings.TrimSpace(manual.Path), path) {
			t.Fatalf("%s was served %s (path %q) while a manual of its own path was in reach; "+
				"the fallback is a second choice, not a replacement", path, id, manual.Path)
		}
		checked++
	}
	if checked != len(catalog.Paths) {
		t.Fatalf("checked %d of %d paths; the sweep is broken", checked, len(catalog.Paths))
	}
}

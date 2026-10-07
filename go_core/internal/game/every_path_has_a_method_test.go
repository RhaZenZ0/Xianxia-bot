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

// TestAPathMatchIsStillPreferred - the fallback must not have flattened the
// rule it was added under. Every path with a demonic manual of its own inside
// reach must still be served that one.
//
// It also holds what two tests beside it used to (TestTheHiddenSectCanServeEveryPath
// and TestTheCellStillRefusesSomeoneElsesArt, folded in here because each was this
// loop with fewer checks): at the top of the ladder the hidden sect serves *every*
// path - the Ghost Cultivator's v1.0.3 bug was having no demonic manual at any
// realm - and serves it its own art or none, never the nearest demonic one.
// Drill: drop `_extend_uncovered_paths` from the generator, re-materialise, and
// this names Ghost Cultivator.
func TestAPathMatchIsStillPreferred(t *testing.T) {
	catalog := shippedTechniqueCatalog(t)
	topOfTheLadder := int64(len(catalog.Realms))
	checked := 0
	for path := range catalog.Paths {
		// The realm ceiling: at the top of the ladder every path has its own.
		id, manual, ok := shadowInitiationManual(catalog, path, topOfTheLadder)
		if !ok {
			t.Fatalf("%s is served nothing even at the top of the ladder", path)
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

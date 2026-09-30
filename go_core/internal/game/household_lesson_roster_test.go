package game

import (
	"strings"
	"testing"
)

// Every household a send-off names has a lesson the action can give (v1.12.3).
// familyLessonActionGo looks the lesson up by the household's archetype and
// refuses "this household has no lesson to give" without one, and thirty-three
// of the forty-six send-offs - every Spiritual, Immortal and Celestial house a
// samsara rebirth can land in - had none, so those lives could never take the
// once-per-life lesson. This asks the shipped catalogue the questions the
// action asks before it rolls anything: is there a lesson, does its trade have
// an attribute to test, is its manual a real and non-forbidden one, and is its
// keepsake an item of this world.
func TestEveryHouseholdASendoffNamesHasALessonItCanGive(t *testing.T) {
	catalog := shippedCatalog(t)
	if len(catalog.BirthFamilySendoff) < 46 {
		t.Fatalf("the send-off roster did not parse (%d); the reader is broken, not the tree", len(catalog.BirthFamilySendoff))
	}
	var problems []string
	for archetype := range catalog.BirthFamilySendoff {
		lesson, ok := catalog.BirthFamilyLessons[archetype]
		if !ok {
			problems = append(problems, archetype+": no lesson")
			continue
		}
		if _, ok := tradeAttribute[householdTradeFor(catalog, archetype)]; !ok {
			problems = append(problems, archetype+": its trade has no attribute to test")
		}
		manual, ok := catalog.TechniqueSystem.Manuals[lesson.Manual]
		if !ok {
			problems = append(problems, archetype+": manual "+lesson.Manual+" is not in this world")
		} else if manualForbidden(manual) {
			problems = append(problems, archetype+": manual "+lesson.Manual+" is forbidden")
		}
		if _, _, known := itemDef(catalog, lesson.Keepsake); !known {
			problems = append(problems, archetype+": keepsake "+lesson.Keepsake+" is not in this world")
		}
	}
	if len(problems) > 0 {
		t.Fatalf("%d household(s) cannot be given their lesson: %s", len(problems), strings.Join(problems, "; "))
	}
}

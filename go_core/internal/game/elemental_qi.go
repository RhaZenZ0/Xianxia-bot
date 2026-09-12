package game

import (
	"strings"

	"xianxia/core/internal/worlddata"
)

// Elemental qi (v1.0.0-rc.9).
//
// Qi is not one substance. Every cultivation method draws one kind of it - the
// manual's element - and a cultivator's spiritual root decides how much of
// that kind their body can actually take in. The five phases generate and
// overcome one another in the old cycle: a root that stands with the method's
// phase resonates with it, a root the cycle feeds or is fed by does well
// enough, a root that overcomes the method's qi forces it in and loses some of
// it, and a root the method's qi overcomes is fighting what it swallows - the
// dangerous case, which is the one that can turn.
//
// Void and Chaos stand with no phase: nothing in the cycle helps or hinders
// them, and they help and hinder nothing.
//
// Every number here is content (`elemental_qi_system` in world.json).

const (
	relationResonant   = "resonant"
	relationGenerative = "generative"
	relationNeutral    = "neutral"
	relationDrained    = "drained"
	relationClashing   = "clashing"
)

// elementRelationRank orders the relations so "the best of a cultivator's
// several roots" is a defined thing.
var elementRelationRank = map[string]int{
	relationClashing: 0, relationDrained: 1, relationNeutral: 2,
	relationGenerative: 3, relationResonant: 4,
}

// manualElementOf is the kind of qi a method draws.
func manualElementOf(catalog worlddata.Catalog, manualID string) string {
	definition, ok := catalog.TechniqueSystem.Manuals[strings.TrimSpace(manualID)]
	if !ok {
		return ""
	}
	return strings.TrimSpace(definition.Element)
}

// elementPhase is the phase of the five an element stands with. A phase stands
// with itself; Void and Chaos stand with none, and neither does anything the
// content has not placed.
func elementPhase(catalog worlddata.Catalog, element string) string {
	element = strings.TrimSpace(element)
	if element == "" {
		return ""
	}
	for _, phase := range catalog.ElementalQi.Phases {
		if phase == element {
			return phase
		}
	}
	return strings.TrimSpace(catalog.ElementalQi.PhaseOf[element])
}

// elementRelation is what one root element makes of one method's element.
func elementRelation(catalog worlddata.Catalog, rootElement, manualElement string) string {
	rootPhase, manualPhase := elementPhase(catalog, rootElement), elementPhase(catalog, manualElement)
	if rootPhase == "" || manualPhase == "" {
		return relationNeutral
	}
	if rootPhase == manualPhase {
		return relationResonant
	}
	generates, overcomes := catalog.ElementalQi.Generates, catalog.ElementalQi.Overcomes
	if generates[rootPhase] == manualPhase || generates[manualPhase] == rootPhase {
		return relationGenerative
	}
	if overcomes[rootPhase] == manualPhase {
		return relationDrained
	}
	if overcomes[manualPhase] == rootPhase {
		return relationClashing
	}
	return relationNeutral
}

// bestElementRelation is what a cultivator with several root elements makes of
// a method: the kindest of their roots answers for all of them, which is what
// a multi-element root is for.
func bestElementRelation(catalog worlddata.Catalog, rootElements []string, manualElement string) string {
	if strings.TrimSpace(manualElement) == "" || len(rootElements) == 0 {
		return relationNeutral
	}
	best := ""
	for _, element := range rootElements {
		relation := elementRelation(catalog, element, manualElement)
		if best == "" || elementRelationRank[relation] > elementRelationRank[best] {
			best = relation
		}
	}
	if best == "" {
		return relationNeutral
	}
	return best
}

// elementRelationDefinition is the content's entry for a relation, with plain
// defaults so a catalogue that has not been given one still behaves.
func elementRelationDefinition(catalog worlddata.Catalog, key string) worlddata.ElementRelation {
	if definition, ok := catalog.ElementalQi.Relations[key]; ok {
		if definition.Mult <= 0 {
			definition.Mult = 1
		}
		if definition.Label == "" {
			definition.Label = key
		}
		return definition
	}
	return worlddata.ElementRelation{Mult: 1, Label: key}
}

// rootGradeRank is where a spiritual root's grade sits in the content's ladder.
func rootGradeRank(catalog worlddata.Catalog, grade string) int {
	for rank, definition := range catalog.SpiritualRootSystem.Grades {
		if definition.Name == grade {
			return rank
		}
	}
	return 0
}

// rootAbsorptionBonus is what the root itself is worth, whatever it is
// absorbing: a better grade and a purer root take in more of anything.
func rootAbsorptionBonus(catalog worlddata.Catalog, root SpiritualRootState) float64 {
	system := catalog.ElementalQi
	bonus := 1.0
	bonus += system.GradeBonusPerRank * float64(rootGradeRank(catalog, root.Grade))
	bonus += system.PurityBonusAtFull * float64(clampI64(int64(root.Purity), 0, 100)) / 100.0
	return bonus
}

// elementalAbsorption is the whole of it for one cultivator and one method:
// the relation their root has with its element, what that is worth, and what
// it risks.
type elementalAbsorption struct {
	Element   string
	Relation  string
	Label     string
	Note      string
	Mult      float64
	Surcharge int
	RootPhase string
}

func absorptionFor(catalog worlddata.Catalog, root SpiritualRootState, manualElement string) elementalAbsorption {
	manualElement = strings.TrimSpace(manualElement)
	relation := bestElementRelation(catalog, root.Elements, manualElement)
	definition := elementRelationDefinition(catalog, relation)
	out := elementalAbsorption{
		Element: manualElement, Relation: relation, Label: definition.Label, Note: definition.Note,
		Mult: 1, Surcharge: definition.DeviationSurchargePercent,
	}
	if manualElement == "" {
		// No method practised, or a method from before the elements: the
		// gathering is unchanged and nothing is risked.
		out.Relation, out.Label, out.Note, out.Surcharge = relationNeutral, elementRelationDefinition(catalog, relationNeutral).Label, "", 0
		return out
	}
	out.Mult = round4(definition.Mult * rootAbsorptionBonus(catalog, root))
	if len(root.Elements) > 0 {
		out.RootPhase = elementPhase(catalog, root.Elements[0])
	}
	return out
}

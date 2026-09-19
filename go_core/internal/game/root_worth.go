package game

import (
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// What a spiritual root is worth (v1.0.0-rc.55).
//
// `spiritual_root_system.grades` has carried a `cultivation_mult` (Mortal 0.88
// through Immortal 1.34) and a `breakthrough_bonus` (-1 through +3) since the
// ladder was written, and until rc.55 the engine read neither. Of RootGrade's
// eight fields the six that decide how a root is *made* - the roll band, the
// element chances, the mutation chance, the realm a grade may evolve at - were
// all read, and the only two that decide what having it is *worth* were not.
//
// That was not only a creation roll: `aptitude.evolve` lets a cultivator climb
// the ladder a rung at a time, paying stability for a failure and risking a
// forced mutation, and the whole payoff of that climb was these two numbers.
//
// The grade did reach cultivation by one flatter route until rc.55 - a per-rung
// term of 0.02 kept under `elemental_qi_system`, which was a second statement
// of the same rule in a system named for something else, and it was folded
// into the *element* multiplier, which the bot hides when the relation is
// indifferent.
// So the authored 1.52x spread was live as 1.10x, under another name, unseen.
// That term is gone; this is the one statement.

// rootGradeDefinition is the content's entry for a grade, by name, and whether
// the ladder carries it at all.
//
// The second return is the point. `gradeIndex` answers 0 for a name it does not
// know, so `gradeDef` hands an unknown grade the *first* rung - Mortal, 0.88
// and -1 - and `admin.player.set_spiritual_root` writes whatever string it is
// given with no check against the ladder. A grade the world does not carry
// must be worth nothing, not the worst thing on the ladder: a fallback that
// looks like a value is not a sentinel.
func rootGradeDefinition(catalog worlddata.Catalog, grade string) (worlddata.RootGrade, bool) {
	grade = strings.TrimSpace(grade)
	if grade == "" {
		return worlddata.RootGrade{}, false
	}
	for _, definition := range catalog.SpiritualRootSystem.Grades {
		if strings.EqualFold(definition.Name, grade) {
			return definition, true
		}
	}
	return worlddata.RootGrade{}, false
}

// rootWorthMultiplier is what the root itself is worth to a cultivation
// session, whatever is being gathered: the grade's own multiplier, deepened by
// purity. A grade off the ladder is worth 1.
func rootWorthMultiplier(catalog worlddata.Catalog, root SpiritualRootState) float64 {
	definition, known := rootGradeDefinition(catalog, root.Grade)
	if !known || definition.CultivationMult <= 0 {
		return 1
	}
	purity := float64(clampI64(int64(root.Purity), 0, 100)) / 100.0
	return round4(definition.CultivationMult * (1 + catalog.SpiritualRootSystem.PurityBonusAtFull*purity))
}

// rootGradeBreakthroughBonus is what the grade is worth to a breakthrough roll.
// It rides `mods` beside the root's own mutation, because
// `breakthroughModifier` reads no other channel - and it lands in
// `innate_breakthrough_bonus`, which the bot has printed as "Innate aptitude
// modifier" since the key existed.
func rootGradeBreakthroughBonus(catalog worlddata.Catalog, root SpiritualRootState) int {
	definition, known := rootGradeDefinition(catalog, root.Grade)
	if !known {
		return 0
	}
	return definition.BreakthroughBonus
}

// seclusionCarried is every multiplier that holds for a whole retreat, and the
// names behind them so the player can be told why the projection is what it is.
type seclusionCarried struct {
	Effect          float64
	Era             float64
	EraName         string
	Manual          float64
	ManualName      string
	ManualGrade     string
	Element         float64
	ElementName     string
	ElementRelation string
	Root            float64
	RootGrade       string
}

func (s seclusionCarried) product() float64 {
	return round4(s.Effect * s.Era * s.Manual * s.Element * s.Root)
}

// loadSeclusionCarried reads them, and never fails: seclusion.start and
// seclusion.settle have never touched the aptitude tables, and a retreat must
// not become refusable because a row is missing. Every term defaults to 1, the
// way soulCultivationMultGo already answers 1 for a soul nobody has recorded.
func loadSeclusionCarried(conn *storage.Conn, catalog worlddata.Catalog, userID, gameMinute int64, mode string) seclusionCarried {
	out := seclusionCarried{Effect: 1, Era: 1, Manual: 1, Element: 1, Root: 1}
	// loadAptitudes errors outright on a character with no spiritual-root row,
	// which is why it is tolerated here rather than propagated.
	bundle, rootErr := loadAptitudes(conn, userID)
	if rootErr == nil {
		out.Root, out.RootGrade = rootWorthMultiplier(catalog, bundle.Root), bundle.Root.Grade
		stat := "cultivation_gain"
		if mode == "body" {
			stat = "body_cultivation_gain"
		}
		if mods, err := loadEffectModifiers(conn, userID, gameMinute, bundle, mechanicsCharacter{}, catalog); err == nil {
			out.Effect = mulOrOne(mods, stat)
		}
	}
	if name, mult, err := eraCultivationMultiplier(conn); err == nil && mult > 0 {
		out.Era, out.EraName = mult, name
	}
	manualName, manualGrade, manualElement, manualMult, _, err := manualCultivationMultiplier(conn, catalog, userID)
	if err == nil {
		if manualMult > 0 {
			out.Manual = manualMult
		}
		out.ManualName, out.ManualGrade = manualName, manualGrade
		// The body path tempers flesh and answers to no element, exactly as a
		// hand-sat body session does.
		if mode != "body" && rootErr == nil {
			absorption := absorptionFor(catalog, bundle.Root, manualElement)
			out.Element, out.ElementName, out.ElementRelation = absorption.Mult, absorption.Element, absorption.Relation
		}
	}
	return out
}

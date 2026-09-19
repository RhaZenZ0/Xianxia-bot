package simulation

// How far one of the world's own people can ever go (v1.0.0-rc.44).
//
// `npcBreakthroughs` asked two questions of a candidate - had they the wealth,
// had they the health - and nothing else. Both are things a porter can have, so
// the porter climbed: every one of the five hundred and seventy-four people in
// the world was on the same ladder as the sect elders, and the only thing at
// the top of it was `realm >= 31`. A world where the innkeeper eventually
// reaches Nascent Soul is not a xianxia world, it is a queue.
//
// Talent is the thing that was missing, and it is the thing the genre is about.
// Most people cannot cultivate at all. Of those who can, most stop early - a
// few realms and then a lifetime at that stage, which is what makes the ones
// who do not stop worth writing about.
//
// It is derived rather than stored, off `hash64(name)`, which is the same seed
// `bootstrapNPCs` already draws wealth, influence and ambition from and the
// same idiom `bootstrap_households.go` uses so that the same content makes the
// same world twice. Nothing is written, no column is added, and asking twice
// gives the same answer forever.
//
// The origin is the catalogue's own `realm` for that NPC, so a Foundation
// Establishment elder's ceiling is measured from where content put them rather
// than from where the tick has since carried them - without that the ceiling
// would move up every time somebody crossed it, which is not a ceiling. Someone
// the catalogue does not carry (a descendant who came of age, a registry NPC)
// begins at zero, which is right: they were born mortal like everybody else.

import (
	"strings"

	"xianxia/core/internal/worlddata"
)

// talentBands are the realms of headroom a life can have, and how likely each
// is out of a hundred. Written as code rather than content for the same reason
// `travelChanceFor` is: it is a rule about what a profession implies, not a
// roster somebody would edit.
type talentBands struct{ cutoffs [5]int64 }

var (
	// Somebody whose work is cultivation. Even here most stop: the sect is
	// full of Qi Refining disciples who will die Qi Refining disciples.
	talentCultivator = talentBands{[5]int64{10, 35, 65, 90, 100}}
	// Somebody whose work is not. A few of them turn out to have it anyway,
	// which is the whole plot of half the stories in the genre.
	talentMundane = talentBands{[5]int64{62, 90, 98, 100, 100}}
	// Everybody else: a guard who drills, a hunter who ranges, a wanderer.
	talentOrdinary = talentBands{[5]int64{40, 74, 92, 99, 100}}
)

// talentHeadroom is the realms each band grants, in the order the cutoffs are
// read. Zero is a life that goes no further than it began.
var talentHeadroom = [5]int64{0, 1, 3, 6, 12}

// npcTalentBand is how many realms past their beginning somebody can climb.
func npcTalentBand(name, profession string) int64 {
	bands := talentOrdinary
	p := strings.ToLower(profession)
	switch {
	case strings.Contains(p, "cultivator"), strings.Contains(p, "elder"),
		strings.Contains(p, "disciple"), strings.Contains(p, "sect"),
		strings.Contains(p, "master"), strings.Contains(p, "patriarch"),
		strings.Contains(p, "sovereign"), strings.Contains(p, "immortal"),
		strings.Contains(p, "alchemist"), strings.Contains(p, "formation"):
		bands = talentCultivator
	case strings.Contains(p, "merchant"), strings.Contains(p, "broker"),
		strings.Contains(p, "peddler"), strings.Contains(p, "innkeeper"),
		strings.Contains(p, "keeper"), strings.Contains(p, "porter"),
		strings.Contains(p, "clerk"), strings.Contains(p, "cook"),
		strings.Contains(p, "farmer"), strings.Contains(p, "servant"),
		strings.Contains(p, "steward"), strings.Contains(p, "auctioneer"):
		bands = talentMundane
	}
	// A distinct slice of the seed from the three `bootstrapNPCs` already
	// spends, so talent does not correlate with how rich somebody started.
	roll := int64((hash64(name, "talent") / 13) % 100)
	for i, cutoff := range bands.cutoffs {
		if roll < cutoff {
			return talentHeadroom[i]
		}
	}
	return talentHeadroom[len(talentHeadroom)-1]
}

// npcTalentCeiling is the highest realm this person will ever reach.
func (r *Runner) npcTalentCeiling(name, profession string) int64 {
	origin := int64(0)
	if def, ok := r.World.NPCs[name]; ok {
		origin = worldRealmIndexFromName(def.Realm, r.World.Realms)
	}
	if origin < 0 {
		origin = 0
	}
	return origin + npcTalentBand(name, profession)
}

// worldRealmIndexFromName is `realmIndexFromName` over the parsed catalogue's
// own realm type. The bootstrap reads `[]Realm` (this package's) and the tick
// holds `[]worlddata.Realm`, and the two are the same ladder; this is the ten
// lines that avoid converting a slice on every candidate, every tick.
func worldRealmIndexFromName(name string, realms []worlddata.Realm) int64 {
	normalized := strings.ToLower(strings.TrimSpace(name))
	if normalized == "" || normalized == "mortal" || normalized == "no detectable cultivation" || normalized == "suppressed aura" {
		return 0
	}
	for index, realm := range realms {
		if strings.ToLower(strings.TrimSpace(realm.Name)) == normalized {
			return int64(index)
		}
	}
	return 0
}

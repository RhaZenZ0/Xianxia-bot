package game

import (
	"encoding/json"
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Attributes grow every qi stage (v1.14.0).
//
// The owner's call: every qi stage adds +1 to all six attributes and +2 to the
// two a path grows (`pathGrowthAttributes`), and the rolls rise to meet it.
// The qi ladder is 32 realms of 9 stages, so that is close to +290 by the top,
// against 2d10 rolls whose TNs run 10 to 32 - every roll therefore meets a
// difficulty rising at the same +1 a stage, keyed on what it is against.
//
// Growth is computed from where a character stands, never stored: a GM's
// `set_realm`, a samsara and a reset are right by construction, and the path's
// lead can be capped without a second column. `attributes_json` holds the
// base - the path's starting spread, plus the body ladder's own +1 body a
// body realm - and schema 73 rewrote every stored value to that base.
//
// The arithmetic is carried as a difference. A roll against something at the
// cultivator's own stage adds `challengeDifficulty` to both sides - the
// attribute grew by it and the TN rises by it - so the two cancel and that
// site needs no term at all. What every reader gets is therefore the attribute
// **relative to the stage baseline**: the base, the `kept_per_realm` a realm
// the difficulty does not take back, and the path's lead capped at
// `path_edge_cap`. A roll against an opponent or a content floor adds
// `stageLead` - the cultivator's difficulty less the faced one - and that is
// the only place the growth shows up as a number. The same relative value is
// what every rate reads (cultivation quality, qi capacity, a war's power),
// because a rate fed +290 will would run fifteen times faster.
//
// The sheet shows the grown number (`characterSheetAttributes`), which is
// what a player sees climb.

var attributeNames = []string{"body", "agility", "spirit", "insight", "will", "presence"}

// qiStagesCrossed is how many qi stages lie behind realm `realm`, stage
// `phase`: 0 at realm 0 stage 1.
func qiStagesCrossed(catalog worlddata.Catalog, realm, phase int64) int64 {
	perRealm := catalog.AttributeGrowth.StagesPerRealm
	if perRealm <= 0 {
		perRealm = 9
	}
	return maxI64(0, realm*perRealm+phase-1)
}

// challengeDifficulty is what an attribute has grown by at a stage, and so
// what a roll against something at that stage must rise by. An absent content
// block answers 0, so a fixture with an empty catalogue sees no growth and no
// difficulty - the two always move together.
//
// It keeps pace with every stage except `kept_per_realm` a realm, and that
// remainder is the growth a cultivator actually feels against their own
// stage. Without it the growth would cancel exactly - and the content's TNs
// were authored to climb a point a realm (a breakthrough runs 10 to 32)
// against attributes that grew a point or two a realm before v1.14.0, so a
// cultivator who kept nothing would find breakthroughs at realm 12 a tenth as
// likely as they had been. The odds test measures it.
func challengeDifficulty(catalog worlddata.Catalog, realm, phase int64) int64 {
	g := catalog.AttributeGrowth
	return maxI64(0, maxI64(0, g.PerStage)*qiStagesCrossed(catalog, realm, phase)-maxI64(0, g.KeptPerRealm)*maxI64(0, realm))
}

// keptGrowth is the growth every attribute carries against its own stage:
// what challengeDifficulty does not take back.
func keptGrowth(catalog worlddata.Catalog, realm, phase int64) int64 {
	g := catalog.AttributeGrowth
	return maxI64(0, g.PerStage)*qiStagesCrossed(catalog, realm, phase) - challengeDifficulty(catalog, realm, phase)
}

// stageLead is how far a cultivator at (realm, phase) has outgrown something
// at (facedRealm, facedPhase): positive when the faced thing is below them.
// It is added to the cultivator's modifier on a roll against that thing.
func stageLead(catalog worlddata.Catalog, realm, phase, facedRealm, facedPhase int64) int64 {
	return challengeDifficulty(catalog, realm, phase) - challengeDifficulty(catalog, facedRealm, facedPhase)
}

// pathEdge is the path pair's lead over the baseline on a roll: the extra a
// path stage gives, capped.
func pathEdge(catalog worlddata.Catalog, realm, phase int64) int64 {
	g := catalog.AttributeGrowth
	extra := maxI64(0, g.PathPerStage-g.PerStage) * qiStagesCrossed(catalog, realm, phase)
	return minI64(extra, maxI64(0, g.PathEdgeCap))
}

// decodeStoredAttributes is the one decoder of `attributes_json`.
func decodeStoredAttributes(raw any) map[string]int64 {
	out := map[string]int64{}
	var parsed map[string]any
	if err := json.Unmarshal([]byte(fmt.Sprint(raw)), &parsed); err != nil {
		return out
	}
	for name, value := range parsed {
		out[strings.ToLower(strings.TrimSpace(name))] = storage.ParseInt(value)
	}
	return out
}

func pathPair(catalog worlddata.Catalog, path string) map[string]bool {
	pair := map[string]bool{}
	if _, ok := catalog.Paths[strings.TrimSpace(path)]; !ok {
		return pair
	}
	for _, name := range pathGrowthAttributes(catalog, strings.TrimSpace(path)) {
		pair[name] = true
	}
	return pair
}

// characterAttributes is what every rule reads: the stored base, the growth
// kept against the cultivator's own stage, and the path pair's capped edge.
func characterAttributes(catalog worlddata.Catalog, raw any, path string, realm, phase int64) map[string]int64 {
	attrs := decodeStoredAttributes(raw)
	if kept := keptGrowth(catalog, realm, phase); kept > 0 {
		for _, name := range attributeNames {
			attrs[name] += kept
		}
	}
	edge := pathEdge(catalog, realm, phase)
	if edge > 0 {
		for name := range pathPair(catalog, path) {
			attrs[name] += edge
		}
	}
	return attrs
}

// characterSheetAttributes is what the sheet shows: the base, +per_stage a
// stage on all six, and +path_per_stage on the pair. It decides nothing.
func characterSheetAttributes(catalog worlddata.Catalog, raw any, path string, realm, phase int64) map[string]int64 {
	attrs := decodeStoredAttributes(raw)
	g := catalog.AttributeGrowth
	stages := qiStagesCrossed(catalog, realm, phase)
	pair := pathPair(catalog, path)
	for _, name := range attributeNames {
		per := maxI64(0, g.PerStage)
		if pair[name] {
			per = maxI64(per, g.PathPerStage)
		}
		attrs[name] += per * stages
	}
	return attrs
}

// stageAttributeGains is what one qi stage adds, for the breakthrough's reply.
func stageAttributeGains(catalog worlddata.Catalog, path string) map[string]any {
	g := catalog.AttributeGrowth
	if g.PerStage <= 0 && g.PathPerStage <= 0 {
		return nil
	}
	pair := pathPair(catalog, path)
	out := map[string]any{}
	for _, name := range attributeNames {
		per := maxI64(0, g.PerStage)
		if pair[name] {
			per = maxI64(per, g.PathPerStage)
		}
		if per > 0 {
			out[name] = per
		}
	}
	return out
}

// rowAttributes is characterAttributes for a row read with firstRowMap or
// rowMaps: the SELECT must carry attributes_json, path, realm_index and phase.
// The values are int64 in an `any` map, so the `i64(attrs["body"])` readers
// stay as they were.
func rowAttributes(catalog worlddata.Catalog, row map[string]any) map[string]any {
	out := map[string]any{}
	if row == nil {
		return out
	}
	for name, value := range characterAttributes(catalog, row["attributes_json"], fmt.Sprint(row["path"]), storage.ParseInt(row["realm_index"]), storage.ParseInt(row["phase"])) {
		out[name] = value
	}
	return out
}

package game

// A road for every realm (v1.16.0).
//
// The beginner path carries a new cultivator to their first gate with a
// place in every label - the household, the street, the road, the lesson,
// the seam, the gate - and then stops at the sect road. From realm 1 to
// realm 7 the places that matter in the Mortal World (the capital, the Forge
// Terraces and their flame, the Boar King in Greenriver's hills, the marsh
// and its serpent, the ninth stage, the Sword Grave, the heavens) were found
// by reading the panel or not at all. `realm_road` in content/world.json is
// the same mechanism one realm further, seven times: an ordinary giver-less
// `quest_definitions` row per realm, seeded the way the beginner path is,
// chained by `follow_on` so each stage hands the next over as it completes.
//
// Two doors hand a stage over, and the second is the reason this file
// exists. The chain reaches a player who walked it from the start; a
// cultivator who stands at Core Formation today finished no stage and holds
// none, and the catch-up follows chains only from quests somebody completed.
// So the crossing into a realm hands over the stage written for that realm
// (grantRealmRoadTx, called from the qi breakthrough), the way a cleared
// tribulation hands over the ascension quest. Both doors are
// grantOrdinaryQuestTx, and (user_id, quest_key) is the memory, so a stage
// reached by both is handed over once.

import (
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// questTargetCity is the city a reported place is part of, when that is what
// a quest asked for. A road arrives at a gate and an explore at a gate
// reports the gate, so "Walk to Azure Crown Imperial City" would never be met
// by walking there. It answers the city only when no objective of the
// event's type names the place itself and one names its city - so a quest
// that names the gate still can, and a report that matched nothing stays a
// report that matched nothing.
func questTargetCity(catalog worlddata.Catalog, reported, objectiveType string, objectives []map[string]any) string {
	city := cityOf(catalog, reported)
	if city == "" || strings.EqualFold(city, reported) {
		return ""
	}
	namesPlace, namesCity := false, false
	for _, objective := range objectives {
		if fmt.Sprint(objective["type"]) != objectiveType || objective["target"] == nil {
			continue
		}
		target := fmt.Sprint(objective["target"])
		if strings.EqualFold(target, reported) {
			namesPlace = true
		}
		if strings.EqualFold(target, city) {
			namesCity = true
		}
	}
	if namesPlace || !namesCity {
		return ""
	}
	return city
}

// realmRoadStageFor is the stage written for a realm, or "" when the content
// authors none - the Spiritual World and above, and realm 0, which the
// beginner path covers. The first match wins, so a roster that authored two
// stages for one realm hands over the one written first.
func realmRoadStageFor(catalog worlddata.Catalog, realmIndex int64) string {
	for _, stage := range catalog.RealmRoad {
		if stage.RealmIndex == realmIndex && strings.TrimSpace(stage.QuestKey) != "" {
			return strings.TrimSpace(stage.QuestKey)
		}
	}
	return ""
}

// grantRealmRoadTx hands a cultivator the stage written for the realm they
// have just crossed into. Reports the key handed over, or "" when the realm
// has no stage, the definition is not there yet, or they held it already -
// three kinds of "no", never an error, for grantOrdinaryQuestTx's reason: a
// breakthrough must not fail over a quest.
func grantRealmRoadTx(conn *storage.Conn, catalog worlddata.Catalog, userID, realmIndex, gameMinute int64) (string, error) {
	key := realmRoadStageFor(catalog, realmIndex)
	if key == "" {
		return "", nil
	}
	granted, err := grantOrdinaryQuestTx(conn, userID, key, gameMinute)
	if err != nil || !granted {
		return "", err
	}
	return key, nil
}

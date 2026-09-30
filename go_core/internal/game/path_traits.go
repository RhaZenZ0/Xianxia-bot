package game

import (
	"encoding/json"
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A cultivation path plays differently (v1.13.0).
//
// There are seven paths, and until this release two of them had a rule of
// their own - the Beast Binder's hunt and taming, the Ghost Cultivator's
// death qi - while the other five differed only in the spread of their
// starting attributes. A first draft gave those five a flat number each (+2
// on an attack, x1.10 on a session), and the owner turned it down on the
// finding this file keeps meeting: a number nobody can feel is a number
// nobody picks a path for. So each path does something instead, and the
// numbers it does it with are content (`paths.<name>.trait`), read here and
// nowhere else, so the summary the bot prints and the rule the engine applies
// cannot disagree.
//
//   - Sword Cultivator: intent banked by wins, spent on an Intent Strike.
//   - Qi Refiner: a cultivation stance no other path may take.
//   - Body Refiner: a body that mends faster and a lost fight's lighter wound.
//   - Soul Cultivator: a steadier Heart wave and a faster-building sense.
//   - Formation Adept: arrays that hold longer, and a free deploy a day.
//
// Every path shares one more rule: a manual written for your own path suits
// you better than anybody else's (`path_system`). The 165 manuals labelled
// with a path had carried that label for nothing - anybody studied any of
// them at the same rate.

const (
	pathSword     = "Sword Cultivator"
	pathQi        = "Qi Refiner"
	pathBody      = "Body Refiner"
	pathSoul      = "Soul Cultivator"
	pathFormation = "Formation Adept"
)

// pathTrait is the one door to a path's ability. A path the catalogue does not
// carry answers false, so an unknown or empty path gets nothing rather than a
// zero value somebody later reads as a rule.
func pathTrait(catalog worlddata.Catalog, path string) (worlddata.PathTrait, bool) {
	definition, ok := catalog.Paths[strings.TrimSpace(path)]
	if !ok {
		return worlddata.PathTrait{}, false
	}
	return definition.Trait, true
}

// characterPathTx is a character's path, or "" when there is none.
func characterPathTx(conn *storage.Conn, userID int64) string {
	r, err := conn.Execute(`SELECT path FROM characters WHERE user_id=?`, []any{userID})
	if err != nil || len(r.Rows) == 0 {
		return ""
	}
	return strings.TrimSpace(fmt.Sprint(r.Rows[0][0]))
}

// manualSuitsPath answers whether a manual was written for this path. A manual
// for "Any" is everybody's and nobody's: the household and sect canons stay
// exactly what they were.
func manualSuitsPath(manualPath, characterPath string) bool {
	manualPath, characterPath = strings.TrimSpace(manualPath), strings.TrimSpace(characterPath)
	return manualPath != "" && characterPath != "" && strings.EqualFold(manualPath, characterPath)
}

// manualGatheringMult is what a practised method is worth to a session: its
// grade, deepened by mastery, and more again when it was written for the
// cultivator's own path. One statement, because two places priced a method and
// they had to agree - the session and the reply that chooses it.
func manualGatheringMult(catalog worlddata.Catalog, definition worlddata.ManualDefinition, mastery int64, characterPath string) float64 {
	mult := manualGradeMultiplier(definition.Grade) * (1 + masteryGatheringShare*float64(clampI64(mastery, 0, 4)))
	if manualSuitsPath(definition.Path, characterPath) && catalog.PathSystem.OwnManualGatheringMult > 0 {
		mult *= catalog.PathSystem.OwnManualGatheringMult
	}
	return round4(mult)
}

// manualPracticeGain is what one session adds to a method's practice: one
// point, and the content's bonus more for a method of your own path.
func manualPracticeGain(catalog worlddata.Catalog, definition worlddata.ManualDefinition, characterPath string) int64 {
	gain := cultivationPracticeGain
	if manualSuitsPath(definition.Path, characterPath) {
		gain += maxI64(0, catalog.PathSystem.OwnManualPracticeBonus)
	}
	return gain
}

// swordIntentCap is how much intent a Sword Cultivator may hold; 0 for anybody
// else, which is what makes intent a Sword Cultivator's alone.
func swordIntentCap(catalog worlddata.Catalog, path string) int64 {
	trait, ok := pathTrait(catalog, path)
	if !ok || strings.TrimSpace(path) != pathSword {
		return 0
	}
	return maxI64(0, trait.IntentCap)
}

// charactersHavePathResource guards the schema-72 column. In the compose stack
// the engine is healthy before db-init migrates (v1.1.0), so for that window a
// win banks no intent and a strike is refused rather than the action failing.
func charactersHavePathResource(conn *storage.Conn) bool {
	ok, err := tableHasColumns(conn, "characters", "path_resource")
	return err == nil && ok
}

// swordIntentTx is the intent a character holds (0 without the column).
func swordIntentTx(conn *storage.Conn, userID int64) int64 {
	if !charactersHavePathResource(conn) {
		return 0
	}
	r, err := conn.Execute(`SELECT path_resource FROM characters WHERE user_id=?`, []any{userID})
	if err != nil || len(r.Rows) == 0 {
		return 0
	}
	return storage.ParseInt(r.Rows[0][0])
}

// bankSwordIntentTx adds one intent for a win, up to the path's cap, and
// answers what the character now holds. Anybody but a Sword Cultivator banks
// nothing. It never fails the win it follows: a lost point of intent is not
// worth the battle's reward.
func bankSwordIntentTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) int64 {
	capacity := swordIntentCap(catalog, characterPathTx(conn, userID))
	if capacity <= 0 || !charactersHavePathResource(conn) {
		return 0
	}
	if _, err := conn.Execute(`UPDATE characters SET path_resource=MIN(?,COALESCE(path_resource,0)+1) WHERE user_id=?`, []any{capacity, userID}); err != nil {
		return 0
	}
	return swordIntentTx(conn, userID)
}

// qiRefinerStanceKey is the stance only a Qi Refiner may take.
const qiRefinerStanceKey = "refined"

// bodyRecoveryMult is how much faster a Body Refiner mends (1 for anybody else).
func bodyRecoveryMult(catalog worlddata.Catalog, path string) float64 {
	trait, ok := pathTrait(catalog, path)
	if !ok || strings.TrimSpace(path) != pathBody || trait.RecoveryMult <= 0 {
		return 1
	}
	return trait.RecoveryMult
}

// defeatWoundSeverity is the severity a lost fight's wound is written at: a
// Body Refiner's is lighter by the path's relief, never under 1.
func defeatWoundSeverity(catalog worlddata.Catalog, path string, severity int64) int64 {
	trait, ok := pathTrait(catalog, path)
	if ok && strings.TrimSpace(path) == pathBody && trait.WoundRelief > 0 {
		severity -= trait.WoundRelief
	}
	return maxI64(1, severity)
}

// heartWaveBonus is a Soul Cultivator's term on the tribulation's Heart wave.
func heartWaveBonus(catalog worlddata.Catalog, path string) int64 {
	trait, ok := pathTrait(catalog, path)
	if !ok || strings.TrimSpace(path) != pathSoul {
		return 0
	}
	return maxI64(0, trait.HeartWaveBonus)
}

// senseGainMult is how much faster a Soul Cultivator's spirit sense builds.
func senseGainMult(catalog worlddata.Catalog, path string) float64 {
	trait, ok := pathTrait(catalog, path)
	if !ok || strings.TrimSpace(path) != pathSoul || trait.SenseGainMult <= 0 {
		return 1
	}
	return trait.SenseGainMult
}

// arrayDurationMult is how much longer a Formation Adept's deployed array holds.
func arrayDurationMult(catalog worlddata.Catalog, path string) float64 {
	trait, ok := pathTrait(catalog, path)
	if !ok || strings.TrimSpace(path) != pathFormation || trait.ArrayDurationMult <= 0 {
		return 1
	}
	return trait.ArrayDurationMult
}

// freeDeploysPerDay is how many deploys a world day cost a Formation Adept no disk.
func freeDeploysPerDay(catalog worlddata.Catalog, path string) int64 {
	trait, ok := pathTrait(catalog, path)
	if !ok || strings.TrimSpace(path) != pathFormation {
		return 0
	}
	return maxI64(0, trait.FreeDeploysPerDay)
}

// practisedManualSuitsPath answers whether the method a cultivator gathers by
// was written for their path, so a session's reply can say why it gathered more.
func practisedManualSuitsPath(conn *storage.Conn, catalog worlddata.Catalog, userID int64, path string) bool {
	id, _, _, err := practisedManual(conn, catalog, userID)
	if err != nil || id == "" {
		return false
	}
	return manualSuitsPath(catalog.TechniqueSystem.Manuals[id].Path, path)
}

// formationFreeDeployLogType is the event_log row a free deploy leaves, so a
// world day's allowance is counted the way good-deed karma is.
const formationFreeDeployLogType = "formation_free_deploy"

// formationFreeDeployTx answers whether this deploy is one of a Formation
// Adept's free ones today, and records it when it is. It never errors: at worst
// the disk is spent, which is what a deploy has always cost.
func formationFreeDeployTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, path string, gameMinute int64, now float64) bool {
	allowance := freeDeploysPerDay(catalog, path)
	if allowance <= 0 || !tableExistsTx(conn, "event_log") {
		return false
	}
	key := fmt.Sprintf("day:%d", gameMinute/1440)
	counted, err := conn.Execute(`SELECT COUNT(*) FROM event_log WHERE user_id=? AND event_type=? AND json_extract(payload_json,'$.key')=?`,
		[]any{userID, formationFreeDeployLogType, key})
	if err != nil || len(counted.Rows) == 0 || storage.ParseInt(counted.Rows[0][0]) >= allowance {
		return false
	}
	payload, _ := json.Marshal(map[string]any{"key": key})
	if _, err := conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`,
		[]any{userID, formationFreeDeployLogType, string(payload), now}); err != nil {
		return false
	}
	return true
}

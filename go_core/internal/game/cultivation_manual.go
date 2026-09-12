package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The art you practise (v1.0.0-rc.6). A manual was a shelf of techniques and
// a realm requirement; the method itself did nothing for the gathering. It
// does now: the manual a cultivator practises multiplies what every session
// gathers, by its grade, and deepens as they master it. A Dao-grade method
// perfected is worth half again what a mortal pamphlet is.
//
// The choice lives in world_state under the player's id, like the stance. A
// cultivator who never chose practises the best method they have learned, so
// every existing character benefits from the manual they already own.

// manualGradeMultipliers is the gathering worth of each grade of method.
var manualGradeMultipliers = map[string]float64{
	"Mortal":   1.03,
	"Earth":    1.07,
	"Spirit":   1.12,
	"Heaven":   1.18,
	"Immortal": 1.26,
	"Dao":      1.36,
}

// manualGradeOrder ranks the grades, so "the best method you have learned"
// is a defined thing.
var manualGradeOrder = []string{"Mortal", "Earth", "Spirit", "Heaven", "Immortal", "Dao"}

// masteryGathering is what practice adds on top of the grade: three percent a
// mastery level, so a perfected method is worth a further twelfth.
const masteryGatheringShare = 0.03

func manualGradeRank(grade string) int {
	for rank, name := range manualGradeOrder {
		if name == grade {
			return rank
		}
	}
	return -1
}

func manualGradeMultiplier(grade string) float64 {
	if v, ok := manualGradeMultipliers[grade]; ok {
		return v
	}
	return 1.0
}

func cultivationManualKey(userID int64) string {
	return fmt.Sprintf("cultivation_manual:%d", userID)
}

// learnedManuals is every method a cultivator has opened, with its mastery.
func learnedManuals(conn *storage.Conn, userID int64) (map[string]int64, error) {
	res, err := conn.Execute(`SELECT manual_id,mastery FROM character_manuals WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	out := map[string]int64{}
	for _, row := range res.Rows {
		if len(row) < 2 {
			continue
		}
		out[fmt.Sprint(row[0])] = storage.ParseInt(row[1])
	}
	return out, nil
}

// practisedManual is the method the cultivator gathers by: the one they chose
// if they still have it, otherwise the best they have learned.
func practisedManual(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (string, int64, bool, error) {
	learned, err := learnedManuals(conn, userID)
	if err != nil {
		return "", 0, false, err
	}
	if len(learned) == 0 {
		return "", 0, false, nil
	}
	state, err := readWorldStateMap(conn, cultivationManualKey(userID))
	if err != nil {
		return "", 0, false, err
	}
	chosen := strings.TrimSpace(fmt.Sprint(state["manual_id"]))
	if mastery, ok := learned[chosen]; ok {
		if _, known := catalog.TechniqueSystem.Manuals[chosen]; known {
			return chosen, mastery, true, nil
		}
	}
	best, bestMastery, bestRank := "", int64(0), -2
	for id, mastery := range learned {
		definition, ok := catalog.TechniqueSystem.Manuals[id]
		if !ok {
			continue
		}
		rank := manualGradeRank(definition.Grade)
		// Ties break on mastery, then on the id, so the default never
		// wanders between two equally good methods.
		if rank > bestRank || (rank == bestRank && (mastery > bestMastery || (mastery == bestMastery && id < best))) {
			best, bestMastery, bestRank = id, mastery, rank
		}
	}
	return best, bestMastery, false, nil
}

// manualCultivationMultiplier is what the practised method is worth to a
// session: its grade, deepened by mastery. No method is 1.0 and no penalty.
// The element (v1.0.0-rc.9) comes back with it: the kind of qi the method
// draws is what a spiritual root is measured against.
func manualCultivationMultiplier(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (name string, grade string, element string, mult float64, chosen bool, err error) {
	id, mastery, chosen, err := practisedManual(conn, catalog, userID)
	if err != nil || id == "" {
		return "", "", "", 1, false, err
	}
	definition, ok := catalog.TechniqueSystem.Manuals[id]
	if !ok {
		return "", "", "", 1, false, nil
	}
	mult = manualGradeMultiplier(definition.Grade) * (1 + masteryGatheringShare*float64(clampI64(mastery, 0, 4)))
	return definition.Name, definition.Grade, strings.TrimSpace(definition.Element), round4(mult), chosen, nil
}

type cultivationManualPayload struct {
	GameMinute int64  `json:"game_minute"`
	ManualID   string `json:"manual_id"`
}

// cultivationManualAction chooses the method a cultivator gathers by. Only a
// manual they have actually learned can be practised.
func cultivationManualAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p cultivationManualPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation practises nothing")
	}
	p.ManualID = strings.TrimSpace(p.ManualID)
	definition, ok := catalog.TechniqueSystem.Manuals[p.ManualID]
	if !ok {
		return authoritativeMutation{}, errors.New("unknown cultivation manual")
	}
	learned, err := learnedManuals(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	mastery, held := learned[p.ManualID]
	if !held {
		return authoritativeMutation{}, fmt.Errorf("%s has not been learned; study it first", definition.Name)
	}
	previousName, _, _, previousMult, _, err := manualCultivationMultiplier(conn, catalog, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if err := writeWorldStateMap(conn, cultivationManualKey(userID), map[string]any{"manual_id": p.ManualID, "chosen_game_minute": p.GameMinute}, now); err != nil {
		return authoritativeMutation{}, err
	}
	mult := round4(manualGradeMultiplier(definition.Grade) * (1 + masteryGatheringShare*float64(clampI64(mastery, 0, 4))))
	bundle, err := loadAptitudes(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	absorption := absorptionFor(catalog, bundle.Root, definition.Element)
	result := map[string]any{
		"manual_id": p.ManualID, "manual_name": definition.Name, "manual_grade": definition.Grade,
		"manual_mult": mult, "mastery": mastery, "alignment": definition.Alignment, "path": definition.Path,
		"previous_manual": previousName, "previous_mult": previousMult, "changed": previousName != definition.Name,
		// v1.0.0-rc.9: the kind of qi it draws, and what this root makes of it.
		// Spelled out rather than merged in, so the Python/Go result-key
		// contract can see every key this action returns.
		"element": absorption.Element, "element_relation": absorption.Relation,
		"element_label": absorption.Label, "element_note": absorption.Note, "element_mult": absorption.Mult,
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "cultivation", EventType: "manual_practised", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result}}, nil
}

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

type beastTamePayload struct {
	EncounterID int64 `json:"encounter_id"`
}
type beastIDPayload struct {
	BeastID int64 `json:"beast_id"`
}
type beastFeedPayload struct {
	BeastID int64  `json:"beast_id"`
	Food    string `json:"food"`
}
type artifactBondPayload struct {
	ItemID string `json:"item_id"`
}
type artifactAwakenPayload struct {
	ItemID     string `json:"item_id"`
	SpiritName string `json:"spirit_name"`
}

const (
	beastTameCooldownSeconds    int64 = 30 * 60
	beastFeedCooldownSeconds    int64 = 20 * 60
	beastTrainCooldownSeconds   int64 = 45 * 60
	artifactBondCooldownSeconds int64 = 30 * 60
)

func validateCompanionPayload(raw json.RawMessage, allowedFields ...string) error {
	var supplied map[string]json.RawMessage
	if err := json.Unmarshal(raw, &supplied); err != nil {
		return err
	}
	if supplied == nil {
		return errors.New("payload must be a JSON object")
	}
	allowed := make(map[string]struct{}, len(allowedFields))
	for _, field := range allowedFields {
		allowed[field] = struct{}{}
	}
	for field := range supplied {
		if _, ok := allowed[field]; !ok {
			return fmt.Errorf("client-supplied %s is forbidden", field)
		}
	}
	return nil
}

func loadLivingCompanionActor(conn *storage.Conn, userID int64) (mechanicsCharacter, error) {
	character, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return mechanicsCharacter{}, err
	}
	if character.LifeStatus != "alive" {
		return mechanicsCharacter{}, errors.New("only a living character can manage companions or artifact bonds")
	}
	return character, nil
}

func canonicalBeastTrainingContext(conn *storage.Conn, userID int64, location string) (int64, int64, error) {
	location = strings.TrimSpace(location)
	if location == "" {
		return 0, 0, nil
	}
	res, err := conn.Execute(
		`SELECT user_id,beast_pen_level FROM cave_abodes WHERE location_key=?`,
		[]any{location},
	)
	if err != nil {
		return 0, 0, err
	}
	row := firstRowMap(res)
	if row == nil {
		return 0, 0, nil
	}
	ownerID := i64(row["user_id"])
	canAccess := ownerID == userID
	if !canAccess {
		access, accessErr := conn.Execute(
			`SELECT 1 FROM cave_abode_access WHERE owner_user_id=? AND guest_user_id=?`,
			[]any{ownerID, userID},
		)
		if accessErr != nil {
			return 0, 0, accessErr
		}
		canAccess = firstRowMap(access) != nil
	}
	if !canAccess {
		return 0, 0, nil
	}
	penLevel := maxI64(0, i64(row["beast_pen_level"]))
	return penLevel, penLevel * 2, nil
}

func spiritBeastRow(conn *storage.Conn, userID, beastID int64) (map[string]any, error) {
	r, e := conn.Execute(`SELECT * FROM spirit_beasts WHERE user_id=? AND beast_id=?`, []any{userID, beastID})
	if e != nil {
		return nil, e
	}
	return firstRowMap(r), nil
}
func artifactRow(conn *storage.Conn, userID int64, item string) (map[string]any, error) {
	r, e := conn.Execute(`SELECT * FROM artifact_bonds WHERE user_id=? AND item_id=?`, []any{userID, item})
	if e != nil {
		return nil, e
	}
	return firstRowMap(r), nil
}

func beastTameAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	if err := validateCompanionPayload(raw, "encounter_id"); err != nil {
		return authoritativeMutation{}, err
	}
	var p beastTamePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if p.EncounterID <= 0 {
		return authoritativeMutation{}, errors.New("encounter_id must be positive")
	}

	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	character, err := loadLivingCompanionActor(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if rem, err := cooldownRemaining(conn, userID, "beast_tame", now); err != nil {
		return authoritativeMutation{}, err
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("beast taming cooldown: %d seconds", rem)
	}

	res, err := conn.Execute(`SELECT * FROM wild_beast_encounters WHERE user_id=? AND encounter_id=?`, []any{userID, p.EncounterID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	encounter := firstRowMap(res)
	if encounter == nil || fmt.Sprint(encounter["status"]) != "available" || i64(encounter["expires_game_minute"]) <= gameMinute {
		return authoritativeMutation{}, errors.New("taming opportunity is missing or expired")
	}
	if strings.TrimSpace(fmt.Sprint(encounter["location"])) != strings.TrimSpace(character.Location) {
		return authoritativeMutation{}, errors.New("taming opportunity is not at the character's current location")
	}

	countRes, err := conn.Execute(`SELECT COUNT(*) AS n FROM spirit_beasts WHERE user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	count := i64(firstRowMap(countRes)["n"])
	if count >= 5 {
		return authoritativeMutation{}, errors.New("maximum of five spirit-beast contracts reached")
	}
	pathBonus := int64(0)
	if character.Path == "Beast Binder" {
		pathBonus = 4
	}
	bondExperience := minI64(count, 4)
	modifier := character.Attributes["spirit"] + character.Attributes["presence"] + character.Attributes["will"]/2 + pathBonus + bondExperience
	roll, err := roll2d10(modifier, i64(encounter["taming_tn"]))
	if err != nil {
		return authoritativeMutation{}, err
	}
	success := boolResult(roll)
	margin := resultMargin(roll)
	if err = setCooldown(conn, userID, "beast_tame", beastTameCooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}

	var beast map[string]any
	status := "escaped"
	if !success {
		if _, err = conn.Execute(`UPDATE wild_beast_encounters SET status='escaped',updated_at=? WHERE encounter_id=?`, []any{now, p.EncounterID}); err != nil {
			return authoritativeMutation{}, err
		}
		if _, err = advanceProfessionTx(conn, userID, "Beast Taming", false, 6, 0, now); err != nil {
			return authoritativeMutation{}, err
		}
	} else {
		active := int64(0)
		if count == 0 {
			active = 1
			if _, err = conn.Execute(`UPDATE spirit_beasts SET active=0 WHERE user_id=?`, []any{userID}); err != nil {
				return authoritativeMutation{}, err
			}
		}
		inserted, insertErr := conn.Execute(
			`INSERT INTO spirit_beasts(user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,loyalty,contract_type,active,techniques_json,created_at,updated_at)
			 VALUES(?,?,?,?,?,?,?,?,0,30,'equality',?,'[]',?,?)`,
			[]any{userID, fmt.Sprint(encounter["species"]), fmt.Sprint(encounter["species"]), i64(encounter["rank"]), fmt.Sprint(encounter["element"]), i64(encounter["intelligence"]), "bonded", fmt.Sprint(encounter["bloodline"]), active, now, now},
		)
		if insertErr != nil {
			return authoritativeMutation{}, insertErr
		}
		if _, err = conn.Execute(`UPDATE wild_beast_encounters SET status='tamed',updated_at=? WHERE encounter_id=?`, []any{now, p.EncounterID}); err != nil {
			return authoritativeMutation{}, err
		}
		beast, err = spiritBeastRow(conn, userID, inserted.LastInsertID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		status = "tamed"
		xp := int64(18)
		if margin > 0 {
			xp += margin
		}
		quality := maxI64(0, margin)
		if _, err = advanceProfessionTx(conn, userID, "Beast Taming", true, xp, quality, now); err != nil {
			return authoritativeMutation{}, err
		}
	}

	progRes, _ := conn.Execute(`SELECT * FROM profession_progress WHERE user_id=? AND profession='Beast Taming'`, []any{userID})
	result := map[string]any{
		"status": status, "encounter": encounter, "beast": beast,
		"profession_progress": firstRowMap(progRes), "game_minute": gameMinute,
		"location": character.Location, "cooldown_seconds": beastTameCooldownSeconds,
	}
	for key, value := range roll {
		result[key] = value
	}
	return authoritativeMutation{
		Result: result,
		Event:  eventledger.Event{Domain: "beasts", EventType: "beast.tame", GameMinute: gameMinute, Payload: result},
	}, nil
}

func beastFeedAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	if err := validateCompanionPayload(raw, "beast_id", "food"); err != nil {
		return authoritativeMutation{}, err
	}
	var p beastFeedPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if p.BeastID <= 0 {
		return authoritativeMutation{}, errors.New("beast_id must be positive")
	}
	p.Food = strings.TrimSpace(p.Food)
	character, err := loadLivingCompanionActor(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gain := int64(0)
	switch p.Food {
	case "spirit_herb":
		gain = 5
	case "beast_core":
		gain = 10
	default:
		return authoritativeMutation{}, errors.New("unsupported beast food")
	}

	now := float64(time.Now().UnixNano()) / 1e9
	key := fmt.Sprintf("beast_feed:%d", p.BeastID)
	if rem, err := cooldownRemaining(conn, userID, key, now); err != nil {
		return authoritativeMutation{}, err
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("beast feeding cooldown: %d seconds", rem)
	}
	if row, err := spiritBeastRow(conn, userID, p.BeastID); err != nil {
		return authoritativeMutation{}, err
	} else if row == nil {
		return authoritativeMutation{}, errors.New("unknown contracted beast")
	}
	missing, err := consumeInventoryTx(conn, userID, map[string]int64{p.Food: 1})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(missing) > 0 {
		return authoritativeMutation{}, errors.New("required beast food is not carried")
	}
	if _, err = conn.Execute(
		`UPDATE spirit_beasts SET loyalty=MIN(100,loyalty+?),intelligence=MIN(100,intelligence+MAX(1,?/3)),updated_at=? WHERE user_id=? AND beast_id=?`,
		[]any{gain, gain, now, userID, p.BeastID},
	); err != nil {
		return authoritativeMutation{}, err
	}
	if err = setCooldown(conn, userID, key, beastFeedCooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}
	row, err := spiritBeastRow(conn, userID, p.BeastID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	row["cooldown_seconds"] = beastFeedCooldownSeconds
	row["feed_gain"] = gain
	row["game_minute"] = gameMinute
	row["location"] = character.Location
	return authoritativeMutation{
		Result: row,
		Event:  eventledger.Event{Domain: "beasts", EventType: "beast.feed", EntityType: "spirit_beast", EntityID: fmt.Sprint(p.BeastID), GameMinute: gameMinute, Payload: row},
	}, nil
}

func beastTrainAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	if err := validateCompanionPayload(raw, "beast_id"); err != nil {
		return authoritativeMutation{}, err
	}
	var p beastIDPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if p.BeastID <= 0 {
		return authoritativeMutation{}, errors.New("beast_id must be positive")
	}

	now := float64(time.Now().UnixNano()) / 1e9
	key := fmt.Sprintf("beast_train:%d", p.BeastID)
	if rem, err := cooldownRemaining(conn, userID, key, now); err != nil {
		return authoritativeMutation{}, err
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("beast training cooldown: %d seconds", rem)
	}
	character, err := loadLivingCompanionActor(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if row, rowErr := spiritBeastRow(conn, userID, p.BeastID); rowErr != nil {
		return authoritativeMutation{}, rowErr
	} else if row == nil {
		return authoritativeMutation{}, errors.New("unknown contracted beast")
	}

	penLevel, contextBonus, err := canonicalBeastTrainingContext(conn, userID, character.Location)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gain := clampI64(6+character.Attributes["spirit"]/3+contextBonus, 1, 25)
	if _, err = conn.Execute(
		`UPDATE spirit_beasts SET loyalty=MIN(100,loyalty+?),intelligence=MIN(100,intelligence+MAX(1,?/3)),updated_at=? WHERE user_id=? AND beast_id=?`,
		[]any{gain, gain, now, userID, p.BeastID},
	); err != nil {
		return authoritativeMutation{}, err
	}
	if err = setCooldown(conn, userID, key, beastTrainCooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}
	prog, err := advanceProfessionTx(conn, userID, "Beast Taming", true, 8, gain/3, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	row, err := spiritBeastRow(conn, userID, p.BeastID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"beast": row, "gain": gain, "profession_progress": prog,
		"location": character.Location, "beast_pen_level": penLevel,
		"context_bonus": contextBonus, "cooldown_seconds": beastTrainCooldownSeconds,
		"game_minute": gameMinute,
	}
	return authoritativeMutation{
		Result: result,
		Event:  eventledger.Event{Domain: "beasts", EventType: "beast.train", EntityType: "spirit_beast", EntityID: fmt.Sprint(p.BeastID), GameMinute: gameMinute, Payload: result},
	}, nil
}

func beastEvolveAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	if err := validateCompanionPayload(raw, "beast_id"); err != nil {
		return authoritativeMutation{}, err
	}
	var p beastIDPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if p.BeastID <= 0 {
		return authoritativeMutation{}, errors.New("beast_id must be positive")
	}
	character, err := loadLivingCompanionActor(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	row, err := spiritBeastRow(conn, userID, p.BeastID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if row == nil {
		return authoritativeMutation{}, errors.New("unknown contracted beast")
	}
	need := int64(60) + i64(row["evolution_stage"])*10
	if i64(row["loyalty"]) < need {
		return authoritativeMutation{}, fmt.Errorf("loyalty %d is below evolution requirement %d", i64(row["loyalty"]), need)
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(
		`UPDATE spirit_beasts SET evolution_stage=evolution_stage+1,rank=rank+1,loyalty=MAX(25,loyalty-20),updated_at=? WHERE user_id=? AND beast_id=?`,
		[]any{now, userID, p.BeastID},
	); err != nil {
		return authoritativeMutation{}, err
	}
	prog, err := advanceProfessionTx(conn, userID, "Beast Taming", true, 25, 10, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	row, err = spiritBeastRow(conn, userID, p.BeastID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"beast": row, "profession_progress": prog,
		"game_minute": gameMinute, "location": character.Location,
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain: "beasts", EventType: "beast.evolve", EntityType: "spirit_beast",
			EntityID: fmt.Sprint(p.BeastID), GameMinute: gameMinute, Payload: result,
		},
	}, nil
}

func beastActiveAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	if err := validateCompanionPayload(raw, "beast_id"); err != nil {
		return authoritativeMutation{}, err
	}
	var p beastIDPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if p.BeastID <= 0 {
		return authoritativeMutation{}, errors.New("beast_id must be positive")
	}
	character, err := loadLivingCompanionActor(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	row, err := spiritBeastRow(conn, userID, p.BeastID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if row == nil {
		return authoritativeMutation{}, errors.New("unknown contracted beast")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE spirit_beasts SET active=0 WHERE user_id=?`, []any{userID}); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = conn.Execute(
		`UPDATE spirit_beasts SET active=1,updated_at=? WHERE user_id=? AND beast_id=?`,
		[]any{now, userID, p.BeastID},
	); err != nil {
		return authoritativeMutation{}, err
	}
	row, err = spiritBeastRow(conn, userID, p.BeastID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	row["game_minute"] = gameMinute
	row["location"] = character.Location
	return authoritativeMutation{
		Result: row,
		Event: eventledger.Event{
			Domain: "beasts", EventType: "beast.active", EntityType: "spirit_beast",
			EntityID: fmt.Sprint(p.BeastID), GameMinute: gameMinute, Payload: row,
		},
	}, nil
}

func artifactBondAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	if err := validateCompanionPayload(raw, "item_id"); err != nil {
		return authoritativeMutation{}, err
	}
	var p artifactBondPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.ItemID = strings.TrimSpace(p.ItemID)
	if p.ItemID == "" {
		return authoritativeMutation{}, errors.New("item_id is required")
	}
	if _, ok := catalog.Items[p.ItemID]; !ok {
		return authoritativeMutation{}, errors.New("unknown canonical item")
	}
	character, err := loadLivingCompanionActor(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	inv, err := conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, p.ItemID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if row := firstRowMap(inv); row == nil || i64(row["quantity"]) <= 0 {
		return authoritativeMutation{}, errors.New("item must be carried to form a bond")
	}

	now := float64(time.Now().UnixNano()) / 1e9
	key := "artifact_bond:" + p.ItemID
	if rem, err := cooldownRemaining(conn, userID, key, now); err != nil {
		return authoritativeMutation{}, err
	} else if rem > 0 {
		return authoritativeMutation{}, fmt.Errorf("artifact resonance cooldown: %d seconds", rem)
	}
	if _, err = conn.Execute(
		`INSERT INTO artifact_bonds(user_id,item_id,bond_level,resonance,awakened,spirit_name,temperament,created_at,updated_at)
		 VALUES(?,?,1,10,0,'','dormant',?,?)
		 ON CONFLICT(user_id,item_id) DO UPDATE SET bond_level=MIN(10,artifact_bonds.bond_level+1),resonance=MIN(100,artifact_bonds.resonance+8),updated_at=excluded.updated_at`,
		[]any{userID, p.ItemID, now, now},
	); err != nil {
		return authoritativeMutation{}, err
	}
	if err = setCooldown(conn, userID, key, artifactBondCooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}
	row, err := artifactRow(conn, userID, p.ItemID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	qualityPoints := maxI64(1, i64(row["resonance"])/20)
	prog, err := advanceProfessionTx(conn, userID, "Artifact Refining", true, 10, qualityPoints, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"artifact": row, "profession_progress": prog,
		"cooldown_seconds": artifactBondCooldownSeconds,
		"game_minute":      gameMinute, "location": character.Location,
	}
	return authoritativeMutation{
		Result: result,
		Event:  eventledger.Event{Domain: "artifacts", EventType: "artifact.bond", EntityType: "artifact", EntityID: p.ItemID, GameMinute: gameMinute, Payload: result},
	}, nil
}

func artifactAwakenAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	if err := validateCompanionPayload(raw, "item_id", "spirit_name"); err != nil {
		return authoritativeMutation{}, err
	}
	var p artifactAwakenPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	p.ItemID = strings.TrimSpace(p.ItemID)
	p.SpiritName = strings.TrimSpace(p.SpiritName)
	if p.ItemID == "" || p.SpiritName == "" {
		return authoritativeMutation{}, errors.New("item_id and spirit_name are required")
	}
	character, err := loadLivingCompanionActor(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	row, err := artifactRow(conn, userID, p.ItemID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if row == nil || i64(row["bond_level"]) < 3 || i64(row["resonance"]) < 25 {
		return authoritativeMutation{}, errors.New("artifact requires Bond 3 and 25 resonance")
	}
	if i64(row["awakened"]) != 0 {
		return authoritativeMutation{}, errors.New("artifact spirit is already awakened")
	}
	if len([]rune(p.SpiritName)) > 80 {
		p.SpiritName = string([]rune(p.SpiritName)[:80])
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(
		`UPDATE artifact_bonds SET awakened=1,spirit_name=?,temperament='awakened',resonance=MIN(100,resonance+15),updated_at=? WHERE user_id=? AND item_id=?`,
		[]any{p.SpiritName, now, userID, p.ItemID},
	); err != nil {
		return authoritativeMutation{}, err
	}
	prog, err := advanceProfessionTx(conn, userID, "Artifact Refining", true, 30, 15, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	row, err = artifactRow(conn, userID, p.ItemID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"artifact": row, "profession_progress": prog,
		"game_minute": gameMinute, "location": character.Location,
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain: "artifacts", EventType: "artifact.awaken", EntityType: "artifact",
			EntityID: p.ItemID, GameMinute: gameMinute, Payload: result,
		},
	}, nil
}

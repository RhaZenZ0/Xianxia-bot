package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A house worth coming back to (v1.0.0-rc.32).
//
// Until now the birth household gave a child a heirloom, a trade and a +2 on
// the way out of the door, and after that one handout of stones every three
// in-world months - which could be asked for from anywhere in the world.
// Stepping back in was a free teleport. The treasury column was written once
// and read by nothing, the hearth was ordinary ground and refused seclusion,
// and nothing in the game ever said "go home". This file is the reasons to.
//
// Four of them are asked for *at home*, and the fifth is what makes home a
// journey: `family.household.enter` now wants the character standing in the
// family's own town, and a Hearth-Return Talisman (item_use_actions.go) is
// the way back from anywhere else. Support, the purse (`family.contribute`),
// the second round of teaching (`family.tutor`) and the household's errands
// (`family.errand`) all refuse a character who is not inside.

// householdStandingKey is the faction_reputation key a character's standing
// with their own household lives under. The table imposes no vocabulary, so
// no schema was needed for the house to remember who put stones in its
// coffers and who brought its errands home.
func householdStandingKey(familyID int64) string {
	return fmt.Sprintf("family:%d", familyID)
}

// hearthReturnTalismanItem is the content id of the talisman the send-off
// folds into a child's hands: the way home from anywhere.
const hearthReturnTalismanItem = "hearth_return_talisman"

// householdErrandPrefix is the namespace every household errand's quest key
// carries. It is what lets a `household_standing` reward be refused on any
// quest that is not one - the Forge must not be able to inflate a house's
// opinion of a player by drafting a quest that says so.
const householdErrandPrefix = "errand_"

// householdContributeStandingCap bounds what one contribution can earn in
// standing; householdSupportStandingCap bounds the stones standing adds to a
// support handout.
const (
	householdContributeStandingCap = 10
	householdSupportStandingCap    = 10
)

// atHouseholdTx says whether the character is standing inside their birth
// household, and which one.
func atHouseholdTx(conn *storage.Conn, userID int64) (familyID int64, home bool, err error) {
	r, err := conn.Execute(`SELECT c.location,cbf.family_id FROM characters c JOIN character_birth_family cbf ON cbf.user_id=c.user_id WHERE c.user_id=?`, []any{userID})
	if err != nil || len(r.Rows) == 0 {
		return 0, false, err
	}
	familyID = i64(r.Rows[0][1])
	return familyID, fmt.Sprint(r.Rows[0][0]) == birthFamilyHouseholdLocation(familyID), nil
}

// requireAtHomeTx is the one refusal all four at-home actions share.
func requireAtHomeTx(conn *storage.Conn, userID int64, what string) (int64, error) {
	familyID, home, err := atHouseholdTx(conn, userID)
	if err != nil {
		return 0, err
	}
	if familyID <= 0 {
		return 0, errors.New("no birth family is recorded")
	}
	if !home {
		return 0, fmt.Errorf("%s is asked for at home — step into your birth household first (/family → Enter)", what)
	}
	return familyID, nil
}

// householdStandingTx is the character's standing with their household,
// -100..100, 0 when nothing has been recorded.
func householdStandingTx(conn *storage.Conn, userID, familyID int64) (int64, error) {
	if !tableExistsTx(conn, "faction_reputation") {
		return 0, nil
	}
	r, err := conn.Execute(`SELECT score FROM faction_reputation WHERE user_id=? AND faction_key=?`, []any{userID, householdStandingKey(familyID)})
	if err != nil || len(r.Rows) == 0 {
		return 0, err
	}
	return clamp(i64(r.Rows[0][0]), -100, 100), nil
}

// householdStandingBand is the word the house has for you.
func householdStandingBand(standing int64) string {
	switch {
	case standing >= 70:
		return "a pillar of the house"
	case standing >= 35:
		return "trusted"
	case standing >= 10:
		return "known"
	default:
		return "a stranger to the ledger"
	}
}

// appendFamilyHistoryTx adds one line to the household's own chronicle.
func appendFamilyHistoryTx(conn *storage.Conn, familyID int64, line string) error {
	r, err := conn.Execute(`SELECT COALESCE(history_json,'[]') FROM birth_families WHERE family_id=?`, []any{familyID})
	if err != nil || len(r.Rows) == 0 {
		return err
	}
	history := []string{}
	_ = json.Unmarshal([]byte(fmt.Sprint(r.Rows[0][0])), &history)
	history = append(history, line)
	if len(history) > 50 {
		history = history[len(history)-50:]
	}
	encoded, _ := json.Marshal(history)
	_, err = conn.Execute(`UPDATE birth_families SET history_json=?,updated_at=? WHERE family_id=?`, []any{string(encoded), nowSeconds(), familyID})
	return err
}

// ---- the hearth -----------------------------------------------------------

// birthFamilyCultivationMultiplier is what sitting in the family's own hall is
// worth: 1.04 + 0.02 per tier, held under the roadside shrine's 1.15 so the
// hearth is a good place to sit and never the best one. One helper, read by
// active cultivation and by seclusion, so the two cannot drift.
func birthFamilyCultivationMultiplier(conn *storage.Conn, location string) (string, float64, error) {
	if !strings.HasPrefix(location, "birth_family:") {
		return "", 1, nil
	}
	familyID := i64(strings.TrimPrefix(location, "birth_family:"))
	if familyID <= 0 || !tableExistsTx(conn, "birth_families") {
		return "", 1, nil
	}
	r, err := conn.Execute(`SELECT family_name,tier FROM birth_families WHERE family_id=?`, []any{familyID})
	if err != nil || len(r.Rows) == 0 {
		return "", 1, err
	}
	tier := clamp(i64(r.Rows[0][1]), 0, 5)
	mult := math.Min(placeShrineMult-0.01, 1.04+0.02*float64(tier))
	return fmt.Sprint(r.Rows[0][0]) + " Household", round4(mult), nil
}

// ---- the purse ------------------------------------------------------------

type familyContributePayload struct {
	GameMinute int64 `json:"game_minute"`
	Amount     int64 `json:"amount"`
}

// familyContributeActionGo puts a character's spirit stones into the
// household's coffers. The treasury remembers every stone; wealth rises a
// fifth as fast and influence a twenty-fifth, both capped at 100; and the
// house remembers who gave.
func familyContributeActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p familyContributePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	if p.Amount <= 0 {
		return authoritativeMutation{}, errors.New("a contribution has to be at least one spirit stone")
	}
	familyID, err := requireAtHomeTx(conn, userID, "a contribution to the household")
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := nowSeconds()
	balance, err := walletDeltaTx(conn, catalog, userID, "low_spirit_stone", -p.Amount, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = conn.Execute(`UPDATE birth_families SET treasury_balance=treasury_balance+?,wealth=MIN(100,wealth+?),influence=MIN(100,influence+?),updated_at=? WHERE family_id=?`, []any{p.Amount, p.Amount / 5, p.Amount / 25, now, familyID}); err != nil {
		return authoritativeMutation{}, err
	}
	standingGain := clamp(p.Amount/10, 1, householdContributeStandingCap)
	if err = adjustReputationGo(conn, userID, householdStandingKey(familyID), standingGain, "put stones into the household's coffers"); err != nil {
		return authoritativeMutation{}, err
	}
	name := ""
	if r, e := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{userID}); e == nil && len(r.Rows) > 0 {
		name = fmt.Sprint(r.Rows[0][0])
	}
	if err = appendFamilyHistoryTx(conn, familyID, fmt.Sprintf("%s put %d spirit stones into the household's coffers.", name, p.Amount)); err != nil {
		return authoritativeMutation{}, err
	}
	fam, err := birthFamilyForUserGo(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	standing, err := householdStandingTx(conn, userID, familyID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{
		"family_id": familyID, "family_name": fmt.Sprint(fam["family_name"]), "amount": p.Amount, "balance": balance,
		"treasury_balance": i64(fam["treasury_balance"]), "wealth": i64(fam["wealth"]), "influence": i64(fam["influence"]),
		"standing": standing, "standing_gain": standingGain, "standing_band": householdStandingBand(standing),
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "family", EventType: "family.contribute", EntityType: "birth_family", EntityID: fmt.Sprint(familyID), SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

// ---- taught again ---------------------------------------------------------

type familyTutorPayload struct {
	GameMinute int64 `json:"game_minute"`
}

// householdNextTutoringBand is the wealth at which the house could teach
// more than it does now, or 0 at the top.
func householdNextTutoringBand(wealth int64) int64 {
	for _, edge := range []int64{40, 60, 80} {
		if wealth < edge {
			return edge
		}
	}
	return 0
}

// familyTutorActionGo is the household teaching its trade again, at home,
// as well as it can now afford to. The row only ever rises: level to the
// band's, XP to the band's if that is more than the player has, and a house
// that can teach nothing new says so and names the wealth at which it could.
func familyTutorActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p familyTutorPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	familyID, err := requireAtHomeTx(conn, userID, "the household's teaching")
	if err != nil {
		return authoritativeMutation{}, err
	}
	fam, err := birthFamilyForUserGo(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	trade := householdTradeFor(catalog, fmt.Sprint(fam["archetype"]))
	if trade == "" {
		return authoritativeMutation{}, errors.New("this household has no trade to teach")
	}
	if !tableExistsTx(conn, "profession_progress") {
		return authoritativeMutation{}, errors.New("profession progress is not available yet")
	}
	wealth := max64(0, i64(fam["wealth"]))
	level, xp, tutor := householdTutoring(wealth)
	now := nowSeconds()
	r, err := conn.Execute(`SELECT level,xp FROM profession_progress WHERE user_id=? AND profession=?`, []any{userID, trade})
	if err != nil {
		return authoritativeMutation{}, err
	}
	taught := false
	if len(r.Rows) == 0 {
		if _, err = conn.Execute(`INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at) VALUES(?,?,?,?,0,0,0,?)`, []any{userID, trade, level, xp, now}); err != nil {
			return authoritativeMutation{}, err
		}
		taught = true
	} else {
		have, haveXP := i64(r.Rows[0][0]), i64(r.Rows[0][1])
		if have < level || (have == level && haveXP < xp) {
			if _, err = conn.Execute(`UPDATE profession_progress SET level=?,xp=?,updated_at=? WHERE user_id=? AND profession=?`, []any{level, xp, now, userID, trade}); err != nil {
				return authoritativeMutation{}, err
			}
			taught = true
		}
	}
	if !taught {
		if next := householdNextTutoringBand(wealth); next > 0 {
			return authoritativeMutation{}, fmt.Errorf("the household has nothing more to teach you in %s yet — at wealth %d it could afford better teaching (it holds %d)", trade, next, wealth)
		}
		return authoritativeMutation{}, fmt.Errorf("the household has taught you all it can in %s; the rest is practice", trade)
	}
	out := map[string]any{"family_id": familyID, "family_name": fmt.Sprint(fam["family_name"]), "profession": trade, "tutor": tutor, "level": level, "xp": xp, "wealth": wealth}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "family", EventType: "family.tutor", EntityType: "birth_family", EntityID: fmt.Sprint(familyID), SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

// ---- the errands ----------------------------------------------------------

type familyErrandPayload struct {
	GameMinute int64 `json:"game_minute"`
}

// familyErrandActionGo is the household asking something of you. The pool
// is content (`household_errands`, keyed by the trade the house teaches);
// each errand is an ordinary quest handed over once, and a house asks for
// one thing at a time.
func familyErrandActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p familyErrandPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	familyID, err := requireAtHomeTx(conn, userID, "an errand")
	if err != nil {
		return authoritativeMutation{}, err
	}
	fam, err := birthFamilyForUserGo(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	trade := householdTradeFor(catalog, fmt.Sprint(fam["archetype"]))
	pool := catalog.HouseholdErrands[trade]
	if trade == "" || len(pool) == 0 {
		return authoritativeMutation{}, errors.New("this household has nothing to ask of anybody")
	}
	if !tableExistsTx(conn, "character_quests") {
		return authoritativeMutation{}, errors.New("quests are not available yet")
	}
	active, err := conn.Execute(`SELECT quest_key FROM character_quests WHERE user_id=? AND status='active' AND quest_key LIKE ? LIMIT 1`, []any{userID, householdErrandPrefix + "%"})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(active.Rows) > 0 {
		return authoritativeMutation{}, fmt.Errorf("finish the errand you already carry (%s) before the household asks another", fmt.Sprint(active.Rows[0][0]))
	}
	for _, errand := range pool {
		granted, err := grantOrdinaryQuestTx(conn, userID, errand.QuestKey, p.GameMinute)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if !granted {
			continue
		}
		out := map[string]any{"family_id": familyID, "family_name": fmt.Sprint(fam["family_name"]), "quest_key": errand.QuestKey, "title": errand.Title, "opening": errand.Opening, "trade": trade}
		return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "family", EventType: "family.errand", EntityType: "birth_family", EntityID: fmt.Sprint(familyID), SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
	}
	return authoritativeMutation{}, errors.New("the household has nothing more to ask of you")
}

// householdErrandCompletedTx is what a finished errand leaves behind: the
// standing the definition promised, and a line in the family's chronicle.
// Only an errand's key may carry `household_standing`; on anything else the
// reward is ignored here and refused by the Python validator.
func householdErrandCompletedTx(conn *storage.Conn, userID int64, questKey string, rewards map[string]any) (int64, error) {
	if !strings.HasPrefix(questKey, householdErrandPrefix) {
		return 0, nil
	}
	r, err := conn.Execute(`SELECT cbf.family_id,c.name,COALESCE(q.title,'') FROM character_birth_family cbf JOIN characters c ON c.user_id=cbf.user_id LEFT JOIN quest_definitions q ON q.quest_key=? WHERE cbf.user_id=?`, []any{questKey, userID})
	if err != nil || len(r.Rows) == 0 {
		return 0, err
	}
	familyID := i64(r.Rows[0][0])
	standing := clamp(i64(rewards["household_standing"]), 0, 25)
	if standing > 0 {
		if err = adjustReputationGo(conn, userID, householdStandingKey(familyID), standing, "brought a household errand home"); err != nil {
			return 0, err
		}
	}
	title := fmt.Sprint(r.Rows[0][2])
	if title == "" {
		title = questKey
	}
	return standing, appendFamilyHistoryTx(conn, familyID, fmt.Sprintf("%s brought home what the household asked for: %s.", fmt.Sprint(r.Rows[0][1]), title))
}

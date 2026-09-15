package game

// The path a new cultivator is put on (v1.0.0-rc.26).
//
// `first_steps` - "First Steps Beneath Heaven" - has been in this repo since
// before the Quest Forge, with exactly the right three objectives, and no
// player has ever held it. It is seeded into `quest_definitions` on every
// boot, and it is offered in `/quests`; what does not exist is any path that
// hands it to anybody. The only two statements in the engine that write a
// `character_quests` row are both in commission_actions.go and both want a
// giver, which the static quests deliberately do not have. So a new player is
// not short of a quest, they are short of being given one - and `/quests` is
// one of sixteen equally-weighted doors with nothing saying it is theirs.
//
// This hands over the first stage at creation, and each stage hands over the
// next as it completes. What it does *not* do is invent a second quest
// mechanism: a stage is an ordinary `quest_definitions` row seeded from
// content the same way the authored commission pool is, and once handed over
// it is pinned, progressed, completed and paid by exactly the code every
// other quest uses. A beginner quest is only special in who gives it to you.

import (
	"encoding/json"
	"fmt"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// grantOrdinaryQuestTx hands a player a quest that has no giver.
//
// Reports whether it actually granted one, and refuses in three ways that are
// all "no", never an error: the tables are not there yet, the definition is
// not there yet, or they have held it before. That matters most at character
// creation - a world whose content has not finished seeding must still be
// able to make a character, so a missing definition costs the player a quest
// and never the character they were making.
//
// Holding it *before* counts, including having finished or abandoned it. The
// row is the memory: `character_quests` is keyed (user_id, quest_key), so
// "fires once" is the primary key rather than a flag somebody has to check.
// That is also what makes this safe to call from creation, a dao-family
// rebirth and a samsara return alike, the way teachHouseholdMethodsTx is.
func grantOrdinaryQuestTx(conn *storage.Conn, userID int64, questKey string, gameMinute int64) (bool, error) {
	if questKey == "" || !tableExistsTx(conn, "quest_definitions") || !tableExistsTx(conn, "character_quests") {
		return false, nil
	}
	held, err := conn.Execute(`SELECT 1 FROM character_quests WHERE user_id=? AND quest_key=?`, []any{userID, questKey})
	if err != nil {
		return false, err
	}
	if len(held.Rows) > 0 {
		return false, nil
	}
	// A giver means a commission, and a commission is something an NPC offers
	// you in person - it occupies the one-at-a-time slot and carries a
	// deadline. Nothing may hand one over behind the player's back.
	defined, err := conn.Execute(
		`SELECT COALESCE(giver_npc,'') AS giver FROM quest_definitions WHERE quest_key=?`, []any{questKey})
	if err != nil {
		return false, err
	}
	row := firstRowMap(defined)
	if row == nil {
		return false, nil
	}
	if fmt.Sprint(row["giver"]) != "" {
		return false, nil
	}
	if _, err := conn.Execute(`INSERT INTO character_quests(user_id,quest_key,status,progress_json,accepted_game_minute,commission,variant_index,created_at,updated_at)
        VALUES(?,?,'active','{}',?,0,0,?,?)`,
		[]any{userID, questKey, maxI64(0, gameMinute), nowSeconds(), nowSeconds()}); err != nil {
		return false, err
	}
	// Pinned for the same reason commissionAcceptAction pins: a later GM edit
	// to the definition must not rewrite what somebody is already carrying.
	pinned, _, err := questDefinitionTerms(conn, questKey, 0)
	if err != nil {
		return false, err
	}
	pinned.PinnedAtGameMinute = gameMinute
	if err := pinQuestTermsTx(conn, userID, questKey, pinned); err != nil {
		return false, err
	}
	return true, nil
}

// grantBeginnerPathTx starts a new cultivator on the first stage.
//
// The first stage is the first entry in the content, not a key written into
// the engine - an operator who reorders `beginner_path` reorders the path.
// An empty roster is a world that simply has no beginner path, which is a
// content decision and not a fault.
func grantBeginnerPathTx(conn *storage.Conn, catalog worlddata.Catalog, userID, gameMinute int64) (string, error) {
	if len(catalog.BeginnerPath) == 0 {
		return "", nil
	}
	first := catalog.BeginnerPath[0].QuestKey
	granted, err := grantOrdinaryQuestTx(conn, userID, first, gameMinute)
	if err != nil || !granted {
		return "", err
	}
	return first, nil
}

// questFollowOnTx is the next stage after this one, read off the definition's
// `seed_json` rather than off the content file.
//
// That is deliberate and it is the one place the chain is authoritative: the
// dashboard's quest workbench writes `seed_json`, so a GM who re-points a
// chain is obeyed, and the shipped `beginner_path` is only the starting
// shape. Reading the content here instead would silently overrule them on
// every completion.
func questFollowOnTx(conn *storage.Conn, questKey string) (string, error) {
	if !tableExistsTx(conn, "quest_definitions") {
		return "", nil
	}
	// `seed_json` arrived with the commissions migration, and an engine can be
	// pointed at a database Python has not migrated yet - the same reason
	// questProgress checks for `terms_json` before selecting it. A world with
	// no such column is a world with no chains, which is a true answer; asking
	// for it anyway would abort every quest completion in that database.
	chained, err := tableHasColumns(conn, "quest_definitions", "seed_json")
	if err != nil || !chained {
		return "", err
	}
	res, err := conn.Execute(`SELECT COALESCE(seed_json,'{}') FROM quest_definitions WHERE quest_key=?`, []any{questKey})
	if err != nil || len(res.Rows) == 0 {
		return "", err
	}
	var seed struct {
		FollowOn string `json:"follow_on"`
	}
	// A seed that is not an object, or has no follow_on, is not an error: most
	// quests have neither and never will.
	_ = json.Unmarshal([]byte(fmt.Sprint(res.Rows[0][0])), &seed)
	if seed.FollowOn == questKey {
		// A chain pointing at itself would re-grant a quest the player has
		// just finished, forever. Refuse it here rather than trusting whoever
		// wrote the content.
		return "", nil
	}
	return seed.FollowOn, nil
}

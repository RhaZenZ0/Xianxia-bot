package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// The last lesson (v1.0.0-rc.34).
//
// The beginner path walked a new cultivator out of the household, through the
// town and the road, and home again - and then stopped. Nobody in the house
// had ever spoken to them as a teacher: the send-off hands over an heirloom,
// one trade's entry methods and a tutoring band, and the head of the family
// (`birth_families.head_title` and `head_name`) was a line in `/family → View`
// and nothing else. And a fresh cultivator could craft only in the household's
// own trade: `craft.resolve` refuses any method they do not know, and the other
// three trades were bought into from slips.
//
// `family.lesson` is the head of the house speaking to their child, at home,
// once per life. It is one demonstration check - the attribute the family's
// trade lives on, against a target a fresh character clears about three times
// in four - and a failure costs one world day, never the path. Passing
// qualifies the cultivator at level 0 in all four trades (a profession record
// in each, never lowered, and every trade's entry methods), hands over the
// family's own manual and studies it once so its first technique is usable at
// once, tells the story of the house into its chronicle, and leaves a
// keepsake in the child's hands. Every line the head speaks is content
// (`birth_family_lesson` in world.json); nothing is decided in presentation.
//
// The record is the event log, not a column: `family.lesson` rows carry the
// attempt, its game minute and the life it belongs to (`soul_legacy.
// incarnation_count`, 1 in a first life), so samsara - which wipes the trades
// and the methods - lets a new life take the lesson again without deleting any
// log, and a pass is refused again only in the life that earned it.

const (
	// householdLessonEvent is the event_log type the lesson writes its
	// attempts under.
	householdLessonEvent = "family.lesson"
	// householdLessonTN is the target a demonstration must reach.
	householdLessonTN int64 = 10
	// householdLessonRetryGameMinutes is how long the head waits after a
	// failed demonstration: one world day, the sect trial's own wait.
	householdLessonRetryGameMinutes int64 = 1440
	// householdLessonStanding is what taking the lesson is worth to the house.
	householdLessonStanding int64 = 5
	// householdLessonStandingCap bounds what standing lends the roll.
	householdLessonStandingCap int64 = 2
	// householdLessonSource marks the methods the lesson taught.
	householdLessonSource = "family_lesson"
)

// householdLessonTrades are the four crafts, in the order the head names them.
var householdLessonTrades = []string{"Forging", "Inscription", "Formation", "Alchemy"}

// tradeAttribute is what a trade lives on: a smith's body, a scribe's
// steadiness, an array-setter's spirit, an alchemist's eye. The head of the
// house asks a child to show it (v1.0.0-rc.34) and a hall's keeper asks a
// candidate to show it at examination (v1.0.0-rc.45) - one statement of the
// rule, because two would be free to disagree about what forging is.
var tradeAttribute = map[string]string{
	"Forging":     "body",
	"Inscription": "will",
	"Formation":   "spirit",
	"Alchemy":     "insight",
}

type familyLessonPayload struct {
	GameMinute int64 `json:"game_minute"`
}

// householdLessonRecord is one attempt as the event log keeps it.
type householdLessonRecord struct {
	Result     string `json:"result"`
	GameMinute int64  `json:"game_minute"`
	Life       int64  `json:"life"`
	Total      int64  `json:"total"`
	TN         int64  `json:"tn"`
}

// soulLifeTx is which life this is: soul_legacy.incarnation_count, 1 for a
// soul that has never been reborn (and has no row).
func soulLifeTx(conn *storage.Conn, userID int64) int64 {
	if !tableExistsTx(conn, "soul_legacy") {
		return 1
	}
	r, err := conn.Execute(`SELECT incarnation_count FROM soul_legacy WHERE user_id=?`, []any{userID})
	if err != nil || len(r.Rows) == 0 {
		return 1
	}
	return maxI64(1, i64(r.Rows[0][0]))
}

// householdLessonRecordsTx reads this life's attempts: whether the lesson has
// been passed, and the most recent failure if there is one.
func householdLessonRecordsTx(conn *storage.Conn, userID, life int64) (passed bool, lastFail *householdLessonRecord, err error) {
	if !tableExistsTx(conn, "event_log") {
		return false, nil, nil
	}
	r, err := conn.Execute(`SELECT payload_json FROM event_log WHERE user_id=? AND event_type=? ORDER BY id DESC`, []any{userID, householdLessonEvent})
	if err != nil {
		return false, nil, err
	}
	for _, row := range r.Rows {
		var rec householdLessonRecord
		if json.Unmarshal([]byte(fmt.Sprint(row[0])), &rec) != nil || rec.Life != life {
			continue
		}
		if rec.Result == "pass" {
			return true, nil, nil
		}
		if rec.Result == "fail" && lastFail == nil {
			copied := rec
			lastFail = &copied
		}
	}
	return false, lastFail, nil
}

func recordHouseholdLessonTx(conn *storage.Conn, userID int64, rec householdLessonRecord) error {
	encoded, _ := json.Marshal(rec)
	_, err := conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`,
		[]any{userID, householdLessonEvent, string(encoded), nowSeconds()})
	return err
}

// teachTradeMethodsTx teaches every entry method of one craft - the recipes
// at MinLevel 0 plus the easiest one the world's own materials make - and says
// how many were new. Idempotent: a method already known is left as it is,
// whatever taught it first. The send-off calls it for the household's trade
// (source 'birth_family'); the head's lesson calls it for all four.
func teachTradeMethodsTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, trade, world, source string, gameMinute int64, now float64) (int64, error) {
	if trade == "" {
		return 0, nil
	}
	taught := map[string]bool{}
	for name, recipe := range catalog.Recipes {
		if recipe.Profession == trade && recipe.MinLevel <= 0 {
			taught[name] = true
		}
	}
	if local, ok := entryRecipeForWorld(catalog, trade, world); ok {
		taught[local] = true
	}
	names := make([]string, 0, len(taught))
	for name := range taught {
		names = append(names, name)
	}
	sort.Strings(names)
	learned := int64(0)
	for _, name := range names {
		res, err := conn.Execute(
			`INSERT INTO character_recipes(user_id,recipe,learned_game_minute,source,created_at)
			 VALUES(?,?,?,?,?) ON CONFLICT(user_id,recipe) DO NOTHING`,
			[]any{userID, name, maxI64(0, gameMinute), source, now})
		if err != nil {
			return learned, err
		}
		learned += res.RowsAffected
	}
	return learned, nil
}

// catchUpBeginnerPathTx hands over any beginner stage whose predecessor this
// character has completed and which they were never given - the grandfathering
// for a stage added after they finished the one before it. A boot migration
// cannot do this: the definitions are seeded by the bot after the engine
// starts, and `grantOrdinaryQuestTx` treats a missing one as "no". Done at the
// lesson's door instead, where it is the lesson stage that matters.
func catchUpBeginnerPathTx(conn *storage.Conn, catalog worlddata.Catalog, userID, gameMinute int64) ([]string, error) {
	handed := []string{}
	if !tableExistsTx(conn, "character_quests") {
		return handed, nil
	}
	for i := 1; i < len(catalog.BeginnerPath); i++ {
		previous, stage := catalog.BeginnerPath[i-1].QuestKey, catalog.BeginnerPath[i].QuestKey
		r, err := conn.Execute(`SELECT 1 FROM character_quests WHERE user_id=? AND quest_key=? AND status='completed'`, []any{userID, previous})
		if err != nil {
			return handed, err
		}
		if len(r.Rows) == 0 {
			continue
		}
		granted, err := grantOrdinaryQuestTx(conn, userID, stage, gameMinute)
		if err != nil {
			return handed, err
		}
		if granted {
			handed = append(handed, stage)
		}
	}
	return handed, nil
}

// familyLessonActionGo is the head of the house's last lesson and test.
func familyLessonActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p familyLessonPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	familyID, err := requireAtHomeTx(conn, userID, "the head's lesson")
	if err != nil {
		return authoritativeMutation{}, err
	}
	fam, err := birthFamilyForUserGo(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if fam == nil {
		return authoritativeMutation{}, errors.New("no birth family is recorded")
	}
	archetype := fmt.Sprint(fam["archetype"])
	lesson, ok := catalog.BirthFamilyLessons[archetype]
	if !ok {
		return authoritativeMutation{}, fmt.Errorf("this household has no lesson to give (%s)", archetype)
	}
	trade := householdTradeFor(catalog, archetype)
	attr, ok := tradeAttribute[trade]
	if trade == "" || !ok {
		return authoritativeMutation{}, errors.New("this household has no trade to test you in")
	}
	manual, ok := catalog.TechniqueSystem.Manuals[lesson.Manual]
	if !ok {
		return authoritativeMutation{}, fmt.Errorf("the household's manual is not in this world: %s", lesson.Manual)
	}
	if manualForbidden(manual) {
		return authoritativeMutation{}, fmt.Errorf("a household's lesson cannot be a forbidden manual: %s", lesson.Manual)
	}
	if lesson.Keepsake != "" {
		if _, known := catalog.Items[lesson.Keepsake]; !known {
			return authoritativeMutation{}, fmt.Errorf("the household's keepsake is not in this world: %s", lesson.Keepsake)
		}
	}
	for _, table := range []string{"profession_progress", "character_recipes", "character_manuals", "inventory"} {
		if !tableExistsTx(conn, table) {
			return authoritativeMutation{}, fmt.Errorf("%s is not available yet", table)
		}
	}

	life := soulLifeTx(conn, userID)
	passed, lastFail, err := householdLessonRecordsTx(conn, userID, life)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if passed {
		return authoritativeMutation{}, errors.New("the head has taught you all this lesson holds; the rest is practice")
	}
	if lastFail != nil && p.GameMinute-lastFail.GameMinute < householdLessonRetryGameMinutes {
		return authoritativeMutation{}, fmt.Errorf("the head will hear you again in %d in-world minutes", householdLessonRetryGameMinutes-(p.GameMinute-lastFail.GameMinute))
	}

	// The demonstration.
	value, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, attr)
	if err != nil {
		return authoritativeMutation{}, err
	}
	level := int64(0)
	if r, e := conn.Execute(`SELECT level FROM profession_progress WHERE user_id=? AND profession=?`, []any{userID, trade}); e == nil && len(r.Rows) > 0 {
		level = i64(r.Rows[0][0])
	}
	standing, err := householdStandingTx(conn, userID, familyID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	modifier := value + level + clamp(standing/10, 0, householdLessonStandingCap)
	roll, err := rollCheck(modifier, householdLessonTN)
	if err != nil {
		return authoritativeMutation{}, err
	}
	success, _ := roll["success"].(bool)
	rec := householdLessonRecord{Result: "fail", GameMinute: p.GameMinute, Life: life, Total: i64(roll["total"]), TN: householdLessonTN}
	if success {
		rec.Result = "pass"
	}
	if err = recordHouseholdLessonTx(conn, userID, rec); err != nil {
		return authoritativeMutation{}, err
	}

	headTitle := strings.TrimSpace(fmt.Sprint(fam["head_title"]))
	headName := strings.TrimSpace(fmt.Sprint(fam["head_name"]))
	if headTitle == "<nil>" {
		headTitle = ""
	}
	if headName == "<nil>" || headName == "" {
		headName = "the head of the house"
	}
	out := map[string]any{
		"family_id": familyID, "family_name": fmt.Sprint(fam["family_name"]),
		"head_title": headTitle, "head_name": headName, "trade": trade, "attribute": attr,
		"attribute_value": value, "trade_level": level, "standing_bonus": clamp(standing/10, 0, householdLessonStandingCap),
		"check": roll, "outcome": rec.Result, "retry_game_minutes": householdLessonRetryGameMinutes,
		"lines": map[string]any{"lesson": lesson.Lesson, "test": lesson.Test, "pass": lesson.Pass, "fail": lesson.Fail},
	}
	event := eventledger.Event{Domain: "family", EventType: householdLessonEvent, EntityType: "birth_family", EntityID: fmt.Sprint(familyID), SubjectType: "character", SubjectID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}
	if !success {
		return authoritativeMutation{Result: out, Event: event}, nil
	}

	// Qualified in all four trades.
	now := nowSeconds()
	world := householdWorldTx(conn, catalog, familyID)
	trades := make([]map[string]any, 0, len(householdLessonTrades))
	for _, craft := range householdLessonTrades {
		res, err := conn.Execute(`INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at) VALUES(?,?,0,0,0,0,0,?) ON CONFLICT(user_id,profession) DO NOTHING`, []any{userID, craft, now})
		if err != nil {
			return authoritativeMutation{}, err
		}
		methods, err := teachTradeMethodsTx(conn, catalog, userID, craft, world, householdLessonSource, p.GameMinute, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		trades = append(trades, map[string]any{"profession": craft, "new_record": res.RowsAffected > 0, "new_methods": methods})
	}
	// The technique of the house: its manual, handed over and studied once.
	if err = addInventoryTx(conn, userID, map[string]int64{manual.ItemID: 1}); err != nil {
		return authoritativeMutation{}, err
	}
	row, err := manualRow(conn, userID, lesson.Manual)
	if err != nil {
		return authoritativeMutation{}, err
	}
	firstStudy := row == nil
	if firstStudy {
		if _, err = conn.Execute(`INSERT INTO character_manuals(user_id,manual_id,mastery,practice,learned_at,updated_at) VALUES(?,?,0,0,?,?)`, []any{userID, lesson.Manual, now, now}); err != nil {
			return authoritativeMutation{}, err
		}
	}
	// The keepsake, the standing and the story.
	if lesson.Keepsake != "" {
		if err = addInventoryTx(conn, userID, map[string]int64{lesson.Keepsake: 1}); err != nil {
			return authoritativeMutation{}, err
		}
	}
	if err = adjustReputationGo(conn, userID, householdStandingKey(familyID), householdLessonStanding, "took the head's last lesson"); err != nil {
		return authoritativeMutation{}, err
	}
	name := ""
	if r, e := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{userID}); e == nil && len(r.Rows) > 0 {
		name = fmt.Sprint(r.Rows[0][0])
	}
	who := strings.TrimSpace(headTitle + " " + headName)
	if err = appendFamilyHistoryTx(conn, familyID, fmt.Sprintf("%s took the last lesson from %s and heard the story of the house.", name, who)); err != nil {
		return authoritativeMutation{}, err
	}
	handed, err := catchUpBeginnerPathTx(conn, catalog, userID, p.GameMinute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	standing, err = householdStandingTx(conn, userID, familyID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	out["trades"] = trades
	out["manual"] = map[string]any{"key": lesson.Manual, "name": manual.Name, "item_id": manual.ItemID, "first_study": firstStudy}
	out["keepsake"] = lesson.Keepsake
	out["story"] = lesson.Story
	out["standing"] = standing
	out["standing_gain"] = householdLessonStanding
	out["standing_band"] = householdStandingBand(standing)
	out["handed_over"] = handed
	event.Payload = out
	return authoritativeMutation{Result: out, Event: event}, nil
}

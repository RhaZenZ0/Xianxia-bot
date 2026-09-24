package game

// The examination that makes a rank mean something (v1.0.0-rc.45).
//
// A trade's rank rose on XP alone and nothing marked the moment. `crafting.resolve`
// added to `profession_progress.xp`, `advanceProfessionTx` carried the level up
// the ladder at `60 + level*40` a step, and that was the whole of a crafter's
// progression: a number went up in silence, six times, from Novice to Saint. The
// hundred and twenty hall keepers who sell a trade's method slips had no opinion
// of anybody, and the twenty-six recipes above level 0 were a shop transaction
// and nothing else - `character_recipes` has had exactly two writers, a bought
// slip and the household's one trade of entry methods.
//
// An examination is what a rank is worth. When a trade reaches a rank the
// content authors an examination for, the hall of that trade offers it: an
// ordinary giver-less quest handed over by `grantOrdinaryQuestTx`, sat at a
// shop of the matching kind, examined by that shop's own `keeper` - a catalogue
// NPC who already stands there rather than somebody invented for the occasion.
// Passing teaches the trade's recipes at that rank.
//
// Three rules hold it, and the first is the one that decides everything else.
//
// **It never blocks a level.** `advanceProfessionTx` is untouched: XP raises a
// rank exactly as it did, and the examination is what the rank is *worth*
// rather than a toll on reaching it. That is what makes the feature safe to
// add to a world already running - no live crafter loses a level they earned,
// and nobody needs grandfathering.
//
// **The examiner is content that already exists.** All 120 shops carry a named
// keeper and all 120 keepers are in `world.npcs`, so `hall_kind` picks a shop
// kind (`weaponsmith` -> Forging, `apothecary` -> Alchemy, `talisman` ->
// Inscription, `array` -> Formation) and the keeper standing in the one the
// candidate walked into is the examiner. Inventing an examiner would have meant
// a registry row, a schedule and a location for somebody the world already had.
//
// **The record is the event log, per life**, exactly as the household lesson's
// is: a pass is refused again only in the life that earned it, so samsara -
// which wipes `profession_progress` and `character_recipes` - lets a new life
// sit the same examination without deleting any history. No schema.

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

const (
	// professionExamEvent is the event_log type an attempt is written under.
	professionExamEvent = "profession.exam"
	// professionExamRetryGameMinutes is how long a hall makes a failed
	// candidate wait: one world day, the household lesson's own wait and the
	// sect trial's before it.
	professionExamRetryGameMinutes int64 = 1440
	// professionExamStanding is what passing is worth to the halls of that
	// trade, under `craft_hall:<trade>` in `faction_reputation` - a table that
	// imposes no vocabulary.
	professionExamStanding int64 = 5
	// professionExamSuccessBonus caps what a record of good work is worth on
	// the demonstration: a candidate who has made a hundred things is steadier
	// than one who has made ten, and not four ranks better.
	professionExamSuccessBonus int64 = 2
)

// professionExamFor is the examination the content authors for one rank of one
// trade, or false when it authors none. Ranks are sorted so a trade carrying
// two entries for one rank answers the same one on every run.
func professionExamFor(catalog worlddata.Catalog, trade string, rank int64) (worlddata.ProfessionExam, bool) {
	exams := append([]worlddata.ProfessionExam(nil), catalog.ProfessionExams[trade]...)
	sort.Slice(exams, func(a, b int) bool { return exams[a].Rank < exams[b].Rank })
	for _, exam := range exams {
		if exam.Rank == rank {
			return exam, true
		}
	}
	return worlddata.ProfessionExam{}, false
}

// offerProfessionExamTx hands over the examination for a rank just reached.
//
// Called from the one place a rank can rise. A trade with no authored
// examination at that rank, a quest already held, or a definition not seeded
// yet are all "no" rather than errors - the same three kinds of no
// `grantOrdinaryQuestTx` already gives, for the same reason: a craft must
// never fail because a quest could not be handed over.
func offerProfessionExamTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, trade string, rank, gameMinute int64) (string, error) {
	exam, authored := professionExamFor(catalog, trade, rank)
	if !authored || strings.TrimSpace(exam.QuestKey) == "" {
		return "", nil
	}
	granted, err := grantOrdinaryQuestTx(conn, userID, strings.TrimSpace(exam.QuestKey), gameMinute)
	if err != nil || !granted {
		return "", err
	}
	return exam.QuestKey, nil
}

// professionExamRecord is one attempt, as it sits in `event_log`.
type professionExamRecord struct {
	Trade      string `json:"trade"`
	Rank       int64  `json:"rank"`
	Life       int64  `json:"life"`
	GameMinute int64  `json:"game_minute"`
	Passed     bool   `json:"passed"`
}

// professionExamRecordsTx reads this life's attempts at one trade's rank:
// whether it has been passed, and the most recent failure if there is one.
func professionExamRecordsTx(conn *storage.Conn, userID, life int64, trade string, rank int64) (passed bool, lastFail *professionExamRecord, err error) {
	if !tableExistsTx(conn, "event_log") {
		return false, nil, nil
	}
	res, err := conn.Execute(
		`SELECT payload_json FROM event_log WHERE user_id=? AND event_type=? ORDER BY id DESC`,
		[]any{userID, professionExamEvent})
	if err != nil {
		return false, nil, err
	}
	for _, row := range res.Rows {
		var rec professionExamRecord
		if len(row) == 0 || json.Unmarshal([]byte(fmt.Sprint(row[0])), &rec) != nil {
			continue
		}
		if rec.Life != life || rec.Trade != trade || rec.Rank != rank {
			continue
		}
		if rec.Passed {
			return true, nil, nil
		}
		if lastFail == nil {
			copied := rec
			lastFail = &copied
		}
	}
	return false, lastFail, nil
}

func recordProfessionExamTx(conn *storage.Conn, userID int64, rec professionExamRecord) error {
	encoded, _ := json.Marshal(rec)
	_, err := conn.Execute(`INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)`,
		[]any{userID, professionExamEvent, string(encoded), nowSeconds()})
	return err
}

// teachRankRecipesTx teaches the trade's recipes at exactly this rank, and
// says how many were new.
//
// Exactly this rank, not every rank up to it: an examination certifies what a
// candidate has just become, and the ranks below it were certified by their own
// examinations or bought a slip at a time. Idempotent, like
// `teachTradeMethodsTx` - a method already known is left alone, so somebody who
// bought the slip early keeps their earlier `learned_game_minute`.
// worldOffers is everything the content says can be had inside one world
// without luck (v1.3.0): a shelf in that world, a guaranteed item in a room of
// a realm standing in it, the world's own tier materials, and the tier-flat
// forage makings. The forage rare pool is deliberately not counted - it is a
// chance, so a method hanging on it is a lottery - which is the same rule
// `test_a_method_can_be_made_where_it_is_sold.py` states for the slips.
func worldOffers(catalog worlddata.Catalog, world string) map[string]bool {
	found := map[string]bool{}
	for _, shop := range catalog.Shops {
		if shop.World != world {
			continue
		}
		for _, line := range shop.Sells {
			found[line.ItemID] = true
		}
	}
	for _, realm := range catalog.SecretRealms {
		if catalog.Locations[realm.Location].World != world {
			continue
		}
		for _, room := range realm.Rooms {
			for item := range room.Items {
				found[item] = true
			}
		}
	}
	for _, item := range catalog.EventSites.TierMaterials[world] {
		found[item] = true
	}
	for item := range catalog.ForageMaterials {
		found[item] = true
	}
	return found
}

// rankRecipesWhereTheyCanBeMade splits a trade's recipes at one rank into the
// ones the hall's world can supply the makings of and the ones it cannot
// (v1.3.0, on the owner's call). Before this a hall taught its whole rank
// wherever it stood, so a Mortal-World Journeyman was handed the Dawn Lotus
// Vitality Pill, whose herb is shelved from the Spiritual World up - a method
// that could not be made anywhere its holder could stand. What a hall
// withholds is still taught by its slip, sold only where the method can be
// made, or by a hall in a world that can.
func rankRecipesWhereTheyCanBeMade(catalog worlddata.Catalog, trade string, rank int64, world string) (teachable, withheld []string) {
	offers := worldOffers(catalog, world)
	for name, recipe := range catalog.Recipes {
		if recipe.Profession != trade || recipe.MinLevel != rank {
			continue
		}
		makeable := true
		for item := range recipe.Cost {
			if !offers[item] {
				makeable = false
				break
			}
		}
		if makeable {
			teachable = append(teachable, name)
		} else {
			withheld = append(withheld, name)
		}
	}
	sort.Strings(teachable)
	sort.Strings(withheld)
	return teachable, withheld
}

func teachRankRecipesTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, trade string, rank, gameMinute int64, now float64, world string) ([]string, []string, error) {
	names, withheld := rankRecipesWhereTheyCanBeMade(catalog, trade, rank, world)
	taught := make([]string, 0, len(names))
	for _, name := range names {
		res, err := conn.Execute(
			`INSERT INTO character_recipes(user_id,recipe,learned_game_minute,source,created_at)
			 VALUES(?,?,?,'exam',?) ON CONFLICT(user_id,recipe) DO NOTHING`,
			[]any{userID, name, gameMinute, now})
		if err != nil {
			return nil, nil, err
		}
		// Only what the conflict clause did not swallow is new, which is what
		// lets the reply name what the candidate actually gained rather than
		// listing methods they walked in already holding.
		if res.RowsAffected > 0 {
			taught = append(taught, name)
		}
	}
	return taught, withheld, nil
}

type professionExamPayload struct {
	GameMinute int64  `json:"game_minute"`
	Profession string `json:"profession"`
}

// professionExamAction sits a candidate at a hall's counter.
func professionExamAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p professionExamPayload
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &p); err != nil {
			return authoritativeMutation{}, err
		}
	}
	trade := strings.TrimSpace(p.Profession)
	if trade == "" {
		return authoritativeMutation{}, errors.New("which trade you are sitting for is required")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot sit an examination")
	}
	// The rank being sat for is the one they have reached, so an examination
	// is always for what the candidate already is. A trade nobody has practised
	// has no rank to certify.
	level, err := professionLevelTx(conn, userID, trade)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if level <= 0 {
		return authoritativeMutation{}, fmt.Errorf("no hall examines a %s who has not reached the first rank", strings.ToLower(trade))
	}
	exam, authored := professionExamFor(catalog, trade, level)
	if !authored {
		return authoritativeMutation{}, fmt.Errorf("no examination is held in %s at this rank", trade)
	}
	// The hall is the examiner. A candidate standing anywhere else is told
	// which counter to stand at rather than merely refused.
	_, shop, inside := shopAt(catalog, c.Location)
	if !inside || shop.Kind != exam.HallKind {
		return authoritativeMutation{}, fmt.Errorf("the %s examination is sat at a %s; you are at %s", strings.ToLower(exam.RankName), exam.Hall, c.Location)
	}
	life := soulLifeTx(conn, userID)
	passed, lastFail, err := professionExamRecordsTx(conn, userID, life, trade, level)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if passed {
		return authoritativeMutation{}, fmt.Errorf("you already hold this hall's %s certificate in %s", exam.RankName, trade)
	}
	if lastFail != nil {
		if waited := p.GameMinute - lastFail.GameMinute; waited < professionExamRetryGameMinutes {
			return authoritativeMutation{}, fmt.Errorf("the hall will look at you again in %d hours", maxI64(1, (professionExamRetryGameMinutes-waited)/60))
		}
	}
	// The fee is the world's own money, and it is spent on sitting rather than
	// on passing: a hall charges for the keeper's afternoon.
	currency, err := characterBaseCurrencyTx(conn, catalog, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	balance := int64(0)
	if exam.Fee > 0 {
		if balance, err = walletDeltaTx(conn, catalog, userID, currency, -exam.Fee, nowSeconds()); err != nil {
			return authoritativeMutation{}, fmt.Errorf("the examination fee is %d %s, which you do not have", exam.Fee, strings.ReplaceAll(currency, "_", " "))
		}
	} else if balance, err = walletBalanceTx(conn, userID, currency); err != nil {
		return authoritativeMutation{}, err
	}

	attribute := tradeAttribute[trade]
	if attribute == "" {
		attribute = "insight"
	}
	score, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, attribute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	steadiness, err := professionSuccessesTx(conn, userID, trade)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if steadiness = steadiness / 10; steadiness > professionExamSuccessBonus {
		steadiness = professionExamSuccessBonus
	}
	tn := exam.TN
	if tn <= 0 {
		tn = 12
	}
	roll, err := rollCheck(score+level+steadiness, tn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	success, _ := roll["success"].(bool)
	now := nowSeconds()
	if err = recordProfessionExamTx(conn, userID, professionExamRecord{
		Trade: trade, Rank: level, Life: life, GameMinute: p.GameMinute, Passed: success}); err != nil {
		return authoritativeMutation{}, err
	}

	out := map[string]any{
		"profession": trade, "rank": level, "rank_name": exam.RankName, "title": exam.Title,
		"hall": exam.Hall, "hall_kind": exam.HallKind, "location": c.Location, "shop": shop.Name,
		"examiner": shop.Keeper, "attribute": attribute, "tn": tn, "roll": roll,
		"fee": exam.Fee, "currency": currency, "balance": balance, "passed": success,
		"quest_key": exam.QuestKey, "opening": exam.Opening,
	}
	if !success {
		out["retry_game_minutes"] = professionExamRetryGameMinutes
		return authoritativeMutation{Result: out, Event: eventledger.Event{
			Domain: "profession", EventType: professionExamEvent, EntityType: "character",
			EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
	}
	taught, withheld, err := teachRankRecipesTx(conn, catalog, userID, trade, level, p.GameMinute, now, shop.World)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if _, err = adjustReputationTx(conn, userID, "craft_hall:"+trade, professionExamStanding,
		fmt.Sprintf("Passed the %s examination in %s", exam.RankName, trade), now); err != nil {
		return authoritativeMutation{}, err
	}
	out["recipes_taught"] = taught
	out["recipes_withheld"] = withheld
	out["hall_world"] = shop.World
	out["standing_gain"] = professionExamStanding
	return authoritativeMutation{Result: out, Event: eventledger.Event{
		Domain: "profession", EventType: professionExamEvent, EntityType: "character",
		EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}

// professionSuccessesTx is how many things a candidate has actually finished in
// this trade - the record the hall reads before it looks at the dice.
func professionSuccessesTx(conn *storage.Conn, userID int64, trade string) (int64, error) {
	res, err := conn.Execute(`SELECT successes FROM profession_progress WHERE user_id=? AND profession=?`, []any{userID, trade})
	if err != nil {
		return 0, err
	}
	if row := firstRowMap(res); row != nil {
		return i64(row["successes"]), nil
	}
	return 0, nil
}

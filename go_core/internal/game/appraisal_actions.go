package game

// Reading what a thing actually is.
//
// "Appraisal" has been one of the eight professions since the progression
// system was written (`app/rules/progression_systems.py`) and nothing in
// either language had ever granted a point of it, named a recipe for it, or
// rolled a check against it. `item_provenance.authenticity` was likewise a
// column every writer set to 100 and no rule ever read - a number printed by
// `/provenance` and consulted by nothing. A broker's goods now enter at a
// rolled authenticity (`authenticity.go`), a keeper pays a forgery's price for
// a forgery, and a reading is how a holder finds out which they are carrying.
//
// Both are load-bearing now, because the world's own people have started
// putting things under the hammer that they cannot read themselves. A blind
// lot says its grade and no more; what it *is* is a question the floor will
// answer for a fee, or that you can try to answer yourself.
//
// Knowing is per-person and permanent, shaped like `character_location_
// discoveries`: the second Nine-Echo Sword Tablet you meet, you read at a
// glance. That is what makes the profession worth levelling, and it is why a
// blind lot is only blind to the people who have not done the work.

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const appraisalProfession = "Appraisal"

// What a reading is worth. A hit teaches four times what a miss does - see the
// comment at the grant site.
const (
	appraisalReadingXP = int64(12)
	appraisalMissXP    = int64(3)
)

type appraisalPayload struct {
	ItemID    string `json:"item_id"`
	AuctionID int64  `json:"auction_id"`
	Paid      bool   `json:"paid"`
}

// appraisalTN is how hard a thing is to read. A legendary relic resists a
// glance; ordinary goods are not worth the roll.
func appraisalTN(item worlddata.Item) int64 {
	switch strings.ToLower(strings.TrimSpace(item.AuctionInterest)) {
	case "legendary":
		return 19
	case "special":
		return 15
	}
	return 11
}

// appraisalFee is what the floor's keeper charges to be certain. A quarter of
// what the thing is worth is steep on purpose: the free roll is the ordinary
// road, and paying is for when you cannot afford to be wrong.
func appraisalFee(item worlddata.Item) int64 {
	base := item.BasePrice
	if base <= 0 {
		base = maxI64(8, item.SectValue*8)
	}
	return maxI64(5, base/4)
}

func knowsItemTx(conn *storage.Conn, userID int64, itemID string) (bool, error) {
	return boolRow(conn, `SELECT 1 FROM character_item_appraisals WHERE user_id=? AND item_id=?`, []any{userID, itemID})
}

func recordAppraisalTx(conn *storage.Conn, userID int64, itemID, kind string, authenticity, gameMinute int64, now float64) error {
	_, err := conn.Execute(
		`INSERT OR IGNORE INTO character_item_appraisals(user_id,item_id,appraisal_kind,authenticity,appraised_game_minute,created_at) VALUES(?,?,?,?,?,?)`,
		[]any{userID, itemID, kind, authenticity, maxI64(0, gameMinute), now})
	return err
}

// appraisalSubject resolves what is being read: an item out of your own bag,
// or the lot standing open in front of you.
func appraisalSubject(conn *storage.Conn, catalog worlddata.Catalog, userID int64, location string, p appraisalPayload) (string, worlddata.Item, error) {
	itemID := strings.TrimSpace(p.ItemID)
	if p.AuctionID > 0 {
		res, err := conn.Execute(`SELECT item_id,active,house_id FROM auctions WHERE auction_id=?`, []any{p.AuctionID})
		if err != nil {
			return "", worlddata.Item{}, err
		}
		row := firstRowMap(res)
		if row == nil {
			return "", worlddata.Item{}, errors.New("no such lot")
		}
		if i64(row["active"]) != 1 {
			return "", worlddata.Item{}, errors.New("that lot has already been struck")
		}
		// "standing where it is being sold" was the comment and not the
		// check until v1.2.3: any lot in the world could be read from anywhere.
		if houseID, _, ok := catalogHouseAt(catalog, location); !ok || houseID != fmt.Sprint(row["house_id"]) {
			return "", worlddata.Item{}, errors.New("that lot is not on this floor")
		}
		itemID = fmt.Sprint(row["item_id"])
	}
	if itemID == "" {
		return "", worlddata.Item{}, errors.New("name an item or a lot to read")
	}
	item, ok := catalog.Items[itemID]
	if !ok {
		return "", worlddata.Item{}, errors.New("unknown item")
	}
	// Reading something out of your own bag requires carrying it; reading a
	// lot requires only standing where it is being sold.
	if p.AuctionID <= 0 {
		carried, err := boolRow(conn, `SELECT 1 FROM inventory WHERE user_id=? AND item_id=? AND quantity>0`, []any{userID, itemID})
		if err != nil {
			return "", worlddata.Item{}, err
		}
		if !carried {
			return "", worlddata.Item{}, errors.New("you are not carrying that")
		}
	}
	return itemID, item, nil
}

func appraisalAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p appraisalPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only a living cultivator can read a treasure")
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	itemID, item, err := appraisalSubject(conn, catalog, userID, c.Location, p)
	if err != nil {
		return authoritativeMutation{}, err
	}
	known, err := knowsItemTx(conn, userID, itemID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := nowSeconds()
	out := map[string]any{"item_id": itemID, "name": item.Name, "already_known": known, "paid": p.Paid}
	if known {
		out["reading"] = "known"
		out["grade"] = item.AuctionInterest
		return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "appraisal.read", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: gameMinute, Payload: out}}, nil
	}

	if p.Paid {
		// The floor's keeper, for a fee. Certain, and it teaches you nothing
		// you did not pay for: buying an answer is not practice.
		houseID, house, ok := catalogHouseAt(catalog, c.Location)
		if !ok {
			return authoritativeMutation{}, errors.New("no auction floor here keeps an appraiser")
		}
		currency := strings.TrimSpace(house.DefaultCurrency)
		if currency == "" {
			currency = "low_spirit_stone"
		}
		fee := appraisalFee(item)
		balance, err := walletDeltaTx(conn, catalog, userID, currency, -fee, now)
		if err != nil {
			return authoritativeMutation{}, fmt.Errorf("the appraisal costs %d and you cannot cover it", fee)
		}
		authenticity, held, aerr := itemAuthenticityTx(conn, userID, itemID)
		if aerr != nil {
			return authoritativeMutation{}, aerr
		}
		if err = recordAppraisalTx(conn, userID, itemID, "steward", authenticity, gameMinute, now); err != nil {
			return authoritativeMutation{}, err
		}
		out["reading"] = "certified"
		out["house_id"] = houseID
		out["house"] = house.Name
		out["fee"] = fee
		out["currency"] = currency
		out["balance"] = balance
		out["grade"] = item.AuctionInterest
		out["authenticity"] = authenticity
		if held {
			// The half of a reading nobody could get before: not only what
			// the thing is, but whether it is what it claims to be.
			out["authenticity_note"] = authenticityNote(authenticity)
			out["forgery"] = authenticity < authenticityForgery
		}
		return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "appraisal.read", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: gameMinute, Payload: out}}, nil
	}

	// Your own eyes, for nothing. Insight is the attribute every other
	// knowledge-flavoured verb in the game already rolls, and the Appraisal
	// profession is the practice that makes it stick.
	insight, err := canonicalAttribute(conn, catalog, userID, gameMinute, "insight")
	if err != nil {
		return authoritativeMutation{}, err
	}
	level := int64(0)
	if pr, err := conn.Execute(`SELECT level FROM profession_progress WHERE user_id=? AND profession=?`, []any{userID, appraisalProfession}); err == nil {
		if row := firstRowMap(pr); row != nil {
			level = i64(row["level"])
		}
	}
	roll, err := rollCheck(insight+level*2, appraisalTN(item))
	if err != nil {
		return authoritativeMutation{}, err
	}
	success := roll["success"].(bool)
	margin := roll["margin"].(int64)
	// A miss teaches less than a hit (v1.0.0-rc.19).
	//
	// rc.15 paid 5 against a hit's 12, on the reasoning that a wrong answer
	// teaches an appraiser as much as a right one. It does not: a botched
	// reading here is *confidently* wrong, and the appraiser walks away
	// believing something false. But it is not worth nothing either - the
	// hands still did the work - so the miss keeps a grant and the gap widens
	// to four to one. Finding the right answer is what moves the profession;
	// failing at it only nudges.
	//
	// The paid reading above still teaches nothing (buying an answer is not
	// practice) and a thing already known returns before this line.
	xp := appraisalMissXP
	quality := int64(0)
	if success {
		xp = appraisalReadingXP
		quality = maxI64(0, margin)
	}
	progress, err := advanceProfessionTx(conn, userID, appraisalProfession, success, xp, quality, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	out["roll"] = roll
	out["profession"] = progress
	switch {
	case success:
		authenticity, held, aerr := itemAuthenticityTx(conn, userID, itemID)
		if aerr != nil {
			return authoritativeMutation{}, aerr
		}
		if err = recordAppraisalTx(conn, userID, itemID, "insight", authenticity, gameMinute, now); err != nil {
			return authoritativeMutation{}, err
		}
		out["reading"] = "read"
		out["grade"] = item.AuctionInterest
		out["authenticity"] = authenticity
		if held {
			out["authenticity_note"] = authenticityNote(authenticity)
			out["forgery"] = authenticity < authenticityForgery
		}
	case margin >= -7:
		// Nothing learned, and you know that you learned nothing.
		out["reading"] = "inconclusive"
	default:
		// Confidently wrong. The mistake stays in the prose rather than the
		// table: nothing is written, so a second look can still find the
		// truth, but the reply says what they think they saw.
		out["reading"] = "misread"
		out["believed_grade"] = misreadGrade(item)
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "economy", EventType: "appraisal.read", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: gameMinute, Payload: out}}, nil
}

// misreadGrade is what a badly botched reading thinks it is looking at: always
// the wrong way round, because a confident mistake is more interesting than a
// random one - a treasure dismissed as junk, or junk mistaken for a treasure.
func misreadGrade(item worlddata.Item) string {
	if strings.EqualFold(strings.TrimSpace(item.AuctionInterest), "legendary") {
		return "ordinary"
	}
	return "legendary"
}

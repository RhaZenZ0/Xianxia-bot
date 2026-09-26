package game

import (
	"encoding/json"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A trade's goods need its certificate at a stall (v1.7.1, on the owner's
// call). An item a recipe makes is listed only by somebody who has passed an
// examination of that trade in this life, at any rank; a raw material is
// anybody's to sell. The records are written the way the examination writes
// them, through recordProfessionExamTx, so the test reads what production
// reads.

func certifyForStall(conn *storage.Conn, userID int64, trade string, rank, life int64) error {
	return recordProfessionExamTx(conn, userID, professionExamRecord{Trade: trade, Rank: rank, Life: life, Passed: true})
}

func stallCertFixture(t *testing.T) string {
	t.Helper()
	path := setupStallDB(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(43,'recovery_pill',3),(43,'spirit_herb',3)`)
	stallMust(t, path, "stall.open", 43, map[string]any{"name": "Hao's Table"})
	return path
}

func stallCertRecord(t *testing.T, path string, userID int64, rec professionExamRecord) {
	t.Helper()
	encoded, _ := json.Marshal(rec)
	batch4Exec(t, path, `INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,0)`, userID, professionExamEvent, string(encoded))
}

func TestAnUncertifiedSellerMayListARawMaterialButNotATradesGoods(t *testing.T) {
	path := stallCertFixture(t)
	_, err := stallAct(t, path, "stall.list", 43, map[string]any{"item_id": "recovery_pill", "quantity": 1, "unit_price": 8})
	if err == nil {
		t.Fatal("an uncertified cultivator listed a Recovery Pill; a trade's goods need its certificate")
	}
	for _, want := range []string{"Recovery Pill", "Alchemy", "certificate", "Apprentice", "apothecary"} {
		if !strings.Contains(err.Error(), want) {
			t.Fatalf("the refusal does not name %q: %v", want, err)
		}
	}
	if got := stallCount(t, path, `SELECT quantity FROM inventory WHERE user_id=43 AND item_id='recovery_pill'`); got != 3 {
		t.Fatalf("a refused listing took goods out of the bag: %d left of 3", got)
	}
	stallMust(t, path, "stall.list", 43, map[string]any{"item_id": "spirit_herb", "quantity": 1, "unit_price": 2})
}

func TestAnyRankOfTheTradeInThisLifeCertifies(t *testing.T) {
	for _, tc := range []struct {
		name    string
		rec     professionExamRecord
		allowed bool
	}{
		{"the first rank", professionExamRecord{Trade: "Alchemy", Rank: 1, Life: 1, Passed: true}, true},
		{"only the second rank", professionExamRecord{Trade: "Alchemy", Rank: 2, Life: 1, Passed: true}, true},
		{"a failed attempt", professionExamRecord{Trade: "Alchemy", Rank: 1, Life: 1, Passed: false}, false},
		{"another trade", professionExamRecord{Trade: "Forging", Rank: 1, Life: 1, Passed: true}, false},
		{"an earlier life", professionExamRecord{Trade: "Alchemy", Rank: 1, Life: 0, Passed: true}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			path := stallCertFixture(t)
			stallCertRecord(t, path, 43, tc.rec)
			_, err := stallAct(t, path, "stall.list", 43, map[string]any{"item_id": "recovery_pill", "quantity": 1, "unit_price": 8})
			if tc.allowed && err != nil {
				t.Fatalf("%s should certify the seller, and the listing was refused: %v", tc.name, err)
			}
			if !tc.allowed && (err == nil || !strings.Contains(err.Error(), "certificate")) {
				t.Fatalf("%s must not certify the seller; the listing answered %v", tc.name, err)
			}
		})
	}
}

// A pass in the life before this one does not carry: samsara wipes the trade,
// and the examination's own record is kept per life. Driven with a real
// incarnation count, so the life the rule reads is the one soul_legacy says.
func TestACertificateFromAnEarlierLifeDoesNotCarry(t *testing.T) {
	path := stallCertFixture(t)
	batch4Exec(t, path, `INSERT OR REPLACE INTO soul_legacy(user_id,incarnation_count) VALUES(43,2)`)
	stallCertRecord(t, path, 43, professionExamRecord{Trade: "Alchemy", Rank: 1, Life: 1, Passed: true})
	if _, err := stallAct(t, path, "stall.list", 43, map[string]any{"item_id": "recovery_pill", "quantity": 1, "unit_price": 8}); err == nil {
		t.Fatal("a certificate earned in the first life let the second life sell pills")
	}
	stallCertRecord(t, path, 43, professionExamRecord{Trade: "Alchemy", Rank: 1, Life: 2, Passed: true})
	stallMust(t, path, "stall.list", 43, map[string]any{"item_id": "recovery_pill", "quantity": 1, "unit_price": 8})
}

// The picker reads this so it never offers what the listing would refuse.
func TestTheStatusSaysWhatTheSellerMayList(t *testing.T) {
	path := stallCertFixture(t)
	status := stallQuery(t, path, "stall.status", 43)
	sellable, _ := status["sellable_items"].([]string)
	withheld, _ := status["uncertified_items"].([]string)
	if strings.Join(sellable, ",") != "spirit_herb" || strings.Join(withheld, ",") != "recovery_pill" {
		t.Fatalf("an uncertified seller carrying a herb and a pill: sellable %v, withheld %v", status["sellable_items"], status["uncertified_items"])
	}
	stallCertRecord(t, path, 43, professionExamRecord{Trade: "Alchemy", Rank: 1, Life: 1, Passed: true})
	status = stallQuery(t, path, "stall.status", 43)
	sellable, _ = status["sellable_items"].([]string)
	if strings.Join(sellable, ",") != "recovery_pill,spirit_herb" {
		t.Fatalf("a certified seller may list both, and the status offers %v", status["sellable_items"])
	}
}

// A player's stall deals in any grade (v1.7.0), and the town buys only what
// some shelf sells, so a grade no shelf carries is for cultivators alone. The
// engine playtest used to prove this with an uncertified keeper, which the
// certificate now refuses; a certified seller is staged here instead of rolled.
func TestTheTownNeverBuysAGradeNoShelfSells(t *testing.T) {
	path := setupStallDB(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'recovery_pill@high',2),(42,'recovery_pill',2)`)
	stallMust(t, path, "stall.open", 42, map[string]any{"name": "Lin's Table"})
	high := stallMust(t, path, "stall.list", 42, map[string]any{"item_id": "recovery_pill@high", "quantity": 1, "unit_price": 90})
	if high["npc_may_buy"] != false || i64(high["npc_ceiling"]) != 0 {
		t.Fatalf("the town may buy a High pill no shelf sells: %v", high)
	}
	low := stallMust(t, path, "stall.list", 42, map[string]any{"item_id": "recovery_pill", "quantity": 1, "unit_price": 8})
	if low["npc_may_buy"] != true {
		t.Fatalf("the town will not buy a Low pill the shelves sell dearer: %v", low)
	}
}

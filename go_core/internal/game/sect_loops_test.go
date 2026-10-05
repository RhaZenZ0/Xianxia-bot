package game

import (
	"fmt"
	"math"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// Loops a member could run for points and rank from nothing (v1.12.3), each
// found by playing the exchange as a person who wanted a promotion would.
// Every test states the loop and is drilled against the code it replaced.

func earnedOf(t *testing.T, conn *storage.Conn, userID int64) int64 {
	t.Helper()
	return i64(memberRow(t, conn, userID)["contribution_earned"])
}

func setEarned(t *testing.T, conn *storage.Conn, userID, earned int64) {
	t.Helper()
	if _, err := conn.Execute(`UPDATE sect_membership SET contribution_earned=? WHERE user_id=?`, []any{earned, userID}); err != nil {
		t.Fatal(err)
	}
}

func setResources(t *testing.T, conn *storage.Conn, resources int64) {
	t.Helper()
	if _, err := conn.Execute(`INSERT INTO sect_politics_state(sect_name,resources) VALUES('Azure Cloud Sect',?) ON CONFLICT(sect_name) DO UPDATE SET resources=excluded.resources`, []any{resources}); err != nil {
		t.Fatal(err)
	}
}

// Donate a Longevity Pill (no Mortal shelf, so no donation cap) for 1.5 times
// its sect value, redeem it for its plain sect value: 180 in, 120 out, pill
// kept, +60 a turn, and the lifetime count only ever went up.
//
// Drill: price the treasury redeem on the pressure multiplier alone again and
// it prints the points the loop netted.
func TestACraftedDonationCannotBeRedeemedForLess(t *testing.T) {
	conn := sectExchangeWorld(t)
	catalog := shippedCatalog(t)
	setResources(t, conn, 90) // a sect that is not short: pressure 1.0
	if err := recordProfessionExamTx(conn, 1, professionExamRecord{Trade: "Alchemy", Rank: 1, Life: 1, Passed: true}); err != nil {
		t.Fatal(err)
	}
	if _, capped := sectDonationCap(catalog, "longevity_pill"); capped || itemTrade(catalog, "longevity_pill") != "Alchemy" {
		t.Fatal("the Longevity Pill is no longer an uncapped Alchemy output; pick another item that shows the loop")
	}
	startPoints, startEarned := int64(0), earnedOf(t, conn, 1)
	setPoints(t, conn, 1, startPoints)
	for turn := 1; turn <= 10; turn++ {
		if err := addInventoryTx(conn, 1, map[string]int64{"longevity_pill": 1}); err != nil {
			t.Fatal(err)
		}
		gave, err := sectOp(t, conn, "sect.contribute", 1, map[string]any{"item_id": "longevity_pill", "quantity": 1})
		if err != nil {
			t.Fatal(err)
		}
		if gave["crafted_bonus"] == nil {
			t.Fatal("the donation earned no crafted bonus; the loop this test guards is not being run")
		}
		took, err := sectOp(t, conn, "sect.redeem", 1, map[string]any{"item_id": "longevity_pill", "quantity": 1})
		if err != nil {
			t.Fatalf("turn %d: the redeem was refused, so the loop is not being run: %v", turn, err)
		}
		if i64(took["cost"]) < i64(gave["points"]) {
			t.Fatalf("a Longevity Pill was donated for %v points and redeemed for %v: the loop nets %d a turn with the pill kept",
				gave["points"], took["cost"], i64(gave["points"])-i64(took["cost"]))
		}
		if pts := i64(memberRow(t, conn, 1)["contribution_points"]); pts > startPoints {
			t.Fatalf("after %d turns the member holds %d points from %d", turn, pts, startPoints)
		}
		if got := earnedOf(t, conn, 1); got > startEarned {
			t.Fatalf("after %d turns the lifetime count is %d from %d: what came back out of the treasury was counted as a contribution", turn, got, startEarned)
		}
	}
}

// One spirit herb donated and redeemed, four hundred times: the lifetime count
// rose on every donation and a redeem never lowered it, so Inner Disciple was
// reached at zero points.
//
// Drill: take the lifetime debit out of the treasury redeem and it prints the
// rank reached and the count.
func TestALoopOfOneHerbNeverPromotes(t *testing.T) {
	conn := sectExchangeWorld(t)
	catalog := shippedCatalog(t)
	setResources(t, conn, 90)
	inner := catalog.SectExchange().Promotion[0]
	if _, err := conn.Execute(`UPDATE sect_membership SET rank_level=0,rank_name='Disciple' WHERE user_id=1`, nil); err != nil {
		t.Fatal(err)
	}
	// Points bought some other way, so the redeem is affordable: the loop's
	// whole claim is that it moves the lifetime count, not the balance.
	setPoints(t, conn, 1, 5000)
	for turn := 1; turn <= int(inner.Earned)+50; turn++ {
		if err := addInventoryTx(conn, 1, map[string]int64{"spirit_herb": 1}); err != nil {
			t.Fatal(err)
		}
		if _, err := sectOp(t, conn, "sect.contribute", 1, map[string]any{"item_id": "spirit_herb", "quantity": 1}); err != nil {
			t.Fatal(err)
		}
		if _, err := sectOp(t, conn, "sect.redeem", 1, map[string]any{"item_id": "spirit_herb", "quantity": 1}); err != nil {
			t.Fatalf("turn %d: the redeem was refused, so the loop is not being run: %v", turn, err)
		}
		if rank := i64(memberRow(t, conn, 1)["rank_level"]); rank != 0 {
			t.Fatalf("turn %d: a herb donated and taken back promoted to rank %d with the lifetime count at %d and %v points",
				turn, rank, earnedOf(t, conn, 1), memberRow(t, conn, 1)["contribution_points"])
		}
	}
	if got := earnedOf(t, conn, 1); got != 0 {
		t.Fatalf("the lifetime count is %d after a loop that contributed nothing", got)
	}
}

// Only the treasury's redeem is a contribution coming back out. Issued stock
// is the sect's to make and never a donation, so it leaves the count alone.
func TestAnIssuedRedeemLeavesTheLifetimeCountAlone(t *testing.T) {
	conn := sectExchangeWorld(t)
	setPoints(t, conn, 1, 500)
	setEarned(t, conn, 1, 500)
	if _, err := sectOp(t, conn, "sect.redeem", 1, map[string]any{"item_id": "swift_wind_talisman", "quantity": 1, "source": "issued"}); err != nil {
		t.Fatal(err)
	}
	if got := earnedOf(t, conn, 1); got != 500 {
		t.Fatalf("an issued redeem moved the lifetime count from 500 to %d", got)
	}
}

// What a redeem takes off the count is floored at zero.
func TestTheLifetimeCountNeverGoesBelowZero(t *testing.T) {
	conn := sectExchangeWorld(t)
	setPoints(t, conn, 1, 1000)
	setEarned(t, conn, 1, 3)
	if _, err := conn.Execute(`INSERT INTO sect_treasury(sect_name,item_id,quantity) VALUES('Azure Cloud Sect','spirit_herb',2)`, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := sectOp(t, conn, "sect.redeem", 1, map[string]any{"item_id": "spirit_herb", "quantity": 2}); err != nil {
		t.Fatal(err)
	}
	if got := earnedOf(t, conn, 1); got != 0 {
		t.Fatalf("the lifetime count is %d after a redeem dearer than it", got)
	}
}

// The treasury's listed price is the one the redeem charges - the bot prints
// it, and a page that quoted less than the action takes is a promise broken.
func TestTheTreasuryListsWhatTheRedeemCharges(t *testing.T) {
	conn := sectExchangeWorld(t)
	catalog := shippedCatalog(t)
	setResources(t, conn, 90)
	if _, err := conn.Execute(`INSERT INTO sect_treasury(sect_name,item_id,quantity) VALUES('Azure Cloud Sect','longevity_pill',1)`, nil); err != nil {
		t.Fatal(err)
	}
	q, err := sectExchangeQuery(conn, catalog, 1)
	if err != nil {
		t.Fatal(err)
	}
	rows, _ := q["treasury"].([]map[string]any)
	if len(rows) != 1 {
		t.Fatalf("the treasury lists %v", q["treasury"])
	}
	setPoints(t, conn, 1, 10000)
	out, err := sectOp(t, conn, "sect.redeem", 1, map[string]any{"item_id": "longevity_pill", "quantity": 1})
	if err != nil {
		t.Fatal(err)
	}
	if i64(rows[0]["unit_cost"]) != i64(out["unit_cost"]) {
		t.Fatalf("the treasury lists a Longevity Pill at %v and the redeem charges %v", rows[0]["unit_cost"], out["unit_cost"])
	}
}

// A GM who demotes a Core Disciple is not undone by the next donation: a
// promotion is paid when a credit carries the count over a rung, not whenever
// the count stands past one.
//
// Drill: read "is the count past a rung" again and it prints the rank the
// next credit restored.
func TestADemotionSurvivesTheNextCredit(t *testing.T) {
	conn := sectExchangeWorld(t)
	catalog := shippedCatalog(t)
	core := catalog.SectExchange().Promotion[1]
	setEarned(t, conn, 1, core.Earned+500)
	if promoted, err := creditSectContributionTx(conn, catalog, 1, 5, 0); err != nil || promoted != "" {
		t.Fatalf("a credit to a member the GM had placed below their count promoted to %q (%v)", promoted, err)
	}
	if rank := i64(memberRow(t, conn, 1)["rank_level"]); rank != 10 {
		t.Fatalf("a demoted member was put back to rank %d by the next credit", rank)
	}
	// A credit that carries the count over a rung makes the member eligible
	// for the rung above where they stand (v1.25.0) and still moves no rank.
	setEarned(t, conn, 1, catalog.SectExchange().Promotion[0].Earned-1)
	if eligible, _ := creditSectContributionTx(conn, catalog, 1, 1, 0); eligible != "Inner Disciple" {
		t.Fatalf("a credit across the Inner rung made the member eligible for %q", eligible)
	}
	if rank := i64(memberRow(t, conn, 1)["rank_level"]); rank != 10 {
		t.Fatalf("a credit moved the rank to %d", rank)
	}
}

// Moving a member to a different sect starts that sect's count; the same sect
// keeps both, and the snapshot holds both as they were.
//
// Drill: drop the reset and it prints the points and count the new sect kept.
func TestAGMPlacementInAnotherSectStartsTheCountOver(t *testing.T) {
	path := setupAdminDB(t)
	batch4Exec(t, path, `INSERT INTO sects(sect_name,updated_at) VALUES('Azure Cloud Sect',0)`)
	batch4Exec(t, path, `INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at,contribution_points,contribution_earned) VALUES(42,'Azure Cloud Sect','Core Disciple',30,0,700,3100)`)
	applyAdmin(t, path, "admin.player.set_sect", map[string]any{"user_id": 42, "sect_name": "Azure Cloud Sect", "rank_name": "Outer Disciple", "rank_level": 10, "reason": "same sect, demoted"})
	if p, e := i64(scalar(t, path, "SELECT contribution_points FROM sect_membership WHERE user_id=42")), i64(scalar(t, path, "SELECT contribution_earned FROM sect_membership WHERE user_id=42")); p != 700 || e != 3100 {
		t.Fatalf("a placement in the same sect moved the count to %d points and %d earned, want 700 and 3100", p, e)
	}
	applyAdmin(t, path, "admin.player.set_sect", map[string]any{"user_id": 42, "sect_name": "Crimson Furnace Sect", "rank_name": "Outer Disciple", "rank_level": 10, "reason": "another sect"})
	if p, e := i64(scalar(t, path, "SELECT contribution_points FROM sect_membership WHERE user_id=42")), i64(scalar(t, path, "SELECT contribution_earned FROM sect_membership WHERE user_id=42")); p != 0 || e != 0 {
		t.Fatalf("a placement in another sect left %d points and %d earned from the old one", p, e)
	}
	snapshot := fmt.Sprint(scalar(t, path, "SELECT before_json FROM admin_audit_log WHERE action='admin.player.set_sect' ORDER BY audit_id DESC LIMIT 1"))
	for _, want := range []string{`"contribution_points":700`, `"contribution_earned":3100`} {
		if !strings.Contains(snapshot, want) {
			t.Fatalf("the audit snapshot %s does not carry %s", snapshot, want)
		}
	}
}

// A client asking for more than the engine issues at once is refused, never
// clamped and never wrapped. 683212743470724134 of a two-point item cost two
// points and paid that many talismans when the product overflowed.
//
// Drill: remove the quantity bound and the overflow guard and it prints the
// talismans the bag received.
func TestAnIssuedRedeemHasAnUpperBound(t *testing.T) {
	conn := sectExchangeWorld(t)
	catalog := shippedCatalog(t)
	unit := sectIssuedPrice(catalog, "swift_wind_talisman")
	setPoints(t, conn, 1, 100000)
	for _, quantity := range []int64{sectIssuedMaxQuantity + 1, 683212743470724134, math.MaxInt64/unit + 1, math.MaxInt64} {
		_, err := sectOp(t, conn, "sect.redeem", 1, map[string]any{"item_id": "swift_wind_talisman", "quantity": quantity, "source": "issued"})
		if err == nil {
			t.Fatalf("an issued redeem of %d was accepted; the bag holds %d talismans", quantity, carriedBy(t, conn, 1, "swift_wind_talisman"))
		}
	}
	if got := carriedBy(t, conn, 1, "swift_wind_talisman"); got != 0 {
		t.Fatalf("refused redeems put %d talismans in the bag", got)
	}
	if got := i64(memberRow(t, conn, 1)["contribution_points"]); got != 100000 {
		t.Fatalf("refused redeems spent points: %d left", got)
	}
	if _, err := sectOp(t, conn, "sect.redeem", 1, map[string]any{"item_id": "swift_wind_talisman", "quantity": sectIssuedMaxQuantity, "source": "issued"}); err != nil {
		t.Fatalf("the most the engine issues at once was refused: %v", err)
	}
}

// A stall never lists above the range the Discord command offers, and a
// listing row from before the bound cannot wrap a buyer's charge.
//
// Drill: remove the bound and it prints the price the stall accepted.
func TestAStallListingHasAnUpperPrice(t *testing.T) {
	path := setupStallDB(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'recovery_pill',5)`)
	stallMust(t, path, "stall.open", 42, map[string]any{"name": "Lin's Table"})
	for _, price := range []int64{stallMaxUnitPrice + 1, math.MaxInt64, 1 << 62} {
		if _, err := stallAct(t, path, "stall.list", 42, map[string]any{"item_id": "recovery_pill", "quantity": 3, "unit_price": price}); err == nil {
			t.Fatalf("a stall accepted a unit price of %d", price)
		}
	}
	if got := stallCount(t, path, `SELECT quantity FROM inventory WHERE user_id=42 AND item_id='recovery_pill'`); got != 5 {
		t.Fatalf("refused listings took goods: %d left of 5", got)
	}
	if n := stallCount(t, path, `SELECT COUNT(*) FROM stall_listings`); n != 0 {
		t.Fatalf("%d listings were written by refused requests", n)
	}
	stallMust(t, path, "stall.list", 42, map[string]any{"item_id": "recovery_pill", "quantity": 3, "unit_price": stallMaxUnitPrice})
}

func TestAStallLineTotalCannotWrap(t *testing.T) {
	if _, err := stallLineTotal(stallMaxUnitPrice, math.MaxInt64); err == nil {
		t.Fatal("a line total that overflows was answered")
	}
	if _, err := stallLineTotal(math.MaxInt64/2, 3); err == nil {
		t.Fatal("a unit price beyond the bound was answered")
	}
	if total, err := stallLineTotal(40, 3); err != nil || total != 120 {
		t.Fatalf("40 x 3 = %d (%v)", total, err)
	}
}

// Drive one event to the cap, then interfere three times and support: the
// support used to pay the three points the interference had taken off the
// running total, and that could be repeated for ever.
//
// Drill: pay off the running contribution again and it prints the points the
// support paid at the cap.
func TestInterferingAtTheCapDoesNotRefillTheAllowance(t *testing.T) {
	conn := sectExchangeWorld(t)
	catalog := shippedCatalog(t)
	home := EraWorldOf(catalog, sectGate(catalog, "Azure Cloud Sect"))
	near := ""
	for name := range catalog.Locations {
		if EraWorldOf(catalog, name) == home && (near == "" || name < near) {
			near = name
		}
	}
	if _, err := conn.Execute(`INSERT INTO world_events(event_key,location) VALUES('near',?)`, []any{near}); err != nil {
		t.Fatal(err)
	}
	limit := catalog.SectExchange().Earning.EventPointsCap
	out, err := sectEventPointsTx(conn, catalog, 1, "near", limit, limit)
	if err != nil || out == nil || i64(out["points"]) != limit {
		t.Fatalf("driving an event to its cap paid %v (%v), want %d", out, err, limit)
	}
	total := limit
	for round := 1; round <= 5; round++ {
		total -= 3 // three interferences, each a point off the running contribution, each paying nothing
		total += 3 // one support, three points on
		if out, err = sectEventPointsTx(conn, catalog, 1, "near", 3, total); err != nil || out != nil {
			t.Fatalf("round %d: a support at the cap paid %v (%v) for points an interference had only taken back", round, out, err)
		}
	}
	// Below the cap a dip is not paid twice either: what was paid is the most
	// that total has been worth.
	if _, err := conn.Execute(`DELETE FROM event_log`, nil); err != nil {
		t.Fatal(err)
	}
	first, _ := sectEventPointsTx(conn, catalog, 1, "near", 6, 6)
	if first == nil || i64(first["points"]) != 6 {
		t.Fatalf("a first action paid %v, want 6", first)
	}
	if again, _ := sectEventPointsTx(conn, catalog, 1, "near", 3, 6-3+3); again != nil {
		t.Fatalf("a recovered dip paid %v", again)
	}
	if next, _ := sectEventPointsTx(conn, catalog, 1, "near", 3, 9); next == nil || i64(next["points"]) != 3 {
		t.Fatalf("a real gain past the old high paid %v, want 3", next)
	}
}

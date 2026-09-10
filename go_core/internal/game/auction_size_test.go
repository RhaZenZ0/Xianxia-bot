package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A smaller city has a smaller house (v0.33.1): the floor holds only so many
// lots at once and none for longer than its limit. Both come from content,
// so a house without them is uncapped - which is what every fixture before
// this release described.
func setupAuctionSizeDB(t *testing.T) string {
	t.Helper()
	path := setupEscrowDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// The seller stands on a local floor with plenty to sell.
	if err := conn.ExecScript(`
UPDATE characters SET location='Riverguard Riverside Auction Hall' WHERE user_id=42;
INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'nine_yang_fragment',50) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=50;
`); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

var auctionSizeSeq int

func sellOneLot(t *testing.T, path, world string, minutes float64) error {
	t.Helper()
	auctionSizeSeq++
	raw, _ := json.Marshal(map[string]any{"item_id": "nine_yang_fragment", "quantity": 1, "currency_id": "low_spirit_stone", "starting_bid": 5, "ends_at": nowSeconds() + minutes*60})
	_, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: fmt.Sprintf("auction-size-%d", auctionSizeSeq), Operation: "auction.sell", ActorID: 42, Payload: raw})
	return err
}

func TestALocalFloorHoldsSixLotsAndNoneLongerThanSixHours(t *testing.T) {
	path := setupAuctionSizeDB(t)
	world := batch4WorldPath(t)
	for i := 0; i < 6; i++ {
		if err := sellOneLot(t, path, world, 60); err != nil {
			t.Fatalf("lot %d refused on a local floor: %v", i+1, err)
		}
	}
	err := sellOneLot(t, path, world, 60)
	if err == nil || !strings.Contains(err.Error(), "at most 6 lots") {
		t.Fatalf("a seventh lot should be refused with the house's limit named, got %v", err)
	}
	if got := escrowScalar(t, path, "SELECT COUNT(*) FROM auctions WHERE active=1"); got != 6 {
		t.Fatalf("active lots=%d, want 6", got)
	}
	// A lot that outruns the floor's limit is refused before anything is listed.
	batch4Exec(t, path, "UPDATE auctions SET active=0")
	err = sellOneLot(t, path, world, 6*60+5)
	if err == nil || !strings.Contains(err.Error(), "at most 360 minutes") {
		t.Fatalf("a seven-hour lot should be refused on a local floor, got %v", err)
	}
	if err := sellOneLot(t, path, world, 6*60); err != nil {
		t.Fatalf("a six-hour lot is within the limit: %v", err)
	}
}

func TestAGrandHouseTakesMoreAndLonger(t *testing.T) {
	path := setupAuctionSizeDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, "UPDATE characters SET location='Golden Pavilion Auction House' WHERE user_id=42")
	for i := 0; i < 7; i++ {
		if err := sellOneLot(t, path, world, 24*60); err != nil {
			t.Fatalf("lot %d refused at a grand house: %v", i+1, err)
		}
	}
}

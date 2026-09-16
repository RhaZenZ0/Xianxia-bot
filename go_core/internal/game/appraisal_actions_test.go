package game

import (
	"fmt"
	"strings"
	"testing"
)

// Appraisal (v1.0.0-rc.15).
//
// "Appraisal" has been one of the eight professions since the progression
// system was written and nothing in either language ever granted a point of
// it. `item_provenance.authenticity` has been a column every writer sets to
// 100 and no rule ever read. Both matter now, because the world's own people
// have started consigning things they cannot read themselves.

func TestReadingSomethingTeachesYouItForGood(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	// An insight-100 character reads anything; the point here is that the
	// knowledge sticks.
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'nine_echo_sword_tablet',1)`)
	batch4SetCanonicalGameMinute(t, path, 3000)

	first := batch4Result(t, batch4Apply(t, path, world, "appraisal.read", 1, map[string]any{"item_id": "nine_echo_sword_tablet"}))
	if first["already_known"] != false {
		t.Fatalf("it should not be known before the first look: %v", first)
	}
	if fmt.Sprint(first["reading"]) != "read" {
		t.Fatalf("an insight-100 cultivator failed to read it: %v", first)
	}
	if got := i64(actionScalar(t, path, `SELECT COUNT(*) FROM character_item_appraisals WHERE user_id=42 AND item_id='nine_echo_sword_tablet'`)); got != 1 {
		t.Fatalf("nothing was learned: %d", got)
	}
	// The second one of these you meet, you read at a glance.
	second := batch4Result(t, batch4Apply(t, path, world, "appraisal.read", 2, map[string]any{"item_id": "nine_echo_sword_tablet"}))
	if second["already_known"] != true || fmt.Sprint(second["reading"]) != "known" {
		t.Fatalf("the knowledge did not stick: %v", second)
	}
	// And practising it is what finally moves the Appraisal profession, which
	// nothing in the game had ever granted a point of.
	if got := i64(actionScalar(t, path, `SELECT xp FROM profession_progress WHERE user_id=42 AND profession='Appraisal'`)); got <= 0 {
		t.Fatalf("Appraisal is still a dead profession: xp=%d", got)
	}
	_ = catalog
}

func TestAHarderThingResistsAGlance(t *testing.T) {
	catalog := districtCatalog(t)
	legendary := catalog.Items["nine_echo_sword_tablet"]
	special := catalog.Items["cloud_stepping_boots"]
	ordinary := catalog.Items["spirit_herb"]
	if !(appraisalTN(legendary) > appraisalTN(special) && appraisalTN(special) > appraisalTN(ordinary)) {
		t.Fatalf("a legendary must be harder to read: %d %d %d",
			appraisalTN(legendary), appraisalTN(special), appraisalTN(ordinary))
	}
	// And the keeper's fee tracks what the thing is worth.
	if !(appraisalFee(legendary) > appraisalFee(ordinary)) {
		t.Fatalf("fees: %d vs %d", appraisalFee(legendary), appraisalFee(ordinary))
	}
}

func TestPayingTheKeeperIsCertainAndCostsWhatItSays(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	// Stand on a floor, carrying nothing: a lot is read where it stands.
	house, houseID := "", ""
	for id, h := range catalog.AuctionHouses {
		house, houseID = h.Location, id
		break
	}
	batch4Exec(t, path, `UPDATE characters SET location=?,attributes_json='{"body":1,"agility":1,"spirit":1,"insight":1,"will":1,"presence":1}' WHERE user_id=42`, house)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_stone',100000) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=100000`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_crystal',100000) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=100000`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_immortal_stone',100000) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=100000`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_celestial_crystal',100000) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=100000`)
	batch4Exec(t, path, `INSERT INTO auctions(house_id,seller_user_id,seller_npc_name,item_id,quantity,currency_id,starting_bid,active,appraised,grade_band,created_at,ends_at) VALUES(?,NULL,'Grave-Robber Shen','nine_echo_sword_tablet',1,'low_spirit_stone',240,1,0,'legendary or near it',0,9e9)`, houseID)
	lot := i64(actionScalar(t, path, `SELECT auction_id FROM auctions WHERE seller_npc_name='Grave-Robber Shen'`))
	batch4SetCanonicalGameMinute(t, path, 3000)

	out := batch4Result(t, batch4Apply(t, path, world, "appraisal.read", 1, map[string]any{"auction_id": lot, "paid": true}))
	if fmt.Sprint(out["reading"]) != "certified" {
		t.Fatalf("paying the keeper must be certain: %v", out)
	}
	fee := i64(out["fee"])
	if fee != appraisalFee(catalog.Items["nine_echo_sword_tablet"]) {
		t.Fatalf("fee=%d", fee)
	}
	if got := i64(actionScalar(t, path, `SELECT COUNT(*) FROM character_item_appraisals WHERE user_id=42 AND item_id='nine_echo_sword_tablet' AND appraisal_kind='steward'`)); got != 1 {
		t.Fatalf("the certificate was not recorded: %d", got)
	}
	// Buying an answer is not practice: it teaches you nothing.
	if got := i64(actionScalar(t, path, `SELECT COUNT(*) FROM profession_progress WHERE user_id=42 AND profession='Appraisal'`)); got != 0 {
		t.Fatal("paying the keeper trained the buyer")
	}
}

func TestAnAppraisalNeedsTheThingOrTheFloor(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 3000)
	// Not carrying it.
	_, err := batch4ApplyErr(path, world, "appraisal.read", 42, 1, map[string]any{"item_id": "nine_echo_sword_tablet"})
	if err == nil || !strings.Contains(err.Error(), "not carrying") {
		t.Fatalf("reading something you do not hold must refuse, got %v", err)
	}
	// Standing nowhere near a floor, asking to pay.
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'nine_echo_sword_tablet',1)`)
	_, err = batch4ApplyErr(path, world, "appraisal.read", 42, 2, map[string]any{"item_id": "nine_echo_sword_tablet", "paid": true})
	if err == nil || !strings.Contains(err.Error(), "appraiser") {
		t.Fatalf("there is no keeper in a field, got %v", err)
	}
}

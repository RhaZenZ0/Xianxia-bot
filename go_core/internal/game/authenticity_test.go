package game

import (
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

// Whether the thing is what it claims to be (v1.0.0-rc.15).
//
// `item_provenance.authenticity` existed from the day the provenance table was
// written and all five of its writers passed the literal 100, so the column
// recorded precisely that nothing in the world was ever fake, and no rule read
// it anyway.

func openAppraisalConn(t *testing.T, path string) *storage.Conn {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	return conn
}

func TestAForgeryIsWorthWhatAForgeryIsWorth(t *testing.T) {
	genuine := authenticityPrice(100, authenticityGenuine)
	copied := authenticityPrice(100, 70)
	junk := authenticityPrice(100, authenticityUnderworldFloor)
	if genuine != 100 {
		t.Fatalf("the real thing must fetch its price: %d", genuine)
	}
	if !(copied < genuine && junk < copied) {
		t.Fatalf("a keeper should pay less the worse it is: %d %d %d", genuine, copied, junk)
	}
	if authenticityPrice(1, authenticityUnderworldFloor) < 1 {
		t.Fatal("a price must never fall below one")
	}
	// And the words a reading reports have to actually differ.
	words := map[string]bool{}
	for _, a := range []int64{100, 90, 70, 56} {
		words[authenticityWord(a)] = true
	}
	if len(words) != 4 {
		t.Fatalf("four bands, %d words", len(words))
	}
}

func TestTheUnderworldSellsSomeFakes(t *testing.T) {
	seenFake, seenReal := false, false
	for i := 0; i < 400; i++ {
		got, err := rollUnderworldAuthenticity()
		if err != nil {
			t.Fatal(err)
		}
		if got < authenticityUnderworldFloor || got > authenticityGenuine {
			t.Fatalf("out of range: %d", got)
		}
		if got < authenticityForgery {
			seenFake = true
		}
		if got == authenticityGenuine {
			seenReal = true
		}
	}
	if !seenFake {
		t.Fatal("four hundred broker deals and not one forgery")
	}
	if !seenReal {
		t.Fatal("the underworld never once sold the real thing")
	}
}

func TestTheWorstProvenanceIsTheOneThatCounts(t *testing.T) {
	path := setupShopDB(t)
	// Nothing recorded: nothing has ever cast doubt on it.
	conn := openAppraisalConn(t, path)
	got, known, err := itemAuthenticityTx(conn, 42, "spirit_herb")
	conn.Close()
	if err != nil {
		t.Fatal(err)
	}
	if known || got != authenticityGenuine {
		t.Fatalf("an unrecorded item should read genuine: %d known=%v", got, known)
	}

	// Two of the same item, one of them a copy: a keeper cannot tell which is
	// which, and neither can the owner.
	batch4Exec(t, path, `INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(42,'spirit_herb',1,'sect_entry','x','','clean',100,0,0,0,0)`)
	batch4Exec(t, path, `INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(42,'spirit_herb',1,'black_market','x','underworld broker','restricted',62,5,0,0,0)`)
	conn2 := openAppraisalConn(t, path)
	got, known, err = itemAuthenticityTx(conn2, 42, "spirit_herb")
	conn2.Close()
	if err != nil {
		t.Fatal(err)
	}
	if !known || got != 62 {
		t.Fatalf("the worst provenance should win: %d known=%v", got, known)
	}
}

// The reading is where a holder finds out which they are carrying.
func TestAnAppraisalNamesTheForgery(t *testing.T) {
	path := setupShopDB(t)
	world := batch4WorldPath(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'nine_echo_sword_tablet',1)`)
	batch4Exec(t, path, `INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(42,'nine_echo_sword_tablet',1,'black_market','Mortal World','underworld broker','restricted',60,5,0,0,0)`)
	batch4SetCanonicalGameMinute(t, path, 3000)

	out := batch4Result(t, batch4Apply(t, path, world, "appraisal.read", 1, map[string]any{"item_id": "nine_echo_sword_tablet"}))
	if fmt.Sprint(out["reading"]) != "read" {
		t.Fatalf("an insight-100 cultivator failed to read it: %v", out)
	}
	if out["forgery"] != true {
		t.Fatalf("the reading did not name the forgery: %v", out)
	}
	if got := i64(out["authenticity"]); got != 60 {
		t.Fatalf("authenticity=%d", got)
	}
	// And the record keeps what was found, not a hardcoded hundred.
	if got := i64(actionScalar(t, path, `SELECT authenticity FROM character_item_appraisals WHERE user_id=42 AND item_id='nine_echo_sword_tablet'`)); got != 60 {
		t.Fatalf("recorded authenticity=%d", got)
	}
}

// Inscription was the eighth profession and nothing in the game had ever
// granted a point of it - the same shape Appraisal was in.
func TestInscriptionIsARealProfession(t *testing.T) {
	catalog := districtCatalog(t)
	talismans := 0
	for name, recipe := range catalog.Recipes {
		if recipe.Profession == "Inscription" {
			talismans++
			_ = name
		}
	}
	if talismans == 0 {
		t.Fatal("Inscription still makes nothing")
	}
	// A talisman bench and an array table are the same room: splitting the
	// talismans out of Formation must not cost them their workshop.
	if craftAbodeFacilityColumn("Inscription") != craftAbodeFacilityColumn("Formation") {
		t.Fatal("an inscriber lost the workshop bonus in the split")
	}
	if craftManorFacilityColumn("Inscription") != craftManorFacilityColumn("Formation") {
		t.Fatal("an inscriber lost the manor bonus in the split")
	}
	if craftEffectStat("Inscription") != craftEffectStat("Formation") {
		t.Fatal("an inscriber lost the effect bonus in the split")
	}
}

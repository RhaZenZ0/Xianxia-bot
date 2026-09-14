package game

import (
	"testing"

	"xianxia/core/internal/storage"
)

// `item_provenance.tracking_strength` (v1.0.0-rc.18). Five writers set it with
// care - an underworld broker at the post's own heat, a hidden sect's grant at
// seventy, a caravan's cargo at five or twenty - and nothing on earth read it,
// so a cultivator wearing a branded relic out of a night market was no easier
// to find than one carrying nothing at all.

func openTrailConn(t *testing.T, path string) *storage.Conn {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	return conn
}

func trailOf(t *testing.T, path string, userID int64) int64 {
	t.Helper()
	conn := openTrailConn(t, path)
	defer conn.Close()
	trail, err := carriedTrailTx(conn, userID)
	if err != nil {
		t.Fatal(err)
	}
	return trail
}

func TestTheTrailIsWhatYouAreStillCarrying(t *testing.T) {
	path := setupShopDB(t)
	if got := trailOf(t, path, 42); got != 0 {
		t.Fatalf("trail=%d with nothing in the bags", got)
	}

	// A provenance row on its own marks nobody: the rows are never deleted,
	// so a trail read off them alone would follow a cultivator for the rest
	// of an incarnation over something they sold years ago.
	batch4Exec(t, path, `INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(42,'nine_echo_sword_tablet',1,'black_market','Mortal World','underworld broker','restricted',60,45,0,0,0)`)
	if got := trailOf(t, path, 42); got != 0 {
		t.Fatalf("trail=%d for goods that are no longer held", got)
	}

	// Holding it is what marks them.
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'nine_echo_sword_tablet',1)`)
	if got := trailOf(t, path, 42); got != 45 {
		t.Fatalf("trail=%d, want the 45 the broker's goods carry", got)
	}

	// And letting it go cools it, which is the whole lever.
	batch4Exec(t, path, `DELETE FROM inventory WHERE user_id=42 AND item_id='nine_echo_sword_tablet'`)
	if got := trailOf(t, path, 42); got != 0 {
		t.Fatalf("trail=%d after selling the thing that marked them", got)
	}
}

func TestWearingTheMarkedThingStillMarksYou(t *testing.T) {
	// equipment.bind takes an item out of `inventory`. A trail read off the
	// bags alone would go cold the moment its owner put the thing on, which
	// is the wrong way round - and is exactly the fault the flying mounts
	// hit in rc.15 when a mount was read off inventory only.
	path := setupShopDB(t)
	batch4Exec(t, path, `INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(42,'nine_echo_sword_tablet',1,'hidden_sect_initiation','x','','forbidden',100,70,0,0,0)`)
	batch4Exec(t, path, `INSERT INTO equipment_instances(user_id,item_id,slot,durability,max_durability,equipped,bound_at,updated_at) VALUES(42,'nine_echo_sword_tablet','weapon',100,100,1,0,0)`)
	if got := trailOf(t, path, 42); got != 70 {
		t.Fatalf("trail=%d for a bound, worn brand", got)
	}
}

func TestTheTrailIsTheStrongestMarkNotTheSumOfThem(t *testing.T) {
	// A trail is followed, not weighed: one branded relic is what a hunter
	// walks towards, and a sack of faintly warm herbs is not twelve times it.
	path := setupShopDB(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',12)`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'nine_echo_sword_tablet',1)`)
	batch4Exec(t, path, `INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(42,'spirit_herb',12,'black_market','x','underworld broker','restricted',90,5,0,0,0)`)
	batch4Exec(t, path, `INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(42,'nine_echo_sword_tablet',1,'black_market','x','underworld broker','restricted',60,45,0,0,0)`)
	if got := trailOf(t, path, 42); got != 45 {
		t.Fatalf("trail=%d, want the strongest single mark of 45", got)
	}
}

func TestAnHonestCultivatorHasNoTrail(t *testing.T) {
	// Every honest writer passes zero, and it has to stay meaning zero: a
	// sect inheritance and a family heirloom must not make anyone findable.
	path := setupShopDB(t)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',3)`)
	batch4Exec(t, path, `INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at) VALUES(42,'spirit_herb',3,'sect_entry','Azure Cloud Sect','','clean',100,0,0,0,0)`)
	if got := trailOf(t, path, 42); got != 0 {
		t.Fatalf("trail=%d for a sect's own gift", got)
	}
}

func TestTheTrailCostsAnEscapeButNeverAllOfIt(t *testing.T) {
	// The floor is the point: carrying the worst of it lengthens a chase
	// rather than ending it. A fugitive who cannot run is a cutscene.
	for _, trail := range []int64{1, 20, 45, 70, 100} {
		if got := trailEscapePenalty(10, trail); got >= 10 {
			t.Fatalf("trail %d took the whole of a 10-point evade (%d)", trail, got)
		}
	}
	if got := trailEscapePenalty(100, 100); got != 40 {
		t.Fatalf("the worst trail should cost 40%% of an evade, got %d", got)
	}
	if got := trailEscapePenalty(100, 0); got != 0 {
		t.Fatalf("an unmarked cultivator paid %d", got)
	}
	// A one-point gain cannot be reduced to nothing.
	if got := trailEscapePenalty(1, 100); got != 0 {
		t.Fatalf("a one-point evade was taken to %d", got)
	}
}

func TestTheTrailSpeedsAHunterUpInProportion(t *testing.T) {
	if got := trailPressureBonus(100, 0); got != 0 {
		t.Fatalf("an unmarked quarry gave up %d", got)
	}
	if got := trailPressureBonus(100, 100); got != 60 {
		t.Fatalf("the worst trail should add 60%% of a step, got %d", got)
	}
	// Monotonic, so a worse mark is never better to carry.
	previous := int64(-1)
	for trail := int64(0); trail <= 100; trail += 10 {
		got := trailPressureBonus(100, trail)
		if got < previous {
			t.Fatalf("carrying a stronger mark (%d) helped: %d after %d", trail, got, previous)
		}
		previous = got
	}
}

func TestTheTrailIsReportedInWordsNotJustANumber(t *testing.T) {
	// A rule the player cannot see is the fault this one was written to fix.
	seen := map[string]bool{}
	for _, trail := range []int64{0, 10, 30, 50, 90} {
		word := trailWord(trail)
		if word == "" {
			t.Fatalf("trail %d says nothing", trail)
		}
		seen[word] = true
	}
	if len(seen) != 5 {
		t.Fatalf("the bands do not read differently: %v", seen)
	}
}

package game

import "testing"

// The backfill reports the connections it raises (v1.12.3).
//
// `householdReputationTx` runs ahead of the heirloom's once-guard so a
// household's contacts reach a character created before they were authored -
// and the function then returned nil on the guard, dropping the `reputation`
// block: the standing rose and `family.support` never said so.
func TestABackfilledHouseholdReportsTheContactsItRaised(t *testing.T) {
	path := sendoffDB(t)
	fid := sendoffFamily(t, path, "hidden_weapon_family", "uw-backfill", 3, 50)
	sendOut(t, path, 42, fid, "hidden_weapon_family", 0)
	// A character from before v1.11.1: the heirloom is already theirs, the
	// contacts never were.
	batch4Exec(t, path, `DELETE FROM faction_reputation WHERE user_id=42`)
	out := sendOut(t, path, 42, fid, "hidden_weapon_family", 0)
	if got := underworldStanding(t, path); got < blackMarketTrustReputation {
		t.Fatalf("the backfill did not raise the contacts: %d", got)
	}
	raised, _ := out["reputation"].(map[string]int64)
	if len(raised) == 0 {
		t.Fatalf("the backfill raised the contacts and did not report them: %v", out)
	}
	if _, gift := out["item_id"]; gift {
		t.Fatalf("a second heirloom was reported on the backfill: %v", out)
	}
	// And a third ask raises nothing and says nothing.
	if again := sendOut(t, path, 42, fid, "hidden_weapon_family", 0); again != nil {
		t.Fatalf("a floor already met was reported again: %v", again)
	}
}

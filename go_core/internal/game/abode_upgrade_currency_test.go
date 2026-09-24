package game

import (
	"encoding/json"
	"testing"

	"xianxia/core/internal/storage"
)

// A homestead may be founded in any world, and its upgrade charged the Mortal
// stone in all four (v1.2.1) - so above the Mortal World, where every reward
// is paid in that world's crystal (rc.44), no upgrade could ever be afforded.
func TestAnUpgradeIsPaidInTheMoneyOfTheWorldTheHomeStandsIn(t *testing.T) {
	world := batch4WorldPath(t)
	path := setupPropertyTypesDB(t)
	batch4Exec(t, path, `UPDATE characters SET location='Jade Meridian Stone Gate' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_crystal',250)`)
	deaconMembership(t, path)
	if _, err := establishProperty(t, path, world, ""); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(map[string]any{"facility": "herb_garden"})
	out, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "prop-upgrade-crystal", Operation: "abode.upgrade", ActorID: 42, Payload: raw})
	if err != nil {
		t.Fatalf("a Spiritual World homestead could not be upgraded with 250 crystals: %v", err)
	}
	built := out.Result.(map[string]any)
	if storage.ParseInt(built["level"]) != 1 {
		t.Fatalf("build=%#v", built)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_crystal'")); got != 150 {
		t.Fatalf("crystal balance=%d want 150: the upgrade was not charged in the world's own money", got)
	}
}

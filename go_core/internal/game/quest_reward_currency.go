package game

import (
	"encoding/json"
	"fmt"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// questRewardCurrencyTx is the coin a quest pays in (v1.35.0): the money of
// the world its giver stands in.
//
// Until v1.35.0 grantQuestRewardTx and payCommissionRewardTx paid through
// characterWalletDeltaTx, the coin of wherever the player stood when the last
// objective was reported. Six realm-road stages end in a raid_win reported at
// the claim, so a stage finished by a claim made outside its own world paid in
// the wrong coin by the same factor of a hundred v1.33.0 closed for the raid
// itself (raidRewardCurrency). A quest has no world of its own to name, so it
// takes one from its giver: a commission's giver has a home
// (commissionGiverHome), and a realm-road stage's giver is the realm it is
// for (`seed_json.realm_index`), whose world the catalogue names. A quest
// with neither - the beginner path, a household errand, a Forge quest, a GM's -
// pays where the player stands, as every quest did before.
//
// The coin is checked to be the world's own, as raidCoinOf does: worldBaseCurrency
// falls back to the Mortal stone for a world the content gives no coin, and a
// fallback that looks like a value is not a sentinel (rc.28).
func questRewardCurrencyTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, questKey string) (string, error) {
	world := ""
	// Guarded rather than assumed: the engine can be pointed at a database an
	// older bootstrap made, and a missing column degrades to the old rule.
	ok, err := tableHasColumns(conn, "quest_definitions", "giver_npc", "seed_json")
	if err != nil {
		return "", err
	}
	if ok {
		r, err := conn.Execute(`SELECT giver_npc,seed_json FROM quest_definitions WHERE quest_key=?`, []any{questKey})
		if err != nil {
			return "", err
		}
		if len(r.Rows) > 0 {
			if home := commissionGiverHome(catalog, fmt.Sprint(r.Rows[0][0])); home != "" {
				world = catalog.Locations[home].World
			}
			if world == "" {
				seed := map[string]any{}
				_ = json.Unmarshal([]byte(fmt.Sprint(r.Rows[0][1])), &seed)
				// Asked whether the key is present, not whether it is zero:
				// realm 0 is a realm.
				if idx, has := seed["realm_index"]; has {
					world = realmWorldGo(catalog, i64(idx))
				}
			}
		}
	}
	if world != "" {
		coin := worldBaseCurrency(catalog, world)
		if def, found := catalog.Currencies[coin]; found && def.World == world {
			return coin, nil
		}
	}
	return characterBaseCurrencyTx(conn, catalog, userID)
}

package game

import (
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// What a caravan does to the places it joins (v1.29.0).
//
// A caravan paid its owner and nothing else. The toll it paid on arrival was
// computed and written to `caravan_operations.toll_paid` and credited to
// nobody, though the ground it was levied on was somebody's; and the goods it
// carried never reached the market they were carried to. Both settle paths -
// a player's `caravan.settle` and the world's own sweep - call this, so the
// two cannot drift, and it never refuses: a caravan's arrival must not fail
// over the market it arrives at.

// CaravanArrivedTx lands a caravan's cargo in the destination city's market
// (supply rises for each item it carried, and the city's prosperity a point)
// and pays its toll to the sect holding that city: a point of resources for
// every 100 stones, at least one and at most three. A seized caravan delivers
// nothing.
func CaravanArrivedTx(conn *storage.Conn, catalog worlddata.Catalog, destination string, cargo map[string]any, toll int64, seized bool, now float64) error {
	city := cityOf(catalog, destination)
	if !seized {
		if tableExistsTx(conn, "economy_markets") {
			for item, qty := range cargo {
				if strings.HasPrefix(item, "_") {
					continue
				}
				if n := storage.ParseInt(qty); n > 0 {
					if _, err := conn.Execute(`UPDATE economy_markets SET supply=MIN(9999,supply+?),updated_at=? WHERE location=? AND item_id=?`, []any{n, now, city, item}); err != nil {
						return err
					}
				}
			}
		}
		if err := nudgeCityProsperityTx(conn, catalog, destination, 1); err != nil {
			return err
		}
	}
	if toll > 0 && tableExistsTx(conn, "territory_state") && tableExistsTx(conn, "sect_politics_state") {
		gain := minI64(3, maxI64(1, toll/100))
		if _, err := conn.Execute(`UPDATE sect_politics_state SET resources=MIN(100,resources+?),updated_at=? WHERE sect_name=(
            SELECT controller_key FROM territory_state WHERE territory_key=? AND controller_type='sect' AND controller_key<>'')`, []any{gain, now, city}); err != nil {
			return err
		}
	}
	return nil
}

// CaravanSecurityRisk is what the security of the road a caravan leaves by
// adds to its risk (v1.29.0): a point for every five below 50, a point off for
// every five above. `civilization_regions.security` was moved by every death
// and every world event and read by no rule a player met; a place with 50
// security - the default - adds nothing.
func CaravanSecurityRisk(conn *storage.Conn, catalog worlddata.Catalog, origin string) int64 {
	if !tableExistsTx(conn, "civilization_regions") {
		return 0
	}
	r, err := conn.Execute(`SELECT security FROM civilization_regions WHERE location=?`, []any{cityOf(catalog, origin)})
	if err != nil || len(r.Rows) == 0 {
		return 0
	}
	return (50 - storage.ParseInt(r.Rows[0][0])) / 5
}

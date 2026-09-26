package game

import (
	"fmt"
	"time"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// settleGrandfatheredBeastsTx brings a beast made before v1.7.3's rules under
// them, once, the next time its owner acts (schema 67 marks it grandfathered=0).
//
//   - A rank above the limit of the world its owner stands in is lowered to it.
//   - Every level it climbed from rank 10 up is paid for now, in ascending
//     order, out of the owner's beast cores (beastLevelCores); it stops at the
//     first level the bag cannot pay for.
//
// Then the mark is cleared, so it runs once per beast. It never returns an
// error the caller needs: a failed settle leaves the mark for the next action,
// and a beast is never the reason a command refuses. The column is checked
// first, because in the compose stack the engine can serve an action before
// db-init has migrated.
func settleGrandfatheredBeastsTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64) error {
	ok, err := tableHasColumns(conn, "spirit_beasts", "grandfathered")
	if err != nil || !ok {
		return err
	}
	rows, err := conn.Execute(`SELECT beast_id,rank FROM spirit_beasts WHERE user_id=? AND grandfathered=0 ORDER BY beast_id`, []any{userID})
	if err != nil || len(rows.Rows) == 0 {
		return err
	}
	loc, err := conn.Execute(`SELECT location FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return err
	}
	location := ""
	if len(loc.Rows) > 0 {
		location = fmt.Sprint(loc.Rows[0][0])
	}
	limit := beastRankLimit(EraWorldOf(catalog, location))
	now := float64(time.Now().UnixNano()) / 1e9
	for _, r := range rows.Rows {
		beastID, rank := i64(r[0]), i64(r[1])
		if rank > limit {
			rank = limit
		}
		for level := int64(10); level < rank; level++ {
			short, err := consumeInventoryTx(conn, userID, map[string]int64{"beast_core": beastLevelCores(level)})
			if err != nil {
				return err
			}
			if len(short) > 0 {
				rank = level
				break
			}
		}
		if _, err := conn.Execute(`UPDATE spirit_beasts SET rank=?,grandfathered=1,updated_at=? WHERE user_id=? AND beast_id=?`, []any{rank, now, userID, beastID}); err != nil {
			return err
		}
	}
	return nil
}

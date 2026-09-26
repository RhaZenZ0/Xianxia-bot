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
//   - A rank above its owner's limit is lowered to it: the limit of the world
//     their realm belongs to, or of where they stand if that is higher.
//   - Every level it climbed by evolving is paid for now, in ascending order,
//     out of the owner's beast cores (beastLevelCores); it stops at the first
//     level the bag cannot pay for. What it climbed is its evolution_stage, so
//     it began at rank - evolution_stage: a beast tamed at rank 12 never
//     climbed 0..11 and owes nothing for them.
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
	rows, err := conn.Execute(`SELECT beast_id,rank,evolution_stage FROM spirit_beasts WHERE user_id=? AND grandfathered=0 ORDER BY beast_id`, []any{userID})
	if err != nil || len(rows.Rows) == 0 {
		return err
	}
	loc, err := conn.Execute(`SELECT location,realm_index FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return err
	}
	location, realm := "", int64(0)
	if len(loc.Rows) > 0 {
		location, realm = fmt.Sprint(loc.Rows[0][0]), i64(loc.Rows[0][1])
	}
	// The owner's cultivation decides first: a Spiritual World cultivator who
	// happens to be home in the Mortal World keeps the Spiritual limit. Where
	// they stand counts only when it is the higher of the two.
	limit := beastRankLimit(realmWorldGo(catalog, realm))
	if here := beastRankLimit(EraWorldOf(catalog, location)); here > limit {
		limit = here
	}
	now := float64(time.Now().UnixNano()) / 1e9
	for _, r := range rows.Rows {
		beastID, rank := i64(r[0]), i64(r[1])
		start := rank - i64(r[2])
		if start < 0 {
			start = 0
		}
		if rank > limit {
			rank = limit
		}
		for level := start; level < rank; level++ {
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

package game

import (
	"fmt"

	"xianxia/core/internal/storage"
)

// householdWorldTerms is what the world around a household does to it each
// year of `family.simulate` (v1.29.0). The household's year was a draw from
// ten fixed events and nothing else, while `martial_clan_relations` held its
// real treaties and feuds - kept by the clan diplomacy tick since v1.0.1 - and
// `martial_clan_branches.wealth_share` said what each cadet branch sends home.
// Neither reached the story the player reads.
type householdWorldTerms struct {
	Influence, Stability, Wealth int64
	Lines                        []string
}

// Each treaty in force is a point of influence a year and each rivalry a point
// of stability lost (a blood feud two), capped so the world shapes a year
// without deciding it; the branches send home a point of wealth for every
// fifty points of share.
const (
	householdTreatyCap  = 3
	householdQuarrelCap = 3
	householdBranchCap  = 3
	householdShareUnit  = 50
)

func householdWorldTermsTx(conn *storage.Conn, familyID int64) householdWorldTerms {
	out := householdWorldTerms{}
	if tableExistsTx(conn, "martial_clan_relations") {
		if res, err := conn.Execute(`SELECT relation_type,COUNT(*) FROM martial_clan_relations WHERE family_id=? AND active=1 GROUP BY relation_type`, []any{familyID}); err == nil {
			treaties, quarrel := int64(0), int64(0)
			for _, row := range res.Rows {
				n := storage.ParseInt(row[1])
				switch fmt.Sprint(row[0]) {
				case "alliance", "marriage_pact", "trade_pact":
					treaties += n
				case "rivalry":
					quarrel += n
				case "blood_feud":
					quarrel += 2 * n
				}
			}
			out.Influence = min64(householdTreatyCap, treaties)
			out.Stability = -min64(householdQuarrelCap, quarrel)
			if treaties > 0 {
				out.Lines = append(out.Lines, fmt.Sprintf("The household's %d treaties in force lent it weight among the clans.", treaties))
			}
			if quarrel > 0 {
				out.Lines = append(out.Lines, "Old rivalries kept the household's people uneasy.")
			}
		}
	}
	if tableExistsTx(conn, "martial_clan_branches") {
		if res, err := conn.Execute(`SELECT COALESCE(SUM(wealth_share),0) FROM martial_clan_branches WHERE family_id=? AND status='active'`, []any{familyID}); err == nil && len(res.Rows) > 0 {
			share := storage.ParseInt(res.Rows[0][0])
			out.Wealth = min64(householdBranchCap, max64(0, share)/householdShareUnit)
			if out.Wealth > 0 {
				out.Lines = append(out.Lines, "The cadet branches sent their share home.")
			}
		}
	}
	return out
}

package game

import (
	"fmt"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// What ending a pursuit costs (v1.28.0).
//
// Atoning for a crime (`crime.atone`) has always cost restitution - a fine in
// stones, paid where the record was made - and paid Orthodox Society for it.
// Surrendering to the hunter who carries its bounty cost nothing, and neither
// did being captured: both closed the same crime and the same bounty with
// three status writes. So the free way out was the hunter's, and the paid one
// was the one the law asks for; a cultivator with a bounty on them did best to
// wait to be caught.
//
// Now a surrender pays the restitution the crime would have cost at the
// magistrate's, and a capture pays half again with karma and Orthodox
// standing lost besides - being dragged in is worse than walking in. Both are
// taken from the purse as far as it reaches and never refused, because a
// surrender that a poor fugitive could not afford would be a fight they were
// made to keep losing, and a capture the tick cannot settle would end it.
const (
	bountyCaptureFineNum, bountyCaptureFineDen = int64(3), int64(2)
	bountyCaptureKarma                         = int64(-5)
	bountyCaptureOrthodox                      = int64(-5)
)

// crimeRestitutionFine is the one statement of what a crime costs to settle:
// `crime.atone`'s fine, and the base of a surrender's and a capture's.
func crimeRestitutionFine(severity, evidence int64) int64 {
	return max64(10, max64(1, severity)*25+(max64(0, evidence)/10)*5)
}

// SettleBountyTx charges the fugitive behind a bounty for how it ended:
// `captured` false for a surrender, true for a capture. It answers what was
// owed and paid, in the money of the world the fugitive stands in. A bounty
// with no crime behind it, or a fugitive the table no longer carries, costs
// nothing.
func SettleBountyTx(conn *storage.Conn, catalog worlddata.Catalog, userID, bountyID int64, captured bool, now float64) (map[string]any, error) {
	r, err := conn.Execute(`SELECT c.severity,c.evidence,c.crime_id FROM bounties b JOIN crime_records c ON c.crime_id=b.source_crime_id WHERE b.bounty_id=? AND b.user_id=?`, []any{bountyID, userID})
	if err != nil {
		return nil, err
	}
	if len(r.Rows) == 0 {
		return nil, nil
	}
	fine := crimeRestitutionFine(storage.ParseInt(r.Rows[0][0]), storage.ParseInt(r.Rows[0][1]))
	crimeID := storage.ParseInt(r.Rows[0][2])
	if captured {
		fine = fine * bountyCaptureFineNum / bountyCaptureFineDen
	}
	currency, err := characterBaseCurrencyTx(conn, catalog, userID)
	if err != nil {
		return nil, err
	}
	balance, err := walletBalanceTx(conn, userID, currency)
	if err != nil {
		return nil, err
	}
	paid := minI64(fine, balance)
	if paid > 0 {
		if _, err = walletDeltaTx(conn, catalog, userID, currency, -paid, now); err != nil {
			return nil, err
		}
	}
	out := map[string]any{"fine": fine, "paid": paid, "currency": currency, "captured": captured}
	if captured {
		if _, err = conn.Execute(`UPDATE characters SET karma_score=MAX(?,MIN(?,karma_score+?)),updated_at=? WHERE user_id=?`,
			[]any{deedKarmaFloor, deedKarmaCeiling, bountyCaptureKarma, now, userID}); err != nil {
			return nil, err
		}
		if _, err = adjustReputationTx(conn, userID, "Orthodox Society", bountyCaptureOrthodox, fmt.Sprintf("captured for crime #%d", crimeID), now); err != nil {
			return nil, err
		}
		out["karma_delta"] = bountyCaptureKarma
		out["orthodox_delta"] = bountyCaptureOrthodox
	}
	return out, nil
}

// PropertyDefenseLevelTx is the Defensive Formation of the property a
// cultivator stands inside, when it is their own; 0 anywhere else, or when the
// table is not there. The homestead's `defense_level` was built and raised for
// stones since rc.19 and read by no rule (v1.28.0).
func PropertyDefenseLevelTx(conn *storage.Conn, userID int64, location string) int64 {
	if ok, err := tableHasColumns(conn, "cave_abodes", "defense_level", "location_key"); err != nil || !ok {
		return 0
	}
	r, err := conn.Execute(`SELECT defense_level FROM cave_abodes WHERE user_id=? AND location_key=? LIMIT 1`, []any{userID, location})
	if err != nil || len(r.Rows) == 0 {
		return 0
	}
	return storage.ParseInt(r.Rows[0][0])
}

package game

import (
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Six reputations were written and read by nothing (v1.28.0). A duel paid
// Martial Society, sparing an NPC paid Merciful Reputation, atoning paid
// Orthodox Society, a forbidden manual paid Demonic Circles, a cleared
// tribulation paid Heavenly Recognition and a trade's examination paid
// `craft_hall:<trade>` - and `/reputation` listed every one of them as if it
// mattered. Each is read now by the one rule its name is about, through
// `standingTx`, and `TestEveryReputationIsReadSomewhere` holds every key a
// writer names to a reader.

// standingTx is a cultivator's standing under one reputation key, 0 when they
// have none. Keys are compared without case, the way the black market's
// Underworld Contacts read always has been.
func standingTx(conn *storage.Conn, userID int64, key string) int64 {
	r, err := conn.Execute(`SELECT score FROM faction_reputation WHERE user_id=? AND LOWER(faction_key)=LOWER(?)`, []any{userID, key})
	if err != nil || len(r.Rows) == 0 {
		return 0
	}
	return storage.ParseInt(r.Rows[0][0])
}

// standingBonus is one point for every `per` of positive standing, at most
// `cap`. A standing below zero is worth nothing here: the reputations that
// can fall have their own readers for that.
func standingBonus(score, per, cap int64) int64 {
	if score <= 0 || per <= 0 {
		return 0
	}
	return minI64(cap, score/per)
}

// sectCircleKey is which wider circle a sect's examiners listen to: the
// demonic sects to Demonic Circles, every other to Orthodox Society.
func sectCircleKey(catalog worlddata.Catalog, sect string) string {
	if def, ok := catalog.Sects[sect]; ok && strings.EqualFold(def.Alignment, "Demonic") {
		return "Demonic Circles"
	}
	return "Orthodox Society"
}

const (
	// A sect's entrance trial is a point easier for every 25 standing with
	// its circle, at most two.
	circleTrialPer, circleTrialCap = int64(25), int64(2)
	// A hall's examination fee falls a tenth for every 10 standing with the
	// hall, at most three tenths.
	craftHallFeePer, craftHallFeeCap = int64(10), int64(3)
	// Every tribulation survived is +8 Heavenly Recognition; the heavens'
	// judgment wave is a point easier for every 8 of it, at most three.
	heavenlyRecognitionPer, heavenlyRecognitionCap = int64(8), int64(3)
	// A duellist known to the Martial Society strikes a point surer for every
	// 25 of it, at most two.
	martialSocietyPer, martialSocietyCap = int64(25), int64(2)
	// A cultivator known for sparing the defeated is spared more often: a
	// percentage point off a defeat's fatal chance for every 10 Merciful
	// Reputation, at most six.
	mercifulPer, mercifulCap = int64(10), int64(6)
)

// examFeeAfterStanding is the hall's fee for somebody with this standing.
func examFeeAfterStanding(fee, standing int64) int64 {
	return fee * (10 - standingBonus(standing, craftHallFeePer, craftHallFeeCap)) / 10
}

// defeatFatalChance is the chance a lost fight kills: `min(75, 8+gap*3)`
// less what mercy shown is repaid with, never below zero. Both defeat paths
// - a turn and a technique - ask it, so the two cannot drift.
func defeatFatalChance(gap, merciful int64) int64 {
	return maxI64(0, minI64(75, 8+gap*3)-standingBonus(merciful, mercifulPer, mercifulCap))
}

package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Supporting the server on a listing site (Top.gg, DISBOARD, whichever the
// operator configured) is the one player action that happens outside the
// world, so the engine cannot verify it - there is no inbound webhook on a NAS
// that publishes nothing. What the engine *can* own is the part that touches
// game state: the cooldown, the size of the gift, and the receipt. A claim is
// therefore taken on trust and metered at the cadence a real vote has, so an
// honest player is rewarded once per vote and a dishonest one is rewarded no
// faster than an honest one.
const (
	// Top.gg, DISBOARD and every other list that matters use twelve hours.
	supportVoteCooldownSeconds int64 = 12 * 60 * 60
	// Fifteen low-grade stones of the world the cultivator stands in: two or
	// three shop items at any tier (shop prices run 7-40 in the local low
	// currency), which is a thank-you and not an economy.
	supportVoteReward int64 = 15
	supportVoteAction       = "support_vote"
)

type supportVotePayload struct {
	GameMinute int64  `json:"game_minute"`
	Site       string `json:"site"`
}

// supportSiteLabel bounds a name that came from the operator's environment
// rather than from the world catalogue. It is echoed back into a result and a
// domain event, so it is held to a short, printable, single-line label.
func supportSiteLabel(raw string) string {
	site := strings.TrimSpace(raw)
	if site == "" {
		return "the server listing"
	}
	cleaned := make([]rune, 0, 40)
	for _, r := range site {
		if len(cleaned) == 40 {
			break
		}
		if r < 32 || r == 127 {
			continue
		}
		cleaned = append(cleaned, r)
	}
	if len(cleaned) == 0 {
		return "the server listing"
	}
	return string(cleaned)
}

func supportVoteClaimAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p supportVotePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	remaining, err := cooldownRemaining(conn, userID, supportVoteAction, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if remaining > 0 {
		return authoritativeMutation{}, fmt.Errorf("cooldown active: %d seconds", remaining)
	}
	world := currentWorld(c, catalog)
	currency := tribulationCurrency(world)
	if _, err = conn.Execute(
		`INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?)
		 ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=balance+excluded.balance`,
		[]any{userID, currency, supportVoteReward},
	); err != nil {
		return authoritativeMutation{}, err
	}
	balance := int64(0)
	walletRes, err := conn.Execute(`SELECT balance FROM currency_wallets WHERE user_id=? AND currency_id=?`, []any{userID, currency})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if row := firstRowMap(walletRes); row != nil {
		balance = i64(row["balance"])
	}
	if err = setCooldown(conn, userID, supportVoteAction, supportVoteCooldownSeconds, now); err != nil {
		return authoritativeMutation{}, err
	}
	site := supportSiteLabel(p.Site)
	result := map[string]any{
		"site": site, "world": world, "currency": currency,
		"amount": supportVoteReward, "balance": balance,
		"next_claim_seconds": supportVoteCooldownSeconds,
	}
	return authoritativeMutation{
		Result: result,
		Event: eventledger.Event{
			Domain: "support", EventType: "support.vote_claim", EntityType: "character",
			EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: result,
		},
	}, nil
}

// supportVoteStatusQuery answers the cooldown without granting anything, so
// the command can draw "claimable now" or "in 4h12m" before the player has
// pressed anything. It writes nothing and is a query for that reason.
func supportVoteStatusQuery(conn *storage.Conn, userID int64) (map[string]any, error) {
	if userID <= 0 {
		return nil, errors.New("actor_id must be positive")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	remaining, err := cooldownRemaining(conn, userID, supportVoteAction, now)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"claimable":         remaining <= 0,
		"remaining_seconds": remaining,
		"cooldown_seconds":  supportVoteCooldownSeconds,
		"amount":            supportVoteReward,
	}, nil
}

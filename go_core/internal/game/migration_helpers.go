package game

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"

	"xianxia/core/internal/storage"
)

func nowSeconds() float64 { return float64(unixNanoNow()) / 1e9 }

// unixNanoNow is split out to keep migration helpers easy to test without
// duplicating time conversion logic in every domain file.
var unixNanoNow = func() int64 {
	return timeNowUnixNano()
}

var timeNowUnixNano = func() int64 {
	return time.Now().UnixNano()
}

func rowsToMaps(res storage.Result) []map[string]any {
	out := make([]map[string]any, 0, len(res.Rows))
	for _, row := range res.Rows {
		m := make(map[string]any, len(res.Columns))
		for i, name := range res.Columns {
			if i < len(row) {
				m[name] = row[i]
			}
		}
		out = append(out, m)
	}
	return out
}

func stablePercentGo(parts ...any) int64 {
	text := make([]string, 0, len(parts))
	for _, part := range parts {
		text = append(text, fmt.Sprint(part))
	}
	sum := sha256.Sum256([]byte(strings.Join(text, "|")))
	return int64(binary.BigEndian.Uint32(sum[:4]) % 100)
}

func walletBalanceTx(conn *storage.Conn, userID int64, currency string) (int64, error) {
	r, err := conn.Execute(`SELECT balance FROM currency_wallets WHERE user_id=? AND currency_id=?`, []any{userID, currency})
	if err != nil {
		return 0, err
	}
	if row := firstRowMap(r); row != nil {
		return i64(row["balance"]), nil
	}
	return 0, nil
}

func walletDeltaTx(conn *storage.Conn, userID int64, currency string, delta int64, now float64) (int64, error) {
	currency = strings.TrimSpace(currency)
	if currency == "" {
		return 0, errors.New("currency_id is required")
	}
	balance, err := walletBalanceTx(conn, userID, currency)
	if err != nil {
		return 0, err
	}
	if delta < 0 && balance < -delta {
		return balance, fmt.Errorf("not enough %s", strings.ReplaceAll(currency, "_", " "))
	}
	next := balance + delta
	if delta > 0 && balance > math.MaxInt64-delta {
		return balance, errors.New("wallet balance overflow")
	}
	_, err = conn.Execute(`INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=excluded.balance`, []any{userID, currency, next})
	if err != nil {
		return balance, err
	}
	if currency == "low_spirit_stone" {
		if _, err = conn.Execute(`UPDATE characters SET spirit_stones=?,updated_at=? WHERE user_id=?`, []any{next, now, userID}); err != nil {
			return balance, err
		}
	}
	return next, nil
}

func inventoryQuantityTx(conn *storage.Conn, userID int64, itemID string) (int64, error) {
	r, err := conn.Execute(`SELECT quantity FROM inventory WHERE user_id=? AND item_id=?`, []any{userID, itemID})
	if err != nil {
		return 0, err
	}
	if row := firstRowMap(r); row != nil {
		return i64(row["quantity"]), nil
	}
	return 0, nil
}

func decodeJSONMap(raw any) map[string]any {
	s := strings.TrimSpace(fmt.Sprint(raw))
	if s == "" || s == "<nil>" {
		return map[string]any{}
	}
	var out map[string]any
	if json.Unmarshal([]byte(s), &out) != nil || out == nil {
		return map[string]any{}
	}
	return out
}

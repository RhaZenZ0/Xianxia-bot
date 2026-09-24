package game

import (
	"testing"

	"xianxia/core/internal/storage"
)

// The sheet's mirror follows the base currency of the world the target stands
// in (rc.44). The GM's grant compared the id to the Mortal stone by name
// (v1.2.1), so a grant of crystals to somebody in the Spiritual World never
// reached their sheet, and a grant of Mortal stones to them corrupted it.
func TestAGrantMirrorsTheMoneyOfTheTargetsWorld(t *testing.T) {
	path := setupAdminDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Execute(`UPDATE characters SET location='Spirit Jade Capital' WHERE user_id=42`, nil); err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	before := storage.ParseInt(scalar(t, path, "SELECT spirit_stones FROM characters WHERE user_id=42"))

	applyAdmin(t, path, "admin.player.grant_currency", map[string]any{"user_id": 42, "currency_id": "low_spirit_crystal", "amount": 7, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT spirit_stones FROM characters WHERE user_id=42")); got != before+7 {
		t.Fatalf("spirit_stones=%d want %d: a grant in the world's own money did not reach the sheet", got, before+7)
	}
	applyAdmin(t, path, "admin.player.grant_currency", map[string]any{"user_id": 42, "currency_id": "low_spirit_stone", "amount": 50, "reason": "test"})
	if got := storage.ParseInt(scalar(t, path, "SELECT spirit_stones FROM characters WHERE user_id=42")); got != before+7 {
		t.Fatalf("spirit_stones=%d want %d: Mortal stones granted in the Spiritual World moved the sheet", got, before+7)
	}
	if got := storage.ParseInt(scalar(t, path, "SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'")); got != 50 {
		t.Fatalf("the Mortal purse should still hold the grant: %d", got)
	}
}

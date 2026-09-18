package game

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// caravanTables is the road's own schema, shared by every caravan test so
// there is one copy of it rather than one per test to drift.
func caravanTables(t *testing.T, conn *storage.Conn) {
	t.Helper()
	if err := conn.ExecScript(`
CREATE TABLE caravans(
	caravan_id INTEGER PRIMARY KEY AUTOINCREMENT,
	owner_type TEXT NOT NULL,
	owner_key TEXT NOT NULL,
	origin TEXT NOT NULL,
	destination TEXT NOT NULL,
	cargo_json TEXT NOT NULL DEFAULT '{}',
	status TEXT NOT NULL DEFAULT 'traveling',
	risk INTEGER NOT NULL DEFAULT 0,
	depart_game_minute INTEGER NOT NULL DEFAULT 0,
	arrive_game_minute INTEGER NOT NULL DEFAULT 0,
	updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE caravan_operations(
	caravan_id INTEGER PRIMARY KEY,
	escort_strength INTEGER NOT NULL DEFAULT 0,
	concealment INTEGER NOT NULL DEFAULT 0,
	smuggling INTEGER NOT NULL DEFAULT 0,
	tax_rate INTEGER NOT NULL DEFAULT 0,
	toll_paid INTEGER NOT NULL DEFAULT 0,
	intercepted INTEGER NOT NULL DEFAULT 0,
	seized INTEGER NOT NULL DEFAULT 0,
	payout_final INTEGER NOT NULL DEFAULT 0,
	losses_json TEXT NOT NULL DEFAULT '{}',
	outcome TEXT NOT NULL DEFAULT 'traveling',
	resolved_game_minute INTEGER,
	updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE caravan_events(
	event_id INTEGER PRIMARY KEY AUTOINCREMENT,
	caravan_id INTEGER NOT NULL,
	event_type TEXT NOT NULL,
	detail_json TEXT NOT NULL DEFAULT '{}',
	game_minute INTEGER NOT NULL DEFAULT 0,
	created_at REAL NOT NULL DEFAULT 0
);
`); err != nil {
		t.Fatal(err)
	}
}

func TestCaravanSettleIsGoAuthoritative(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	caravanTables(t, conn)
	if err := conn.ExecScript(`
INSERT INTO caravans(owner_type,owner_key,origin,destination,cargo_json,status,risk,depart_game_minute,arrive_game_minute,updated_at)
VALUES('player','42','Greenriver Town','Cloudspine Foothills','{"_payout":100,"_currency":"low_spirit_stone"}','traveling',0,0,10,0);
INSERT INTO caravan_operations(caravan_id,escort_strength,concealment,smuggling,tax_rate,updated_at)
VALUES(1,0,0,0,0,0);
`); err != nil {
		_ = conn.Close()
		t.Fatal(err)
	}
	_ = conn.Close()

	result := batch4Result(t, batch4Apply(t, path, world, "caravan.settle", 1, map[string]any{"game_minute": 10}))
	if storage.ParseInt(result["count"]) != 1 {
		t.Fatalf("settle result=%v", result)
	}
	if got := actionScalar(t, path, "SELECT status FROM caravans WHERE caravan_id=1"); got != "arrived" {
		t.Fatalf("status=%v", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'")); got != 100 {
		t.Fatalf("wallet=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path, "SELECT COUNT(*) FROM authoritative_action_receipts WHERE operation='caravan.settle'")); got != 1 {
		t.Fatalf("receipt count=%d", got)
	}
}

func TestStage6CaravanDispatchRejectsOutOfRangeMechanicalInputs(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 7000)

	cases := []struct {
		name    string
		payload map[string]any
		want    string
	}{
		{
			name:    "zero quantity",
			payload: map[string]any{"destination": "Riverguard City", "item_id": "spirit_herb", "quantity": 0, "escort": 0},
			want:    "quantity must be between 1 and 50",
		},
		{
			name:    "oversized quantity",
			payload: map[string]any{"destination": "Riverguard City", "item_id": "spirit_herb", "quantity": 51, "escort": 0},
			want:    "quantity must be between 1 and 50",
		},
		{
			name:    "negative escort",
			payload: map[string]any{"destination": "Riverguard City", "item_id": "spirit_herb", "quantity": 1, "escort": -1},
			want:    "escort must be between 0 and 20",
		},
		{
			name:    "oversized escort",
			payload: map[string]any{"destination": "Riverguard City", "item_id": "spirit_herb", "quantity": 1, "escort": 21},
			want:    "escort must be between 0 and 20",
		},
		{
			name:    "caller duration",
			payload: map[string]any{"destination": "Riverguard City", "item_id": "spirit_herb", "quantity": 1, "escort": 0, "minutes_per_day": 1},
			want:    "client-supplied caravan duration is forbidden",
		},
	}

	for index, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			raw, err := json.Marshal(tc.payload)
			if err != nil {
				t.Fatal(err)
			}
			_, err = ApplyWithWorld(path, world, ActionRequest{
				APIVersion: authoritativeAPIVersion,
				ActionID:   "stage6-caravan-bounds-" + tc.name,
				Operation:  "caravan.dispatch",
				ActorID:    42,
				Payload:    raw,
			})
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("case %d err=%v want substring %q", index, err, tc.want)
			}
		})
	}
}

func TestStage6CaravanDispatchRejectsDeadActor(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 7100)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',1)`)
	batch4Exec(t, path, `UPDATE characters SET life_status='dead' WHERE user_id=42`)

	raw, err := json.Marshal(map[string]any{
		"destination": "Riverguard City",
		"item_id":     "spirit_herb",
		"quantity":    1,
		"escort":      0,
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "stage6-caravan-dead-actor",
		Operation:  "caravan.dispatch",
		ActorID:    42,
		Payload:    raw,
	})
	if err == nil || !strings.Contains(err.Error(), "only a living incarnation can dispatch a caravan") {
		t.Fatalf("err=%v", err)
	}
}

// A caravan a realm-4 cultivator can actually pay for (v1.0.0-rc.43).
//
// Dispatch charged `mid_spirit_stone` from realm 4 and `high_spirit_stone` from
// realm 7. Those two ids appeared at exactly the two lines that spent them:
// nothing in the game has ever credited a tier above the base, and
// `walletDeltaTx` refuses a debit beyond the balance, so the leaf was dead for
// every cultivator past the fourth realm. With the old lines restored this
// fails on "not enough mid spirit stone", which is the production symptom.
func TestACaravanIsPaidForInTheMoneyOfItsOwnWorld(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 7000)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	caravanTables(t, conn)
	if err := conn.Commit(); err != nil {
		conn.Close()
		t.Fatal(err)
	}
	conn.Close()

	// Past the realm the old escalation began at, standing in the Mortal World,
	// holding the Mortal World's own money and nothing else.
	batch4Exec(t, path, `UPDATE characters SET realm_index=4,location='Greenriver Town' WHERE user_id=42`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',10)
		ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=10`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_stone',5000)
		ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=5000`)

	raw, err := json.Marshal(map[string]any{
		"destination": "Riverguard City", "item_id": "spirit_herb", "quantity": 2, "escort": 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	out, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   "rc43-caravan-world-money",
		Operation:  "caravan.dispatch",
		ActorID:    42,
		Payload:    raw,
	})
	if err != nil {
		t.Fatalf("a realm-4 dispatch holding the world's own money was refused: %v", err)
	}
	result, _ := out.Result.(map[string]any)
	if got := fmt.Sprint(result["currency_id"]); got != "low_spirit_stone" {
		t.Fatalf("the road was priced in %q, want the Mortal World's own low_spirit_stone", got)
	}
	// The charge landed where the wallet could meet it, and nowhere else.
	if got := storage.ParseInt(actionScalar(t, path,
		`SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id='low_spirit_stone'`)); got >= 5000 {
		t.Fatalf("the operating cost was never taken: balance=%d", got)
	}
	if got := storage.ParseInt(actionScalar(t, path,
		`SELECT COUNT(*) FROM currency_wallets WHERE user_id=42 AND currency_id<>'low_spirit_stone'`)); got != 0 {
		t.Fatalf("%d wallet row(s) in a currency nothing credits", got)
	}
}

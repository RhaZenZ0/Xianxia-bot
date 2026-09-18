package game

import (
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

func shippedCatalog(t *testing.T) worlddata.Catalog {
	t.Helper()
	catalog, err := worlddata.Load("../../../content/world.json")
	if err != nil {
		t.Skipf("shipped content not readable from here: %v", err)
	}
	return catalog
}

// The ladder is content, and it was content nothing had ever parsed: until
// rc.43 `CurrencyDefinition` carried `name` and nothing else, so the world a
// currency belongs to, its tier, and what a tier is worth were all dropped by
// the parser. Holding the shape here is what lets `worldBaseCurrency` trust it,
// and what makes an exchange counter buildable later without first auditing
// sixteen entries by hand.
func TestTheStoneLadderIsWholeInEveryWorld(t *testing.T) {
	catalog := shippedCatalog(t)
	if len(catalog.Currencies) == 0 {
		t.Fatal("the content file declares no currencies at all")
	}
	wantRatio := map[int64]int64{1: 0, 2: 100, 3: 10000, 4: 1000000}
	byWorld := map[string]map[int64]string{}
	for id, def := range catalog.Currencies {
		if def.World == "" {
			t.Errorf("%s belongs to no world", id)
			continue
		}
		if def.Name == "" {
			t.Errorf("%s has no name", id)
		}
		want, known := wantRatio[def.Tier]
		if !known {
			t.Errorf("%s sits at tier %d, which is not one of the four", id, def.Tier)
			continue
		}
		if def.BaseRatio != want {
			t.Errorf("%s is tier %d and worth %d of the base; want %d", id, def.Tier, def.BaseRatio, want)
		}
		if byWorld[def.World] == nil {
			byWorld[def.World] = map[int64]string{}
		}
		if other, clash := byWorld[def.World][def.Tier]; clash {
			t.Errorf("%s and %s are both tier %d of %s", other, id, def.Tier, def.World)
		}
		byWorld[def.World][def.Tier] = id
	}
	for world, tiers := range byWorld {
		for tier := int64(1); tier <= 4; tier++ {
			if tiers[tier] == "" {
				t.Errorf("%s has no tier %d currency", world, tier)
			}
		}
	}
}

// Every price in the content file is tier 1, so the money of the world a
// player stands in is the money they are asked for. `worldBaseCurrency` is how
// the one price that lives in code rather than content says so.
func TestEveryWorldHasOneBaseCurrencyAndTheCaravanChargesIt(t *testing.T) {
	catalog := shippedCatalog(t)
	for _, want := range []struct{ world, currency string }{
		{"Mortal World", "low_spirit_stone"},
		{"Spiritual World", "low_spirit_crystal"},
		{"Immortal World", "low_immortal_stone"},
		{"Celestial World", "low_celestial_crystal"},
	} {
		if got := worldBaseCurrency(catalog, want.world); got != want.currency {
			t.Errorf("%s is priced in %q, want %q", want.world, got, want.currency)
		}
	}
	// A world the content does not carry keeps the Mortal stone rather than
	// charging an empty currency id, which walletDeltaTx refuses outright.
	if got := worldBaseCurrency(catalog, "Nowhere At All"); got != fallbackBaseCurrency {
		t.Errorf("an unknown world is priced in %q, want the %q fallback", got, fallbackBaseCurrency)
	}
}

// The bug itself, stated as a test: the tiers above 1 are spendable and nothing
// credits them, so a price named in one is a price nobody can pay.
func TestNoTierAboveTheBaseIsEverCharged(t *testing.T) {
	catalog := shippedCatalog(t)
	for _, world := range []string{"Mortal World", "Spiritual World", "Immortal World", "Celestial World"} {
		base := worldBaseCurrency(catalog, world)
		def, ok := catalog.Currencies[base]
		if !ok {
			t.Fatalf("%s is priced in %q, which the content file does not carry", world, base)
		}
		if def.Tier != 1 {
			t.Errorf("%s is priced in %s, which is tier %d", world, base, def.Tier)
		}
	}
}

// Paid where you stand (v1.0.0-rc.44).
//
// Every price in the content file is its own world's tier-1 currency, and until
// now almost every reward credited `low_spirit_stone` wherever it was earned -
// so a cultivator above the Mortal World was paid in money the shops there do
// not take. With the old constant restored this fails on the currency id.
func TestARewardIsPaidInTheMoneyOfTheWorldItIsEarnedIn(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	batch4SetCanonicalGameMinute(t, path, 6000)

	for _, where := range []struct{ location, currency string }{
		{"Greenriver Town", "low_spirit_stone"},
		{"Spirit Jade Capital", "low_spirit_crystal"},
	} {
		t.Run(where.location, func(t *testing.T) {
			batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, where.location)
			batch4Exec(t, path, `DELETE FROM currency_wallets WHERE user_id=42`)
			batch4Exec(t, path, `UPDATE characters SET spirit_stones=0 WHERE user_id=42`)

			conn, err := storage.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			catalog, err := worlddata.Load(world)
			if err != nil {
				conn.Close()
				t.Fatal(err)
			}
			if _, err := characterWalletDeltaTx(conn, catalog, 42, 40, nowSeconds()); err != nil {
				conn.Close()
				t.Fatal(err)
			}
			if err := conn.Commit(); err != nil {
				conn.Close()
				t.Fatal(err)
			}
			conn.Close()

			held := storage.ParseInt(actionScalar(t, path,
				`SELECT balance FROM currency_wallets WHERE user_id=42 AND currency_id=?`, where.currency))
			if held != 40 {
				t.Fatalf("standing in %s, 40 stones landed in %s as %d", where.location, where.currency, held)
			}
			rows := storage.ParseInt(actionScalar(t, path, `SELECT COUNT(*) FROM currency_wallets WHERE user_id=42`))
			if rows != 1 {
				t.Fatalf("%d wallet rows; the reward should be denominated once", rows)
			}
			// The sheet's one number follows the character, not the Mortal World.
			if sheet := storage.ParseInt(actionScalar(t, path,
				`SELECT spirit_stones FROM characters WHERE user_id=42`)); sheet != 40 {
				t.Fatalf("the sheet says %d while the purse holds 40", sheet)
			}
		})
	}
}

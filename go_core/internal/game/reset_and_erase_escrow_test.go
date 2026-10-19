package game

// What a reset or an erasure leaves in the auction house and on the road
// (v1.34.0).
//
// Both levers sweep a person's rows through `applyErasureTargets`, and neither
// ran the resolver a death runs. Three faults came of it, one story: the sweep
// walks tables alphabetically and `auctions.current_bidder_user_id` was
// classified a *delete* column, so a bidder's reset deleted the **seller's**
// lot with the item the seller had put in escrow; a seller's reset deleted the
// lot with the **bidder's** stones in it; and a caravan an erased owner had on
// the road stayed `traveling`, so the tick that settled it paid a wallet whose
// foreign key refused, every tick.
//
// The fixture is the character-creation one with the tables the escrow lives
// in, **foreign keys and all**: `auctions` cascades on its seller and clears
// on its bidder, `currency_wallets` is keyed to `characters`. A fixture
// without them accepted every one of the three faults - the npc_consignments
// lesson.

import (
	"encoding/json"
	"testing"
)

const escrowFixtureDDL = `
DROP TABLE currency_wallets;
CREATE TABLE currency_wallets(user_id INTEGER NOT NULL,currency_id TEXT NOT NULL,balance INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id,currency_id),FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE auctions (
  auction_id INTEGER PRIMARY KEY AUTOINCREMENT, house_id TEXT NOT NULL, seller_user_id INTEGER,
  item_id TEXT NOT NULL, quantity INTEGER NOT NULL, currency_id TEXT NOT NULL, starting_bid INTEGER NOT NULL,
  current_bid INTEGER NOT NULL DEFAULT 0, current_bidder_user_id INTEGER, anonymous INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, ends_at REAL NOT NULL,
  FOREIGN KEY(seller_user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
  FOREIGN KEY(current_bidder_user_id) REFERENCES characters(user_id) ON DELETE SET NULL);
CREATE TABLE auction_bids (bid_id INTEGER PRIMARY KEY AUTOINCREMENT, auction_id INTEGER NOT NULL, bidder_user_id INTEGER NOT NULL,
  amount INTEGER NOT NULL, created_at REAL NOT NULL,
  FOREIGN KEY(auction_id) REFERENCES auctions(auction_id) ON DELETE CASCADE,
  FOREIGN KEY(bidder_user_id) REFERENCES characters(user_id) ON DELETE CASCADE);
CREATE TABLE caravans (
  caravan_id INTEGER PRIMARY KEY AUTOINCREMENT, owner_type TEXT NOT NULL DEFAULT 'npc',
  owner_key TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL,
  cargo_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'traveling',
  risk INTEGER NOT NULL DEFAULT 10, depart_game_minute INTEGER NOT NULL DEFAULT 0,
  arrive_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS admin_audit_log (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT, admin_user_id INTEGER NOT NULL, action TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '', before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL DEFAULT '{}',
  reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
`

const (
	escrowBidder int64 = 77
	escrowSeller int64 = 88
	escrowStones int64 = 100
	escrowBid    int64 = 50
)

// escrowWorld is a seller's lot with a bidder's stones in it: the bid is out of
// the bidder's wallet already (that is what escrow is), so the wallet holds 100
// and the lot holds 50.
func escrowWorld(t *testing.T) (path, world string) {
	t.Helper()
	path = setupCharacterResetDB(t)
	world = batch4WorldPath(t)
	resetExec(t, path, escrowFixtureDDL)
	makeCultivator(t, path, world, escrowBidder, "Lin Bidder", 200)
	makeCultivator(t, path, world, escrowSeller, "Wu Seller", 300)
	resetExec(t, path, `
INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(77,'low_spirit_stone',100)
  ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=excluded.balance;
INSERT INTO auctions(house_id,seller_user_id,item_id,quantity,currency_id,starting_bid,current_bid,current_bidder_user_id,created_at,ends_at)
  VALUES('h',88,'spirit_herb',1,'low_spirit_stone',10,50,77,1,9999999999);
INSERT INTO auction_bids(auction_id,bidder_user_id,amount,created_at) VALUES(1,77,50,1);`)
	return path, world
}

// eraseWith is the production dispatch for `admin.player.erase`, with the world
// file, because the resolver it now runs reads the catalogue.
func eraseWith(t *testing.T, path, world string, admin, subject int64) error {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"user_id": subject, "reason": "the player asked"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = ApplyWithWorld(path, world, ActionRequest{Operation: "admin.player.erase", ActorID: 1, Payload: raw})
	return err
}

// The two levers that end a person's rows, driven the way production drives
// them. Every escrow test below runs against both.
var escrowLevers = []struct {
	name string
	run  func(t *testing.T, path, world string, subject int64) error
}{
	{"character.reset", func(t *testing.T, path, world string, subject int64) error {
		_, err := resetCultivator(t, path, world, subject, "escrow-reset")
		return err
	}},
	{"admin.player.erase", func(t *testing.T, path, world string, subject int64) error {
		return eraseWith(t, path, world, 1, subject)
	}},
}

func TestABiddersLeavingLeavesTheSellersLotAndClearsTheBid(t *testing.T) {
	for _, lever := range escrowLevers {
		t.Run(lever.name, func(t *testing.T) {
			path, world := escrowWorld(t)
			if err := lever.run(t, path, world, escrowBidder); err != nil {
				t.Fatal(err)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM auctions"); got != 1 {
				t.Fatalf("%s took the seller's lot out of the world: %d lot(s) left, want 1 - the item the seller put in escrow went with it", lever.name, got)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM auctions WHERE active=1 AND current_bidder_user_id IS NULL AND current_bid=0"); got != 1 {
				t.Fatalf("%s left the lot still naming its bidder or still holding the bid", lever.name)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM characters WHERE user_id=88"); got != 1 {
				t.Fatalf("%s touched the seller", lever.name)
			}
		})
	}
}

func TestASellerLeavingClosesTheLotAndRefundsItsBidder(t *testing.T) {
	for _, lever := range escrowLevers {
		t.Run(lever.name, func(t *testing.T) {
			path, world := escrowWorld(t)
			if err := lever.run(t, path, world, escrowSeller); err != nil {
				t.Fatal(err)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM auctions"); got != 0 {
				t.Fatalf("%s left %d lot(s) belonging to a seller who is gone", lever.name, got)
			}
			if got := walletOf(t, path, "low_spirit_stone", escrowBidder); got != escrowStones+escrowBid {
				t.Fatalf("%s stranded the bidder's escrow: wallet is %d, want %d (the 50 stones the lot held come back)",
					lever.name, got, escrowStones+escrowBid)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM characters WHERE user_id=77"); got != 1 {
				t.Fatalf("%s touched the bidder", lever.name)
			}
		})
	}
}

// A lot that closed with a winner keeps naming them until something clears it.
// It is not escrow any more (the settlement paid the seller), so there is
// nothing to refund - but the row is the seller's ledger, and the link to a
// person who is gone is what goes, the way the schema's ON DELETE SET NULL says.
func TestAClosedLotKeepsItsRowAndLosesItsWinner(t *testing.T) {
	for _, lever := range escrowLevers {
		t.Run(lever.name, func(t *testing.T) {
			path, world := escrowWorld(t)
			resetExec(t, path, `UPDATE auctions SET active=0 WHERE auction_id=1;`)
			if err := lever.run(t, path, world, escrowBidder); err != nil {
				t.Fatal(err)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM auctions WHERE seller_user_id=88"); got != 1 {
				t.Fatalf("%s deleted a closed lot that belongs to another seller", lever.name)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM auctions WHERE current_bidder_user_id=77"); got != 0 {
				t.Fatalf("%s left a closed lot naming the winner who is gone", lever.name)
			}
		})
	}
}

func TestACaravanOnTheRoadIsLostWithItsOwner(t *testing.T) {
	for _, lever := range escrowLevers {
		t.Run(lever.name, func(t *testing.T) {
			path, world := escrowWorld(t)
			resetExec(t, path, `
INSERT INTO caravans(owner_type,owner_key,origin,destination,status,updated_at) VALUES('player','88','Greenriver Town','Riverguard City','traveling',0);
INSERT INTO caravans(owner_type,owner_key,origin,destination,status,updated_at) VALUES('player','77','Greenriver Town','Riverguard City','traveling',0);`)
			if err := lever.run(t, path, world, escrowSeller); err != nil {
				t.Fatal(err)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM caravans WHERE owner_key='88' AND status='traveling'"); got != 0 {
				t.Fatalf("%s left %d caravan(s) traveling for an owner who is gone; the tick that settles them pays a wallet that no longer exists", lever.name, got)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM caravans WHERE owner_key='88' AND status='lost'"); got != 1 {
				t.Fatalf("%s did not mark the owner's caravan lost", lever.name)
			}
			if got := resetScalarI(t, path, "SELECT COUNT(*) FROM caravans WHERE owner_key='77' AND status='traveling'"); got != 1 {
				t.Fatalf("%s lost somebody else's caravan", lever.name)
			}
		})
	}
}

// The reset's reply says what it settled, in the engine's own words, so the
// Discord side prints it rather than restating it.
func TestAResetReportsWhatItSettled(t *testing.T) {
	path, world := escrowWorld(t)
	result, err := resetCultivator(t, path, world, escrowSeller, "escrow-report")
	if err != nil {
		t.Fatal(err)
	}
	escrow, ok := result["escrow"].(map[string]any)
	if !ok {
		t.Fatalf("the reset reported no escrow: %v", result)
	}
	if got := i64(escrow["auctions_cancelled"]); got != 1 {
		t.Fatalf("auctions_cancelled=%d, want 1", got)
	}
}

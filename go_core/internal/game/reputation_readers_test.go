package game

import (
	"go/ast"
	"go/parser"
	"go/token"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"
	"xianxia/core/internal/storage"
)

// reputationKeyOf is the literal key an argument names: "Martial Society", or
// the literal prefix of "craft_hall:"+trade.
func reputationKeyOf(expr ast.Expr) string {
	switch x := expr.(type) {
	case *ast.BasicLit:
		if s, err := strconv.Unquote(x.Value); err == nil {
			return s
		}
	case *ast.BinaryExpr:
		return reputationKeyOf(x.X)
	}
	return ""
}

// v1.28.0: every reputation a writer names is one some rule reads. Six were
// written for releases and read by nothing; this is the gate on the next.
// Keys written from a variable (a sect's own name, a household's key) are
// read by their own readers and are not this gate's business.
func TestEveryReputationIsReadSomewhere(t *testing.T) {
	files, err := filepath.Glob("*.go")
	if err != nil || len(files) == 0 {
		t.Fatalf("cannot list the package: %v", err)
	}
	written, read := map[string]bool{}, map[string]bool{}
	for _, name := range files {
		if strings.HasSuffix(name, "_test.go") {
			continue
		}
		src, err := os.ReadFile(name)
		if err != nil {
			t.Fatal(err)
		}
		file, err := parser.ParseFile(token.NewFileSet(), name, src, 0)
		if err != nil {
			t.Fatal(err)
		}
		// A key a reader is handed through sectCircleKey is the circle it
		// returns: its returned literals are reads.
		for _, decl := range file.Decls {
			if fn, ok := decl.(*ast.FuncDecl); ok && fn.Name.Name == "sectCircleKey" {
				ast.Inspect(fn, func(n ast.Node) bool {
					if ret, ok := n.(*ast.ReturnStmt); ok {
						for _, res := range ret.Results {
							if key := reputationKeyOf(res); key != "" {
								read[key] = true
							}
						}
					}
					return true
				})
			}
		}
		ast.Inspect(file, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			id, ok := call.Fun.(*ast.Ident)
			if !ok {
				return true
			}
			switch {
			case id.Name == "adjustReputationTx" && len(call.Args) >= 3:
				if key := reputationKeyOf(call.Args[2]); key != "" {
					written[key] = true
				}
			case id.Name == "standingTx" && len(call.Args) >= 3:
				if key := reputationKeyOf(call.Args[2]); key != "" {
					read[key] = true
				}
			}
			return true
		})
		// Merciful Reputation is written by a raw INSERT beside the battle's
		// other bookkeeping, so it is named here rather than missed.
		if strings.Contains(string(src), `"Merciful Reputation", repDelta`) {
			written["Merciful Reputation"] = true
		}
	}
	if !written["Martial Society"] || !written["Merciful Reputation"] {
		t.Fatalf("the writer scan found %v; the gate is broken, not the tree", written)
	}
	unread := []string{}
	for key := range written {
		if !read[key] && !strings.EqualFold(key, "Underworld Contacts") {
			unread = append(unread, key)
		}
	}
	sort.Strings(unread)
	if len(unread) > 0 {
		t.Fatalf("reputations written and read by no rule: %v", unread)
	}
}

func TestStandingIsWorthSomethingAndIsCapped(t *testing.T) {
	if got := examFeeAfterStanding(100, 20); got != 80 {
		t.Fatalf("a hall standing of 20 left a 100 fee at %d, want 80", got)
	}
	if got := examFeeAfterStanding(100, 1000); got != 70 {
		t.Fatalf("the hall's discount is uncapped: %d", got)
	}
	if got := defeatFatalChance(0, 0); got != 8 {
		t.Fatalf("a same-realm defeat is fatal %d%% of the time, want 8", got)
	}
	if got := defeatFatalChance(0, 1000); got != 2 {
		t.Fatalf("mercy took the fatal chance to %d, want 8-6", got)
	}
	if got := standingBonus(-50, 10, 3); got != 0 {
		t.Fatalf("a negative standing paid %d", got)
	}
}

func TestAHiddenInitiateIsKnownToTheBrokers(t *testing.T) {
	conn, err := storage.Open(t.TempDir() + "/bm.sqlite3")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(`
CREATE TABLE faction_reputation(user_id INTEGER NOT NULL, faction_key TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0, last_reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(user_id,faction_key));
CREATE TABLE sect_membership(user_id INTEGER PRIMARY KEY, sect_name TEXT NOT NULL);
CREATE TABLE hidden_sect_membership(user_id INTEGER PRIMARY KEY, sect_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', updated_at REAL NOT NULL DEFAULT 0);
`); err != nil {
		t.Fatal(err)
	}
	catalog := eventScopeCatalog(t)
	who := map[string]any{"karma_score": 0}
	if why, _, _ := blackMarketAuthorized(conn, catalog, 42, who); why != "" {
		t.Fatalf("a stranger was let in as %q", why)
	}
	if _, err := conn.Execute(`INSERT INTO hidden_sect_membership(user_id,sect_name) VALUES(42,'Heaven-Devouring Demon Sect')`, nil); err != nil {
		t.Fatal(err)
	}
	if why, _, _ := blackMarketAuthorized(conn, catalog, 42, who); why != "hidden sect" {
		t.Fatalf("a hidden initiate was not known to the brokers: %q", why)
	}
	if _, err := conn.Execute(`INSERT INTO faction_reputation(user_id,faction_key,score) VALUES(43,'Demonic Circles',20)`, nil); err != nil {
		t.Fatal(err)
	}
	if why, _, _ := blackMarketAuthorized(conn, catalog, 43, who); why != "demonic circles" {
		t.Fatalf("a cultivator the Demonic Circles count as theirs was refused: %q", why)
	}
}

// --- from sendoff_backfill_reputation_test.go ---

// The backfill reports the connections it raises (v1.12.3).
//
// `householdReputationTx` runs ahead of the heirloom's once-guard so a
// household's contacts reach a character created before they were authored -
// and the function then returned nil on the guard, dropping the `reputation`
// block: the standing rose and `family.support` never said so.
func TestABackfilledHouseholdReportsTheContactsItRaised(t *testing.T) {
	path := sendoffDB(t)
	fid := sendoffFamily(t, path, "hidden_weapon_family", "uw-backfill", 3, 50)
	sendOut(t, path, 42, fid, "hidden_weapon_family", 0)
	// A character from before v1.11.1: the heirloom is already theirs, the
	// contacts never were.
	batch4Exec(t, path, `DELETE FROM faction_reputation WHERE user_id=42`)
	out := sendOut(t, path, 42, fid, "hidden_weapon_family", 0)
	if got := underworldStanding(t, path); got < blackMarketTrustReputation {
		t.Fatalf("the backfill did not raise the contacts: %d", got)
	}
	raised, _ := out["reputation"].(map[string]int64)
	if len(raised) == 0 {
		t.Fatalf("the backfill raised the contacts and did not report them: %v", out)
	}
	if _, gift := out["item_id"]; gift {
		t.Fatalf("a second heirloom was reported on the backfill: %v", out)
	}
	// And a third ask raises nothing and says nothing.
	if again := sendOut(t, path, 42, fid, "hidden_weapon_family", 0); again != nil {
		t.Fatalf("a floor already met was reported again: %v", again)
	}
}

// --- from underworld_contacts_test.go ---

// Underworld Contacts had one source, a trade at a black-market post, and a
// trade needed the very trust it was meant to earn - so the door by reputation
// could never open (v1.11.1). A broker buys from a stranger now, and five
// underworld households send their children out already known.

func underworldPostDB(t *testing.T) string {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS black_market_posts(world_name TEXT PRIMARY KEY,location TEXT NOT NULL,heat INTEGER NOT NULL DEFAULT 0,opens_game_minute INTEGER NOT NULL,closes_game_minute INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL,updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS black_market_stock(world_name TEXT NOT NULL,item_id TEXT NOT NULL,currency_id TEXT NOT NULL,unit_price INTEGER NOT NULL,quantity INTEGER NOT NULL DEFAULT 0,legal_status TEXT NOT NULL DEFAULT 'forbidden',updated_at REAL NOT NULL,PRIMARY KEY(world_name,item_id),FOREIGN KEY(world_name) REFERENCES black_market_posts(world_name) ON DELETE CASCADE)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS item_provenance(provenance_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,item_id TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 1,source_type TEXT NOT NULL,source_key TEXT NOT NULL DEFAULT '',ownership_mark TEXT NOT NULL DEFAULT '',legal_status TEXT NOT NULL DEFAULT 'clean',authenticity INTEGER NOT NULL DEFAULT 100,tracking_strength INTEGER NOT NULL DEFAULT 0,acquired_game_minute INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL)`)
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS sect_membership(user_id INTEGER PRIMARY KEY,sect_name TEXT NOT NULL,rank_name TEXT NOT NULL DEFAULT 'Disciple',rank_level INTEGER NOT NULL DEFAULT 0,joined_at REAL NOT NULL DEFAULT 0)`)
	// Heat 20 keeps a buy below the watch's notice (40), so no roll decides it.
	batch4Exec(t, path, `INSERT INTO black_market_posts(world_name,location,heat,opens_game_minute,closes_game_minute,active,created_at,updated_at) VALUES('Mortal World','Greenriver Town',20,0,999999999,1,0,0)`)
	batch4Exec(t, path, `INSERT INTO black_market_stock(world_name,item_id,currency_id,unit_price,quantity,legal_status,updated_at) VALUES('Mortal World','hundred_year_peach','low_spirit_stone',100,5,'restricted',0)`)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'hundred_year_peach',3) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=3`)
	batch4Exec(t, path, `UPDATE characters SET karma_score=50 WHERE user_id=42`)
	return path
}

func underworldStanding(t *testing.T, path string) int64 {
	t.Helper()
	return i64(actionScalar(t, path, `SELECT COALESCE((SELECT score FROM faction_reputation WHERE user_id=42 AND faction_key='Underworld Contacts'),0)`))
}

// A broker buys ordinary goods too (v1.35.0): under the cheapest shelf, and the
// standing it earns is one point a world day however much is fenced, where the
// post's own stock pays one a sale. A stranger's way to Underworld Contacts 15
// used to cost fifteen rare items no shelf sells.
func TestABrokerFencesOrdinaryGoodsForLessAndADaysTrustAtMost(t *testing.T) {
	path := underworldPostDB(t)
	world := batch4WorldPath(t)
	catalog := crossingCatalog(t)
	shelf, ok := cheapestShelfPrice(catalog, "qi_pill", "low_spirit_stone")
	if !ok || shelf < 2 {
		t.Fatalf("the shipped content gives qi_pill a shelf of %d (%v); pick an item a shelf sells for two or more", shelf, ok)
	}
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'qi_pill',6) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=6`)
	// The day's point is keyed in event_log, as a battle's daily reward is.
	batch4Exec(t, path, `CREATE TABLE IF NOT EXISTS event_log(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,event_type TEXT NOT NULL,payload_json TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL DEFAULT 0)`)
	// A stranger cannot buy a pill the post does not stock.
	if _, err := batch4ApplyErr(path, world, "black_market.trade", 42, 11, map[string]any{"item_id": "qi_pill", "quantity": 1, "buy": true}); err == nil {
		t.Fatal("a broker sold a pill the post does not stock")
	}
	sold := batch4Result(t, batch4Apply(t, path, world, "black_market.trade", 12, map[string]any{"item_id": "qi_pill", "quantity": 2, "buy": false, "game_minute": 100}))
	if sold["ordinary"] != true || sold["trust_gained"] != true || i64(sold["underworld_reputation"]) != 1 {
		t.Fatalf("the day's first ordinary fence: %v", sold)
	}
	want, ok := blackMarketOrdinaryFence(catalog, "qi_pill", "low_spirit_stone")
	if !ok || want >= shelf {
		t.Fatalf("the ordinary fence prices qi_pill at %d (%v) against a shelf of %d; a fence sits under every shelf", want, ok, shelf)
	}
	if unit := i64(sold["unit_price"]); unit != want {
		t.Fatalf("a broker paid %d for a pill the ordinary fence prices at %d: one door", unit, want)
	}
	// The same day's second fence earns money and no standing.
	again := batch4Result(t, batch4Apply(t, path, world, "black_market.trade", 13, map[string]any{"item_id": "qi_pill", "quantity": 2, "buy": false, "game_minute": 200}))
	if again["trust_gained"] != false || i64(again["underworld_reputation"]) != 1 || underworldStanding(t, path) != 1 {
		t.Fatalf("a second ordinary fence the same day moved the standing: %v (standing %d)", again, underworldStanding(t, path))
	}
	// The next world day earns the next point.
	tomorrow := batch4Result(t, batch4Apply(t, path, world, "black_market.trade", 14, map[string]any{"item_id": "qi_pill", "quantity": 1, "buy": false, "game_minute": 100 + gameMinutesPerDay}))
	if tomorrow["trust_gained"] != true || underworldStanding(t, path) != 2 {
		t.Fatalf("the next day's fence earned nothing: %v (standing %d)", tomorrow, underworldStanding(t, path))
	}
	// And an item no shelf sells is still refused.
	if _, err := batch4ApplyErr(path, world, "black_market.trade", 42, 15, map[string]any{"item_id": "no_such_item", "quantity": 1, "buy": false}); err == nil {
		t.Fatal("a broker bought something that is not an item")
	}
}

func TestABrokerBuysFromAStrangerAndThatBuildsTrust(t *testing.T) {
	path := underworldPostDB(t)
	world := batch4WorldPath(t)
	if _, err := batch4ApplyErr(path, world, "black_market.trade", 42, 1, map[string]any{"item_id": "hundred_year_peach", "quantity": 1, "buy": true}); err == nil || !strings.Contains(err.Error(), "will buy from a stranger") {
		t.Fatalf("a stranger must be refused a purchase, and told the way in: %v", err)
	}
	sold := batch4Result(t, batch4Apply(t, path, world, "black_market.trade", 2, map[string]any{"item_id": "hundred_year_peach", "quantity": 1, "buy": false}))
	if sold["access"] != "fencing as a stranger" || i64(sold["underworld_reputation"]) != 1 || i64(sold["trust_reputation"]) != blackMarketTrustReputation {
		t.Fatalf("a stranger fences and the fence is the first point of trust: %v", sold)
	}
	if got := underworldStanding(t, path); got != 1 {
		t.Fatalf("the fence did not raise Underworld Contacts: %d", got)
	}
	// One fence short of trust, then the fence that earns it.
	batch4Exec(t, path, `UPDATE faction_reputation SET score=? WHERE user_id=42 AND faction_key='Underworld Contacts'`, blackMarketTrustReputation-1)
	batch4Apply(t, path, world, "black_market.trade", 3, map[string]any{"item_id": "hundred_year_peach", "quantity": 1, "buy": false})
	bought := batch4Result(t, batch4Apply(t, path, world, "black_market.trade", 4, map[string]any{"item_id": "hundred_year_peach", "quantity": 1, "buy": true}))
	if bought["access"] != "underworld contacts" {
		t.Fatalf("fifteen fences are the trust to buy: %v", bought["access"])
	}
}

func TestAnUnderworldHouseholdSendsItsChildOutKnown(t *testing.T) {
	catalog := districtCatalog(t)
	houses := 0
	for archetype, sendoff := range catalog.BirthFamilySendoff {
		if n := sendoff.Reputation["Underworld Contacts"]; n > 0 {
			houses++
			if n < blackMarketTrustReputation {
				t.Fatalf("%s grants Underworld Contacts %d, short of the %d a broker sells at", archetype, n, blackMarketTrustReputation)
			}
		}
	}
	if houses < 1 {
		t.Fatal("no household grants Underworld Contacts; the content read is broken, not the tree")
	}
	if catalog.BirthFamilySendoff["hidden_weapon_family"].Reputation["Underworld Contacts"] <= 0 {
		t.Fatal("the Hidden-Weapon family's discreet underworld contacts are prose again")
	}

	path := sendoffDB(t)
	first := sendoffFamily(t, path, "hidden_weapon_family", "uw-1", 3, 50)
	out := sendOut(t, path, 42, first, "hidden_weapon_family", 0)
	if got := underworldStanding(t, path); got != 15 {
		t.Fatalf("the household's contacts did not come with the child: %d (%v)", got, out["reputation"])
	}
	// A floor, not a grant: a second household of the same kind adds nothing
	// to standing already earned past it.
	batch4Exec(t, path, `UPDATE faction_reputation SET score=20 WHERE user_id=42 AND faction_key='Underworld Contacts'`)
	second := sendoffFamily(t, path, "hidden_weapon_family", "uw-2", 3, 50)
	again := sendOut(t, path, 42, second, "hidden_weapon_family", 0)
	if got := underworldStanding(t, path); got != 20 {
		t.Fatalf("a second household stacked its contacts: %d", got)
	}
	if _, raised := again["reputation"]; raised {
		t.Fatalf("a floor already met must not be reported as raised: %v", again["reputation"])
	}
}

// A broker pays for the post's own stock under every shelf too (v1.36.0). The
// post's asking price is its own arithmetic - base, scarcity, heat - and the
// broker used to pay 0.55 of it however it compared with the shelves, so a
// stock row priced above a shelf (a hot post, a scarce good, or a shelf priced
// under its base) paid a cultivator more than the shelf sold for. The pay goes
// through blackMarketStockPay now, the door the arbitrage gate walks.
func TestABrokerPaysForItsOwnStockUnderTheCheapestShelf(t *testing.T) {
	path := underworldPostDB(t)
	world := batch4WorldPath(t)
	catalog := crossingCatalog(t)
	shelf, ok := cheapestShelfPrice(catalog, "qi_pill", "low_spirit_stone")
	if !ok || shelf < 2 {
		t.Fatalf("the shipped content gives qi_pill a shelf of %d (%v); pick an item a shelf sells for two or more", shelf, ok)
	}
	// The post stocks the pill at ten times any shelf: a rotation at heat 80
	// and a single unit left prices the ask well above the base, and the
	// row is what the trade reads.
	batch4Exec(t, path, `INSERT INTO black_market_stock(world_name,item_id,currency_id,unit_price,quantity,legal_status,updated_at) VALUES('Mortal World','qi_pill','low_spirit_stone',?,2,'restricted',0)`, shelf*10)
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'qi_pill',3) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=3`)
	sold := batch4Result(t, batch4Apply(t, path, world, "black_market.trade", 21, map[string]any{"item_id": "qi_pill", "quantity": 1, "buy": false}))
	if sold["ordinary"] == true {
		t.Fatalf("a stocked good went through the ordinary fence: %v", sold)
	}
	unit := i64(sold["unit_price"])
	if unit >= shelf {
		t.Fatalf("a broker paid %d for a pill the cheapest shelf sells for %d: list it, buy it there and sell it here, and the post prints money", unit, shelf)
	}
	if want := blackMarketStockPay(catalog, "qi_pill", "low_spirit_stone", shelf*10); unit != want {
		t.Fatalf("the broker paid %d and blackMarketStockPay says %d: one door", unit, want)
	}
	// The row is bounded by the engine's own dearest ask before the share is
	// taken, so a row priced ten shelves pays what a real row at the hottest
	// post would, and never over the shelf.
	base, ok := UnderworldBase(catalog, "qi_pill", "low_spirit_stone")
	if !ok {
		t.Fatal("qi_pill has no base in the Mortal stone")
	}
	if want := min64(shelf-1, max64(1, int64(math.Round(float64(blackMarketDearestAsk(base))*blackMarketStockShare)))); unit != want {
		t.Fatalf("a row at ten shelves pays %d; the dearest real ask (%d) shares to %d under the shelf %d", unit, blackMarketDearestAsk(base), want, shelf)
	}
	// Under the shelf the share is the share.
	if got, want := blackMarketStockPay(catalog, "qi_pill", "low_spirit_stone", 2), int64(1); got != want {
		t.Fatalf("an ask of 2 pays %d, want %d (the floor of one coin)", got, want)
	}
	// And an upper-world post pays nothing for a Mortal pill, because it never
	// stocked one: less than a coin of crystal, it has no base there.
	if got := blackMarketStockPay(catalog, "qi_pill", worldBaseCurrency(catalog, "Celestial World"), 1<<40); got != 0 {
		t.Fatalf("a Celestial broker pays %d crystals for a Mortal pill worth a few stones", got)
	}
}

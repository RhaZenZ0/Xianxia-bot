package game

import (
	"go/ast"
	"go/parser"
	"go/token"
	"strconv"
	"testing"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// What tempers the body besides a body session (v1.20.0): a share of a
// session for a deed done, and ground that tempers flesh. Every test drives
// the shipped catalogue, because the shares and the grounds are content.

func bodyTemperingFixture(t *testing.T) (string, worlddata.Catalog) {
	t.Helper()
	world := batch4WorldPath(t)
	catalog, err := worlddata.Load(world)
	if err != nil {
		t.Fatal(err)
	}
	return world, catalog
}

func bodyEssence(t *testing.T, path string) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	r, err := conn.Execute(`SELECT body_cultivation FROM characters WHERE user_id=42`, nil)
	if err != nil {
		t.Fatal(err)
	}
	return i64(firstRowMap(r)["body_cultivation"])
}

func temperOnce(t *testing.T, path string, catalog worlddata.Catalog, deed string) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	gain, err := temperBodyByUseTx(conn, catalog, 42, deed, 1)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return gain
}

func TestEveryDeedTempersAQuarterOfASession(t *testing.T) {
	_, catalog := bodyTemperingFixture(t)
	path := setupBatch5AuthorityDB(t)
	cost, err := phaseCost(catalog.BodyRealms, 0, 1)
	if err != nil {
		t.Fatal(err)
	}
	for _, deed := range []string{"hunt", "mine", "battle"} {
		share := catalog.BodyTempering.ByUse[deed]
		if share <= 0 {
			t.Fatalf("the content names no share for %q", deed)
		}
		batch4Exec(t, path, `UPDATE characters SET body_cultivation=0 WHERE user_id=42`)
		want := int64(float64(stagePace(cost, 0))*share + 0.5)
		if got := temperOnce(t, path, catalog, deed); got != want || bodyEssence(t, path) != want {
			t.Fatalf("%s tempered %d (stored %d), want %d: a share of %.2f of a %d-essence session", deed, got, bodyEssence(t, path), want, share, stagePace(cost, 0))
		}
	}
}

func TestTemperingNeverFillsPastTheStage(t *testing.T) {
	_, catalog := bodyTemperingFixture(t)
	path := setupBatch5AuthorityDB(t)
	cost, err := phaseCost(catalog.BodyRealms, 0, 1)
	if err != nil {
		t.Fatal(err)
	}
	batch4Exec(t, path, `UPDATE characters SET body_cultivation=? WHERE user_id=42`, cost-1)
	if got := temperOnce(t, path, catalog, "hunt"); got != 1 {
		t.Fatalf("one essence short of a full stage, a hunt tempered %d, want 1", got)
	}
	if got := temperOnce(t, path, catalog, "hunt"); got != 0 || bodyEssence(t, path) != cost {
		t.Fatalf("a full stage took %d more (stored %d of %d); the breakthrough is the player's to attempt", got, bodyEssence(t, path), cost)
	}
}

func TestADeedTheContentDoesNotNameTempersNothing(t *testing.T) {
	_, catalog := bodyTemperingFixture(t)
	path := setupBatch5AuthorityDB(t)
	if got := temperOnce(t, path, catalog, "forage"); got != 0 || bodyEssence(t, path) != 0 {
		t.Fatalf("an unnamed deed tempered %d", got)
	}
}

func TestADigTempersTheBodyThatSwungThePick(t *testing.T) {
	// Through the real action: the dice are lent, so the dig lands, and the
	// result names what it tempered.
	defer everyMakingIsFound()()
	world, _ := bodyTemperingFixture(t)
	path := setupBatch5AuthorityDB(t)
	setupForageEffectAuthorityTables(t, path)
	batch4Exec(t, path, `INSERT INTO civilization_regions(location,world_name,spirit_resources) VALUES(?,?,?)`, "Greenriver Town", "Mortal World", 100)
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=42`)
	result := batch4Result(t, batch4Apply(t, path, world, "exploration.mine", 1, map[string]any{}))
	if !result["success"].(bool) {
		t.Fatalf("a dig with the dice lent missed: %v", result["roll"])
	}
	tempered := storage.ParseInt(result["body_tempered"])
	if tempered <= 0 || bodyEssence(t, path) != tempered {
		t.Fatalf("a successful dig tempered %d (stored %d); a dig tempers the body", tempered, bodyEssence(t, path))
	}
}

// The helper's own tests pass against a tree nothing calls it from
// (`TestEveryGoodDeedIsPaidWhereItHappens`' reason), so the three doors are
// read: each deed's action calls the helper with its own key.
func TestEveryDeedTempersWhereItHappens(t *testing.T) {
	want := map[string]string{
		"explorationHuntAction": "hunt",
		"explorationMineAction": "mine",
		"combatFinalizeAction":  "battle",
	}
	found := map[string]string{}
	fset := token.NewFileSet()
	for _, file := range []string{"exploration_actions.go", "mining.go", "combat_actions.go"} {
		parsed, err := parser.ParseFile(fset, file, nil, 0)
		if err != nil {
			t.Fatalf("%s: %v", file, err)
		}
		for _, decl := range parsed.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok {
				continue
			}
			if _, wanted := want[fn.Name.Name]; !wanted {
				continue
			}
			ast.Inspect(fn, func(n ast.Node) bool {
				call, ok := n.(*ast.CallExpr)
				if !ok {
					return true
				}
				if ident, ok := call.Fun.(*ast.Ident); ok && ident.Name == "temperBodyByUseTx" && len(call.Args) >= 4 {
					if lit, ok := call.Args[3].(*ast.BasicLit); ok {
						found[fn.Name.Name], _ = strconv.Unquote(lit.Value)
					}
				}
				return true
			})
		}
	}
	for fn, deed := range want {
		if found[fn] != deed {
			t.Errorf("%s does not temper the body as %q (found %q)", fn, deed, found[fn])
		}
	}
}

// groundFixture is the sect-residence schema (it carries every table the two
// cultivation doors read) plus the deployed-array table the qi door reads.
func groundFixture(t *testing.T) *storage.Conn {
	t.Helper()
	conn := mustOpen(t, setupSectResidenceDB(t))
	if _, err := conn.Execute(`CREATE TABLE IF NOT EXISTS deployed_location_arrays(location TEXT,item_id TEXT,name TEXT,effect_json TEXT,starts_game_minute INTEGER,ends_game_minute INTEGER)`, nil); err != nil {
		t.Fatal(err)
	}
	return conn
}

func TestTheGroundTempersTheBodyAndNotTheQi(t *testing.T) {
	_, catalog := bodyTemperingFixture(t)
	conn := groundFixture(t)
	defer conn.Close()
	cases := map[string]float64{
		"Boar Hollow Hunting Ground": catalog.BodyTempering.Grounds.RoadSites["hunting_ground"],
		"Emberforge Forge Terraces":  catalog.BodyTempering.Grounds.Districts["forge"],
		"Ironbanner Garrison Ward":   catalog.BodyTempering.Grounds.Districts["garrison"],
		"Cloudspine Foothills":       catalog.BodyTempering.Grounds.Wilds,
	}
	for location, want := range cases {
		if want <= 1 {
			t.Fatalf("%s: the content gives this ground %.2f; the test is vacuous", location, want)
		}
		name, mult, err := placeMultiplierForPath(conn, catalog, 42, location, 0, true)
		if err != nil {
			t.Fatal(err)
		}
		if mult != round4(want) || name != location {
			t.Errorf("a body session at %s is worth x%.2f (%q), want x%.2f", location, mult, name, want)
		}
		if _, qi, err := placeMultiplierForPath(conn, catalog, 42, location, 0, false); err != nil || qi != 1 {
			t.Errorf("a qi session at %s is worth x%.2f; a tempering ground is the body's alone (err %v)", location, qi, err)
		}
	}
	if name, mult := bodyTemperingGround(catalog, "Greenriver Town"); name != "" || mult != 1 {
		t.Fatalf("a town's street tempers the body x%.2f (%q)", mult, name)
	}
}

func TestABodyRetreatOnTemperingGroundCountsIt(t *testing.T) {
	// v1.2.3's rule: the ground is one rule at both cultivation doors. The
	// forge terraces are a safe city district, so a retreat may be held there.
	_, catalog := bodyTemperingFixture(t)
	conn := groundFixture(t)
	defer conn.Close()
	want := catalog.BodyTempering.Grounds.Districts["forge"]
	env, body, err := seclusionEnvironmentGo(conn, catalog, 42, "Emberforge Forge Terraces", "body", 0)
	if err != nil {
		t.Fatal(err)
	}
	_, qi, err := seclusionEnvironmentGo(conn, catalog, 42, "Emberforge Forge Terraces", "qi", 0)
	if err != nil {
		t.Fatal(err)
	}
	if body != round4(qi*want) || env["tempering_ground"] != "Emberforge Forge Terraces" {
		t.Fatalf("a body retreat at the forge terraces is worth x%.4f, a qi one x%.4f; want the body x%.2f above it (env %v)", body, qi, want, env)
	}
}

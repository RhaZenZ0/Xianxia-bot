package game

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A place's floor is measured on the ladder that carried the cultivator there.
//
// `accessRealmIndex` is the higher of the qi and body ladders, and its own
// comment states the rule: a body cultivator clears the Mortal Body Ascension
// and breaks into the Spiritual World's body realm exactly as a qi cultivator
// clears theirs, so whichever ladder walked them to a place is the one that
// answers for it. Travel, the realm capitals, the stalls and a raised gate's
// opener all read it. Three doors read `realm_index` alone and so refused
// somebody the road had just let in: `sect.ascend` at the allied gate,
// `array.use` at the crossing arrays (authored and raised), and
// `caravan.dispatch`, whose road planner drops every place above the realm it
// is handed - the origin included - so a body cultivator at the capital of any
// world above the Mortal one was told "no canonical road route" about a road
// they had just walked.

// placeFloorChar seats character 42 on the two ladders: a body cultivator is
// one whose body stage carried them to a place the qi stage has not reached.
func placeFloorChar(t *testing.T, path, location string, qi, body int64) {
	t.Helper()
	batch4Exec(t, path, fmt.Sprintf(`UPDATE characters SET location='%s',realm_index=%d,body_realm_index=%d WHERE user_id=42`, location, qi, body))
}

func useArray(t *testing.T, path string, id string) error {
	t.Helper()
	catalog := crossingCatalog(t)
	return crossingApply(t, path, func(conn *storage.Conn) error {
		_, err := teleportArrayActionGo(conn, catalog, 42, json.RawMessage(`{"array_id":"`+id+`","game_minute":1000}`))
		return err
	})
}

func TestABodyCultivatorRidesTheAuthoredArraysBothWays(t *testing.T) {
	path := crossingDB(t)
	catalog := crossingCatalog(t)
	up, down := catalog.TeleportArrays["imperial_spirit"], catalog.TeleportArrays["spirit_imperial"]
	if up.MinRealmIndex < 5 || down.MinRealmIndex != up.MinRealmIndex || up.To != down.From {
		t.Fatalf("the shipped arrays no longer pair a Mortal gate with its return at one floor (%+v, %+v); the fixture is wrong, not the rule", up, down)
	}
	batch4Exec(t, path, `UPDATE currency_wallets SET balance=90000 WHERE user_id=42 AND currency_id='low_spirit_stone'`)

	placeFloorChar(t, path, up.From, 3, up.MinRealmIndex)
	if err := useArray(t, path, "imperial_spirit"); err != nil {
		t.Fatalf("body %d / qi 3 was refused the way up: %v", up.MinRealmIndex, err)
	}
	if got := actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`); got != up.To {
		t.Fatalf("still standing at %v", got)
	}
	if err := useArray(t, path, "spirit_imperial"); err != nil {
		t.Fatalf("body %d / qi 3 was refused the way down: %v", down.MinRealmIndex, err)
	}
	if got := actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`); got != down.To {
		t.Fatalf("still standing at %v", got)
	}

	// One stage short on both ladders is still short: the rule is the higher
	// ladder, not either.
	placeFloorChar(t, path, up.From, 3, up.MinRealmIndex-1)
	if err := useArray(t, path, "imperial_spirit"); err == nil || !strings.Contains(err.Error(), "cannot withstand") {
		t.Fatalf("body %d / qi 3 passed an array that asks %d: err=%v", up.MinRealmIndex-1, up.MinRealmIndex, err)
	}
}

// The seam is cut at the realm that carried the opener (`opened_realm_index`),
// so a body cultivator who survived the storm is the one a qi-only door
// locked out of their own gate.
func TestABodyCultivatorWalksThroughTheGateTheyTore(t *testing.T) {
	path := crossingDB(t)
	catalog := crossingCatalog(t)
	placeFloorChar(t, path, "Greenriver Town", 3, 8)
	batch4Exec(t, path, `INSERT INTO tribulation_state(user_id,gate_realm_index,cleared,updated_at) VALUES(42,7,1,0)`)
	batch4Exec(t, path, `UPDATE currency_wallets SET balance=9000 WHERE user_id=42 AND currency_id='low_spirit_stone'`)
	gate, err := raiseGate(t, path, catalog, 42)
	if err != nil {
		t.Fatal(err)
	}
	if i64(gate["opened_realm_index"]) != 8 {
		t.Fatalf("the seam was cut at %v, not at the body stage that carried the opener", gate["opened_realm_index"])
	}
	if err := useArray(t, path, fmtArrayID(gate)); err != nil {
		t.Fatalf("the opener could not step through the gate they tore: %v", err)
	}
	if got := actionScalar(t, path, `SELECT location FROM characters WHERE user_id=42`); got != "Spirit Jade Capital" {
		t.Fatalf("still standing at %v", got)
	}
}

func TestABodyCultivatorSendsACaravanFromTheCapitalOfAHigherWorld(t *testing.T) {
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	catalog := crossingCatalog(t)
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
	capital, destination := "Spirit Jade Capital", "Jadeflow Spirit City"
	if catalog.Locations[capital].MinRealmIndex < 5 || catalog.Locations[destination].World != catalog.Locations[capital].World {
		t.Fatalf("the shipped Spiritual World no longer opens at a high floor with a road city beside its capital; the fixture is wrong, not the rule")
	}
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'spirit_herb',10)
		ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=10`)
	batch4Exec(t, path, `INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(42,'low_spirit_crystal',5000)
		ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=5000`)
	raw, err := json.Marshal(map[string]any{"destination": destination, "item_id": "spirit_herb", "quantity": 2, "escort": 0})
	if err != nil {
		t.Fatal(err)
	}
	dispatch := func(id string) (ActionResponse, error) {
		return ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: id,
			Operation: "caravan.dispatch", ActorID: 42, Payload: raw})
	}

	// A qi ladder at 3 and a body ladder at 0 is nobody who can stand in this
	// capital at all; the road is still closed to them.
	placeFloorChar(t, path, capital, 3, 0)
	if _, err := dispatch("place-floor-caravan-refused"); err == nil || !strings.Contains(err.Error(), "no canonical road route") {
		t.Fatalf("a cultivator on neither ladder's stage dispatched from the capital: err=%v", err)
	}
	if n := actionScalar(t, path, `SELECT COUNT(*) FROM caravans`); fmt.Sprint(n) != "0" {
		t.Fatalf("a refused dispatch left %v caravans behind", n)
	}

	placeFloorChar(t, path, capital, 3, catalog.Locations[capital].MinRealmIndex)
	out, err := dispatch("place-floor-caravan-body")
	if err != nil {
		t.Fatalf("body %d / qi 3 at %s was refused: %v", catalog.Locations[capital].MinRealmIndex, capital, err)
	}
	result, _ := out.Result.(map[string]any)
	if result["origin"] != capital || result["destination"] != destination {
		t.Fatalf("the caravan went %v -> %v", result["origin"], result["destination"])
	}
}

// --- the gate ----------------------------------------------------------------

// placeFloorHelpers are the helpers that take the realm a road, a frontier or a
// search area is measured against, and where that parameter is. Each of them
// drops every place whose floor is above it, so the realm handed in is the
// one that decides which places exist for the caller.
var placeFloorHelpers = map[string]int{
	"canonicalRoadRoute":       3,
	"canonicalRoadRouteRiding": 3,
	"canonicalRoadNeighbors":   2,
	"roadSiteHop":              3,
	"roadSiteCandidates":       3,
	"roadFrontierTx":           2,
	"wildsCandidates":          3,
	"discoveryCandidates":      3,
	"exploreSearchArea":        2,
	"searchHereTx":             4,
}

// placeFloorAllowed is empty on the day it was written. A qi-only measure of a
// place is a decision for the strength floors (a Law, a flame, a secret
// realm's rooms), none of which read a place's floor.
var placeFloorAllowed = map[string]string{}

type placeFloorScan struct {
	findings   []string
	floorSeen  map[string]int
	helperSeen map[string]map[string]int
	declared   map[string][]string
}

func newPlaceFloorScan() *placeFloorScan {
	return &placeFloorScan{floorSeen: map[string]int{}, helperSeen: map[string]map[string]int{}, declared: map[string][]string{}}
}

func isPlaceTable(e ast.Expr) bool {
	sel, ok := e.(*ast.SelectorExpr)
	return ok && (sel.Sel.Name == "Locations" || sel.Sel.Name == "TeleportArrays")
}

func isPlaceSource(e ast.Expr) bool {
	switch x := e.(type) {
	case *ast.ParenExpr:
		return isPlaceSource(x.X)
	case *ast.IndexExpr:
		return isPlaceTable(x.X)
	}
	return false
}

// scan reads one file. A place floor is a `MinRealmIndex` read off a location
// or a teleport array (directly, or through a variable assigned from one), a
// variable assigned from such a read, or `worldMinRealm`; a qi-only read is
// `.RealmIndex`, a `"realm_index"` map read (bare or under i64, int64,
// ParseInt or intFromDB) or a variable assigned from one. It is a shape
// detector, not a type checker: it cannot tell two variables sharing a name
// apart, so it keeps to the shapes this tree uses, and the empty allowlist is
// what keeps a false positive from being quietly carried.
func (s *placeFloorScan) scan(fset *token.FileSet, file *ast.File) {
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok {
			continue
		}
		if _, listed := placeFloorHelpers[fn.Name.Name]; listed && fn.Recv == nil {
			names := []string{}
			for _, field := range fn.Type.Params.List {
				for _, name := range field.Names {
					names = append(names, name.Name)
				}
			}
			s.declared[fn.Name.Name] = names
		}
		if fn.Body == nil {
			continue
		}
		s.scanBody(fset, fn.Name.Name, fn.Body)
	}
}

func (s *placeFloorScan) scanBody(fset *token.FileSet, fnName string, body *ast.BlockStmt) {
	placeVars, floorVars, qiVars := map[string]bool{}, map[string]bool{}, map[string]bool{}
	var isFloor, isQi func(ast.Expr) bool
	isFloor = func(e ast.Expr) bool {
		switch x := e.(type) {
		case *ast.ParenExpr:
			return isFloor(x.X)
		case *ast.Ident:
			return floorVars[x.Name]
		case *ast.CallExpr:
			id, ok := x.Fun.(*ast.Ident)
			return ok && id.Name == "worldMinRealm"
		case *ast.SelectorExpr:
			if x.Sel.Name != "MinRealmIndex" {
				return false
			}
			if isPlaceSource(x.X) {
				return true
			}
			id, ok := x.X.(*ast.Ident)
			return ok && placeVars[id.Name]
		}
		return false
	}
	isQi = func(e ast.Expr) bool {
		switch x := e.(type) {
		case *ast.ParenExpr:
			return isQi(x.X)
		case *ast.Ident:
			return qiVars[x.Name]
		case *ast.SelectorExpr:
			return x.Sel.Name == "RealmIndex"
		case *ast.IndexExpr:
			key, ok := x.Index.(*ast.BasicLit)
			return ok && key.Kind == token.STRING && key.Value == `"realm_index"`
		case *ast.CallExpr:
			name := ""
			switch f := x.Fun.(type) {
			case *ast.Ident:
				name = f.Name
			case *ast.SelectorExpr:
				name = f.Sel.Name
			}
			switch name {
			case "i64", "int64", "ParseInt", "intFromDB":
				for _, arg := range x.Args {
					if isQi(arg) {
						return true
					}
				}
			}
		}
		return false
	}
	note := func(name ast.Expr, value ast.Expr) {
		id, ok := name.(*ast.Ident)
		if !ok || id.Name == "_" {
			return
		}
		if isPlaceSource(value) {
			placeVars[id.Name] = true
		}
		if isFloor(value) {
			floorVars[id.Name] = true
		}
		if isQi(value) {
			qiVars[id.Name] = true
		}
	}
	ast.Inspect(body, func(n ast.Node) bool {
		switch x := n.(type) {
		case *ast.AssignStmt:
			if len(x.Lhs) == len(x.Rhs) {
				for i := range x.Lhs {
					note(x.Lhs[i], x.Rhs[i])
				}
			} else if len(x.Rhs) == 1 && len(x.Lhs) > 0 {
				note(x.Lhs[0], x.Rhs[0])
			}
		case *ast.RangeStmt:
			if x.Value != nil && isPlaceTable(x.X) {
				if id, ok := x.Value.(*ast.Ident); ok {
					placeVars[id.Name] = true
				}
			}
		case *ast.BinaryExpr:
			switch x.Op {
			case token.LSS, token.LEQ, token.GTR, token.GEQ:
				floorX, floorY := isFloor(x.X), isFloor(x.Y)
				if floorX || floorY {
					s.floorSeen[fnName]++
				}
				if (floorX && isQi(x.Y)) || (floorY && isQi(x.X)) {
					s.findings = append(s.findings, fmt.Sprintf("%s %s: a place floor is compared with the qi ladder alone", fset.Position(x.Pos()), fnName))
				}
			}
		case *ast.CallExpr:
			id, ok := x.Fun.(*ast.Ident)
			if !ok {
				return true
			}
			pos, listed := placeFloorHelpers[id.Name]
			if !listed {
				return true
			}
			if s.helperSeen[fnName] == nil {
				s.helperSeen[fnName] = map[string]int{}
			}
			s.helperSeen[fnName][id.Name]++
			if pos < len(x.Args) && isQi(x.Args[pos]) {
				p := fset.Position(x.Pos())
				s.findings = append(s.findings, fmt.Sprintf("%s:%d %s -> %s", filepath.Base(p.Filename), p.Line, fnName, id.Name))
			}
		}
		return true
	})
}

func (s *placeFloorScan) unlisted() []string {
	out := []string{}
	for _, f := range s.findings {
		allowed := false
		for key := range placeFloorAllowed {
			if strings.Contains(f, key) {
				allowed = true
			}
		}
		if !allowed {
			out = append(out, f)
		}
	}
	sort.Strings(out)
	return out
}

func TestAPlaceFloorIsMeasuredOnTheHigherLadder(t *testing.T) {
	scan := newPlaceFloorScan()
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	scanned := 0
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		fset := token.NewFileSet()
		file, err := parser.ParseFile(fset, name, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		scanned++
		scan.scan(fset, file)
	}
	if scanned < 50 {
		t.Fatalf("scanned %d files; the walk is broken, not the tree", scanned)
	}
	// A reader is asserted before it is trusted: every parameter this gate
	// reads by position must still be the one named for the realm, and the
	// gate must still see the three floors and the call it was written for.
	for helper, pos := range placeFloorHelpers {
		names := scan.declared[helper]
		if pos >= len(names) || names[pos] != "realmIndex" {
			t.Fatalf("%s no longer takes realmIndex at argument %d (%v); the gate's table is stale, not the tree", helper, pos, names)
		}
	}
	for _, fn := range []string{"sectAscendActionGo", "teleportArrayActionGo", "planTravelTx"} {
		if scan.floorSeen[fn] == 0 {
			t.Fatalf("the gate no longer sees a place floor compared in %s; the walk is broken, not the tree", fn)
		}
	}
	if scan.helperSeen["caravanDispatchActionGo"]["canonicalRoadRoute"] == 0 {
		t.Fatalf("the gate no longer sees the caravan's route call; the walk is broken, not the tree")
	}
	if found := scan.unlisted(); len(found) > 0 {
		t.Fatalf("a place is measured on the qi ladder alone; travel measures it on accessRealmIndex(), the higher of the qi and body ladders, "+
			"so a body cultivator is walked to a place and refused at it:\n  %s", strings.Join(found, "\n  "))
	}
}

// The gate reads shapes, so it is held against source that carries each shape
// it forbids and each it must leave alone. Without this a quietly emptied walk
// would pass for a clean tree.
func TestThePlaceFloorGateSeesWhatItForbids(t *testing.T) {
	const src = `package game

func comparesAFloorWithTheQiLadder(catalog Catalog, c mechanicsCharacter) bool {
	if floor := catalog.Locations[gate].MinRealmIndex; c.RealmIndex < floor {
		return false
	}
	return true
}

func comparesAnArrayWithARowRead(catalog Catalog, row map[string]any) bool {
	d := catalog.TeleportArrays["x"]
	return i64(row["realm_index"]) < d.MinRealmIndex
}

func comparesThroughAVariable(catalog Catalog, c mechanicsCharacter) bool {
	realm := c.RealmIndex
	dest := catalog.Locations["y"]
	return realm >= dest.MinRealmIndex
}

func routesOnTheQiLadder(catalog Catalog, c map[string]any) {
	canonicalRoadRoute(catalog, "a", "b", i64(c["realm_index"]))
	searchHereTx(conn, catalog, 1, "here", storage.ParseInt(c["realm_index"]), 0)
}

func measuresOnTheHigherLadder(catalog Catalog, c mechanicsCharacter) bool {
	canonicalRoadRoute(catalog, "a", "b", c.accessRealmIndex())
	canonicalRoadNeighbors(catalog, "a", c.accessRealmIndex())
	dest := catalog.Locations["y"]
	return c.accessRealmIndex() < dest.MinRealmIndex || c.RealmIndex < secretRealm.MinRealmIndex
}
`
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "synthetic.go", src, 0)
	if err != nil {
		t.Fatal(err)
	}
	scan := newPlaceFloorScan()
	scan.scan(fset, file)
	got := strings.Join(scan.unlisted(), "\n")
	for _, want := range []string{
		"comparesAFloorWithTheQiLadder: a place floor is compared with the qi ladder alone",
		"comparesAnArrayWithARowRead: a place floor is compared with the qi ladder alone",
		"comparesThroughAVariable: a place floor is compared with the qi ladder alone",
		"synthetic.go:22 routesOnTheQiLadder -> canonicalRoadRoute",
		"synthetic.go:23 routesOnTheQiLadder -> searchHereTx",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("the gate missed %q; it saw:\n%s", want, got)
		}
	}
	if strings.Contains(got, "measuresOnTheHigherLadder") {
		t.Errorf("the gate flagged the higher-ladder shapes it is there to accept:\n%s", got)
	}
	if n := len(scan.unlisted()); n != 5 {
		t.Errorf("the gate found %d things in the synthetic source, want 5:\n%s", n, got)
	}
}

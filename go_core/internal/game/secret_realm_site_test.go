package game

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

// Reported from play (v1.31.1): the Stygian Lantern Tomb's scene, opened by
// the rotation, had nothing in it - no site to work, only "Event Actions" and
// a stance menu. Every door that opens a secret realm wrote its world_events
// row and never called SpawnWorldEventNodes, so all four were the empty room
// schema 42 was written to end.

func addEventSiteTables(t *testing.T, path string) {
	t.Helper()
	batch4Exec(t, path, worldEventNodesDDL)
	batch4Exec(t, path, worldEventNPCsDDL)
}

func siteNodeCount(t *testing.T, path, eventKey string) (nodes, tasks int64) {
	t.Helper()
	nodes = i64(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_nodes WHERE event_key=?`, eventKey))
	tasks = i64(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_nodes WHERE event_key=? AND node_type='task'`, eventKey))
	return nodes, tasks
}

func TestTheRotationOpensARealmWithASite(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	addEventSiteTables(t, path)
	catalog := districtCatalog(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	opened, err := RotateSecretRealms(conn, catalog, 1000)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	conn.Close()
	if opened == nil {
		t.Fatal("the rotation opened nothing")
	}
	nodes, tasks := siteNodeCount(t, path, opened.EventKey)
	if nodes < 3 || tasks < 1 {
		t.Fatalf("%s opened with %d site nodes and %d tasks; a realm's threshold is not an empty room", opened.Name, nodes, tasks)
	}
	if got := i64(actionScalar(t, path, `SELECT COUNT(*) FROM world_event_npcs WHERE event_key=?`, opened.EventKey)); got < 1 {
		t.Fatalf("%s opened with nobody at its threshold", opened.Name)
	}
}

func TestASpatialKeyOpensARealmWithASite(t *testing.T) {
	// The spawn counts are fixed by severity, but the action path can reach a
	// roll elsewhere, so the dice are lent rather than argued about.
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path := setupShopDB(t)
	addEventSiteTables(t, path)
	world := batch4WorldPath(t)
	catalog := districtCatalog(t)
	realm := catalog.SecretRealms[catalog.Items["verdant_grotto_key"].SpatialKey["secret_realm_id"].(string)]
	batch4Exec(t, path, `INSERT INTO inventory(user_id,item_id,quantity) VALUES(42,'verdant_grotto_key',1)`)
	batch4Exec(t, path, `UPDATE characters SET location=? WHERE user_id=42`, realm.Location)
	out := batch4Result(t, batch4Apply(t, path, world, "spatial_key.use", 2, map[string]any{"item_id": "verdant_grotto_key"}))
	nodes, tasks := siteNodeCount(t, path, out["event_key"].(string))
	if nodes < 3 || tasks < 1 {
		t.Fatalf("a key opened %s with %d site nodes and %d tasks", realm.Name, nodes, tasks)
	}
}

func TestAGMSpawnedRealmHasASite(t *testing.T) {
	// The spawn counts are fixed by severity, but the action path can reach a
	// roll elsewhere, so the dice are lent rather than argued about.
	defer gamerng.UseRoller(func(int) int { return 0 })()
	path := setupAdminPlayerDB(t)
	addEventSiteTables(t, path)
	raw, _ := json.Marshal(map[string]any{
		"realm_id": "stygian_lantern_tomb", "title": "Stygian Lantern Tomb",
		"location": "Greenriver Town", "open_hours": 4,
	})
	resp, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{Operation: "admin.world.spawn_realm", ActorID: 7, Payload: raw})
	if err != nil {
		t.Fatal(err)
	}
	key := resp.Result.(map[string]any)["event_key"].(string)
	if nodes, tasks := siteNodeCount(t, path, key); nodes < 3 || tasks < 1 {
		t.Fatalf("the GM's realm opened with %d site nodes and %d tasks", nodes, tasks)
	}
}

// A realm opened before every door spawned a site gets one on the next tick,
// once, and a closed one is left alone.
func TestAnOpenRealmWithNoSiteIsGivenOne(t *testing.T) {
	path := setupBatch5AuthorityDB(t)
	addEventSiteTables(t, path)
	catalog := districtCatalog(t)
	now := float64(time.Now().UnixNano()) / 1e9
	insert := `INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at) VALUES(?,?,?,?,?,?,1,?,?)`
	batch4Exec(t, path, insert, "open-realm", "secret_realm:stygian_lantern_tomb", "secret_realm", "Stygian Lantern Tomb", "Greenriver Town",
		`{"definition_id":"rotation","category":"Rotation","realm_id":"stygian_lantern_tomb"}`, now-60, now+3600)
	batch4Exec(t, path, insert, "closed-realm", "", "secret_realm", "Stygian Lantern Tomb", "Greenriver Town",
		`{"realm_id":"stygian_lantern_tomb"}`, now-7200, now-60)
	fill := func() int64 {
		conn, err := storage.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		n, err := FillSecretRealmSitesTx(conn, catalog, now)
		if err != nil {
			t.Fatal(err)
		}
		if err := conn.Commit(); err != nil {
			t.Fatal(err)
		}
		return n
	}
	if got := fill(); got != 1 {
		t.Fatalf("filled %d realms, want the one open realm", got)
	}
	if nodes, tasks := siteNodeCount(t, path, "open-realm"); nodes < 3 || tasks < 1 {
		t.Fatalf("the open realm was given %d nodes and %d tasks", nodes, tasks)
	}
	if nodes, _ := siteNodeCount(t, path, "closed-realm"); nodes != 0 {
		t.Fatalf("a closed realm was given %d nodes", nodes)
	}
	if got := fill(); got != 0 {
		t.Fatalf("a second tick filled %d realms; the site is spawned once", got)
	}
}

// The class: every production function that writes a world_events row must
// spawn a site for each row it writes. A door written later that forgets is
// the empty room again, and the backfill only catches secret realms.
func TestEveryWorldEventDoorSpawnsASite(t *testing.T) {
	root := filepath.Join("..")
	found := 0
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return err
		}
		fset := token.NewFileSet()
		file, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			return err
		}
		for _, decl := range file.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok || fn.Body == nil {
				continue
			}
			inserts, spawns := 0, 0
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				switch x := n.(type) {
				case *ast.BasicLit:
					if x.Kind == token.STRING && strings.Contains(x.Value, "INSERT INTO world_events(") {
						inserts++
					}
				case *ast.CallExpr:
					name := ""
					switch f := x.Fun.(type) {
					case *ast.Ident:
						name = f.Name
					case *ast.SelectorExpr:
						name = f.Sel.Name
					}
					if name == "SpawnWorldEventNodes" || name == "SpawnSecretRealmSite" {
						spawns++
					}
				}
				return true
			})
			found += inserts
			if inserts > spawns {
				t.Errorf("%s: %s writes %d world_events row(s) and spawns %d site(s)", fset.Position(fn.Pos()), fn.Name.Name, inserts, spawns)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if found < 5 {
		t.Fatalf("the walk found %d world_events writers; the sweep is broken, not the tree", found)
	}
}

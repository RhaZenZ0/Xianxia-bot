package game

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

// A JSON number decoded into `any` is a float64, and a Discord snowflake is
// above 2^53: the canonicalizer that writes the engine's game_minute over a
// payload used to round every member id a player named (v1.38.0). The claim
// is byte-exactness, not a parse: the id comes out spelled as it went in.
func TestCanonicalisingAPayloadKeepsASnowflakeExact(t *testing.T) {
	raw := json.RawMessage(`{"partner_user_id":987654321098765432,"stakes":"a cup of tea","quantity":2,"ratio":0.25,"flag":true,"nested":{"user_id":1234567890123456789}}`)
	out, err := canonicalPayloadWithGameMinute(raw, 777)
	if err != nil {
		t.Fatalf("canonicalise: %v", err)
	}
	text := string(out)
	for _, want := range []string{`"partner_user_id":987654321098765432`, `"user_id":1234567890123456789`, `"game_minute":777`, `"quantity":2`, `"ratio":0.25`, `"flag":true`, `"stakes":"a cup of tea"`} {
		if !strings.Contains(text, want) {
			t.Fatalf("the canonical payload lost %s: %s", want, text)
		}
	}
	var decoded struct {
		Partner    int64 `json:"partner_user_id"`
		GameMinute int64 `json:"game_minute"`
	}
	if err := json.Unmarshal(out, &decoded); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if decoded.Partner != 987654321098765432 || decoded.GameMinute != 777 {
		t.Fatalf("a handler reading the canonical payload sees partner %d at minute %d", decoded.Partner, decoded.GameMinute)
	}
}

// The caller's own game_minute is refused before this point
// (rejectCallerGameMinute); here an empty or null payload still gets the
// engine's minute, the shape every no-argument action sends.
func TestCanonicalisingAnEmptyPayloadStillWritesTheMinute(t *testing.T) {
	for _, raw := range []json.RawMessage{nil, json.RawMessage(``), json.RawMessage(`null`), json.RawMessage(`{}`)} {
		out, err := canonicalPayloadWithGameMinute(raw, 5)
		if err != nil {
			t.Fatalf("%q: %v", string(raw), err)
		}
		if string(out) != `{"game_minute":5}` {
			t.Fatalf("%q canonicalised to %s", string(raw), string(out))
		}
	}
}

// The class behind the canonicaliser's fault (v1.39.0): any decode of a
// request payload into a plain map goes through encoding/json's float64 and
// rounds a snowflake. The one door is decodeMap, which decodes with
// UseNumber; this walks every non-test file of the engine and refuses an
// `Unmarshal(..., &m)` or a `Decode(&m)` whose target is a map[string]any
// or a bare `any`, unless the function also calls UseNumber. A struct
// target is fine: a struct's int64 field decodes exactly.
//
// It walked three packages and only map targets until v1.40, when the gate
// ledger found the one `any` outside them: `eventledger.Replay` decoded a
// stored receipt into `var result any`, so the second delivery of an action
// naming a member id answered with the id rounded. Every package under
// internal/ is walked now, and a target declared `any` counts.
func TestNoPayloadIsDecodedIntoAPlainMap(t *testing.T) {
	// Decodes into a plain map that carry no id, each with the reason.
	allowed := map[string]string{
		"contentsync/contentsync.go:project": "a content entry from world.json, projected into typed columns; content carries no Discord id and nothing above 2^53",
	}
	packages, err := os.ReadDir("..")
	if err != nil {
		t.Fatal(err)
	}
	var offenders []string
	checked := 0
	for _, pkg := range packages {
		if !pkg.IsDir() {
			continue
		}
		dir := filepath.Join("..", pkg.Name())
		entries, err := os.ReadDir(dir)
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			name := entry.Name()
			if !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
				continue
			}
			file, err := parser.ParseFile(token.NewFileSet(), filepath.Join(dir, name), nil, 0)
			if err != nil {
				t.Fatal(err)
			}
			for _, decl := range file.Decls {
				fn, ok := decl.(*ast.FuncDecl)
				if !ok || fn.Body == nil {
					continue
				}
				if _, ok := allowed[pkg.Name()+"/"+name+":"+fn.Name.Name]; ok {
					continue
				}
				mapVars := plainMapVars(fn)
				usesNumber := false
				ast.Inspect(fn.Body, func(n ast.Node) bool {
					if call, ok := n.(*ast.CallExpr); ok {
						if sel, ok := call.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "UseNumber" {
							usesNumber = true
						}
					}
					return true
				})
				ast.Inspect(fn.Body, func(n ast.Node) bool {
					call, ok := n.(*ast.CallExpr)
					if !ok {
						return true
					}
					sel, ok := call.Fun.(*ast.SelectorExpr)
					if !ok || (sel.Sel.Name != "Unmarshal" && sel.Sel.Name != "Decode") {
						return true
					}
					checked++
					target := call.Args[len(call.Args)-1]
					unary, ok := target.(*ast.UnaryExpr)
					if !ok || unary.Op != token.AND {
						return true
					}
					ident, ok := unary.X.(*ast.Ident)
					if !ok || !mapVars[ident.Name] {
						return true
					}
					if sel.Sel.Name == "Decode" && usesNumber {
						return true
					}
					offenders = append(offenders, fmt.Sprintf("%s/%s: %s decodes %q into a plain map or any through float64", pkg.Name(), name, fn.Name.Name, ident.Name))
					return true
				})
			}
		}
	}
	if checked < 50 {
		t.Fatalf("the walk saw only %d decodes; the reader is broken, not the tree", checked)
	}
	if len(offenders) > 0 {
		t.Fatalf("a snowflake would be rounded here - use decodeMap, a RawMessage or a struct:\n%s", strings.Join(offenders, "\n"))
	}
	for key := range allowed {
		pkg, rest, _ := strings.Cut(key, "/")
		file, fnName, _ := strings.Cut(rest, ":")
		parsed, err := parser.ParseFile(token.NewFileSet(), filepath.Join("..", pkg, file), nil, 0)
		if err != nil {
			t.Fatalf("%s is named in `allowed` and cannot be read: %v", key, err)
		}
		found := false
		for _, decl := range parsed.Decls {
			if fn, ok := decl.(*ast.FuncDecl); ok && fn.Name.Name == fnName {
				found = true
			}
		}
		if !found {
			t.Fatalf("%s is named in `allowed` and no longer exists; remove the entry", key)
		}
	}
}

// plainMapVars names every variable a function declares as map[string]any
// or as a bare any, by `var x map[string]any`, `var x any` or
// `x := map[string]any{}`: the targets a decode reads numbers into as float64.
func plainMapVars(fn *ast.FuncDecl) map[string]bool {
	out := map[string]bool{}
	isAny := func(expr ast.Expr) bool {
		switch v := expr.(type) {
		case *ast.Ident:
			return v.Name == "any"
		case *ast.InterfaceType:
			return v.Methods == nil || len(v.Methods.List) == 0
		}
		return false
	}
	isPlainMap := func(expr ast.Expr) bool {
		m, ok := expr.(*ast.MapType)
		if !ok {
			return false
		}
		key, ok := m.Key.(*ast.Ident)
		if !ok || key.Name != "string" {
			return false
		}
		return isAny(m.Value)
	}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		switch node := n.(type) {
		case *ast.DeclStmt:
			if gen, ok := node.Decl.(*ast.GenDecl); ok {
				for _, spec := range gen.Specs {
					if vs, ok := spec.(*ast.ValueSpec); ok && vs.Type != nil && (isPlainMap(vs.Type) || isAny(vs.Type)) {
						for _, name := range vs.Names {
							out[name.Name] = true
						}
					}
				}
			}
		case *ast.AssignStmt:
			for i, rhs := range node.Rhs {
				if lit, ok := rhs.(*ast.CompositeLit); ok && isPlainMap(lit.Type) && i < len(node.Lhs) {
					if ident, ok := node.Lhs[i].(*ast.Ident); ok {
						out[ident.Name] = true
					}
				}
			}
		}
		return true
	})
	return out
}

// The wiring, end to end through the real dispatch: two cultivators with ids
// the size Discord mints, one proposing to the other through
// ApplyWithWorld, and the partnership row carrying both ids to the digit.
// Before v1.38.0 this answered "both cultivators must be living
// incarnations", because the partner the canonical payload named did not
// exist.
func TestASnowflakeNamedInAPayloadReachesTheHandlerIntact(t *testing.T) {
	const a, b = int64(1234567890123456789), int64(987654321098765432)
	path := setupBatch4AuthorityDB(t)
	createProductionTable(t, path, "dao_partnerships")
	for _, uid := range []int64{a, b} {
		batch4Exec(t, path, fmt.Sprintf(`INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,realm_index,phase,cultivation,body_realm_index,body_phase,body_cultivation,life_status,karma_score,qi,qi_max,vitality,vitality_max)
VALUES(%d,'Snowflake %d','neutral','Sword Cultivator','Fire Root','Greenriver Town','{}',0,9,100,0,9,100,'alive',50,100,100,100,100)`, uid, uid))
	}
	world := batch4WorldPath(t)
	raw, _ := json.Marshal(map[string]any{"partner_user_id": b})
	if _, err := ApplyWithWorld(path, world, ActionRequest{APIVersion: authoritativeAPIVersion, ActionID: "snowflake-propose", Operation: "dao.propose", ActorID: a, Payload: raw}); err != nil {
		t.Fatalf("a proposal between two real-sized ids was refused: %v", err)
	}
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT user_a,user_b,requested_by FROM dao_partnerships`, nil)
	if err != nil {
		t.Fatal(err)
	}
	row := firstRowMap(res)
	if row == nil {
		t.Fatal("no partnership row was written")
	}
	lo, hi := b, a
	if storage.ParseInt(row["user_a"]) != lo || storage.ParseInt(row["user_b"]) != hi || storage.ParseInt(row["requested_by"]) != a {
		t.Fatalf("the row names %v/%v by %v, not %d/%d by %d: an id was rounded on the way in", row["user_a"], row["user_b"], row["requested_by"], lo, hi, a)
	}
}

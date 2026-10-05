package game

import (
	"go/ast"
	"go/parser"
	"go/token"
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

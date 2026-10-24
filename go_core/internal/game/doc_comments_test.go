package game

// A doc comment names the declaration it sits on (v1.39.2).
//
// A comment pass over the whole tree found no dead code, no debris and no
// typos, and about thirty comments that had quietly stopped being true. The
// Go half of those had one shape: a doc comment whose first word is a name
// that is not the declaration under it. It happens three ways, and none of
// them is carelessness in the writing - the code moved underneath the prose:
//
//   - a function was renamed and its doc kept the old name
//     ("roadSiteOnRoadOfKind says..." over roadSiteOnTheRoad);
//   - a new declaration was inserted between a doc and its target, so godoc
//     attached the doc to the newcomer and the real target lost it
//     ("BirthFamilySendoff is what a household..." over CommissionGiver);
//   - several docs piled onto one function when the ones between were cut.
//
// gofmt, vet and staticcheck read none of this: the default staticcheck set
// leaves the ST1020 doc-form checks off, and this tree does not want them on,
// because a doc here may open with a sentence about the world before it names
// the code ("An era belongs to one world (v1.0.7)."). So this is the narrower
// reader: every paragraph of every doc comment in `go_core`, and the one
// question is whether its first word is *identifier-shaped* - camelCase, an
// underscore, a digit, or a name this package declares - and is not the
// declaration's own name. Plain English passes; a name that is not this
// declaration's does not, whether it is an old name of this function or the
// right name of the one below.
//
// A deliberate exception is named in `docAllowed` with its reason, and an
// entry naming a declaration that no longer exists fails, so the list can
// only shrink honestly.

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"unicode"
)

// docAllowed names "file.go:Declaration" whose doc may open a paragraph with
// another identifier, each with the reason. A reason is required and must say
// why the name is right where it is.
var docAllowed = map[string]string{}

type docSite struct {
	file string
	name string
	word string
	line int
}

// goTreeFiles parses every .go file under go_core, tests included, keeping
// comments. The package name of each file is returned beside it so the gate
// can ask what that package declares.
func goTreeFiles(t *testing.T) (map[string]*ast.File, *token.FileSet) {
	t.Helper()
	root := filepath.Join("..", "..")
	fset := token.NewFileSet()
	files := map[string]*ast.File{}
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if path != root && (d.Name() == "testdata" || strings.HasPrefix(d.Name(), ".")) {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") {
			return nil
		}
		file, err := parser.ParseFile(fset, path, nil, parser.ParseComments)
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(root, path)
		files[filepath.ToSlash(rel)] = file
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(files) < 300 {
		t.Fatalf("read %d Go files under go_core; the reader is broken, not the tree", len(files))
	}
	return files, fset
}

// docParagraphs splits a doc comment into its paragraphs - runs of lines
// separated by a blank comment line - and returns each one's first word with
// the comment's line.
func docParagraphs(fset *token.FileSet, doc *ast.CommentGroup) []struct {
	word string
	line int
} {
	var out []struct {
		word string
		line int
	}
	newParagraph := true
	for _, c := range doc.List {
		text := strings.TrimSpace(strings.TrimPrefix(c.Text, "//"))
		if strings.HasPrefix(c.Text, "/*") {
			text = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(c.Text, "/*"), "*/"))
		}
		if text == "" {
			newParagraph = true
			continue
		}
		if strings.HasPrefix(text, "go:") || strings.HasPrefix(text, "nolint") || strings.HasPrefix(text, "-") || strings.HasPrefix(text, "  ") {
			// A directive, a list item or an indented block never opens a doc paragraph.
			newParagraph = false
			continue
		}
		if newParagraph {
			word := strings.Fields(text)[0]
			out = append(out, struct {
				word string
				line int
			}{word, fset.Position(c.Pos()).Line})
		}
		newParagraph = false
	}
	return out
}

// identifierShaped says whether a word is a Go name rather than English: a
// lower-to-upper case change inside it, an underscore, a digit, or a name the
// package declares at the top level.
func identifierShaped(word string, declared map[string]bool) bool {
	word = cleanWord(word)
	if word == "" || strings.ContainsAny(word, "._/") || releaseLabel.MatchString(word) {
		// A table or content key (`career_progress`, `items.heart_calming_pill`),
		// a path and a release label all open sentences here and none is a Go
		// declaration; a snake_case name is a Go declaration only when the
		// package declares it, which the next line asks.
		return declared[word]
	}
	if declared[word] {
		return true
	}
	runes := []rune(word)
	for i, r := range runes {
		if i > 0 && unicode.IsUpper(r) && unicode.IsLower(runes[i-1]) {
			return true
		}
	}
	return false
}

var releaseLabel = regexp.MustCompile(`^(v\d|rc\.|\d)`)

func isTestFunc(name string) bool {
	for _, prefix := range []string{"Test", "Benchmark", "Example", "Fuzz"} {
		if strings.HasPrefix(name, prefix) {
			return true
		}
	}
	return false
}

func cleanWord(word string) string {
	word = strings.TrimRight(word, ".,:;!?)'\"")
	word = strings.TrimLeft(word, "(`'\"*")
	return strings.TrimRight(word, "`*")
}

// declaredNames is every top-level name a package declares across its files,
// keyed by the package's directory.
func declaredNames(files map[string]*ast.File) map[string]map[string]bool {
	out := map[string]map[string]bool{}
	for path, file := range files {
		dir := filepath.ToSlash(filepath.Dir(path))
		names := out[dir]
		if names == nil {
			names = map[string]bool{}
			out[dir] = names
		}
		for _, decl := range file.Decls {
			switch d := decl.(type) {
			case *ast.FuncDecl:
				names[d.Name.Name] = true
			case *ast.GenDecl:
				for _, spec := range d.Specs {
					switch s := spec.(type) {
					case *ast.TypeSpec:
						names[s.Name.Name] = true
					case *ast.ValueSpec:
						for _, n := range s.Names {
							names[n.Name] = true
						}
					}
				}
			}
		}
	}
	return out
}

func TestADocCommentNamesTheDeclarationItSitsOn(t *testing.T) {
	files, fset := goTreeFiles(t)
	declared := declaredNames(files)
	seen := map[string]bool{}
	documented := 0
	var wrong []docSite
	for path, file := range files {
		pkgNames := declared[filepath.ToSlash(filepath.Dir(path))]
		check := func(name string, doc *ast.CommentGroup) {
			if doc == nil {
				return
			}
			documented++
			seen[path+":"+name] = true
			paragraphs := docParagraphs(fset, doc)
			if len(paragraphs) == 0 {
				return
			}
			// Only the opening paragraph says what the doc is about; a later one
			// may rightly open with a parameter, a field or the helper it leans on.
			p := paragraphs[0]
			word := cleanWord(p.word)
			if word == name || !identifierShaped(p.word, pkgNames) {
				return
			}
			if _, ok := docAllowed[path+":"+name]; ok {
				return
			}
			wrong = append(wrong, docSite{path, name, word, p.line})
		}
		for _, decl := range file.Decls {
			switch d := decl.(type) {
			case *ast.FuncDecl:
				if isTestFunc(d.Name.Name) {
					// A test's doc rightly opens with the function it drives.
					continue
				}
				check(d.Name.Name, d.Doc)
			case *ast.GenDecl:
				if len(d.Specs) != 1 {
					continue
				}
				switch s := d.Specs[0].(type) {
				case *ast.TypeSpec:
					check(s.Name.Name, d.Doc)
				case *ast.ValueSpec:
					if len(s.Names) == 1 {
						check(s.Names[0].Name, d.Doc)
					}
				}
			}
		}
	}
	if documented < 500 {
		t.Fatalf("read %d documented declarations; the reader is broken, not the tree", documented)
	}
	for key, reason := range docAllowed {
		if !seen[key] {
			t.Errorf("docAllowed names %s, which no longer has a doc comment: drop the entry", key)
		}
		if len(strings.TrimSpace(reason)) < 20 {
			t.Errorf("docAllowed[%q] needs a reason of at least twenty characters, not %q", key, reason)
		}
	}
	for _, w := range wrong {
		t.Errorf("%s:%d: the doc comment on %s opens a paragraph with %q - a name that is not this declaration's. "+
			"Either the function was renamed and the doc kept the old name, or a declaration was inserted "+
			"between the doc and the one it describes; move or rename the doc, or name it in docAllowed with a reason.",
			w.file, w.line, w.name, w.word)
	}
}

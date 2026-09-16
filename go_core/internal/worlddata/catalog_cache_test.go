package worlddata

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// Load is memoised (v1.0.0-rc.28). Fifteen of its seventeen call sites are in
// authoritative.go, inside the request path, so before this every player
// action re-read and re-parsed two and a half megabytes of JSON on hardware
// this project exists to run on.

func writeCatalog(t *testing.T, path, extraRoot string) {
	t.Helper()
	body := `{"paths":{"Sword":{"body":1,"agility":1,"spirit":1,"insight":1,"will":1,"presence":1,"skill":"x"}},` +
		`"roots":["Mortal Root"` + extraRoot + `]}`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestTheSameFileIsParsedOnce(t *testing.T) {
	path := filepath.Join(t.TempDir(), "world.json")
	writeCatalog(t, path, "")
	first, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Roots) != 1 || first.Roots[0] != "Mortal Root" {
		t.Fatalf("setup: %v", first.Roots)
	}

	// Rewrite the contents while keeping the length and the timestamp exactly
	// as they were. Nothing about the file's stat has changed, so a Load that
	// goes back to the disk returns the new name and a Load that uses the
	// cache returns the old one - which is the only difference the two can
	// possibly have, and it works the same whoever is running the test.
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	swapped := strings.Replace(string(body), "Mortal Root", "Mortal Toor", 1)
	if len(swapped) != len(body) {
		t.Fatalf("the test's own swap changed the length: %d vs %d", len(swapped), len(body))
	}
	if err := os.WriteFile(path, []byte(swapped), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, info.ModTime(), info.ModTime()); err != nil {
		t.Skip("cannot pin mtime on this filesystem")
	}

	second, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if second.Roots[0] != "Mortal Root" {
		t.Fatalf("Load went back to the disk for a file whose stat had not changed: got %q", second.Roots[0])
	}
}

// The reason the key is (path, mtime, size) rather than the path alone: an
// operator editing content on a live NAS must not need a restart.
func TestEditingTheFileIsNoticed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "world.json")
	writeCatalog(t, path, "")
	first, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Roots) != 1 {
		t.Fatalf("setup: %d roots", len(first.Roots))
	}
	writeCatalog(t, path, `,"Spirit Root"`)
	// Some filesystems keep mtime at one-second resolution, which is exactly
	// why size is in the key as well - but move the timestamp anyway so the
	// test proves the mtime half too.
	future := time.Now().Add(2 * time.Second)
	_ = os.Chtimes(path, future, future)
	second, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(second.Roots) != 2 {
		t.Fatalf("an edited file served the old parse: %d roots", len(second.Roots))
	}
}

// Two edits inside one filesystem timestamp tick that change the length.
func TestALengthChangeAloneIsNoticed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "world.json")
	writeCatalog(t, path, "")
	if _, err := Load(path); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	writeCatalog(t, path, `,"Spirit Root","Ice Root"`)
	// Pin the timestamp back to what it was, leaving only the size different.
	if err := os.Chtimes(path, info.ModTime(), info.ModTime()); err != nil {
		t.Skip("cannot set mtime on this filesystem")
	}
	again, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(again.Roots) != 3 {
		t.Fatalf("an edit that kept the mtime served the old parse: %d roots", len(again.Roots))
	}
}

func TestAMissingFileIsStillAnError(t *testing.T) {
	if _, err := Load(filepath.Join(t.TempDir(), "nothing.json")); err == nil {
		t.Fatal("a missing catalogue loaded successfully")
	}
}

func TestAnInvalidCatalogueIsNeverCached(t *testing.T) {
	path := filepath.Join(t.TempDir(), "world.json")
	if err := os.WriteFile(path, []byte(`{"paths":{},"roots":[]}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil {
		t.Fatal("a catalogue with no paths or roots was accepted")
	}
	// And the failure did not poison the cache: fixing the file works without
	// a restart.
	writeCatalog(t, path, "")
	future := time.Now().Add(2 * time.Second)
	_ = os.Chtimes(path, future, future)
	if _, err := Load(path); err != nil {
		t.Fatalf("a corrected catalogue still failed: %v", err)
	}
}

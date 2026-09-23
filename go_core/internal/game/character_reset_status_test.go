package game

// What a GM can see of an account's restart allowance (v1.0.13).
//
// `character.reset` has reported `resets_used` and `resets_remaining` in its
// own reply since v1.0.1, and that reply was the only place either number ever
// appeared: a player learned how many chances were left by spending one, and a
// GM could not look it up at all. `character.reset_status` is the read, and the
// reason it is an engine query rather than a SELECT in each of the two GM
// surfaces is the allowance itself - `characterResetAllowance` is a Go constant
// and `characterResetEvent` a Go string, so a surface holding its own copy of
// either would be free to tell a GM "one left" on the day the engine refuses.
//
// The load-bearing test here is not "does it count" - it is
// `TestTheStatusAndTheResetCannotDisagree`, which measures the read against a
// real reset's own reply rather than against a number written down twice.

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func resetStatus(t *testing.T, path, world string, actor, subject int64) map[string]any {
	t.Helper()
	payload := map[string]any{}
	if subject != 0 {
		payload["user_id"] = subject
	}
	return authority2Query(t, path, world, "character.reset_status", actor, payload)
}

// TestTheStatusAndTheResetCannotDisagree is the whole point of the query.
//
// A card that said "1 of 3" from its own copy of the bound would read correctly
// today and wrongly the day the allowance moves, and nothing would go red - so
// the numbers are held against the reset's *own* reply rather than against a
// literal. `reset_allowance` must be what the action itself accounts for, and
// used/remaining must be what it just reported.
func TestTheStatusAndTheResetCannotDisagree(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Lin First", 200)

	// A GM asking before anything has been spent gets the whole allowance.
	before := resetStatus(t, path, world, 4242, 77)
	if got := storage.ParseInt(before["resets_used"]); got != 0 {
		t.Fatalf("an account that has never reset reports resets_used=%d", got)
	}
	if got := storage.ParseInt(before["resets_remaining"]); got != characterResetAllowance {
		t.Fatalf("resets_remaining=%d before any reset, want the whole allowance %d", got, characterResetAllowance)
	}

	reset, err := resetCultivator(t, path, world, 77, "reset-status-1")
	if err != nil {
		t.Fatalf("a fresh cultivator was refused: %v", err)
	}
	after := resetStatus(t, path, world, 4242, 77)

	// Measured against the action, never against a number written twice.
	spent := storage.ParseInt(reset["resets_used"])
	left := storage.ParseInt(reset["resets_remaining"])
	if got := storage.ParseInt(after["reset_allowance"]); got != spent+left {
		t.Fatalf("the status calls the allowance %d while the reset accounted for %d (%d used + %d left); "+
			"a surface reading this would tell a GM a bound the engine does not enforce",
			got, spent+left, spent, left)
	}
	if got := storage.ParseInt(after["resets_used"]); got != spent {
		t.Fatalf("the status says %d resets used, the reset that just happened said %d", got, spent)
	}
	if got := storage.ParseInt(after["resets_remaining"]); got != left {
		t.Fatalf("the status says %d remaining, the reset that just happened said %d", got, left)
	}
}

// TestTheStatusAnswersForAnAccountWithNoCharacter is the case the lever exists
// for. Somebody who reset and has not begun again has no `characters` row at
// all - that is what a reset is - so a read joined to the sheet would answer
// "nobody" about precisely the person a GM is looking up.
func TestTheStatusAnswersForAnAccountWithNoCharacter(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Lin First", 200)
	if _, err := resetCultivator(t, path, world, 77, "reset-status-orphan"); err != nil {
		t.Fatal(err)
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM characters WHERE user_id=77"); got != 0 {
		t.Fatalf("the fixture still has a character row; this test proves nothing")
	}
	status := resetStatus(t, path, world, 4242, 77)
	if got := storage.ParseInt(status["resets_used"]); got != 1 {
		t.Fatalf("an account with no character reports resets_used=%d, want 1 - the one state a GM "+
			"asks this question in is the one where there is nothing left to join to", got)
	}
}

// TestTheStatusNamesTheLivesThatWereAbandoned holds the half a count cannot
// give: the payload each reset already wrote, which is what makes the card
// worth reading rather than a number.
func TestTheStatusNamesTheLivesThatWereAbandoned(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Lin First", 200)
	if _, err := resetCultivator(t, path, world, 77, "reset-status-a"); err != nil {
		t.Fatal(err)
	}
	makeCultivator(t, path, world, 77, "Lin Second", 400)
	if _, err := resetCultivator(t, path, world, 77, "reset-status-b"); err != nil {
		t.Fatal(err)
	}

	status := resetStatus(t, path, world, 4242, 77)
	lives, ok := status["resets"].([]map[string]any)
	if !ok {
		t.Fatalf("resets is %T, not a list of the lives that were given up", status["resets"])
	}
	if len(lives) != 2 {
		t.Fatalf("two lives were abandoned and the status lists %d", len(lives))
	}
	// Newest first: a GM reads the most recent decision at the top.
	if got := fmt.Sprint(lives[0]["name"]); got != "Lin Second" {
		t.Fatalf("the newest abandoned life is %q, want Lin Second - the list is not newest first", got)
	}
	if got := fmt.Sprint(lives[1]["name"]); got != "Lin First" {
		t.Fatalf("the older abandoned life is %q, want Lin First", got)
	}
	if got := storage.ParseInt(lives[0]["reset_number"]); got != 2 {
		t.Fatalf("the newest life is reset_number=%d, want 2", got)
	}
	for _, key := range []string{"path", "spiritual_root", "realm_index", "created_at"} {
		if lives[0][key] == nil {
			t.Fatalf("the abandoned life carries no %q; the payload the reset wrote is not being read back", key)
		}
	}
}

// TestTheStatusRefusesWithoutASubject. Both callers ask about somebody else -
// Discord's actor is the GM who typed the command, and the dashboard asks as
// actor 0 - so an absent `user_id` has no sensible default. Answering about the
// actor would quietly report the GM's own allowance under the player's name.
func TestTheStatusRefusesWithoutASubject(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Lin First", 200)
	if _, err := resetCultivator(t, path, world, 77, "reset-status-nosubject"); err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	_, err = ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion, Operation: "character.reset_status",
		ActorID: 77, Payload: raw,
	})
	if err == nil {
		t.Fatalf("a status with no user_id was answered; a GM surface that forgot the subject would " +
			"have been shown somebody else's allowance")
	}
	if !strings.Contains(err.Error(), "user_id") {
		t.Fatalf("refusal=%q, which does not name the missing subject", err)
	}
}

// TestTheStatusIsAReadAndWritesNothing. It is on `authoritativeQueries`, so it
// must not be capable of leaving a mark - including an event_log row of its
// own, which would be counted as a reset by the very function it reports on.
func TestTheStatusIsAReadAndWritesNothing(t *testing.T) {
	path := setupCharacterResetDB(t)
	world := batch4WorldPath(t)
	makeCultivator(t, path, world, 77, "Lin First", 200)
	if _, err := resetCultivator(t, path, world, 77, "reset-status-read"); err != nil {
		t.Fatal(err)
	}
	before := resetScalarI(t, path, "SELECT COUNT(*) FROM event_log")
	for i := 0; i < 3; i++ {
		resetStatus(t, path, world, 4242, 77)
	}
	if got := resetScalarI(t, path, "SELECT COUNT(*) FROM event_log"); got != before {
		t.Fatalf("three reads moved event_log from %d to %d rows; a read that logs itself into the "+
			"table holding the allowance spends the allowance it reports", before, got)
	}
}

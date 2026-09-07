package game

// The v0.22.3 regression tests for the review's finding #5.
//
// Location, life status and safe-zone were checked when a challenge was
// created and never again. A challenge lives five minutes, which is enough
// time to walk into a city, so a duel could begin between two people in
// different places, one of them standing inside formations that are supposed
// to make duelling impossible.
//
// The half of this worth arguing about is not the check, it is the response to
// a check that fails *mid-match*. Refusing every action would leave the duel
// permanently active - a worse bug than the one being fixed, and one the
// player cannot escape. So a breached match is resolved: the participant whose
// own change broke it forfeits, and when nobody is at fault it is void. Either
// way it ends, and neither pays reputation.

import (
	"fmt"
	"os"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func setupPvpDB(t *testing.T) (string, string) {
	t.Helper()
	path := setupBatch4AuthorityDB(t)
	world := batch4WorldPath(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := conn.ExecScript(`
CREATE TABLE pvp_challenges(challenge_id INTEGER PRIMARY KEY AUTOINCREMENT,challenger_user_id INTEGER NOT NULL,target_user_id INTEGER NOT NULL,stakes TEXT NOT NULL DEFAULT 'honor',status TEXT NOT NULL DEFAULT 'pending',created_at REAL NOT NULL,expires_at REAL NOT NULL);
CREATE TABLE pvp_matches(match_id INTEGER PRIMARY KEY AUTOINCREMENT,challenge_id INTEGER NOT NULL UNIQUE,player1_user_id INTEGER NOT NULL,player2_user_id INTEGER NOT NULL,player1_hp INTEGER NOT NULL,player2_hp INTEGER NOT NULL,turn_user_id INTEGER NOT NULL,player1_guard INTEGER NOT NULL DEFAULT 0,player2_guard INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'active',winner_user_id INTEGER,version INTEGER NOT NULL DEFAULT 0,location TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,updated_at REAL NOT NULL);
`); err != nil {
		_ = conn.Close()
		t.Fatal(err)
	}
	_ = conn.Close()
	// 42 and 43 both stand in Greenriver Town, which is not a safe zone.
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id IN (42,43)`)
	return path, world
}

func pvpApply(t *testing.T, path, world, op string, actor int64, seq int, payload map[string]any) (map[string]any, error) {
	t.Helper()
	out, err := ApplyWithWorld(path, world, ActionRequest{
		APIVersion: authoritativeAPIVersion,
		ActionID:   fmt.Sprintf("pvp-%s-%d-%d", op, actor, seq),
		Operation:  op,
		ActorID:    actor,
		Payload:    payloadJSON(t, payload),
	})
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

func challengeAndAccept(t *testing.T, path, world string, seq int) int64 {
	t.Helper()
	challenge, err := pvpApply(t, path, world, "pvp.challenge", 42, seq, map[string]any{"target_user_id": 43})
	if err != nil {
		t.Fatal(err)
	}
	accepted, err := pvpApply(t, path, world, "pvp.respond", 43, seq,
		map[string]any{"challenge_id": i64(challenge["challenge_id"]), "accept": true})
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(accepted["status"]) != "accepted" {
		t.Fatalf("challenge was not accepted: %v", accepted)
	}
	return i64(accepted["match_id"])
}

func matchRow(t *testing.T, path string, matchID int64) map[string]any {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT * FROM pvp_matches WHERE match_id=?`, []any{matchID})
	if err != nil {
		t.Fatal(err)
	}
	return firstRowMap(res)
}

// ---------------------------------------------------------------- accept ----

func TestAcceptingFromSomewhereElseDoesNotStartADuel(t *testing.T) {
	path, world := setupPvpDB(t)
	challenge, err := pvpApply(t, path, world, "pvp.challenge", 42, 1, map[string]any{"target_user_id": 43})
	if err != nil {
		t.Fatal(err)
	}
	// The five-minute window is exactly long enough to leave.
	batch4Exec(t, path, `UPDATE characters SET location='Cloudspine Foothills' WHERE user_id=43`)

	result, err := pvpApply(t, path, world, "pvp.respond", 43, 1,
		map[string]any{"challenge_id": i64(challenge["challenge_id"]), "accept": true})
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(result["status"]) != "void" {
		t.Fatalf("a duel started between two places: %v", result)
	}
	if got := countRows(t, path, `SELECT COUNT(*) FROM pvp_matches`); got != 0 {
		t.Fatalf("matches=%d, want none", got)
	}
	// And the challenge is closed, not left pending for the next attempt.
	if got := countRows(t, path, `SELECT COUNT(*) FROM pvp_challenges WHERE status='pending'`); got != 0 {
		t.Fatal("the challenge was left pending after being voided")
	}
}

func TestAcceptingInsideASafeZoneDoesNotStartADuel(t *testing.T) {
	path, world := setupPvpDB(t)
	challenge, err := pvpApply(t, path, world, "pvp.challenge", 42, 1, map[string]any{"target_user_id": 43})
	if err != nil {
		t.Fatal(err)
	}
	// Both walk into the auction house together. Same location, so the old
	// check would have been satisfied - but its formations suppress PvP.
	batch4Exec(t, path, `UPDATE characters SET location='Golden Pavilion Auction House' WHERE user_id IN (42,43)`)

	result, err := pvpApply(t, path, world, "pvp.respond", 43, 1,
		map[string]any{"challenge_id": i64(challenge["challenge_id"]), "accept": true})
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(result["status"]) != "void" {
		t.Fatalf("a duel started inside a safe zone: %v", result)
	}
}

func TestAcceptingAfterADeathDoesNotStartADuel(t *testing.T) {
	path, world := setupPvpDB(t)
	challenge, err := pvpApply(t, path, world, "pvp.challenge", 42, 1, map[string]any{"target_user_id": 43})
	if err != nil {
		t.Fatal(err)
	}
	batch4Exec(t, path, `UPDATE characters SET life_status='deceased' WHERE user_id=43`)

	result, err := pvpApply(t, path, world, "pvp.respond", 43, 1,
		map[string]any{"challenge_id": i64(challenge["challenge_id"]), "accept": true})
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(result["status"]) != "void" {
		t.Fatalf("a dead cultivator accepted a duel: %v", result)
	}
}

func TestAnAcceptedDuelRecordsWhereItIsFought(t *testing.T) {
	path, world := setupPvpDB(t)
	matchID := challengeAndAccept(t, path, world, 1)
	if got := fmt.Sprint(matchRow(t, path, matchID)["location"]); got != "Greenriver Town" {
		t.Fatalf("match location=%q", got)
	}
}

// ------------------------------------------------------------------- act ----

func TestWalkingAwayFromADuelForfeitsIt(t *testing.T) {
	path, world := setupPvpDB(t)
	matchID := challengeAndAccept(t, path, world, 1)

	// 42 is losing and leaves. Walking off must not be cheaper than losing.
	batch4Exec(t, path, `UPDATE characters SET location='Cloudspine Foothills' WHERE user_id=42`)
	result, err := pvpApply(t, path, world, "pvp.act", 42, 1, map[string]any{"match_id": matchID, "style": "attack"})
	if err != nil {
		t.Fatal(err)
	}
	if finished, _ := result["finished"].(bool); !finished {
		t.Fatalf("the duel continued after a participant left: %v", result)
	}
	if i64(result["winner_user_id"]) != 43 || i64(result["forfeited_by"]) != 42 {
		t.Fatalf("wrong forfeit: %v", result)
	}
	if awarded, _ := result["reputation_awarded"].(bool); awarded {
		t.Fatal("a forfeit paid reputation; only a fought duel does")
	}
	row := matchRow(t, path, matchID)
	if fmt.Sprint(row["status"]) != "finished" || i64(row["winner_user_id"]) != 43 {
		t.Fatalf("match row=%v", row)
	}
}

func TestAnOpponentWhoLeavesLosesToTheOneWhoStayed(t *testing.T) {
	path, world := setupPvpDB(t)
	matchID := challengeAndAccept(t, path, world, 1)

	batch4Exec(t, path, `UPDATE characters SET location='Cloudspine Foothills' WHERE user_id=43`)
	result, err := pvpApply(t, path, world, "pvp.act", 42, 1, map[string]any{"match_id": matchID, "style": "attack"})
	if err != nil {
		t.Fatal(err)
	}
	if i64(result["winner_user_id"]) != 42 || i64(result["forfeited_by"]) != 43 {
		t.Fatalf("the wrong participant forfeited: %v", result)
	}
}

func TestADeathMidDuelEndsItInFavourOfTheLiving(t *testing.T) {
	path, world := setupPvpDB(t)
	matchID := challengeAndAccept(t, path, world, 1)

	batch4Exec(t, path, `UPDATE characters SET life_status='deceased' WHERE user_id=43`)
	result, err := pvpApply(t, path, world, "pvp.act", 42, 1, map[string]any{"match_id": matchID, "style": "attack"})
	if err != nil {
		t.Fatal(err)
	}
	if i64(result["winner_user_id"]) != 42 {
		t.Fatalf("a duel against a dead opponent did not resolve to the living one: %v", result)
	}
}

func TestBothLeavingVoidsTheDuelWithNoWinner(t *testing.T) {
	path, world := setupPvpDB(t)
	matchID := challengeAndAccept(t, path, world, 1)

	batch4Exec(t, path, `UPDATE characters SET location='Cloudspine Foothills' WHERE user_id IN (42,43)`)
	result, err := pvpApply(t, path, world, "pvp.act", 42, 1, map[string]any{"match_id": matchID, "style": "attack"})
	if err != nil {
		t.Fatal(err)
	}
	if voided, _ := result["voided"].(bool); !voided {
		t.Fatalf("nobody was at fault but the duel picked a winner: %v", result)
	}
	if row := matchRow(t, path, matchID); row["winner_user_id"] != nil {
		t.Fatalf("a void duel recorded a winner: %v", row["winner_user_id"])
	}
}

func TestABreachedDuelEndsRatherThanBlockingForever(t *testing.T) {
	// The reason a breach resolves instead of erroring: an unresolvable match
	// would sit `active` forever, and the one-duel-at-a-time rule would then
	// lock both players out of ever duelling again.
	path, world := setupPvpDB(t)
	matchID := challengeAndAccept(t, path, world, 1)
	batch4Exec(t, path, `UPDATE characters SET location='Cloudspine Foothills' WHERE user_id=43`)
	if _, err := pvpApply(t, path, world, "pvp.act", 42, 1, map[string]any{"match_id": matchID, "style": "attack"}); err != nil {
		t.Fatal(err)
	}
	if got := countRows(t, path, `SELECT COUNT(*) FROM pvp_matches WHERE status='active'`); got != 0 {
		t.Fatalf("active matches=%d after a breach; the players are locked out", got)
	}
	// Both are free to duel again once they are back together.
	batch4Exec(t, path, `UPDATE characters SET location='Greenriver Town' WHERE user_id=43`)
	challengeAndAccept(t, path, world, 2)
}

func TestAnUnbreachedDuelIsUnaffected(t *testing.T) {
	// The check must not break ordinary duelling: two people standing where
	// they started exchange blows exactly as before.
	path, world := setupPvpDB(t)
	matchID := challengeAndAccept(t, path, world, 1)
	// Enough hit points that one blow cannot end it, so "still active" means
	// the invariant check left it alone rather than the fight being over.
	batch4Exec(t, path, `UPDATE pvp_matches SET player1_hp=9999,player2_hp=9999 WHERE match_id=?`, matchID)

	result, err := pvpApply(t, path, world, "pvp.act", 42, 1, map[string]any{"match_id": matchID, "style": "attack"})
	if err != nil {
		t.Fatal(err)
	}
	if _, breached := result["breach"]; breached {
		t.Fatalf("a legitimate duel was treated as breached: %v", result)
	}
	if fmt.Sprint(matchRow(t, path, matchID)["status"]) != "active" {
		t.Fatal("a legitimate duel ended after one attack")
	}
}

func TestAMatchWithNoRecordedLocationSkipsTheLocationRule(t *testing.T) {
	// Matches created before schema 31 have no location. They must keep
	// working rather than failing every action, so the location rule is
	// skipped for them - the life and safe-zone rules still apply.
	path, world := setupPvpDB(t)
	matchID := challengeAndAccept(t, path, world, 1)
	batch4Exec(t, path, `UPDATE pvp_matches SET location='' WHERE match_id=?`, matchID)
	batch4Exec(t, path, `UPDATE characters SET location='Cloudspine Foothills' WHERE user_id IN (42,43)`)

	result, err := pvpApply(t, path, world, "pvp.act", 42, 1, map[string]any{"match_id": matchID, "style": "attack"})
	if err != nil {
		t.Fatal(err)
	}
	if _, breached := result["breach"]; breached {
		t.Fatalf("a pre-schema-31 match was breached by the location rule: %v", result)
	}
}

// v0.23.1, review finding #6: the breach check must not sit behind the turn
// check.
//
// v0.22.4 added the check on every act precisely so a match whose
// preconditions had stopped holding would end rather than be refused - and
// then placed it after `turn_user_id`, which is the one case it exists for.
// When the player holding the turn dies, they cannot act; their opponent is
// told "it is not your turn" and never reaches the check; and the match stays
// active, which blocks the survivor from ever challenging anyone again.
func TestTheSurvivorCanEndADuelWhenTheDeadPlayerHoldsTheTurn(t *testing.T) {
	path, world := setupPvpDB(t)
	matchID := challengeAndAccept(t, path, world, 1)

	// 43 dies while it is their turn - the exact ordering the old code could
	// not recover from.
	batch4Exec(t, path, `UPDATE pvp_matches SET turn_user_id=43 WHERE match_id=?`, matchID)
	batch4Exec(t, path, `UPDATE characters SET life_status='deceased' WHERE user_id=43`)

	result, err := pvpApply(t, path, world, "pvp.act", 42, 1,
		map[string]any{"match_id": matchID, "style": "attack"})
	if err != nil {
		t.Fatalf("the survivor could not end the duel: %v", err)
	}
	if _, breached := result["breach"]; !breached {
		t.Fatalf("the duel did not resolve as breached: %v", result)
	}
	if got := countRows(t, path, `SELECT COUNT(*) FROM pvp_matches WHERE status='active'`); got != 0 {
		t.Fatalf("active matches=%d; the survivor is still locked out", got)
	}
	// And the lockout is really lifted: a fresh duel can start.
	batch4Exec(t, path, `UPDATE characters SET life_status='alive' WHERE user_id=43`)
	challengeAndAccept(t, path, world, 2)
}

// The turn rule still holds in a duel that is actually valid - moving the
// breach check earlier must not turn PvP into a free-for-all.
func TestTheTurnRuleStillHoldsInAValidDuel(t *testing.T) {
	path, world := setupPvpDB(t)
	matchID := challengeAndAccept(t, path, world, 1)
	batch4Exec(t, path, `UPDATE pvp_matches SET player1_hp=9999,player2_hp=9999,turn_user_id=42 WHERE match_id=?`, matchID)

	if _, err := pvpApply(t, path, world, "pvp.act", 43, 1,
		map[string]any{"match_id": matchID, "style": "attack"}); err == nil {
		t.Fatal("a player acted out of turn")
	}
	if fmt.Sprint(matchRow(t, path, matchID)["status"]) != "active" {
		t.Fatal("an out-of-turn attempt ended the duel")
	}
}

// And a stranger cannot act on someone else's duel just because the breach
// check now runs first: the participant check is what refuses them.
func TestAnOutsiderCannotActOnSomeoneElsesDuel(t *testing.T) {
	path, world := setupPvpDB(t)
	matchID := challengeAndAccept(t, path, world, 1)
	batch4Exec(t, path, `INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,attributes_json,life_status)
		VALUES(44,'Bystander','neutral','Sword Cultivator','Fire Root','Greenriver Town','{"body":10,"agility":10,"spirit":10,"insight":10,"will":10,"presence":10}','alive')`)

	if _, err := pvpApply(t, path, world, "pvp.act", 44, 1,
		map[string]any{"match_id": matchID, "style": "attack"}); err == nil {
		t.Fatal("a bystander acted on a duel they were not in")
	}
	if fmt.Sprint(matchRow(t, path, matchID)["status"]) != "active" {
		t.Fatal("a bystander's attempt ended someone else's duel")
	}
}

// The shape, pinned in source order rather than behaviour.
//
// This is the third time in two releases that a guard existed, was correct,
// and sat on a path the party it protected could not reach: the late-ack fix
// accepted an ack inside a branch that returns, the toxicity settle only ran
// if a player opened a screen, and this check ran only for whoever held the
// turn. A behavioural test catches the instance; this catches the edit that
// would reintroduce it, because "validate, then check whose turn it is" is an
// ordering a refactor can quietly reverse without failing anything else.
func TestTheBreachCheckStaysAheadOfTheTurnCheck(t *testing.T) {
	source, err := os.ReadFile("pvp_actions.go")
	if err != nil {
		t.Fatal(err)
	}
	body := string(source)
	start := strings.Index(body, "func pvpActAction(")
	if start < 0 {
		t.Fatal("pvpActAction not found")
	}
	body = body[start:]

	breach := strings.Index(body, "checkPvpParticipants(")
	turn := strings.Index(body, `errors.New("it is not your turn")`)
	if breach < 0 || turn < 0 {
		t.Fatalf("pvpActAction no longer has both checks (breach=%d turn=%d)", breach, turn)
	}
	if breach > turn {
		t.Fatal("the turn check runs before the participant check again: a player " +
			"who cannot act is exactly the one whose match needs ending, so the " +
			"check must not depend on it being their turn")
	}
}

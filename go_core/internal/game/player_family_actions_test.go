package game

// The player-founded house. Four tables carried it since the schema was
// written and nothing ever wrote a row into any of them, so the dashboard
// panel built on them could only ever be empty. These hold the doors open.

import (
	"encoding/json"
	"fmt"
	"testing"

	"xianxia/core/internal/storage"
)

const playerFamilyDDL = `
CREATE TABLE player_families(
    family_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    founder_user_id INTEGER NOT NULL, created_at REAL NOT NULL);
CREATE TABLE player_family_members(
    family_id INTEGER NOT NULL, user_id INTEGER NOT NULL UNIQUE,
    seniority_order INTEGER NOT NULL, joined_at REAL NOT NULL,
    PRIMARY KEY(family_id,user_id));
CREATE UNIQUE INDEX idx_player_family_order ON player_family_members(family_id,seniority_order);
CREATE TABLE player_family_invites(
    family_id INTEGER NOT NULL, inviter_user_id INTEGER NOT NULL, invitee_user_id INTEGER NOT NULL UNIQUE,
    requested_order INTEGER NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
    PRIMARY KEY(family_id,invitee_user_id));
CREATE TABLE family_children(
    child_id INTEGER PRIMARY KEY AUTOINCREMENT, family_id INTEGER NOT NULL, parent_user_id INTEGER NOT NULL,
    name TEXT NOT NULL, gender TEXT NOT NULL DEFAULT 'neutral', birth_game_minute INTEGER NOT NULL,
    spiritual_root TEXT NOT NULL, cultivation_potential INTEGER NOT NULL DEFAULT 0,
    can_cultivate INTEGER NOT NULL DEFAULT 0, awakening_state INTEGER NOT NULL DEFAULT 0,
    natural_lifespan_years INTEGER NOT NULL DEFAULT 75, status TEXT NOT NULL DEFAULT 'alive',
    created_at REAL NOT NULL);
`

func setupFamilyDB(t *testing.T, people ...int64) string {
	t.Helper()
	path := setupBatch5AuthorityDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if err := conn.ExecScript(playerFamilyDDL); err != nil {
		t.Fatal(err)
	}
	for _, id := range people {
		if _, err := conn.Execute(`INSERT INTO characters(user_id,name,gender,path,spiritual_root,location,
            attributes_json,realm_index,phase,cultivation,body_realm_index,body_phase,body_cultivation,
            life_status,karma_score,qi,qi_max,vitality,vitality_max)
            VALUES(?,?,'male','Qi Refiner','Single','Greenriver Town','{"spirit":3}',1,1,0,0,1,0,'alive',0,10,10,10,10)`,
			[]any{id, fmt.Sprintf("Cultivator %d", id)}); err != nil {
			t.Fatal(err)
		}
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	return path
}

// Every call gets its own action id. Reusing one is not a harmless shortcut:
// these are authoritative actions, so a repeated id replays the first answer
// instead of running again - which is the idempotency layer working, and makes
// a test of "the second attempt is refused" quietly pass on the first success.
var familyActionSeq int

func familyApply(t *testing.T, path, op string, actor int64, payload map[string]any) (map[string]any, error) {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	familyActionSeq++
	out, err := ApplyWithWorld(path, batch4WorldPath(t), ActionRequest{
		APIVersion: "v1", ActionID: fmt.Sprintf("test:%s:%d:%d", op, actor, familyActionSeq),
		Operation: op, ActorID: actor, Payload: raw,
	})
	if err != nil {
		return nil, err
	}
	result, _ := out.Result.(map[string]any)
	return result, nil
}

func familyScalar(t *testing.T, path, sql string, args ...any) int64 {
	t.Helper()
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	res, err := conn.Execute(sql, args)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) == 0 {
		return 0
	}
	return storage.ParseInt(res.Rows[0][0])
}

func TestFoundingAHouseSeatsTheFounderFirst(t *testing.T) {
	path := setupFamilyDB(t, 1)
	result, err := familyApply(t, path, "player_family.found", 1, map[string]any{"name": "House of the Still Lake"})
	if err != nil {
		t.Fatal(err)
	}
	if result["name"] != "House of the Still Lake" {
		t.Errorf("name = %v", result["name"])
	}
	if familyScalar(t, path, `SELECT COUNT(*) FROM player_families`) != 1 {
		t.Error("no house was written")
	}
	if familyScalar(t, path, `SELECT seniority_order FROM player_family_members WHERE user_id=1`) != 1 {
		t.Error("the founder does not hold the first seat")
	}
}

// name is UNIQUE and user_id is UNIQUE on members: both must refuse clearly
// rather than surfacing a constraint error at the player.
func TestAHouseNameAndAMembershipAreEachHeldOnce(t *testing.T) {
	path := setupFamilyDB(t, 1, 2)
	if _, err := familyApply(t, path, "player_family.found", 1, map[string]any{"name": "Cloud Gate"}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.found", 2, map[string]any{"name": "cloud gate"}); err == nil {
		t.Error("a second house took a name already carried, in another case")
	}
	if _, err := familyApply(t, path, "player_family.found", 1, map[string]any{"name": "Second House"}); err == nil {
		t.Error("a member founded a second house while still in the first")
	}
	for _, bad := range []string{"", "x", "   "} {
		if _, err := familyApply(t, path, "player_family.found", 2, map[string]any{"name": bad}); err == nil {
			t.Errorf("a house was founded with the name %q", bad)
		}
	}
}

func TestAnInviteIsOfferedAndAccepted(t *testing.T) {
	path := setupFamilyDB(t, 1, 2)
	if _, err := familyApply(t, path, "player_family.found", 1, map[string]any{"name": "Iron Bell"}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.invite", 1, map[string]any{"invitee_user_id": 2}); err != nil {
		t.Fatal(err)
	}
	if familyScalar(t, path, `SELECT COUNT(*) FROM player_family_invites WHERE invitee_user_id=2`) != 1 {
		t.Fatal("the invitation was not recorded")
	}
	result, err := familyApply(t, path, "player_family.respond", 2, map[string]any{"accept": true})
	if err != nil {
		t.Fatal(err)
	}
	if result["accepted"] != true {
		t.Errorf("accepted = %v", result["accepted"])
	}
	if familyScalar(t, path, `SELECT COUNT(*) FROM player_family_members WHERE user_id=2`) != 1 {
		t.Error("the invitee did not join")
	}
	// However it ends, the offer is spent.
	if familyScalar(t, path, `SELECT COUNT(*) FROM player_family_invites`) != 0 {
		t.Error("the invitation outlived its answer")
	}
}

func TestDecliningLeavesThePlayerFree(t *testing.T) {
	path := setupFamilyDB(t, 1, 2)
	if _, err := familyApply(t, path, "player_family.found", 1, map[string]any{"name": "Quiet Fern"}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.invite", 1, map[string]any{"invitee_user_id": 2}); err != nil {
		t.Fatal(err)
	}
	result, err := familyApply(t, path, "player_family.respond", 2, map[string]any{"accept": false})
	if err != nil {
		t.Fatal(err)
	}
	if result["accepted"] != false {
		t.Error("a decline was recorded as an acceptance")
	}
	if familyScalar(t, path, `SELECT COUNT(*) FROM player_family_members WHERE user_id=2`) != 0 {
		t.Error("declining still joined them")
	}
	if familyScalar(t, path, `SELECT COUNT(*) FROM player_family_invites`) != 0 {
		t.Error("a declined invitation is still standing")
	}
}

// (family_id, seniority_order) is UNIQUE. Two people must not be offered the
// same seat, and the refusal has to come at the invitation - not after the
// invitee has already accepted.
func TestTwoPeopleCannotHoldTheSameSeat(t *testing.T) {
	path := setupFamilyDB(t, 1, 2, 3)
	if _, err := familyApply(t, path, "player_family.found", 1, map[string]any{"name": "Twin Pine"}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.invite", 1,
		map[string]any{"invitee_user_id": 2, "seniority_order": 2}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.respond", 2, map[string]any{"accept": true}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.invite", 1,
		map[string]any{"invitee_user_id": 3, "seniority_order": 2}); err == nil {
		t.Error("a seat already held was offered again")
	}
	// The founder's own seat is likewise not on offer.
	if _, err := familyApply(t, path, "player_family.invite", 1,
		map[string]any{"invitee_user_id": 3, "seniority_order": 1}); err == nil {
		t.Error("the founder's seat was offered away")
	}
}

func TestNobodyIsInvitedIntoTwoHousesAtOnce(t *testing.T) {
	path := setupFamilyDB(t, 1, 2)
	if _, err := familyApply(t, path, "player_family.found", 1, map[string]any{"name": "First House"}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.invite", 1, map[string]any{"invitee_user_id": 2}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.respond", 2, map[string]any{"accept": true}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.invite", 1, map[string]any{"invitee_user_id": 2}); err == nil {
		t.Error("a member of the house was invited into it again")
	}
}

// A house with nobody in it is not a house.
func TestTheLastMemberOutDissolvesTheHouse(t *testing.T) {
	path := setupFamilyDB(t, 1)
	if _, err := familyApply(t, path, "player_family.found", 1, map[string]any{"name": "Sole Candle"}); err != nil {
		t.Fatal(err)
	}
	result, err := familyApply(t, path, "player_family.leave", 1, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result["dissolved"] != true {
		t.Errorf("dissolved = %v", result["dissolved"])
	}
	if familyScalar(t, path, `SELECT COUNT(*) FROM player_families`) != 0 {
		t.Error("an empty house is still standing")
	}
}

// The founder leaving must not leave founder_user_id pointing at somebody gone.
func TestAFounderLeavingHandsTheHouseOn(t *testing.T) {
	path := setupFamilyDB(t, 1, 2)
	if _, err := familyApply(t, path, "player_family.found", 1, map[string]any{"name": "Long Bridge"}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.invite", 1, map[string]any{"invitee_user_id": 2}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.respond", 2, map[string]any{"accept": true}); err != nil {
		t.Fatal(err)
	}
	result, err := familyApply(t, path, "player_family.leave", 1, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result["dissolved"] != false {
		t.Error("a house with a member left in it was dissolved")
	}
	if got := familyScalar(t, path, `SELECT founder_user_id FROM player_families`); got != 2 {
		t.Errorf("founder_user_id = %d, want the remaining member", got)
	}
}

func TestAChildIsBornIntoTheHouse(t *testing.T) {
	path := setupFamilyDB(t, 1)
	if _, err := familyApply(t, path, "player_family.found", 1, map[string]any{"name": "Morning Reed"}); err != nil {
		t.Fatal(err)
	}
	result, err := familyApply(t, path, "player_family.child", 1,
		map[string]any{"child_name": "Reed Xiaolan", "gender": "female"})
	if err != nil {
		t.Fatal(err)
	}
	if result["name"] != "Reed Xiaolan" || result["gender"] != "female" {
		t.Errorf("child = %v", result)
	}
	if result["spiritual_root"] == "" || result["spiritual_root"] == nil {
		t.Error("the child was born with no spiritual root")
	}
	if familyScalar(t, path, `SELECT COUNT(*) FROM family_children WHERE family_id=1`) != 1 {
		t.Error("the child was not recorded")
	}
	// The same name twice in one house is a confusion, not a family.
	if _, err := familyApply(t, path, "player_family.child", 1,
		map[string]any{"child_name": "reed xiaolan"}); err == nil {
		t.Error("two children of one house share a name")
	}
	// And somebody with no house has nowhere to bear one.
	if _, err := familyApply(t, path, "player_family.child", 99,
		map[string]any{"child_name": "Nobody"}); err == nil {
		t.Error("a child was born to a cultivator with no house")
	}
}

func TestTheStatusReadShowsTheHouseAndWhatIsWaiting(t *testing.T) {
	path := setupFamilyDB(t, 1, 2)
	if _, err := familyApply(t, path, "player_family.found", 1, map[string]any{"name": "Tall Gate"}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.child", 1, map[string]any{"child_name": "Gate Anlu"}); err != nil {
		t.Fatal(err)
	}
	if _, err := familyApply(t, path, "player_family.invite", 1, map[string]any{"invitee_user_id": 2}); err != nil {
		t.Fatal(err)
	}

	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	mine, err := playerFamilyStatus(conn, 1)
	if err != nil {
		t.Fatal(err)
	}
	got, _ := mine.(map[string]any)
	if got["in_family"] != true || got["name"] != "Tall Gate" {
		t.Errorf("the founder's status = %v", got)
	}
	if members, _ := got["members"].([]map[string]any); len(members) != 1 || members[0]["founder"] != true {
		t.Errorf("members = %v", got["members"])
	}
	if children, _ := got["children"].([]map[string]any); len(children) != 1 {
		t.Errorf("children = %v", got["children"])
	}

	theirs, err := playerFamilyStatus(conn, 2)
	if err != nil {
		t.Fatal(err)
	}
	other, _ := theirs.(map[string]any)
	if other["in_family"] != false {
		t.Error("the invitee is already counted as a member")
	}
	invite, _ := other["invite"].(map[string]any)
	if invite == nil || invite["family"] != "Tall Gate" {
		t.Errorf("the waiting invitation is not shown: %v", other)
	}
}

func TestAHouseSeatsOnlySoMany(t *testing.T) {
	people := []int64{}
	for i := int64(1); i <= familyMaxMembers+2; i++ {
		people = append(people, i)
	}
	path := setupFamilyDB(t, people...)
	if _, err := familyApply(t, path, "player_family.found", 1, map[string]any{"name": "Wide Hall"}); err != nil {
		t.Fatal(err)
	}
	for i := int64(2); i <= familyMaxMembers; i++ {
		if _, err := familyApply(t, path, "player_family.invite", 1, map[string]any{"invitee_user_id": i}); err != nil {
			t.Fatalf("inviting %d: %v", i, err)
		}
		if _, err := familyApply(t, path, "player_family.respond", i, map[string]any{"accept": true}); err != nil {
			t.Fatalf("%d accepting: %v", i, err)
		}
	}
	if _, err := familyApply(t, path, "player_family.invite", 1,
		map[string]any{"invitee_user_id": int64(familyMaxMembers + 1)}); err == nil {
		t.Errorf("a %d-seat house took one more", familyMaxMembers)
	}
}

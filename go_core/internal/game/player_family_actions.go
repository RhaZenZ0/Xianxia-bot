package game

// Families the players found themselves.
//
// Four tables carried this since the schema was written - `player_families`,
// `player_family_members`, `player_family_invites`, `family_children`, with
// foreign keys, a unique seniority index and a cascade - and nothing ever
// wrote a single row into any of them. The GM dashboard had a whole panel
// joining founders to members to children, which could only ever be empty, and
// character deletion carefully cleaned up rows that could not exist. The
// design was finished; only the doors were missing.
//
// These are the doors. Note what the schema already decides, which is why
// none of it is invented here: `name` is UNIQUE, so two houses cannot share a
// name; `user_id` is UNIQUE on members, so nobody belongs to two houses;
// `invitee_user_id` is UNIQUE on invites, so nobody holds two offers at once;
// and (family_id, seniority_order) is UNIQUE, so two people cannot hold the
// same seat. Every one of those is enforced below with a clear refusal rather
// than left to surface as a constraint error.

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// countRow is a single-number query - a COUNT, a MAX+1 - which these do often
// enough that spelling it out each time buries the rule being expressed.
func countRow(conn *storage.Conn, sql string, args []any) (int64, error) {
	res, err := conn.Execute(sql, args)
	if err != nil {
		return 0, err
	}
	row := firstRowMap(res)
	if row == nil {
		return 0, nil
	}
	return i64(row["n"]), nil
}

// floatOf reads a REAL column, which SQLite may hand back as any numeric type.
func floatOf(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case float32:
		return float64(n)
	case int64:
		return float64(n)
	case int:
		return float64(n)
	}
	var f float64
	_, _ = fmt.Sscanf(fmt.Sprint(v), "%g", &f)
	return f
}

const (
	familyNameMinRunes = 2
	familyNameMaxRunes = 48
	// An invitation is an offer, not a standing claim on somebody.
	familyInviteTTLSeconds = 7 * 24 * 60 * 60
	familyMaxMembers       = 12
	familyChildNameMax     = 48
)

type playerFamilyPayload struct {
	Name          string `json:"name"`
	InviteeUserID int64  `json:"invitee_user_id"`
	Seniority     int64  `json:"seniority_order"`
	Accept        bool   `json:"accept"`
	ChildName     string `json:"child_name"`
	Gender        string `json:"gender"`
}

// livingCharacter is the one check every one of these shares.
func livingCharacterName(conn *storage.Conn, userID int64) (string, error) {
	res, err := conn.Execute(
		`SELECT name FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
	if err != nil {
		return "", err
	}
	row := firstRowMap(res)
	if row == nil {
		return "", errors.New("living character not found")
	}
	return fmt.Sprint(row["name"]), nil
}

// familyOf is the house a player belongs to, or 0.
func familyOf(conn *storage.Conn, userID int64) (int64, string, error) {
	res, err := conn.Execute(`SELECT f.family_id,f.name FROM player_family_members m
        JOIN player_families f ON f.family_id=m.family_id WHERE m.user_id=?`, []any{userID})
	if err != nil {
		return 0, "", err
	}
	row := firstRowMap(res)
	if row == nil {
		return 0, "", nil
	}
	return i64(row["family_id"]), fmt.Sprint(row["name"]), nil
}

func playerFamilyFoundAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p playerFamilyPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	founder, err := livingCharacterName(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	name := strings.Join(strings.Fields(p.Name), " ")
	if n := len([]rune(name)); n < familyNameMinRunes || n > familyNameMaxRunes {
		return authoritativeMutation{}, fmt.Errorf("a house name must be %d to %d characters", familyNameMinRunes, familyNameMaxRunes)
	}
	existing, existingName, err := familyOf(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if existing != 0 {
		return authoritativeMutation{}, fmt.Errorf("you already belong to %s; leave it before founding another", existingName)
	}
	taken, err := boolRow(conn, `SELECT 1 FROM player_families WHERE name=? COLLATE NOCASE LIMIT 1`, []any{name})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if taken {
		return authoritativeMutation{}, fmt.Errorf("a house already carries the name %q", name)
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(
		`INSERT INTO player_families(name,founder_user_id,created_at) VALUES(?,?,?)`,
		[]any{name, userID, now}); err != nil {
		return authoritativeMutation{}, err
	}
	res, err := conn.Execute(`SELECT family_id FROM player_families WHERE name=?`, []any{name})
	if err != nil {
		return authoritativeMutation{}, err
	}
	familyID := i64(firstRowMap(res)["family_id"])
	// The founder takes the first seat.
	if _, err := conn.Execute(
		`INSERT INTO player_family_members(family_id,user_id,seniority_order,joined_at) VALUES(?,?,1,?)`,
		[]any{familyID, userID, now}); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"family_id": familyID, "name": name, "founder": founder, "seniority_order": 1}
	return authoritativeMutation{Result: result, Event: eventledger.Event{
		Domain: "player_family", EventType: "family_founded", EntityType: "player_family",
		EntityID: fmt.Sprint(familyID), Payload: result,
	}}, nil
}

func playerFamilyInviteAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p playerFamilyPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if p.InviteeUserID == userID {
		return authoritativeMutation{}, errors.New("you are already in your own house")
	}
	familyID, familyName, err := familyOf(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if familyID == 0 {
		return authoritativeMutation{}, errors.New("you belong to no house")
	}
	inviterName, err := livingCharacterName(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	inviteeName, err := livingCharacterName(conn, p.InviteeUserID)
	if err != nil {
		return authoritativeMutation{}, errors.New("that cultivator has no living character")
	}
	theirs, theirName, err := familyOf(conn, p.InviteeUserID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if theirs != 0 {
		return authoritativeMutation{}, fmt.Errorf("%s already belongs to %s", inviteeName, theirName)
	}
	count, err := countRow(conn, `SELECT COUNT(*) AS n FROM player_family_members WHERE family_id=?`, []any{familyID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if count >= familyMaxMembers {
		return authoritativeMutation{}, fmt.Errorf("%s already seats %d", familyName, familyMaxMembers)
	}
	// The seat has to be free, or the unique index would refuse the join
	// later - after the invitee had already accepted, which is the worst
	// possible moment to find out.
	seat := p.Seniority
	if seat <= 0 {
		seat = count + 1
	}
	taken, err := boolRow(conn,
		`SELECT 1 FROM player_family_members WHERE family_id=? AND seniority_order=? LIMIT 1`,
		[]any{familyID, seat})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if taken {
		return authoritativeMutation{}, fmt.Errorf("seniority %d is already held in %s", seat, familyName)
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`INSERT INTO player_family_invites
        (family_id,inviter_user_id,invitee_user_id,requested_order,created_at,expires_at) VALUES(?,?,?,?,?,?)
        ON CONFLICT(invitee_user_id) DO UPDATE SET family_id=excluded.family_id,inviter_user_id=excluded.inviter_user_id,
        requested_order=excluded.requested_order,created_at=excluded.created_at,expires_at=excluded.expires_at`,
		[]any{familyID, userID, p.InviteeUserID, seat, now, now + familyInviteTTLSeconds}); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"family_id": familyID, "name": familyName, "inviter": inviterName,
		"invitee": inviteeName, "invitee_user_id": p.InviteeUserID, "seniority_order": seat,
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{
		Domain: "player_family", EventType: "family_invited", EntityType: "player_family",
		EntityID: fmt.Sprint(familyID), Payload: result,
	}}, nil
}

func playerFamilyRespondAction(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p playerFamilyPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	if _, err := livingCharacterName(conn, userID); err != nil {
		return authoritativeMutation{}, err
	}
	res, err := conn.Execute(`SELECT i.family_id,i.requested_order,i.expires_at,f.name
        FROM player_family_invites i JOIN player_families f ON f.family_id=i.family_id
        WHERE i.invitee_user_id=?`, []any{userID})
	if err != nil {
		return authoritativeMutation{}, err
	}
	row := firstRowMap(res)
	if row == nil {
		return authoritativeMutation{}, errors.New("no house has invited you")
	}
	familyID, seat, familyName := i64(row["family_id"]), i64(row["requested_order"]), fmt.Sprint(row["name"])
	now := float64(time.Now().UnixNano()) / 1e9
	expired := floatOf(row["expires_at"]) <= now
	// However it ends, the offer is spent.
	if _, err := conn.Execute(`DELETE FROM player_family_invites WHERE invitee_user_id=?`, []any{userID}); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"family_id": familyID, "name": familyName, "accepted": false, "expired": expired}
	if expired {
		result["reason"] = "the invitation had expired"
	} else if p.Accept {
		mine, mineName, err := familyOf(conn, userID)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if mine != 0 {
			return authoritativeMutation{}, fmt.Errorf("you already belong to %s", mineName)
		}
		taken, err := boolRow(conn,
			`SELECT 1 FROM player_family_members WHERE family_id=? AND seniority_order=? LIMIT 1`,
			[]any{familyID, seat})
		if err != nil {
			return authoritativeMutation{}, err
		}
		if taken {
			// Somebody took the seat between the offer and the answer.
			next, err := countRow(conn, `SELECT COALESCE(MAX(seniority_order),0)+1 AS n FROM player_family_members WHERE family_id=?`, []any{familyID})
			if err != nil {
				return authoritativeMutation{}, err
			}
			seat = next
		}
		if _, err := conn.Execute(
			`INSERT INTO player_family_members(family_id,user_id,seniority_order,joined_at) VALUES(?,?,?,?)`,
			[]any{familyID, userID, seat, now}); err != nil {
			return authoritativeMutation{}, err
		}
		result["accepted"] = true
		result["seniority_order"] = seat
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{
		Domain: "player_family", EventType: "family_invite_answered", EntityType: "player_family",
		EntityID: fmt.Sprint(familyID), Payload: result,
	}}, nil
}

func playerFamilyLeaveAction(conn *storage.Conn, userID int64, _ json.RawMessage) (authoritativeMutation, error) {
	left, err := playerFamilyDepartTx(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if left.FamilyID == 0 {
		return authoritativeMutation{}, errors.New("you belong to no house")
	}
	result := map[string]any{"family_id": left.FamilyID, "name": left.Name, "dissolved": left.Dissolved, "remaining": left.Remaining}
	return authoritativeMutation{Result: result, Event: eventledger.Event{
		Domain: "player_family", EventType: "family_left", EntityType: "player_family",
		EntityID: fmt.Sprint(left.FamilyID), Payload: result,
	}}, nil
}

// playerFamilyDeparture is what one member leaving did to their house.
type playerFamilyDeparture struct {
	FamilyID  int64
	Name      string
	Dissolved bool
	Remaining int64
	HeirID    int64 // the member who became founder, 0 when the founder did not change
}

// playerFamilyDepartTx is the one statement of what happens to a house when a
// member goes: `family.leave` and a character reset (v1.0.14) both call it, so
// a founder who resets is succeeded exactly as a founder who walks out is. A
// FamilyID of 0 means they belonged to no house, which is not an error here -
// the reset asks about everybody, and most people have no player family.
func playerFamilyDepartTx(conn *storage.Conn, userID int64) (playerFamilyDeparture, error) {
	familyID, familyName, err := familyOf(conn, userID)
	if err != nil || familyID == 0 {
		return playerFamilyDeparture{}, err
	}
	out := playerFamilyDeparture{FamilyID: familyID, Name: familyName}
	if _, err := conn.Execute(`DELETE FROM player_family_members WHERE user_id=?`, []any{userID}); err != nil {
		return playerFamilyDeparture{}, err
	}
	remaining, err := countRow(conn, `SELECT COUNT(*) AS n FROM player_family_members WHERE family_id=?`, []any{familyID})
	if err != nil {
		return playerFamilyDeparture{}, err
	}
	out.Remaining = remaining
	out.Dissolved = remaining == 0
	if out.Dissolved {
		// The cascade takes the invites and the children with it. A house
		// with nobody in it is not a house.
		if _, err := conn.Execute(`DELETE FROM player_families WHERE family_id=?`, []any{familyID}); err != nil {
			return playerFamilyDeparture{}, err
		}
		return out, nil
	}
	// The founder leaving hands the house to the most senior who stays,
	// rather than leaving a founder_user_id pointing at somebody gone.
	founder, err := countRow(conn, `SELECT COUNT(*) AS n FROM player_families WHERE family_id=? AND founder_user_id=?`, []any{familyID, userID})
	if err != nil {
		return playerFamilyDeparture{}, err
	}
	if founder > 0 {
		heirRes, err := conn.Execute(
			`SELECT user_id FROM player_family_members WHERE family_id=? ORDER BY seniority_order LIMIT 1`,
			[]any{familyID})
		if err != nil {
			return playerFamilyDeparture{}, err
		}
		if heir := firstRowMap(heirRes); heir != nil {
			out.HeirID = i64(heir["user_id"])
			if _, err := conn.Execute(`UPDATE player_families SET founder_user_id=? WHERE family_id=?`,
				[]any{out.HeirID, familyID}); err != nil {
				return playerFamilyDeparture{}, err
			}
		}
	}
	return out, nil
}

func playerFamilyChildAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p playerFamilyPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	parent, err := livingCharacterName(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	familyID, familyName, err := familyOf(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if familyID == 0 {
		return authoritativeMutation{}, errors.New("you belong to no house")
	}
	name := strings.Join(strings.Fields(p.ChildName), " ")
	if n := len([]rune(name)); n < 1 || n > familyChildNameMax {
		return authoritativeMutation{}, fmt.Errorf("a child's name must be 1 to %d characters", familyChildNameMax)
	}
	taken, err := boolRow(conn, `SELECT 1 FROM family_children WHERE family_id=? AND name=? COLLATE NOCASE LIMIT 1`,
		[]any{familyID, name})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if taken {
		return authoritativeMutation{}, fmt.Errorf("%s already has a child named %q", familyName, name)
	}
	gender := strings.ToLower(strings.TrimSpace(p.Gender))
	if gender != "male" && gender != "female" {
		gender = "neutral"
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	// A child's talent is the world's to decide, not the parent's to choose.
	root := "Mortal Root"
	if len(catalog.Roots) > 0 {
		pick, err := gamerng.Intn(len(catalog.Roots))
		if err != nil {
			return authoritativeMutation{}, err
		}
		root = catalog.Roots[pick]
	}
	potential, err := gamerng.Intn(100)
	if err != nil {
		return authoritativeMutation{}, err
	}
	canCultivate := int64(0)
	if root != "Mortal Root" && potential >= 25 {
		canCultivate = 1
	}
	lifespan := int64(75)
	if canCultivate == 1 {
		lifespan = 120
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err := conn.Execute(`INSERT INTO family_children
        (family_id,parent_user_id,name,gender,birth_game_minute,spiritual_root,cultivation_potential,
         can_cultivate,awakening_state,natural_lifespan_years,status,created_at)
        VALUES(?,?,?,?,?,?,?,?,0,?, 'alive',?)`,
		[]any{familyID, userID, name, gender, gameMinute, root, potential, canCultivate, lifespan, now}); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{
		"family_id": familyID, "family": familyName, "parent": parent, "name": name, "gender": gender,
		"spiritual_root": root, "cultivation_potential": int64(potential),
		"can_cultivate": canCultivate == 1, "natural_lifespan_years": lifespan,
	}
	if err := recordWorldHistoryTx(conn, fmt.Sprintf("player_family_birth:%d:%s:%d", familyID, name, gameMinute),
		"family_birth", name+" is born to "+familyName,
		fmt.Sprintf("%s is born to %s of %s, with a %s spiritual root.", name, parent, familyName, root),
		30, "public", "", familyName, "player", fmt.Sprint(userID), parent, "family", fmt.Sprint(familyID), familyName,
		&userID, "", []string{"family", "birth"}, gameMinute, map[string]any{"child": name}, now); err != nil {
		return authoritativeMutation{}, err
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{
		Domain: "player_family", EventType: "family_child_born", EntityType: "player_family",
		EntityID: fmt.Sprint(familyID), GameMinute: gameMinute, Payload: result,
	}}, nil
}

// playerFamilyStatus is the read: the house, who sits in it and in what order,
// the children, and any offer waiting for the asker.
func playerFamilyStatus(conn *storage.Conn, userID int64) (any, error) {
	out := map[string]any{"in_family": false}
	inviteRes, err := conn.Execute(`SELECT f.name,i.requested_order,i.expires_at,c.name AS inviter
        FROM player_family_invites i JOIN player_families f ON f.family_id=i.family_id
        LEFT JOIN characters c ON c.user_id=i.inviter_user_id WHERE i.invitee_user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	if row := firstRowMap(inviteRes); row != nil {
		out["invite"] = map[string]any{
			"family": fmt.Sprint(row["name"]), "inviter": fmt.Sprint(row["inviter"]),
			"seniority_order": i64(row["requested_order"]),
			"expired":         floatOf(row["expires_at"]) <= float64(time.Now().UnixNano())/1e9,
		}
	}
	familyID, familyName, err := familyOf(conn, userID)
	if err != nil {
		return nil, err
	}
	if familyID == 0 {
		return out, nil
	}
	out["in_family"] = true
	out["family_id"] = familyID
	out["name"] = familyName
	memberRes, err := conn.Execute(`SELECT m.user_id,m.seniority_order,c.name,c.realm_index,c.phase,c.life_status,
        (f.founder_user_id=m.user_id) AS is_founder
        FROM player_family_members m JOIN player_families f ON f.family_id=m.family_id
        LEFT JOIN characters c ON c.user_id=m.user_id
        WHERE m.family_id=? ORDER BY m.seniority_order`, []any{familyID})
	if err != nil {
		return nil, err
	}
	members := []map[string]any{}
	for _, row := range memberRes.Rows {
		members = append(members, map[string]any{
			"user_id": storage.ParseInt(row[0]), "seniority_order": storage.ParseInt(row[1]),
			"name": fmt.Sprint(row[2]), "realm_index": storage.ParseInt(row[3]),
			"phase": storage.ParseInt(row[4]), "life_status": fmt.Sprint(row[5]),
			"founder": storage.ParseInt(row[6]) != 0,
		})
	}
	out["members"] = members
	childRes, err := conn.Execute(`SELECT name,gender,spiritual_root,cultivation_potential,can_cultivate,status,birth_game_minute
        FROM family_children WHERE family_id=? ORDER BY birth_game_minute,child_id`, []any{familyID})
	if err != nil {
		return nil, err
	}
	children := []map[string]any{}
	for _, row := range childRes.Rows {
		children = append(children, map[string]any{
			"name": fmt.Sprint(row[0]), "gender": fmt.Sprint(row[1]),
			"spiritual_root": fmt.Sprint(row[2]), "cultivation_potential": storage.ParseInt(row[3]),
			"can_cultivate": storage.ParseInt(row[4]) != 0, "status": fmt.Sprint(row[5]),
			"birth_game_minute": storage.ParseInt(row[6]),
		})
	}
	out["children"] = children
	return out, nil
}

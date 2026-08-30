package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type pvpChallengePayload struct {
	TargetUserID int64  `json:"target_user_id"`
	Stakes       string `json:"stakes"`
	TTLSeconds   int64  `json:"ttl_seconds"`
}
type pvpRespondPayload struct {
	ChallengeID int64 `json:"challenge_id"`
	Accept      bool  `json:"accept"`
}
type pvpActPayload struct {
	MatchID      int64  `json:"match_id"`
	Style        string `json:"style"`
	MatchVersion *int64 `json:"match_version,omitempty"`
}

func adjustReputationTx(conn *storage.Conn, userID int64, faction string, delta int64, reason string, now float64) (int64, error) {
	if delta > 100 {
		delta = 100
	}
	if delta < -100 {
		delta = -100
	}
	_, e := conn.Execute(`INSERT INTO faction_reputation(user_id,faction_key,score,last_reason,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id,faction_key) DO UPDATE SET score=MAX(-100,MIN(100,faction_reputation.score+excluded.score)),last_reason=excluded.last_reason,updated_at=excluded.updated_at`, []any{userID, faction, delta, reason, now})
	if e != nil {
		return 0, e
	}
	r, e := conn.Execute(`SELECT score FROM faction_reputation WHERE user_id=? AND faction_key=?`, []any{userID, faction})
	if e != nil {
		return 0, e
	}
	row := firstRowMap(r)
	if row == nil {
		return 0, nil
	}
	return i64(row["score"]), nil
}
func pvpMatchRow(conn *storage.Conn, matchID int64) (map[string]any, error) {
	r, e := conn.Execute(`SELECT * FROM pvp_matches WHERE match_id=?`, []any{matchID})
	if e != nil {
		return nil, e
	}
	return firstRowMap(r), nil
}

func pvpChallengeAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p pvpChallengePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	if p.TargetUserID <= 0 || p.TargetUserID == userID {
		return authoritativeMutation{}, errors.New("target must be another cultivator")
	}
	actor, e := loadMechanicsCharacter(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	target, e := loadMechanicsCharacter(conn, p.TargetUserID)
	if e != nil {
		return authoritativeMutation{}, errors.New("target cultivator is unavailable")
	}
	if actor.LifeStatus != "alive" || target.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("both cultivators must be alive")
	}
	if actor.Location != target.Location {
		return authoritativeMutation{}, errors.New("both cultivators must be at the same location")
	}
	if loc, ok := catalog.Locations[actor.Location]; ok && loc.SafeZone {
		return authoritativeMutation{}, errors.New("local formations suppress PvP here")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if p.TTLSeconds <= 0 {
		p.TTLSeconds = 300
	}
	p.Stakes = strings.TrimSpace(p.Stakes)
	if p.Stakes == "" {
		p.Stakes = "honor"
	}
	if len([]rune(p.Stakes)) > 200 {
		p.Stakes = string([]rune(p.Stakes)[:200])
	}
	cur, e := conn.Execute(`INSERT INTO pvp_challenges(challenger_user_id,target_user_id,stakes,status,created_at,expires_at) VALUES(?,?,?,'pending',?,?)`, []any{userID, p.TargetUserID, p.Stakes, now, now + float64(p.TTLSeconds)})
	if e != nil {
		return authoritativeMutation{}, e
	}
	result := map[string]any{"challenge_id": cur.LastInsertID, "challenger_user_id": userID, "target_user_id": p.TargetUserID, "stakes": p.Stakes, "expires_at": now + float64(p.TTLSeconds)}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "pvp", EventType: "pvp.challenge", EntityType: "pvp_challenge", EntityID: fmt.Sprint(cur.LastInsertID), Payload: result}}, nil
}

func pvpRespondAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p pvpRespondPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	now := float64(time.Now().UnixNano()) / 1e9
	r, e := conn.Execute(`SELECT * FROM pvp_challenges WHERE challenge_id=? AND target_user_id=? AND status='pending'`, []any{p.ChallengeID, userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	ch := firstRowMap(r)
	exp := 0.0
	if ch != nil {
		exp, _ = strconv.ParseFloat(fmt.Sprint(ch["expires_at"]), 64)
	}
	if ch == nil || exp < now {
		return authoritativeMutation{}, errors.New("pending challenge is missing or expired")
	}
	status := "rejected"
	result := map[string]any{"challenge_id": p.ChallengeID, "status": status}
	if p.Accept {
		active, e := conn.Execute(`SELECT 1 AS found FROM pvp_matches WHERE status='active' AND (player1_user_id IN (?,?) OR player2_user_id IN (?,?)) LIMIT 1`, []any{i64(ch["challenger_user_id"]), userID, i64(ch["challenger_user_id"]), userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if firstRowMap(active) != nil {
			return authoritativeMutation{}, errors.New("one of the cultivators already has an active duel")
		}
		v1, e := conn.Execute(`SELECT vitality_max FROM characters WHERE user_id=?`, []any{i64(ch["challenger_user_id"])})
		if e != nil {
			return authoritativeMutation{}, e
		}
		v2, e := conn.Execute(`SELECT vitality_max FROM characters WHERE user_id=?`, []any{userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		r1, r2 := firstRowMap(v1), firstRowMap(v2)
		if r1 == nil || r2 == nil {
			return authoritativeMutation{}, errors.New("duel character vitality unavailable")
		}
		cur, e := conn.Execute(`INSERT INTO pvp_matches(challenge_id,player1_user_id,player2_user_id,player1_hp,player2_hp,turn_user_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'active',?,?)`, []any{p.ChallengeID, i64(ch["challenger_user_id"]), userID, max64(1, i64(r1["vitality_max"])), max64(1, i64(r2["vitality_max"])), i64(ch["challenger_user_id"]), now, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		status = "accepted"
		result["status"] = status
		result["match_id"] = cur.LastInsertID
	}
	_, e = conn.Execute(`UPDATE pvp_challenges SET status=? WHERE challenge_id=?`, []any{status, p.ChallengeID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "pvp", EventType: "pvp.respond", EntityType: "pvp_challenge", EntityID: fmt.Sprint(p.ChallengeID), Payload: result}}, nil
}
func max64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}

func pvpActAction(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p pvpActPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.Style = strings.ToLower(strings.TrimSpace(p.Style))
	if p.Style != "attack" && p.Style != "defend" && p.Style != "surrender" {
		return authoritativeMutation{}, errors.New("style must be attack, defend, or surrender")
	}
	m, e := pvpMatchRow(conn, p.MatchID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if m == nil || fmt.Sprint(m["status"]) != "active" {
		return authoritativeMutation{}, errors.New("active duel not found")
	}
	if i64(m["turn_user_id"]) != userID {
		return authoritativeMutation{}, errors.New("it is not your turn")
	}
	if p.MatchVersion != nil && i64(m["version"]) != *p.MatchVersion {
		return authoritativeMutation{}, errors.New("stale duel state")
	}
	p1, p2 := i64(m["player1_user_id"]), i64(m["player2_user_id"])
	actorP1 := userID == p1
	opponentID := p1
	if actorP1 {
		opponentID = p2
	}
	now := float64(time.Now().UnixNano()) / 1e9
	result := map[string]any{"match_id": p.MatchID, "style": p.Style, "opponent_user_id": opponentID}
	if p.Style == "surrender" {
		_, e = conn.Execute(`UPDATE pvp_matches SET status='finished',winner_user_id=?,version=version+1,updated_at=? WHERE match_id=?`, []any{opponentID, now, p.MatchID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		_, e = adjustReputationTx(conn, opponentID, "Martial Society", 2, fmt.Sprintf("won consensual duel #%d", p.MatchID), now)
		if e != nil {
			return authoritativeMutation{}, e
		}
		_, e = adjustReputationTx(conn, userID, "Martial Society", 1, fmt.Sprintf("honorably completed consensual duel #%d", p.MatchID), now)
		if e != nil {
			return authoritativeMutation{}, e
		}
		updated, _ := pvpMatchRow(conn, p.MatchID)
		result["match"] = updated
		result["finished"] = true
		result["winner_user_id"] = opponentID
		return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "pvp", EventType: "pvp.surrender", EntityType: "pvp_match", EntityID: fmt.Sprint(p.MatchID), Payload: result}}, nil
	}
	actorGuard := "player2_guard"
	targetGuardField := "player1_guard"
	hpField := "player1_hp"
	targetGuard := i64(m["player1_guard"])
	if actorP1 {
		actorGuard = "player1_guard"
		targetGuardField = "player2_guard"
		hpField = "player2_hp"
		targetGuard = i64(m["player2_guard"])
	}
	if p.Style == "defend" {
		_, e = conn.Execute(fmt.Sprintf(`UPDATE pvp_matches SET %s=1,%s=0,turn_user_id=?,version=version+1,updated_at=? WHERE match_id=?`, actorGuard, targetGuardField), []any{opponentID, now, p.MatchID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		updated, _ := pvpMatchRow(conn, p.MatchID)
		result["match"] = updated
		result["finished"] = false
		return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "pvp", EventType: "pvp.defend", EntityType: "pvp_match", EntityID: fmt.Sprint(p.MatchID), Payload: result}}, nil
	}
	actor, e := loadMechanicsCharacter(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	opp, e := loadMechanicsCharacter(conn, opponentID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	attackBase := actor.Attributes["body"]
	if actor.Attributes["spirit"] > attackBase {
		attackBase = actor.Attributes["spirit"]
	}
	attackMod := attackBase + actor.RealmIndex*2 + actor.Phase/3
	defBase := opp.Attributes["agility"]
	if opp.Attributes["body"] > defBase {
		defBase = opp.Attributes["body"]
	}
	defTN := int64(10) + defBase + opp.RealmIndex*2 + opp.Phase/3
	roll, e := roll2d10(attackMod, defTN)
	if e != nil {
		return authoritativeMutation{}, e
	}
	damage := int64(0)
	if boolResult(roll) {
		damage = 2
		mar := resultMargin(roll)
		if mar > 0 {
			damage += mar / 3
		}
		if actor.RealmIndex > opp.RealmIndex {
			damage += actor.RealmIndex - opp.RealmIndex
		}
	}
	if targetGuard > 0 {
		damage -= 2
		if damage < 0 {
			damage = 0
		}
	}
	newHP := i64(m[hpField]) - damage
	if newHP < 0 {
		newHP = 0
	}
	finished := newHP <= 0
	if finished {
		_, e = conn.Execute(fmt.Sprintf(`UPDATE pvp_matches SET %s=0,%s=0,%s=0,status='finished',winner_user_id=?,version=version+1,updated_at=? WHERE match_id=?`, hpField, actorGuard, targetGuardField), []any{userID, now, p.MatchID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		_, e = adjustReputationTx(conn, userID, "Martial Society", 2, fmt.Sprintf("won consensual duel #%d", p.MatchID), now)
		if e != nil {
			return authoritativeMutation{}, e
		}
		_, e = adjustReputationTx(conn, opponentID, "Martial Society", 1, fmt.Sprintf("honorably completed consensual duel #%d", p.MatchID), now)
		if e != nil {
			return authoritativeMutation{}, e
		}
	} else {
		_, e = conn.Execute(fmt.Sprintf(`UPDATE pvp_matches SET %s=?,%s=0,%s=0,turn_user_id=?,version=version+1,updated_at=? WHERE match_id=?`, hpField, actorGuard, targetGuardField), []any{newHP, opponentID, now, p.MatchID})
		if e != nil {
			return authoritativeMutation{}, e
		}
	}
	updated, _ := pvpMatchRow(conn, p.MatchID)
	for k, v := range roll {
		result[k] = v
	}
	result["damage"] = damage
	result["match"] = updated
	result["finished"] = finished
	if finished {
		result["winner_user_id"] = userID
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "pvp", EventType: "pvp.attack", EntityType: "pvp_match", EntityID: fmt.Sprint(p.MatchID), Payload: result}}, nil
}

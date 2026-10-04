package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Three per-player levers the Player Editor had no way to write (v1.23.0):
// a trade's rank, a Law's comprehension and a sect member's contribution.
// Each was a number a fault could cost somebody and only a database edit
// could restore - the shape every other admin.player.* lever exists to end.
// Each is held to the range the rule that writes it holds, audits what it
// replaced in the same transaction, and is undoable.

// adminProfessions is every trade the engine advances: the four the household
// teaches plus the five that call advanceProfessionTx under their own name.
// A GM lever offering a trade no rule reads would make a row nothing ever
// looks at, so the lever is held to this list and the dashboard's picker is
// held to it by test_the_player_editor_edits_what_the_engine_writes.py.
func adminProfessions() []string {
	out := append([]string{}, householdLessonTrades...)
	out = append(out, "Foraging", miningProfession, beastTamingProfession, artifactRefiningProfession, appraisalProfession)
	sort.Strings(out)
	return out
}

func adminSetProfession(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	profession := stringField(p, "profession")
	known := false
	for _, name := range adminProfessions() {
		if name == profession {
			known = true
		}
	}
	if !known {
		return nil, fmt.Errorf("profession must be one of %s", strings.Join(adminProfessions(), ", "))
	}
	level, err := requiredInt(p, "level")
	if err != nil {
		return nil, err
	}
	if level < 0 || level > tradeTopRank {
		return nil, fmt.Errorf("level must be 0 to %d", tradeTopRank)
	}
	xp := storage.ParseInt(p["xp"])
	// Below the top rank a full bar is a rank-up the next craft would take,
	// so the lever refuses to store one; at the top the bar only fills.
	if xp < 0 || (level < tradeTopRank && xp >= professionXPNeeded(level)) {
		return nil, fmt.Errorf("xp must be 0 to %d at level %d", professionXPNeeded(level)-1, level)
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	charRow, err := characterRowTx(conn, uid)
	if err != nil {
		return nil, err
	}
	res, err := conn.Execute(`SELECT level,xp FROM profession_progress WHERE user_id=? AND profession=?`, []any{uid, profession})
	if err != nil {
		return nil, err
	}
	before := map[string]any{"profession": profession, "level": nil, "xp": nil}
	if row := firstRowMap(res); row != nil {
		before["level"], before["xp"] = storage.ParseInt(row["level"]), storage.ParseInt(row["xp"])
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`INSERT INTO profession_progress(user_id,profession,level,xp,updated_at) VALUES(?,?,?,?,?)
		ON CONFLICT(user_id,profession) DO UPDATE SET level=excluded.level,xp=excluded.xp,updated_at=excluded.updated_at`,
		[]any{uid, profession, level, xp, now}); err != nil {
		return nil, err
	}
	after := map[string]any{"profession": profession, "level": level, "xp": xp}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_profession", fmt.Sprintf("user:%d", uid), before, after, stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": charRow["name"], "profession": profession, "level": level, "xp": xp}, nil
}

func adminSetLaw(conn *storage.Conn, catalog worlddata.Catalog, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	lawID := stringField(p, "law_id")
	def, ok := catalog.LawSystem.Laws[lawID]
	if !ok {
		ids := make([]string, 0, len(catalog.LawSystem.Laws))
		for id := range catalog.LawSystem.Laws {
			ids = append(ids, id)
		}
		sort.Strings(ids)
		return nil, fmt.Errorf("law_id must be one of %s", strings.Join(ids, ", "))
	}
	comprehension, err := requiredInt(p, "comprehension")
	if err != nil {
		return nil, err
	}
	// The same ceiling law.comprehend clamps every gain to.
	if comprehension < 0 || comprehension > 100 {
		return nil, errors.New("comprehension must be 0 to 100")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	charRow, err := characterRowTx(conn, uid)
	if err != nil {
		return nil, err
	}
	res, err := conn.Execute(`SELECT comprehension,insights FROM law_progress WHERE user_id=? AND law_id=?`, []any{uid, lawID})
	if err != nil {
		return nil, err
	}
	before := map[string]any{"comprehension": nil, "insights": nil}
	insights := int64(0)
	if row := firstRowMap(res); row != nil {
		insights = storage.ParseInt(row["insights"])
		before["comprehension"], before["insights"] = storage.ParseInt(row["comprehension"]), insights
	}
	// Insights are optional: a GM correcting the number leaves the count of
	// sittings alone unless they say otherwise.
	if stringField(p, "insights") != "" {
		insights = storage.ParseInt(p["insights"])
		if insights < 0 {
			return nil, errors.New("insights must be 0 or more")
		}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`INSERT INTO law_progress(user_id,law_id,comprehension,insights,updated_at) VALUES(?,?,?,?,?)
		ON CONFLICT(user_id,law_id) DO UPDATE SET comprehension=excluded.comprehension,insights=excluded.insights,updated_at=excluded.updated_at`,
		[]any{uid, lawID, comprehension, insights, now}); err != nil {
		return nil, err
	}
	after := map[string]any{"law_id": lawID, "comprehension": comprehension, "insights": insights}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_law", fmt.Sprintf("user:%d law:%s", uid, lawID), before, after, stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": charRow["name"], "law_id": lawID, "law": def.Name, "comprehension": comprehension, "insights": insights}, nil
}

// adminSetSectContribution sets a member's spendable balance and, where the
// column exists, the lifetime count promotion reads. It deliberately promotes
// nobody: a GM setting the count says what was earned, and the rank is the
// sect card's to set - a lever that moved both would make one correction two.
func adminSetSectContribution(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	points, err := requiredInt(p, "contribution_points")
	if err != nil {
		return nil, err
	}
	if points < 0 {
		return nil, errors.New("contribution_points must be 0 or more")
	}
	setEarned := stringField(p, "contribution_earned") != ""
	earned := storage.ParseInt(p["contribution_earned"])
	if setEarned && earned < 0 {
		return nil, errors.New("contribution_earned must be 0 or more")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	earnedColumn := sectEarnedColumn(conn)
	if setEarned && !earnedColumn {
		return nil, errors.New("this world has no lifetime contribution count yet; set the balance alone")
	}
	sql := `SELECT sect_name,contribution_points`
	if earnedColumn {
		sql += `,contribution_earned`
	}
	res, err := conn.Execute(sql+` FROM sect_membership WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("player is not in a recorded sect")
	}
	before := map[string]any{"contribution_points": storage.ParseInt(row["contribution_points"])}
	after := map[string]any{"contribution_points": points}
	if _, err = conn.Execute(`UPDATE sect_membership SET contribution_points=? WHERE user_id=?`, []any{points, uid}); err != nil {
		return nil, err
	}
	if earnedColumn {
		before["contribution_earned"] = storage.ParseInt(row["contribution_earned"])
		after["contribution_earned"] = before["contribution_earned"]
		if setEarned {
			if _, err = conn.Execute(`UPDATE sect_membership SET contribution_earned=? WHERE user_id=?`, []any{earned, uid}); err != nil {
				return nil, err
			}
			after["contribution_earned"] = earned
		}
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_sect_contribution", fmt.Sprintf("user:%d", uid), before, after, stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	result := map[string]any{"user_id": uid, "sect_name": row["sect_name"]}
	for k, v := range after {
		result[k] = v
	}
	return result, nil
}

func reverseSetProfession(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
	uid, err := parseTargetUserID(target)
	if err != nil {
		return nil, err
	}
	profession := fmt.Sprint(after["profession"])
	snap := pickSnapshot(before, after, redo)
	if snap["level"] == nil {
		return []sqlStmt{{`DELETE FROM profession_progress WHERE user_id=? AND profession=?`, []any{uid, profession}}}, nil
	}
	return []sqlStmt{{`INSERT INTO profession_progress(user_id,profession,level,xp,updated_at) VALUES(?,?,?,?,?)
		ON CONFLICT(user_id,profession) DO UPDATE SET level=excluded.level,xp=excluded.xp,updated_at=excluded.updated_at`,
		[]any{uid, profession, i64(snap["level"]), i64(snap["xp"]), float64(time.Now().UnixNano()) / 1e9}}}, nil
}

func reverseSetLaw(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
	uid, err := parseTargetUserID(target)
	if err != nil {
		return nil, err
	}
	lawID, err := parseTargetSuffix(target, "law")
	if err != nil {
		return nil, err
	}
	snap := pickSnapshot(before, after, redo)
	if snap["comprehension"] == nil {
		return []sqlStmt{{`DELETE FROM law_progress WHERE user_id=? AND law_id=?`, []any{uid, lawID}}}, nil
	}
	return []sqlStmt{{`INSERT INTO law_progress(user_id,law_id,comprehension,insights,updated_at) VALUES(?,?,?,?,?)
		ON CONFLICT(user_id,law_id) DO UPDATE SET comprehension=excluded.comprehension,insights=excluded.insights,updated_at=excluded.updated_at`,
		[]any{uid, lawID, i64(snap["comprehension"]), i64(snap["insights"]), float64(time.Now().UnixNano()) / 1e9}}}, nil
}

func reverseSetSectContribution(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
	uid, err := parseTargetUserID(target)
	if err != nil {
		return nil, err
	}
	snap := pickSnapshot(before, after, redo)
	stmts := []sqlStmt{{`UPDATE sect_membership SET contribution_points=? WHERE user_id=?`, []any{i64(snap["contribution_points"]), uid}}}
	if _, ok := snap["contribution_earned"]; ok {
		stmts = append(stmts, sqlStmt{`UPDATE sect_membership SET contribution_earned=? WHERE user_id=?`, []any{i64(snap["contribution_earned"]), uid}})
	}
	return stmts, nil
}

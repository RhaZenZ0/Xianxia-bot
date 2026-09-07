package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/storage"
)

// Admin writes that used to run from Python (v0.23.0, the v0.21 Authority I
// backlog).
//
// These were the last GM mutations the Discord side performed itself: set and
// clear a master, change a sect rank, adjust master attention, grant a storage
// container, open a secret realm. Each was a bare DB call followed by a
// separate audit write, which is the thing rule 6 in CLAUDE.md exists to
// prevent - if the process died between them the world changed and the log did
// not. Every action below writes its own audit row inside the same transaction
// as the change, so the two cannot disagree.
//
// The validations are transcribed from the Python they replace rather than
// improved, with one exception noted at setMasterCycleLimit.

// setMasterCycleLimit bounds the lineage walk that rejects a cycle. The Python
// version walked 64 links and then gave up *silently*, accepting an assignment
// it had not finished checking. A deeper chain than this is not a valid
// lineage, so exhausting the budget is a refusal here, not a shrug.
const setMasterCycleLimit = 64

func adminSetMaster(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	disciple, err := requiredInt(p, "disciple_user_id")
	if err != nil {
		return nil, err
	}
	if disciple <= 0 {
		return nil, errors.New("invalid disciple_user_id")
	}
	clear, _ := p["clear"].(bool)

	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()

	discipleRow, err := characterRowTx(conn, disciple)
	if err != nil {
		return nil, err
	}
	beforeRes, err := conn.Execute(
		`SELECT master_user_id FROM sect_lineage WHERE disciple_user_id=?`, []any{disciple})
	if err != nil {
		return nil, err
	}
	var before map[string]any
	if row := firstRowMap(beforeRes); row != nil {
		before = map[string]any{"master_user_id": storage.ParseInt(row["master_user_id"])}
	} else {
		before = map[string]any{"master_user_id": nil}
	}

	if clear {
		if _, err = conn.Execute(
			`DELETE FROM sect_lineage WHERE disciple_user_id=?`, []any{disciple}); err != nil {
			return nil, err
		}
		result := map[string]any{
			"disciple_user_id": disciple,
			"disciple_name":    discipleRow["name"],
			"master_user_id":   nil,
		}
		if err := auditAdmin(conn, adminUserID, "admin.player.set_master",
			fmt.Sprintf("user:%d", disciple), before, map[string]any{"master_user_id": nil},
			stringField(p, "reason")); err != nil {
			return nil, err
		}
		if err := conn.Commit(); err != nil {
			return nil, err
		}
		return result, nil
	}

	master, err := requiredInt(p, "master_user_id")
	if err != nil {
		return nil, err
	}
	if master <= 0 {
		return nil, errors.New("invalid master_user_id")
	}
	if master == disciple {
		return nil, errors.New("a cultivator cannot be their own master")
	}
	masterRow, err := characterRowTx(conn, master)
	if err != nil {
		return nil, err
	}

	// Both in a sect means the same sect. One or neither in a sect is allowed,
	// the same as before: a GM assigning a master to a sectless disciple is a
	// normal thing to do.
	memberships, err := conn.Execute(
		`SELECT user_id,sect_name FROM sect_membership WHERE user_id IN (?,?)`, []any{disciple, master})
	if err != nil {
		return nil, err
	}
	if len(memberships.Rows) == 2 {
		sects := map[int64]string{}
		for _, row := range memberships.Rows {
			sects[storage.ParseInt(row[0])] = fmt.Sprint(row[1])
		}
		if sects[disciple] != sects[master] {
			return nil, errors.New("master and disciple must belong to the same sect")
		}
	}

	if err := rejectLineageCycle(conn, disciple, master); err != nil {
		return nil, err
	}

	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(
		`INSERT INTO sect_lineage(disciple_user_id,master_user_id,accepted_at) VALUES(?,?,?)
		 ON CONFLICT(disciple_user_id) DO UPDATE SET
			master_user_id=excluded.master_user_id,accepted_at=excluded.accepted_at`,
		[]any{disciple, master, now},
	); err != nil {
		return nil, err
	}
	after := map[string]any{"master_user_id": master}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_master",
		fmt.Sprintf("user:%d", disciple), before, after, stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{
		"disciple_user_id": disciple,
		"disciple_name":    discipleRow["name"],
		"master_user_id":   master,
		"master_name":      masterRow["name"],
	}, nil
}

// rejectLineageCycle walks up from the proposed master. Reaching the disciple
// means the assignment would close a loop - A teaches B teaches A - which makes
// every lineage query that follows the chain run forever.
func rejectLineageCycle(conn *storage.Conn, disciple, master int64) error {
	cursor := master
	seen := map[int64]bool{disciple: true}
	for i := 0; i < setMasterCycleLimit; i++ {
		if seen[cursor] {
			return errors.New("that master assignment would create a lineage cycle")
		}
		seen[cursor] = true
		res, err := conn.Execute(
			`SELECT master_user_id FROM sect_lineage WHERE disciple_user_id=?`, []any{cursor})
		if err != nil {
			return err
		}
		row := firstRowMap(res)
		if row == nil {
			return nil
		}
		cursor = storage.ParseInt(row["master_user_id"])
	}
	return fmt.Errorf("lineage chain is deeper than %d links; refusing to assign a master it cannot verify", setMasterCycleLimit)
}

func adminSetSectRank(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	if uid <= 0 {
		return nil, errors.New("invalid user_id")
	}
	rankName := stringField(p, "rank_name")
	if rankName == "" {
		return nil, errors.New("rank_name is required")
	}
	rankLevel, err := requiredInt(p, "rank_level")
	if err != nil {
		return nil, err
	}

	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(
		`SELECT sect_name,rank_name,rank_level FROM sect_membership WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, errors.New("player is not in a recorded sect")
	}
	before := map[string]any{
		"rank_name":  row["rank_name"],
		"rank_level": storage.ParseInt(row["rank_level"]),
	}
	if _, err = conn.Execute(
		`UPDATE sect_membership SET rank_name=?,rank_level=? WHERE user_id=?`,
		[]any{rankName, rankLevel, uid},
	); err != nil {
		return nil, err
	}
	after := map[string]any{"rank_name": rankName, "rank_level": rankLevel}
	if err := auditAdmin(conn, adminUserID, "admin.player.set_sect_rank",
		fmt.Sprintf("user:%d", uid), before, after, stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{
		"user_id":    uid,
		"sect_name":  row["sect_name"],
		"rank_name":  rankName,
		"rank_level": rankLevel,
	}, nil
}

func adminMasterAttention(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	disciple, err := requiredInt(p, "disciple_user_id")
	if err != nil {
		return nil, err
	}
	if disciple <= 0 {
		return nil, errors.New("invalid disciple_user_id")
	}
	delta, err := requiredInt(p, "delta")
	if err != nil {
		return nil, err
	}

	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(
		`SELECT attention FROM sect_lineage WHERE disciple_user_id=?`, []any{disciple})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		// The Python version updated nothing and cheerfully reported 0, so a GM
		// adjusting attention for a disciple with no master saw a success they
		// had not had. There is nothing to adjust; say so.
		return nil, errors.New("that cultivator has no recorded master")
	}
	before := storage.ParseInt(row["attention"])
	if _, err = conn.Execute(
		`UPDATE sect_lineage SET attention=MAX(0,attention+?) WHERE disciple_user_id=?`,
		[]any{delta, disciple},
	); err != nil {
		return nil, err
	}
	res, err = conn.Execute(
		`SELECT attention FROM sect_lineage WHERE disciple_user_id=?`, []any{disciple})
	if err != nil {
		return nil, err
	}
	attention := storage.ParseInt(firstRowMap(res)["attention"])
	if err := auditAdmin(conn, adminUserID, "admin.player.master_attention",
		fmt.Sprintf("user:%d", disciple),
		map[string]any{"attention": before},
		map[string]any{"attention": attention, "delta": delta},
		stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"disciple_user_id": disciple, "attention": attention, "delta": delta}, nil
}

func adminGrantStorage(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	if uid <= 0 {
		return nil, errors.New("invalid user_id")
	}
	name := stringField(p, "name")
	if name == "" {
		return nil, errors.New("name is required")
	}
	containerID := stringField(p, "container_id")
	if containerID == "" {
		containerID = strings.ReplaceAll(strings.ToLower(name), " ", "_")
	}
	grade := stringField(p, "grade")
	if grade == "" {
		grade = "Earth"
	}
	slots, err := requiredInt(p, "slot_capacity")
	if err != nil {
		return nil, err
	}
	livingSpace, _ := p["living_space"].(bool)

	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	if _, err := characterRowTx(conn, uid); err != nil {
		return nil, err
	}
	// A replacement container may never be smaller than what is already inside
	// it, or the surplus becomes unreachable without becoming lost.
	usedRes, err := conn.Execute(
		`SELECT COUNT(*) FROM storage_inventory WHERE user_id=? AND quantity>0`, []any{uid})
	if err != nil {
		return nil, err
	}
	used := int64(0)
	if len(usedRes.Rows) > 0 && len(usedRes.Rows[0]) > 0 {
		used = storage.ParseInt(usedRes.Rows[0][0])
	}
	capacity := maxI64(used, maxI64(1, slots))

	beforeRes, err := conn.Execute(
		`SELECT container_id,name,grade,slot_capacity,living_space FROM storage_containers WHERE user_id=?`,
		[]any{uid})
	if err != nil {
		return nil, err
	}
	var before map[string]any
	if row := firstRowMap(beforeRes); row != nil {
		before = map[string]any{
			"container_id":  row["container_id"],
			"name":          row["name"],
			"grade":         row["grade"],
			"slot_capacity": storage.ParseInt(row["slot_capacity"]),
			"living_space":  storage.ParseInt(row["living_space"]) != 0,
		}
	} else {
		before = map[string]any{"container_id": nil}
	}

	now := float64(time.Now().UnixNano()) / 1e9
	living := int64(0)
	if livingSpace {
		living = 1
	}
	if _, err = conn.Execute(
		`INSERT INTO storage_containers(user_id,container_id,name,grade,slot_capacity,living_space,updated_at)
		 VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
			container_id=excluded.container_id,name=excluded.name,grade=excluded.grade,
			slot_capacity=excluded.slot_capacity,living_space=excluded.living_space,updated_at=excluded.updated_at`,
		[]any{uid, containerID, name, grade, capacity, living, now},
	); err != nil {
		return nil, err
	}
	after := map[string]any{
		"container_id":  containerID,
		"name":          name,
		"grade":         grade,
		"slot_capacity": capacity,
		"living_space":  livingSpace,
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.grant_storage",
		fmt.Sprintf("user:%d", uid), before, after, stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	result := map[string]any{"user_id": uid, "requested_slot_capacity": slots}
	for key, value := range after {
		result[key] = value
	}
	return result, nil
}

func adminSpawnRealm(conn *storage.Conn, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	realmID := stringField(p, "realm_id")
	title := stringField(p, "title")
	location := stringField(p, "location")
	if realmID == "" || title == "" || location == "" {
		return nil, errors.New("realm_id, title and location are required")
	}
	openHours, err := requiredInt(p, "open_hours")
	if err != nil {
		return nil, err
	}
	if openHours <= 0 {
		return nil, errors.New("open_hours must be positive")
	}

	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	now := float64(time.Now().UnixNano()) / 1e9
	// The event key is minted here rather than by the caller: it carries the
	// spawn time, and the engine is the only thing that knows what time it is.
	eventKey := fmt.Sprintf("secret:%s:admin:%d", realmID, time.Now().UnixNano())
	endsAt := now + float64(openHours)*3600

	if _, err = conn.Execute(
		`UPDATE world_events SET active=0 WHERE active=1 AND ends_at<=?`, []any{now}); err != nil {
		return nil, err
	}
	payload, err := json.Marshal(map[string]any{"definition_id": "admin_spawn", "realm_id": realmID})
	if err != nil {
		return nil, err
	}
	if _, err = conn.Execute(
		`INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at)
		 VALUES(?,'','secret_realm',?,?,?,1,?,?)
		 ON CONFLICT(event_key) DO UPDATE SET active=1,payload_json=excluded.payload_json,ends_at=excluded.ends_at`,
		[]any{eventKey, title, location, string(payload), now, endsAt},
	); err != nil {
		return nil, err
	}
	after := map[string]any{"realm_id": realmID, "ends_at": endsAt, "location": location}
	if err := auditAdmin(conn, adminUserID, "admin.world.spawn_realm", eventKey,
		map[string]any{"event_key": nil}, after, stringField(p, "reason")); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{
		"event_key": eventKey,
		"realm_id":  realmID,
		"title":     title,
		"location":  location,
		"starts_at": now,
		"ends_at":   endsAt,
	}, nil
}

// characterRowTx is the "does this player exist" check these actions share.
// It runs inside the transaction so a character deleted between the check and
// the write cannot slip through.
func characterRowTx(conn *storage.Conn, userID int64) (map[string]any, error) {
	res, err := conn.Execute(`SELECT user_id,name FROM characters WHERE user_id=?`, []any{userID})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, fmt.Errorf("character not found: %d", userID)
	}
	return row, nil
}

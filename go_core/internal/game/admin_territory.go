package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// admin.territory.set_controller (v1.38.0): a GM hands a territory to a sect,
// or makes it neutral again.
//
// Migration 74's first statement copied a gate's banner onto a neutral seat
// whoever held the gate, so a sect whose gate a rival had taken in a
// v1.12-v1.18 war lost its home city to that rival, and no repair could tell
// that from a city honestly held. The owner's call was a lever a GM pulls on
// the world that shows it, not a migration that guesses. The lever takes the
// whole place a key names (TerritoryGround: a gate, a district or a shop is
// its city, v1.0.9), refuses while a war contests the territory - the war is
// the two sects' to end, through war.act or war.peace - refuses a sect the
// catalogue does not know, writes the row's controller, records a public
// history row when a sect takes the ground, audits before and after, and is
// reversible: the undo restores the two controller columns from the snapshot.
type territoryControllerPayload struct {
	TerritoryKey string `json:"territory_key"`
	// The sect to hand the territory to; empty or absent makes it neutral.
	SectName string `json:"sect_name"`
	Reason   string `json:"reason"`
}

func adminTerritorySetController(conn *storage.Conn, catalog worlddata.Catalog, adminUserID int64, raw json.RawMessage) (any, error) {
	var p territoryControllerPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	p.TerritoryKey = strings.TrimSpace(p.TerritoryKey)
	if p.TerritoryKey == "" {
		return nil, errors.New("territory_key is required")
	}
	if whole, part := TerritoryGround(catalog, p.TerritoryKey); part {
		if whole == "" {
			return nil, fmt.Errorf("%s is no ground a sect can hold", p.TerritoryKey)
		}
		p.TerritoryKey = whole
	}
	sect := strings.TrimSpace(p.SectName)
	if sect != "" {
		if _, known := catalog.Sects[sect]; !known {
			return nil, fmt.Errorf("unknown sect %q", sect)
		}
	}
	if !tableExistsTx(conn, "territory_state") {
		return nil, errors.New("this world has no territories yet")
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	res, err := conn.Execute(`SELECT territory_key,name,controller_type,controller_key FROM territory_state WHERE territory_key=?`, []any{p.TerritoryKey})
	if err != nil {
		return nil, err
	}
	row := firstRowMap(res)
	if row == nil {
		return nil, fmt.Errorf("no territory named %q", p.TerritoryKey)
	}
	if tableExistsTx(conn, "territory_wars") {
		war, err := conn.Execute(`SELECT war_id FROM territory_wars WHERE territory_key=? AND status='active' LIMIT 1`, []any{p.TerritoryKey})
		if err != nil {
			return nil, err
		}
		if len(war.Rows) > 0 {
			return nil, fmt.Errorf("an active war (#%d) contests %s; it ends through war.act or war.peace, not a GM's hand", i64(war.Rows[0][0]), p.TerritoryKey)
		}
	}
	before := map[string]any{
		"territory_key":   p.TerritoryKey,
		"controller_type": fmt.Sprint(row["controller_type"]),
		"controller_key":  fmt.Sprint(row["controller_key"]),
	}
	controllerType, controllerKey := "neutral", ""
	if sect != "" {
		controllerType, controllerKey = "sect", sect
	}
	gm, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	now := nowSeconds()
	if _, err := conn.Execute(`UPDATE territory_state SET controller_type=?,controller_key=?,updated_game_minute=?,updated_at=? WHERE territory_key=?`,
		[]any{controllerType, controllerKey, gm, now, p.TerritoryKey}); err != nil {
		return nil, err
	}
	if sect != "" {
		recordTerritoryHandedTx(conn, catalog, sect, p.TerritoryKey, fmt.Sprint(row["name"]), gm, now)
	}
	after := map[string]any{
		"territory_key":   p.TerritoryKey,
		"controller_type": controllerType,
		"controller_key":  controllerKey,
	}
	if err := auditAdmin(conn, adminUserID, "admin.territory.set_controller", "territory:"+strings.ReplaceAll(p.TerritoryKey, " ", "_"), before, after, p.Reason); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	out := map[string]any{
		"territory_key": p.TerritoryKey, "name": fmt.Sprint(row["name"]),
		"controller_type": controllerType, "controller_key": controllerKey,
		"previous_controller_type": before["controller_type"], "previous_controller_key": before["controller_key"],
	}
	return out, nil
}

// recordTerritoryHandedTx is the public history row a GM's hand leaves: the
// world is told the banner changed, as a claim tells it (RecordTerritoryClaimedTx),
// and not by whose decree.
func recordTerritoryHandedTx(conn *storage.Conn, catalog worlddata.Catalog, sect, territory, name string, gm int64, now float64) {
	if !tableExistsTx(conn, "world_history_events") {
		return
	}
	world := ""
	if loc, ok := catalog.Locations[territory]; ok {
		world = loc.World
	}
	if name == "" {
		name = territory
	}
	title := sect + " holds " + name
	summary := fmt.Sprintf("The banners of %s fly over %s; the ground answers to the sect from this day.", sect, name)
	source := fmt.Sprintf("sect_handed:%s:%s:%d", sect, territory, gm)
	_, _ = conn.Execute(`INSERT INTO world_history_events(
        source_key,event_type,title,summary,significance,visibility,location,world_name,faction,
        actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,
        related_npc_name,tags,game_minute,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?, 'public', ?,?,?, 'faction',?,?, 'territory',?,?, NULL,'',?,?,?,?,?)
        ON CONFLICT(source_key) DO NOTHING`,
		[]any{source, "territory_claimed", title, summary, 60, territory, world, sect,
			sect, sect, territory, name, "territory claim " + territory, gm, "{}", now, now})
}

// reverseTerritoryController restores the two controller columns the lever
// wrote, from the snapshot the audit row carries; the key rides the snapshot
// because a territory's name carries spaces and the target field cannot.
func reverseTerritoryController(before, after map[string]any, target string, redo bool) ([]sqlStmt, error) {
	snap := pickSnapshot(before, after, redo)
	key := strings.TrimSpace(fmt.Sprint(snap["territory_key"]))
	if key == "" || key == "<nil>" {
		return nil, fmt.Errorf("the audit row carries no territory_key (target %q)", target)
	}
	return []sqlStmt{{`UPDATE territory_state SET controller_type=?,controller_key=? WHERE territory_key=?`,
		[]any{fmt.Sprint(snap["controller_type"]), fmt.Sprint(snap["controller_key"]), key}, nil}}, nil
}

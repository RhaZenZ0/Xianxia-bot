package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// A world event used to be an announcement with nothing inside it: the action
// menu rolled dice that moved contribution integers, and the only reward in
// the entire scene was one first-participation claim. This file is what is
// actually there - the beasts, the herb and ore nodes, the relics and the
// tasks - spawned from the category's site template in content/world.json,
// finite, and depleting as players work them.

// eventSiteCount scales a template's [min,max] pair by event severity, so a
// severity-10 beast tide fields the full roster and a severity-1 disturbance
// fields the floor of it.
func eventSiteCount(count []int64, severity int64) int64 {
	if len(count) == 0 {
		return 1
	}
	lo := count[0]
	hi := lo
	if len(count) > 1 {
		hi = count[1]
	}
	if hi < lo {
		lo, hi = hi, lo
	}
	if lo < 1 {
		lo = 1
	}
	severity = clampI64(severity, 1, 10)
	return lo + ((hi-lo)*(severity-1)+4)/9
}

// SpawnWorldEventNodes fills a freshly activated world event with its site.
// It is exported because the native simulation batch spawns autonomous events
// from its own package; every spawn path routes through here so no event can
// reach a player empty. Spawning is idempotent per event key.
func SpawnWorldEventNodes(conn *storage.Conn, catalog worlddata.Catalog, eventKey, category, location string, severity int64, now float64) (int64, error) {
	eventKey = strings.TrimSpace(eventKey)
	if eventKey == "" {
		return 0, errors.New("event_key is required to spawn a world-event site")
	}
	if !tableExistsTx(conn, "world_event_nodes") {
		return 0, nil
	}
	existing, err := conn.Execute(`SELECT 1 FROM world_event_nodes WHERE event_key=? LIMIT 1`, []any{eventKey})
	if err != nil {
		return 0, err
	}
	if len(existing.Rows) > 0 {
		return 0, nil
	}
	world := ""
	if loc, ok := catalog.Locations[location]; ok {
		world = loc.World
	}
	template := catalog.EventSites.Template(category)
	tierRank := catalog.EventSites.TierRank[world]
	if tierRank < 1 {
		tierRank = 1
	}
	// A template that marks a node or a cast member as the sect's (v1.1.0 -
	// the Sect Recruitment delegation) speaks for a real sect of this world,
	// and the rows are stamped with it here so a content edit mid-event cannot
	// change whom a running delegation speaks for. The columns are migration
	// 61's; in the boot window before it has run the event stays generic
	// rather than failing the tick that spawned it.
	recruiting := ""
	if templateNamesASect(template) {
		recruiting = recruitingSectFor(catalog, eventKey, location)
	}
	nodeSects, err := tableHasColumns(conn, "world_event_nodes", "reveals_sect")
	if err != nil {
		return 0, err
	}
	spawned := int64(0)
	for _, n := range template.Nodes {
		key := strings.TrimSpace(n.Key)
		if key == "" {
			continue
		}
		total := eventSiteCount(n.Count, severity)
		itemID := catalog.EventSites.Material(world, n.Item)
		qty := n.ItemQty
		if itemID == "" {
			qty = 0
		}
		if _, ok := catalog.Items[itemID]; itemID != "" && !ok {
			// A tier material the item catalogue does not carry would grant a
			// phantom item; drop the payout rather than the node.
			itemID, qty = "", 0
		}
		rank := tierRank + n.RankBonus
		if rank < 1 {
			rank = 1
		}
		reveals := ""
		if n.RevealsSect {
			reveals = recruiting
		}
		if nodeSects {
			if _, err := conn.Execute(`INSERT INTO world_event_nodes(
            event_key,node_key,node_type,name,descriptor,rank,total,remaining,cleared_by,tn,attribute,
            item_id,item_qty,cultivation,spirit_stones,contribution,reveals_sect,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key,node_key) DO NOTHING`,
				[]any{eventKey, key, n.Type, n.Name, n.Descriptor, rank, total, total, n.TN, n.Attribute,
					itemID, qty, n.Cultivation, n.SpiritStones, n.Contribution, reveals, now, now}); err != nil {
				return 0, err
			}
		} else if _, err := conn.Execute(`INSERT INTO world_event_nodes(
            event_key,node_key,node_type,name,descriptor,rank,total,remaining,cleared_by,tn,attribute,
            item_id,item_qty,cultivation,spirit_stones,contribution,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key,node_key) DO NOTHING`,
			[]any{eventKey, key, n.Type, n.Name, n.Descriptor, rank, total, total, n.TN, n.Attribute,
				itemID, qty, n.Cultivation, n.SpiritStones, n.Contribution, now, now}); err != nil {
			return 0, err
		}
		spawned++
	}
	cast, err := spawnWorldEventCastTx(conn, catalog, eventKey, location, template, recruiting, now)
	if err != nil {
		return 0, err
	}
	return spawned + cast, nil
}

// spawnWorldEventCastTx gives the event the people it needs. Names come from
// the shared pool, offset by a hash of the event and the role so two concurrent
// events of the same category field different officers, and walked forward
// until one is free - the name index is unique across live events, so a cast
// member is always an unambiguous person to address.
func spawnWorldEventCastTx(conn *storage.Conn, catalog worlddata.Catalog, eventKey, location string, template worlddata.EventSiteTemplate, recruiting string, now float64) (int64, error) {
	if !tableExistsTx(conn, "world_event_npcs") || len(template.NPCs) == 0 {
		return 0, nil
	}
	castSects, err := tableHasColumns(conn, "world_event_npcs", "sect_name", "can_recommend")
	if err != nil {
		return 0, err
	}
	pool := catalog.EventSites.NamePool
	spawned := int64(0)
	for _, person := range template.NPCs {
		key := strings.TrimSpace(person.Key)
		if key == "" {
			continue
		}
		title := strings.TrimSpace(person.Title)
		if title == "" {
			title = "Bystander"
		}
		name := title
		if len(pool) > 0 {
			start := int(hashString(eventKey+":"+key) % uint64(len(pool)))
			for i := 0; i < len(pool); i++ {
				candidate := title + " " + pool[(start+i)%len(pool)]
				taken, err := conn.Execute(`SELECT 1 FROM world_event_npcs WHERE name=? LIMIT 1`, []any{candidate})
				if err != nil {
					return 0, err
				}
				if len(taken.Rows) == 0 {
					name = candidate
					break
				}
			}
		}
		var res storage.Result
		if castSects {
			sect, sponsor := "", int64(0)
			if recruiting != "" && (person.SectMember || person.CanRecommend) {
				sect = recruiting
				if person.CanRecommend {
					sponsor = 1
				}
			}
			res, err = conn.Execute(`INSERT INTO world_event_npcs(
            event_key,npc_key,name,title,role,personality,speech,want,fear,descriptor,location,sect_name,can_recommend,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key,npc_key) DO NOTHING`,
				[]any{eventKey, key, name, title, person.Role, person.Personality, person.Speech,
					person.Want, person.Fear, person.Descriptor, location, sect, sponsor, now})
		} else {
			res, err = conn.Execute(`INSERT INTO world_event_npcs(
            event_key,npc_key,name,title,role,personality,speech,want,fear,descriptor,location,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key,npc_key) DO NOTHING`,
				[]any{eventKey, key, name, title, person.Role, person.Personality, person.Speech,
					person.Want, person.Fear, person.Descriptor, location, now})
		}
		if err != nil {
			return 0, err
		}
		spawned += res.RowsAffected
	}
	return spawned, nil
}

// templateNamesASect reports whether an event template marks anything as the
// sect's - a node whose clearing reveals it, or a cast member who belongs to it.
func templateNamesASect(template worlddata.EventSiteTemplate) bool {
	for _, n := range template.Nodes {
		if n.RevealsSect {
			return true
		}
	}
	for _, person := range template.NPCs {
		if person.SectMember || person.CanRecommend {
			return true
		}
	}
	return false
}

func hashString(s string) uint64 {
	h := uint64(1469598103934665603)
	for i := 0; i < len(s); i++ {
		h ^= uint64(s[i])
		h *= 1099511628211
	}
	return h
}

type worldEventSiteNode struct {
	NodeID       int64
	NodeKey      string
	NodeType     string
	Name         string
	Descriptor   string
	Rank         int64
	Total        int64
	Remaining    int64
	TN           int64
	Attribute    string
	ItemID       string
	ItemQty      int64
	Cultivation  int64
	SpiritStones int64
	Contribution int64
	// RevealsSect is the sect this node shows the way to when cleared, stamped
	// at spawn (v1.1.0); "" for every node that reveals nothing.
	RevealsSect string
}

func loadWorldEventNodeTx(conn *storage.Conn, eventKey, nodeKey string) (worldEventSiteNode, error) {
	var n worldEventSiteNode
	sects, err := tableHasColumns(conn, "world_event_nodes", "reveals_sect")
	if err != nil {
		return n, err
	}
	revealsColumn := "''"
	if sects {
		revealsColumn = "reveals_sect"
	}
	res, err := conn.Execute(`SELECT node_id,node_key,node_type,name,descriptor,rank,total,remaining,tn,attribute,
        item_id,item_qty,cultivation,spirit_stones,contribution,`+revealsColumn+` FROM world_event_nodes WHERE event_key=? AND node_key=? LIMIT 1`,
		[]any{eventKey, nodeKey})
	if err != nil {
		return n, err
	}
	if len(res.Rows) == 0 {
		return n, errors.New("that is not part of this event")
	}
	r := res.Rows[0]
	n = worldEventSiteNode{
		NodeID: storage.ParseInt(r[0]), NodeKey: fmt.Sprint(r[1]), NodeType: fmt.Sprint(r[2]),
		Name: fmt.Sprint(r[3]), Descriptor: fmt.Sprint(r[4]), Rank: storage.ParseInt(r[5]),
		Total: storage.ParseInt(r[6]), Remaining: storage.ParseInt(r[7]), TN: storage.ParseInt(r[8]),
		Attribute: fmt.Sprint(r[9]), ItemID: fmt.Sprint(r[10]), ItemQty: storage.ParseInt(r[11]),
		Cultivation: storage.ParseInt(r[12]), SpiritStones: storage.ParseInt(r[13]), Contribution: storage.ParseInt(r[14]),
		RevealsSect: strings.TrimSpace(fmt.Sprint(r[15])),
	}
	if n.RevealsSect == "<nil>" {
		n.RevealsSect = ""
	}
	return n, nil
}

// depleteWorldEventNodeTx takes one unit off a node and credits the clearer.
// It is guarded on remaining>0 in the UPDATE itself, so two players racing the
// last beast cannot both take it.
func depleteWorldEventNodeTx(conn *storage.Conn, eventKey, nodeKey string, now float64) (bool, int64, error) {
	res, err := conn.Execute(`UPDATE world_event_nodes SET remaining=remaining-1,cleared_by=cleared_by+1,updated_at=?
        WHERE event_key=? AND node_key=? AND remaining>0`, []any{now, eventKey, nodeKey})
	if err != nil {
		return false, 0, err
	}
	if res.RowsAffected == 0 {
		return false, 0, nil
	}
	left, err := conn.Execute(`SELECT remaining FROM world_event_nodes WHERE event_key=? AND node_key=?`, []any{eventKey, nodeKey})
	if err != nil {
		return true, 0, err
	}
	remaining := int64(0)
	if len(left.Rows) > 0 {
		remaining = storage.ParseInt(left.Rows[0][0])
	}
	return true, remaining, nil
}

// worldEventSiteProgressTx reports how much of the site is left, which is what
// makes an event readable as a shared objective rather than a list of rolls.
func worldEventSiteProgressTx(conn *storage.Conn, eventKey string) (map[string]any, error) {
	res, err := conn.Execute(`SELECT COUNT(*),COALESCE(SUM(total),0),COALESCE(SUM(remaining),0),
        COALESCE(SUM(CASE WHEN node_type='beast' THEN remaining ELSE 0 END),0)
        FROM world_event_nodes WHERE event_key=?`, []any{eventKey})
	if err != nil || len(res.Rows) == 0 {
		return map[string]any{}, err
	}
	r := res.Rows[0]
	total, remaining := storage.ParseInt(r[1]), storage.ParseInt(r[2])
	cleared := total - remaining
	percent := int64(0)
	if total > 0 {
		percent = cleared * 100 / total
	}
	return map[string]any{
		"nodes": storage.ParseInt(r[0]), "total": total, "remaining": remaining,
		"cleared": cleared, "percent": percent, "beasts_remaining": storage.ParseInt(r[3]),
		"resolved": total > 0 && remaining == 0,
	}, nil
}

type worldEventEngagePayload struct {
	EventKey   string `json:"event_key"`
	NodeKey    string `json:"node_key"`
	GameMinute int64  `json:"game_minute"`
}

// worldEventEngageAction resolves one attempt against one node of the site:
// a skill check that, on success, takes a unit off the node and pays the real
// item, cultivation and spirit stones the template attached to it. Unlike the
// abstract action menu it can be worked repeatedly for as long as the node
// holds out, which is what makes an event scene something to play rather than
// something to click once.
func worldEventEngageAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p worldEventEngagePayload
	if err := json.Unmarshal(raw, &p); err != nil {
		return authoritativeMutation{}, err
	}
	eventKey := strings.TrimSpace(p.EventKey)
	nodeKey := strings.TrimSpace(p.NodeKey)
	if eventKey == "" || nodeKey == "" {
		return authoritativeMutation{}, errors.New("event_key and node_key are required")
	}
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only a living character can act in a world event")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	er, err := conn.Execute(`SELECT title,location,payload_json,active,ends_at FROM world_events WHERE event_key=? LIMIT 1`, []any{eventKey})
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(er.Rows) == 0 {
		return authoritativeMutation{}, errors.New("world event not found")
	}
	row := er.Rows[0]
	title, location := fmt.Sprint(row[0]), fmt.Sprint(row[1])
	if storage.ParseInt(row[3]) != 1 || parseFloat(row[4]) <= now {
		return authoritativeMutation{}, errors.New("world event is closed")
	}
	if c.Location != location {
		return authoritativeMutation{}, fmt.Errorf("travel to %s before acting in this event", location)
	}
	payload := map[string]any{}
	_ = json.Unmarshal([]byte(fmt.Sprint(row[2])), &payload)
	severity := clampI64(storage.ParseInt(payload["severity"]), 1, 10)

	node, err := loadWorldEventNodeTx(conn, eventKey, nodeKey)
	if err != nil {
		return authoritativeMutation{}, err
	}
	out := map[string]any{
		"event_key": eventKey, "title": title, "node_key": node.NodeKey, "node_type": node.NodeType,
		"name": node.Name, "descriptor": node.Descriptor, "rank": node.Rank, "severity": severity,
	}
	if node.Remaining <= 0 {
		return authoritativeMutation{}, errors.New(node.Name + " is already cleared out")
	}

	bonus, err := canonicalAttribute(conn, catalog, userID, p.GameMinute, node.Attribute)
	if err != nil {
		return authoritativeMutation{}, err
	}
	tn := node.TN + maxI64(0, severity-2)/2
	roll, err := roll2d10(bonus, tn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	success := boolResult(roll)
	out["roll"] = roll
	out["tn"] = tn
	out["success"] = success

	contribution := int64(0)
	took := false
	remaining := node.Remaining
	if success {
		took, remaining, err = depleteWorldEventNodeTx(conn, eventKey, nodeKey, now)
		if err != nil {
			return authoritativeMutation{}, err
		}
		if !took {
			// Somebody else took the last one between the load and the write.
			return authoritativeMutation{}, errors.New(node.Name + " was cleared out a moment before you reached it")
		}
		contribution = node.Contribution
		reward := canonicalReward{Cultivation: node.Cultivation, SpiritStones: node.SpiritStones, Items: map[string]int64{}}
		if node.ItemID != "" && node.ItemQty > 0 {
			reward.Items[node.ItemID] = node.ItemQty
		}
		if reward.Cultivation != 0 || reward.SpiritStones != 0 || len(reward.Items) > 0 {
			awarded, _, e := applyCanonicalRewardTx(conn, catalog, userID, c, reward, "world_event_site_"+node.NodeType, now)
			if e != nil {
				return authoritativeMutation{}, e
			}
			out["cultivation_awarded"] = awarded
			out["spirit_stones"] = reward.SpiritStones
			out["items"] = reward.Items
		}
		// The delegation's trial, passed (v1.1.0): the sect it speaks for is
		// known now and its gate is on the cultivator's travel list. The trial
		// that makes somebody a disciple is still sat at that gate.
		if node.RevealsSect != "" {
			shown, e := revealSectRouteTx(conn, catalog, userID, node.RevealsSect, "event_trial", "event:"+eventKey, p.GameMinute, now)
			if e != nil {
				return authoritativeMutation{}, e
			}
			if shown != nil {
				out["sect_revealed"] = shown
			}
		}
	}
	out["remaining"] = remaining
	out["total"] = node.Total

	verb := map[string]string{"beast": "drove off", "herb": "harvested", "ore": "cut loose", "relic": "recovered", "task": "carried out"}[node.NodeType]
	if verb == "" {
		verb = "worked"
	}
	detail := fmt.Sprintf("%s %s (%s) at %s.", map[bool]string{true: "Successfully " + verb, false: "Failed against"}[success], node.Name, node.NodeType, title)
	state, err := recordWorldEventActionTx(conn, eventKey, userID, "engage:"+node.NodeKey, node.NodeType, node.Name, node.Attribute,
		storage.ParseInt(roll["total"]), tn, success, contribution, 0, 0, 0, success && node.NodeType == "beast", detail, p.GameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	out["state"] = state

	progress, err := worldEventSiteProgressTx(conn, eventKey)
	if err != nil {
		return authoritativeMutation{}, err
	}
	out["site"] = progress

	return authoritativeMutation{Result: out, Event: eventledger.Event{
		Domain: "world_event", EventType: "site_engage", EntityType: "world_event",
		EntityID: eventKey, GameMinute: p.GameMinute, Payload: out,
	}}, nil
}

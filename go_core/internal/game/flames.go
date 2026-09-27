package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// Flames (v1.10.0, on the owner's calls). A cultivator captures a flame at
// its world's forge terraces, refines it with beast cores and ore, and binds
// one to steady an Alchemy or Forging roll. Three decisions shape it:
//
//   - **Alchemy and Forging only**, the two trades that work with fire. The
//     roster's `trades` says so and nothing else is asked.
//   - **Permanent.** A flame is never used up and never wears down: it is a
//     row that only refining changes. Capturing costs qi and risks the burn;
//     refining costs materials; neither spends the flame.
//   - **A flame is what opens Transcendent.** That rung asks rank 7 and a
//     trade stops rising at rank 6 (crafting_actions.go), so until now it was
//     unreachable. A fully refined flame whose definition says it opens the
//     top grade lets a crafter at the rung's `flame_min_rank` (6) make it -
//     the margin it asks for is still the roll's to reach.
//
// The capture roll is the purge scorch's shape (alchemy_actions.go): the
// same `fire_resistance` term, and a failure is `meridian_damage`, severity
// 2 on a bad miss. A Heavenly Flame Root adds `flame_affinity`.

const (
	flameDefaultMaxRefinement = int64(9)
	flameResistScale          = int64(5)
)

func flameRules(catalog worlddata.Catalog) worlddata.FlameSystem {
	rules := catalog.FlameSystem
	if rules.MaxRefinement <= 0 {
		rules.MaxRefinement = flameDefaultMaxRefinement
	}
	return rules
}

// flameServesTrade is whether a bound flame steadies this trade's roll.
func flameServesTrade(rules worlddata.FlameSystem, profession string) bool {
	for _, trade := range rules.Trades {
		if strings.EqualFold(trade, strings.TrimSpace(profession)) {
			return true
		}
	}
	return false
}

// flameBonusAt is what a flame adds to a craft at a refinement: its base,
// rising evenly to its maximum at full refinement.
func flameBonusAt(def worlddata.FlameDefinition, refinement, maxRefinement int64) int64 {
	if maxRefinement <= 0 {
		return def.BaseBonus
	}
	refinement = clamp(refinement, 0, maxRefinement)
	return def.BaseBonus + refinement*(def.MaxBonus-def.BaseBonus)/maxRefinement
}

// flameOpensTopGrade is whether this flame, at this refinement, opens the
// rung that carries a `flame_min_rank`.
func flameOpensTopGrade(def worlddata.FlameDefinition, refinement, maxRefinement int64) bool {
	return def.OpensTopGrade && refinement >= maxRefinement
}

// flameRefineCost is what the next refinement asks: each listed item times
// the level being reached, and the qi. The qi is a content number - a share of
// the reference pool (`qiShare`), which the engine caps at half a dantian - so
// it stays flat: multiplying it by the step would price every late refinement
// at that cap, which is what the engine playtest found the first version do.
func flameRefineCost(def worlddata.FlameDefinition, refinement int64) (map[string]int64, int64) {
	step := refinement + 1
	items := map[string]int64{}
	for id, qty := range def.RefineItems {
		items[id] = qty * step
	}
	return items, def.RefineQi
}

func sortedFlameIDs(rules worlddata.FlameSystem) []string {
	ids := make([]string, 0, len(rules.Flames))
	for id := range rules.Flames {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

type heldFlame struct {
	ID         string
	Refinement int64
	Bound      bool
}

// heldFlamesTx reads a cultivator's flames. A table that is not there yet -
// the engine can be healthy before db-init migrates (v1.1.0) - is no flames,
// never an error, because a craft reads this and must not refuse over it.
func heldFlamesTx(conn *storage.Conn, userID int64) map[string]heldFlame {
	out := map[string]heldFlame{}
	res, err := conn.Execute(`SELECT flame_id,refinement,bound FROM character_flames WHERE user_id=?`, []any{userID})
	if err != nil {
		return out
	}
	for _, row := range res.Rows {
		id := fmt.Sprint(row[0])
		out[id] = heldFlame{ID: id, Refinement: storage.ParseInt(row[1]), Bound: storage.ParseInt(row[2]) != 0}
	}
	return out
}

// craftFlameTx is what the bound flame gives this craft: its bonus, whether it
// opens the top grade, and its name - nothing for a trade flames do not serve.
func craftFlameTx(conn *storage.Conn, catalog worlddata.Catalog, userID int64, profession string) (int64, bool, string) {
	rules := flameRules(catalog)
	if !flameServesTrade(rules, profession) {
		return 0, false, ""
	}
	for _, held := range heldFlamesTx(conn, userID) {
		if !held.Bound {
			continue
		}
		def, ok := rules.Flames[held.ID]
		if !ok {
			return 0, false, ""
		}
		return flameBonusAt(def, held.Refinement, rules.MaxRefinement), flameOpensTopGrade(def, held.Refinement, rules.MaxRefinement), def.Name
	}
	return 0, false, ""
}

type flamePayload struct {
	FlameID    string `json:"flame_id"`
	GameMinute int64  `json:"game_minute"`
}

func decodeFlamePayload(raw json.RawMessage) (flamePayload, error) {
	var p flamePayload
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &p); err != nil {
			return p, err
		}
	}
	p.FlameID = strings.TrimSpace(p.FlameID)
	return p, nil
}

// flameAt is the flame whose source is this place, if any.
func flameAt(rules worlddata.FlameSystem, location string) (string, worlddata.FlameDefinition, bool) {
	for _, id := range sortedFlameIDs(rules) {
		def := rules.Flames[id]
		if def.Location == location {
			return id, def, true
		}
	}
	return "", worlddata.FlameDefinition{}, false
}

// flameCaptureAction tries to take the flame that burns where the cultivator
// stands. A success writes the row and binds it when nothing else is bound; a
// failure burns the meridians. Qi is spent either way.
func flameCaptureAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	p, err := decodeFlamePayload(raw)
	if err != nil {
		return authoritativeMutation{}, err
	}
	rules := flameRules(catalog)
	c, err := loadMechanicsCharacter(conn, userID)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if c.LifeStatus != "alive" {
		return authoritativeMutation{}, errors.New("only a living character can capture a flame")
	}
	flameID, def, ok := flameAt(rules, strings.TrimSpace(c.Location))
	if !ok {
		return authoritativeMutation{}, errors.New("no flame burns here to be captured; flames are taken at a world's forge terraces")
	}
	if p.FlameID != "" && p.FlameID != flameID {
		return authoritativeMutation{}, fmt.Errorf("the flame that burns here is the %s", def.Name)
	}
	if c.RealmIndex < def.MinRealmIndex {
		return authoritativeMutation{}, fmt.Errorf("the %s cannot be held below realm %d", def.Name, def.MinRealmIndex)
	}
	held := heldFlamesTx(conn, userID)
	if _, already := held[flameID]; already {
		return authoritativeMutation{}, fmt.Errorf("you already hold the %s; refine it instead", def.Name)
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	state, err := settleQi(conn, catalog, userID, gameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	qiCost := state.Cost(def.CaptureQi)
	if state.Qi < qiCost {
		return authoritativeMutation{}, fmt.Errorf("capturing the %s takes %d qi and you hold %d", def.Name, qiCost, state.Qi)
	}
	if _, err = conn.Execute(`UPDATE characters SET qi=qi-?,updated_at=? WHERE user_id=?`, []any{qiCost, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	spirit, err := canonicalAttribute(conn, catalog, userID, gameMinute, "spirit")
	if err != nil {
		return authoritativeMutation{}, err
	}
	will, err := canonicalAttribute(conn, catalog, userID, gameMinute, "will")
	if err != nil {
		return authoritativeMutation{}, err
	}
	fireResistance, err := canonicalAdditiveEffectBonus(conn, catalog, userID, c.Location, gameMinute, "fire_resistance")
	if err != nil {
		return authoritativeMutation{}, err
	}
	affinity, err := canonicalAdditiveEffectBonus(conn, catalog, userID, c.Location, gameMinute, "flame_affinity")
	if err != nil {
		return authoritativeMutation{}, err
	}
	modifier := (spirit+will)/2 + fireResistance/flameResistScale + affinity
	roll, err := rollCheck(modifier, def.CaptureTN)
	if err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"flame_id": flameID, "name": def.Name, "qi_cost": qiCost, "roll": roll,
		"success": roll["success"], "flame_affinity": affinity, "fire_resistance": fireResistance}
	if roll["success"] == true {
		bound := int64(1)
		for _, other := range held {
			if other.Bound {
				bound = 0
			}
		}
		if _, err = conn.Execute(`INSERT INTO character_flames(user_id,flame_id,refinement,bound,captured_game_minute,updated_at) VALUES(?,?,0,?,?,?)`,
			[]any{userID, flameID, bound, gameMinute, now}); err != nil {
			return authoritativeMutation{}, err
		}
		result["bound"] = bound == 1
		result["bonus"] = flameBonusAt(def, 0, rules.MaxRefinement)
	} else {
		severity := int64(1)
		if i64(roll["margin"]) <= rules.CaptureScorchSevereMargin {
			severity = 2
		}
		scorched, cerr := applyCombatCondition(conn, userID, "meridian_damage", severity, "flame", flameID, gameMinute)
		if cerr != nil {
			return authoritativeMutation{}, cerr
		}
		result["scorched"] = scorched
	}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "flame", EventType: "flame.capture", EntityType: "flame", EntityID: flameID, GameMinute: gameMinute, Payload: result}}, nil
}

// flameRefineAction raises a held flame one refinement, for the materials and
// qi that level asks. It is certain: refining is the price, not a gamble.
func flameRefineAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	p, err := decodeFlamePayload(raw)
	if err != nil {
		return authoritativeMutation{}, err
	}
	rules := flameRules(catalog)
	held, ok := heldFlamesTx(conn, userID)[p.FlameID]
	def, known := rules.Flames[p.FlameID]
	if !ok || !known {
		return authoritativeMutation{}, errors.New("you hold no such flame")
	}
	if held.Refinement >= rules.MaxRefinement {
		return authoritativeMutation{}, fmt.Errorf("the %s is already fully refined", def.Name)
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return authoritativeMutation{}, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	items, qiBase := flameRefineCost(def, held.Refinement)
	state, err := settleQi(conn, catalog, userID, gameMinute, now)
	if err != nil {
		return authoritativeMutation{}, err
	}
	qiCost := state.Cost(qiBase)
	if state.Qi < qiCost {
		return authoritativeMutation{}, fmt.Errorf("refining the %s takes %d qi and you hold %d", def.Name, qiCost, state.Qi)
	}
	missing, err := consumeInventoryTx(conn, userID, items)
	if err != nil {
		return authoritativeMutation{}, err
	}
	if len(missing) > 0 {
		return authoritativeMutation{}, fmt.Errorf("missing materials: %s", describeMaterials(catalog, missing))
	}
	if _, err = conn.Execute(`UPDATE characters SET qi=qi-?,updated_at=? WHERE user_id=?`, []any{qiCost, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	level := held.Refinement + 1
	if _, err = conn.Execute(`UPDATE character_flames SET refinement=?,updated_at=? WHERE user_id=? AND flame_id=?`, []any{level, now, userID, p.FlameID}); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"flame_id": p.FlameID, "name": def.Name, "refinement": level, "max_refinement": rules.MaxRefinement,
		"bonus": flameBonusAt(def, level, rules.MaxRefinement), "opens_top_grade": flameOpensTopGrade(def, level, rules.MaxRefinement),
		"spent_items": items, "qi_cost": qiCost}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "flame", EventType: "flame.refine", EntityType: "flame", EntityID: p.FlameID, GameMinute: gameMinute, Payload: result}}, nil
}

// flameBindAction makes one held flame the one a craft reads.
func flameBindAction(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	p, err := decodeFlamePayload(raw)
	if err != nil {
		return authoritativeMutation{}, err
	}
	rules := flameRules(catalog)
	held, ok := heldFlamesTx(conn, userID)[p.FlameID]
	def, known := rules.Flames[p.FlameID]
	if !ok || !known {
		return authoritativeMutation{}, errors.New("you hold no such flame")
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`UPDATE character_flames SET bound=CASE WHEN flame_id=? THEN 1 ELSE 0 END,updated_at=? WHERE user_id=?`, []any{p.FlameID, now, userID}); err != nil {
		return authoritativeMutation{}, err
	}
	result := map[string]any{"flame_id": p.FlameID, "name": def.Name, "refinement": held.Refinement,
		"bonus": flameBonusAt(def, held.Refinement, rules.MaxRefinement)}
	return authoritativeMutation{Result: result, Event: eventledger.Event{Domain: "flame", EventType: "flame.bind", EntityType: "flame", EntityID: p.FlameID, Payload: result}}, nil
}

// flameStatusQuery is the whole roster as this cultivator stands to it: what
// they hold, at what refinement, what it gives, what the next level costs, and
// where the ones they do not hold are taken. It never refuses.
func flameStatusQuery(conn *storage.Conn, catalog worlddata.Catalog, userID int64) (map[string]any, error) {
	rules := flameRules(catalog)
	held := heldFlamesTx(conn, userID)
	flames := []map[string]any{}
	for _, id := range sortedFlameIDs(rules) {
		def := rules.Flames[id]
		entry := map[string]any{
			"flame_id": id, "name": def.Name, "description": def.Description, "world": def.World,
			"location": def.Location, "min_realm_index": def.MinRealmIndex, "capture_tn": def.CaptureTN,
			"capture_qi": def.CaptureQi, "base_bonus": def.BaseBonus, "max_bonus": def.MaxBonus,
			"opens_top_grade": def.OpensTopGrade, "held": false,
		}
		if h, ok := held[id]; ok {
			entry["held"] = true
			entry["bound"] = h.Bound
			entry["refinement"] = h.Refinement
			entry["bonus"] = flameBonusAt(def, h.Refinement, rules.MaxRefinement)
			entry["opens_now"] = flameOpensTopGrade(def, h.Refinement, rules.MaxRefinement)
			if h.Refinement < rules.MaxRefinement {
				items, qi := flameRefineCost(def, h.Refinement)
				entry["next_refine_items"] = items
				entry["next_refine_qi"] = qi
			}
		}
		flames = append(flames, entry)
	}
	return map[string]any{"description": rules.Description, "trades": rules.Trades, "max_refinement": rules.MaxRefinement, "flames": flames}, nil
}

// adminGrantFlame gives a cultivator a flame at a refinement, or sets the
// refinement of one they hold (v1.10.0). It is the GM's lever for a flame a
// fault cost somebody, and the one way a harness reaches refine and bind
// without winning a capture roll. The flame is held to the roster, the
// refinement to its range, and the change is audited with what was there.
func adminGrantFlame(conn *storage.Conn, catalog worlddata.Catalog, adminUserID int64, raw json.RawMessage) (any, error) {
	p, err := decodeMap(raw)
	if err != nil {
		return nil, err
	}
	uid, err := requiredInt(p, "user_id")
	if err != nil {
		return nil, err
	}
	flameID := stringField(p, "flame_id")
	rules := flameRules(catalog)
	def, ok := rules.Flames[flameID]
	if !ok {
		return nil, fmt.Errorf("flame_id must be one of %s", strings.Join(sortedFlameIDs(rules), ", "))
	}
	refinement := storage.ParseInt(p["refinement"])
	if refinement < 0 || refinement > rules.MaxRefinement {
		return nil, fmt.Errorf("refinement must be 0 to %d", rules.MaxRefinement)
	}
	if err := begin(conn); err != nil {
		return nil, err
	}
	defer func() {
		if conn.InTransaction() {
			rollback(conn)
		}
	}()
	charRes, err := conn.Execute(`SELECT name FROM characters WHERE user_id=?`, []any{uid})
	if err != nil {
		return nil, err
	}
	charRow := firstRowMap(charRes)
	if charRow == nil {
		return nil, errors.New("character not found")
	}
	held := heldFlamesTx(conn, uid)
	before := map[string]any{"held": false}
	bound := int64(1)
	for id, h := range held {
		if id == flameID {
			before = map[string]any{"held": true, "refinement": h.Refinement, "bound": h.Bound}
		}
		if h.Bound {
			bound = 0
		}
	}
	gameMinute, err := canonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	if _, err = conn.Execute(`INSERT INTO character_flames(user_id,flame_id,refinement,bound,captured_game_minute,updated_at) VALUES(?,?,?,?,?,?)
		ON CONFLICT(user_id,flame_id) DO UPDATE SET refinement=excluded.refinement,updated_at=excluded.updated_at`,
		[]any{uid, flameID, refinement, bound, gameMinute, now}); err != nil {
		return nil, err
	}
	if err := auditAdmin(conn, adminUserID, "admin.player.grant_flame", fmt.Sprintf("user:%d", uid), before,
		map[string]any{"flame_id": flameID, "refinement": refinement}, fmt.Sprint(p["reason"])); err != nil {
		return nil, err
	}
	if err := conn.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"user_id": uid, "name": charRow["name"], "flame_id": flameID, "flame": def.Name, "refinement": refinement,
		"bonus": flameBonusAt(def, refinement, rules.MaxRefinement)}, nil
}

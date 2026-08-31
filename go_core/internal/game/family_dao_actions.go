package game

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"

	"xianxia/core/internal/eventledger"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type familySimPayload struct {
	FamilyID       int64 `json:"family_id"`
	GameMinute     int64 `json:"game_minute"`
	MinutesPerYear int64 `json:"minutes_per_year"`
}
type familySupportPayload struct {
	GameMinute          int64 `json:"game_minute"`
	CooldownGameMinutes int64 `json:"cooldown_game_minutes"`
}
type familyChildPayload struct {
	Name       string `json:"name"`
	Gender     string `json:"gender"`
	GameMinute int64  `json:"game_minute"`
}
type seclusionStartPayload struct {
	Mode                string  `json:"mode"`
	GameMinute          int64   `json:"game_minute"`
	DurationGameMinutes int64   `json:"duration_game_minutes"`
	Location            string  `json:"location"`
	EnvironmentMult     float64 `json:"environment_mult"`
}
type seclusionSettlePayload struct {
	GameMinute    int64  `json:"game_minute"`
	MinutesPerDay int64  `json:"minutes_per_day"`
	ForceEnd      bool   `json:"force_end"`
	EndReason     string `json:"end_reason"`
}
type daoProposePayload struct {
	PartnerUserID int64 `json:"partner_user_id"`
}
type daoRespondPayload struct {
	PartnershipID int64 `json:"partnership_id"`
	Accept        bool  `json:"accept"`
}
type daoDualPayload struct {
	GameMinute      int64 `json:"game_minute"`
	CooldownSeconds int64 `json:"cooldown_seconds"`
}
type fateAdjustPayload struct {
	Delta      int64  `json:"delta"`
	Reason     string `json:"reason"`
	GameMinute int64  `json:"game_minute"`
}

func birthFamilyForUserGo(conn *storage.Conn, userID int64) (map[string]any, error) {
	r, e := conn.Execute(`SELECT bf.*,cbf.birth_order,cbf.generation AS character_generation,cbf.last_support_game_minute FROM character_birth_family cbf JOIN birth_families bf ON bf.family_id=cbf.family_id WHERE cbf.user_id=?`, []any{userID})
	if e != nil {
		return nil, e
	}
	return firstRowMap(r), nil
}
func simulateBirthFamilyGo(conn *storage.Conn, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p familySimPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	if p.MinutesPerYear <= 0 {
		p.MinutesPerYear = 525600
	}
	familyID := p.FamilyID
	if familyID <= 0 {
		f, e := birthFamilyForUserGo(conn, userID)
		if e != nil {
			return authoritativeMutation{}, e
		}
		if f == nil {
			return authoritativeMutation{}, errors.New("no birth family is recorded")
		}
		familyID = i64(f["family_id"])
	} else {
		// family_id is a shared-household key - multiple players can belong
		// to the same birth family - but that's not license for any caller
		// to target an arbitrary family_id. Without this check a client
		// could advance (and read the full wealth/influence/stability/
		// bloodline/history of) any family in the game, including ones they
		// have no connection to at all, just by guessing an ID.
		linked, e := conn.Execute(`SELECT 1 FROM character_birth_family WHERE user_id=? AND family_id=?`, []any{userID, familyID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if len(linked.Rows) == 0 {
			return authoritativeMutation{}, errors.New("you are not a member of that birth family")
		}
	}
	r, e := conn.Execute(`SELECT * FROM birth_families WHERE family_id=?`, []any{familyID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	f := firstRowMap(r)
	if f == nil {
		return authoritativeMutation{}, errors.New("birth family not found")
	}
	elapsed := max64(0, p.GameMinute-i64(f["last_simulated_game_minute"]))
	years := min64(25, elapsed/max64(1, p.MinutesPerYear))
	history := []string{}
	_ = json.Unmarshal([]byte(fmt.Sprint(f["history_json"])), &history)
	wealth := i64(f["wealth"])
	influence := i64(f["influence"])
	stability := i64(f["stability"])
	purity := clamp(i64(f["bloodline_purity"]), 0, 100)
	branches := max64(1, i64(f["branch_count"]))
	retainers := max64(0, i64(f["retainer_count"]))
	events := []struct {
		Text    string
		W, I, S int64
	}{{"A successful trade season enriched the household.", 9, 2, 1}, {"A marriage alliance improved the family's standing.", 2, 8, 6}, {"A talented junior brought prestige to the family.", 1, 9, 2}, {"A crop failure and bad contracts drained family stores.", -9, -1, -5}, {"A feud with a neighboring clan damaged the family's position.", -3, -8, -7}, {"A beast raid damaged property and frightened retainers.", -8, -2, -9}, {"A sect elder took interest in one of the family's juniors.", 0, 11, 3}, {"An internal inheritance dispute split several relatives into factions.", -2, -3, -12}, {"A quiet year allowed the household to recover and consolidate.", 3, 2, 7}, {"A scandal surrounding a senior relative harmed the family name.", -1, -10, -4}}
	for y := int64(0); y < years; y++ {
		idx, e := gamerng.Intn(len(events))
		if e != nil {
			return authoritativeMutation{}, e
		}
		ev := events[idx]
		wealth = clamp(wealth+ev.W, 0, 100)
		influence = clamp(influence+ev.I, 0, 100)
		stability = clamp(stability+ev.S, 0, 100)
		if purity > 0 {
			roll, e := gamerng.Intn(100)
			if e != nil {
				return authoritativeMutation{}, e
			}
			if roll < 8 {
				purity = max64(1, purity-1)
				history = append(history, "The ancestral bloodline thinned slightly across a generation.")
			} else if roll > 94 && stability >= 55 {
				purity = min64(100, purity+1)
				history = append(history, "A gifted marriage and careful lineage rites strengthened the ancestral bloodline.")
			}
			roll, e = gamerng.Intn(100)
			if e != nil {
				return authoritativeMutation{}, e
			}
			if roll < 7 && stability >= 45 {
				branches = min64(99, branches+1)
				history = append(history, "A prosperous cadet branch formally established itself within the clan.")
			}
			roll, e = gamerng.Intn(100)
			if e != nil {
				return authoritativeMutation{}, e
			}
			if roll < 6 && stability < 35 && branches > 1 {
				branches = max64(1, branches-1)
				history = append(history, "A cadet branch broke away after internal conflict.")
			}
			if influence >= 55 {
				r2, e := gamerng.Intn(2)
				if e != nil {
					return authoritativeMutation{}, e
				}
				if r2 == 0 {
					retainers++
				}
			} else if stability < 25 {
				r3, e := gamerng.Intn(3)
				if e != nil {
					return authoritativeMutation{}, e
				}
				if r3 == 0 {
					retainers--
				}
			}
			retainers = clamp(retainers, 0, 5000)
		}
		history = append(history, ev.Text)
	}
	score := wealth + influence + stability
	tier := int64(1)
	if score >= 245 {
		tier = 5
	} else if score >= 185 {
		tier = 4
	} else if score >= 125 {
		tier = 3
	} else if score >= 70 {
		tier = 2
	}
	oldTier := i64(f["tier"])
	if tier > oldTier {
		history = append(history, fmt.Sprintf("The family rose from tier %d to tier %d after years of growth.", oldTier, tier))
	} else if tier < oldTier {
		history = append(history, fmt.Sprintf("The family declined from tier %d to tier %d after accumulated setbacks.", oldTier, tier))
	}
	if len(history) > 50 {
		history = history[len(history)-50:]
	}
	hj, _ := json.Marshal(history)
	anchor := i64(f["last_simulated_game_minute"]) + years*p.MinutesPerYear
	now := nowSeconds()
	if years > 0 {
		_, e = conn.Execute(`UPDATE birth_families SET wealth=?,influence=?,stability=?,tier=?,bloodline_purity=?,branch_count=?,retainer_count=?,last_simulated_game_minute=?,history_json=?,updated_at=? WHERE family_id=?`, []any{wealth, influence, stability, tier, purity, branches, retainers, anchor, string(hj), now, familyID})
		if e != nil {
			return authoritativeMutation{}, e
		}
	}
	out := map[string]any{"family_id": familyID, "years_advanced": years, "wealth": wealth, "influence": influence, "stability": stability, "tier": tier, "bloodline_purity": purity, "branch_count": branches, "retainer_count": retainers, "last_simulated_game_minute": anchor, "history": history}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "family", EventType: "family.simulate", EntityType: "birth_family", EntityID: fmt.Sprint(familyID), GameMinute: p.GameMinute, Payload: out}}, nil
}
func familySupportActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p familySupportPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	if p.CooldownGameMinutes <= 0 {
		p.CooldownGameMinutes = 43200
	}
	f, e := birthFamilyForUserGo(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if f == nil {
		return authoritativeMutation{}, errors.New("no birth family is recorded")
	}
	remain := p.CooldownGameMinutes - max64(0, p.GameMinute-i64(f["last_support_game_minute"]))
	if remain > 0 {
		return authoritativeMutation{}, fmt.Errorf("family support cooldown has %d in-world minutes remaining", remain)
	}
	fid := i64(f["family_id"])
	r, _ := conn.Execute(`SELECT COUNT(*) AS n FROM martial_clan_branches WHERE family_id=? AND status='active' AND loyalty>=40`, []any{fid})
	branches := i64(firstRowMap(r)["n"])
	r, _ = conn.Execute(`SELECT COALESCE(SUM(members),0) AS n FROM martial_clan_retainers WHERE family_id=? AND status='active' AND loyalty>=35`, []any{fid})
	retainers := i64(firstRowMap(r)["n"])
	r, _ = conn.Execute(`SELECT COUNT(*) AS n FROM martial_clan_relations WHERE family_id=? AND active=1 AND relation_type IN ('alliance','marriage_pact','trade_pact') AND relation_score>0`, []any{fid})
	relations := i64(firstRowMap(r)["n"])
	clanBonus := min64(60, branches*2+retainers/8+relations*4)
	arch := fmt.Sprint(f["archetype"])
	tier := max64(1, i64(f["tier"]))
	wealth := max64(0, i64(f["wealth"]))
	influence := max64(0, i64(f["influence"]))
	purity := max64(0, i64(f["bloodline_purity"]))
	stones := int64(0)
	items := map[string]int64{}
	switch arch {
	case "martial_household":
		stones = 6 + tier*4 + purity/20
		items = map[string]int64{"recovery_pill": 1, "spirit_iron": 1}
	case "escort_martial_family":
		stones = 10 + tier*6 + wealth/12
		items = map[string]int64{"recovery_pill": 1}
	case "weaponsmith_martial_family":
		stones = 7 + tier*4 + wealth/15
		items = map[string]int64{"spirit_iron": 2}
	case "body_tempering_family":
		stones = 5 + tier*4
		items = map[string]int64{"recovery_pill": 1}
		if tier >= 3 {
			items["heart_calming_pill"] = 1
		}
	case "sword_hall_family":
		stones = 8 + tier*5 + purity/25
		items = map[string]int64{"spirit_iron": 1}
		if tier >= 3 {
			items["qi_replenishment_pill"] = 1
		}
	case "spear_guard_family":
		stones = 9 + tier*5 + wealth/18
		items = map[string]int64{"recovery_pill": 1}
		if tier >= 3 {
			items["formation_flags"] = 1
		}
	case "hidden_weapon_family":
		stones = 7 + tier*5
		items = map[string]int64{"heart_calming_pill": 1, "spirit_herb": 1}
	case "border_garrison_family":
		stones = 9 + tier*5 + influence/20
		items = map[string]int64{"recovery_pill": 1, "spirit_iron": 1}
		if tier >= 3 {
			items["recovery_pill"] = 2
		}
	case "fallen_martial_clan":
		stones = 5 + tier*4 + purity/25
		items = map[string]int64{"spirit_herb": 1}
		if tier >= 3 {
			items["qi_pill"] = 1
		}
	case "noble_martial_clan":
		stones = 18 + tier*9 + wealth/10 + purity/20
		items = map[string]int64{"qi_pill": 1}
		if tier >= 4 {
			items["qi_replenishment_pill"] = 1
		}
	case "alchemy_family":
		stones = 9 + tier*5 + wealth/16
		items = map[string]int64{"spirit_herb": 3, "recovery_pill": 1}
		if tier >= 3 {
			items["heart_calming_pill"] = 1
		}
	default:
		stones = 6 + tier*4 + wealth/20
		items = map[string]int64{"recovery_pill": 1}
	}
	stones += clanBonus
	if retainers >= 20 && (arch == "martial_household" || arch == "escort_martial_family" || arch == "border_garrison_family" || arch == "noble_martial_clan") {
		items["recovery_pill"]++
	}
	sumItems := int64(0)
	for _, q := range items {
		sumItems += q
	}
	cost := max64(2, stones/8+sumItems*2)
	if wealth <= 0 {
		return authoritativeMutation{}, errors.New("family currently has no spare resources")
	}
	now := nowSeconds()
	_, e = conn.Execute(`UPDATE birth_families SET wealth=MAX(0,wealth-?),updated_at=? WHERE family_id=?`, []any{cost, now, fid})
	if e != nil {
		return authoritativeMutation{}, e
	}
	_, _ = conn.Execute(`UPDATE character_birth_family SET last_support_game_minute=? WHERE user_id=?`, []any{p.GameMinute, userID})
	if stones > 0 {
		if _, e = walletDeltaTx(conn, userID, "low_spirit_stone", stones, now); e != nil {
			return authoritativeMutation{}, e
		}
	}
	for item, q := range items {
		if q > 0 {
			if _, e = conn.Execute(`INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=inventory.quantity+excluded.quantity`, []any{userID, item, q}); e != nil {
				return authoritativeMutation{}, e
			}
		}
	}
	out := map[string]any{"stones": stones, "items": items, "family_name": fmt.Sprint(f["family_name"]), "cost": cost, "clan_support_bonus": clanBonus, "active_branches": branches, "loyal_retainers": retainers, "helpful_relations": relations}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "family", EventType: "family.support", EntityType: "birth_family", EntityID: fmt.Sprint(fid), GameMinute: p.GameMinute, Payload: out}}, nil
}
func inheritedChildRootGo(parentRoot string, roots []string, familyTier, karmaScore, bloodlinePurity int64) (string, error) {
	parentRoot = strings.TrimSpace(parentRoot)
	if parentRoot == "" {
		parentRoot = "Mortal Root"
	}
	echoChance := minI64(88, 40+familyTier*6+minI64(10, absI64(karmaScore)/50)+maxI64(0, bloodlinePurity)/10)
	roll, err := gamerng.Intn(100)
	if err != nil {
		return "", err
	}
	if int64(roll) < echoChance {
		return parentRoot, nil
	}
	if len(roots) == 0 {
		return "Mortal Root", nil
	}
	for attempt := int64(0); attempt < maxI64(1, familyTier); attempt++ {
		idx, err := gamerng.Intn(len(roots))
		if err != nil {
			return "", err
		}
		pick := roots[idx]
		if pick != "Mortal Root" {
			return pick, nil
		}
		ordinaryRoll, err := gamerng.Intn(100)
		if err != nil {
			return "", err
		}
		if ordinaryRoll < 45 {
			return pick, nil
		}
	}
	return "Mortal Root", nil
}

func familyAddChildActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var supplied map[string]json.RawMessage
	if e := json.Unmarshal(raw, &supplied); e != nil {
		return authoritativeMutation{}, e
	}
	for _, forbidden := range []string{"spiritual_root", "realm_index", "phase"} {
		if _, ok := supplied[forbidden]; ok {
			return authoritativeMutation{}, fmt.Errorf("client-supplied %s is forbidden", forbidden)
		}
	}
	var p familyChildPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	f, e := birthFamilyForUserGo(conn, userID)
	if e != nil {
		return authoritativeMutation{}, e
	}
	if f == nil {
		return authoritativeMutation{}, errors.New("no birth family is recorded")
	}
	name := strings.TrimSpace(p.Name)
	if len([]rune(name)) < 1 || len([]rune(name)) > 40 {
		return authoritativeMutation{}, errors.New("child name must be 1-40 characters")
	}
	characterResult, e := conn.Execute(`SELECT spiritual_root,karma_score FROM characters WHERE user_id=? AND life_status='alive'`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	character := firstRowMap(characterResult)
	if character == nil {
		return authoritativeMutation{}, errors.New("living character not found")
	}
	root, e := inheritedChildRootGo(fmt.Sprint(character["spiritual_root"]), catalog.Roots, i64(f["tier"]), i64(character["karma_score"]), i64(f["bloodline_purity"]))
	if e != nil {
		return authoritativeMutation{}, e
	}
	lifeRoll, e := gamerng.Intn(11)
	if e != nil {
		return authoritativeMutation{}, e
	}
	now := nowSeconds()
	c, e := conn.Execute(`INSERT INTO birth_family_npcs(family_id,name,relation,gender,age_at_creation,birth_game_minute,natural_lifespan_years,status,spiritual_root,realm_index,phase,personality,created_at) VALUES(?,?,?,?,0,?,?,'alive',?,0,1,'Born into the player branch of the family',?)`, []any{i64(f["family_id"]), name, fmt.Sprintf("Child of user %d", userID), p.Gender, p.GameMinute, 70 + lifeRoll, root, now})
	if e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"child_id": c.LastInsertID, "npc_id": c.LastInsertID, "family_id": i64(f["family_id"]), "name": name, "spiritual_root": root, "natural_lifespan_years": 70 + lifeRoll}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "family", EventType: "family.add_child", EntityType: "birth_family", EntityID: fmt.Sprint(f["family_id"]), GameMinute: p.GameMinute, Payload: out}}, nil
}
func phaseCapGo(catalog worlddata.Catalog, realm, phase int64, body bool) int64 {
	realms := catalog.Realms
	if body {
		realms = catalog.BodyRealms
	}
	if realm < 0 || int(realm) >= len(realms) {
		return math.MaxInt64
	}
	costs := realms[realm].PhaseCosts
	if phase <= 0 || int(phase) > len(costs) {
		return math.MaxInt64
	}
	return costs[phase-1]
}
func soulCultivationMultGo(conn *storage.Conn, userID int64) float64 {
	r, e := conn.Execute(`SELECT talent_echo,special_trait FROM soul_legacy WHERE user_id=?`, []any{userID})
	if e != nil {
		return 1
	}
	x := firstRowMap(r)
	if x == nil {
		return 1
	}
	mult := 1 + math.Min(.10, float64(clamp(i64(x["talent_echo"]), 0, 100))/1000)
	switch fmt.Sprint(x["special_trait"]) {
	case "Born Knowing":
		mult += .03
	case "Old Soul":
		mult += .02
	case "Heaven-Defying Fate":
		mult += .05
	}
	return math.Min(1.25, math.Max(1, mult))
}
func seclusionStartActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p seclusionStartPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	p.Mode = strings.ToLower(strings.TrimSpace(p.Mode))
	if p.Mode != "qi" && p.Mode != "body" {
		return authoritativeMutation{}, errors.New("seclusion mode must be qi or body")
	}
	if p.DurationGameMinutes <= 0 {
		p.DurationGameMinutes = 1
	}
	r, e := conn.Execute(`SELECT life_status,location FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	c := firstRowMap(r)
	if c == nil {
		return authoritativeMutation{}, errors.New("create a cultivation character first")
	}
	if fmt.Sprint(c["life_status"]) != "alive" {
		return authoritativeMutation{}, errors.New("a deceased incarnation cannot enter seclusion")
	}
	if p.Location == "" {
		p.Location = fmt.Sprint(c["location"])
	}
	if p.Location != fmt.Sprint(c["location"]) {
		return authoritativeMutation{}, errors.New("seclusion location must match current location")
	}
	r, _ = conn.Execute(`SELECT 1 FROM battles WHERE user_id=? AND status='active' LIMIT 1`, []any{userID})
	if firstRowMap(r) != nil {
		return authoritativeMutation{}, errors.New("cannot enter seclusion during an active battle")
	}
	r, _ = conn.Execute(`SELECT status FROM seclusion_sessions WHERE user_id=?`, []any{userID})
	if x := firstRowMap(r); x != nil && fmt.Sprint(x["status"]) == "active" {
		return authoritativeMutation{}, errors.New("already in seclusion")
	}
	if p.EnvironmentMult <= 0 {
		p.EnvironmentMult = 1
	}
	p.EnvironmentMult = math.Max(.5, math.Min(1.75, p.EnvironmentMult))
	end := p.GameMinute + p.DurationGameMinutes
	now := nowSeconds()
	_, e = conn.Execute(`INSERT INTO seclusion_sessions(user_id,mode,started_game_minute,ends_game_minute,last_settled_game_minute,start_location,environment_mult,accumulated_gain,status,ended_reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,0,'active','',?,?) ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode,started_game_minute=excluded.started_game_minute,ends_game_minute=excluded.ends_game_minute,last_settled_game_minute=excluded.last_settled_game_minute,start_location=excluded.start_location,environment_mult=excluded.environment_mult,accumulated_gain=0,status='active',ended_reason='',created_at=excluded.created_at,updated_at=excluded.updated_at`, []any{userID, p.Mode, p.GameMinute, end, p.GameMinute, p.Location, p.EnvironmentMult, now, now})
	if e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"mode": p.Mode, "started_game_minute": p.GameMinute, "ends_game_minute": end, "last_settled_game_minute": p.GameMinute, "start_location": p.Location, "environment_mult": p.EnvironmentMult, "status": "active"}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "cultivation", EventType: "seclusion.start", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
func seclusionSettleActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p seclusionSettlePayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	if p.MinutesPerDay <= 0 {
		p.MinutesPerDay = 1440
	}
	r, e := conn.Execute(`SELECT * FROM seclusion_sessions WHERE user_id=? AND status='active'`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	s := firstRowMap(r)
	if s == nil {
		return authoritativeMutation{}, errors.New("no active seclusion")
	}
	r, e = conn.Execute(`SELECT life_status,realm_index,phase,body_realm_index,body_phase,cultivation,body_cultivation,attributes_json FROM characters WHERE user_id=?`, []any{userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	c := firstRowMap(r)
	if c == nil {
		return authoritativeMutation{}, errors.New("character not found")
	}
	last := i64(s["last_settled_game_minute"])
	end := i64(s["ends_game_minute"])
	target := min64(p.GameMinute, end)
	days := max64(0, (target-last)/p.MinutesPerDay)
	attrs := decodeJSONMap(c["attributes_json"])
	mode := fmt.Sprint(s["mode"])
	base := int64(0)
	if mode == "body" {
		base = 7 + i64(attrs["body"]) + i64(attrs["will"])/3 + i64(c["body_realm_index"])/2
	} else {
		base = 8 + i64(attrs["will"]) + i64(attrs["insight"])/2 + i64(c["realm_index"])/2
	}
	env, _ := strconvFloat(s["environment_mult"])
	daily := max64(1, int64(math.Round(float64(base)*.60*math.Max(.5, math.Min(1.75, env))*soulCultivationMultGo(conn, userID))))
	attempted := daily * days
	awarded := int64(0)
	field := "cultivation"
	current := i64(c["cultivation"])
	cap := phaseCapGo(catalog, i64(c["realm_index"]), i64(c["phase"]), false)
	if mode == "body" {
		field = "body_cultivation"
		current = i64(c["body_cultivation"])
		cap = phaseCapGo(catalog, i64(c["body_realm_index"]), i64(c["body_phase"]), true)
	}
	awarded = min64(attempted, max64(0, cap-current))
	settled := last + days*p.MinutesPerDay
	completed := p.GameMinute >= end && settled >= end
	if p.ForceEnd {
		completed = true
		if p.EndReason == "" {
			p.EndReason = "emerged early"
		}
	} else if completed && p.EndReason == "" {
		p.EndReason = "planned seclusion completed"
	}
	now := nowSeconds()
	if awarded > 0 {
		q := fmt.Sprintf("UPDATE characters SET %s=%s+?,updated_at=? WHERE user_id=?", field, field)
		if _, e = conn.Execute(q, []any{awarded, now, userID}); e != nil {
			return authoritativeMutation{}, e
		}
	}
	status := "active"
	reason := ""
	if completed {
		status = "completed"
		reason = p.EndReason
	}
	_, e = conn.Execute(`UPDATE seclusion_sessions SET last_settled_game_minute=?,accumulated_gain=accumulated_gain+?,status=?,ended_reason=?,updated_at=? WHERE user_id=?`, []any{settled, awarded, status, reason, now, userID})
	if e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"mode": mode, "awarded_now": awarded, "settled_days_now": days, "daily_gain": daily, "last_settled_game_minute": settled, "ends_game_minute": end, "status": status, "ended_reason": reason}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "cultivation", EventType: "seclusion.settle", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
func strconvFloat(v any) (float64, error) {
	var f float64
	_, e := fmt.Sscan(fmt.Sprint(v), &f)
	return f, e
}
func adjustFateGo(conn *storage.Conn, userID, delta int64, reason string, gm int64, now float64) (int64, error) {
	r, e := conn.Execute(`SELECT points,lifetime_earned,lifetime_spent FROM character_fate WHERE user_id=?`, []any{userID})
	if e != nil {
		return 0, e
	}
	x := firstRowMap(r)
	cur, earned, spent := int64(0), int64(0), int64(0)
	if x != nil {
		cur = i64(x["points"])
		earned = i64(x["lifetime_earned"])
		spent = i64(x["lifetime_spent"])
	}
	target := clamp(cur+delta, 0, 9)
	applied := target - cur
	if applied > 0 {
		earned += applied
	} else if applied < 0 {
		spent += -applied
	}
	_, e = conn.Execute(`INSERT INTO character_fate(user_id,points,lifetime_earned,lifetime_spent,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET points=excluded.points,lifetime_earned=excluded.lifetime_earned,lifetime_spent=excluded.lifetime_spent,updated_at=excluded.updated_at`, []any{userID, target, earned, spent, now})
	if e != nil {
		return cur, e
	}
	if applied != 0 {
		if len([]rune(reason)) > 300 {
			reason = string([]rune(reason)[:300])
		}
		_, e = conn.Execute(`INSERT INTO fate_ledger(user_id,delta,balance_after,reason,game_minute,created_at) VALUES(?,?,?,?,?,?)`, []any{userID, applied, target, reason, gm, now})
	}
	return target, e
}
func fateAdjustActionGo(conn *storage.Conn, _ worlddata.Catalog, userID int64, raw json.RawMessage) (authoritativeMutation, error) {
	var p fateAdjustPayload
	if e := json.Unmarshal(raw, &p); e != nil {
		return authoritativeMutation{}, e
	}
	now := nowSeconds()
	balance, e := adjustFateGo(conn, userID, p.Delta, p.Reason, p.GameMinute, now)
	if e != nil {
		return authoritativeMutation{}, e
	}
	out := map[string]any{"balance": balance, "delta_requested": p.Delta, "reason": p.Reason}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "fate", EventType: "fate.adjust", EntityType: "character", EntityID: fmt.Sprint(userID), GameMinute: p.GameMinute, Payload: out}}, nil
}
func daoPartnershipActionGo(conn *storage.Conn, catalog worlddata.Catalog, userID int64, raw json.RawMessage, op string) (authoritativeMutation, error) {
	now := nowSeconds()
	out := map[string]any{}
	if op == "dao.propose" {
		var p daoProposePayload
		if e := json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		if p.PartnerUserID == userID {
			return authoritativeMutation{}, errors.New("cannot form a Dao partnership with yourself")
		}
		a, b := userID, p.PartnerUserID
		if a > b {
			a, b = b, a
		}
		r, e := conn.Execute(`SELECT user_id,life_status FROM characters WHERE user_id IN (?,?)`, []any{a, b})
		if e != nil {
			return authoritativeMutation{}, e
		}
		rows := rowsToMaps(r)
		if len(rows) != 2 {
			return authoritativeMutation{}, errors.New("both cultivators must be living incarnations")
		}
		for _, x := range rows {
			if fmt.Sprint(x["life_status"]) != "alive" {
				return authoritativeMutation{}, errors.New("both cultivators must be living incarnations")
			}
		}
		r, _ = conn.Execute(`SELECT 1 FROM dao_partnerships WHERE status IN ('pending','active') AND (user_a IN (?,?) OR user_b IN (?,?)) LIMIT 1`, []any{a, b, a, b})
		if firstRowMap(r) != nil {
			return authoritativeMutation{}, errors.New("one cultivator already has a pending or active Dao partnership")
		}
		_, e = conn.Execute(`INSERT INTO dao_partnerships(user_a,user_b,requested_by,status,resonance,dual_sessions,created_at,updated_at) VALUES(?,?,?,'pending',0,0,?,?) ON CONFLICT(user_a,user_b) DO UPDATE SET requested_by=excluded.requested_by,status='pending',resonance=0,dual_sessions=0,created_at=excluded.created_at,updated_at=excluded.updated_at`, []any{a, b, userID, now, now})
		if e != nil {
			return authoritativeMutation{}, e
		}
		r, _ = conn.Execute(`SELECT * FROM dao_partnerships WHERE user_a=? AND user_b=?`, []any{a, b})
		out = firstRowMap(r)
	} else if op == "dao.respond" {
		var p daoRespondPayload
		if e := json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		r, e := conn.Execute(`SELECT * FROM dao_partnerships WHERE partnership_id=? AND status='pending'`, []any{p.PartnershipID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		row := firstRowMap(r)
		if row == nil {
			return authoritativeMutation{}, errors.New("pending Dao-partnership proposal does not exist")
		}
		if (userID != i64(row["user_a"]) && userID != i64(row["user_b"])) || userID == i64(row["requested_by"]) {
			return authoritativeMutation{}, errors.New("only the invited cultivator can answer this proposal")
		}
		status := "rejected"
		if p.Accept {
			status = "active"
		}
		_, e = conn.Execute(`UPDATE dao_partnerships SET status=?,updated_at=? WHERE partnership_id=?`, []any{status, now, p.PartnershipID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		row["status"] = status
		out = row
	} else if op == "dao.sever" {
		r, e := conn.Execute(`SELECT * FROM dao_partnerships WHERE status='active' AND (user_a=? OR user_b=?) LIMIT 1`, []any{userID, userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		row := firstRowMap(r)
		if row == nil {
			return authoritativeMutation{}, errors.New("no active Dao partnership")
		}
		_, e = conn.Execute(`UPDATE dao_partnerships SET status='severed',updated_at=? WHERE partnership_id=?`, []any{now, i64(row["partnership_id"])})
		if e != nil {
			return authoritativeMutation{}, e
		}
		row["status"] = "severed"
		out = row
	} else {
		var p daoDualPayload
		if e := json.Unmarshal(raw, &p); e != nil {
			return authoritativeMutation{}, e
		}
		if p.CooldownSeconds <= 0 {
			p.CooldownSeconds = 1800
		}
		r, e := conn.Execute(`SELECT * FROM dao_partnerships WHERE status='active' AND (user_a=? OR user_b=?) LIMIT 1`, []any{userID, userID})
		if e != nil {
			return authoritativeMutation{}, e
		}
		bond := firstRowMap(r)
		if bond == nil {
			return authoritativeMutation{}, errors.New("no active Dao partnership")
		}
		a, b := i64(bond["user_a"]), i64(bond["user_b"])
		partner := b
		if userID == b {
			partner = a
		}
		r, e = conn.Execute(`SELECT user_id,name,location,life_status,cultivation,realm_index,phase,attributes_json FROM characters WHERE user_id IN (?,?)`, []any{a, b})
		if e != nil {
			return authoritativeMutation{}, e
		}
		chars := map[int64]map[string]any{}
		for _, x := range rowsToMaps(r) {
			chars[i64(x["user_id"])] = x
		}
		if len(chars) != 2 || fmt.Sprint(chars[a]["life_status"]) != "alive" || fmt.Sprint(chars[b]["life_status"]) != "alive" {
			return authoritativeMutation{}, errors.New("both Dao partners must be living incarnations")
		}
		if fmt.Sprint(chars[a]["location"]) != fmt.Sprint(chars[b]["location"]) {
			return authoritativeMutation{}, errors.New("dual cultivation requires both partners at the same location")
		}
		r, _ = conn.Execute(`SELECT 1 FROM battles WHERE status='active' AND user_id IN (?,?) LIMIT 1`, []any{a, b})
		if firstRowMap(r) != nil {
			return authoritativeMutation{}, errors.New("dual cultivation cannot begin during battle")
		}
		r, _ = conn.Execute(`SELECT user_id,available_at FROM cooldowns WHERE action='dao_dual_cultivation' AND user_id IN (?,?)`, []any{a, b})
		for _, cd := range rowsToMaps(r) {
			f, _ := strconvFloat(cd["available_at"])
			if f > now {
				return authoritativeMutation{}, errors.New("paired meridian cycle is still on cooldown")
			}
		}
		aa, bb := decodeJSONMap(chars[a]["attributes_json"]), decodeJSONMap(chars[b]["attributes_json"])
		base := int64(4) + min64(i64(aa["spirit"])+i64(aa["will"]), i64(bb["spirit"])+i64(bb["will"]))/4
		oldRes := clamp(i64(bond["resonance"]), 0, 100)
		gain := max64(3, base+oldRes/25)
		newRes := min64(100, oldRes+4)
		milestone := newRes/25 > oldRes/25
		awarded := map[string]int64{}
		for _, uid := range []int64{a, b} {
			cur := i64(chars[uid]["cultivation"])
			cap := phaseCapGo(catalog, i64(chars[uid]["realm_index"]), i64(chars[uid]["phase"]), false)
			actual := min64(gain, max64(0, cap-cur))
			awarded[fmt.Sprint(uid)] = actual
			_, e = conn.Execute(`UPDATE characters SET cultivation=cultivation+?,updated_at=? WHERE user_id=?`, []any{actual, now, uid})
			if e != nil {
				return authoritativeMutation{}, e
			}
			_, _ = conn.Execute(`INSERT INTO cooldowns(user_id,action,available_at) VALUES(?,'dao_dual_cultivation',?) ON CONFLICT(user_id,action) DO UPDATE SET available_at=excluded.available_at`, []any{uid, now + float64(max64(60, p.CooldownSeconds))})
		}
		_, e = conn.Execute(`UPDATE dao_partnerships SET resonance=?,dual_sessions=dual_sessions+1,updated_at=? WHERE partnership_id=?`, []any{newRes, now, i64(bond["partnership_id"])})
		if e != nil {
			return authoritativeMutation{}, e
		}
		if milestone {
			_, _ = adjustFateGo(conn, a, 1, "dao_partner_resonance", p.GameMinute, now)
			_, _ = adjustFateGo(conn, b, 1, "dao_partner_resonance", p.GameMinute, now)
		}
		out = map[string]any{"partnership_id": i64(bond["partnership_id"]), "partner_user_id": partner, "partner_name": fmt.Sprint(chars[partner]["name"]), "location": fmt.Sprint(chars[a]["location"]), "resonance": newRes, "previous_resonance": oldRes, "milestone": milestone, "awarded": awarded}
	}
	return authoritativeMutation{Result: out, Event: eventledger.Event{Domain: "dao", EventType: op, EntityType: "character", EntityID: fmt.Sprint(userID), Payload: out}}, nil
}
func sortedKeys(m map[string]int64) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

package simulation

import (
	"encoding/json"
	"fmt"
	"math"
	"strings"

	"xianxia/core/internal/gamerng"
	lifespanmodel "xianxia/core/internal/lifespan"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

type BootstrapRequest struct {
	GameMinute int64 `json:"game_minute"`
}

type BootstrapResult struct {
	SimulationSystemsCreated int64 `json:"simulation_systems_created"`
	RegionsCreated           int64 `json:"regions_created"`
	NPCsCreated              int64 `json:"npcs_created"`
	SectStatesCreated        int64 `json:"sect_states_created"`
	EconomyMarketsCreated    int64 `json:"economy_markets_created"`
	NPCMoodsInitialized      int64 `json:"npc_moods_initialized"`
	ClanBranchesCreated      int64 `json:"clan_branches_created"`
	RetainerGroupsCreated    int64 `json:"retainer_groups_created"`
	ClanRelationsCreated     int64 `json:"clan_relations_created"`
}

var clanRelationTypes = []string{"alliance", "marriage_pact", "trade_pact", "rivalry", "blood_feud"}
var retainerRoles = []string{"Estate Guards", "Caravan Guards", "Guest Elders", "Herb Gatherers", "Forge Retainers", "Information Brokers"}
var clanPartnerSurnames = []string{"Han", "Qin", "Lu", "Wei", "Sun", "Gu", "Ning", "Feng"}
var cadetBranchLabels = []string{
	"Second Branch", "Third Branch", "Fourth Branch", "Fifth Branch", "Sixth Branch",
	"Seventh Branch", "Eighth Branch", "Ninth Branch", "Tenth Branch", "Eleventh Branch", "Twelfth Branch",
}

func realmIndexFromName(name string, realms []Realm) int64 {
	normalized := strings.ToLower(strings.TrimSpace(name))
	if normalized == "" || normalized == "mortal" || normalized == "no detectable cultivation" || normalized == "suppressed aura" {
		return 0
	}
	for i, realm := range realms {
		if strings.ToLower(strings.TrimSpace(realm.Name)) == normalized {
			return int64(i)
		}
	}
	known := []string{
		"body tempering", "qi refining", "foundation establishment", "core formation",
		"nascent soul", "spirit severing", "dao seeking", "immortal ascension",
	}
	for i, candidate := range known {
		if normalized == candidate && i < len(realms) {
			return int64(i)
		}
	}
	return 0
}

func marketTradeable(item Item) bool {
	return worlddata.MarketTradeable(item.MarketExcluded, item.SpatialKey != nil, item.AuctionInterest)
}

func worldMultiplier(world string) int64 {
	switch world {
	case "Spiritual World":
		return 3
	case "Immortal World":
		return 10
	case "Celestial World":
		return 30
	default:
		return 1
	}
}

func marketWorldFactor(world string) float64 {
	switch world {
	case "Spiritual World":
		return 1.2
	case "Immortal World":
		return 1.5
	case "Celestial World":
		return 2.0
	default:
		return 1.0
	}
}

func worldCurrency(world string) string {
	switch world {
	case "Spiritual World":
		return "low_spirit_crystal"
	case "Immortal World":
		return "low_immortal_stone"
	case "Celestial World":
		return "low_celestial_crystal"
	default:
		return "low_spirit_stone"
	}
}

func bootstrapRank(profile NPC, faction string, realmIndex, influence int64) string {
	if faction == "Independent" {
		return "Independent"
	}
	role := strings.ToLower(profile.Role)
	switch {
	case strings.Contains(role, "leader") || strings.Contains(role, "sect master"):
		return "Sect Master"
	case strings.Contains(role, "elder"):
		return "Elder"
	case realmIndex >= 4 || influence >= 75:
		return "Core Disciple"
	case realmIndex >= 2 || influence >= 45:
		return "Inner Disciple"
	default:
		return "Outer Disciple"
	}
}

func npcMoodOptions(personality, role, activity string) []string {
	personality = strings.ToLower(personality)
	role = strings.ToLower(role)
	var base []string
	switch {
	case strings.Contains(personality, "exhausted") || strings.Contains(personality, "weary"):
		base = []string{"weary", "focused", "concerned"}
	case containsAnyText(personality, "proud", "competitive", "blunt"):
		base = []string{"competitive", "restless", "focused"}
	case containsAnyText(personality, "warm", "compassionate", "patient"):
		base = []string{"patient", "calm", "concerned"}
	case containsAnyText(personality, "observant", "cautious", "restrained"):
		base = []string{"watchful", "guarded", "focused"}
	case strings.Contains(role, "merchant") || strings.Contains(role, "broker"):
		base = []string{"businesslike", "curious", "guarded"}
	default:
		base = []string{"calm", "focused", "curious", "guarded"}
	}
	if activity == "Traveling" || activity == "Wandering" || activity == "Patrolling" {
		base = append(base, "watchful")
	}
	return base
}

func containsAnyText(value string, needles ...string) bool {
	for _, needle := range needles {
		if strings.Contains(value, needle) {
			return true
		}
	}
	return false
}

func (r *Runner) Bootstrap(req BootstrapRequest) (BootstrapResult, error) {
	conn, err := storage.Open(r.DatabasePath)
	if err != nil {
		return BootstrapResult{}, err
	}
	defer conn.Close()
	if err := conn.ExecScript("BEGIN IMMEDIATE;"); err != nil {
		return BootstrapResult{}, err
	}
	committed := false
	defer func() {
		if !committed {
			_ = conn.Rollback()
		}
	}()

	out := BootstrapResult{}
	if err = r.bootstrapCoreState(conn, req.GameMinute, &out); err != nil {
		return BootstrapResult{}, err
	}
	if out.NPCMoodsInitialized, err = r.bootstrapNPCMoods(conn, req.GameMinute); err != nil {
		return BootstrapResult{}, err
	}
	if err = r.bootstrapClans(conn, req.GameMinute, &out); err != nil {
		return BootstrapResult{}, err
	}
	if err = conn.Commit(); err != nil {
		return BootstrapResult{}, err
	}
	committed = true
	return out, nil
}

func (r *Runner) bootstrapCoreState(conn *storage.Conn, gameMinute int64, out *BootstrapResult) error {
	now := nowFloat()

	for system, interval := range SystemIntervals {
		res, err := conn.Execute(`INSERT INTO world_simulation_state(system,last_game_minute,interval_game_minutes,last_run_real,runs)
VALUES(?,?,?,?,0) ON CONFLICT(system) DO NOTHING`, []any{system, gameMinute, interval, now})
		if err != nil {
			return err
		}
		out.SimulationSystemsCreated += res.RowsAffected
	}

	for location, data := range r.Catalog.Locations {
		world := strings.TrimSpace(data.World)
		if world == "" {
			world = "Mortal World"
		}
		seed := hash64(location)
		prosperity := clamp(38+int64(seed%36), 10, 95)
		security := clamp(35+int64((seed/7)%42), 10, 98)
		if data.SafeZone {
			prosperity = clamp(prosperity+8, 10, 95)
			security = clamp(security+12, 10, 98)
		}
		population := (2500 + int64(seed%35000)) * worldMultiplier(world)
		resources := clamp(30+int64((seed/11)%55), 5, 100)
		food := clamp(45+int64((seed/13)%45), 5, 100)
		res, err := conn.Execute(`INSERT INTO civilization_regions(location,world_name,population,prosperity,security,spirit_resources,food_supply,migration_pressure,unrest,last_game_minute,updated_at)
VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(location) DO NOTHING`,
			[]any{location, world, population, prosperity, security, resources, food, 0, max64(0, 50-security), gameMinute, now})
		if err != nil {
			return err
		}
		out.RegionsCreated += res.RowsAffected
	}

	for npcName, data := range r.Catalog.NPCs {
		location := strings.TrimSpace(data.Location)
		if location == "" {
			location = "Greenriver Town"
		}
		world := "Mortal World"
		if loc, ok := r.Catalog.Locations[location]; ok && strings.TrimSpace(loc.World) != "" {
			world = loc.World
		}
		realmIndex := realmIndexFromName(data.Realm, r.Catalog.Realms)
		phase := max64(1, data.Stage)
		if data.HiddenMaster != nil && strings.TrimSpace(fmt.Sprint(data.HiddenMaster["kind"])) == "real" {
			realmIndex = storage.ParseInt(data.HiddenMaster["true_realm_index"])
			phase = max64(1, storage.ParseInt(data.HiddenMaster["true_stage"]))
		}
		role := strings.TrimSpace(data.Role)
		if role == "" {
			role = "Wandering cultivator"
		}
		faction := "Independent"
		for sectName, sect := range r.Catalog.Sects {
			if sect.Hidden {
				continue
			}
			if strings.Contains(strings.ToLower(role), strings.ToLower(sectName)) {
				faction = sectName
				break
			}
		}
		seed := hash64(npcName)
		wealth := int64(10 + seed%80)
		influence := int64(5 + (seed/5)%90)
		ambition := int64(20 + (seed/9)%75)
		res, err := conn.Execute(`INSERT INTO npc_civilization_state(npc_name,home_location,current_location,world_name,profession,faction,wealth,influence,ambition,realm_index,phase,status,activity,last_game_minute,updated_at)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(npc_name) DO NOTHING`,
			[]any{npcName, location, location, world, role, faction, wealth, influence, ambition, realmIndex, phase, "alive", "Following established routine", gameMinute, now})
		if err != nil {
			return err
		}
		out.NPCsCreated += res.RowsAffected

		goal := strings.TrimSpace(data.Want)
		if goal == "" {
			goal = "Continue their established work"
		}
		if _, err = conn.Execute(`INSERT INTO npc_mind_state(npc_name,current_goal,mood,focus_target,recent_event,goal_progress,last_game_minute,updated_at)
VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(npc_name) DO NOTHING`,
			[]any{npcName, goal, "pending", map[bool]string{true: faction, false: ""}[faction != "Independent"], "", int64(seed % 21), gameMinute, now}); err != nil {
			return err
		}
		naturalLife := lifespanmodel.NaturalYearsFromSeed(seed)
		startingAge := lifespanmodel.BootstrapAge(realmIndex, phase, naturalLife, seed)
		rank := bootstrapRank(data, faction, realmIndex, influence)
		if _, err = conn.Execute(`INSERT INTO npc_life_state(npc_name,birth_game_minute,age_at_creation_years,natural_lifespan_years,health,injury,injury_severity,sect_rank,career_progress,relationship_status,spouse_name,children_count,last_social_game_minute,last_cultivation_game_minute,updated_at)
VALUES(?,?,?,?,100,'',0,?,?, 'single','',0,?,?,?) ON CONFLICT(npc_name) DO NOTHING`,
			[]any{npcName, gameMinute, startingAge, naturalLife, rank, int64(seed % 31), gameMinute, gameMinute, now}); err != nil {
			return err
		}
	}

	for sectName, data := range r.Catalog.Sects {
		if data.Hidden {
			continue
		}
		seed := hash64(sectName)
		alignment := strings.TrimSpace(data.Alignment)
		if alignment == "" {
			alignment = "Neutral"
		}
		specialty := strings.TrimSpace(data.Specialty)
		if specialty == "" {
			specialty = "General cultivation"
		}
		res, err := conn.Execute(`INSERT INTO sect_politics_state(sect_name,alignment,specialty,influence,cohesion,resources,recruitment_pressure,doctrine_pressure,leader_policy,last_game_minute,updated_at)
VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sect_name) DO NOTHING`,
			[]any{sectName, alignment, specialty, 45 + int64(seed%35), 45 + int64((seed/3)%35), 40 + int64((seed/7)%40), 50, 50, "Balanced", gameMinute, now})
		if err != nil {
			return err
		}
		out.SectStatesCreated += res.RowsAffected

		factions := [][2]string{
			{"Old Guard", "Preserve lineage, traditions and elder authority"},
			{"Merit Hall", "Reward contribution, talent and battlefield merit"},
			{"Expansion Bloc", "Acquire territory, disciples and external influence"},
		}
		for i, faction := range factions {
			if _, err = conn.Execute(`INSERT INTO sect_factions(sect_name,faction_name,agenda,power,loyalty,updated_at)
VALUES(?,?,?,?,?,?) ON CONFLICT(sect_name,faction_name) DO NOTHING`,
				[]any{sectName, faction[0], faction[1], 25 + int64((seed+uint64(i*17))%35), 45 + int64((seed+uint64(i*11))%45), now}); err != nil {
				return err
			}
		}
	}

	sectNames := make([]string, 0, len(r.Catalog.Sects))
	for name, sect := range r.Catalog.Sects {
		if sect.Hidden {
			continue
		}
		sectNames = append(sectNames, name)
	}
	for i, a := range sectNames {
		for _, b := range sectNames[i+1:] {
			score := int64(-5)
			if strings.TrimSpace(r.Catalog.Sects[a].Alignment) == strings.TrimSpace(r.Catalog.Sects[b].Alignment) {
				score = 15
			}
			if _, err := conn.Execute(`INSERT INTO sect_relations(sect_a,sect_b,relation_score,relation_type,treaty_status,updated_at)
VALUES(?,?,?,?,?,?) ON CONFLICT(sect_a,sect_b) DO NOTHING`, []any{a, b, score, "neutral", "none", now}); err != nil {
				return err
			}
		}
	}

	for location, loc := range r.Catalog.Locations {
		world := strings.TrimSpace(loc.World)
		if world == "" {
			world = "Mortal World"
		}
		for itemID, item := range r.Catalog.Items {
			if !marketTradeable(item) {
				if _, err := conn.Execute(`DELETE FROM economy_markets WHERE location=? AND item_id=?`, []any{location, itemID}); err != nil {
					return err
				}
				continue
			}
			base := item.SectValue
			if base < 1 {
				base = 5
			}
			basePrice := max64(1, int64(math.Round(float64(base)*marketWorldFactor(world))))
			rarity := math.Max(1, math.Sqrt(float64(base)))
			supply := max64(1, int64(120/rarity))
			demand := max64(5, int64(40+math.Min(80, float64(base)/4)))
			res, err := conn.Execute(`INSERT INTO economy_markets(location,item_id,world_name,currency_id,base_price,supply,demand,price_index,last_game_minute,updated_at)
VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(location,item_id) DO NOTHING`,
				[]any{location, itemID, world, worldCurrency(world), basePrice, supply, demand, 1.0, gameMinute, now})
			if err != nil {
				return err
			}
			out.EconomyMarketsCreated += res.RowsAffected
		}
	}
	return nil
}

func (r *Runner) bootstrapNPCMoods(conn *storage.Conn, gameMinute int64) (int64, error) {
	res, err := conn.Execute(`SELECT c.npc_name,c.profession,c.activity,m.mood
FROM npc_civilization_state c
JOIN npc_mind_state m ON m.npc_name=c.npc_name
WHERE m.mood='' OR m.mood='pending'`, nil)
	if err != nil {
		return 0, err
	}
	var changed int64
	now := nowFloat()
	for _, row := range maps(res) {
		name := fmt.Sprint(row["npc_name"])
		role := fmt.Sprint(row["profession"])
		personality := ""
		if profile, ok := r.Catalog.NPCs[name]; ok {
			if strings.TrimSpace(profile.Role) != "" {
				role = profile.Role
			}
			personality = profile.Personality
		}
		options := npcMoodOptions(personality, role, fmt.Sprint(row["activity"]))
		idx, e := gamerng.Intn(len(options))
		if e != nil {
			return changed, e
		}
		if _, e = conn.Execute(`UPDATE npc_mind_state SET mood=?,last_game_minute=?,updated_at=? WHERE npc_name=? AND (mood='' OR mood='pending')`, []any{options[idx], gameMinute, now, name}); e != nil {
			return changed, e
		}
		changed++
	}
	return changed, nil
}

func (r *Runner) bootstrapClans(conn *storage.Conn, gameMinute int64, out *BootstrapResult) error {
	res, err := conn.Execute(`SELECT family_id,family_name,surname,tier,head_name,head_realm_index,branch_count,retainer_count
FROM birth_families WHERE line_status='active'`, nil)
	if err != nil {
		return err
	}
	now := nowFloat()
	for _, fam := range maps(res) {
		familyID := i64(fam["family_id"])
		surname := strings.TrimSpace(fmt.Sprint(fam["surname"]))
		if surname == "" {
			surname = "Clan"
		}
		countRes, e := conn.Execute(`SELECT COUNT(*) AS n FROM martial_clan_branches WHERE family_id=?`, []any{familyID})
		if e != nil {
			return e
		}
		existing := i64(firstMap(countRes)["n"])
		desired := clamp(i64(fam["branch_count"]), 1, 12)
		if existing == 0 {
			memberRoll, e := gamerng.Intn(45)
			if e != nil {
				return e
			}
			if _, e = conn.Execute(`INSERT INTO martial_clan_branches(family_id,branch_name,branch_type,leader_name,members_estimate,martial_strength,wealth_share,loyalty,status,updated_at)
VALUES(?,?,?,?,?,?,?,?,?,?)`, []any{familyID, surname + " Main Branch", "main", firstNonemptyText(fmt.Sprint(fam["head_name"]), "Family Head"), 18 + memberRoll, 25 + i64(fam["tier"])*10, 55, 85, "active", now}); e != nil {
				return e
			}
			existing = 1
			out.ClanBranchesCreated++
		}
		for idx := existing; idx < desired; idx++ {
			labelIndex := int(idx - 1)
			if labelIndex < 0 {
				labelIndex = 0
			}
			if labelIndex >= len(cadetBranchLabels) {
				labelIndex = len(cadetBranchLabels) - 1
			}
			members, e := gamerng.Intn(30)
			if e != nil {
				return e
			}
			strength, e := gamerng.Intn(45)
			if e != nil {
				return e
			}
			wealth, e := gamerng.Intn(18)
			if e != nil {
				return e
			}
			loyalty, e := gamerng.Intn(45)
			if e != nil {
				return e
			}
			if _, e = conn.Execute(`INSERT INTO martial_clan_branches(family_id,branch_name,branch_type,leader_name,members_estimate,martial_strength,wealth_share,loyalty,status,updated_at)
VALUES(?,?,?,?,?,?,?,?,?,?)`, []any{familyID, surname + " " + cadetBranchLabels[labelIndex], "cadet", surname + " Branch Elder", 8 + members, 15 + strength, 8 + wealth, 45 + loyalty, "active", now}); e != nil {
				return e
			}
			out.ClanBranchesCreated++
		}

		retainedRes, e := conn.Execute(`SELECT COALESCE(SUM(members),0) AS n FROM martial_clan_retainers WHERE family_id=? AND status='active'`, []any{familyID})
		if e != nil {
			return e
		}
		retained := i64(firstMap(retainedRes)["n"])
		desiredRetainers := max64(0, i64(fam["retainer_count"]))
		if retained < desiredRetainers {
			remaining := desiredRetainers - retained
			groups := min64(4, max64(1, (remaining+7)/8))
			for group := int64(0); group < groups && remaining > 0; group++ {
				groupsLeft := max64(1, groups-group)
				members := max64(1, (remaining+groupsLeft-1)/groupsLeft)
				roleIndex, e := gamerng.Intn(len(retainerRoles))
				if e != nil {
					return e
				}
				loyalty, e := gamerng.Intn(45)
				if e != nil {
					return e
				}
				role := retainerRoles[roleIndex]
				if _, e = conn.Execute(`INSERT INTO martial_clan_retainers(family_id,group_name,leader_name,role,members,realm_index,loyalty,upkeep,status,updated_at)
VALUES(?,?,?,?,?,?,?,?,?,?)`, []any{familyID, surname + " " + role, surname + " Retainer Captain", role, members, max64(0, i64(fam["head_realm_index"])-1), 50 + loyalty, max64(1, members/3), "active", now}); e != nil {
					return e
				}
				remaining -= members
				out.RetainerGroupsCreated++
			}
		}

		relationRes, e := conn.Execute(`SELECT COUNT(*) AS n FROM martial_clan_relations WHERE family_id=? AND active=1`, []any{familyID})
		if e != nil {
			return e
		}
		if i64(firstMap(relationRes)["n"]) == 0 {
			partnerIndex, e := gamerng.Intn(len(clanPartnerSurnames))
			if e != nil {
				return e
			}
			relationIndex, e := gamerng.Intn(len(clanRelationTypes))
			if e != nil {
				return e
			}
			partner := clanPartnerSurnames[partnerIndex] + " Martial Clan"
			relation := clanRelationTypes[relationIndex]
			score := map[string]int64{"alliance": 45, "marriage_pact": 35, "trade_pact": 25, "rivalry": -30, "blood_feud": -65}[relation]
			if _, e = conn.Execute(`INSERT INTO martial_clan_relations(family_id,partner_family_id,partner_name,relation_type,relation_score,active,started_game_minute,updated_at)
VALUES(?,?,?,?,?,?,?,?)`, []any{familyID, nil, partner, relation, score, 1, gameMinute, now}); e != nil {
				return e
			}
			out.ClanRelationsCreated++
			if relation == "alliance" || relation == "marriage_pact" || relation == "trade_pact" || relation == "blood_feud" {
				if e = recordClanBootstrapHistory(conn, fam, partner, relation, score, gameMinute, now); e != nil {
					return e
				}
			}
		}
	}
	return nil
}

func firstNonemptyText(value, fallback string) string {
	if strings.TrimSpace(value) != "" {
		return value
	}
	return fallback
}

func recordClanBootstrapHistory(conn *storage.Conn, fam map[string]any, partner, relation string, score, gameMinute int64, now float64) error {
	familyID := i64(fam["family_id"])
	familyName := firstNonemptyText(fmt.Sprint(fam["family_name"]), firstNonemptyText(fmt.Sprint(fam["surname"]), "Clan")+" Clan")
	label := strings.ReplaceAll(relation, "_", " ")
	eventType := "alliance"
	significance := int64(68)
	if relation == "marriage_pact" {
		eventType = "marriage"
	} else if relation == "blood_feud" {
		eventType = "blood_feud"
		significance = 74
	}
	metadata, _ := json.Marshal(map[string]any{"relation_score": score})
	sourceKey := fmt.Sprintf("clan_relation:%d:%s:%s", familyID, relation, partner)
	_, err := conn.Execute(`INSERT INTO world_history_events(
source_key,event_type,title,summary,significance,visibility,location,world_name,faction,
actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,
related_npc_name,tags,game_minute,metadata_json,created_at,updated_at)
VALUES(?,?,?,?,?,'public','','',?,'clan',?,?,'clan',?,?,NULL,'',?,?,?, ?,?)
ON CONFLICT(source_key) DO NOTHING`, []any{
		sourceKey, eventType, familyName + " formed a " + label + " with " + partner,
		familyName + " and " + partner + " entered a lasting " + label + " recorded by the martial world.",
		significance, familyName, fmt.Sprint(familyID), familyName, partner, partner,
		"clan " + relation + " relationship", gameMinute, string(metadata), now, now,
	})
	return err
}

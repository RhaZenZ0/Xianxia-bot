package simulation

import (
	"encoding/json"
	"fmt"
	"hash/fnv"
	"math"
	"os"
	"sort"
	"strings"
	"time"

	"xianxia/core/internal/storage"
)

const (
	minutesPerDay       = int64(1440)
	minutesPerYear      = int64(1440 * 30 * 12)
	blackMarketRotation = int64(3 * 1440)
	blackMarketOpen     = int64(2 * 1440)
)

var SystemIntervals = map[string]int64{
	"npc_civilization": minutesPerDay,
	"npc_life":         7 * minutesPerDay,
	"dynamic_economy":  minutesPerDay,
	"black_markets":    blackMarketRotation,
	"sect_politics":    7 * minutesPerDay,
	"clan_dynamics":    30 * minutesPerDay,
}

var orderedSystems = []string{"npc_civilization", "npc_life", "dynamic_economy", "black_markets", "sect_politics", "clan_dynamics"}

type Location struct {
	World        string `json:"world"`
	SafeZone     bool   `json:"safe_zone"`
	AuctionHouse any    `json:"auction_house"`
}
type Item struct {
	Name            string `json:"name"`
	SectValue       int64  `json:"sect_value"`
	BasePrice       int64  `json:"base_price"`
	LegalStatus     string `json:"legal_status"`
	AuctionInterest string `json:"auction_interest"`
	Type            string `json:"type"`
	MarketExcluded  bool   `json:"market_excluded"`
	SpatialKey      any    `json:"spatial_key"`
}
type Catalog struct {
	Locations map[string]Location `json:"locations"`
	Items     map[string]Item     `json:"items"`
}

type Runner struct {
	DatabasePath string
	Catalog      Catalog
}
type Run struct {
	System       string `json:"system"`
	DueSteps     int64  `json:"due_steps"`
	AppliedSteps int64  `json:"applied_steps"`
	Summary      string `json:"summary"`
}
type RunDueRequest struct {
	GameMinute int64           `json:"game_minute"`
	Automation map[string]bool `json:"automation"`
}
type ForceRequest struct {
	System     string `json:"system"`
	Steps      int64  `json:"steps"`
	GameMinute int64  `json:"game_minute"`
}

func NewRunner(databasePath, worldPath string) (*Runner, error) {
	r := &Runner{DatabasePath: databasePath, Catalog: Catalog{Locations: map[string]Location{}, Items: map[string]Item{}}}
	if strings.TrimSpace(worldPath) == "" {
		return r, nil
	}
	data, err := os.ReadFile(worldPath)
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(data, &r.Catalog); err != nil {
		return nil, err
	}
	return r, nil
}

func nowFloat() float64 { return float64(time.Now().UnixNano()) / 1e9 }
func hash64(parts ...string) uint64 {
	h := fnv.New64a()
	for _, p := range parts {
		_, _ = h.Write([]byte(p))
		_, _ = h.Write([]byte{0})
	}
	return h.Sum64()
}
func clamp(v, lo, hi int64) int64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
func firstMap(res storage.Result) map[string]any {
	if len(res.Rows) == 0 {
		return nil
	}
	out := map[string]any{}
	for i, k := range res.Columns {
		if i < len(res.Rows[0]) {
			out[k] = res.Rows[0][i]
		}
	}
	return out
}
func maps(res storage.Result) []map[string]any {
	out := make([]map[string]any, 0, len(res.Rows))
	for _, row := range res.Rows {
		m := map[string]any{}
		for i, k := range res.Columns {
			if i < len(row) {
				m[k] = row[i]
			}
		}
		out = append(out, m)
	}
	return out
}
func i64(v any) int64 { return storage.ParseInt(v) }

func (r *Runner) RunDue(req RunDueRequest) ([]Run, error) {
	conn, err := storage.Open(r.DatabasePath)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	res, err := conn.Execute(`SELECT system,last_game_minute,interval_game_minutes FROM world_simulation_state`, nil)
	if err != nil {
		return nil, err
	}
	states := map[string]map[string]any{}
	for _, row := range maps(res) {
		states[fmt.Sprint(row["system"])] = row
	}
	runs := []Run{}
	for _, system := range orderedSystems {
		if system == "black_markets" || system == "npc_life" {
			if !req.Automation[system] {
				continue
			}
		} else if enabled, ok := req.Automation[system]; ok && !enabled {
			continue
		}
		state := states[system]
		if state == nil {
			continue
		}
		interval := i64(state["interval_game_minutes"])
		if interval < 1 {
			interval = SystemIntervals[system]
		}
		last := i64(state["last_game_minute"])
		delta := req.GameMinute - last
		if delta < 0 {
			delta = 0
		}
		due := delta / interval
		if due <= 0 {
			continue
		}
		applied := due
		if applied > 120 {
			applied = 120
		}
		processed := last + applied*interval
		summary, err := r.runSystem(conn, system, applied, processed)
		if err != nil {
			return runs, fmt.Errorf("%s: %w", system, err)
		}
		if due > applied {
			summary += fmt.Sprintf("; %d interval(s) remain queued for catch-up", due-applied)
		}
		runs = append(runs, Run{System: system, DueSteps: due, AppliedSteps: applied, Summary: summary})
	}
	return runs, nil
}

func (r *Runner) Force(req ForceRequest) (Run, error) {
	if req.Steps < 1 {
		req.Steps = 1
	}
	if req.Steps > 120 {
		req.Steps = 120
	}
	if _, ok := SystemIntervals[req.System]; !ok {
		return Run{}, fmt.Errorf("unknown simulation system %q", req.System)
	}
	conn, err := storage.Open(r.DatabasePath)
	if err != nil {
		return Run{}, err
	}
	defer conn.Close()
	summary, err := r.runSystem(conn, req.System, req.Steps, req.GameMinute)
	if err != nil {
		return Run{}, err
	}
	return Run{System: req.System, DueSteps: req.Steps, AppliedSteps: req.Steps, Summary: summary}, nil
}

func (r *Runner) runSystem(conn *storage.Conn, system string, steps, gameMinute int64) (string, error) {
	if err := conn.ExecScript("BEGIN IMMEDIATE;"); err != nil {
		return "", err
	}
	ok := false
	defer func() {
		if !ok {
			_ = conn.Rollback()
		}
	}()
	var summary string
	var err error
	switch system {
	case "npc_civilization":
		summary, err = r.civilization(conn, steps, gameMinute)
	case "npc_life":
		summary, err = r.npcLife(conn, steps, gameMinute)
	case "dynamic_economy":
		summary, err = r.economy(conn, steps, gameMinute)
	case "black_markets":
		summary, err = r.blackMarkets(conn, steps, gameMinute)
	case "sect_politics":
		summary, err = r.sects(conn, steps, gameMinute)
	case "clan_dynamics":
		summary, err = r.clans(conn, steps, gameMinute)
	default:
		err = fmt.Errorf("unknown system %s", system)
	}
	if err != nil {
		return "", err
	}
	_, err = conn.Execute(`UPDATE world_simulation_state SET last_game_minute=?,last_run_real=?,runs=runs+? WHERE system=?`, []any{gameMinute, nowFloat(), steps, system})
	if err != nil {
		return "", err
	}
	if err = conn.Commit(); err != nil {
		return "", err
	}
	ok = true
	return summary, nil
}

func (r *Runner) civilization(conn *storage.Conn, steps, gm int64) (string, error) {
	now := nowFloat()
	_, err := conn.Execute(`UPDATE civilization_regions SET
population=MAX(50,CAST(population*(1.0+((food_supply-45+security-45-unrest)/2000000.0)*?) AS INTEGER)),
prosperity=MAX(0,MIN(100,prosperity+CASE WHEN food_supply>60 AND security>55 THEN MIN(3,?) WHEN unrest>60 THEN -MIN(3,?) ELSE 0 END)),
security=MAX(0,MIN(100,security+CASE WHEN unrest>70 THEN -MIN(2,?) WHEN prosperity>65 THEN 1 ELSE 0 END)),
spirit_resources=MAX(0,MIN(100,spirit_resources-CASE WHEN prosperity>70 THEN MIN(2,?) ELSE 0 END)),
food_supply=MAX(0,MIN(100,food_supply+CASE WHEN security>55 THEN MIN(2,?) ELSE -MIN(2,?) END)),
migration_pressure=MAX(-50,MIN(50,(prosperity+security+food_supply-150)/3)),
unrest=MAX(0,MIN(100,50-security+MAX(0,35-food_supply)/2)),last_game_minute=?,updated_at=?`, []any{steps, max1(steps / 15), max1(steps / 15), max1(steps / 20), max1(steps / 30), max1(steps / 25), max1(steps / 25), gm, now})
	if err != nil {
		return "", err
	}
	regions := int64(0)
	if res, e := conn.Execute(`SELECT COUNT(*) AS n FROM civilization_regions`, nil); e == nil {
		if row := firstMap(res); row != nil {
			regions = i64(row["n"])
		}
	}
	_, err = conn.Execute(`UPDATE npc_civilization_state SET
wealth=MAX(0,MIN(9999,wealth+CASE WHEN profession LIKE '%Merchant%' OR profession LIKE '%Broker%' THEN ? ELSE MAX(1,?/2) END)),
influence=MAX(0,MIN(999,influence+CASE WHEN ambition>65 THEN MAX(1,?/7) ELSE 0 END)),
ambition=MAX(0,MIN(100,ambition+CASE WHEN (realm_index+phase+?)%5=0 THEN 1 ELSE 0 END)),
activity=CASE WHEN profession LIKE '%Merchant%' OR profession LIKE '%Broker%' THEN 'Trading' WHEN profession LIKE '%Guard%' OR profession LIKE '%Warden%' THEN 'Patrolling' WHEN profession LIKE '%Elder%' OR profession LIKE '%Sect%' THEN 'Managing disciples' ELSE 'Cultivating' END,
phase=CASE WHEN ambition>70 AND ? >= 7 THEN MIN(9,phase+1) ELSE phase END,
last_game_minute=?,updated_at=? WHERE status='alive'`, []any{max1(steps / 2), steps, max1(steps), gm, max1(steps), gm, now})
	if err != nil {
		return "", err
	}
	_, _ = conn.Execute(`UPDATE npc_mind_state SET goal_progress=MIN(100,goal_progress+MAX(1,?/2)),last_game_minute=?,updated_at=? WHERE npc_name IN (SELECT npc_name FROM npc_civilization_state WHERE status='alive')`, []any{steps, gm, now})
	npcs := int64(0)
	if res, e := conn.Execute(`SELECT COUNT(*) AS n FROM npc_civilization_state WHERE status='alive'`, nil); e == nil {
		if row := firstMap(res); row != nil {
			npcs = i64(row["n"])
		}
	}
	return fmt.Sprintf("batch-updated %d regions and %d living NPCs", regions, npcs), nil
}

func (r *Runner) npcLife(conn *storage.Conn, steps, gm int64) (string, error) {
	now := nowFloat()
	heal := max1(steps / 2)
	_, err := conn.Execute(`UPDATE npc_life_state SET health=MIN(100,health+?),injury_severity=MAX(0,injury_severity-?),injury=CASE WHEN injury_severity<=? THEN '' ELSE injury END,career_progress=MIN(1000,career_progress+MAX(1,?)),last_cultivation_game_minute=?,updated_at=? WHERE health>0`, []any{heal, max1(steps / 3), max1(steps / 3), steps, gm, now})
	if err != nil {
		return "", err
	}
	// Realm extends NPC lifespan modestly. Age is based entirely on canonical game time.
	deathRes, err := conn.Execute(`SELECT l.npc_name FROM npc_life_state l JOIN npc_civilization_state c ON c.npc_name=l.npc_name WHERE c.status='alive' AND (l.age_at_creation_years + MAX(0,?-l.birth_game_minute)/?) >= (l.natural_lifespan_years + c.realm_index*20)`, []any{gm, minutesPerYear})
	if err != nil {
		return "", err
	}
	deaths := maps(deathRes)
	for _, row := range deaths {
		name := fmt.Sprint(row["npc_name"])
		_, err = conn.Execute(`UPDATE npc_life_state SET health=0,death_game_minute=?,cause_of_death='natural lifespan exhausted',updated_at=? WHERE npc_name=?`, []any{gm, now, name})
		if err != nil {
			return "", err
		}
		_, err = conn.Execute(`UPDATE npc_civilization_state SET status='dead',activity='Deceased',last_game_minute=?,updated_at=? WHERE npc_name=?`, []any{gm, now, name})
		if err != nil {
			return "", err
		}
	}
	_, err = conn.Execute(`UPDATE npc_social_relations SET affinity=MAX(-100,MIN(100,affinity+CASE WHEN grudge>40 THEN -1 WHEN trust>35 THEN 1 ELSE 0 END)),trust=MAX(-100,MIN(100,trust+CASE WHEN affinity>40 THEN 1 WHEN grudge>50 THEN -1 ELSE 0 END)),grudge=MAX(-100,MIN(100,grudge-CASE WHEN grudge>0 THEN 1 ELSE 0 END)),last_interaction_game_minute=?,updated_at=? WHERE status='active'`, []any{gm, now})
	if err != nil {
		return "", err
	}
	// Pair a bounded number of compatible single NPCs at the same location. This is done in Go,
	// in the same transaction, so autonomous social life never causes Python/SQLite round trips.
	singlesRes, err := conn.Execute(`SELECT c.npc_name,c.current_location,l.children_count FROM npc_civilization_state c JOIN npc_life_state l ON l.npc_name=c.npc_name WHERE c.status='alive' AND l.relationship_status='single' ORDER BY c.current_location,c.npc_name LIMIT 200`, nil)
	if err != nil {
		return "", err
	}
	singles := maps(singlesRes)
	marriages := 0
	for i := 0; i+1 < len(singles); i += 2 {
		a, b := singles[i], singles[i+1]
		if fmt.Sprint(a["current_location"]) != fmt.Sprint(b["current_location"]) {
			continue
		}
		an, bn := fmt.Sprint(a["npc_name"]), fmt.Sprint(b["npc_name"])
		if hash64(an, bn, fmt.Sprint(gm))%100 >= uint64(min64(35, steps+5)) {
			continue
		}
		if _, err = conn.Execute(`UPDATE npc_life_state SET relationship_status='married',spouse_name=?,last_social_game_minute=?,updated_at=? WHERE npc_name=?`, []any{bn, gm, now, an}); err != nil {
			return "", err
		}
		if _, err = conn.Execute(`UPDATE npc_life_state SET relationship_status='married',spouse_name=?,last_social_game_minute=?,updated_at=? WHERE npc_name=?`, []any{an, gm, now, bn}); err != nil {
			return "", err
		}
		pair := []string{an, bn}
		sort.Strings(pair)
		_, err = conn.Execute(`INSERT INTO npc_social_relations(npc_a,npc_b,affinity,trust,grudge,relation_type,status,started_game_minute,last_interaction_game_minute,updated_at) VALUES(?,?,55,45,0,'marriage','active',?,?,?) ON CONFLICT(npc_a,npc_b) DO UPDATE SET relation_type='marriage',status='active',affinity=MAX(affinity,55),trust=MAX(trust,45),updated_at=excluded.updated_at`, []any{pair[0], pair[1], gm, gm, now})
		if err != nil {
			return "", err
		}
		marriages++
	}
	return fmt.Sprintf("batch-advanced NPC life; %d natural death(s), %d new marriage(s)", len(deaths), marriages), nil
}

func (r *Runner) economy(conn *storage.Conn, steps, gm int64) (string, error) {
	now := nowFloat()
	_, err := conn.Execute(`UPDATE economy_markets SET
supply=MAX(1,MIN(500,supply+CASE WHEN COALESCE((SELECT spirit_resources FROM civilization_regions r WHERE r.location=economy_markets.location),50)>55 THEN MAX(1,?/4) ELSE -MAX(1,?/5) END)),
demand=MAX(1,MIN(500,demand+CASE WHEN COALESCE((SELECT prosperity FROM civilization_regions r WHERE r.location=economy_markets.location),50)>60 THEN MAX(1,?/5) ELSE -MAX(1,?/6) END)),
price_index=MAX(0.25,MIN(4.0,1.0 + ((demand-supply)*1.0/MAX(20,supply))/2.5)),last_game_minute=?,updated_at=?`, []any{steps, steps, steps, steps, gm, now})
	if err != nil {
		return "", err
	}
	count := int64(0)
	if res, e := conn.Execute(`SELECT COUNT(*) AS n FROM economy_markets`, nil); e == nil {
		if row := firstMap(res); row != nil {
			count = i64(row["n"])
		}
	}
	return fmt.Sprintf("batch-repriced %d regional market listings", count), nil
}

func (r *Runner) sects(conn *storage.Conn, steps, gm int64) (string, error) {
	now := nowFloat()
	_, err := conn.Execute(`UPDATE sect_politics_state SET influence=MAX(0,MIN(100,influence+CASE WHEN resources>60 AND cohesion>55 THEN 1 WHEN cohesion<30 THEN -1 ELSE 0 END)),cohesion=MAX(0,MIN(100,cohesion+CASE WHEN doctrine_pressure>75 THEN -1 WHEN resources>55 THEN 1 ELSE 0 END)),resources=MAX(0,MIN(100,resources+CASE WHEN influence>55 THEN MAX(1,?/8) ELSE -1 END)),recruitment_pressure=MAX(0,MIN(100,recruitment_pressure+CASE WHEN influence>65 THEN -1 ELSE 1 END)),doctrine_pressure=MAX(0,MIN(100,doctrine_pressure+CASE WHEN cohesion<40 THEN 1 ELSE 0 END)),leader_policy=CASE WHEN cohesion<30 THEN 'Stabilize Internal Factions' WHEN resources<30 THEN 'Secure Resources' WHEN influence>70 THEN 'Expand Influence' ELSE 'Balanced' END,last_game_minute=?,updated_at=?`, []any{steps, gm, now})
	if err != nil {
		return "", err
	}
	_, _ = conn.Execute(`UPDATE sect_factions SET power=MAX(1,MIN(100,power+CASE WHEN loyalty>60 THEN 1 WHEN loyalty<30 THEN -1 ELSE 0 END)),loyalty=MAX(0,MIN(100,loyalty+CASE WHEN power>70 THEN -1 ELSE 0 END)),updated_at=?`, []any{now})
	count := int64(0)
	if res, e := conn.Execute(`SELECT COUNT(*) AS n FROM sect_politics_state`, nil); e == nil {
		if row := firstMap(res); row != nil {
			count = i64(row["n"])
		}
	}
	return fmt.Sprintf("batch-advanced politics for %d sects", count), nil
}

func (r *Runner) clans(conn *storage.Conn, steps, gm int64) (string, error) {
	now := nowFloat()
	_, err := conn.Execute(`UPDATE martial_clan_branches SET martial_strength=MAX(1,MIN(999,martial_strength+CASE WHEN loyalty>65 THEN MAX(1,?/10) ELSE 0 END)),wealth_share=MAX(0,MIN(100,wealth_share+CASE WHEN status='active' THEN 1 ELSE -1 END)),loyalty=MAX(0,MIN(100,loyalty+CASE WHEN branch_type='main' THEN 1 WHEN martial_strength>70 THEN -1 ELSE 0 END)),updated_at=? WHERE status='active'`, []any{steps, now})
	if err != nil {
		return "", err
	}
	_, err = conn.Execute(`UPDATE martial_clan_retainers SET loyalty=MAX(0,MIN(100,loyalty+CASE WHEN upkeep<=5 THEN 1 ELSE -1 END)),updated_at=? WHERE status='active'`, []any{now})
	if err != nil {
		return "", err
	}
	_, err = conn.Execute(`UPDATE martial_clan_relations SET relation_score=MAX(-100,MIN(100,relation_score+CASE relation_type WHEN 'alliance' THEN 1 WHEN 'marriage_pact' THEN 1 WHEN 'blood_feud' THEN -1 WHEN 'rivalry' THEN -1 ELSE 0 END)),updated_at=? WHERE active=1`, []any{now})
	if err != nil {
		return "", err
	}
	count := int64(0)
	if res, e := conn.Execute(`SELECT COUNT(DISTINCT family_id) AS n FROM martial_clan_branches WHERE status='active'`, nil); e == nil {
		if row := firstMap(res); row != nil {
			count = i64(row["n"])
		}
	}
	_ = gm
	return fmt.Sprintf("batch-advanced %d martial clans", count), nil
}

func (r *Runner) blackMarkets(conn *storage.Conn, steps, gm int64) (string, error) {
	_ = steps
	candidates := make([]string, 0)
	for id, item := range r.Catalog.Items {
		legal := strings.ToLower(item.LegalStatus)
		interest := strings.ToLower(item.AuctionInterest)
		typ := strings.ToLower(item.Type)
		name := strings.ToLower(item.Name)
		if legal == "forbidden" || legal == "contraband" || legal == "restricted" || (typ == "manual" && containsAny(name, "demon", "blood", "soul", "nether", "bone", "venom")) || interest == "special" || interest == "legendary" {
			candidates = append(candidates, id)
		}
	}
	sort.Strings(candidates)
	if len(candidates) == 0 {
		return "no eligible underworld goods exist in the content catalog", nil
	}
	worlds := []string{"Mortal World", "Spiritual World", "Immortal World", "Celestial World"}
	currency := map[string]string{"Mortal World": "low_spirit_stone", "Spiritual World": "low_spirit_crystal", "Immortal World": "low_immortal_stone", "Celestial World": "low_celestial_crystal"}
	rotated := 0
	now := nowFloat()
	for _, world := range worlds {
		locs := []string{}
		rough := []string{}
		for name, loc := range r.Catalog.Locations {
			if loc.World != world || (loc.AuctionHouse != nil && strings.TrimSpace(fmt.Sprint(loc.AuctionHouse)) != "") || strings.Contains(name, "Rebirth") || strings.Contains(name, "Cradle") {
				continue
			}
			locs = append(locs, name)
			if !loc.SafeZone {
				rough = append(rough, name)
			}
		}
		pool := locs
		if len(rough) > 0 {
			pool = rough
		}
		if len(pool) == 0 {
			continue
		}
		sort.Strings(pool)
		location := pool[int(hash64(world, fmt.Sprint(gm))%uint64(len(pool)))]
		heat := int64(20 + hash64(location, fmt.Sprint(gm))%61)
		_, err := conn.Execute(`INSERT INTO black_market_posts(world_name,location,heat,opens_game_minute,closes_game_minute,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?) ON CONFLICT(world_name) DO UPDATE SET location=excluded.location,heat=excluded.heat,opens_game_minute=excluded.opens_game_minute,closes_game_minute=excluded.closes_game_minute,active=1,updated_at=excluded.updated_at`, []any{world, location, heat, gm, gm + blackMarketOpen, now, now})
		if err != nil {
			return "", err
		}
		_, err = conn.Execute(`DELETE FROM black_market_stock WHERE world_name=?`, []any{world})
		if err != nil {
			return "", err
		}
		n := 6
		if len(candidates) < n {
			n = len(candidates)
		}
		start := int(hash64(world, "stock", fmt.Sprint(gm)) % uint64(len(candidates)))
		used := map[string]bool{}
		for j := 0; j < n; j++ {
			idx := (start + j*7) % len(candidates)
			for used[candidates[idx]] {
				idx = (idx + 1) % len(candidates)
			}
			id := candidates[idx]
			used[id] = true
			item := r.Catalog.Items[id]
			base := item.BasePrice
			if base <= 0 {
				base = max64(8, item.SectValue*8)
			}
			qty := int64(1 + hash64(id, world, fmt.Sprint(gm))%4)
			scarcity := 1.0 + float64(max64(0, 8-qty))*0.05
			danger := 1.15 + float64(heat)/250.0
			price := int64(math.Round(float64(base) * scarcity * danger))
			if price < 1 {
				price = 1
			}
			legal := item.LegalStatus
			if legal == "" {
				legal = "restricted"
			}
			_, err = conn.Execute(`INSERT INTO black_market_stock(world_name,item_id,currency_id,unit_price,quantity,legal_status,updated_at) VALUES(?,?,?,?,?,?,?)`, []any{world, id, currency[world], price, qty, legal, now})
			if err != nil {
				return "", err
			}
		}
		rotated++
	}
	return fmt.Sprintf("batch-rotated %d underworld trading post(s)", rotated), nil
}

func containsAny(text string, words ...string) bool {
	for _, w := range words {
		if strings.Contains(text, w) {
			return true
		}
	}
	return false
}
func max1(v int64) int64 {
	if v < 1 {
		return 1
	}
	return v
}
func max64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}
func min64(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}

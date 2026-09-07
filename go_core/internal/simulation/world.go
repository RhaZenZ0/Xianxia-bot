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

	"xianxia/core/internal/game"
	"xianxia/core/internal/gamerng"
	lifespanmodel "xianxia/core/internal/lifespan"
	"xianxia/core/internal/storage"
)

const (
	minutesPerDay       = int64(1440)
	minutesPerYear      = int64(1440 * 30 * 12)
	blackMarketRotation = int64(3 * 1440)
	blackMarketOpen     = int64(2 * 1440)
)

var SystemIntervals = map[string]int64{
	"npc_civilization":        minutesPerDay,
	"npc_life":                7 * minutesPerDay,
	"dynamic_economy":         minutesPerDay,
	"black_markets":           blackMarketRotation,
	"sect_politics":           7 * minutesPerDay,
	"clan_dynamics":           30 * minutesPerDay,
	"autonomous_world_events": minutesPerDay,
}

var orderedSystems = []string{"npc_civilization", "npc_life", "dynamic_economy", "black_markets", "sect_politics", "clan_dynamics", "autonomous_world_events"}

type Location struct {
	World        string `json:"world"`
	SafeZone     bool   `json:"safe_zone"`
	Private      bool   `json:"private"`
	AuctionHouse any    `json:"auction_house"`
}
type NPC struct {
	Role         string         `json:"role"`
	Personality  string         `json:"personality"`
	Location     string         `json:"location"`
	Realm        string         `json:"realm"`
	Stage        int64          `json:"stage"`
	Want         string         `json:"want"`
	HiddenMaster map[string]any `json:"hidden_master"`
}

type Sect struct {
	Alignment string `json:"alignment"`
	Specialty string `json:"specialty"`
	// A hidden lineage (the Heaven-Devouring Demon Sect) has no public
	// politics, relations or NPC faction: it recruits through karma, not
	// through the world's sect life. Since v0.21.3 the catalog on disk
	// carries it (materialised from the advanced catalog), so bootstrap
	// must skip it explicitly rather than by never having seen it.
	Hidden bool `json:"hidden"`
}

type Item struct {
	Name            string `json:"name"`
	SectValue       int64  `json:"sect_value"`
	BasePrice       int64  `json:"base_price"`
	LegalStatus     string `json:"legal_status"`
	AuctionInterest string `json:"auction_interest"`
	DoorEventChance int64  `json:"door_event_chance"`
	Type            string `json:"type"`
	MarketExcluded  bool   `json:"market_excluded"`
	SpatialKey      any    `json:"spatial_key"`
}
type Realm struct {
	Name       string  `json:"name"`
	PhaseCosts []int64 `json:"phase_costs"`
}

type Catalog struct {
	Locations        map[string]Location `json:"locations"`
	NPCs             map[string]NPC      `json:"npcs"`
	Sects            map[string]Sect     `json:"sects"`
	Items            map[string]Item     `json:"items"`
	UnexpectedEvents []UnexpectedEvent   `json:"unexpected_events"`
	Realms           []Realm             `json:"realms"`
	BodyRealms       []Realm             `json:"body_realms"`
}

type UnexpectedEvent struct {
	ID              string         `json:"id"`
	Title           string         `json:"title"`
	Category        string         `json:"category"`
	Kind            string         `json:"kind"`
	Weight          int            `json:"weight"`
	DurationHours   int64          `json:"duration_hours"`
	Severity        int64          `json:"severity"`
	Description     string         `json:"description"`
	ConsequenceText string         `json:"consequence_text"`
	Locations       []string       `json:"locations"`
	Worlds          []string       `json:"worlds"`
	PlayerReward    map[string]any `json:"player_reward"`
	PlayerEffect    map[string]any `json:"player_effect"`
	KarmaDelta      int64          `json:"karma_delta"`
	FateDelta       int64          `json:"fate_delta"`
	WorldEffect     map[string]any `json:"world_effect"`
}

type SpawnedWorldEvent struct {
	EventKey        string   `json:"event_key"`
	EventID         string   `json:"event_id"`
	Title           string   `json:"title"`
	Category        string   `json:"category"`
	Description     string   `json:"description"`
	ConsequenceText string   `json:"consequence_text"`
	Location        string   `json:"location"`
	Severity        int64    `json:"severity"`
	ExpiresAt       float64  `json:"expires_at"`
	Impacts         []string `json:"impacts,omitempty"`
}

type Runner struct {
	DatabasePath string
	Catalog      Catalog
}
type Run struct {
	System       string              `json:"system"`
	DueSteps     int64               `json:"due_steps"`
	AppliedSteps int64               `json:"applied_steps"`
	Summary      string              `json:"summary"`
	Events       []SpawnedWorldEvent `json:"events,omitempty"`
}
type RunDueRequest struct {
	// GameMinute is accepted for wire compatibility and deliberately ignored:
	// RunDue derives the canonical world minute inside Go (see #6 in the
	// v0.22.2 review). A scheduled tick must not be able to tell the world
	// what time it is.
	GameMinute int64           `json:"game_minute"`
	Automation map[string]bool `json:"automation"`
}
type ForceRequest struct {
	System     string `json:"system"`
	Steps      int64  `json:"steps"`
	GameMinute int64  `json:"game_minute"`
}

func NewRunner(databasePath, worldPath string) (*Runner, error) {
	r := &Runner{DatabasePath: databasePath, Catalog: Catalog{Locations: map[string]Location{}, NPCs: map[string]NPC{}, Sects: map[string]Sect{}, Items: map[string]Item{}, UnexpectedEvents: []UnexpectedEvent{}}}
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

// RunDue advances every simulation system that has fallen behind the canonical
// clock.
//
// The whole of "how far behind is this system, and what does that make due" is
// decided *inside* the system's own write transaction. It used to be decided
// before the transaction was opened, from a snapshot read at the top of the
// call, which is a textbook stale-decision race: two concurrent callers both
// read anchor=0, both computed due=1, and SQLite dutifully serialised two
// writes that each believed they were the only one. Under a 32-goroutine
// stress test the same interval applied seven to fourteen times. Serialising
// the writes cannot fix a decision made before the lock, so the read moved
// under it.
//
// The UPDATE is additionally guarded on the anchor value the decision was made
// from, so if this invariant is ever broken again the write fails loudly
// instead of quietly double-applying.
func (r *Runner) RunDue(req RunDueRequest) ([]Run, error) {
	conn, err := storage.Open(r.DatabasePath)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	gameMinute, err := game.CanonicalWorldGameMinute(conn)
	if err != nil {
		return nil, err
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
		run, ran, err := r.runDueSystem(conn, system, gameMinute)
		if err != nil {
			return runs, fmt.Errorf("%s: %w", system, err)
		}
		if ran {
			runs = append(runs, run)
		}
	}
	if maintenance, changed, err := r.advancedMaintenance(conn, gameMinute, req.Automation); err != nil {
		return runs, fmt.Errorf("advanced_world: %w", err)
	} else if changed {
		runs = append(runs, maintenance)
	}
	return runs, nil
}

// runDueSystem is one system's entire decide-and-apply cycle under one write
// lock. It returns ran=false when the system is not due, having written
// nothing.
func (r *Runner) runDueSystem(conn *storage.Conn, system string, gameMinute int64) (Run, bool, error) {
	if err := conn.ExecScript("BEGIN IMMEDIATE;"); err != nil {
		return Run{}, false, err
	}
	committed := false
	defer func() {
		if !committed {
			_ = conn.Rollback()
		}
	}()
	// Read under the write lock. Everything below is decided from this row.
	res, err := conn.Execute(`SELECT last_game_minute,interval_game_minutes FROM world_simulation_state WHERE system=?`, []any{system})
	if err != nil {
		return Run{}, false, err
	}
	state := firstMap(res)
	if state == nil {
		return Run{}, false, conn.Rollback()
	}
	interval := i64(state["interval_game_minutes"])
	if interval < 1 {
		interval = SystemIntervals[system]
	}
	if interval < 1 {
		return Run{}, false, conn.Rollback()
	}
	last := i64(state["last_game_minute"])
	delta := gameMinute - last
	if delta < 0 {
		delta = 0
	}
	due := delta / interval
	if due <= 0 {
		return Run{}, false, conn.Rollback()
	}
	applied := due
	if applied > 120 {
		applied = 120
	}
	processed := last + applied*interval
	summary, events, err := r.applySystem(conn, system, applied, processed)
	if err != nil {
		return Run{}, false, err
	}
	// Guarded on the anchor we decided from: if anything moved it since the
	// read, this affects no rows and the run is refused rather than doubled.
	update, err := conn.Execute(
		`UPDATE world_simulation_state SET last_game_minute=?,last_run_real=?,runs=runs+? WHERE system=? AND last_game_minute=?`,
		[]any{processed, nowFloat(), applied, system, last})
	if err != nil {
		return Run{}, false, err
	}
	if update.RowsAffected != 1 {
		return Run{}, false, fmt.Errorf("simulation anchor for %s moved during the run (expected last_game_minute=%d)", system, last)
	}
	if err := conn.Commit(); err != nil {
		return Run{}, false, err
	}
	committed = true
	if due > applied {
		summary += fmt.Sprintf("; %d interval(s) remain queued for catch-up", due-applied)
	}
	return Run{System: system, DueSteps: due, AppliedSteps: applied, Summary: summary, Events: events}, true, nil
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
	summary, events, err := r.runSystem(conn, req.System, req.Steps, req.GameMinute)
	if err != nil {
		return Run{}, err
	}
	return Run{System: req.System, DueSteps: req.Steps, AppliedSteps: req.Steps, Summary: summary, Events: events}, nil
}

// runSystem is the GM Force path: apply N steps and stamp the anchor at a
// caller-chosen minute, in its own transaction. RunDue does not use it - a
// scheduled tick has to decide how many steps are due under the same lock that
// applies them, which runDueSystem does.
func (r *Runner) runSystem(conn *storage.Conn, system string, steps, gameMinute int64) (string, []SpawnedWorldEvent, error) {
	if err := conn.ExecScript("BEGIN IMMEDIATE;"); err != nil {
		return "", nil, err
	}
	ok := false
	defer func() {
		if !ok {
			_ = conn.Rollback()
		}
	}()
	summary, events, err := r.applySystem(conn, system, steps, gameMinute)
	if err != nil {
		return "", nil, err
	}
	_, err = conn.Execute(`UPDATE world_simulation_state SET last_game_minute=?,last_run_real=?,runs=runs+? WHERE system=?`, []any{gameMinute, nowFloat(), steps, system})
	if err != nil {
		return "", nil, err
	}
	if err = conn.Commit(); err != nil {
		return "", nil, err
	}
	ok = true
	return summary, events, nil
}

// applySystem runs one system's mutations. The caller owns the transaction.
func (r *Runner) applySystem(conn *storage.Conn, system string, steps, gameMinute int64) (string, []SpawnedWorldEvent, error) {
	var summary string
	var events []SpawnedWorldEvent
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
	case "autonomous_world_events":
		summary, events, err = r.autonomousWorldEvents(conn, steps, gameMinute)
	default:
		err = fmt.Errorf("unknown system %s", system)
	}
	if err != nil {
		return "", nil, err
	}
	return summary, events, nil
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
	deathRes, err := conn.Execute(`SELECT l.npc_name,l.birth_game_minute,l.age_at_creation_years,l.natural_lifespan_years,c.* FROM npc_life_state l JOIN npc_civilization_state c ON c.npc_name=l.npc_name WHERE c.status='alive'`, nil)
	if err != nil {
		return "", err
	}
	deaths := []map[string]any{}
	for _, row := range maps(deathRes) {
		subject := lifespanmodel.Subject{
			RealmIndex:         i64(row["realm_index"]),
			Phase:              i64(row["phase"]),
			NaturalYears:       i64(row["natural_lifespan_years"]),
			BirthGameMinute:    i64(row["birth_game_minute"]),
			AgeAtCreationYears: i64(row["age_at_creation_years"]),
		}
		if lifespanmodel.OldAgeExpired(subject, gm) {
			deaths = append(deaths, row)
		}
	}
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

func simTableExists(conn *storage.Conn, name string) bool {
	res, err := conn.Execute(`SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1`, []any{name})
	return err == nil && len(res.Rows) > 0
}

func simMap(v any) map[string]any {
	if m, ok := v.(map[string]any); ok {
		return m
	}
	return map[string]any{}
}

func simFloat(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case int64:
		return float64(x)
	case int:
		return float64(x)
	default:
		var f float64
		_, _ = fmt.Sscan(fmt.Sprint(v), &f)
		return f
	}
}

func simClampFloat(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func stringIn(values []string, needle string) bool {
	for _, v := range values {
		if v == needle {
			return true
		}
	}
	return false
}

func (r *Runner) applyAutonomousWorldEffect(conn *storage.Conn, event UnexpectedEvent, location string, gm int64, now float64) ([]string, error) {
	effect := event.WorldEffect
	if len(effect) == 0 {
		return nil, nil
	}
	impacts := []string{}
	history := strings.TrimSpace(fmt.Sprint(effect["history"]))
	if history == "" || history == "<nil>" {
		history = event.Title + " changed the region."
	}
	sev := clamp(event.Severity, 1, 10)
	region := simMap(effect["region"])
	if len(region) > 0 && simTableExists(conn, "civilization_regions") {
		res, err := conn.Execute(`SELECT population,prosperity,security,spirit_resources,food_supply,migration_pressure,unrest FROM civilization_regions WHERE location=?`, []any{location})
		if err != nil {
			return nil, err
		}
		if len(res.Rows) > 0 {
			row := res.Rows[0]
			pct := simClampFloat(simFloat(region["population_percent"]), -25, 25)
			pop := int64(math.Round(float64(i64(row[0])) * (1 + pct/100)))
			if pop < 0 {
				pop = 0
			}
			pros := clamp(i64(row[1])+i64(region["prosperity"]), 0, 100)
			sec := clamp(i64(row[2])+i64(region["security"]), 0, 100)
			spirit := clamp(i64(row[3])+i64(region["spirit_resources"]), 0, 100)
			food := clamp(i64(row[4])+i64(region["food_supply"]), 0, 100)
			migration := clamp(i64(row[5])+i64(region["migration_pressure"]), 0, 100)
			unrest := clamp(i64(row[6])+i64(region["unrest"]), 0, 100)
			if _, err = conn.Execute(`UPDATE civilization_regions SET population=?,prosperity=?,security=?,spirit_resources=?,food_supply=?,migration_pressure=?,unrest=?,last_game_minute=?,updated_at=? WHERE location=?`, []any{pop, pros, sec, spirit, food, migration, unrest, gm, now, location}); err != nil {
				return nil, err
			}
			if simTableExists(conn, "civilization_events") {
				if _, err = conn.Execute(`INSERT INTO civilization_events(location,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)`, []any{location, history, sev, gm, now}); err != nil {
					return nil, err
				}
			}
			impacts = append(impacts, "regional population, security, resources, or unrest changed")
		}
	}
	market := simMap(effect["market"])
	if len(market) > 0 && simTableExists(conn, "economy_markets") {
		res, err := conn.Execute(`UPDATE economy_markets SET supply=MIN(9999,MAX(1,supply+?)),demand=MIN(500,MAX(1,demand+?)),price_index=MIN(5.0,MAX(0.25,price_index+?)),updated_at=? WHERE location=?`, []any{clamp(i64(market["supply"]), -200, 200), clamp(i64(market["demand"]), -200, 200), simClampFloat(simFloat(market["price_index"]), -1.5, 1.5), now, location})
		if err != nil {
			return nil, err
		}
		if res.RowsAffected > 0 {
			if simTableExists(conn, "economy_events") {
				if _, err = conn.Execute(`INSERT INTO economy_events(location,item_id,event_text,game_minute,created_at) VALUES(?,NULL,?,?,?)`, []any{location, history, gm, now}); err != nil {
					return nil, err
				}
			}
			impacts = append(impacts, "local supply, demand, and prices shifted")
		}
	}
	sect := simMap(effect["sect"])
	if len(sect) > 0 && simTableExists(conn, "sect_politics_state") {
		if _, err := conn.Execute(`UPDATE sect_politics_state SET influence=MIN(100,MAX(0,influence+?)),cohesion=MIN(100,MAX(0,cohesion+?)),resources=MIN(100,MAX(0,resources+?)),recruitment_pressure=MIN(100,MAX(0,recruitment_pressure+?)),doctrine_pressure=MIN(100,MAX(0,doctrine_pressure+?)),updated_at=?`, []any{i64(sect["influence"]), i64(sect["cohesion"]), i64(sect["resources"]), i64(sect["recruitment_pressure"]), i64(sect["doctrine_pressure"]), now}); err != nil {
			return nil, err
		}
		rows, err := conn.Execute(`SELECT sect_name FROM sect_politics_state ORDER BY sect_name`, nil)
		if err != nil {
			return nil, err
		}
		if simTableExists(conn, "sect_politics_events") {
			for _, row := range rows.Rows {
				if len(row) > 0 {
					if _, err = conn.Execute(`INSERT INTO sect_politics_events(sect_name,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)`, []any{fmt.Sprint(row[0]), history, sev, gm, now}); err != nil {
						return nil, err
					}
				}
			}
		}
		if len(rows.Rows) > 0 {
			impacts = append(impacts, "sect influence, cohesion, resources, or recruitment pressure changed")
		}
	}
	return impacts, nil
}

func (r *Runner) autonomousWorldEvents(conn *storage.Conn, steps, gm int64) (string, []SpawnedWorldEvent, error) {
	_ = steps
	if !simTableExists(conn, "world_events") {
		return "world-event table unavailable", nil, nil
	}
	roll, err := gamerng.Intn(100)
	if err != nil {
		return "", nil, err
	}
	if roll >= 45 {
		return "no autonomous world event manifested", nil, nil
	}
	type candidate struct {
		location string
		event    UnexpectedEvent
	}
	candidates := []candidate{}
	for location, loc := range r.Catalog.Locations {
		if loc.Private || strings.HasPrefix(location, "abode:") || strings.HasPrefix(location, "personal_world:") || (loc.AuctionHouse != nil && strings.TrimSpace(fmt.Sprint(loc.AuctionHouse)) != "") {
			continue
		}
		for _, event := range r.Catalog.UnexpectedEvents {
			if event.Kind != "world_event" {
				continue
			}
			if len(event.Locations) > 0 && !stringIn(event.Locations, location) {
				continue
			}
			if len(event.Worlds) > 0 && !stringIn(event.Worlds, loc.World) {
				continue
			}
			weight := event.Weight
			if weight < 1 {
				weight = 1
			}
			for i := 0; i < weight; i++ {
				candidates = append(candidates, candidate{location, event})
			}
		}
	}
	if len(candidates) == 0 {
		return "no eligible autonomous world events", nil, nil
	}
	idx, err := gamerng.Intn(len(candidates))
	if err != nil {
		return "", nil, err
	}
	pick := candidates[idx]
	now := nowFloat()
	if _, err = conn.Execute(`UPDATE world_events SET active=0 WHERE active=1 AND ends_at<=?`, []any{now}); err != nil {
		return "", nil, err
	}
	dedupe := "random:" + pick.event.ID
	existing, err := conn.Execute(`SELECT 1 FROM world_events WHERE dedupe_key=? AND location=? AND active=1 AND ends_at>? LIMIT 1`, []any{dedupe, pick.location, now})
	if err != nil {
		return "", nil, err
	}
	if len(existing.Rows) > 0 {
		return "eligible autonomous event already active; duplicate suppressed", nil, nil
	}
	rnd, err := gamerng.Intn(1_000_000_000)
	if err != nil {
		return "", nil, err
	}
	eventKey := fmt.Sprintf("auto:%s:%d:%d", pick.event.ID, gm, rnd)
	duration := pick.event.DurationHours
	if duration < 1 {
		duration = 2
	}
	ends := now + float64(duration)*3600
	payload := map[string]any{"definition_id": pick.event.ID, "category": pick.event.Category, "severity": pick.event.Severity, "consequence_text": pick.event.ConsequenceText, "player_reward": pick.event.PlayerReward, "player_effect": pick.event.PlayerEffect, "karma_delta": pick.event.KarmaDelta, "fate_delta": pick.event.FateDelta, "world_effect": pick.event.WorldEffect, "autonomous": true}
	enc, _ := json.Marshal(payload)
	if _, err = conn.Execute(`INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at) VALUES(?,?,?,?,?,?,1,?,?)`, []any{eventKey, dedupe, "random_event", pick.event.Title, pick.location, string(enc), now, ends}); err != nil {
		return "", nil, err
	}
	impacts, err := r.applyAutonomousWorldEffect(conn, pick.event, pick.location, gm, now)
	if err != nil {
		return "", nil, err
	}
	if simTableExists(conn, "world_history_events") {
		summary := fmt.Sprintf("A server-wide %s manifested at %s. %s", pick.event.Category, pick.location, pick.event.Description)
		if len(impacts) > 0 {
			summary += " Persistent effects: " + strings.Join(impacts, "; ") + "."
		}
		meta, _ := json.Marshal(map[string]any{"impacts": impacts, "severity": pick.event.Severity, "autonomous": true})
		_, err = conn.Execute(`INSERT INTO world_history_events(source_key,event_type,title,summary,significance,visibility,location,world_name,faction,actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,related_npc_name,tags,game_minute,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,'public',?,'','',?,?,?,'location',?,?,NULL,'',?,?,?, ?,?) ON CONFLICT(source_key) DO NOTHING`, []any{"world_event:" + eventKey + ":history", "world_event", pick.event.Title, summary, min64(95, 50+pick.event.Severity*5), pick.location, "world", pick.event.ID, "World phenomenon", pick.location, pick.location, "world event " + pick.event.Category + " " + pick.event.ID, gm, string(meta), now, now})
		if err != nil {
			return "", nil, err
		}
	}
	evt := SpawnedWorldEvent{EventKey: eventKey, EventID: pick.event.ID, Title: pick.event.Title, Category: pick.event.Category, Description: pick.event.Description, ConsequenceText: pick.event.ConsequenceText, Location: pick.location, Severity: pick.event.Severity, ExpiresAt: ends, Impacts: impacts}
	return "spawned autonomous world event " + pick.event.Title, []SpawnedWorldEvent{evt}, nil
}

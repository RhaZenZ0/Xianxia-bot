package simulation

// A sect keeps its people (v1.24.0).
//
// Every sect had one or two named people in the content file - an examiner, an
// elder who sponsors - and nobody else, so a player who joined found nobody to
// be their master and nobody to raise them a rank. `sect_system.population` is
// the hall the owner asked for (about twenty-five: a Sect Master, Elders, and
// Core, Inner and Outer Disciples), and this step keeps each sect topped up to
// it: once to seed a world, and after that whenever somebody dies or walks out.
//
// A generated member is an ordinary NPC from the moment they exist - the same
// registry row and two simulation rows `npcMaturation` writes - so they age,
// court, travel and are buried like anybody else, and they count toward the
// sect's tribute because `sectTribute` counts every living member. Catalogue
// people of the sect count toward the target too; the content file's examiner
// is a member, not somebody to be replaced.
//
// The total is capped as well as each rank. A promotion (`npcCareers`) moves
// somebody up and leaves a hole below, and topping up every hole would grow a
// sect by one each time anybody was raised; filling only while the sect is
// short of its whole target keeps the hall the size the content names.

import (
	"fmt"
	"sort"
	"strings"

	"xianxia/core/internal/game"
	lifespanmodel "xianxia/core/internal/lifespan"
	"xianxia/core/internal/storage"
)

// sectPopulationRanks are the ranks a hall is made of, highest first: the order
// a sect is filled in, so the Sect Master is never the slot a cap cuts off.
var sectPopulationRanks = []string{"Sect Master", "Elder", "Core Disciple", "Inner Disciple", "Outer Disciple"}

type sectPopulationRule struct {
	Ranks      map[string]int64
	Offset     map[string][2]int64
	AtGate     map[string]bool
	MaxPerTick int64
	Surnames   []string
	Given      []string
}

func (r *Runner) sectPopulationRule() (sectPopulationRule, bool) {
	raw, _ := r.World.SectSystem["population"].(map[string]any)
	if raw == nil {
		return sectPopulationRule{}, false
	}
	rule := sectPopulationRule{Ranks: map[string]int64{}, Offset: map[string][2]int64{}, AtGate: map[string]bool{}}
	ranks, _ := raw["ranks"].(map[string]any)
	for rank, n := range ranks {
		if v := i64(n); v > 0 {
			rule.Ranks[rank] = v
		}
	}
	offsets, _ := raw["realm_offset"].(map[string]any)
	for rank, v := range offsets {
		pair, _ := v.([]any)
		if len(pair) == 2 {
			lo, hi := i64(pair[0]), i64(pair[1])
			if hi < lo {
				lo, hi = hi, lo
			}
			rule.Offset[rank] = [2]int64{lo, hi}
		}
	}
	gate, _ := raw["at_gate"].([]any)
	for _, v := range gate {
		rule.AtGate[fmt.Sprint(v)] = true
	}
	rule.MaxPerTick = i64(raw["max_new_per_tick"])
	for _, pool := range []struct {
		key string
		dst *[]string
	}{{"surnames", &rule.Surnames}, {"given_names", &rule.Given}} {
		list, _ := raw[pool.key].([]any)
		for _, v := range list {
			if s := strings.TrimSpace(fmt.Sprint(v)); s != "" {
				*pool.dst = append(*pool.dst, s)
			}
		}
	}
	if len(rule.Ranks) == 0 || len(rule.Surnames) == 0 || len(rule.Given) == 0 || rule.MaxPerTick <= 0 {
		return sectPopulationRule{}, false
	}
	return rule, true
}

// worldFloorRealm is the first realm of a world - where a sect's realm offsets
// are measured from, so a Spiritual World Outer Disciple stands at realm 8 and
// not at Body Tempering.
func (r *Runner) worldFloorRealm(world string) int64 {
	for i, realm := range r.World.Realms {
		if realm.World == world {
			return int64(i)
		}
	}
	return 0
}

// sectPopulation tops every sect with a gate up to the content's hall. It
// returns how many people it made. One person refused (a name taken, a row a
// constraint will not hold) costs that person, never the tick: one system's
// error ends every system ordered after it (rc.28).
func (r *Runner) sectPopulation(conn *storage.Conn, gm int64) (int64, error) {
	rule, ok := r.sectPopulationRule()
	if !ok || !simTableExists(conn, "npc_registry") || !simTableExists(conn, "npc_life_state") {
		return 0, nil
	}
	target := int64(0)
	for _, n := range rule.Ranks {
		target += n
	}
	sects := make([]string, 0, len(r.World.Sects))
	for name := range r.World.Sects {
		if game.SectGate(r.World, name) != "" {
			sects = append(sects, name)
		}
	}
	sort.Strings(sects)
	now := nowFloat()
	made := int64(0)
	for _, sect := range sects {
		if made >= rule.MaxPerTick {
			break
		}
		gate, home := game.SectGate(r.World, sect), game.SectHome(r.World, sect)
		world := "Mortal World"
		if loc, ok := r.World.Locations[gate]; ok && strings.TrimSpace(loc.World) != "" {
			world = loc.World
		}
		floor := r.worldFloorRealm(world)
		have, total, err := sectMembersByRank(conn, sect)
		if err != nil {
			continue
		}
		for _, rank := range sectPopulationRanks {
			want := rule.Ranks[rank]
			for slot := int64(0); have[rank] < want && total < target && made < rule.MaxPerTick && slot < want*4; slot++ {
				name := r.freeSectMemberName(conn, rule, sect, rank, slot)
				if name == "" {
					break
				}
				place := home
				if rule.AtGate[rank] {
					place = gate
				}
				off := rule.Offset[rank]
				realmIndex := floor + off[0]
				if span := off[1] - off[0]; span > 0 {
					realmIndex += int64(hash64(name, "sect_realm") % uint64(span+1))
				}
				if last := int64(len(r.World.Realms)) - 1; realmIndex > last {
					realmIndex = last
				}
				phase := int64(1 + hash64(name, "sect_phase")%9)
				if r.makeSectMember(conn, name, sect, rank, place, world, realmIndex, phase, gm, now) {
					have[rank]++
					total++
					made++
				}
			}
		}
	}
	return made, nil
}

// sectMembersByRank counts a sect's living people by the rank they hold, and
// the whole of them (every rank, Grand Elders and joiners included).
func sectMembersByRank(conn *storage.Conn, sect string) (map[string]int64, int64, error) {
	res, err := conn.Execute(`SELECT l.sect_rank, COUNT(*) FROM npc_civilization_state c
        JOIN npc_life_state l ON l.npc_name=c.npc_name
        WHERE c.faction=? AND c.status='alive' GROUP BY l.sect_rank`, []any{sect})
	if err != nil {
		return nil, 0, err
	}
	have := map[string]int64{}
	total := int64(0)
	for _, row := range res.Rows {
		n := i64(row[1])
		have[fmt.Sprint(row[0])] += n
		total += n
	}
	return have, total, nil
}

// freeSectMemberName walks the two name lists from a hash of the sect, rank and
// slot until it finds a name nobody in the world carries. "" when the walk finds
// none, which leaves the slot for a later tick rather than doubling a name.
func (r *Runner) freeSectMemberName(conn *storage.Conn, rule sectPopulationRule, sect, rank string, slot int64) string {
	n := uint64(len(rule.Surnames) * len(rule.Given))
	start := hash64(sect, rank, fmt.Sprint(slot))
	descendants := simTableExists(conn, "npc_descendants")
	for i := uint64(0); i < n && i < 512; i++ {
		k := (start + i*7919) % n
		name := rule.Surnames[k/uint64(len(rule.Given))] + " " + rule.Given[k%uint64(len(rule.Given))]
		if _, taken := r.World.NPCs[name]; taken {
			continue
		}
		query := `SELECT 1 FROM npc_registry WHERE name=? UNION ALL SELECT 1 FROM npc_civilization_state WHERE npc_name=?`
		args := []any{name, name}
		if descendants {
			query += ` UNION ALL SELECT 1 FROM npc_descendants WHERE child_name=?`
			args = append(args, name)
		}
		res, err := conn.Execute(query+` LIMIT 1`, args)
		if err != nil {
			return ""
		}
		if len(res.Rows) == 0 {
			return name
		}
	}
	return ""
}

// makeSectMember writes one person the way `npcMaturation` and bootstrap write
// everybody else: a registry row first (the catalogue wins a name), then the two
// simulation rows and the mind row. No chronicle line: a hall being filled is
// not news, and three hundred "takes their place" rows would bury every rumour
// page in the seat cities.
func (r *Runner) makeSectMember(conn *storage.Conn, name, sect, rank, place, world string, realmIndex, phase, gm int64, now float64) bool {
	person := game.GenerateNPCTraits(r.World.GeneratedTraits, name)
	person.Origin = game.NPCOriginSect
	person.Role = fmt.Sprintf("%s of the %s", rank, sect)
	person.Realm = r.realmName(realmIndex)
	person.Location = place
	person.SectAffiliation = sect
	person.SourceKey = fmt.Sprintf("sect:%s:%s", sect, name)
	registered, err := game.RegisterNPCTx(conn, person, gm)
	if err != nil || !registered {
		return false
	}
	seed := hash64(name, "sect_member")
	influence := map[string]int64{"Sect Master": 85, "Elder": 65, "Core Disciple": 45, "Inner Disciple": 25, "Outer Disciple": 10}[rank]
	if _, err := conn.Execute(`INSERT INTO npc_civilization_state(
        npc_name,home_location,current_location,world_name,profession,faction,
        wealth,influence,ambition,realm_index,phase,status,activity,last_game_minute,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,'alive','Keeping the sect''s hours',?,?) ON CONFLICT(npc_name) DO NOTHING`,
		[]any{name, place, place, world, person.Role, sect, int64(20 + seed%60), influence, int64(30 + (seed/7)%60),
			realmIndex, phase, gm, now}); err != nil {
		return false
	}
	naturalLife := lifespanmodel.NaturalYearsFromSeed(seed)
	age := lifespanmodel.BootstrapAge(realmIndex, phase, naturalLife, seed)
	if _, err := conn.Execute(`INSERT INTO npc_life_state(
        npc_name,birth_game_minute,age_at_creation_years,natural_lifespan_years,health,
        injury,injury_severity,sect_rank,career_progress,relationship_status,spouse_name,
        children_count,last_social_game_minute,last_cultivation_game_minute,updated_at)
        VALUES(?,?,?,?,100,'',0,?,?,'single','',0,?,?,?) ON CONFLICT(npc_name) DO NOTHING`,
		[]any{name, gm, age, naturalLife, rank, int64(seed % 31), gm, gm, now}); err != nil {
		return false
	}
	if simTableExists(conn, "npc_mind_state") {
		_, _ = conn.Execute(`INSERT INTO npc_mind_state(npc_name,current_goal,mood,focus_target,recent_event,goal_progress,last_game_minute,updated_at)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(npc_name) DO NOTHING`,
			[]any{name, person.Want, "pending", sect, "", int64(seed % 21), gm, now})
	}
	return true
}

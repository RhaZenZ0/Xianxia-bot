package simulation

// The rest of an NPC's life.
//
// The schema was built for a far livelier world than the ticks delivered.
// `npc_descendants` and `npc_disciple_bonds` had no writer at all - the second
// was even *queried* by the world status view, which therefore always came
// back empty - and `sect_rank`, `career_progress`, `children_count`, `grudge`
// and `realm_index` were columns that filled up or sat still and were read by
// nothing. Married couples never had children, so the population could only
// fall: every NPC died of old age and none were ever born.
//
// Everything here writes a column or a table that already existed. Nothing
// invents state, and each thing that happens is recorded where a player can
// find it, because a world that changes silently reads the same as one that
// does not change at all.

import (
	"fmt"
	"strings"

	"xianxia/core/internal/game"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
)

const (
	// A couple raises a few children over a long life, not a dynasty a week.
	childbirthChance    = 12
	childbirthMaxKids   = 4
	childbirthCap       = 6
	childbirthMinHealth = 45

	// Career. `career_progress` climbs to 1000 and was read by nothing.
	careerPromotionAt = 600
	careerCap         = 10

	// A breakthrough is the work of a life, and it is paid for.
	breakthroughAt     = 9 // phase
	breakthroughCost   = 120
	breakthroughChance = 18
	breakthroughCap    = 6

	// Master and disciple.
	discipleRealmGap = 4
	discipleChance   = 20
	discipleCap      = 6

	// A grudge that climbs to the top and never resolves is not a grudge.
	feudGrudge = 70
	feudChance = 25
	feudCap    = 5
	feudDeadly = 18 // of the confrontations that happen, this many end in a death
)

// sectRankLadder is the way up inside a sect. An NPC outside one is an
// "Independent Cultivator" and climbs nothing until they swear to a banner.
var sectRankLadder = []string{
	"Outer Disciple", "Inner Disciple", "Core Disciple", "Elder", "Grand Elder",
}

func nextSectRank(current string) string {
	current = strings.TrimSpace(current)
	for i, rank := range sectRankLadder {
		if rank == current {
			if i+1 < len(sectRankLadder) {
				return sectRankLadder[i+1]
			}
			return "" // already at the top
		}
	}
	return sectRankLadder[0]
}

// recordNPCHistory writes a public world-history row. Best-effort: the thing
// itself is already committed, and a tick must not fail because an old
// database has no history table.
func (r *Runner) recordNPCHistory(conn *storage.Conn, kind, key, title, summary, location, npcName string, significance, gm int64, now float64) {
	if !simTableExists(conn, "world_history_events") {
		return
	}
	_, _ = conn.Execute(`INSERT INTO world_history_events(
        source_key,event_type,title,summary,significance,visibility,location,world_name,faction,
        actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,
        related_npc_name,tags,game_minute,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?, 'public', ?,'','', 'npc',?,?, 'npc',?,?, NULL,?,?,?,'{}',?,?)
        ON CONFLICT(source_key) DO NOTHING`,
		[]any{key, kind, title, summary, significance, location,
			npcName, npcName, npcName, npcName, npcName, kind, gm, now, now})
}

// npcChildbirth gives married couples children. Without it the world is
// demographically terminal: npc_life already marries people and already kills
// them of old age, and `children_count` was only ever read - in the query that
// looks for singles to marry - never once incremented.
func (r *Runner) npcChildbirth(conn *storage.Conn, steps, gm int64) (int64, error) {
	if !simTableExists(conn, "npc_descendants") || !simTableExists(conn, "npc_life_state") {
		return 0, nil
	}
	// The spouse has to be alive as well, which nothing used to check: the
	// join is on `l.npc_name` only, so `c.status='alive'` spoke for one half
	// of the couple and a widow went on bearing a dead man's children. The
	// widowing in ReleaseNPCBondsTx also closes this, by taking her out of
	// 'married' - this is the belt to that pair of braces, because a death
	// path added later will not know to call it.
	res, err := conn.Execute(`SELECT l.npc_name,l.spouse_name,c.current_location
        FROM npc_life_state l
        JOIN npc_civilization_state c ON c.npc_name=l.npc_name
        JOIN npc_civilization_state s ON s.npc_name=l.spouse_name
        JOIN npc_life_state ls ON ls.npc_name=l.spouse_name
        WHERE c.status='alive' AND s.status='alive' AND ls.health>0
          AND l.relationship_status='married' AND l.spouse_name<>''
          AND l.health>=? AND l.children_count<?
          AND l.npc_name < l.spouse_name
        ORDER BY l.npc_name LIMIT 150`, []any{childbirthMinHealth, childbirthMaxKids})
	if err != nil {
		return 0, err
	}
	now := nowFloat()
	born := int64(0)
	for _, row := range res.Rows {
		if born >= childbirthCap {
			break
		}
		roll, err := gamerng.Intn(100)
		if err != nil {
			return born, err
		}
		if int64(roll) >= min64(60, childbirthChance*max1(min64(4, steps))) {
			continue
		}
		parentA, parentB := fmt.Sprint(row[0]), fmt.Sprint(row[1])
		location := fmt.Sprint(row[2])
		childName, err := r.freeChildName(conn, parentA)
		if err != nil || childName == "" {
			continue
		}
		root := "Mortal Root"
		if len(r.World.Roots) > 0 {
			pick, err := gamerng.Intn(len(r.World.Roots))
			if err != nil {
				return born, err
			}
			root = r.World.Roots[pick]
		}
		gender := "neutral"
		if g, err := gamerng.Intn(2); err == nil {
			gender = []string{"male", "female"}[g]
		}
		if _, err := conn.Execute(`INSERT INTO npc_descendants
            (child_name,parent_a,parent_b,birth_game_minute,gender,spiritual_root,realm_index,phase,status,generated_as_npc,created_at,updated_at)
            VALUES(?,?,?,?,?,?,0,1,'alive',0,?,?) ON CONFLICT(child_name) DO NOTHING`,
			[]any{childName, parentA, parentB, gm, gender, root, now, now}); err != nil {
			return born, err
		}
		if _, err := conn.Execute(
			`UPDATE npc_life_state SET children_count=children_count+1,last_social_game_minute=?,updated_at=? WHERE npc_name IN (?,?)`,
			[]any{gm, now, parentA, parentB}); err != nil {
			return born, err
		}
		r.recordNPCHistory(conn, "npc_birth", fmt.Sprintf("npc_birth:%s:%d", childName, gm),
			childName+" is born",
			fmt.Sprintf("%s is born to %s and %s at %s, with a %s spiritual root.",
				childName, parentA, parentB, location, root),
			location, parentA, 20, gm, now)
		born++
	}
	return born, nil
}

// freeChildName builds a child's name from the family name they are born to
// and a given name from the world's own pool, and makes sure nobody already
// carries it - `npc_descendants.child_name` is UNIQUE, and a name collision
// with a catalogue NPC would be worse than a refused birth.
func (r *Runner) freeChildName(conn *storage.Conn, parent string) (string, error) {
	surname := parent
	if parts := strings.Fields(parent); len(parts) > 0 {
		surname = parts[0]
	}
	pool := r.World.EventSites.NamePool
	if len(pool) == 0 {
		return "", nil
	}
	start, err := gamerng.Intn(len(pool))
	if err != nil {
		return "", err
	}
	for i := 0; i < len(pool); i++ {
		entry := pool[(start+i)%len(pool)]
		given := entry
		if parts := strings.Fields(entry); len(parts) > 1 {
			given = strings.Join(parts[1:], " ")
		}
		candidate := surname + " " + given
		if _, taken := r.World.NPCs[candidate]; taken {
			continue
		}
		res, err := conn.Execute(`SELECT 1 FROM npc_descendants WHERE child_name=?
            UNION ALL SELECT 1 FROM npc_civilization_state WHERE npc_name=? LIMIT 1`,
			[]any{candidate, candidate})
		if err != nil {
			return "", err
		}
		if len(res.Rows) == 0 {
			return candidate, nil
		}
	}
	return "", nil
}

// npcCareers spends career_progress, which climbed to its ceiling and was read
// by nothing. Inside a sect it buys the next rank; outside one it is a
// cultivator making a name for themselves, which is influence.
func (r *Runner) npcCareers(conn *storage.Conn, gm int64) (int64, error) {
	if !simTableExists(conn, "npc_life_state") {
		return 0, nil
	}
	res, err := conn.Execute(`SELECT l.npc_name,l.sect_rank,c.faction,c.current_location,c.influence
        FROM npc_life_state l JOIN npc_civilization_state c ON c.npc_name=l.npc_name
        WHERE c.status='alive' AND l.career_progress>=? ORDER BY l.career_progress DESC LIMIT 60`,
		[]any{careerPromotionAt})
	if err != nil {
		return 0, err
	}
	now := nowFloat()
	promoted := int64(0)
	for _, row := range res.Rows {
		if promoted >= careerCap {
			break
		}
		name, rank := fmt.Sprint(row[0]), fmt.Sprint(row[1])
		faction, location := fmt.Sprint(row[2]), fmt.Sprint(row[3])
		inSect := faction != "" && faction != "Independent"
		if !inSect {
			// No banner to climb: the work still buys a reputation.
			if _, err := conn.Execute(`UPDATE npc_civilization_state SET influence=MIN(999,influence+3),updated_at=? WHERE npc_name=?`,
				[]any{now, name}); err != nil {
				return promoted, err
			}
			if _, err := conn.Execute(`UPDATE npc_life_state SET career_progress=0,updated_at=? WHERE npc_name=?`,
				[]any{now, name}); err != nil {
				return promoted, err
			}
			continue
		}
		next := nextSectRank(rank)
		if next == "" {
			// At the top of the ladder the work becomes influence instead.
			if _, err := conn.Execute(`UPDATE npc_civilization_state SET influence=MIN(999,influence+2),updated_at=? WHERE npc_name=?`,
				[]any{now, name}); err != nil {
				return promoted, err
			}
			if _, err := conn.Execute(`UPDATE npc_life_state SET career_progress=0,updated_at=? WHERE npc_name=?`,
				[]any{now, name}); err != nil {
				return promoted, err
			}
			continue
		}
		if _, err := conn.Execute(`UPDATE npc_life_state SET sect_rank=?,career_progress=0,updated_at=? WHERE npc_name=?`,
			[]any{next, now, name}); err != nil {
			return promoted, err
		}
		if _, err := conn.Execute(`UPDATE npc_civilization_state SET influence=MIN(999,influence+5),activity=?,updated_at=? WHERE npc_name=?`,
			[]any{"Newly raised to " + next, now, name}); err != nil {
			return promoted, err
		}
		r.recordNPCHistory(conn, "sect_promotion", fmt.Sprintf("sect_rank:%s:%s:%d", faction, name, gm),
			name+" is raised to "+next,
			fmt.Sprintf("%s is raised to %s of %s.", name, next, faction),
			location, name, 30, gm, now)
		promoted++
	}
	return promoted, nil
}

// npcBreakthroughs is the one thing an NPC could never do: cross a realm.
// `phase` crept to nine and stopped there forever, because `realm_index` was
// fixed at bootstrap. It is paid for in wealth, which is the first thing in
// this simulation that wealth has ever been for.
//
// The ascension gate is the exception (v1.0.0-rc.44). Wealth and health carried
// one of the world's own people across a world boundary exactly as they carried
// them across an ordinary realm - so the tick walked NPCs out of the Mortal
// World at realm 7 while a player standing at the same stage had to survive
// three waves of heavenly lightning to do it. The heavens do not hold two
// standards. An NPC is refused at a gate realm, and there is no
// `tribulation.attempt` for them to answer with: a tribulation is three rolls
// against a named character's attributes and an NPC has none of that.
//
// What opens it is a player going first. A cultivator who survives the storm
// and anchors the seam where it fell has torn a passage out of that world, and
// the people whose cultivation is near theirs follow them up it - the same
// reach `game.NPCMayCross` applies to walking through the gate, applied here to
// the breakthrough. Until somebody goes first, the top of the world is the top
// of the world.
func (r *Runner) npcBreakthroughs(conn *storage.Conn, gm int64) (int64, error) {
	if !simTableExists(conn, "npc_life_state") {
		return 0, nil
	}
	// The seams, loaded once for the same reason the travel step loads them
	// once: single figures of gates against hundreds of people.
	gates, err := game.OpenCrossings(conn)
	if err != nil {
		return 0, err
	}
	reach := r.World.WorldCrossing.NPCCrossingRealmReach
	if reach <= 0 {
		reach = defaultNPCCrossingRealmReach
	}
	res, err := conn.Execute(`SELECT c.npc_name,c.realm_index,c.wealth,c.current_location,c.ambition,l.health,c.profession
        FROM npc_civilization_state c JOIN npc_life_state l ON l.npc_name=c.npc_name
        WHERE c.status='alive' AND c.phase>=? AND c.wealth>=? AND l.health>=60
        ORDER BY c.ambition DESC,c.npc_name LIMIT 60`, []any{breakthroughAt, breakthroughCost})
	if err != nil {
		return 0, err
	}
	now := nowFloat()
	crossed := int64(0)
	for _, row := range res.Rows {
		if crossed >= breakthroughCap {
			break
		}
		roll, err := gamerng.Intn(100)
		if err != nil {
			return crossed, err
		}
		if int64(roll) >= breakthroughChance {
			continue
		}
		name, realm := fmt.Sprint(row[0]), i64(row[1])
		location := fmt.Sprint(row[3])
		if realm >= 31 {
			continue
		}
		// Talent first, because it is the thing that decides most lives. Most
		// of the world cannot climb at all and the rest stop somewhere; wealth
		// and health were the only two questions asked before, and both are
		// things a porter can have.
		if ceiling := r.npcTalentCeiling(name, fmt.Sprint(row[6])); realm >= ceiling {
			if _, err := conn.Execute(
				`UPDATE npc_civilization_state SET activity='Has gone as far as their talent allows',last_game_minute=?,updated_at=? WHERE npc_name=? AND activity<>'Has gone as far as their talent allows'`,
				[]any{gm, now, name}); err != nil {
				return crossed, err
			}
			continue
		}
		// The gate. Wealth buys an ordinary realm and buys nothing here, and
		// most of the world stays under it: a seam is cut to one cultivator's
		// measure, so the people near that measure follow and everybody else
		// stands where they have always stood. Being stuck is said out loud
		// rather than left as an absence - `activity` is what `/civilization`
		// and the GM's NPC card read, so a world full of people waiting at the
		// top of it looks like one.
		if game.IsWorldCrossingRealm(realm) && !anySeamAdmits(gates, realm, reach) {
			if _, err := conn.Execute(
				`UPDATE npc_civilization_state SET activity='Stalled at the ascension gate',last_game_minute=?,updated_at=? WHERE npc_name=? AND activity<>'Stalled at the ascension gate'`,
				[]any{gm, now, name}); err != nil {
				return crossed, err
			}
			continue
		}
		realmName := fmt.Sprintf("realm %d", realm+1)
		if int(realm)+1 < len(r.World.Realms) {
			realmName = r.World.Realms[realm+1].Name
		}
		if _, err := conn.Execute(`UPDATE npc_civilization_state
            SET realm_index=realm_index+1,phase=1,wealth=MAX(0,wealth-?),activity='Consolidating a new realm',
                last_game_minute=?,updated_at=? WHERE npc_name=?`,
			[]any{breakthroughCost, gm, now, name}); err != nil {
			return crossed, err
		}
		r.recordNPCHistory(conn, "npc_breakthrough", fmt.Sprintf("npc_breakthrough:%s:%d:%d", name, realm+1, gm),
			name+" breaks through to "+realmName,
			fmt.Sprintf("%s has crossed into %s at %s.", name, realmName, location),
			location, name, 55, gm, now)
		crossed++
	}
	return crossed, nil
}

// npcDiscipleBonds fills a table that had no writer while the world status
// view queried it - so "who is whose disciple" was a question the world could
// be asked and always answered empty.
func (r *Runner) npcDiscipleBonds(conn *storage.Conn, gm int64) (int64, error) {
	if !simTableExists(conn, "npc_disciple_bonds") || !simTableExists(conn, "npc_civilization_state") {
		return 0, nil
	}
	// End bonds whose master or disciple has died.
	now := nowFloat()
	if _, err := conn.Execute(`UPDATE npc_disciple_bonds SET status='ended',ended_game_minute=?,reason='the master is dead',updated_at=?
        WHERE status='active' AND master_name IN (SELECT npc_name FROM npc_civilization_state WHERE status<>'alive')`,
		[]any{gm, now}); err != nil {
		return 0, err
	}
	if _, err := conn.Execute(`UPDATE npc_disciple_bonds SET status='ended',ended_game_minute=?,reason='the disciple is dead',updated_at=?
        WHERE status='active' AND disciple_name IN (SELECT npc_name FROM npc_civilization_state WHERE status<>'alive')`,
		[]any{gm, now}); err != nil {
		return 0, err
	}

	res, err := conn.Execute(`SELECT a.npc_name,a.realm_index,b.npc_name,b.realm_index,a.current_location,a.faction
        FROM npc_civilization_state a JOIN npc_civilization_state b
          ON b.current_location=a.current_location AND b.npc_name<>a.npc_name
        WHERE a.status='alive' AND b.status='alive' AND a.realm_index-b.realm_index>=?
          AND NOT EXISTS (SELECT 1 FROM npc_disciple_bonds d WHERE d.disciple_name=b.npc_name AND d.status='active')
        ORDER BY a.realm_index DESC,a.npc_name,b.npc_name LIMIT 80`, []any{discipleRealmGap})
	if err != nil {
		return 0, err
	}
	formed := int64(0)
	taken := map[string]bool{}
	for _, row := range res.Rows {
		if formed >= discipleCap {
			break
		}
		master, disciple := fmt.Sprint(row[0]), fmt.Sprint(row[2])
		if taken[disciple] {
			continue
		}
		roll, err := gamerng.Intn(100)
		if err != nil {
			return formed, err
		}
		if int64(roll) >= discipleChance {
			continue
		}
		location := fmt.Sprint(row[4])
		if _, err := conn.Execute(`INSERT INTO npc_disciple_bonds(master_name,disciple_name,status,started_game_minute,reason,updated_at)
            VALUES(?,?,'active',?,'taken as a personal disciple',?)
            ON CONFLICT(master_name,disciple_name) DO UPDATE SET status='active',ended_game_minute=NULL,updated_at=excluded.updated_at`,
			[]any{master, disciple, gm, now}); err != nil {
			return formed, err
		}
		taken[disciple] = true
		r.recordNPCHistory(conn, "npc_discipleship", fmt.Sprintf("npc_disciple:%s:%s:%d", master, disciple, gm),
			master+" takes "+disciple+" as a disciple",
			fmt.Sprintf("%s has taken %s as a personal disciple at %s.", master, disciple, location),
			location, master, 35, gm, now)
		formed++
	}
	return formed, nil
}

// npcFeuds settles grudges. `grudge` climbed to a hundred and nothing ever
// happened - the only death in this world was old age. Now a grudge that has
// run its course is answered, usually with an injury and occasionally with a
// killing, and either way the world hears about it.
func (r *Runner) npcFeuds(conn *storage.Conn, gm int64) (int64, int64, error) {
	if !simTableExists(conn, "npc_social_relations") || !simTableExists(conn, "npc_life_state") {
		return 0, 0, nil
	}
	res, err := conn.Execute(`SELECT s.npc_a,s.npc_b,s.grudge,a.current_location,a.realm_index,b.realm_index
        FROM npc_social_relations s
        JOIN npc_civilization_state a ON a.npc_name=s.npc_a
        JOIN npc_civilization_state b ON b.npc_name=s.npc_b
        WHERE s.status='active' AND s.grudge>=? AND a.status='alive' AND b.status='alive'
        ORDER BY s.grudge DESC LIMIT 40`, []any{feudGrudge})
	if err != nil {
		return 0, 0, err
	}
	now := nowFloat()
	fought, killed := int64(0), int64(0)
	for _, row := range res.Rows {
		if fought >= feudCap {
			break
		}
		roll, err := gamerng.Intn(100)
		if err != nil {
			return fought, killed, err
		}
		if int64(roll) >= feudChance {
			continue
		}
		a, b := fmt.Sprint(row[0]), fmt.Sprint(row[1])
		location := fmt.Sprint(row[3])
		realmA, realmB := i64(row[4]), i64(row[5])
		winner, loser := a, b
		if realmB > realmA {
			winner, loser = b, a
		}
		deadly, err := gamerng.Intn(100)
		if err != nil {
			return fought, killed, err
		}
		if int64(deadly) < feudDeadly {
			if _, err := conn.Execute(`UPDATE npc_life_state SET health=0,death_game_minute=?,cause_of_death=?,updated_at=? WHERE npc_name=?`,
				[]any{gm, "killed by " + winner, now, loser}); err != nil {
				return fought, killed, err
			}
			if _, err := conn.Execute(`UPDATE npc_civilization_state SET status='dead',activity='Deceased',last_game_minute=?,updated_at=? WHERE npc_name=?`,
				[]any{gm, now, loser}); err != nil {
				return fought, killed, err
			}
			if err := game.ReleaseNPCBondsTx(conn, loser, gm, now); err != nil {
				return fought, killed, err
			}
			r.recordNPCHistory(conn, "npc_killing", fmt.Sprintf("npc_feud_death:%s:%s:%d", winner, loser, gm),
				winner+" kills "+loser,
				fmt.Sprintf("A long grudge between %s and %s ended at %s. %s did not walk away.", a, b, location, loser),
				location, winner, 70, gm, now)
			killed++
		} else {
			if _, err := conn.Execute(`UPDATE npc_life_state SET health=MAX(1,health-25),injury='wounded settling a grudge',injury_severity=MIN(10,injury_severity+3),updated_at=? WHERE npc_name=?`,
				[]any{now, loser}); err != nil {
				return fought, killed, err
			}
			r.recordNPCHistory(conn, "npc_duel", fmt.Sprintf("npc_feud:%s:%s:%d", a, b, gm),
				a+" and "+b+" come to blows",
				fmt.Sprintf("%s and %s settled a long grudge at %s. %s came off worst.", a, b, location, loser),
				location, winner, 40, gm, now)
		}
		// Answered, one way or the other.
		if _, err := conn.Execute(`UPDATE npc_social_relations SET grudge=MAX(0,grudge-45),last_interaction_game_minute=?,updated_at=?
            WHERE npc_a=? AND npc_b=?`, []any{gm, now, a, b}); err != nil {
			return fought, killed, err
		}
		fought++
	}
	return fought, killed, nil
}

// anySeamAdmits is whether any gate a player has torn was cut near enough to
// this cultivation to carry somebody of it through the ascension gate.
//
// The seam need not be where they are standing: a passage out of a world is a
// passage out of a world, and word of one reaches everybody in it. Where it
// stands decides who can *walk* through it (`npcTravel`); what it was cut at
// decides who may follow the cultivator who made it up past the gate.
func anySeamAdmits(gates map[string]game.Crossing, realmIndex, reach int64) bool {
	for _, gate := range gates {
		if gate.Destination == "" {
			continue
		}
		gap := realmIndex - gate.OpenedRealmIndex
		if gap < 0 {
			gap = -gap
		}
		if gap <= reach {
			return true
		}
	}
	return false
}

package simulation

// What the world's own people do when nobody has told them to (v1.0.0-rc.22).
//
// `npc_life` gave them births, marriages, careers, breakthroughs, masters and
// feuds, and `npc_consignments` gave them finds. Between those, the world was
// law-abiding by omission: `crime_records` is keyed to `characters` and always
// has been, so the only crime in this world was a player's, and the only way
// an NPC's trade showed in the world was a lot appearing on an auction floor.
// A hunter and a bandit lived exactly the same life.
//
// They do not now. A bandit robs somebody, a smuggler moves something that
// cannot be sold in daylight, and a hunter goes out after the same beasts
// `/hunt` draws on and does not always come back whole.
//
// Two rules shape all of it:
//
// Everything here writes a column or a table that already exists - wealth and
// activity on `npc_civilization_state`, health and injury on `npc_life_state`,
// grudges in `npc_social_relations`, contraband in `black_market_stock`, lots
// in `auctions`, and the record in `world_history_events`. No NPC gets a crime
// record, because that table is the player's and giving an NPC a row in it
// would mean a bounty nobody can collect and a capture nothing can perform.
// Making NPC crime *prosecutable* is a schema change and a separate decision;
// making it real is not.
//
// And the visibility ladder does the work that a crime table would otherwise
// do. A crime somebody saw is a `public` row: the town talks about it, the
// narrator's RAG can surface it, the victim holds a grudge against a name. A
// crime nobody saw is `hidden`, which by the rule this repo already keeps
// never reaches narrator RAG at all - so the world genuinely does not know who
// did it, rather than pretending not to. A killing is the one thing that is
// always noticed, because a body is; whether the culprit is named depends on
// the same roll as everything else.

import (
	"fmt"
	"sort"
	"strings"

	"xianxia/core/internal/game"
	"xianxia/core/internal/gamerng"
	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

const (
	// A trade that is already outside the law needs no excuse. Anyone else
	// needs to want something badly enough and have little enough to lose.
	crimeChanceCriminal    = 20
	crimeChanceDesperate   = 4
	crimeDesperateAmbition = 70
	crimeDesperateWealth   = 15
	// A tick is a day or so of world time, not a crime wave.
	crimeCap = 5
	// Of the crimes that happen: most are theft, some are smuggling, a few
	// turn violent, and a slice of those end in a death.
	crimeSmugglingFrom = 60
	crimeViolentFrom   = 85
	crimeFatal         = 22
	// What a theft is worth, as a share of what the victim has.
	crimeTakeShare = 4
	crimeTakeFloor = 2

	// Hunting. A hunter's trade is dangerous and that is the point: the
	// world's hunters are the reason cores reach the floors at all.
	huntChance = 16
	huntCap    = 5
	huntFatal  = 12
	// Lost by this much or worse and it was very nearly the end. It is
	// deliberately out of a competent hunter's reach: at a 1-in-70 death per
	// outing the trade empties itself of everyone who practises it inside a
	// season, which is not a living world, it is a cull. A hunter who knows
	// the woods comes home hurt; the one who does not know them is the one
	// the woods keep.
	huntMargin = 10
	huntStones = 3 // wealth a sold carcass is worth beyond the lot itself
)

// npcCriminalTrade is the trade, matched the way every other profession rule
// in this package is matched, because `profession` is free text an author
// wrote rather than an enum.
func npcCriminalTrade(profession string) bool {
	p := strings.ToLower(profession)
	for _, word := range []string{"bandit", "thief", "thug", "smuggl", "rogue", "pirate",
		"assassin", "cutpurse", "fence", "poacher", "raider", "brigand", "outlaw"} {
		if strings.Contains(p, word) {
			return true
		}
	}
	return false
}

// npcHuntingTrade is who goes out after a beast on purpose. A herbalist walks
// the same woods and is not hunting; a guard fights and does not go looking.
func npcHuntingTrade(profession string) bool {
	p := strings.ToLower(profession)
	for _, word := range []string{"hunter", "beast", "tamer", "ranger", "warden",
		"trapper", "stalker", "huntress"} {
		if strings.Contains(p, word) {
			return true
		}
	}
	return false
}

// contrabandItems is what a smuggler has to move: the things the legal floors
// will not take. Sorted, because map order must not decide what the world
// produces.
func contrabandItems(catalog worlddata.Catalog) []string {
	out := []string{}
	for id, item := range catalog.Items {
		switch strings.ToLower(strings.TrimSpace(item.LegalStatus)) {
		case "forbidden", "contraband", "restricted":
			out = append(out, id)
		}
	}
	sort.Strings(out)
	return out
}

// npcDeed is one alive NPC, as both steps below read them.
type npcDeed struct {
	name       string
	location   string
	world      string
	profession string
	wealth     int64
	ambition   int64
	realmIndex int64
	phase      int64
}

func (r *Runner) aliveNPCs(conn *storage.Conn) ([]npcDeed, error) {
	res, err := conn.Execute(`SELECT npc_name,current_location,world_name,profession,wealth,ambition,realm_index,phase
        FROM npc_civilization_state WHERE status='alive' ORDER BY npc_name`, nil)
	if err != nil {
		return nil, err
	}
	out := make([]npcDeed, 0, len(res.Rows))
	for _, row := range res.Rows {
		out = append(out, npcDeed{
			name: fmt.Sprint(row[0]), location: fmt.Sprint(row[1]), world: fmt.Sprint(row[2]),
			profession: fmt.Sprint(row[3]), wealth: i64(row[4]), ambition: i64(row[5]),
			realmIndex: i64(row[6]), phase: i64(row[7]),
		})
	}
	return out, nil
}

// recordDeed is recordNPCHistory with the two things a deed needs that a birth
// does not: a visibility that may be hidden, and a named victim.
func (r *Runner) recordDeed(
	conn *storage.Conn, kind, key, title, summary, visibility, location, world, actor, victim string,
	significance, gm int64, now float64,
) {
	if !simTableExists(conn, "world_history_events") {
		return
	}
	targetType, targetKey, targetName := "npc", victim, victim
	if victim == "" {
		targetType, targetKey, targetName = "location", location, location
	}
	_, _ = conn.Execute(`INSERT INTO world_history_events(
        source_key,event_type,title,summary,significance,visibility,location,world_name,faction,
        actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,
        related_npc_name,tags,game_minute,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,'', 'npc',?,?,?,?,?, NULL,?,?,?,'{}',?,?)
        ON CONFLICT(source_key) DO NOTHING`,
		[]any{key, kind, title, summary, significance, visibility, location, world,
			actor, actor, targetType, targetKey, targetName, actor, kind, gm, now, now})
}

// npcCrimes is the tick's criminal half. Returns crimes committed, of which
// how many were witnessed, and how many ended in a death.
func (r *Runner) npcCrimes(conn *storage.Conn, gm int64) (int64, int64, int64, error) {
	if !simTableExists(conn, "npc_civilization_state") {
		return 0, 0, 0, nil
	}
	people, err := r.aliveNPCs(conn)
	if err != nil {
		return 0, 0, 0, err
	}
	// Who is standing where, so a crowded capital is a worse place to rob
	// somebody than an empty road, and so a victim can be found at all.
	atLocation := map[string][]npcDeed{}
	for _, person := range people {
		atLocation[person.location] = append(atLocation[person.location], person)
	}
	contraband := contrabandItems(r.World)
	now := nowFloat()
	committed, witnessed, fatal := int64(0), int64(0), int64(0)
	for _, criminal := range people {
		if committed >= crimeCap {
			break
		}
		chance := int64(0)
		switch {
		case npcCriminalTrade(criminal.profession):
			chance = crimeChanceCriminal
		case criminal.ambition >= crimeDesperateAmbition && criminal.wealth <= crimeDesperateWealth:
			chance = crimeChanceDesperate
		default:
			continue
		}
		roll, err := gamerng.Intn(100)
		if err != nil {
			return committed, witnessed, fatal, err
		}
		if int64(roll) >= chance {
			continue
		}
		victim, ok := richestNeighbour(atLocation[criminal.location], criminal)
		if !ok {
			continue
		}
		kind, err := gamerng.Intn(100)
		if err != nil {
			return committed, witnessed, fatal, err
		}
		// A crowd is what makes a crime a rumour. Two people on a road is
		// nearly always unseen; a capital with a dozen NPCs in it rarely is.
		bystanders := int64(len(atLocation[criminal.location])) - 2
		if bystanders < 0 {
			bystanders = 0
		}
		seenRoll, err := gamerng.Intn(100)
		if err != nil {
			return committed, witnessed, fatal, err
		}
		seen := int64(seenRoll) < min64(85, 15+bystanders*12)

		take := max64(crimeTakeFloor, victim.wealth/crimeTakeShare)
		violent := int64(kind) >= crimeViolentFrom
		smuggling := !violent && int64(kind) >= crimeSmugglingFrom && len(contraband) > 0

		if smuggling {
			pick, err := gamerng.Intn(len(contraband))
			if err != nil {
				return committed, witnessed, fatal, err
			}
			itemID := contraband[pick]
			item := r.World.Items[itemID]
			moved, err := r.smuggleToNightMarket(conn, criminal.world, itemID, item, gm)
			if err != nil {
				return committed, witnessed, fatal, err
			}
			if !moved {
				continue
			}
			if err := r.moveWealth(conn, criminal.name, "", take, gm, now); err != nil {
				return committed, witnessed, fatal, err
			}
			if err := r.setActivity(conn, criminal.name, "Moving goods that cannot be sold in daylight", gm, now); err != nil {
				return committed, witnessed, fatal, err
			}
			name := item.Name
			if name == "" {
				name = itemID
			}
			visibility := "hidden"
			title := fmt.Sprintf("Contraband reaches the night market at %s", criminal.location)
			summary := fmt.Sprintf("%s appeared on the night market of the %s. Nobody will say who brought it.", name, criminal.world)
			if seen {
				visibility = "public"
				title = criminal.name + " is moving contraband"
				summary = fmt.Sprintf("%s was seen handing %s to the night market's people at %s.", criminal.name, name, criminal.location)
				witnessed++
			}
			r.recordDeed(conn, "npc_smuggling", fmt.Sprintf("npc_smuggling:%s:%s:%d", criminal.name, itemID, gm),
				title, summary, visibility, criminal.location, criminal.world, criminal.name, "", 40, gm, now)
			committed++
			continue
		}

		if err := r.moveWealth(conn, criminal.name, victim.name, take, gm, now); err != nil {
			return committed, witnessed, fatal, err
		}

		if violent {
			deadly, err := gamerng.Intn(100)
			if err != nil {
				return committed, witnessed, fatal, err
			}
			if int64(deadly) < crimeFatal {
				if _, err := conn.Execute(`UPDATE npc_life_state SET health=0,death_game_minute=?,cause_of_death=?,updated_at=? WHERE npc_name=?`,
					[]any{gm, "killed in a robbery", now, victim.name}); err != nil {
					return committed, witnessed, fatal, err
				}
				if _, err := conn.Execute(`UPDATE npc_civilization_state SET status='dead',activity='Deceased',last_game_minute=?,updated_at=? WHERE npc_name=?`,
					[]any{gm, now, victim.name}); err != nil {
					return committed, witnessed, fatal, err
				}
				// The dead leave a widow (rc.24), on this path as on every other.
				if err := game.ReleaseNPCBondsTx(conn, victim.name, gm, now); err != nil {
					return committed, witnessed, fatal, err
				}
				// A body is always found. Whether it is attached to a name is
				// the same roll as any other crime.
				title := fmt.Sprintf("%s is found dead at %s", victim.name, victim.location)
				summary := fmt.Sprintf("%s was found robbed and killed at %s. Nobody saw who did it.", victim.name, victim.location)
				if seen {
					title = criminal.name + " kills " + victim.name
					summary = fmt.Sprintf("%s robbed and killed %s at %s, in front of witnesses.", criminal.name, victim.name, victim.location)
					witnessed++
				}
				// Public either way: the death is known even when the culprit
				// is not, and the summary is what says which.
				r.recordDeed(conn, "npc_killing", fmt.Sprintf("npc_robbery_death:%s:%s:%d", criminal.name, victim.name, gm),
					title, summary, "public", victim.location, victim.world, criminal.name, victim.name, 72, gm, now)
				fatal++
				committed++
				if err := r.setActivity(conn, criminal.name, "Keeping out of sight", gm, now); err != nil {
					return committed, witnessed, fatal, err
				}
				continue
			}
			if _, err := conn.Execute(`UPDATE npc_life_state SET health=MAX(1,health-20),injury='beaten and robbed',injury_severity=MIN(10,injury_severity+3),updated_at=? WHERE npc_name=?`,
				[]any{now, victim.name}); err != nil {
				return committed, witnessed, fatal, err
			}
		}

		visibility := "hidden"
		title := fmt.Sprintf("%s is robbed at %s", victim.name, victim.location)
		summary := fmt.Sprintf("%s was robbed at %s and could not say by whom.", victim.name, victim.location)
		if seen {
			visibility = "public"
			title = criminal.name + " robs " + victim.name
			summary = fmt.Sprintf("%s took what %s was carrying at %s, and was seen doing it.", criminal.name, victim.name, victim.location)
			witnessed++
			// A grudge needs a name to hold. An unseen theft leaves none,
			// which is exactly why an unsolved crime stays unsolved.
			if err := r.holdGrudge(conn, victim.name, criminal.name, gm, now); err != nil {
				return committed, witnessed, fatal, err
			}
		}
		kindKey := "npc_theft"
		if violent {
			kindKey = "npc_robbery"
		}
		r.recordDeed(conn, kindKey, fmt.Sprintf("%s:%s:%s:%d", kindKey, criminal.name, victim.name, gm),
			title, summary, visibility, victim.location, victim.world, criminal.name, victim.name, 45, gm, now)
		if err := r.setActivity(conn, criminal.name, "Living on what other people were carrying", gm, now); err != nil {
			return committed, witnessed, fatal, err
		}
		committed++
	}
	return committed, witnessed, fatal, nil
}

// richestNeighbour is who is worth robbing where the criminal is standing.
func richestNeighbour(here []npcDeed, criminal npcDeed) (npcDeed, bool) {
	best, found := npcDeed{}, false
	for _, person := range here {
		if person.name == criminal.name || person.wealth <= criminal.wealth {
			continue
		}
		if !found || person.wealth > best.wealth {
			best, found = person, true
		}
	}
	return best, found
}

func (r *Runner) moveWealth(conn *storage.Conn, gainer, loser string, amount, gm int64, now float64) error {
	if loser != "" {
		if _, err := conn.Execute(`UPDATE npc_civilization_state SET wealth=MAX(0,wealth-?),last_game_minute=?,updated_at=? WHERE npc_name=?`,
			[]any{amount, gm, now, loser}); err != nil {
			return err
		}
	}
	_, err := conn.Execute(`UPDATE npc_civilization_state SET wealth=wealth+?,last_game_minute=?,updated_at=? WHERE npc_name=?`,
		[]any{amount, gm, now, gainer})
	return err
}

func (r *Runner) setActivity(conn *storage.Conn, npcName, activity string, gm int64, now float64) error {
	_, err := conn.Execute(`UPDATE npc_civilization_state SET activity=?,last_game_minute=?,updated_at=? WHERE npc_name=? AND status='alive'`,
		[]any{activity, gm, now, npcName})
	return err
}

// holdGrudge is the victim's memory of a face. The pair is stored in sorted
// order the way every other row in this table is, so npcFeuds - which is what
// eventually settles it - finds it.
func (r *Runner) holdGrudge(conn *storage.Conn, victim, criminal string, gm int64, now float64) error {
	if !simTableExists(conn, "npc_social_relations") {
		return nil
	}
	pair := []string{victim, criminal}
	sort.Strings(pair)
	_, err := conn.Execute(`INSERT INTO npc_social_relations(npc_a,npc_b,affinity,trust,grudge,relation_type,status,started_game_minute,last_interaction_game_minute,updated_at)
        VALUES(?,?,-30,-25,45,'grudge','active',?,?,?)
        ON CONFLICT(npc_a,npc_b) DO UPDATE SET grudge=MIN(100,npc_social_relations.grudge+35),
            affinity=MAX(-100,npc_social_relations.affinity-25),trust=MAX(-100,npc_social_relations.trust-20),
            status='active',last_interaction_game_minute=excluded.last_interaction_game_minute,updated_at=excluded.updated_at`,
		[]any{pair[0], pair[1], gm, gm, now})
	return err
}

// npcBeastHunts sends the world's hunters out. Returns hunts made, of which
// how many came back with something, and how many did not come back.
func (r *Runner) npcBeastHunts(conn *storage.Conn, gm int64) (int64, int64, int64, error) {
	if !simTableExists(conn, "npc_civilization_state") || !simTableExists(conn, "npc_life_state") {
		return 0, 0, 0, nil
	}
	people, err := r.aliveNPCs(conn)
	if err != nil {
		return 0, 0, 0, err
	}
	now := nowFloat()
	hunted, took, died := int64(0), int64(0), int64(0)
	for _, hunter := range people {
		if hunted >= huntCap {
			break
		}
		if !npcHuntingTrade(hunter.profession) {
			continue
		}
		roll, err := gamerng.Intn(100)
		if err != nil {
			return hunted, took, died, err
		}
		if int64(roll) >= huntChance {
			continue
		}
		quarry, err := game.RollHuntQuarry(hunter.realmIndex)
		if err != nil {
			return hunted, took, died, err
		}
		d20, err := gamerng.Intn(20)
		if err != nil {
			return hunted, took, died, err
		}
		total := int64(d20) + 1 + hunter.realmIndex*2 + hunter.phase
		margin := total - quarry.TN
		hunted++
		if margin >= 0 {
			// What they took comes off the same roster a player's hunt pays
			// out, and goes where an NPC's finds already go: the nearest floor.
			listed := false
			for _, itemID := range sortedKeys(quarry.Loot) {
				item, ok := r.World.Items[itemID]
				if !ok {
					continue
				}
				placed, err := r.consignToNearestHouse(conn, hunter.name, hunter.location, itemID, item, hunter.profession, hunter.realmIndex, gm)
				if err != nil {
					return hunted, took, died, err
				}
				listed = listed || placed
			}
			if err := r.moveWealth(conn, hunter.name, "", huntStones, gm, now); err != nil {
				return hunted, took, died, err
			}
			if err := r.setActivity(conn, hunter.name, "Back from the hills with a carcass", gm, now); err != nil {
				return hunted, took, died, err
			}
			where := "kept it"
			if listed {
				where = "sent what it was carrying to the nearest floor"
			}
			r.recordDeed(conn, "npc_beast_hunt", fmt.Sprintf("npc_hunt:%s:%s:%d", hunter.name, quarry.Name, gm),
				hunter.name+" brings down a "+quarry.Name,
				fmt.Sprintf("%s took a %s near %s and %s.", hunter.name, quarry.Name, hunter.location, where),
				"public", hunter.location, hunter.world, hunter.name, "", 40, gm, now)
			took++
			continue
		}
		deadly, err := gamerng.Intn(100)
		if err != nil {
			return hunted, took, died, err
		}
		if margin <= -huntMargin && int64(deadly) < huntFatal {
			if _, err := conn.Execute(`UPDATE npc_life_state SET health=0,death_game_minute=?,cause_of_death=?,updated_at=? WHERE npc_name=?`,
				[]any{gm, "killed hunting a " + quarry.Name, now, hunter.name}); err != nil {
				return hunted, took, died, err
			}
			if _, err := conn.Execute(`UPDATE npc_civilization_state SET status='dead',activity='Deceased',last_game_minute=?,updated_at=? WHERE npc_name=?`,
				[]any{gm, now, hunter.name}); err != nil {
				return hunted, took, died, err
			}
			// The dead leave a widow (rc.24), on this path as on every other.
			if err := game.ReleaseNPCBondsTx(conn, hunter.name, gm, now); err != nil {
				return hunted, took, died, err
			}
			r.recordDeed(conn, "npc_killing", fmt.Sprintf("npc_hunt_death:%s:%s:%d", hunter.name, quarry.Name, gm),
				"A "+quarry.Name+" kills "+hunter.name,
				fmt.Sprintf("%s went out after a %s near %s and did not come back.", hunter.name, quarry.Name, hunter.location),
				"public", hunter.location, hunter.world, hunter.name, "", 60, gm, now)
			died++
			continue
		}
		if _, err := conn.Execute(`UPDATE npc_life_state SET health=MAX(1,health-18),injury=?,injury_severity=MIN(10,injury_severity+3),updated_at=? WHERE npc_name=?`,
			[]any{"mauled by a " + quarry.Name, now, hunter.name}); err != nil {
			return hunted, took, died, err
		}
		if err := r.setActivity(conn, hunter.name, "Nursing what the hills did to them", gm, now); err != nil {
			return hunted, took, died, err
		}
		r.recordDeed(conn, "npc_beast_hunt", fmt.Sprintf("npc_hunt_failed:%s:%s:%d", hunter.name, quarry.Name, gm),
			hunter.name+" comes off worst against a "+quarry.Name,
			fmt.Sprintf("%s went out after a %s near %s and came back carrying nothing but the wound.", hunter.name, quarry.Name, hunter.location),
			"public", hunter.location, hunter.world, hunter.name, "", 35, gm, now)
	}
	return hunted, took, died, nil
}

func sortedKeys(m map[string]int64) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

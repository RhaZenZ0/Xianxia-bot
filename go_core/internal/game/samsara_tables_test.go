package game

// Every table that carries a person is held to what a rebirth does with its
// rows (v1.36.0).
//
// v1.33.0's gate holds every column of `characters` to being reset or kept by
// a rebirth. The rows of the other tables had no such gate: a captured flame
// and a built spirit sense each passed into the next life for want of a line
// in a list (v1.12.3), and about a hundred table columns name a person. This
// reads production's DDL (app/database/core.py, the way the foreign-key gate
// does), finds every column that carries a Discord id (erasureSubjectColumns,
// the erasure's own vocabulary, so `disciple_user_id` is not missed), and
// holds each table.column to one of four lists: wiped by the rebirth
// (incarnationScopedTables and incarnationScopedLinks, read off the code),
// kept because it is the soul's or the world's, settled when the body died
// (the leaving doors), or named in `samsaraOpen` with the question it waits
// on. A column in none of them fails here, naming the table.

import (
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"testing"
)

// samsaraKept is the soul's and the world's: rows a rebirth leaves standing,
// with why. Keyed "table.column".
var samsaraKept = map[string]string{
	"characters.user_id":                        "the soul's row; the install rewrites it in place and the v1.33.0 column gate holds every column of it",
	"soul_legacy.user_id":                       "what samsara remembers of every life: the echoes, the legacy points, the past lives",
	"reincarnation_state.user_id":               "the wheel itself: the wait, the target world, the lives count",
	"samsara_dynasty_history.user_id":           "the soul's lineage across lives, which the dynasty quests read",
	"samsara_ancestral_leads.user_id":           "leads into the soul's own past lives",
	"samsara_investigation_quests.user_id":      "quests into the soul's own past lives",
	"samsara_dynasty_claims.user_id":            "a claim on a past life's dynasty, the soul's to press",
	"samsara_dynasty_conflicts.user_id":         "a conflict over a past life's dynasty, the soul's",
	"character_birth_family.user_id":            "rewritten by the rebirth itself: the new life's family row (INSERT ... ON CONFLICT DO UPDATE)",
	"player_scene_state.user_id":                "rewritten by the rebirth itself: the new life's scene is its birth family's seat",
	"event_log.user_id":                         "the ledger: what happened to the soul, across lives, as the engine wrote it",
	"admin_audit_log.admin_user_id":             "the GM's own trail, which a rebirth of the GM's own character cannot erase",
	"authoritative_actor_versions.actor_id":     "the protocol's optimistic-concurrency counter for the actor, which is the Discord id and not the body",
	"authoritative_action_receipts.actor_id":    "the protocol's idempotency receipts for the actor; a replayed action must still find its receipt",
	"core_request_log.actor_id":                 "the request log, operational and across lives",
	"domain_events.actor_id":                    "the domain event ledger, canon and across lives",
	"world_history_events.related_user_id":      "world canon - the battle happened, whoever the soul is now",
	"world_crossings.opened_by_user_id":         "world canon - the gate is still open and the road still used",
	"npc_graves.claimed_by_user_id":             "world canon - the grave was emptied, and says by whom",
	"world_action_events.user_id":               "the world's own record of what players did, read by the simulation's eras",
	"world_event_participation.user_id":         "the record of who took part in a world event; the event and its site are the world's",
	"world_event_actions.user_id":               "the record of what was done at a world event",
	"exploration_event_participants.user_id":    "the record of who stood in an exploration event's scene",
	"exploration_event_actions.user_id":         "the record of what was done in an exploration event's scene",
	"event_claims.user_id":                      "a claimed event reward, keyed on the event: the soul claimed it and cannot claim it twice",
	"scene_history.user_id":                     "the soul's typed words, kept for the RAG across lives as the memory seed intends",
	"rag_memories.user_id":                      "the soul's memories; awakened_memory on soul_legacy is how much of them the new life recalls",
	"npc_memory.user_id":                        "what the world's people remember of the soul, which is the point of a past life being recognised",
	"npc_player_memories.user_id":               "what one NPC remembers of the soul, read with npc_memory",
	"npc_relationships.user_id":                 "how the world's people feel about the soul; a reborn cultivator is met as a stranger by the install (karma) and remembered by the people (this)",
	"character_creation_family_options.user_id": "the offers made at creation, which expire on their own and a reborn soul never sees again",
	"quest_definitions.owner_user_id":           "authored content other players are mid-way through",
	"sect_manors.founded_by_user_id":            "a sect its remaining disciples still belong to",
	"player_families.founder_user_id":           "a family that outlives whoever founded it",
	"stall_sales.buyer_user_id":                 "the seller's own ledger: the sale happened",
	"territory_war_actions.user_id":             "a war's record - the field was taken or held",
	"sect_manor_projects.user_id":               "a sect's building work, which the sect keeps",
	"deployed_location_arrays.owner_user_id":    "a formation still standing at a place and working on everybody there",
	"character_quests.user_id":                  "the quests a soul holds are its own across lives (schema 61 migrated their terms, not their holders); a quest that names a place or a person is the soul's to finish",
	"fate_ledger.user_id":                       "the ledger of fate across lives, the soul's",
	"character_fate.user_id":                    "the soul's fate, read by the wheel",
	"character_location_discoveries.user_id":    "the map the soul has walked; a reborn cultivator with a memory seed remembers roads",
	"character_sect_discoveries.user_id":        "the sects the soul has heard of; the same memory",
	"character_item_appraisals.user_id":         "what the soul has learned to tell apart, kept with its memories",
	"disciple_requests.master_user_id":          "a request another cultivator made of this soul, which the leaving doors answer at death",
	"disciple_requests.disciple_user_id":        "a request this soul made, which the leaving doors answer at death",
}

// samsaraSettledAtDeath is state the body left open and the death closed
// before the wheel turned (resolveIncarnationEscrowTx, the one place a life
// ends), so a rebirth finds nothing of it to wipe.
var samsaraSettledAtDeath = map[string]string{
	"auctions.seller_user_id":         "the dead seller's lots are cancelled and their goods returned to the corpse (resolveDeadPlayersAuctionsTx)",
	"auctions.current_bidder_user_id": "the dead bidder's escrow is refunded and the lot reverts to no bid",
	"auction_bids.bidder_user_id":     "the bid history of lots the death already cancelled or reverted",
	"seclusion_sessions.user_id":      "the retreat ends when the body does (review finding #4)",
	"caravans.owner_key":              "the ventures on the road are lost with their owner (markPlayersCaravansLostTx)",
	"battles.user_id":                 "the battle the body died in is abandoned by the death itself (recordTrueDeathAuthoritative: UPDATE battles SET status='abandoned')",
}

// samsaraOpen is what still waits on an owner's word, with the question. An
// entry here is a decision deferred in docs/TODO.md, not a fault the gate
// forgives: each names what a reborn cultivator still finds of the last life.
var samsaraOpen = map[string]string{
	"character_qi_body.user_id":         "per-body qi: the install sets qi_max on the character row and this table's row is neither wiped nor rewritten - does the new body start it afresh?",
	"alchemy_state.user_id":             "per-body alchemy residue and furnace state, neither wiped nor rewritten",
	"alchemy_batches.user_id":           "a batch brewing in the old body's furnace",
	"wild_beast_encounters.user_id":     "an encounter the old body was in the middle of",
	"expedition_threads.user_id":        "the Discord thread of the old body's expedition",
	"sect_abodes.user_id":               "the sect residence the old body was granted; sect_membership is wiped, the residence is not",
	"sect_recommendations.user_id":      "a recommendation into a sect the old body earned",
	"sect_recruitment_attempts.user_id": "the old body's failed attempts, which pace the next",
	"player_stalls.user_id":             "the old body's stall in a city, still open",
	"stall_listings.user_id":            "goods the old body listed, still for sale",
	"stall_sales.user_id":               "the old body's sales ledger",
	"stall_card_messages.user_id":       "the stall's Discord card",
	"trade_offers.from_user_id":         "a trade the old body offered, still open",
	"trade_offers.to_user_id":           "a trade offered to the old body, still open",
	"dao_partnerships.user_a":           "a Dao partnership the old body was in",
	"dao_partnerships.user_b":           "a Dao partnership the old body was in",
	"npc_mentorships.disciple_user_id":  "a bond with a master among the sect's people; sect.ascend and the GM lever end it (v1.35.0), the rebirth does not",
	"pvp_matches.player1_user_id":       "a duel the old body was fighting",
	"pvp_matches.player2_user_id":       "a duel the old body was fighting",
	"pvp_matches.turn_user_id":          "a duel the old body was fighting, and whose turn it was",
	"pvp_matches.winner_user_id":        "a duel the old body won",
	"parties.leader_user_id":            "the party the old body led is disbanded by the rebirth (UPDATE parties SET status='disbanded'), which the gate cannot read as a wipe; named here so the rule is written down",
}

var (
	createTablePattern = regexp.MustCompile(`(?i)CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(`)
	addColumnPattern   = regexp.MustCompile(`(?i)ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)`)
	sqlCommentPattern  = regexp.MustCompile(`(?m)--[^\n]*`)
	constraintWords    = map[string]bool{"PRIMARY": true, "FOREIGN": true, "UNIQUE": true, "CHECK": true, "CONSTRAINT": true}
)

// personColumns lists every production table.column that carries a Discord
// id, read off core.py's DDL: the columns of every CREATE TABLE body (split
// at top-level commas, first word) and every ADD COLUMN.
func personColumns(t *testing.T, ddl string) []string {
	t.Helper()
	seen := map[string]bool{}
	for _, m := range createTablePattern.FindAllStringSubmatch(ddl, -1) {
		table := m[1]
		if strings.HasPrefix(table, "migrate") {
			continue // a migration's scratch copy, dropped by the same migration
		}
		// A column written under a `-- comment` line is still a column: the
		// comments go before the body is split, or the comment's first word
		// is read as the column's name and `auctions.seller_user_id`, which
		// sits under three lines about NULL, is never seen.
		body := sqlCommentPattern.ReplaceAllString(tableBody(ddl, table), "")
		depth := 0
		start := 0
		var parts []string
		for i, ch := range body {
			switch ch {
			case '(':
				depth++
			case ')':
				depth--
			case ',':
				if depth == 0 {
					parts = append(parts, body[start:i])
					start = i + 1
				}
			}
		}
		parts = append(parts, body[start:])
		for _, part := range parts {
			words := strings.Fields(part)
			if len(words) == 0 || constraintWords[strings.ToUpper(words[0])] {
				continue
			}
			if erasureSubjectColumns[words[0]] {
				seen[table+"."+words[0]] = true
			}
		}
	}
	for _, m := range addColumnPattern.FindAllStringSubmatch(ddl, -1) {
		if erasureSubjectColumns[m[2]] {
			seen[m[1]+"."+m[2]] = true
		}
	}
	// caravans.owner_key carries the player's id as text under owner_type
	// 'player' (v1.35.0 anonymises it); it is not in the erasure vocabulary
	// because the column is shared with NPC owners, so it is named by hand.
	if strings.Contains(tableBody(ddl, "caravans"), "owner_key") {
		seen["caravans.owner_key"] = true
	}
	out := make([]string, 0, len(seen))
	for key := range seen {
		out = append(out, key)
	}
	sort.Strings(out)
	return out
}

func TestEveryTableThatCarriesAPersonSaysWhatARebirthDoesWithIt(t *testing.T) {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot resolve test source path")
	}
	raw, err := os.ReadFile(filepath.Clean(filepath.Join(filepath.Dir(file), "../../../app/database/core.py")))
	if err != nil {
		t.Fatalf("production DDL: %v", err)
	}
	columns := personColumns(t, string(raw))
	if len(columns) < 100 {
		t.Fatalf("found %d table columns carrying a person; the reader is broken, not the schema", len(columns))
	}
	wiped := map[string]bool{}
	for _, table := range incarnationScopedTables {
		wiped[table+".user_id"] = true
	}
	for _, table := range incarnationScopedLaterTables {
		wiped[table+".user_id"] = true
	}
	for _, link := range incarnationScopedLinks {
		wiped[link.Table+"."+link.Column] = true
	}
	var faults []string
	named := map[string]bool{}
	for _, key := range columns {
		switch {
		case wiped[key]:
		case samsaraKept[key] != "":
			named[key] = true
		case samsaraSettledAtDeath[key] != "":
			named[key] = true
		case samsaraOpen[key] != "":
			named[key] = true
		default:
			faults = append(faults, key+": a rebirth neither wipes nor keeps it on purpose - name it in incarnationScopedTables/Links, samsaraKept, samsaraSettledAtDeath, or samsaraOpen with the question")
		}
	}
	for _, list := range []map[string]string{samsaraKept, samsaraSettledAtDeath, samsaraOpen} {
		for key, reason := range list {
			if !named[key] {
				if wiped[key] {
					faults = append(faults, key+": listed as kept, settled or open and wiped by the rebirth - one list or the other")
				} else {
					faults = append(faults, key+": listed and no production table carries it - stale")
				}
			}
			if len(strings.TrimSpace(reason)) < 20 {
				faults = append(faults, key+": listed without a reason")
			}
		}
	}
	sort.Strings(faults)
	if len(faults) > 0 {
		t.Fatalf("%d table column(s) carrying a person are not accounted for by the rebirth:\n  %s", len(faults), strings.Join(faults, "\n  "))
	}
}

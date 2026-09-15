package game

// People this world made for itself (v1.0.0-rc.27, schema 49).
//
// `npc_descendants.generated_as_npc` has existed since the NPC life cycle was
// written and is read by *nothing*: it appears in the DDL and in two INSERTs
// that hardcode it to 0. That is not an oversight, it is a missing table.
// There was nowhere to promote a child into. `catalog_npcs` is a mirror of
// `content/world.json`, rewritten from the file at every boot, so a runtime
// NPC written there is deleted by the next restart; `npc_civilization_state`
// says where somebody is and what they are doing and has no room for who they
// are. So children were named, tracked, aged - and could never be spoken to.
//
// `npc_registry` is the other half: authored state, written at runtime,
// carried in backups, and never touched by a rebuild from the content file.
// Keeping it out of the catalogue mirror is the whole safety property - a
// rebuild is an unconditional DELETE over the derived table, and this one is
// never named in that statement, so no wrong predicate can wipe the people the
// world made for itself.
//
// It lives in `game` rather than `simulation` because three different places
// make a person - the life cycle matures a descendant, a starter household
// writes its relatives, and a GM may write one by hand - and the simulation
// package already imports `game`, so the rule sits at the bottom where all of
// them can reach it instead of being written out three times.

import (
	"fmt"
	"hash/fnv"
	"strings"
	"time"

	"xianxia/core/internal/storage"
	"xianxia/core/internal/worlddata"
)

// RegisteredNPC is one person, in the shape `/talk` and the narrator read.
type RegisteredNPC struct {
	Name            string
	Origin          string
	Role            string
	Realm           string
	Personality     string
	Speech          string
	Want            string
	Fear            string
	Secret          string
	Location        string
	SectAffiliation string
	SourceKey       string
}

// Who may have made one. A value outside this set is a typo rather than a new
// kind of person, and a typo here would put somebody in the world that no
// later pass knows how to age, retire or explain.
const (
	NPCOriginDescendant  = "descendant"
	NPCOriginBirthFamily = "birth_family"
	NPCOriginEvent       = "event"
	NPCOriginGM          = "gm"
)

func validNPCOrigin(origin string) bool {
	switch origin {
	case NPCOriginDescendant, NPCOriginBirthFamily, NPCOriginEvent, NPCOriginGM:
		return true
	}
	return false
}

// RegisterNPCTx writes one person, or leaves the one already there alone.
//
// Insert-only on the identity: a second call for the same name must not
// rewrite who somebody is, because the callers are re-entrant by nature - a
// household is ensured on every character creation that picks it, and the
// maturation pass runs every tick. What a later pass *may* move is where they
// are standing, which is the one thing about them that changes.
func RegisterNPCTx(conn *storage.Conn, npc RegisteredNPC, gameMinute int64) (bool, error) {
	name := strings.TrimSpace(npc.Name)
	if name == "" || !tableExistsTx(conn, "npc_registry") {
		return false, nil
	}
	if !validNPCOrigin(npc.Origin) {
		return false, fmt.Errorf("unknown npc_registry origin %q", npc.Origin)
	}
	// A name the content file already carries is not ours to take. The
	// catalogue wins, always: two different people answering to one name is
	// worse than a birth refused, and `/talk` resolves the catalogue first.
	if tableExistsTx(conn, "catalog_npcs") {
		clash, err := conn.Execute(`SELECT 1 FROM catalog_npcs WHERE name=?`, []any{name})
		if err != nil {
			return false, err
		}
		if len(clash.Rows) > 0 {
			return false, nil
		}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	res, err := conn.Execute(`INSERT INTO npc_registry(
        name,origin,role,realm,personality,speech,want,fear,secret,location,
        sect_affiliation,source_key,created_game_minute,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO NOTHING`,
		[]any{name, npc.Origin, npc.Role, npc.Realm, npc.Personality, npc.Speech,
			npc.Want, npc.Fear, npc.Secret, npc.Location, npc.SectAffiliation,
			npc.SourceKey, maxI64(0, gameMinute), now, now})
	if err != nil {
		return false, err
	}
	return res.RowsAffected > 0, nil
}

// MoveRegisteredNPCTx keeps the registry's idea of where somebody is in step
// with the simulation's, for the surfaces that read this table rather than
// `npc_civilization_state`. Silent when they are not ours.
func MoveRegisteredNPCTx(conn *storage.Conn, name, location string) error {
	if !tableExistsTx(conn, "npc_registry") || strings.TrimSpace(name) == "" {
		return nil
	}
	_, err := conn.Execute(`UPDATE npc_registry SET location=?,updated_at=? WHERE name=?`,
		[]any{location, float64(time.Now().UnixNano()) / 1e9, name})
	return err
}

// GenerateNPCTraits gives a person prose, deterministically.
//
// Keyed off the name through fnv-1a, the same way bootstrap keys everything
// else, so the same world makes the same person twice and a replayed tick
// cannot quietly reroll somebody's character. Each field is drawn separately -
// a single index across all five pools would tie fear to personality forever
// and make the world's own people read as a handful of fixed archetypes.
//
// An empty pool yields an empty string rather than a placeholder: a narrator
// handed "" says nothing about the trait, and a narrator handed "unknown" says
// something false.
func GenerateNPCTraits(traits worlddata.GeneratedTraits, name string) RegisteredNPC {
	return RegisteredNPC{
		Name:        name,
		Role:        pickTrait(traits.Role, name, "role"),
		Personality: pickTrait(traits.Personality, name, "personality"),
		Speech:      pickTrait(traits.Speech, name, "speech"),
		Want:        pickTrait(traits.Want, name, "want"),
		Fear:        pickTrait(traits.Fear, name, "fear"),
	}
}

func pickTrait(pool []string, name, field string) string {
	if len(pool) == 0 {
		return ""
	}
	h := fnv.New64a()
	_, _ = h.Write([]byte(name))
	_, _ = h.Write([]byte{0})
	_, _ = h.Write([]byte(field))
	return pool[h.Sum64()%uint64(len(pool))]
}

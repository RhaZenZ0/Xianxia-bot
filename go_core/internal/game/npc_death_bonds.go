package game

// What a death does to the attachments the dead leave behind (v1.0.0-rc.24).
//
// Nothing in this engine had ever set `relationship_status` back from
// 'married'. The only two writes to that column marked people married, so a
// widow stayed married to a corpse for the rest of her life: she could never
// be courted again, and - because `npcChildbirth` reads the column and never
// checked the spouse's pulse - she went on bearing that dead man's children.
//
// This is exported because three separate places kill an NPC and all three owe
// the survivor the same thing: old age and a settled feud, both in the
// simulation package, and a player's blade here. The simulation package
// already imports this one, so the rule lives at the bottom where both can
// reach it rather than being written out three times.

import "xianxia/core/internal/storage"

// ReleaseNPCBondsTx frees whoever the dead were attached to.
//
// A spouse is widowed rather than made single, because 'widowed' is a status
// the courtship pairing still reads as available - they may marry again, and
// the word is the truthful one for the GM card and the narrator. A courtship
// simply ends, and the other party is single.
//
// Grudges are deliberately left untouched. A feud with the dead is precisely
// what a surviving family goes on remembering, and `npcFeuds` already refuses
// to act on a pair unless both are alive.
//
// Best-effort about the tables: a database old enough to lack them must not
// fail a combat or a tick.
func ReleaseNPCBondsTx(conn *storage.Conn, deceased string, gameMinute int64, now float64) error {
	if !tableExistsTx(conn, "npc_life_state") {
		return nil
	}
	// Keyed off whoever names the dead as their spouse, rather than off the
	// dead person's own row. Those two should agree and usually do, but only
	// one of them is the row being changed, and a widowing that depends on
	// the corpse's bookkeeping being tidy is a widowing that will one day
	// not happen.
	if _, err := conn.Execute(`UPDATE npc_life_state
        SET relationship_status='widowed',spouse_name='',last_social_game_minute=?,updated_at=?
        WHERE spouse_name=? AND relationship_status='married'`,
		[]any{gameMinute, now, deceased}); err != nil {
		return err
	}
	if !tableExistsTx(conn, "npc_social_relations") {
		return nil
	}
	if _, err := conn.Execute(`UPDATE npc_life_state
        SET relationship_status='single',spouse_name='',last_social_game_minute=?,updated_at=?
        WHERE relationship_status='courting' AND npc_name IN (
            SELECT CASE WHEN npc_a=? THEN npc_b ELSE npc_a END
            FROM npc_social_relations
            WHERE status='active' AND relation_type='courtship' AND (npc_a=? OR npc_b=?))`,
		[]any{gameMinute, now, deceased, deceased, deceased}); err != nil {
		return err
	}
	_, err := conn.Execute(`UPDATE npc_social_relations
        SET status='ended',last_interaction_game_minute=?,updated_at=?
        WHERE status='active' AND relation_type IN ('marriage','courtship') AND (npc_a=? OR npc_b=?)`,
		[]any{gameMinute, now, deceased, deceased})
	return err
}

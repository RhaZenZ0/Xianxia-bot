package game

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"xianxia/core/internal/storage"
)

func dynastyTestConn(t *testing.T) *storage.Conn {
	t.Helper()
	path := filepath.Join(t.TempDir(), "dynasty.sqlite3")
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	oldRoller := dynastyRoll2d10
	dynastyRoll2d10 = func(modifier, tn int64) (map[string]any, error) {
		total := int64(20) + modifier
		return map[string]any{
			"die1": int64(10), "die2": int64(10), "modifier": modifier, "tn": tn,
			"total": total, "margin": total - tn, "success": true, "degree": "Strong Success",
		}, nil
	}
	t.Cleanup(func() { dynastyRoll2d10 = oldRoller })
	if err := conn.ExecScript(`
		CREATE TABLE characters (
			user_id INTEGER PRIMARY KEY,
			life_status TEXT NOT NULL,
			attributes_json TEXT NOT NULL DEFAULT '{"body":10,"agility":10,"spirit":10,"insight":10,"will":10,"presence":10,"heart":10}',
			vitality INTEGER NOT NULL DEFAULT 100,
			updated_at REAL NOT NULL DEFAULT 0
		);
		CREATE TABLE birth_families (
			family_id INTEGER PRIMARY KEY,
			family_name TEXT NOT NULL,
			location TEXT NOT NULL DEFAULT 'Test City'
		);
		CREATE TABLE samsara_dynasty_history (
			history_id INTEGER PRIMARY KEY AUTOINCREMENT,
			user_id INTEGER NOT NULL,
			incarnation_number INTEGER NOT NULL,
			source_family_id INTEGER,
			source_family_name TEXT NOT NULL,
			source_family_archetype TEXT NOT NULL DEFAULT '',
			source_world TEXT NOT NULL,
			destination_family_id INTEGER,
			destination_family_name TEXT NOT NULL,
			destination_family_archetype TEXT NOT NULL DEFAULT '',
			destination_world TEXT NOT NULL,
			lineage_status TEXT NOT NULL,
			blood_continuity INTEGER NOT NULL DEFAULT 0,
			event_kind TEXT NOT NULL,
			summary TEXT NOT NULL,
			evidence_json TEXT NOT NULL DEFAULT '[]',
			investigation_level INTEGER NOT NULL DEFAULT 0,
			investigation_count INTEGER NOT NULL DEFAULT 0,
			first_discovered_game_minute INTEGER,
			last_investigated_game_minute INTEGER,
			created_game_minute INTEGER NOT NULL DEFAULT 0,
			created_at REAL NOT NULL,
			updated_at REAL NOT NULL,
			UNIQUE(user_id, incarnation_number),
			FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
			FOREIGN KEY(source_family_id) REFERENCES birth_families(family_id) ON DELETE SET NULL,
			FOREIGN KEY(destination_family_id) REFERENCES birth_families(family_id) ON DELETE SET NULL
		);
		CREATE TABLE samsara_ancestral_leads (
			lead_id INTEGER PRIMARY KEY AUTOINCREMENT,
			user_id INTEGER NOT NULL,
			history_id INTEGER NOT NULL,
			lead_kind TEXT NOT NULL,
			name TEXT NOT NULL,
			location TEXT NOT NULL,
			world_name TEXT NOT NULL,
			description TEXT NOT NULL,
			status TEXT NOT NULL DEFAULT 'hidden',
			clue_required INTEGER NOT NULL DEFAULT 1,
			danger INTEGER NOT NULL DEFAULT 0,
			evidence_weight INTEGER NOT NULL DEFAULT 10,
			retainer_name TEXT NOT NULL DEFAULT '',
			retainer_relation TEXT NOT NULL DEFAULT '',
			discovered_game_minute INTEGER,
			resolved_game_minute INTEGER,
			created_game_minute INTEGER NOT NULL DEFAULT 0,
			created_at REAL NOT NULL,
			updated_at REAL NOT NULL,
			UNIQUE(history_id,lead_kind)
		);
		CREATE TABLE samsara_investigation_quests (
			quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
			user_id INTEGER NOT NULL,
			history_id INTEGER NOT NULL,
			lead_id INTEGER NOT NULL,
			quest_kind TEXT NOT NULL,
			title TEXT NOT NULL,
			description TEXT NOT NULL,
			status TEXT NOT NULL DEFAULT 'locked',
			progress INTEGER NOT NULL DEFAULT 0,
			target INTEGER NOT NULL DEFAULT 1,
			reward_evidence INTEGER NOT NULL DEFAULT 10,
			hostile_cause INTEGER NOT NULL DEFAULT 0,
			culprit_name TEXT NOT NULL DEFAULT '',
			created_game_minute INTEGER NOT NULL DEFAULT 0,
			completed_game_minute INTEGER,
			created_at REAL NOT NULL,
			updated_at REAL NOT NULL,
			UNIQUE(history_id,quest_kind)
		);
		CREATE TABLE samsara_dynasty_claims (
			claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
			user_id INTEGER NOT NULL,
			history_id INTEGER NOT NULL,
			claim_type TEXT NOT NULL,
			dynasty_name TEXT NOT NULL,
			target_family_name TEXT NOT NULL DEFAULT '',
			target_world TEXT NOT NULL,
			status TEXT NOT NULL DEFAULT 'pending',
			legitimacy INTEGER NOT NULL DEFAULT 0,
			support INTEGER NOT NULL DEFAULT 0,
			opposition INTEGER NOT NULL DEFAULT 0,
			blood_based INTEGER NOT NULL DEFAULT 0,
			resolution TEXT NOT NULL DEFAULT '',
			created_game_minute INTEGER NOT NULL DEFAULT 0,
			resolved_game_minute INTEGER,
			created_at REAL NOT NULL,
			updated_at REAL NOT NULL,
			UNIQUE(user_id,history_id,claim_type)
		);
		CREATE TABLE samsara_dynasty_conflicts (
			conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
			user_id INTEGER NOT NULL,
			claim_id INTEGER NOT NULL UNIQUE,
			history_id INTEGER NOT NULL,
			conflict_type TEXT NOT NULL,
			opponent_name TEXT NOT NULL,
			opponent_family_name TEXT NOT NULL DEFAULT '',
			stakes TEXT NOT NULL,
			status TEXT NOT NULL DEFAULT 'active',
			player_progress INTEGER NOT NULL DEFAULT 0,
			opponent_progress INTEGER NOT NULL DEFAULT 0,
			rounds INTEGER NOT NULL DEFAULT 0,
			last_tactic TEXT NOT NULL DEFAULT '',
			outcome TEXT NOT NULL DEFAULT '',
			created_game_minute INTEGER NOT NULL DEFAULT 0,
			resolved_game_minute INTEGER,
			created_at REAL NOT NULL,
			updated_at REAL NOT NULL
		);
		INSERT INTO characters(user_id,life_status) VALUES(77,'alive');
		INSERT INTO birth_families(family_id,family_name) VALUES
			(1,'Han Family'),
			(2,'Ji Stone-Marrow House'),
			(3,'He Tide-Listening House'),
			(4,'Dugu Ruined-Constellation Successor House');
	`); err != nil {
		t.Fatal(err)
	}
	return conn
}

func TestSamsaraDynastyHistoryPersistsAndReplacementNeverBecomesBloodline(t *testing.T) {
	conn := dynastyTestConn(t)

	replacementID, err := recordSamsaraDynastyHistory(
		conn,
		77,
		2,
		1,
		2,
		100,
		"Han Family",
		"noble_martial_clan",
		"Mortal World",
		"Ji Stone-Marrow House",
		"spirit_body_house",
		"Spiritual World",
		"extinct_branch_replaced",
		"The Han-associated upper branch died out; Ji House later occupied its former local role.",
		1.0,
	)
	if err != nil {
		t.Fatal(err)
	}

	for attempt := int64(1); attempt <= maxDynastyInvestigationLevel; attempt++ {
		raw, _ := json.Marshal(map[string]any{
			"history_id":  replacementID,
			"game_minute": 100 + attempt,
		})
		mutation, investigateErr := lineageInvestigateAction(conn, 77, raw)
		if investigateErr != nil {
			t.Fatal(investigateErr)
		}
		result := mutation.Result.(map[string]any)
		if got := i64(result["investigation_level"]); got != attempt {
			t.Fatalf("attempt %d investigation_level=%d", attempt, got)
		}
		if attempt == maxDynastyInvestigationLevel {
			if got := fmt.Sprint(result["blood_continuity"]); got != "confirmed_no_blood_continuity" {
				t.Fatalf("replacement continuity=%q", got)
			}
			evidence, ok := result["evidence"].([]string)
			if !ok || len(evidence) != 3 {
				t.Fatalf("replacement evidence=%#v", result["evidence"])
			}
			if !strings.Contains(strings.ToLower(evidence[2]), "no blood continuity") {
				t.Fatalf("replacement evidence does not explicitly sever bloodline: %q", evidence[2])
			}
		}
	}

	_, err = recordSamsaraDynastyHistory(
		conn,
		77,
		3,
		2,
		3,
		200,
		"Ji Stone-Marrow House",
		"spirit_body_house",
		"Spiritual World",
		"He Tide-Listening House",
		"immortal_river_house",
		"Immortal World",
		"no_known_connection",
		"No reliable connection survives between the houses.",
		2.0,
	)
	if err != nil {
		t.Fatal(err)
	}
	_, err = recordSamsaraDynastyHistory(
		conn,
		77,
		4,
		3,
		4,
		300,
		"He Tide-Listening House",
		"immortal_river_house",
		"Immortal World",
		"Dugu Ruined-Constellation Successor House",
		"celestial_successor_house",
		"Celestial World",
		"distant_surviving_branch",
		"A distant branch survived, but inherited no automatic status.",
		3.0,
	)
	if err != nil {
		t.Fatal(err)
	}

	rows, err := conn.Execute(`
		SELECT incarnation_number,lineage_status,blood_continuity,investigation_level
		FROM samsara_dynasty_history
		WHERE user_id=77
		ORDER BY incarnation_number`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows.Rows) != 3 {
		t.Fatalf("history rows=%d want=3", len(rows.Rows))
	}
	if got := fmt.Sprint(rows.Rows[0][1]); got != "extinct_branch_replaced" {
		t.Fatalf("first lineage status=%q", got)
	}
	if got := i64(rows.Rows[0][2]); got != 0 {
		t.Fatalf("replacement blood continuity changed to %d", got)
	}
	if got := i64(rows.Rows[0][3]); got != maxDynastyInvestigationLevel {
		t.Fatalf("replacement investigation level=%d", got)
	}
	if got := i64(rows.Rows[2][2]); got != 1 {
		t.Fatalf("surviving branch blood continuity=%d want=1", got)
	}
}

func TestSamsaraDynastyEvidenceClassifiesAllLineageOutcomes(t *testing.T) {
	cases := []struct {
		status          string
		eventKind       string
		bloodContinuity bool
	}{
		{"distant_surviving_branch", "surviving_branch", true},
		{"fallen_severed_branch", "fallen_branch", true},
		{"extinct_branch_replaced", "extinction_and_replacement", false},
		{"no_known_connection", "unrelated_rebirth", false},
		{"new_mortal_incarnation", "rebirth_into_established_house", false},
	}
	for _, tc := range cases {
		if got := samsaraDynastyEventKind(tc.status); got != tc.eventKind {
			t.Fatalf("%s event kind=%q want=%q", tc.status, got, tc.eventKind)
		}
		if got := samsaraDynastyBloodContinuity(tc.status); got != tc.bloodContinuity {
			t.Fatalf("%s blood continuity=%v want=%v", tc.status, got, tc.bloodContinuity)
		}
		evidence := samsaraDynastyEvidence(tc.status, "Old House", "New House", "Mortal World", "Spiritual World")
		if len(evidence) != 3 {
			t.Fatalf("%s evidence count=%d want=3", tc.status, len(evidence))
		}
	}
}

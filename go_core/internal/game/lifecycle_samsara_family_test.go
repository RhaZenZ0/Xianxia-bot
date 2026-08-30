package game

import (
	"strings"
	"testing"
)

func TestMortalSamsaraUsesCanonicalStartingFamilies(t *testing.T) {
	allowed := map[string]struct {
		surname  string
		location string
	}{
		"martial_household":          {"Han", "Riverguard City"},
		"escort_martial_family":      {"Chen", "Four-Roads Caravan City"},
		"weaponsmith_martial_family": {"Wei", "Emberforge City"},
		"body_tempering_family":      {"Zhao", "Stoneback Mountain City"},
		"sword_hall_family":          {"Shen", "Cloudblade City"},
		"spear_guard_family":         {"Lin", "Ironbanner City"},
		"hidden_weapon_family":       {"Su", "Moonfen City"},
		"border_garrison_family":     {"Gu", "Frostwatch City"},
		"fallen_martial_clan":        {"Luo", "Ashenwall City"},
		"noble_martial_clan":         {"Qin", "Azure Crown Imperial City"},
		"alchemy_family":             {"Bai", "Jadewood Medicine City"},
	}

	for _, karma := range []int64{-1000, -250, 0, 250, 1000} {
		for i := 0; i < 100; i++ {
			family, err := generateSamsaraFamily("Mortal World", karma)
			if err != nil {
				t.Fatal(err)
			}
			want, ok := allowed[family.ID]
			if !ok {
				t.Fatalf("unexpected Mortal Samsara family id %q", family.ID)
			}
			if family.Surname != want.surname {
				t.Fatalf("%s surname=%q want=%q", family.ID, family.Surname, want.surname)
			}
			if family.Location != want.location {
				t.Fatalf("%s location=%q want=%q", family.ID, family.Location, want.location)
			}
			if family.Location == "Greenriver Town" {
				t.Fatalf("Mortal Samsara rebirth returned deprecated default origin %q", family.Location)
			}
		}
	}
}

func TestUpperWorldSamsaraUsesRealmLocalFamilies(t *testing.T) {
	mortalIDs := map[string]bool{}
	for _, family := range samsaraFamilies {
		mortalIDs[family.ID] = true
	}
	deprecated := map[string]bool{
		"Spirit Jade Rebirth Enclave":  true,
		"Nine-Heavens Rebirth Terrace": true,
		"Celestial Cradle Province":    true,
		"Greenriver Town":              true,
	}
	for _, world := range []string{"Spiritual World", "Immortal World", "Celestial World"} {
		for _, karma := range []int64{-1000, 0, 1000} {
			for i := 0; i < 50; i++ {
				family, err := generateSamsaraFamilyWithLineage(world, karma, "Qin Family", "noble_martial_clan")
				if err != nil {
					t.Fatal(err)
				}
				if mortalIDs[family.ID] {
					t.Fatalf("%s reused Mortal family archetype %q", world, family.ID)
				}
				if family.RebirthWorld != world {
					t.Fatalf("rebirth_world=%q want=%q", family.RebirthWorld, world)
				}
				if family.Location == "" || deprecated[family.Location] {
					t.Fatalf("%s returned invalid shared rebirth location %q", world, family.Location)
				}
				if family.LineageStatus == "" || family.LineageSummary == "" {
					t.Fatalf("%s missing lineage history: %+v", world, family)
				}
				if family.FamilyName == "Qin Family" {
					t.Fatalf("%s copied previous family identity", world)
				}
			}
		}
	}
}

func TestSamsaraLineageSupportsSurvivalFallExtinctionReplacementAndNoConnection(t *testing.T) {
	family := BirthFamily{FamilyName: "Yu River-Ward House"}

	cases := []struct {
		roll int
		want string
	}{
		{0, "distant_surviving_branch"},
		{20, "fallen_severed_branch"},
		{50, "extinct_branch_replaced"},
		{90, "no_known_connection"},
	}
	for _, tc := range cases {
		status, summary := samsaraLineageFromRoll("Han Family", "martial_household", "Spiritual World", family, 0, tc.roll)
		if status != tc.want {
			t.Fatalf("roll=%d status=%q want=%q", tc.roll, status, tc.want)
		}
		if summary == "" {
			t.Fatalf("roll=%d missing summary", tc.roll)
		}
	}

	status, summary := samsaraLineageFromRoll("Qin Clan", "noble_martial_clan", "Immortal World", family, 0, 0)
	if status != "distant_surviving_branch" {
		t.Fatalf("noble surviving status=%q", status)
	}
	if !strings.Contains(summary, "never inherited its lower-world rank") || !strings.Contains(summary, "not a royal continuation") {
		t.Fatalf("noble branch incorrectly preserves rank: %q", summary)
	}

	status, summary = samsaraLineageFromRoll("Luo Clan", "fallen_martial_clan", "Immortal World", family, 0, 50)
	if status != "extinct_branch_replaced" {
		t.Fatalf("replacement status=%q", status)
	}
	if !strings.Contains(summary, "no blood continuity") {
		t.Fatalf("replacement does not clearly sever lineage: %q", summary)
	}
}

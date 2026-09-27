package game

// A sect residence's facility column is spliced into its SQL, because a
// column name cannot be a bound parameter (v1.12.0). Every caller passes a
// constant; the lookup holds the name to the table's own facility columns
// anyway, so a scanner's "string-built query" has nothing to reach.

import (
	"testing"

	"xianxia/core/internal/storage"
)

func TestTheResidenceLookupRefusesAColumnItDoesNotHave(t *testing.T) {
	path := setupSectResidenceDB(t)
	conn, err := storage.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	level, _, found, err := sectResidenceFacilityLevel(conn, 42, "sect_abode:42", "cultivation_level")
	if err != nil || !found || level != 1 {
		t.Fatalf("a real column must still be read: level=%d found=%v err=%v", level, found, err)
	}
	for _, bad := range []string{"user_id", "cultivation_level FROM sect_abodes --", "(SELECT 1)", ""} {
		if _, _, found, err := sectResidenceFacilityLevel(conn, 42, "sect_abode:42", bad); err == nil || found {
			t.Fatalf("column %q was spliced into the query: found=%v err=%v", bad, found, err)
		}
	}
}

func TestEveryWorkshopColumnIsAResidenceColumn(t *testing.T) {
	// The crafting lookup hands its column to the residence lookup; a
	// workshop whose column the allowlist does not carry would lose its bonus
	// inside a sect residence and read as an error instead.
	for _, trade := range []string{"alchemy", "forging", "formation", "inscription"} {
		if column := craftAbodeFacilityColumn(trade); !isSectAbodeFacilityColumn(column) {
			t.Fatalf("%s crafts at %q, which the residence lookup refuses", trade, column)
		}
	}
	if !isSectAbodeFacilityColumn("herb_garden_level") {
		t.Fatal("the herb garden column is refused, so foraging in a residence errors")
	}
}

package game

import "testing"

func TestFirstFourMigrationOperationsRegisteredAuthoritative(t *testing.T) {
	operations := []string{
		"craft.resolve",
		"forage.resolve",
		"beast.tame",
		"beast.feed",
		"beast.train",
		"beast.evolve",
		"beast.active",
		"artifact.bond",
		"artifact.awaken",
		"pvp.challenge",
		"pvp.respond",
		"pvp.act",
		"manual.study",
		"manual.technique",
		"crime.atone",
	}
	for _, op := range operations {
		if !isAuthoritativeOperation(op) {
			t.Errorf("%s is not registered as authoritative", op)
		}
	}
}

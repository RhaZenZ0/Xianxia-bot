package lifespan

import "testing"

func TestRealmCeilingAndOldAgeUseOneModel(t *testing.T) {
	ceiling := RealmCeiling(4, 1, 75)
	if ceiling == nil || *ceiling != 2000 {
		t.Fatalf("nascent soul stage-1 ceiling=%v", ceiling)
	}
	subject := Subject{
		RealmIndex: 4, Phase: 1, NaturalYears: 75,
		BirthGameMinute: 0, AgeAtCreationYears: 1000,
	}
	if OldAgeExpired(subject, 0) {
		t.Fatal("high-realm NPC incorrectly expired under mortal-scale threshold")
	}
	subject.AgeAtCreationYears = 2000
	if !OldAgeExpired(subject, 0) {
		t.Fatal("finite lifespan should expire at canonical ceiling")
	}
}

func TestImmortalIsAgeless(t *testing.T) {
	subject := Subject{
		RealmIndex:         ImmortalRealmIndex,
		Phase:              1,
		NaturalYears:       75,
		AgeAtCreationYears: 900000000000,
	}
	status := Evaluate(subject, 0)
	if !status.Ageless || status.TotalYears != nil || OldAgeExpired(subject, 0) {
		t.Fatalf("immortal status=%+v", status)
	}
}

func TestBootstrapAgeUsesCanonicalRealmCeiling(t *testing.T) {
	natural := int64(75)
	age := BootstrapAge(5, 9, natural, 7)
	ceiling := RealmCeiling(5, 9, natural)
	if ceiling == nil {
		t.Fatal("expected finite ceiling")
	}
	if age < 18 || age >= *ceiling {
		t.Fatalf("bootstrap age=%d ceiling=%d", age, *ceiling)
	}
	immortalAge := BootstrapAge(ImmortalRealmIndex, 1, natural, 7)
	if immortalAge < 10000 {
		t.Fatalf("immortal bootstrap age=%d", immortalAge)
	}
}

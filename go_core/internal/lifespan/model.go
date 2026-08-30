package lifespan

import "math"

const (
	MinutesPerYear       int64 = 1440 * 30 * 12
	ImmortalRealmIndex   int64 = 16
	DefaultNaturalYears  int64 = 75
	DefaultStartingYears int64 = 18
)

var realmRanges = [][2]int64{
	{70, 80}, {100, 250}, {200, 500}, {500, 1000}, {2000, 5000}, {10000, 100000}, {100000, 1000000},
	{10000000, 150000000}, {150000000, 300000000}, {300000000, 600000000}, {600000000, 1000000000},
	{1000000000, 2000000000}, {2000000000, 5000000000}, {5000000000, 10000000000}, {10000000000, 25000000000}, {25000000000, 100000000000},
}

type Subject struct {
	RealmIndex         int64
	Phase              int64
	BodyRealmIndex     int64
	BodyPhase          int64
	NaturalYears       int64
	ExtensionYears     int64
	BirthGameMinute    int64
	AgeAtCreationYears int64
}

type Status struct {
	AgeYears              float64  `json:"age_years"`
	NaturalYears          int64    `json:"natural_years"`
	CultivationBonusYears int64    `json:"cultivation_bonus_years"`
	ExtensionYears        int64    `json:"extension_years"`
	TotalYears            *int64   `json:"total_years"`
	Ageless               bool     `json:"ageless"`
	RemainingYears        *float64 `json:"remaining_years"`
	RealmFloorYears       *int64   `json:"realm_floor_years"`
	RealmCeilingYears     *int64   `json:"realm_ceiling_years"`
}

func RealmRange(realm int64) *[2]int64 {
	if realm < 0 {
		realm = 0
	}
	if realm >= ImmortalRealmIndex {
		return nil
	}
	idx := realm
	if idx >= int64(len(realmRanges)) {
		idx = int64(len(realmRanges) - 1)
	}
	v := realmRanges[idx]
	return &v
}

func RealmCeiling(realm, phase, natural int64) *int64 {
	if realm < 0 {
		realm = 0
	}
	if realm >= ImmortalRealmIndex {
		return nil
	}
	if natural < 1 {
		natural = DefaultNaturalYears
	}
	if realm == 0 {
		v := natural
		return &v
	}
	r := RealmRange(realm)
	p := phase
	if p < 1 {
		p = 1
	}
	if p > 9 {
		p = 9
	}
	v := int64(math.Round(float64(r[0]) + float64(r[1]-r[0])*(float64(p-1)/8.0)))
	return &v
}

func AgeYears(currentGameMinute, birthGameMinute, ageAtCreationYears int64) float64 {
	elapsed := currentGameMinute - birthGameMinute
	if elapsed < 0 {
		elapsed = 0
	}
	if ageAtCreationYears < 0 {
		ageAtCreationYears = 0
	}
	return float64(ageAtCreationYears) + float64(elapsed)/float64(MinutesPerYear)
}

func bodyBonus(subject Subject, qiCeiling int64) int64 {
	b := subject.BodyRealmIndex
	if b <= 0 || qiCeiling <= 0 || b >= ImmortalRealmIndex {
		return 0
	}
	bp := subject.BodyPhase
	if bp < 1 {
		bp = 1
	}
	if bp > 9 {
		bp = 9
	}
	qi := subject.RealmIndex
	if qi < 1 {
		qi = 1
	}
	relative := math.Min(1, float64(b)/float64(qi))
	stage := 0.55 + 0.45*(float64(bp-1)/8.0)
	return int64(float64(qiCeiling) * 0.20 * relative * stage)
}

func Evaluate(subject Subject, currentGameMinute int64) Status {
	natural := subject.NaturalYears
	if natural < 1 {
		natural = DefaultNaturalYears
	}
	extensions := subject.ExtensionYears
	if extensions < 0 {
		extensions = 0
	}
	age := AgeYears(currentGameMinute, subject.BirthGameMinute, subject.AgeAtCreationYears)
	ageless := subject.RealmIndex >= ImmortalRealmIndex || subject.BodyRealmIndex >= ImmortalRealmIndex
	rng := RealmRange(subject.RealmIndex)
	ceiling := RealmCeiling(subject.RealmIndex, subject.Phase, natural)
	bonus := int64(0)
	if ceiling != nil {
		bonus = *ceiling - natural + bodyBonus(subject, *ceiling)
		if bonus < 0 {
			bonus = 0
		}
	}
	out := Status{
		AgeYears: age, NaturalYears: natural, CultivationBonusYears: bonus,
		ExtensionYears: extensions, Ageless: ageless,
	}
	if rng != nil {
		floor, top := rng[0], rng[1]
		out.RealmFloorYears = &floor
		out.RealmCeilingYears = &top
	}
	if !ageless {
		total := natural + bonus + extensions
		remaining := math.Max(0, float64(total)-age)
		out.TotalYears = &total
		out.RemainingYears = &remaining
	}
	return out
}

func OldAgeExpired(subject Subject, currentGameMinute int64) bool {
	status := Evaluate(subject, currentGameMinute)
	return !status.Ageless && status.TotalYears != nil && status.AgeYears >= float64(*status.TotalYears)
}

func NaturalYearsFromSeed(seed uint64) int64 {
	return 70 + int64(seed%11)
}

func BootstrapAge(realm, phase, natural int64, seed uint64) int64 {
	if realm <= 0 {
		return DefaultStartingYears + int64(seed%43)
	}
	ceiling := RealmCeiling(realm, phase, natural)
	if ceiling == nil {
		return 10000 + int64(seed%900001)
	}
	fraction := 0.08 + float64(seed%25)/100.0
	age := int64(math.Round(float64(*ceiling) * fraction))
	if age < DefaultStartingYears {
		age = DefaultStartingYears
	}
	return age
}

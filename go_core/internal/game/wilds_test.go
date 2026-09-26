package game

import (
	"sort"
	"strings"
	"testing"
)

// A place in the wilds of a city is found by exploring from that city
// (v1.7.7). Moonfen Marsh and Cloudspine Foothills carried no road and no
// writer of a discovery ever named them, so the Drowned Serpent's lair, the
// Nine-Echo Sword Wraith's floor and the two secret realms whose entrances
// they are could be reached by a GM teleport and nothing else.
func TestAPlaceInTheWildsIsFoundFromItsCity(t *testing.T) {
	catalog := crossingCatalog(t)
	has := func(list []string, name string) bool {
		for _, x := range list {
			if x == name {
				return true
			}
		}
		return false
	}
	for city, wild := range map[string]string{"Moonfen City": "Moonfen Marsh", "Cloudblade City": "Cloudspine Foothills"} {
		got := discoveryCandidates(catalog, map[string]bool{city: true}, "Mortal World", 0)
		if !has(got, wild) {
			t.Fatalf("exploring from %s cannot turn up %s; candidates: %v", city, wild, got)
		}
		if has(discoveryCandidates(catalog, map[string]bool{city: true, wild: true}, "Mortal World", 0), wild) {
			t.Fatalf("%s is offered again to somebody who already knows it", wild)
		}
	}
	// Knowing somewhere else is not knowing the city the wilds belong to.
	got := discoveryCandidates(catalog, map[string]bool{"Greenriver Town": true}, "Mortal World", 0)
	if has(got, "Moonfen Marsh") || has(got, "Cloudspine Foothills") {
		t.Fatalf("the wilds were offered from a city they do not belong to: %v", got)
	}
}

// Every raid lair and every secret realm's entrance can be reached: it is on
// the road network, beside a road, a sect's gate, or in the wilds of a city
// the road network reaches. A lair nothing reaches is a boss nobody fights.
func TestEveryLairAndRealmEntranceCanBeReached(t *testing.T) {
	catalog := crossingCatalog(t)
	onRoads := map[string]bool{}
	for name, loc := range catalog.Locations {
		if len(loc.Roads) > 0 {
			onRoads[name] = true
			for _, n := range loc.Roads {
				onRoads[n] = true
			}
		}
	}
	gates := map[string]bool{}
	for sect := range catalog.Sects {
		if gate := sectGate(catalog, sect); gate != "" {
			gates[gate] = true
		}
	}
	reachable := func(place string) bool {
		loc, ok := catalog.Locations[place]
		if !ok {
			return false
		}
		if onRoads[place] || loc.RoadSite != "" || gates[place] {
			return true
		}
		city, ok := catalog.Locations[loc.WildsOf]
		return ok && onRoads[loc.WildsOf] && city.World == loc.World
	}
	places := map[string]string{}
	for key, template := range bossTemplatesGo {
		lair, _ := bossLair(catalog, template)
		places[lair] = "the lair of " + key
	}
	for id, realm := range catalog.SecretRealms {
		if _, taken := places[realm.Location]; !taken {
			places[realm.Location] = "the entrance of " + id
		}
	}
	if !reachable("Greenriver Town") {
		t.Fatal("the starting town reads as unreachable; the check is broken, not the tree")
	}
	var stranded []string
	for place, what := range places {
		if !reachable(place) {
			stranded = append(stranded, place+" ("+what+")")
		}
	}
	sort.Strings(stranded)
	if len(stranded) > 0 {
		t.Fatalf("nothing reaches %s", strings.Join(stranded, ", "))
	}
}

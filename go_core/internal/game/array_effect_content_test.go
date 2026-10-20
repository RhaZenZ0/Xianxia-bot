package game

import (
	"sort"
	"strings"
	"testing"
)

// A formation disk's effect is content since v1.35.0 (`array_effect` on the
// item), read by the deploy here and by the craft menu in Python. Every disk
// the catalogue deploys carries a whole one, so no disk crafts, sells and then
// is refused at the one place it is used.
func TestEveryFormationDiskCarriesItsEffectInTheContent(t *testing.T) {
	catalog := shippedCatalog(t)
	var disks, wrong []string
	for id, item := range catalog.Items {
		if item.ArrayDeploy == "" && item.ArrayEffect == nil {
			continue
		}
		disks = append(disks, id)
		switch {
		case item.ArrayDeploy == "":
			wrong = append(wrong, id+": carries an effect and no array_deploy")
		case item.ArrayEffect == nil:
			wrong = append(wrong, id+": deploys and carries no array_effect")
		case strings.TrimSpace(item.ArrayEffect.Name) == "" || item.ArrayEffect.DurationGameMinutes <= 0:
			wrong = append(wrong, id+": its array has no name or holds for no time")
		default:
			if mods, _ := item.ArrayEffect.Effect["modifiers"].([]any); len(mods) == 0 {
				wrong = append(wrong, id+": its array does nothing")
			}
		}
	}
	if len(disks) < 9 {
		t.Fatalf("the catalogue carries %d disks (%v); the reader is broken or the ladder lost a rung", len(disks), disks)
	}
	sort.Strings(wrong)
	if len(wrong) != 0 {
		t.Fatalf("a disk the deploy would refuse:\n  %s", strings.Join(wrong, "\n  "))
	}
}

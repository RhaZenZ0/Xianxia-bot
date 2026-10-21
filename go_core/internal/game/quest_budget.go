package game

import (
	"fmt"
	"os"
	"sort"
	"strings"

	"xianxia/core/internal/storage"
)

// The GM reward budget, read by the engine (v1.37.0).
//
// QUEST_REWARD_MAX_XP, QUEST_REWARD_MAX_STONES and QUEST_REWARD_MAX_ITEMS were
// the Forge's: app/rules/quests.py held a quest's rewards to them when the
// dashboard saved one, and admin.quest.save wrote whatever it was given. A
// bound that lives in the client is not a bound (rc.48): a request straight at
// the engine paid what it liked. The engine reads the same three keys (compose
// passes them, the rc.39 shape) and refuses a save over the budget, with the
// Forge's own rule (v1.36.0): the rewards the stored row already pays are the
// baseline, so a shipped quest above the budget saves unchanged and only a raise
// is refused. The validator stays as presentation, anticipating this refusal.
//
// The three defaults are stated here, in app/ops/config.py and in
// app/dashboard/server.py; tests/python/contracts/test_cooldowns_are_the_engines.py
// holds the three statements equal.
const (
	questBudgetMaxXPDefault     = int64(50)
	questBudgetMaxStonesDefault = int64(200)
	questBudgetMaxItemsDefault  = int64(3)
)

type questRewardBudget struct {
	MaxXP, MaxStones, MaxItems int64
}

func questRewardBudgetFromEnv() questRewardBudget {
	const ceiling = int64(1) << 40
	return questRewardBudget{
		MaxXP:     boundedEnvValue(os.Getenv("QUEST_REWARD_MAX_XP"), questBudgetMaxXPDefault, 0, ceiling),
		MaxStones: boundedEnvValue(os.Getenv("QUEST_REWARD_MAX_STONES"), questBudgetMaxStonesDefault, 0, ceiling),
		MaxItems:  boundedEnvValue(os.Getenv("QUEST_REWARD_MAX_ITEMS"), questBudgetMaxItemsDefault, 0, ceiling),
	}
}

// rewardItems is the items map of a rewards object, item -> quantity, with
// the zero and negative quantities the validator drops.
func rewardItems(rewards map[string]any) map[string]int64 {
	out := map[string]int64{}
	raw, ok := rewards["items"].(map[string]any)
	if !ok {
		return out
	}
	for item, qty := range raw {
		if n := storage.ParseInt(qty); n > 0 {
			out[strings.TrimSpace(item)] = n
		}
	}
	return out
}

func sameRewardItems(a, b map[string]int64) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

// checkQuestRewardBudget refuses a reward above the budget unless the stored
// row (the baseline) already paid exactly that. Its sentences are the
// validator's (app/rules/quests.py), so a GM reads the same refusal from either
// side.
func checkQuestRewardBudget(rewards, baseline map[string]any, budget questRewardBudget) error {
	for _, each := range []struct {
		Key string
		Cap int64
	}{{"insight_xp", budget.MaxXP}, {"spirit_stones", budget.MaxStones}} {
		value := storage.ParseInt(rewards[each.Key])
		if value > each.Cap && (baseline == nil || value != storage.ParseInt(baseline[each.Key])) {
			return fmt.Errorf("reward %s=%d exceeds the GM budget (%d)", each.Key, value, each.Cap)
		}
	}
	items := rewardItems(rewards)
	total := int64(0)
	for _, n := range items {
		total += n
	}
	if total > budget.MaxItems && (baseline == nil || !sameRewardItems(items, rewardItems(baseline))) {
		names := make([]string, 0, len(items))
		for item := range items {
			names = append(names, item)
		}
		sort.Strings(names)
		return fmt.Errorf("reward items total %d exceeds the GM budget (%d): %s", total, budget.MaxItems, strings.Join(names, ", "))
	}
	return nil
}

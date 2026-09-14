package gamerng

import (
	"crypto/rand"
	"errors"
	"math/big"
	"sync"
)

// Every die in this engine is rolled here, and in production every one of them
// is crypto/rand. There is deliberately no seed: a game whose next roll a
// player could work out is not a game.
//
// `roll` is a variable for exactly one reason, and the reason is not
// production. The world simulation is built out of deliberately low
// probabilities - a sect declares war on a 12% roll, a grave-robber turns
// something up on a 22% one - and a test that asserts such a thing eventually
// happened is asserting against the dice. Pile on enough iterations and the
// failure rate only gets small, never zero: the sect-war test failed about one
// run in two thousand and the grave-robber test about one in fifty. A test
// that fails for no reason is worse than no test, because it teaches the next
// person to re-run CI rather than read it.
//
// So a test can borrow the dice through UseRoller. Nothing outside a _test.go
// file calls it, and `go vet ./...` sees a plain package variable either way.
var roll = cryptoIntn

func cryptoIntn(n int) (int, error) {
	v, err := rand.Int(rand.Reader, big.NewInt(int64(n)))
	if err != nil {
		return 0, err
	}
	return int(v.Int64()), nil
}

// Intn is a uniform roll in [0,n).
func Intn(n int) (int, error) {
	if n <= 0 {
		return 0, errors.New("random bound must be positive")
	}
	return roll(n)
}

func D10() (int64, error) {
	n, err := Intn(10)
	if err != nil {
		return 0, err
	}
	return int64(n + 1), nil
}

// UseRoller lends the dice to a test and hands back the way to return them.
//
//	defer gamerng.UseRoller(func(n int) int { ... })()
//
// `fn` is given the exclusive bound the caller asked for - which is what lets
// a test answer one kind of roll differently from another, since a 1-in-100
// chance and a pick from a three-item list arrive with different bounds - and
// its answer is clamped into [0,n), so no test can produce a die that could
// not have been rolled. Restoring is not optional: every later test in the
// package would inherit the loaded dice.
func UseRoller(fn func(n int) int) (restore func()) {
	previous := roll
	var mu sync.Mutex
	roll = func(n int) (int, error) {
		mu.Lock()
		v := fn(n)
		mu.Unlock()
		if v < 0 {
			v = 0
		}
		if v >= n {
			v = n - 1
		}
		return v, nil
	}
	return func() { roll = previous }
}

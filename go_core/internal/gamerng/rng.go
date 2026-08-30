package gamerng

import (
	"crypto/rand"
	"errors"
	"math/big"
)

func Intn(n int) (int, error) {
	if n <= 0 {
		return 0, errors.New("random bound must be positive")
	}
	v, err := rand.Int(rand.Reader, big.NewInt(int64(n)))
	if err != nil {
		return 0, err
	}
	return int(v.Int64()), nil
}

func D10() (int64, error) {
	n, err := Intn(10)
	if err != nil {
		return 0, err
	}
	return int64(n + 1), nil
}

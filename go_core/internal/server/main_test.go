package server

import (
	"os"
	"testing"
)

// New refuses to build an engine without a usable ENGINE_AUTH_TOKEN (v0.29.0),
// so the package's tests run under one. A test that wants to prove the
// refusal overrides this with t.Setenv, which restores it afterwards.
func TestMain(m *testing.M) {
	if os.Getenv("ENGINE_AUTH_TOKEN") == "" {
		_ = os.Setenv("ENGINE_AUTH_TOKEN", "test-engine-token-1234567890")
	}
	os.Exit(m.Run())
}

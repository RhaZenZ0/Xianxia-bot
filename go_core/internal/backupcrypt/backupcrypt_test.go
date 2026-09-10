package backupcrypt

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestRoundTripAndFormat(t *testing.T) {
	plain := bytes.Repeat([]byte("SQLite format 3\x00 page "), 4096)
	sealed, err := Encrypt(plain, "correct horse")
	if err != nil {
		t.Fatal(err)
	}
	if string(sealed[:8]) != magic {
		t.Fatalf("magic=%q", sealed[:8])
	}
	if bytes.Contains(sealed, []byte("SQLite format 3")) {
		t.Fatal("ciphertext leaks the plaintext header")
	}
	back, err := Decrypt(sealed, "correct horse")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(back, plain) {
		t.Fatal("round trip changed the bytes")
	}
	// Two seals of the same bytes differ (fresh salt and nonce).
	again, _ := Encrypt(plain, "correct horse")
	if bytes.Equal(again, sealed) {
		t.Fatal("two seals produced identical output")
	}
}

func TestWrongKeyTruncationAndTamperingAllFail(t *testing.T) {
	plain := []byte("the whole database")
	sealed, err := Encrypt(plain, "right")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Decrypt(sealed, "wrong"); !errors.Is(err, ErrWrongKey) {
		t.Fatalf("wrong key: %v", err)
	}
	if _, err := Decrypt(sealed[:len(sealed)-1], "right"); !errors.Is(err, ErrWrongKey) {
		t.Fatalf("truncated: %v", err)
	}
	tampered := append([]byte{}, sealed...)
	tampered[headerLength()] ^= 0x01
	if _, err := Decrypt(tampered, "right"); !errors.Is(err, ErrWrongKey) {
		t.Fatalf("tampered body: %v", err)
	}
	tampered = append([]byte{}, sealed...)
	tampered[len(magic)] ^= 0x01
	if _, err := Decrypt(tampered, "right"); !errors.Is(err, ErrWrongKey) {
		t.Fatalf("tampered salt: %v", err)
	}
	if _, err := Decrypt([]byte("SQLite format 3\x00"), "right"); !errors.Is(err, ErrNotEncrypted) {
		t.Fatalf("plain file: %v", err)
	}
	if _, err := Decrypt(sealed, ""); err == nil {
		t.Fatal("an empty key opened the file")
	}
	if _, err := Encrypt(plain, "  "); err == nil {
		t.Fatal("an empty key sealed the file")
	}
}

func TestFilesRoundTripAndAreRecognised(t *testing.T) {
	dir := t.TempDir()
	src := filepath.Join(dir, "a.sqlite3")
	enc := filepath.Join(dir, "a.sqlite3.enc")
	out := filepath.Join(dir, "b.sqlite3")
	if err := os.WriteFile(src, []byte("payload"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := EncryptFile(src, enc, "k"); err != nil {
		t.Fatal(err)
	}
	if yes, _ := IsEncryptedFile(enc); !yes {
		t.Fatal("encrypted file not recognised")
	}
	if yes, _ := IsEncryptedFile(src); yes {
		t.Fatal("plain file mistaken for encrypted")
	}
	if err := DecryptFile(enc, out, "k"); err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(out); string(data) != "payload" {
		t.Fatalf("decrypted=%q", data)
	}
	if _, err := os.Stat(enc + ".partial"); !os.IsNotExist(err) {
		t.Fatal("the .partial file was left behind")
	}
	if !IsEncryptedName("xianxia-1.sqlite3.enc") || IsEncryptedName("xianxia-1.sqlite3") {
		t.Fatal("IsEncryptedName is wrong")
	}
}

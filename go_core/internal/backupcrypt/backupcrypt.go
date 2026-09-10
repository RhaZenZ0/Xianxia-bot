// Package backupcrypt seals a SQLite backup file with an operator passphrase
// (v0.32.0). The engine writes it when XIANXIA_BACKUP_KEY is set, the
// restore endpoint reads it back, and `xianxia-engine decrypt-backup` opens
// one off-box without the engine running.
//
// Format, so a file can be recognised and read without this package:
//
//	8 bytes   magic "XXBKENC1"
//	16 bytes  PBKDF2 salt
//	12 bytes  AES-GCM nonce
//	rest      AES-256-GCM ciphertext of the whole SQLite file, tag appended
//
// The key is PBKDF2-HMAC-SHA256(passphrase, salt, 200000 iterations, 32
// bytes). The file is sealed in one piece: a truncated or altered copy fails
// authentication as a whole rather than decrypting to a database missing its
// tail. That holds the whole backup in memory once while sealing and once
// while opening, which is fine for the databases this bot keeps (tens of
// megabytes) and is stated here so nobody expects it to stream.
package backupcrypt

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
)

const (
	magic      = "XXBKENC1"
	saltSize   = 16
	nonceSize  = 12
	keySize    = 32
	iterations = 200_000
	// Suffix is what an encrypted backup's name ends in, after the ordinary
	// .sqlite3 - the listing and restore code key on it.
	Suffix = ".enc"
)

// ErrNotEncrypted is returned when Decrypt is handed a file this package did
// not write.
var ErrNotEncrypted = errors.New("not an encrypted xianxia backup (missing header)")

// ErrWrongKey covers both a wrong passphrase and a damaged file: AES-GCM
// cannot tell them apart, and saying so is more honest than guessing.
var ErrWrongKey = errors.New("backup could not be opened: wrong XIANXIA_BACKUP_KEY or damaged file")

// IsEncryptedName reports whether a backup filename carries the suffix.
func IsEncryptedName(name string) bool { return strings.HasSuffix(name, Suffix) }

func deriveKey(passphrase string, salt []byte) []byte {
	// PBKDF2-HMAC-SHA256 (RFC 8018 §5.2) with one block, since 32 bytes is
	// exactly one SHA-256 output. Written out rather than imported because the
	// module pins Go 1.23, which has no crypto/pbkdf2, and x/crypto is not a
	// dependency this engine otherwise needs.
	prf := hmac.New(sha256.New, []byte(passphrase))
	prf.Write(salt)
	prf.Write([]byte{0, 0, 0, 1})
	u := prf.Sum(nil)
	out := make([]byte, len(u))
	copy(out, u)
	for i := 1; i < iterations; i++ {
		prf.Reset()
		prf.Write(u)
		u = prf.Sum(nil)
		for j := range out {
			out[j] ^= u[j]
		}
	}
	return out[:keySize]
}

// Encrypt seals plaintext under the passphrase and returns the file bytes.
func Encrypt(plaintext []byte, passphrase string) ([]byte, error) {
	if strings.TrimSpace(passphrase) == "" {
		return nil, errors.New("a backup key is required to encrypt")
	}
	salt := make([]byte, saltSize)
	if _, err := io.ReadFull(rand.Reader, salt); err != nil {
		return nil, err
	}
	nonce := make([]byte, nonceSize)
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(deriveKey(passphrase, salt))
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	out := make([]byte, 0, len(magic)+saltSize+nonceSize+len(plaintext)+gcm.Overhead())
	out = append(out, magic...)
	out = append(out, salt...)
	out = append(out, nonce...)
	// The header is bound as additional data so a spliced salt or nonce is
	// caught the same way a spliced body is.
	return gcm.Seal(out, nonce, plaintext, out[:len(magic)+saltSize+nonceSize]), nil
}

// Decrypt opens file bytes written by Encrypt.
func Decrypt(sealed []byte, passphrase string) ([]byte, error) {
	header := len(magic) + saltSize + nonceSize
	if len(sealed) < header || !bytes.Equal(sealed[:len(magic)], []byte(magic)) {
		return nil, ErrNotEncrypted
	}
	if strings.TrimSpace(passphrase) == "" {
		return nil, errors.New("this backup is encrypted; XIANXIA_BACKUP_KEY is required to open it")
	}
	salt := sealed[len(magic) : len(magic)+saltSize]
	nonce := sealed[len(magic)+saltSize : header]
	block, err := aes.NewCipher(deriveKey(passphrase, salt))
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	plaintext, err := gcm.Open(nil, nonce, sealed[header:], sealed[:header])
	if err != nil {
		return nil, ErrWrongKey
	}
	return plaintext, nil
}

// EncryptFile seals src into dst. dst is written whole, then renamed into
// place, so a crash mid-write never leaves a half file under the final name.
func EncryptFile(src, dst, passphrase string) error {
	plaintext, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	sealed, err := Encrypt(plaintext, passphrase)
	if err != nil {
		return err
	}
	return writeAtomically(dst, sealed)
}

// DecryptFile opens src into dst.
func DecryptFile(src, dst, passphrase string) error {
	sealed, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	plaintext, err := Decrypt(sealed, passphrase)
	if err != nil {
		return fmt.Errorf("%s: %w", src, err)
	}
	return writeAtomically(dst, plaintext)
}

// IsEncryptedFile looks at the header only.
func IsEncryptedFile(path string) (bool, error) {
	f, err := os.Open(path)
	if err != nil {
		return false, err
	}
	defer f.Close()
	head := make([]byte, len(magic))
	n, err := io.ReadFull(f, head)
	if err != nil && !errors.Is(err, io.ErrUnexpectedEOF) && !errors.Is(err, io.EOF) {
		return false, err
	}
	return n == len(magic) && string(head) == magic, nil
}

func writeAtomically(dst string, data []byte) error {
	tmp := dst + ".partial"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	if err := os.Rename(tmp, dst); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

// headerLength lets a test corrupt the body only.
func headerLength() int { return len(magic) + saltSize + nonceSize }

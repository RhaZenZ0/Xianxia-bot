package server

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"xianxia/core/internal/backupcrypt"
)

// backupPolicy is what keeps data/backups from being the whole disk
// (v0.32.0). Until this release storage.BackupTo was the entire backup
// story: every backup stayed forever, in the clear, on the same disk as the
// database it protected.
//
//   - KeepDaily: every backup from the N most recent days that have one is
//     kept whole - a safety backup taken before a restore is never pruned
//     the day it was made.
//   - KeepWeekly: older than that, the newest backup of each of the M most
//     recent weeks survives; the rest go.
//   - MaxBytes: after retention, the oldest survivors are dropped until the
//     directory fits, but the newest backup is never dropped, whatever the cap.
//   - Key: when set, every new backup is sealed with it (backupcrypt) and
//     restore opens it with the same key.
//
// KeepDaily and KeepWeekly both 0 means retention is off, which is what
// every deployment had before; the shipped defaults are 14 and 8.
type backupPolicy struct {
	KeepDaily  int
	KeepWeekly int
	MaxBytes   int64
	Key        string
}

const (
	defaultBackupKeepDaily  = 14
	defaultBackupKeepWeekly = 8
)

func backupPolicyFromEnv() backupPolicy {
	return backupPolicy{
		KeepDaily:  envInt("XIANXIA_BACKUP_KEEP_DAILY", defaultBackupKeepDaily),
		KeepWeekly: envInt("XIANXIA_BACKUP_KEEP_WEEKLY", defaultBackupKeepWeekly),
		MaxBytes:   int64(envInt("XIANXIA_BACKUP_MAX_MB", 0)) << 20,
		Key:        strings.TrimSpace(os.Getenv("XIANXIA_BACKUP_KEY")),
	}
}

func envInt(key string, fallback int) int {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n < 0 {
		return fallback
	}
	return n
}

// isBackupName is the one definition of "a file this engine will list,
// restore or prune": what reserveBackupPath writes, plain or sealed. Anything
// else in the directory - an operator's own copy, a stray file - is left
// alone by every path here.
func isBackupName(name string) bool {
	if !strings.HasPrefix(name, "xianxia-") {
		return false
	}
	return strings.HasSuffix(name, ".sqlite3") || strings.HasSuffix(name, ".sqlite3"+backupcrypt.Suffix)
}

// backupTime reads the stamp reserveBackupPath put in the name, so retention
// sorts by when the backup was taken rather than by an mtime a copy or a
// restore may have touched. A name without a readable stamp falls back to
// the mtime the caller supplies.
func backupTime(name string, fallback time.Time) time.Time {
	stamp := strings.TrimPrefix(name, "xianxia-")
	if len(stamp) < len("20060102-150405.000") {
		return fallback
	}
	when, err := time.Parse("20060102-150405.000", stamp[:len("20060102-150405.000")])
	if err != nil {
		return fallback
	}
	return when
}

// retainBackups decides which of items (any order) survive under policy.
// Pure, so the rule is testable without a filesystem. now is unused by the
// rule itself - days and weeks are counted among the backups that exist,
// not against the calendar, so a server that was off for a month does not
// come back and delete everything as "older than N days".
func retainBackups(items []backupInfo, policy backupPolicy) (keep, drop []backupInfo) {
	sorted := append([]backupInfo(nil), items...)
	sort.SliceStable(sorted, func(i, j int) bool {
		if !sorted[i].when.Equal(sorted[j].when) {
			return sorted[i].when.After(sorted[j].when)
		}
		return sorted[i].Name > sorted[j].Name
	})
	retention := policy.KeepDaily > 0 || policy.KeepWeekly > 0
	dayKeys := []string{}
	dayKept := map[string]bool{}
	weekKept := map[string]bool{}
	for _, item := range sorted {
		if !retention {
			keep = append(keep, item)
			continue
		}
		day := item.when.UTC().Format("2006-01-02")
		if !dayKept[day] && len(dayKeys) < policy.KeepDaily {
			dayKeys = append(dayKeys, day)
			dayKept[day] = true
		}
		if dayKept[day] {
			keep = append(keep, item)
			continue
		}
		year, week := item.when.UTC().ISOWeek()
		weekKey := fmt.Sprintf("%d-W%02d", year, week)
		if _, seen := weekKept[weekKey]; !seen && len(weekKept) < policy.KeepWeekly {
			weekKept[weekKey] = true
			keep = append(keep, item)
			continue
		}
		drop = append(drop, item)
	}
	if policy.MaxBytes > 0 {
		var total int64
		for _, item := range keep {
			total += item.Size
		}
		// keep is newest first; shed from the back, never the front.
		for len(keep) > 1 && total > policy.MaxBytes {
			last := keep[len(keep)-1]
			keep = keep[:len(keep)-1]
			drop = append(drop, last)
			total -= last.Size
		}
	}
	return keep, drop
}

// listBackups reads the directory into backupInfo rows, newest first.
func listBackups(backupDir string) ([]backupInfo, error) {
	entries, err := os.ReadDir(backupDir)
	if err != nil {
		if os.IsNotExist(err) {
			return []backupInfo{}, nil
		}
		return nil, err
	}
	items := make([]backupInfo, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || !isBackupName(entry.Name()) {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		items = append(items, backupInfoFor(info))
	}
	sort.Slice(items, func(i, j int) bool {
		if !items[i].when.Equal(items[j].when) {
			return items[i].when.After(items[j].when)
		}
		return items[i].Name > items[j].Name
	})
	return items, nil
}

func backupInfoFor(info os.FileInfo) backupInfo {
	return backupInfo{
		Name:       info.Name(),
		Size:       info.Size(),
		ModifiedAt: float64(info.ModTime().UnixNano()) / 1e9,
		Encrypted:  backupcrypt.IsEncryptedName(info.Name()),
		when:       backupTime(info.Name(), info.ModTime()),
	}
}

// pruneBackups applies the policy to the directory and returns the names it
// removed. A file that will not delete is reported, not fatal: the backup
// that just succeeded is still a backup.
func pruneBackups(backupDir string, policy backupPolicy) ([]string, error) {
	items, err := listBackups(backupDir)
	if err != nil {
		return nil, err
	}
	_, drop := retainBackups(items, policy)
	removed := []string{}
	var firstErr error
	for _, item := range drop {
		if err := os.Remove(filepath.Join(backupDir, item.Name)); err != nil {
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		removed = append(removed, item.Name)
	}
	return removed, firstErr
}

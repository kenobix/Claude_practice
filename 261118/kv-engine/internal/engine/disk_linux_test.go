//go:build linux

package engine

import (
	"bufio"
	"bytes"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/recovery"
)

func mustOpen(t *testing.T, dir string) *DB {
	t.Helper()
	db, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := db.Close(); err != nil {
			t.Error(err)
		}
	})
	return db
}
func put(t *testing.T, db *DB, key string, value []byte, want uint64) {
	t.Helper()
	seq, err := db.Put(key, value)
	if err != nil || seq != want {
		t.Fatalf("seq=%d want=%d err=%v", seq, want, err)
	}
}
func value(t *testing.T, db *DB, key string, want []byte) {
	t.Helper()
	got, err := db.Get(key)
	if err != nil || got == nil || !bytes.Equal(got, want) {
		t.Fatalf("key=%s got=%x want=%x err=%v", key, got, want, err)
	}
}
func logBytes(t *testing.T, dir string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(dir, "data.log"))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func probeLock(path string) (*os.File, error) {
	dir, err := openDirectory(path)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	return lockDirectory(dir)
}

func TestDiskRestart(t *testing.T) {
	dir := t.TempDir()
	db := mustOpen(t, dir)
	put(t, db, "k", []byte("old"), 1)
	put(t, db, "k", []byte{0, 255}, 2)
	put(t, db, "empty", nil, 3)
	if seq, err := db.Delete("k"); seq != 4 || err != nil {
		t.Fatal(seq, err)
	}
	if seq, err := db.Delete("missing"); seq != 5 || err != nil {
		t.Fatal(seq, err)
	}
	put(t, db, "k", []byte("last"), 6)
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	db = mustOpen(t, dir)
	value(t, db, "k", []byte("last"))
	value(t, db, "empty", nil)
	if _, err := db.Get("missing"); !errors.Is(err, ErrNotFound) {
		t.Fatal(err)
	}
	report, err := db.Inspect()
	if err != nil || report.RecordCount != 6 || report.LiveKeys != 2 || report.DeleteCount != 2 {
		t.Fatal(report, err)
	}
	put(t, db, "next", nil, 7)
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Put("k", nil); !errors.Is(err, ErrClosed) {
		t.Fatal(err)
	}
}

func TestDiskValidation(t *testing.T) {
	dir := t.TempDir()
	db := mustOpen(t, dir)
	before := logBytes(t, dir)
	if _, err := db.Put("", nil); !errors.Is(err, ErrInvalidKey) {
		t.Fatal(err)
	}
	if _, err := db.Put("k", make([]byte, record.MaxValueSize+1)); !errors.Is(err, ErrValueTooLarge) {
		t.Fatal(err)
	}
	if _, err := db.Delete("\x00"); !errors.Is(err, ErrInvalidKey) {
		t.Fatal(err)
	}
	if !bytes.Equal(before, logBytes(t, dir)) {
		t.Fatal("invalid input changed log")
	}
	put(t, db, strings.Repeat("é", 128), make([]byte, record.MaxValueSize), 1)
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	db = mustOpen(t, dir)
	value(t, db, strings.Repeat("é", 128), make([]byte, record.MaxValueSize))
}

func TestLockProcess(t *testing.T) {
	if dir := os.Getenv("KV_TEST_LOCK_DIR"); dir != "" {
		db, err := Open(dir)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(10)
		}
		fmt.Println("locked")
		_, err = io.Copy(io.Discard, os.Stdin)
		closeErr := db.Close()
		if err != nil || closeErr != nil {
			os.Exit(11)
		}
		os.Exit(0)
	}
	dir := t.TempDir()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(exe, "-test.run=^TestLockProcess$")
	cmd.Env = append(os.Environ(), "KV_TEST_LOCK_DIR="+dir)
	input, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	output, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		input.Close()
		if cmd.ProcessState == nil {
			cmd.Process.Kill()
			cmd.Wait()
		}
	}()
	line, err := bufio.NewReader(output).ReadString('\n')
	if err != nil || line != "locked\n" {
		t.Fatal(line, err, stderr.String())
	}
	before, err := os.Stat(filepath.Join(dir, "LOCK"))
	if err != nil {
		t.Fatal(err)
	}
	if db, err := Open(dir); db != nil || !errors.Is(err, ErrLocked) {
		t.Fatal(db, err)
	}
	if _, err := Inspect(dir); !errors.Is(err, ErrLocked) {
		t.Fatal(err)
	}
	input.Close()
	if err := cmd.Wait(); err != nil {
		t.Fatal(err, stderr.String())
	}
	db := mustOpen(t, dir)
	after, err := os.Stat(filepath.Join(dir, "LOCK"))
	if err != nil || !os.SameFile(before, after) {
		t.Fatal("LOCK replaced", err)
	}
	put(t, db, "k", nil, 1)
}

type faultFile struct {
	*os.File
	mode   string
	events *[]string
	hook   func()
}

func (f *faultFile) WriteAt(p []byte, offset int64) (int, error) {
	if f.events != nil {
		*f.events = append(*f.events, "write")
	}
	switch f.mode {
	case "short":
		if len(p) > 3 {
			p = p[:3]
		}
	case "zero":
		return 0, nil
	case "enospc":
		n, _ := f.File.WriteAt(p[:min(5, len(p))], offset)
		return n, syscall.ENOSPC
	}
	return f.File.WriteAt(p, offset)
}
func (f *faultFile) Sync() error {
	if f.events != nil {
		*f.events = append(*f.events, "file-sync")
	}
	if f.hook != nil {
		f.hook()
	}
	if f.mode == "sync" {
		return syscall.EIO
	}
	return f.File.Sync()
}
func (f *faultFile) ReadAt(p []byte, offset int64) (int, error) {
	if f.mode == "read" {
		return 0, syscall.EIO
	}
	if f.mode == "header-eof" {
		return io.NewSectionReader(f.File, 0, 8).ReadAt(p, offset)
	}
	return f.File.ReadAt(p, offset)
}
func (f *faultFile) Truncate(size int64) error {
	if f.mode == "truncate" {
		return syscall.EIO
	}
	return f.File.Truncate(size)
}

func TestDiskWriteAndSyncFailures(t *testing.T) {
	for _, mode := range []string{"short", "zero", "enospc", "sync", "read"} {
		t.Run(mode, func(t *testing.T) {
			dir := t.TempDir()
			var file *faultFile
			ops := defaultOps()
			ops.wrap = func(raw *os.File) logFile { file = &faultFile{File: raw}; return file }
			db, err := openDisk(dir, Options{}, ops)
			if !errors.Is(err, os.ErrNotExist) {
				t.Fatal(err)
			}
			db, err = openDisk(dir, Options{Create: true}, ops)
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			put(t, db, "k", []byte("committed"), 1)
			file.mode = mode
			if mode == "read" {
				_, err = db.Get("k")
			} else {
				_, err = db.Put("k", []byte("unacknowledged"))
			}
			if mode == "short" {
				if err != nil {
					t.Fatal(err)
				}
				value(t, db, "k", []byte("unacknowledged"))
				return
			}
			if err == nil {
				t.Fatal("injected failure reported success")
			}
			if mode == "enospc" && !errors.Is(err, syscall.ENOSPC) {
				t.Fatal(err)
			}
			if db.state.Report.LatestSequence != 1 {
				t.Fatal("published before successful sync")
			}
			if _, err = db.Put("next", nil); !errors.Is(err, ErrRecoveryRequired) {
				t.Fatal(err)
			}
			if _, err = db.Delete("k"); !errors.Is(err, ErrRecoveryRequired) {
				t.Fatal(err)
			}
			if _, err = db.Get("k"); !errors.Is(err, ErrRecoveryRequired) {
				t.Fatal(err)
			}
			if _, err = db.Inspect(); !errors.Is(err, ErrRecoveryRequired) {
				t.Fatal(err)
			}
			if err := db.Close(); err != nil {
				t.Fatal(err)
			}
			var diagnostic bytes.Buffer
			reopened, err := OpenWithOptions(dir, Options{Diagnostics: &diagnostic})
			if err != nil {
				t.Fatal(err)
			}
			defer reopened.Close()
			if _, err = reopened.Get("k"); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestSyncOrdering(t *testing.T) {
	dir := t.TempDir()
	var file *faultFile
	var events []string
	ops := defaultOps()
	ops.wrap = func(raw *os.File) logFile { file = &faultFile{File: raw, events: &events}; return file }
	ops.syncDir = func(f *os.File) error {
		events = append(events, "dir:"+filepath.Clean(f.Name()))
		return f.Sync()
	}
	db, err := openDisk(dir, Options{Create: true}, ops)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	want := []string{"write", "file-sync", "dir:" + dir, "dir:" + filepath.Dir(dir)}
	if strings.Join(events, "|") != strings.Join(want, "|") {
		t.Fatal(events)
	}
	events = nil
	file.hook = func() {
		if db.state.Report.LatestSequence != 0 || len(db.state.Index) != 0 {
			t.Error("published before Sync")
		}
	}
	put(t, db, "k", nil, 1)
	if strings.Join(events, "|") != "write|file-sync" {
		t.Fatal(events)
	}
}

func TestInitializationFailures(t *testing.T) {
	for _, mode := range []string{"zero", "enospc", "sync", "dir", "parent", "stat"} {
		t.Run(mode, func(t *testing.T) {
			dir := t.TempDir()
			ops := defaultOps()
			ops.wrap = func(raw *os.File) logFile {
				if mode == "stat" {
					raw.Close()
				}
				return &faultFile{File: raw, mode: mode}
			}
			ops.syncDir = func(f *os.File) error {
				path := filepath.Clean(f.Name())
				if mode == "dir" && path == dir || mode == "parent" && path == filepath.Dir(dir) {
					return syscall.EIO
				}
				return f.Sync()
			}
			if db, err := openDisk(dir, Options{Create: true}, ops); db != nil || err == nil {
				t.Fatal(db, err)
			}
			// Failure must release the OS lock and leave the file for diagnosis.
			lock, err := probeLock(dir)
			if err != nil {
				t.Fatal(err)
			}
			lock.Close()
			if _, err := os.Stat(filepath.Join(dir, "data.log")); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestOpenDirectorySyncPaths(t *testing.T) {
	for _, name := range []string{"absolute", "relative", "dot", "trailing-slash", "symlink-parent", "dot-parent-eio"} {
		t.Run(name, func(t *testing.T) {
			parent := t.TempDir()
			dir := filepath.Join(parent, "db")
			if err := os.Mkdir(dir, 0700); err != nil {
				t.Fatal(err)
			}
			t.Chdir(parent)
			path := dir
			switch name {
			case "relative":
				path = "db"
			case "dot", "dot-parent-eio":
				t.Chdir(dir)
				path = "."
			case "trailing-slash":
				path = "./db/"
			case "symlink-parent":
				aliasDir := t.TempDir()
				child := filepath.Join(parent, "child")
				if err := os.Mkdir(child, 0700); err != nil {
					t.Fatal(err)
				}
				alias := filepath.Join(aliasDir, "alias")
				if err := os.Symlink(child, alias); err != nil {
					t.Fatal(err)
				}
				path = alias + "/../db"
			}

			var events []string
			dirInfo, err := os.Stat(dir)
			if err != nil {
				t.Fatal(err)
			}
			parentInfo, err := os.Stat(parent)
			if err != nil {
				t.Fatal(err)
			}
			ops := defaultOps()
			ops.wrap = func(raw *os.File) logFile {
				return &faultFile{File: raw, events: &events}
			}
			ops.syncDir = func(f *os.File) error {
				info, err := f.Stat()
				if err != nil {
					return err
				}
				switch {
				case os.SameFile(info, dirInfo):
					events = append(events, "dir:"+dir)
				case os.SameFile(info, parentInfo):
					events = append(events, "dir:"+parent)
					if name == "dot-parent-eio" {
						return syscall.EIO
					}
				default:
					return fmt.Errorf("unexpected sync target: %s", f.Name())
				}
				return f.Sync()
			}
			db, err := openDisk(path, Options{Create: true}, ops)
			if db != nil {
				defer db.Close()
			}
			want := []string{"write", "file-sync", "dir:" + dir, "dir:" + parent}
			if strings.Join(events, "|") != strings.Join(want, "|") {
				t.Fatalf("sync order=%v, want %v (Open: %v)", events, want, err)
			}
			if name == "dot-parent-eio" {
				if db != nil || !errors.Is(err, syscall.EIO) || !bytes.Equal(logBytes(t, dir), record.FileHeader()) {
					t.Fatalf("Open=%v, err=%v; expected EIO with the log header retained", db, err)
				}
				db = mustOpen(t, dir)
			} else if err != nil || db == nil {
				t.Fatal(db, err)
			}
			if _, err := Inspect(dir); !errors.Is(err, ErrLocked) {
				t.Fatalf("Open and Inspect do not share the lock: %v", err)
			}
			put(t, db, "k", []byte("v"), 1)
			if err := db.Close(); err != nil {
				t.Fatal(err)
			}
			if name == "dot-parent-eio" {
				before := logBytes(t, dir)
				reopened, err := openDisk(path, Options{}, ops)
				if reopened != nil || !errors.Is(err, syscall.EIO) || !bytes.Equal(before, logBytes(t, dir)) {
					t.Fatalf("reopen=%v, err=%v; expected EIO and unchanged log", reopened, err)
				}
			}
			report, err := Inspect(path)
			if err != nil || report.LatestSequence != 1 {
				t.Fatalf("Inspect via %q: report=%+v, err=%v", path, report, err)
			}
			value(t, mustOpen(t, dir), "k", []byte("v"))
		})
	}
}

func TestDeniedDirectoryTraversal(t *testing.T) {
	parent := t.TempDir()
	dir := filepath.Join(parent, "db")
	empty := filepath.Join(parent, "empty")
	blocked := filepath.Join(parent, "no-search")
	for _, path := range []string{dir, empty, blocked} {
		if err := os.Mkdir(path, 0700); err != nil {
			t.Fatal(err)
		}
	}
	t.Chdir(parent)
	db := mustOpen(t, dir)
	put(t, db, "saved", []byte("value"), 1)
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	before := logBytes(t, dir)
	if err := os.Symlink("no-search/../db", "alias"); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(blocked, 0600); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.Chmod(blocked, 0700); err != nil {
			t.Error(err)
		}
	})
	if _, err := os.Stat("no-search/../db"); err == nil {
		t.Skip("test user can bypass directory search permissions")
	} else if !errors.Is(err, syscall.EACCES) {
		t.Fatalf("unexpected kernel path error: %v", err)
	}
	for _, path := range []string{"no-search/../db", parent + "/no-search/../db", "alias", "no-search/../empty"} {
		t.Run(path, func(t *testing.T) {
			db, err := Open(path)
			if db != nil {
				if closeErr := db.Close(); closeErr != nil {
					t.Error(closeErr)
				}
			}
			if db != nil || !errors.Is(err, syscall.EACCES) {
				t.Errorf("Open(%q) = %v, %v; want EACCES", path, db != nil, err)
			}
			report, err := Inspect(path)
			if report.Status != "" || !errors.Is(err, syscall.EACCES) {
				t.Errorf("Inspect(%q) = %+v, %v; want EACCES before scanning", path, report, err)
			}
		})
	}
	if !bytes.Equal(before, logBytes(t, dir)) {
		t.Error("rejected paths changed the existing log")
	}
	if entries, err := os.ReadDir(empty); err != nil || len(entries) != 0 {
		t.Fatalf("rejected path changed empty directory: %v, %v", entries, err)
	}
}

func TestCapacityAndSequence(t *testing.T) {
	db := mustOpen(t, t.TempDir())
	// Exercise the arithmetic boundary without writing a 256 MiB log in unit tests.
	size := int64(record.HeaderSize + 1 + 4)
	db.state.Report.LogBytes = record.MaxLogSize - size
	put(t, db, "k", nil, 1)
	if db.state.Report.LogBytes != record.MaxLogSize {
		t.Fatal("exact limit rejected")
	}
	if _, err := db.Delete("k"); !errors.Is(err, ErrLogFull) {
		t.Fatal(err)
	}
	db.state.Report.LogBytes = record.FileHeaderSize
	db.state.Report.LatestSequence = ^uint64(0)
	if _, err := db.Put("k", nil); !errors.Is(err, ErrSequenceExhausted) {
		t.Fatal(err)
	}
	if db.failed {
		t.Fatal("capacity/sequence error poisoned DB")
	}
}

func TestDiskConcurrentCalls(t *testing.T) {
	db := mustOpen(t, t.TempDir())
	var wg sync.WaitGroup
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			key := fmt.Sprint(i)
			for j := 0; j < 8; j++ {
				if _, err := db.Put(key, []byte(key)); err != nil {
					t.Error(err)
				}
				if _, err := db.Get(key); err != nil {
					t.Error(err)
				}
			}
		}(i)
	}
	wg.Wait()
	if report, err := db.Inspect(); err != nil || report.RecordCount != 32 {
		t.Fatal(report, err)
	}
}

func TestTailRecoveryConditions(t *testing.T) {
	one, _ := record.Encode(record.Record{Sequence: 1, Op: record.Put, Key: "k", Value: []byte("v")})
	two, _ := record.Encode(record.Record{Sequence: 2, Op: record.Delete, Key: "k"})
	prefix := append(record.FileHeader(), one...)
	for cut := 1; cut < len(two); cut++ {
		t.Run(fmt.Sprint(cut), func(t *testing.T) {
			dir := t.TempDir()
			original := append(bytes.Clone(prefix), two[:cut]...)
			path := filepath.Join(dir, "data.log")
			if err := os.WriteFile(path, original, 0600); err != nil {
				t.Fatal(err)
			}
			report, err := Inspect(dir)
			if !errors.Is(err, record.ErrIncomplete) || report.ValidBytes != 54 {
				t.Fatal(report, err)
			}
			if !bytes.Equal(original, logBytes(t, dir)) {
				t.Fatal("inspect modified log")
			}
			var diagnostic bytes.Buffer
			db, err := OpenWithOptions(dir, Options{Diagnostics: &diagnostic})
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			value(t, db, "k", []byte("v"))
			if !bytes.Equal(prefix, logBytes(t, dir)) || !strings.Contains(diagnostic.String(), `"last_valid_offset":54`) {
				t.Fatal("incorrect recovery", diagnostic.String())
			}
			if seq, err := db.Delete("k"); err != nil || seq != 2 {
				t.Fatal(seq, err)
			}
		})
	}
}

func TestCorruptionNeverTruncates(t *testing.T) {
	one, _ := record.Encode(record.Record{Sequence: 1, Op: record.Put, Key: "k", Value: []byte("v")})
	base := append(record.FileHeader(), one...)
	cases := map[string][]byte{
		"empty": {}, "short-header": record.FileHeader()[:15],
		"bad-record-crc": bytes.Clone(base), "bad-header-crc": bytes.Clone(base[:48]),
		"unknown-version": bytes.Clone(base), "bad-magic": bytes.Clone(base),
	}
	cases["bad-record-crc"][49] ^= 1
	cases["bad-header-crc"][44] ^= 1
	cases["unknown-version"][8] = 2
	cases["bad-magic"][0] ^= 1
	for name, data := range cases {
		t.Run(name, func(t *testing.T) {
			dir := t.TempDir()
			if err := os.WriteFile(filepath.Join(dir, "data.log"), data, 0600); err != nil {
				t.Fatal(err)
			}
			db, err := Open(dir)
			if err == nil || db != nil {
				t.Fatal(db, err)
			}
			if !bytes.Equal(data, logBytes(t, dir)) {
				t.Fatal("Open changed corrupt log")
			}
			report, err := Inspect(dir)
			if err == nil || report.Status == "ok" {
				t.Fatal(report, err)
			}
		})
	}
}

func TestHeaderReadFailurePreservesLog(t *testing.T) {
	for _, tc := range []struct {
		name, status string
		size         int
		cause        error
	}{
		{"short-file", "corrupt", 15, record.ErrCorrupt},
		{"header-eof", "io_error", 16, io.ErrUnexpectedEOF},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dir := t.TempDir()
			original := record.FileHeader()[:tc.size]
			if err := os.WriteFile(filepath.Join(dir, "data.log"), original, 0600); err != nil {
				t.Fatal(err)
			}
			ops := defaultOps()
			ops.wrap = func(raw *os.File) logFile { return &faultFile{File: raw, mode: tc.name} }
			report, err := inspectDisk(dir, ops.wrap)
			var problem *recovery.Problem
			if !errors.Is(err, tc.cause) || !errors.As(err, &problem) ||
				problem.Offset != 0 || problem.LastValidOffset != 0 ||
				report.Status != tc.status || report.InvalidOffset == nil || *report.InvalidOffset != 0 ||
				report.ValidBytes != 0 || report.LogBytes != int64(tc.size) ||
				report.Reason == nil || !strings.Contains(*report.Reason, "file header") {
				t.Fatalf("Inspect: report=%+v, err=%v", report, err)
			}
			if !bytes.Equal(original, logBytes(t, dir)) {
				t.Fatal("Inspect changed the log")
			}
			var diagnostic bytes.Buffer
			db, err := openDisk(dir, Options{Create: true, Diagnostics: &diagnostic}, ops)
			if db != nil || !errors.Is(err, tc.cause) || diagnostic.Len() != 0 {
				t.Fatalf("Open: db=%v, err=%v, repair diagnostic=%q", db, err, diagnostic.String())
			}
			if !bytes.Equal(original, logBytes(t, dir)) {
				t.Fatal("Open changed the log")
			}
		})
	}
}

type diagnosticFailure struct{}

func (diagnosticFailure) Write([]byte) (int, error) { return 0, syscall.EIO }
func TestRecoveryFailures(t *testing.T) {
	for _, mode := range []string{"truncate", "sync", "diagnostic"} {
		t.Run(mode, func(t *testing.T) {
			dir := t.TempDir()
			data := append(record.FileHeader(), 1, 2, 3)
			if err := os.WriteFile(filepath.Join(dir, "data.log"), data, 0600); err != nil {
				t.Fatal(err)
			}
			ops := defaultOps()
			ops.wrap = func(raw *os.File) logFile { return &faultFile{File: raw, mode: mode} }
			options := Options{Diagnostics: io.Discard}
			if mode == "diagnostic" {
				options.Diagnostics = diagnosticFailure{}
			}
			if db, err := openDisk(dir, options, ops); db != nil || !errors.Is(err, syscall.EIO) {
				t.Fatal(db, err)
			}
			if mode != "sync" && !bytes.Equal(data, logBytes(t, dir)) {
				t.Fatal("modified before diagnostic/truncate success")
			}
			lock, err := probeLock(dir)
			if err != nil {
				t.Fatal(err)
			}
			lock.Close()
		})
	}
}

func TestOversizedLogRejected(t *testing.T) {
	dir := t.TempDir()
	file, err := os.Create(filepath.Join(dir, "data.log"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = file.Write(record.FileHeader()); err != nil {
		t.Fatal(err)
	}
	if err = file.Truncate(record.MaxLogSize + 1); err != nil {
		t.Fatal(err)
	}
	file.Close()
	if db, err := Open(dir); db != nil || !errors.Is(err, record.ErrCorrupt) {
		t.Fatal(db, err)
	}
	info, err := os.Stat(filepath.Join(dir, "data.log"))
	if err != nil || info.Size() != record.MaxLogSize+1 {
		t.Fatal(info, err)
	}
}

func TestUnexpectedFiles(t *testing.T) {
	for _, kind := range []string{"directory", "symlink-log", "symlink-lock", "fifo", "unrelated"} {
		t.Run(kind, func(t *testing.T) {
			dir := t.TempDir()
			other := filepath.Join(t.TempDir(), "sentinel")
			marker := []byte("existing user data")
			if err := os.WriteFile(other, marker, 0600); err != nil {
				t.Fatal(err)
			}
			var err error
			switch kind {
			case "directory":
				err = os.Mkdir(filepath.Join(dir, "data.log"), 0700)
			case "symlink-log":
				err = os.Symlink(other, filepath.Join(dir, "data.log"))
			case "symlink-lock":
				err = os.Symlink(other, filepath.Join(dir, "LOCK"))
			case "fifo":
				err = syscall.Mkfifo(filepath.Join(dir, "data.log"), 0600)
			case "unrelated":
				err = os.WriteFile(filepath.Join(dir, "unrelated"), marker, 0600)
			}
			if err != nil {
				t.Fatal(err)
			}
			if db, err := Open(dir); err == nil || db != nil {
				t.Fatal(db, err)
			}
			if _, err := Inspect(dir); err == nil {
				t.Fatal("unexpected file accepted by Inspect")
			}
			got, err := os.ReadFile(other)
			if err != nil || !bytes.Equal(got, marker) {
				t.Fatal("sentinel modified", err)
			}
		})
	}
}

func TestDiskReferenceModelAcrossReopens(t *testing.T) {
	dir := t.TempDir()
	db := mustOpen(t, dir)
	model := map[string][]byte{}
	var seq uint64
	rng := rand.New(rand.NewSource(20261118))
	for step := 0; step < 128; step++ {
		key := fmt.Sprint(rng.Intn(8))
		seq++
		if rng.Intn(3) == 0 {
			got, err := db.Delete(key)
			if err != nil || got != seq {
				t.Fatal(got, err)
			}
			delete(model, key)
		} else {
			v := make([]byte, rng.Intn(16))
			rng.Read(v)
			put(t, db, key, v, seq)
			model[key] = bytes.Clone(v)
		}
		if step%16 == 15 {
			if err := db.Close(); err != nil {
				t.Fatal(err)
			}
			db = mustOpen(t, dir)
		}
		for i := 0; i < 8; i++ {
			key := fmt.Sprint(i)
			want, ok := model[key]
			if ok {
				value(t, db, key, want)
			} else if _, err := db.Get(key); !errors.Is(err, ErrNotFound) {
				t.Fatal(step, key, err)
			}
		}
	}
}

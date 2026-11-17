//go:build linux

package engine

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"maps"
	"os"
	"path/filepath"
	"reflect"
	"syscall"
	"testing"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

// Each handle has its own fault plan. Failures are armed only after the baseline
// is acknowledged; reopen failures cannot accidentally affect the original writes.
type compoundFile struct {
	*os.File
	mode   string
	events []string
	closes int
}

func (f *compoundFile) WriteAt(p []byte, offset int64) (int, error) {
	f.events = append(f.events, "write")
	if f.mode == "partial_header" || f.mode == "partial_body" {
		cut := 10
		if f.mode == "partial_body" {
			cut = record.HeaderSize + int(binary.LittleEndian.Uint32(p[16:20])) + 1
		}
		n, err := f.File.WriteAt(p[:cut], offset)
		if err != nil {
			return n, err
		}
		if n != cut {
			return n, io.ErrShortWrite
		}
		return n, syscall.ENOSPC
	}
	return f.File.WriteAt(p, offset)
}
func (f *compoundFile) ReadAt(p []byte, offset int64) (int, error) {
	f.events = append(f.events, "read")
	if f.mode == "read" {
		return 0, syscall.EIO
	}
	return f.File.ReadAt(p, offset)
}
func (f *compoundFile) Sync() error {
	f.events = append(f.events, "sync")
	if f.mode == "sync" {
		return syscall.EIO
	}
	return f.File.Sync()
}
func (f *compoundFile) Truncate(size int64) error {
	f.events = append(f.events, "truncate")
	if f.mode == "truncate" {
		return syscall.EIO
	}
	return f.File.Truncate(size)
}
func (f *compoundFile) Close() error {
	f.events = append(f.events, "close")
	f.closes++
	return f.File.Close()
}
func assertCompoundClosed(t *testing.T, f *compoundFile) {
	t.Helper()
	if f == nil || f.closes != 1 {
		t.Fatalf("log handle not closed exactly once: %+v", f)
	}
	if _, err := f.File.Stat(); !errors.Is(err, os.ErrClosed) {
		t.Fatalf("log FD remains open: %v", err)
	}
}
func assertCompoundLockReleased(t *testing.T, dir string, original os.FileInfo) {
	t.Helper()
	lock, err := lockDirectory(dir)
	if err != nil {
		t.Fatalf("lock leaked: %v", err)
	}
	info, statErr := lock.Stat()
	closeErr := lock.Close()
	if statErr != nil || closeErr != nil || !os.SameFile(original, info) {
		t.Fatalf("LOCK replaced or cleanup failed: %v %v", statErr, closeErr)
	}
}

func TestCompoundIORecovery(t *testing.T) {
	for _, op := range []string{"put", "delete"} {
		for _, updateFault := range []string{"partial_header", "partial_body", "sync"} {
			reopenFaults := []string{"none", "read", "dir", "parent"}
			if updateFault != "sync" {
				reopenFaults = append(reopenFaults, "diagnostic", "truncate", "sync")
			}
			for _, reopenFault := range reopenFaults {
				t.Run(op+"/"+updateFault+"/reopen_"+reopenFault, func(t *testing.T) {
					dir := t.TempDir()
					var writer *compoundFile
					ops := defaultOps()
					ops.wrap = func(f *os.File) logFile { writer = &compoundFile{File: f}; return writer }
					db, err := openDisk(dir, Options{Create: true}, ops)
					if err != nil {
						t.Fatal(err)
					}
					defer db.Close()
					history := []crashRequest{
						{Op: "put", Key: "target", Value: []byte("acknowledged")},
						{Op: "put", Key: "keep", Value: []byte{0, 255, 10}},
						{Op: "put", Key: "empty", Value: []byte{}},
						{Op: "delete", Key: "missing"},
					}
					for i, req := range history {
						var seq uint64
						if req.Op == "put" {
							seq, err = db.Put(req.Key, req.Value)
						} else {
							seq, err = db.Delete(req.Key)
						}
						if err != nil || seq != uint64(i+1) {
							t.Fatal(seq, err)
						}
					}
					baseline := logBytes(t, dir)
					baselineReport := db.state.Report
					baselineIndex := maps.Clone(db.state.Index)
					lockInfo, err := db.lock.Stat()
					if err != nil {
						t.Fatal(err)
					}
					pending := crashRequest{Op: op, Key: "target"}
					if op == "put" {
						pending.Value = []byte{255, 0, 1, 10}
					}
					writer.mode = updateFault
					writer.events = nil
					var seq uint64
					if op == "put" {
						seq, err = db.Put(pending.Key, pending.Value)
					} else {
						seq, err = db.Delete(pending.Key)
					}
					cause := error(syscall.ENOSPC)
					wantEvents := []string{"write"}
					if updateFault == "sync" {
						cause = syscall.EIO
						wantEvents = append(wantEvents, "sync")
					}
					if seq != 0 || !errors.Is(err, cause) || !reflect.DeepEqual(writer.events, wantEvents) {
						t.Fatalf("bad update failure: seq=%d err=%v events=%v", seq, err, writer.events)
					}
					failedBytes := logBytes(t, dir)
					if !bytes.HasPrefix(failedBytes, baseline) {
						t.Fatal("acknowledged prefix modified")
					}
					tailLength := 10
					if updateFault == "partial_body" {
						tailLength = record.HeaderSize + len(pending.Key) + 1
					}
					if updateFault == "sync" {
						tailLength = record.HeaderSize + len(pending.Key) + len(pending.Value) + 4
					}
					if len(failedBytes) != len(baseline)+tailLength {
						t.Fatal("fault not injected at expected length")
					}
					// A failed update must not publish an index/sequence or perform any later I/O.
					if !reflect.DeepEqual(db.state.Report, baselineReport) || !reflect.DeepEqual(db.state.Index, baselineIndex) {
						t.Fatal("failed update published state")
					}
					if s, e := db.Put("forbidden", nil); s != 0 || !errors.Is(e, ErrRecoveryRequired) {
						t.Fatal(s, e)
					}
					if s, e := db.Delete("target"); s != 0 || !errors.Is(e, ErrRecoveryRequired) {
						t.Fatal(s, e)
					}
					if v, e := db.Get("keep"); v != nil || !errors.Is(e, ErrRecoveryRequired) {
						t.Fatal(v, e)
					}
					if _, e := db.Inspect(); !errors.Is(e, ErrRecoveryRequired) {
						t.Fatal(e)
					}
					if !reflect.DeepEqual(writer.events, wantEvents) || !bytes.Equal(failedBytes, logBytes(t, dir)) {
						t.Fatal("failed DB continued I/O")
					}
					if _, e := Inspect(dir); !errors.Is(e, ErrLocked) {
						t.Fatalf("failed DB released lock early: %v", e)
					}
					if err := db.Close(); err != nil {
						t.Fatal(err)
					}
					assertCompoundClosed(t, writer)
					assertCompoundLockReleased(t, dir, lockInfo)

					// Independent inspect must distinguish complete-but-unsynced from partial records.
					scan, scanErr := Inspect(dir)
					partial := updateFault != "sync"
					if partial {
						if !errors.Is(scanErr, record.ErrIncomplete) || scan.ValidBytes != int64(len(baseline)) || scan.LatestSequence != 4 {
							t.Fatal(scan, scanErr)
						}
					} else if scanErr != nil || scan.LatestSequence != 5 {
						t.Fatal(scan, scanErr)
					}
					if !bytes.Equal(failedBytes, logBytes(t, dir)) {
						t.Fatal("inspect changed failed update")
					}
					var reopenedFile *compoundFile
					var diagnostic bytes.Buffer
					var dirEvents []string
					reopenOps := defaultOps()
					reopenOps.wrap = func(f *os.File) logFile {
						reopenedFile = &compoundFile{File: f, mode: reopenFault}
						return reopenedFile
					}
					reopenOps.syncDir = func(path string) error {
						point := "parent"
						if filepath.Clean(path) == filepath.Clean(dir) {
							point = "dir"
						}
						dirEvents = append(dirEvents, point)
						if reopenFault == point {
							return syscall.EIO
						}
						return syncDirectory(path)
					}
					options := Options{Diagnostics: &diagnostic}
					if reopenFault == "diagnostic" {
						options.Diagnostics = io.MultiWriter(&diagnostic, diagnosticFailure{})
					}
					reopened, openErr := openDisk(dir, options, reopenOps)
					if reopenFault == "none" {
						if openErr != nil {
							t.Fatal(openErr)
						}
						if err := reopened.Close(); err != nil {
							t.Fatal(err)
						}
					} else {
						if reopened != nil || !errors.Is(openErr, syscall.EIO) {
							t.Fatalf("failed reopen returned usable DB: %v %v", reopened, openErr)
						}
					}
					assertCompoundClosed(t, reopenedFile)
					assertCompoundLockReleased(t, dir, lockInfo)
					// Repair Sync fails after Truncate has succeeded. Other failures must leave
					// the whole log unchanged, including its incomplete tail.
					truncated := partial && (reopenFault == "none" || reopenFault == "sync")
					wantBytes := failedBytes
					if truncated {
						wantBytes = baseline
					}
					if !bytes.Equal(logBytes(t, dir), wantBytes) {
						t.Fatal("reopen changed unexpected bytes")
					}
					wantDiagnostic := partial && (reopenFault == "none" || reopenFault == "diagnostic" || reopenFault == "truncate" || reopenFault == "sync")
					if wantDiagnostic {
						var event struct {
							Event           string `json:"event"`
							OldSize         int64  `json:"old_size"`
							LastValidOffset int64  `json:"last_valid_offset"`
						}
						if err := json.Unmarshal(diagnostic.Bytes(), &event); err != nil || event.Event != "tail_truncate_planned" || event.OldSize != int64(len(failedBytes)) || event.LastValidOffset != int64(len(baseline)) {
							t.Fatalf("bad repair diagnostic: %s %v", diagnostic.Bytes(), err)
						}
					} else if diagnostic.Len() != 0 {
						t.Fatal("unexpected diagnostic")
					}
					hasEvent := func(name string) bool {
						for _, event := range reopenedFile.events {
							if event == name {
								return true
							}
						}
						return false
					}
					if hasEvent("write") || hasEvent("truncate") != (partial && (reopenFault == "none" || reopenFault == "truncate" || reopenFault == "sync")) || hasEvent("sync") != truncated {
						t.Fatalf("unexpected repair I/O: %v", reopenedFile.events)
					}
					if reopenFault == "dir" && !reflect.DeepEqual(dirEvents, []string{"dir"}) {
						t.Fatal(dirEvents)
					}
					if reopenFault != "dir" && !reflect.DeepEqual(dirEvents, []string{"dir", "parent"}) {
						t.Fatal(dirEvents)
					}

					// Remove the injected failure and retry on exactly the same real files.
					diagnostic.Reset()
					recovered, err := OpenWithOptions(dir, Options{Diagnostics: &diagnostic})
					if err != nil {
						t.Fatal(err)
					}
					defer recovered.Close()
					if (diagnostic.Len() != 0) != (partial && !truncated) {
						t.Fatal("unexpected retry repair")
					}
					selected := history
					if !partial {
						selected = append(append([]crashRequest{}, history...), pending)
					}
					keys := []string{"target", "keep", "empty", "missing", "forbidden", "next"}
					if !reflect.DeepEqual(recoveredModel(t, recovered, keys), modelAfter(selected)) {
						t.Fatal("recovery lost ack or exposed partial operation")
					}
					report, err := recovered.Inspect()
					if err != nil || report.LatestSequence != uint64(len(selected)) || report.RecordCount != uint64(len(selected)) {
						t.Fatal(report, err)
					}
					if partial && !bytes.Equal(logBytes(t, dir), baseline) || !partial && !bytes.Equal(logBytes(t, dir), failedBytes) {
						t.Fatal("retry changed valid prefix")
					}
					if err := recovered.Close(); err != nil {
						t.Fatal(err)
					}
					diagnostic.Reset()
					again, err := OpenWithOptions(dir, Options{Diagnostics: &diagnostic})
					if err != nil {
						t.Fatal(err)
					}
					defer again.Close()
					if diagnostic.Len() != 0 || !reflect.DeepEqual(recoveredModel(t, again, keys), modelAfter(selected)) {
						t.Fatal("recovery not idempotent")
					}
					put(t, again, "next", []byte{0, 255}, uint64(len(selected)+1))
					if err := again.Close(); err != nil {
						t.Fatal(err)
					}
					final := mustOpen(t, dir)
					expected := modelAfter(selected)
					expected["next"] = []byte{0, 255}
					finalReport, err := final.Inspect()
					if err != nil || finalReport.LatestSequence != uint64(len(selected)+1) || !reflect.DeepEqual(recoveredModel(t, final, keys), expected) {
						t.Fatal("next update not retained")
					}
					t.Logf("update=%s reopen=%s original=%d failed=%d after_reopen=%d recovered_sequence=%d", updateFault, reopenFault, len(baseline), len(failedBytes), len(wantBytes), report.LatestSequence)
				})
			}
		}
	}
}

func TestCompoundIONormalControl(t *testing.T) {
	dir := t.TempDir()
	var file *compoundFile
	ops := defaultOps()
	ops.wrap = func(f *os.File) logFile { file = &compoundFile{File: f}; return file }
	db, err := openDisk(dir, Options{Create: true}, ops)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	put(t, db, "k", []byte{0, 255}, 1)
	if seq, err := db.Delete("k"); err != nil || seq != 2 {
		t.Fatal(seq, err)
	}
	put(t, db, "empty", nil, 3)
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	assertCompoundClosed(t, file)
	var diagnostic bytes.Buffer
	again, err := openDisk(dir, Options{Diagnostics: &diagnostic}, ops)
	if err != nil {
		t.Fatal(err)
	}
	defer again.Close()
	value(t, again, "empty", nil)
	if _, err := again.Get("k"); !errors.Is(err, ErrNotFound) {
		t.Fatal(err)
	}
	if diagnostic.Len() != 0 {
		t.Fatal("normal log repaired")
	}
	if err := again.Close(); err != nil {
		t.Fatal(err)
	}
	assertCompoundClosed(t, file)
}

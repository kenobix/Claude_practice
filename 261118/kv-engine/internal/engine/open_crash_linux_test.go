//go:build linux

package engine

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

var initializationPoints = []string{
	"init_before_header", "init_partial_header", "init_after_header",
	"init_before_file_sync", "init_after_file_sync",
	"init_before_dir_sync", "init_after_dir_sync",
	"init_before_parent_sync", "init_after_parent_sync",
}
var recoveryPoints = []string{
	"recovery_before_truncate", "recovery_after_truncate",
	"recovery_before_sync", "recovery_after_sync",
}

// openCrashLog intercepts only initialization/repair I/O, using the same parent rendezvous.
type openCrashLog struct {
	*crashLog
	openPoint string
	truncated bool
}

func (f *openCrashLog) WriteAt(p []byte, offset int64) (int, error) {
	if f.openPoint == "init_before_header" {
		f.pause(f.openPoint)
	}
	if f.openPoint == "init_partial_header" {
		if offset != 0 || len(p) != record.FileHeaderSize {
			return 0, fmt.Errorf("unexpected initial header write")
		}
		n, err := f.File.WriteAt(p[:8], offset)
		if err != nil {
			return n, err
		}
		if n != 8 {
			return n, io.ErrShortWrite
		}
		f.pause(f.openPoint)
	}
	n, err := f.File.WriteAt(p, offset)
	if err == nil && n == len(p) && f.openPoint == "init_after_header" {
		f.pause(f.openPoint)
	}
	return n, err
}
func (f *openCrashLog) Sync() error {
	if f.openPoint == "init_before_file_sync" || f.openPoint == "recovery_before_sync" && f.truncated {
		f.pause(f.openPoint)
	}
	if err := f.File.Sync(); err != nil {
		return err
	}
	if f.openPoint == "init_after_file_sync" || f.openPoint == "recovery_after_sync" && f.truncated {
		f.pause(f.openPoint)
	}
	return nil
}
func (f *openCrashLog) Truncate(size int64) error {
	if f.openPoint == "recovery_before_truncate" {
		f.pause(f.openPoint)
	}
	if err := f.File.Truncate(size); err != nil {
		return err
	}
	f.truncated = true
	if f.openPoint == "recovery_after_truncate" {
		f.pause(f.openPoint)
	}
	return nil
}
func openCrashOps(dir, point string, events *json.Encoder) (diskOps, Options, error) {
	known := false
	for _, p := range append(append([]string{}, initializationPoints...), recoveryPoints...) {
		if p == point {
			known = true
		}
	}
	if !known {
		return diskOps{}, Options{}, fmt.Errorf("unknown Open checkpoint: %q", point)
	}
	var log *openCrashLog
	ops := defaultOps()
	ops.wrap = func(raw *os.File) logFile {
		log = &openCrashLog{crashLog: &crashLog{File: raw, events: events}, openPoint: point}
		return log
	}
	ops.syncDir = func(path string) error {
		stage := "parent"
		if filepath.Clean(path) == filepath.Clean(dir) {
			stage = "dir"
		}
		if log.openPoint == "init_before_"+stage+"_sync" {
			log.pause(log.openPoint)
		}
		if err := syncDirectory(path); err != nil {
			return err
		}
		if log.openPoint == "init_after_"+stage+"_sync" {
			log.pause(log.openPoint)
		}
		return nil
	}
	return ops, Options{Create: strings.HasPrefix(point, "init_")}, nil
}
func killAtOpen(t *testing.T, dir, point string) {
	t.Helper()
	child := startCrashChildAtOpen(t, dir, point)
	event := child.read(t)
	if event.Event != "stopped" || event.Point != point {
		t.Fatalf("Open returned/acknowledged before checkpoint: %+v", event)
	}
	// First event must be the checkpoint, never ready or an operation ack.
	child.kill(t)
}

func TestCrashInitializationCheckpoints(t *testing.T) {
	for _, point := range initializationPoints {
		t.Run(point, func(t *testing.T) {
			dir := t.TempDir()
			killAtOpen(t, dir, point)
			before := logBytes(t, dir)
			if point == "init_before_header" || point == "init_partial_header" {
				want := 0
				if point == "init_partial_header" {
					want = 8
				}
				if len(before) != want {
					t.Fatal("unexpected short header length", len(before))
				}
				scan, err := Inspect(dir)
				if !errors.Is(err, record.ErrCorrupt) || scan.ValidBytes != 0 {
					t.Fatal(scan, err)
				}
				for attempt := 0; attempt < 2; attempt++ {
					if db, err := Open(dir); db != nil || !errors.Is(err, record.ErrCorrupt) {
						t.Fatal("short header was reset", db, err)
					}
					if !bytes.Equal(before, logBytes(t, dir)) {
						t.Fatal("short header changed")
					}
				}
				// Failed initialization must still release the advisory lock.
				lock, err := lockDirectory(dir)
				if err != nil {
					t.Fatal(err)
				}
				lock.Close()
				t.Logf("point=%s acknowledged=0 bytes=%d reopen=corrupt unchanged=true", point, len(before))
				return
			}
			if !bytes.Equal(before, record.FileHeader()) {
				t.Fatal("initial header differs from v1")
			}
			db := mustOpen(t, dir)
			report, err := db.Inspect()
			if err != nil || report.RecordCount != 0 || report.LatestSequence != 0 || report.LiveKeys != 0 {
				t.Fatal(report, err)
			}
			put(t, db, "first", []byte{0, 255}, 1)
			if err := db.Close(); err != nil {
				t.Fatal(err)
			}
			again := mustOpen(t, dir)
			value(t, again, "first", []byte{0, 255})
			t.Logf("point=%s acknowledged=0 bytes=%d reopen=empty first_sequence=1", point, len(before))
		})
	}
}

func TestCrashRecoveryOpenCheckpoints(t *testing.T) {
	for _, point := range recoveryPoints {
		t.Run(point, func(t *testing.T) {
			dir := t.TempDir()
			db := mustOpen(t, dir)
			if err := db.Close(); err != nil {
				t.Fatal(err)
			}
			writer := startCrashChild(t, dir)
			history := []crashRequest{
				{Op: "put", Key: "keep", Value: []byte{0, 255}},
				{Op: "put", Key: "target", Value: []byte("committed")},
				{Op: "put", Key: "empty", Value: []byte{}},
			}
			for i, req := range history {
				writer.send(t, req)
				event := writer.read(t)
				if event.Event != "ack" || event.Sequence != uint64(i+1) {
					t.Fatalf("bad ack: %+v", event)
				}
			}
			// Produce the partial tail through the real update path, then kill a second worker during Open.
			writer.send(t, crashRequest{Op: "delete", Key: "target", Stop: "partial_body"})
			event := writer.read(t)
			if event.Event != "stopped" || event.Point != "partial_body" {
				t.Fatal(event)
			}
			writer.kill(t)
			scan, err := Inspect(dir)
			if !errors.Is(err, record.ErrIncomplete) || scan.LatestSequence != 3 {
				t.Fatal(scan, err)
			}
			partial := logBytes(t, dir)
			prefix := bytes.Clone(partial[:scan.ValidBytes])
			killAtOpen(t, dir, point)
			after := logBytes(t, dir)
			if point == "recovery_before_truncate" {
				if !bytes.Equal(after, partial) {
					t.Fatal("changed before Truncate")
				}
			} else if !bytes.Equal(after, prefix) {
				t.Fatal("wrong truncated prefix")
			}
			var diagnostic bytes.Buffer
			reopened, err := OpenWithOptions(dir, Options{Diagnostics: &diagnostic})
			if err != nil {
				t.Fatal(err)
			}
			defer reopened.Close()
			if got := recoveredModel(t, reopened, []string{"keep", "target", "empty"}); !reflect.DeepEqual(got, modelAfter(history)) {
				t.Fatalf("lost ack during repair: %v", got)
			}
			report, err := reopened.Inspect()
			if err != nil || report.LatestSequence != 3 || report.LogBytes != scan.ValidBytes {
				t.Fatal(report, err)
			}
			if point == "recovery_before_truncate" {
				if diagnostic.Len() == 0 {
					t.Fatal("remaining partial tail not diagnosed")
				}
			} else if diagnostic.Len() != 0 {
				t.Fatalf("already truncated log repaired twice: %s", diagnostic.String())
			}
			if err := reopened.Close(); err != nil {
				t.Fatal(err)
			}
			// A second clean Open must not repeat repair or alter the validated prefix.
			var second bytes.Buffer
			again, err := OpenWithOptions(dir, Options{Diagnostics: &second})
			if err != nil {
				t.Fatal(err)
			}
			defer again.Close()
			if second.Len() != 0 || !bytes.Equal(prefix, logBytes(t, dir)) {
				t.Fatal("repair not idempotent")
			}
			put(t, again, "next", nil, 4)
			t.Logf("point=%s acknowledged=%d partial_bytes=%d valid_bytes=%d bytes_after_kill=%d recovered_sequence=3 next_sequence=4", point, len(history), len(partial), scan.ValidBytes, len(after))
		})
	}
}

func TestCrashOpenNormalAndRejectedInput(t *testing.T) {
	for _, mode := range []string{"initialization", "recovery", "unknown_checkpoint"} {
		t.Run(mode, func(t *testing.T) {
			dir := t.TempDir()
			encoder := json.NewEncoder(io.Discard)
			if mode == "unknown_checkpoint" {
				if _, _, err := openCrashOps(dir, "unknown", encoder); err == nil {
					t.Fatal("unknown checkpoint accepted")
				}
				if _, err := os.Stat(filepath.Join(dir, "data.log")); !errors.Is(err, os.ErrNotExist) {
					t.Fatal("invalid setup created log", err)
				}
				return
			}
			point := "init_after_parent_sync"
			if mode == "recovery" {
				point = "recovery_after_sync"
				initial := mustOpen(t, dir)
				put(t, initial, "keep", []byte("ok"), 1)
				if err := initial.Close(); err != nil {
					t.Fatal(err)
				}
				raw, err := os.OpenFile(filepath.Join(dir, "data.log"), os.O_WRONLY|os.O_APPEND, 0)
				if err != nil {
					t.Fatal(err)
				}
				_, writeErr := raw.Write([]byte{1, 2, 3})
				closeErr := raw.Close()
				if writeErr != nil || closeErr != nil {
					t.Fatal(writeErr, closeErr)
				}
			}
			ops, options, err := openCrashOps(dir, point, encoder)
			if err != nil {
				t.Fatal(err)
			}
			// Disable only pauses: keep the same wrappers and directory Sync routing as the kill cases.
			originalWrap := ops.wrap
			ops.wrap = func(raw *os.File) logFile {
				file := originalWrap(raw).(*openCrashLog)
				file.openPoint = ""
				return file
			}
			options.Diagnostics = io.Discard
			opened, err := openDisk(dir, options, ops)
			if err != nil {
				t.Fatal(err)
			}
			if err := opened.Close(); err != nil {
				t.Fatal(err)
			}
			again := mustOpen(t, dir)
			if mode == "recovery" {
				value(t, again, "keep", []byte("ok"))
			} else if report, err := again.Inspect(); err != nil || report.RecordCount != 0 {
				t.Fatal(report, err)
			}
		})
	}
}

//go:build linux

package engine

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"syscall"
	"testing"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

type recoveryDiagnosticWriter struct {
	mode                string
	calls               int
	attempted, accepted []byte
}

func (w *recoveryDiagnosticWriter) Write(p []byte) (int, error) {
	w.calls++
	w.attempted = bytes.Clone(p)
	n, err := len(p), error(nil)
	switch w.mode {
	case "zero":
		n = 0
	case "partial":
		n = len(p) / 2
	case "partial-error":
		n, err = len(p)/2, syscall.EIO
	case "full-error":
		err = syscall.EIO
	}
	w.accepted = append(w.accepted, p[:n]...)
	return n, err
}

type diagnosticGuardLog struct {
	*os.File
	truncates, syncs, closes int
}

func (f *diagnosticGuardLog) Truncate(size int64) error { f.truncates++; return f.File.Truncate(size) }
func (f *diagnosticGuardLog) Sync() error               { f.syncs++; return f.File.Sync() }
func (f *diagnosticGuardLog) Close() error              { f.closes++; return f.File.Close() }

func TestRecoveryDiagnosticCompletion(t *testing.T) {
	for _, mode := range []string{"zero", "partial", "partial-error", "full-error", "ok"} {
		t.Run(mode, func(t *testing.T) {
			dir := t.TempDir()
			one, err := record.Encode(record.Record{Sequence: 1, Op: record.Put, Key: "k", Value: []byte("v")})
			if err != nil {
				t.Fatal(err)
			}
			two, err := record.Encode(record.Record{Sequence: 2, Op: record.Delete, Key: "k"})
			if err != nil {
				t.Fatal(err)
			}
			prefix := append(record.FileHeader(), one...)
			original := append(bytes.Clone(prefix), two[:10]...)
			if err := os.WriteFile(filepath.Join(dir, "data.log"), original, 0600); err != nil {
				t.Fatal(err)
			}
			writer := &recoveryDiagnosticWriter{mode: mode}
			var file *diagnosticGuardLog
			ops := defaultOps()
			ops.wrap = func(raw *os.File) logFile { file = &diagnosticGuardLog{File: raw}; return file }
			db, err := openDisk(dir, Options{Diagnostics: writer}, ops)
			// Ensure an unexpected success in a negative case cannot leak a handle.
			if db != nil {
				t.Cleanup(func() { _ = db.Close() })
			}
			wantErr := error(nil)
			switch mode {
			case "zero", "partial":
				wantErr = io.ErrShortWrite
			case "partial-error", "full-error":
				wantErr = syscall.EIO
			}
			if wantErr != nil {
				if db != nil || !errors.Is(err, wantErr) {
					t.Fatalf("db=%v error=%v want=%v", db, err, wantErr)
				}
				if file.truncates != 0 || file.syncs != 0 || !bytes.Equal(logBytes(t, dir), original) {
					t.Fatalf("diagnostic failure changed log: truncate=%d sync=%d", file.truncates, file.syncs)
				}
			} else {
				if err != nil || db == nil {
					t.Fatal(db, err)
				}
				if file.truncates != 1 || file.syncs != 1 || !bytes.Equal(logBytes(t, dir), prefix) {
					t.Fatal("complete diagnostic did not permit synchronized recovery")
				}
				value(t, db, "k", []byte("v"))
				if err := db.Close(); err != nil {
					t.Fatal(err)
				}
			}
			if file.closes != 1 {
				t.Fatalf("log Close count=%d", file.closes)
			}
			if _, err := file.Stat(); !errors.Is(err, os.ErrClosed) {
				t.Fatalf("log FD not closed: %v", err)
			}
			lock, err := probeLock(dir)
			if err != nil {
				t.Fatalf("LOCK not released: %v", err)
			}
			if err := lock.Close(); err != nil {
				t.Fatal(err)
			}
			if writer.calls != 1 {
				t.Fatalf("diagnostic writes=%d", writer.calls)
			}
			var event struct {
				Event           string `json:"event"`
				OldSize         int64  `json:"old_size"`
				LastValidOffset int64  `json:"last_valid_offset"`
				Reason          string `json:"reason"`
			}
			if err := json.Unmarshal(writer.attempted, &event); err != nil || event.Event != "tail_truncate_planned" || event.OldSize != 64 || event.LastValidOffset != 54 || event.Reason == "" || !bytes.HasSuffix(writer.attempted, []byte("\n")) {
				t.Fatalf("event=%+v err=%v attempted=%q", event, err, writer.attempted)
			}
			if mode == "zero" && len(writer.accepted) != 0 || (mode == "partial" || mode == "partial-error") && len(writer.accepted) != len(writer.attempted)/2 {
				t.Fatal("diagnostic fault not applied")
			}
			// A complete writer can retry the same file, then a second Open needs no repair.
			for attempt := 0; attempt < 2; attempt++ {
				var diagnostic bytes.Buffer
				retry, err := OpenWithOptions(dir, Options{Diagnostics: &diagnostic})
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = retry.Close() })
				value(t, retry, "k", []byte("v"))
				report, err := retry.Inspect()
				if err != nil || report.LatestSequence != 1 || report.LiveKeys != 1 || report.LogBytes != 54 {
					t.Fatal(report, err)
				}
				wantDiagnostic := wantErr != nil && attempt == 0
				if (diagnostic.Len() > 0) != wantDiagnostic || !bytes.Equal(logBytes(t, dir), prefix) {
					t.Fatal("unexpected retry recovery", diagnostic.String())
				}
				if err := retry.Close(); err != nil {
					t.Fatal(err)
				}
			}
		})
	}
}

//go:build linux

package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"syscall"
	"testing"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/engine"
)

var responseValue = []byte{0, 255, 10, 1}

func responseFixture(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	db, err := engine.Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.Close() })
	for _, pair := range []struct {
		key   string
		value []byte
	}{{"target", []byte("old")}, {"keep", responseValue}, {"empty", []byte{}}} {
		if _, err := db.Put(pair.key, pair.value); err != nil {
			t.Fatal(err)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	return dir
}

func responseArgs(op, dir string) []string {
	args := []string{op, "--db", dir}
	if op != "inspect" {
		args = append(args, "--key", "target")
	}
	if op == "put" {
		args = append(args, "--value-file", "-")
	}
	return args
}

func checkResponseDB(t *testing.T, dir, op string) {
	t.Helper()
	db, err := engine.Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	wantSeq := uint64(3)
	if op == "put" || op == "delete" {
		wantSeq = 4
	}
	report, err := db.Inspect()
	if err != nil || report.LatestSequence != wantSeq {
		t.Fatalf("report=%+v error=%v", report, err)
	}
	wantBytes, wantPut, wantDelete, wantLive := int64(146), uint64(3), uint64(0), 3
	if op == "put" {
		wantBytes, wantPut = 192, 4
	}
	if op == "delete" {
		wantBytes, wantDelete, wantLive = 188, 1, 2
	}
	if report.LogBytes != wantBytes || report.ValidBytes != wantBytes || report.RecordCount != wantSeq || report.PutCount != wantPut || report.DeleteCount != wantDelete || report.LiveKeys != wantLive || report.Status != "ok" {
		t.Fatalf("unexpected report: %+v", report)
	}
	for _, key := range []string{"target", "keep", "empty", "missing"} {
		value, err := db.Get(key)
		if key == "missing" || key == "target" && op == "delete" {
			if !errors.Is(err, engine.ErrNotFound) {
				t.Fatalf("%s: %v", key, err)
			}
			continue
		}
		want := []byte("old")
		if key == "keep" || key == "target" && op == "put" {
			want = responseValue
		}
		if key == "empty" {
			want = []byte{}
		}
		if err != nil || !bytes.Equal(value, want) {
			t.Fatalf("%s: value=%x want=%x err=%v", key, value, want, err)
		}
	}
}

type responseWriter struct {
	bytes.Buffer
	mode   string
	before func()
}

func (w *responseWriter) Write(p []byte) (int, error) {
	if w.before != nil {
		w.before()
	}
	switch w.mode {
	case "eio":
		return 0, syscall.EIO
	case "pipe":
		return 0, syscall.EPIPE
	case "zero":
		return 0, nil
	case "short":
		return w.Buffer.Write(p[:len(p)/2])
	case "partial-error":
		n, _ := w.Buffer.Write(p[:len(p)/2])
		return n, syscall.EIO
	default:
		return w.Buffer.Write(p)
	}
}

func TestPersistentResponseFailures(t *testing.T) {
	for _, op := range []string{"put", "delete", "get", "inspect"} {
		for _, mode := range []string{"ok", "eio", "pipe", "zero", "short", "partial-error"} {
			t.Run(op+"/"+mode, func(t *testing.T) {
				dir := responseFixture(t)
				// The stdout callback must be able to acquire the lock: Close precedes output.
				out := &responseWriter{mode: mode, before: func() {
					if _, err := engine.Inspect(dir); err != nil {
						t.Fatalf("stdout before unlock: %v", err)
					}
				}}
				var stderr bytes.Buffer
				code := runPersistent(responseArgs(op, dir), bytes.NewReader(responseValue), out, &stderr)
				wantCode := 6
				if mode == "ok" {
					wantCode = 0
				}
				if code != wantCode {
					t.Fatalf("exit=%d want=%d stdout=%q stderr=%s", code, wantCode, out.String(), stderr.String())
				}
				if mode == "ok" {
					switch op {
					case "put", "delete":
						if out.String() != "{\"sequence\":4}\n" {
							t.Fatal(out.String())
						}
					case "get":
						if out.String() != "old" {
							t.Fatal(out.String())
						}
					case "inspect":
						var report struct {
							Status         string
							LatestSequence uint64 `json:"latest_sequence"`
						}
						if err := json.Unmarshal(out.Bytes(), &report); err != nil || report.Status != "ok" || report.LatestSequence != 3 {
							t.Fatalf("report=%+v err=%v", report, err)
						}
					}
				}
				if mode == "ok" && stderr.Len() != 0 {
					t.Fatal(stderr.String())
				}
				if mode != "ok" && !bytes.Contains(stderr.Bytes(), []byte(`"error":"io"`)) {
					t.Fatal(stderr.String())
				}
				if mode == "zero" || mode == "eio" || mode == "pipe" {
					if out.Len() != 0 {
						t.Fatal("unexpected stdout", out.String())
					}
				}
				checkResponseDB(t, dir, op)
			})
		}
	}
}

type responseCloseDB struct {
	*engine.DB
	closeFn func() error
}

func (db *responseCloseDB) Close() error { return db.closeFn() }

func TestPersistentCloseFailureSuppressesOutput(t *testing.T) {
	for _, op := range []string{"put", "delete", "get"} {
		t.Run(op, func(t *testing.T) {
			dir := responseFixture(t)
			closes := 0
			open := func(dir string, options engine.Options) (persistentDB, error) {
				db, err := engine.OpenWithOptions(dir, options)
				if err != nil {
					return nil, err
				}
				t.Cleanup(func() { _ = db.Close() })
				return &responseCloseDB{db, func() error { closes++; return errors.Join(db.Close(), syscall.EIO) }}, nil
			}
			var out, stderr bytes.Buffer
			code := runPersistentWithOpen(responseArgs(op, dir), bytes.NewReader(responseValue), &out, &stderr, open)
			if code != 6 || out.Len() != 0 || closes != 1 || !bytes.Contains(stderr.Bytes(), []byte(`"error":"io"`)) {
				t.Fatalf("exit=%d stdout=%q closes=%d stderr=%s", code, out.String(), closes, stderr.String())
			}
			checkResponseDB(t, dir, op)
		})
	}
}

func TestPersistentEmptyResponse(t *testing.T) {
	dir := responseFixture(t)
	var out, stderr bytes.Buffer
	if code := runPersistent([]string{"get", "--db", dir, "--key", "empty"}, nil, &out, &stderr); code != 0 || out.Len() != 0 || stderr.Len() != 0 {
		t.Fatalf("exit=%d stdout=%q stderr=%s", code, out.String(), stderr.String())
	}
}

var _ io.Writer = (*responseWriter)(nil)

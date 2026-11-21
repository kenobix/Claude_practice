//go:build linux

package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"testing"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/engine"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

func TestBenchValidationBeforeIO(t *testing.T) {
	parent := t.TempDir()
	sentinel := filepath.Join(parent, "keep")
	if err := os.WriteFile(sentinel, []byte("existing"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{
		{"--keys", "0"}, {"--keys", "1,1"}, {"--keys", "1000001"}, {"--keys", "x"},
		{"--value-bytes", "-1"}, {"--value-bytes", "1048577"},
		{"--open-samples", "0"}, {"--update-samples", "10001"},
		{"--keys", "255", "--value-bytes", "1048576", "--update-samples", "1"}, {"unexpected"},
	} {
		var out, stderr bytes.Buffer
		if code := run(append([]string{"--work-dir", parent}, args...), &out, &stderr); code != 2 {
			t.Fatalf("%v: exit=%d %s", args, code, stderr.String())
		}
		if out.Len() != 0 {
			t.Fatalf("invalid config produced results: %v", args)
		}
	}
	var out, stderr bytes.Buffer
	if code := run(nil, &out, &stderr); code != 2 {
		t.Fatal(code)
	}
	entries, err := os.ReadDir(parent)
	if err != nil || len(entries) != 1 || entries[0].Name() != "keep" {
		t.Fatal(entries, err)
	}
	b, err := os.ReadFile(sentinel)
	if err != nil || string(b) != "existing" {
		t.Fatal(string(b), err)
	}
	// Adjacent record counts straddle capacity at maximum value size; no DB allocation.
	if _, err := parse([]string{"--work-dir", parent, "--keys", "254", "--value-bytes", "1048576", "--update-samples", "1"}); err != nil {
		t.Fatal(err)
	}
	if len(key(999999)) != 16 || logSize(255, record.MaxValueSize) > record.MaxLogSize {
		t.Fatal("capacity preflight mismatch")
	}
}

func TestBenchRealFilesAndCleanup(t *testing.T) {
	for _, size := range []int{0, 128, record.MaxValueSize} {
		parent := t.TempDir()
		var out, stderr bytes.Buffer
		args := []string{"--work-dir", parent, "--keys", "1,3", "--open-samples", "2", "--update-samples", "4", "--value-bytes", strconv.Itoa(size)}
		if code := run(args, &out, &stderr); code != 0 {
			t.Fatalf("exit=%d %s", code, stderr.String())
		}
		var got report
		if err := json.Unmarshal(out.Bytes(), &got); err != nil {
			t.Fatal(err)
		}
		if got.Schema != 1 || got.KeyBytes != 16 || got.ValueBytes != size || len(got.Cases) != 2 || got.Environment.Go == "" || got.Cache == "" {
			t.Fatalf("metadata: %+v", got)
		}
		for i, r := range got.Cases {
			if !r.Verified || r.Keys != []int{1, 3}[i] || len(r.Opens) != 2 || len(r.UpdateNS) != 4 || r.FinalLogBytes != logSize(r.Keys+4, size) {
				t.Fatalf("result: %+v", r)
			}
			for _, s := range r.Opens {
				if s.Nanoseconds <= 0 || s.HeapDelta != int64(s.HeapLive)-int64(s.HeapBefore) {
					t.Fatal(s)
				}
			}
			for _, n := range r.UpdateNS {
				if n <= 0 {
					t.Fatal(n)
				}
			}
		}
		entries, err := os.ReadDir(parent)
		if err != nil || len(entries) != 0 {
			t.Fatal(entries, err)
		}
	}
}

type shortWriter struct{}

func (shortWriter) Write(b []byte) (int, error) { return len(b) - 1, nil }
func TestBenchFailureHasNoSuccess(t *testing.T) {
	var help, helpErr bytes.Buffer
	if code := run([]string{"--help"}, &help, &helpErr); code != 0 || help.Len() == 0 {
		t.Fatal(code, helpErr.String())
	}
	if code := run([]string{"--help"}, shortWriter{}, &helpErr); code != 1 {
		t.Fatal(code)
	}
	parent := t.TempDir()
	var out, stderr bytes.Buffer
	file := filepath.Join(parent, "file")
	if err := os.WriteFile(file, []byte("unchanged"), 0600); err != nil {
		t.Fatal(err)
	}
	args := []string{"--work-dir", file, "--keys", "1", "--open-samples", "1", "--update-samples", "1"}
	if code := run(args, &out, &stderr); code != 1 || out.Len() != 0 {
		t.Fatal(code, out.String(), stderr.String())
	}
	b, err := os.ReadFile(file)
	if err != nil || !reflect.DeepEqual(b, []byte("unchanged")) {
		t.Fatal(err, b)
	}
	args[1] = parent
	if code := run(args, shortWriter{}, &stderr); code != 1 {
		t.Fatal(code)
	}
	entries, err := os.ReadDir(parent)
	if err != nil || len(entries) != 1 {
		t.Fatal(entries, err)
	}
}

// A correct report alone must not allow wrong values to be marked verified.
func TestBenchVerificationRejectsWrongData(t *testing.T) {
	dir := t.TempDir()
	db, err := engine.Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.Close() })
	if _, err = db.Put(key(0), []byte{0x00}); err != nil {
		t.Fatal(err)
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "data.log")
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = sampleOpen(dir, 1, 1); err == nil {
		t.Fatal("accepted wrong value despite matching counts")
	}
	after, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(before, after) {
		t.Fatal("verification modified log", err)
	}
	reopened, err := engine.OpenWithOptions(dir, engine.Options{})
	if err != nil {
		t.Fatal("verification failure retained lock", err)
	}
	if err = reopened.Close(); err != nil {
		t.Fatal(err)
	}
}

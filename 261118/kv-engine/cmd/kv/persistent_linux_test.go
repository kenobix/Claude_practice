//go:build linux

package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/engine"
	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

func TestPersistentCLIProcesses(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "kv")
	if out, err := exec.Command("go", "build", "-o", binary, ".").CombinedOutput(); err != nil {
		t.Fatal(err, string(out))
	}
	call := func(input []byte, wantCode int, args ...string) []byte {
		t.Helper()
		cmd := exec.Command(binary, args...)
		cmd.Stdin = bytes.NewReader(input)
		var stdout, stderr bytes.Buffer
		cmd.Stdout = &stdout
		cmd.Stderr = &stderr
		err := cmd.Run()
		code := 0
		if err != nil {
			var exit *exec.ExitError
			if !errors.As(err, &exit) {
				t.Fatal(err)
			}
			code = exit.ExitCode()
		}
		if code != wantCode {
			t.Fatalf("%v: code=%d want=%d stderr=%s", args, code, wantCode, stderr.String())
		}
		return stdout.Bytes()
	}
	dir := t.TempDir()
	for _, tc := range []struct {
		value []byte
		seq   string
	}{{[]byte{0, 255, 10}, "1"}, {[]byte{}, "2"}} {
		response := call(tc.value, 0, "put", "--db", dir, "--key", "k", "--value-file", "-")
		if string(response) != "{\"sequence\":"+tc.seq+"}\n" {
			t.Fatal(string(response))
		}
		got := call(nil, 0, "get", "--db", dir, "--key", "k")
		if !bytes.Equal(got, tc.value) {
			t.Fatal(got, tc.value)
		}
	}
	// File input and overwritten keys are also observed from a fresh process.
	file := filepath.Join(t.TempDir(), "value.bin")
	if err := os.WriteFile(file, []byte("file value"), 0600); err != nil {
		t.Fatal(err)
	}
	call(nil, 0, "put", "--db", dir, "--key", "k", "--value-file", file)
	if string(call(nil, 0, "get", "--db", dir, "--key", "k")) != "file value" {
		t.Fatal("file input mismatch")
	}
	call(nil, 0, "delete", "--db", dir, "--key", "k")
	call(nil, 3, "get", "--db", dir, "--key", "k")
	call(nil, 0, "delete", "--db", dir, "--key", "missing")
	report := call(nil, 0, "inspect", "--db", dir)
	var decoded map[string]any
	if err := json.Unmarshal(report, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded["latest_sequence"] != float64(5) || decoded["live_keys"] != float64(0) {
		t.Fatal(decoded)
	}
	db, err := engine.Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	call(nil, 4, "put", "--db", dir, "--key", "k", "--value-file", "-")
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	// Inspect must report an incomplete suffix without modifying it.
	original, err := os.ReadFile(filepath.Join(dir, "data.log"))
	if err != nil {
		t.Fatal(err)
	}
	suffix := append(bytes.Clone(original), 1, 2, 3)
	if err := os.WriteFile(filepath.Join(dir, "data.log"), suffix, 0600); err != nil {
		t.Fatal(err)
	}
	call(nil, 5, "inspect", "--db", dir)
	after, err := os.ReadFile(filepath.Join(dir, "data.log"))
	if err != nil || !bytes.Equal(after, suffix) {
		t.Fatal("inspect changed suffix", err)
	}
	// Normal Open diagnoses and removes only this incomplete last record.
	call(nil, 3, "get", "--db", dir, "--key", "k")
	after, err = os.ReadFile(filepath.Join(dir, "data.log"))
	if err != nil || !bytes.Equal(after, original) {
		t.Fatal("recovery boundary", err)
	}
	after[len(after)-1] ^= 1
	if err := os.WriteFile(filepath.Join(dir, "data.log"), after, 0600); err != nil {
		t.Fatal(err)
	}
	call(nil, 5, "get", "--db", dir, "--key", "k")
}

func TestPersistentCLIInvalidInput(t *testing.T) {
	for _, tc := range []struct {
		name  string
		args  []string
		input string
		code  int
	}{
		{"no-value", []string{"put", "--key", "k"}, "", 2},
		{"empty-key", []string{"put", "--key", "", "--value-file", "-"}, "", 2},
		{"large-value", []string{"put", "--key", "k", "--value-file", "-"}, strings.Repeat("x", record.MaxValueSize+1), 2},
		{"extra-arg", []string{"get", "--key", "k", "extra"}, "", 2},
		{"unknown-flag", []string{"get", "--key", "k", "--bad"}, "", 2},
		{"no-log-get", []string{"get", "--key", "k"}, "", 6},
		{"no-log-inspect", []string{"inspect"}, "", 6},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dir := t.TempDir()
			args := append(tc.args, "--db", dir)
			code, _, stderr := invoke(args, tc.input)
			if code != tc.code {
				t.Fatal(code, stderr)
			}
			if _, err := os.Stat(filepath.Join(dir, "data.log")); !errors.Is(err, os.ErrNotExist) {
				t.Fatal("invalid input created log", err)
			}
		})
	}
}

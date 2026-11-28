package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/local"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func TestCLI(t *testing.T) {
	volume := t.TempDir()
	for _, name := range []string{"A", "B"} {
		if err := os.Mkdir(filepath.Join(volume, name), 0700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(volume, "A", "notes.txt"), []byte("abc"), 0600); err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	invoke := func(command string, args ...string) error {
		output.Reset()
		return run(t.Context(), append([]string{command, "-volume", volume}, args...), &output)
	}
	require := func(command string, args ...string) {
		if err := invoke(command, args...); err != nil {
			t.Fatal(err)
		}
	}
	require("init", "-root", "B")
	args := []string{"-source", "A", "-destination", "B", "-revision", "1"}
	if err := invoke("copy", append(args, "-path", "")...); err == nil {
		t.Fatal("accepted an explicit empty path")
	}
	require("copy", args...)
	require("recover", "-source", "A", "-destination", "B")
	require("scan", "-root", "B")
	var entries []safefs.Entry
	want := safefs.Digest{Hash: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", Size: 3}
	if err := json.Unmarshal(output.Bytes(), &entries); err != nil || len(entries) != 1 || entries[0].Path != "notes.txt" || entries[0].Digest != want {
		t.Fatalf("scan: %v, %v", entries, err)
	}
	require("status", "-root", "B")
	var status local.Status
	if err := json.Unmarshal(output.Bytes(), &status); err != nil || status.Bases["notes.txt"] != (local.Base{Revision: 1, Digest: want}) {
		t.Fatalf("status: %+v, %v", status, err)
	}
}

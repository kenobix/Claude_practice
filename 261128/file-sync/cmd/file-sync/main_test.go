package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/hub"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/local"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
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

func TestHubOfflineCLI(t *testing.T) {
	volume := t.TempDir()
	if err := os.Mkdir(filepath.Join(volume, "hub"), 0700); err != nil {
		t.Fatal(err)
	}
	// Disposable tokens do not leave the harness environment.
	t.Setenv("FILE_SYNC_TOKEN_A", base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{1}, 32)))
	t.Setenv("FILE_SYNC_TOKEN_B", base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{2}, 32)))
	invoke := func(args ...string) []byte {
		t.Helper()
		var out bytes.Buffer
		if err := run(t.Context(), append(args, "-volume", volume), &out); err != nil {
			t.Fatal(err)
		}
		return out.Bytes()
	}
	invoke("hub-init", "-root", "hub")
	var b struct{ Spaces []hub.RestoreInfo }
	if err := json.Unmarshal(invoke("hub-backup", "-source", "hub", "-destination", "backup"), &b); err != nil || len(b.Spaces) != 1 {
		t.Fatalf("backup CLI %+v %v", b, err)
	}
	var r hub.RestoreInfo
	if err := json.Unmarshal(invoke("hub-restore", "-source", "backup", "-destination", "restored"), &r); err != nil {
		t.Fatal(err)
	}
	if !protocol.IsHex(r.Generation, 32) || r.Space != b.Spaces[0].Space || r.Generation == b.Spaces[0].Generation {
		t.Fatalf("restore CLI %+v (previous %+v)", r, b)
	}
	h, err := hub.Open(t.Context(), volume, "restored")
	if err != nil {
		t.Fatal(err)
	}
	if err := h.Close(); err != nil {
		t.Fatal(err)
	}
}

package main

import (
	"bufio"
	"bytes"
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/local"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func TestIndependentHubAndClientProcesses(t *testing.T) {
	volume := t.TempDir()
	ctx, cancel := context.WithTimeout(t.Context(), 90*time.Second)
	defer cancel()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	bin := filepath.Join(volume, "file-sync")
	goBin := filepath.Join(os.Getenv("GOROOT"), "bin", "go")
	if os.Getenv("GOROOT") == "" {
		goBin = "go"
	}
	build := exec.CommandContext(ctx, goBin, "build", "-mod=readonly", "-o", bin, ".")
	if data, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build CLI: %v %s", err, data)
	}
	for _, name := range []string{"A", "B", "hub"} {
		check(os.Mkdir(filepath.Join(volume, name), 0700))
	}
	artificial := func(n byte) string { return base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{n}, 32)) }
	tokens := []string{artificial(1), artificial(2)}
	call := func(cmd, root string, env []string, args ...string) []byte {
		t.Helper()
		command := exec.CommandContext(ctx, bin, append([]string{cmd, "-volume", volume, "-root", root}, args...)...)
		command.Env = append(os.Environ(), env...)
		out, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("%s %s: %v %s", cmd, root, err, out)
		}
		if bytes.Contains(out, []byte(tokens[0])) || bytes.Contains(out, []byte(tokens[1])) {
			t.Fatalf("%s leaked a token", cmd)
		}
		return out
	}
	call("hub-init", "hub", []string{"FILE_SYNC_TOKEN_A=" + tokens[0], "FILE_SYNC_TOKEN_B=" + tokens[1]})
	call("init", "A", nil)
	call("init", "B", nil)
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	check(err)
	address := listener.Addr().String()
	check(listener.Close())

	process := exec.CommandContext(ctx, bin, "hub", "-volume", volume, "-root", "hub", "-listen", address)
	pipe, err := process.StdoutPipe()
	check(err)
	var diagnostics bytes.Buffer
	process.Stderr = &diagnostics
	check(process.Start())
	stopped := false
	defer func() {
		if !stopped {
			_ = process.Process.Kill()
			_ = process.Wait()
		}
	}()
	line := make(chan string, 1)
	go func() {
		s := bufio.NewScanner(pipe)
		if s.Scan() {
			line <- s.Text()
		}
		close(line)
	}()
	select {
	case got := <-line:
		if got != "listening "+address {
			t.Fatalf("hub readiness %q", got)
		}
	case <-ctx.Done():
		t.Fatal("hub did not announce readiness")
	}

	const path = "notes.txt"
	const pHash = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
	const qHash = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
	env := func(client int) []string { return []string{"FILE_SYNC_TOKEN=" + tokens[client]} }
	files := func(content, hash, generation string, revision int64) {
		t.Helper()
		for _, root := range []string{"A", "B"} {
			data, err := os.ReadFile(filepath.Join(volume, root, path))
			check(err)
			var status local.Status
			check(json.Unmarshal(call("status", root, nil), &status))
			if string(data) != content || status.Bases[path] != (local.Base{Revision: revision, Digest: safefs.Digest{Hash: hash, Size: int64(len(content))}}) ||
				status.Registration == nil || !status.Registration.Complete || status.Registration.Generation != generation ||
				len(status.Outbox) != 0 || len(status.Holds) != 0 {
				t.Fatalf("%s body %q, status %+v", root, data, status)
			}
		}
	}
	remote, err := protocol.NewClient(address, tokens[0])
	check(err)
	m, err := remote.Manifest(ctx)
	check(err)
	g := m.Generation
	check(os.WriteFile(filepath.Join(volume, "A", path), []byte("abc"), 0600))
	call("register", "A", env(0), "--seed", "-hub", address)
	call("register", "B", env(1), "--pull", "-hub", address)
	files("abc", pHash, g, 1)
	check(os.WriteFile(filepath.Join(volume, "B", path), []byte("hello"), 0600))
	call("sync-once", "B", env(1), "-hub", address)
	call("sync-once", "A", env(0), "-hub", address)
	files("hello", qHash, g, 2)
	m, err = remote.Manifest(ctx)
	check(err)
	if len(m.Entries) != 1 || m.Entries[0].Path != path || m.Entries[0].Revision != 2 || m.Entries[0].Hash == nil ||
		*m.Entries[0].Hash != qHash || m.Entries[0].Deleted || m.Entries[0].Client != "client-b" || m.Generation != g {
		t.Fatalf("manifest %+v", m)
	}
	for _, entry := range []struct{ hash, body string }{{pHash, "abc"}, {qHash, "hello"}} {
		got, err := remote.Get(ctx, g, safefs.Digest{Hash: entry.hash, Size: int64(len(entry.body))})
		check(err)
		if string(got) != entry.body {
			t.Fatalf("blob %s: %q", entry.hash, got)
		}
	}
	check(process.Process.Signal(syscall.SIGTERM))
	err = process.Wait()
	stopped = true
	if err != nil || strings.Contains(diagnostics.String(), tokens[0]) || strings.Contains(diagnostics.String(), tokens[1]) {
		t.Fatalf("hub shutdown failed or leaked a token: %v", err)
	}
	db, err := sql.Open("sqlite3", "file:"+filepath.Join(volume, "hub", safefs.Management, "state", safefs.StateDB)+"?mode=ro")
	check(err)
	defer db.Close()
	rows, err := db.QueryContext(ctx, "SELECT generation, client, response FROM receipts ORDER BY rowid")
	check(err)
	defer rows.Close()
	var count int
	for rows.Next() {
		var gotG, client string
		var data []byte
		var receipt protocol.Receipt
		check(rows.Scan(&gotG, &client, &data))
		check(json.Unmarshal(data, &receipt))
		count++
		if count > 2 || gotG != g || client != []string{"client-a", "client-b"}[count-1] || receipt.Entry.Revision != int64(count) || receipt.Result != "applied" {
			t.Fatalf("unexpected receipt %d: %s %+v", count, client, receipt)
		}
	}
	check(rows.Err())
	if count != 2 {
		t.Fatalf("receipt count %d", count)
	}
	t.Logf("distinct hub/client processes: %s/1 to %s/2; generation %s; two applied receipts", pHash, qHash, g)
}

package main

import (
	"bufio"
	"bytes"
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"errors"
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
	invoke := func(exitCode int, cmd, root string, env []string, args ...string) []byte {
		t.Helper()
		command := exec.CommandContext(ctx, bin, append([]string{cmd, "-volume", volume, "-root", root}, args...)...)
		command.Env = append(os.Environ(), env...)
		out, err := command.CombinedOutput()
		actual := 0
		if err != nil {
			var exit *exec.ExitError
			if !errors.As(err, &exit) {
				t.Fatalf("%s %s: %v", cmd, root, err)
			}
			actual = exit.ExitCode()
		}
		if actual != exitCode {
			t.Fatalf("%s %s: exit %d instead of %d, %s", cmd, root, actual, exitCode, out)
		}
		if bytes.Contains(out, []byte(tokens[0])) || bytes.Contains(out, []byte(tokens[1])) {
			t.Fatalf("%s leaked a token", cmd)
		}
		return out
	}
	call := func(cmd, root string, env []string, args ...string) []byte {
		t.Helper()
		return invoke(0, cmd, root, env, args...)
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
	const sHash = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
	const rHash = "486ea46224d1bb4fb680f34f7c9ad96a8f24ec88be73ea8e5a6c65260e9cb8a7"
	check(os.WriteFile(filepath.Join(volume, "A", path), []byte("hello\n"), 0600))
	check(os.WriteFile(filepath.Join(volume, "B", path), []byte("world"), 0600))
	call("sync-once", "A", env(0), "-hub", address)
	invoke(3, "sync-once", "B", env(1), "-hub", address)
	var conflicts []local.Conflict
	check(json.Unmarshal(call("conflicts", "B", env(1), "-hub", address), &conflicts))
	if len(conflicts) != 1 || !conflicts[0].Saved || conflicts[0].Base.Revision != 2 ||
		conflicts[0].Local.Hash != rHash || conflicts[0].Hub[0].Hash != sHash || conflicts[0].Hub[0].Revision != 3 {
		t.Fatalf("CLI conflict %+v", conflicts)
	}
	stage := filepath.Join(volume, "B", safefs.Management, "stage")
	for _, c := range []local.ConflictCandidate{conflicts[0].Local, conflicts[0].Hub[0]} {
		data, err := os.ReadFile(filepath.Join(stage, c.Copy))
		check(err)
		if protocol.Digest(data) != c.Digest {
			t.Fatalf("CLI conflict candidate differs: %+v", c)
		}
	}
	call("resolve", "B", env(1), "-hub", address, "-path", path, "--generation", g, "--revision", "3", "--choose", "local")
	call("sync-once", "A", env(0), "-hub", address)
	files("world", rHash, g, 4)
	check(os.Remove(filepath.Join(volume, "A", path)))
	call("sync-once", "A", env(0), "-hub", address)
	call("sync-once", "B", env(1), "-hub", address)
	for _, root := range []string{"A", "B"} {
		var s local.Status
		check(json.Unmarshal(call("status", root, nil), &s))
		if _, err := os.Stat(filepath.Join(volume, root, path)); !errors.Is(err, os.ErrNotExist) ||
			s.Bases[path] != (local.Base{Revision: 5}) || len(s.Holds) != 0 {
			t.Fatalf("CLI tombstone %s status %+v, lookup %v", root, s, err)
		}
		if root == "B" {
			plan := s.Plans[len(s.Plans)-1]
			for _, p := range s.Plans {
				if p.Target.Revision == 5 {
					plan = p
				}
			}
			data, err := os.ReadFile(filepath.Join(stage, plan.Backup()))
			check(err)
			if plan.Target.Revision != 5 || !plan.BackedUp || plan.Status != "complete" ||
				protocol.Digest(data) != (safefs.Digest{Hash: rHash, Size: 5}) {
				t.Fatalf("CLI trash invalid %+v", plan)
			}
		}
	}
	m, err = remote.Manifest(ctx)
	check(err)
	if len(m.Entries) != 1 || !m.Entries[0].Deleted || m.Entries[0].Hash != nil || m.Entries[0].Revision != 5 || m.Generation != g {
		t.Fatalf("CLI manifest after deletion %+v", m)
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
		if count > 5 || gotG != g || client != []string{"client-a", "client-b", "client-a", "client-b", "client-a"}[count-1] || receipt.Entry.Revision != int64(count) || receipt.Result != "applied" {
			t.Fatalf("unexpected receipt %d: %s %+v", count, client, receipt)
		}
	}
	check(rows.Err())
	if count != 5 {
		t.Fatalf("receipt count %d", count)
	}
	t.Logf("distinct hub/client processes: %s/1 -> %s/2 -> %s/3; preserved competing %s, resolved /4, deleted /5; generation %s; five receipts", pHash, qHash, sHash, rHash, g)
}

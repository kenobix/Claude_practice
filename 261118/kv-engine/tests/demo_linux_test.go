//go:build linux

package tests

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestDemoProductProcesses(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Fatal("demo requires Python 3:", err)
	}
	tmp := t.TempDir()
	kv := filepath.Join(tmp, "kv")
	build := exec.Command("go", "build", "-o", kv, "../cmd/kv")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build: %v\n%s", err, output)
	}
	parent := filepath.Join(tmp, "demo-parent")
	if err := os.Mkdir(parent, 0700); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, python, "../scripts/demo.py", "--kv", kv)
	cmd.Env = append(os.Environ(), "TMPDIR="+parent, "PYTHONDONTWRITEBYTECODE=1")
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("demo: %v\n%s", err, output)
	}
	passed := map[string]bool{}
	stops := map[string]bool{}
	complete := false
	for _, line := range bytes.Split(bytes.TrimSpace(output), []byte{'\n'}) {
		var event struct {
			Event     string
			Scenario  string
			Test      string
			Scenarios int
			Cleanup   bool
			PowerLoss bool `json:"power_loss_verified"`
		}
		if err := json.Unmarshal(line, &event); err != nil {
			t.Fatalf("bad demo output: %s", line)
		}
		switch event.Event {
		case "scenario_pass":
			passed[event.Scenario] = true
		case "sigkill":
			stops[event.Test] = true
		case "demo_pass":
			complete = event.Scenarios == 3 && event.Cleanup && !event.PowerLoss
		}
	}
	if !complete || len(passed) != 3 || !passed["normal"] || !passed["tail"] || !passed["corrupt"] || !stops["TestCrashRecoveryCheckpoints/put/after_ack"] || !stops["TestCrashRecoveryCheckpoints/put/partial_body"] {
		t.Fatalf("incomplete demo: %s", output)
	}
	entries, err := os.ReadDir(parent)
	if err != nil || len(entries) != 0 {
		t.Fatal("demo left files", entries, err)
	}
}

func TestDemoRejectsMissingBinary(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Fatal(err)
	}
	parent := t.TempDir()
	sentinel := filepath.Join(parent, "keep")
	if err := os.WriteFile(sentinel, []byte("existing"), 0600); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(python, "../scripts/demo.py", "--kv", filepath.Join(parent, "missing"))
	cmd.Env = append(os.Environ(), "TMPDIR="+parent, "PYTHONDONTWRITEBYTECODE=1")
	var out, stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr
	err = cmd.Run()
	exit, ok := err.(*exec.ExitError)
	if !ok || exit.ExitCode() != 1 || out.Len() != 0 || !bytes.Contains(stderr.Bytes(), []byte("demo_failed")) {
		t.Fatal(err, out.String(), stderr.String())
	}
	entries, err := os.ReadDir(parent)
	if err != nil || len(entries) != 1 || entries[0].Name() != "keep" {
		t.Fatal(entries, err)
	}
	data, err := os.ReadFile(sentinel)
	if err != nil || string(data) != "existing" {
		t.Fatal(string(data), err)
	}
}

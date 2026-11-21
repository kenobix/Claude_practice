//go:build linux

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/engine"
)

// Only the test executable reads these controls; the product CLI has no crash flags.
func TestCLIResponseWorker(t *testing.T) {
	if os.Getenv("KV_CLI_RESPONSE_WORKER") != "1" {
		return
	}
	point, op, dir := os.Getenv("KV_CLI_POINT"), os.Getenv("KV_CLI_OP"), os.Getenv("KV_CLI_DIR")
	switch point {
	case "", "before_close", "after_close", "before_response", "partial_response", "after_response":
	default:
		os.Exit(90)
	}
	event := os.NewFile(3, "response-events")
	pause := func(at string) {
		if point != at {
			return
		}
		if err := json.NewEncoder(event).Encode(at); err != nil {
			os.Exit(91)
		}
		var b [1]byte
		_, _ = os.Stdin.Read(b[:])
		os.Exit(92) // A stopped worker may only be killed by the parent.
	}
	open := func(dir string, options engine.Options) (persistentDB, error) {
		db, err := engine.OpenWithOptions(dir, options)
		if err != nil {
			return nil, err
		}
		return &responseCloseDB{db, func() error {
			pause("before_close")
			err := db.Close()
			if err == nil {
				pause("after_close")
			}
			return err
		}}, nil
	}
	out := &crashResponseWriter{pause: pause, point: point}
	code := runPersistentWithOpen(responseArgs(op, dir), bytes.NewReader(responseValue), out, os.Stderr, open)
	os.Exit(code)
}

type crashResponseWriter struct {
	pause func(string)
	point string
}

func (w *crashResponseWriter) Write(p []byte) (int, error) {
	w.pause("before_response")
	if w.point == "partial_response" {
		n, err := os.Stdout.Write(p[:5])
		if err != nil {
			return n, err
		}
		w.pause("partial_response")
	}
	n, err := os.Stdout.Write(p)
	if err == nil && n == len(p) {
		w.pause("after_response")
	}
	return n, err
}

func responseCommand(t *testing.T, dir, op, point string) (*exec.Cmd, context.CancelFunc) {
	t.Helper()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	cmd := exec.CommandContext(ctx, exe, "-test.run=^TestCLIResponseWorker$")
	cmd.Env = append(os.Environ(), "KV_CLI_RESPONSE_WORKER=1", "KV_CLI_DIR="+dir, "KV_CLI_OP="+op, "KV_CLI_POINT="+point)
	return cmd, cancel
}

func TestCLIResponseCrashBoundaries(t *testing.T) {
	for _, op := range []string{"put", "delete"} {
		for _, point := range []string{"before_close", "after_close", "before_response", "partial_response", "after_response"} {
			t.Run(op+"/"+point, func(t *testing.T) {
				dir := responseFixture(t)
				logPath := filepath.Join(dir, "data.log")
				prefix, err := os.ReadFile(logPath)
				if err != nil {
					t.Fatal(err)
				}
				cmd, cancel := responseCommand(t, dir, op, point)
				defer cancel()
				eventsR, eventsW, err := os.Pipe()
				if err != nil {
					t.Fatal(err)
				}
				defer eventsR.Close()
				defer eventsW.Close()
				cmd.ExtraFiles = []*os.File{eventsW}
				stdin, err := cmd.StdinPipe()
				if err != nil {
					t.Fatal(err)
				}
				defer stdin.Close()
				stdoutR, stdoutW, err := os.Pipe()
				if err != nil {
					t.Fatal(err)
				}
				defer stdoutR.Close()
				defer stdoutW.Close()
				if err := stdoutR.SetReadDeadline(time.Now().Add(10 * time.Second)); err != nil {
					t.Fatal(err)
				}
				var stderr bytes.Buffer
				cmd.Stdout, cmd.Stderr = stdoutW, &stderr
				if err := cmd.Start(); err != nil {
					t.Fatal(err)
				}
				_ = eventsW.Close()
				_ = stdoutW.Close()
				waited := false
				defer func() {
					if !waited {
						_ = cmd.Process.Kill()
						_ = cmd.Wait()
					}
				}()
				notified := make(chan error, 1)
				go func() {
					var got string
					err := json.NewDecoder(eventsR).Decode(&got)
					if err == nil && got != point {
						err = fmt.Errorf("point=%q want=%q", got, point)
					}
					notified <- err
				}()
				select {
				case err := <-notified:
					if err != nil {
						t.Fatal(err)
					}
				case <-time.After(10 * time.Second):
					t.Fatal("worker checkpoint timeout")
				}
				report, err := engine.Inspect(dir)
				if point == "before_close" {
					if !errors.Is(err, engine.ErrLocked) {
						t.Fatalf("lock lost before Close: %v", err)
					}
				} else if err != nil || report.LatestSequence != 4 {
					t.Fatalf("unlocked report=%+v err=%v", report, err)
				}
				// Observe the complete/partial response in the parent before the kill.
				want := ""
				if point == "partial_response" {
					want = `{"seq`
				}
				if point == "after_response" {
					want = "{\"sequence\":4}\n"
				}
				observed := make([]byte, len(want))
				if _, err := io.ReadFull(stdoutR, observed); err != nil {
					t.Fatal(err)
				}
				if string(observed) != want {
					t.Fatalf("observed=%q want=%q", observed, want)
				}
				if err := cmd.Process.Kill(); err != nil {
					t.Fatal(err)
				}
				err = cmd.Wait()
				waited = true
				var exit *exec.ExitError
				if !errors.As(err, &exit) {
					t.Fatalf("expected SIGKILL: %v stderr=%s", err, stderr.String())
				}
				status, ok := exit.Sys().(syscall.WaitStatus)
				if !ok || !status.Signaled() || status.Signal() != syscall.SIGKILL {
					t.Fatalf("unexpected status: %v", exit.Sys())
				}
				remaining, err := io.ReadAll(stdoutR)
				if err != nil || len(remaining) != 0 || stderr.Len() != 0 {
					t.Fatalf("unexpected remaining stdout=%q stderr=%s err=%v", remaining, stderr.String(), err)
				}

				after, err := os.ReadFile(logPath)
				if err != nil || !bytes.HasPrefix(after, prefix) {
					t.Fatalf("successful prefix changed: %v", err)
				}
				// In all these checkpoints Sync has already completed. No partial value is allowed.
				checkResponseDB(t, dir, op)
				checkResponseDB(t, dir, op) // Reopening again must be idempotent.
				reopened, err := os.ReadFile(logPath)
				if err != nil || !bytes.Equal(after, reopened) {
					t.Fatalf("reopen changed log: %v", err)
				}
				t.Logf("checkpoint=%s acknowledged_prefix=3 output_bytes=%d recovered_sequence=4 log_bytes=%d", point, len(observed), len(after))
			})
		}
	}
}

func TestCLIResponseWorkerControls(t *testing.T) {
	for _, op := range []string{"put", "delete"} {
		t.Run(op, func(t *testing.T) {
			dir := responseFixture(t)
			cmd, cancel := responseCommand(t, dir, op, "")
			defer cancel()
			out, err := cmd.CombinedOutput()
			if err != nil || string(out) != "{\"sequence\":4}\n" {
				t.Fatalf("normal: %v %q", err, out)
			}
			checkResponseDB(t, dir, op)
		})
	}
	t.Run("unknown-checkpoint", func(t *testing.T) {
		dir := t.TempDir()
		cmd, cancel := responseCommand(t, dir, "put", "invalid")
		defer cancel()
		out, err := cmd.CombinedOutput()
		var exit *exec.ExitError
		if !errors.As(err, &exit) || exit.ExitCode() != 90 || len(out) != 0 {
			t.Fatalf("invalid: %v %q", err, out)
		}
		entries, err := os.ReadDir(dir)
		if err != nil || len(entries) != 0 {
			t.Fatalf("invalid checkpoint changed directory: %v %v", entries, err)
		}
	})
}

// An actual product binary must report an output-device error without rolling back.
func TestPersistentCLIOutputDeviceFailure(t *testing.T) {
	binary := t.TempDir() + "/kv"
	if out, err := exec.Command("go", "build", "-o", binary, ".").CombinedOutput(); err != nil {
		t.Fatal(err, string(out))
	}
	for _, op := range []string{"put", "delete"} {
		t.Run(op, func(t *testing.T) {
			dir := responseFixture(t)
			full, err := os.OpenFile("/dev/full", os.O_WRONLY, 0)
			if err != nil {
				t.Fatal(err)
			}
			defer full.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, binary, responseArgs(op, dir)...)
			cmd.Stdin = bytes.NewReader(responseValue)
			cmd.Stdout = full
			var stderr bytes.Buffer
			cmd.Stderr = &stderr
			err = cmd.Run()
			var exit *exec.ExitError
			if !errors.As(err, &exit) || exit.ExitCode() != 6 || !strings.Contains(stderr.String(), `"error":"io"`) {
				t.Fatalf("error=%v stderr=%s", err, stderr.String())
			}
			checkResponseDB(t, dir, op)
		})
	}
}

var _ io.Writer = (*crashResponseWriter)(nil)

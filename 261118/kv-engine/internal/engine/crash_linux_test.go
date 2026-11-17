//go:build linux

package engine

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"reflect"
	"syscall"
	"testing"
	"time"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

// Fault controls exist only in this test binary, never in the shipped CLI.
type crashRequest struct {
	Op    string
	Key   string
	Value []byte
	Stop  string
}
type crashEvent struct {
	Event    string
	Point    string
	Sequence uint64
	Error    string
}

// crashLog wraps the actual file used by DB. The parent owns all acknowledged history.
type crashLog struct {
	*os.File
	point  string
	events *json.Encoder
}

func (f *crashLog) pause(point string) {
	if err := f.events.Encode(crashEvent{Event: "stopped", Point: point}); err != nil {
		panic(err)
	}
	// Explicit rendezvous: the parent sends no more input and kills this process.
	// Returning from this read (even EOF) is a protocol failure, never a continuation.
	var control [1]byte
	_, _ = io.ReadFull(os.Stdin, control[:])
	os.Exit(91)
}
func (f *crashLog) WriteAt(p []byte, offset int64) (int, error) {
	if f.point == "before_write" {
		f.pause(f.point)
	}
	if f.point == "partial_header" || f.point == "partial_body" {
		cut := 10
		if f.point == "partial_body" {
			cut = record.HeaderSize + int(binary.LittleEndian.Uint32(p[16:20])) + 1
		}
		if cut >= len(p) {
			return 0, fmt.Errorf("invalid partial-write checkpoint")
		}
		n, err := f.File.WriteAt(p[:cut], offset)
		if err != nil {
			return n, err
		}
		if n != cut {
			return n, io.ErrShortWrite
		}
		f.pause(f.point)
	}
	n, err := f.File.WriteAt(p, offset)
	if err == nil && n == len(p) && f.point == "after_write" {
		f.pause(f.point)
	}
	return n, err
}
func (f *crashLog) Sync() error {
	if f.point == "before_sync" {
		f.pause(f.point)
	}
	if err := f.File.Sync(); err != nil {
		return err
	}
	if f.point == "after_sync" {
		f.pause(f.point)
	}
	return nil
}

func TestCrashWorker(t *testing.T) {
	if os.Getenv("KV_CRASH_WORKER") != "1" {
		return
	}
	// This helper runs only as a child; normal tests never open an environment-supplied DB.
	if err := runCrashWorker(os.Getenv("KV_CRASH_DIR")); err != nil {
		json.NewEncoder(os.Stdout).Encode(crashEvent{Event: "error", Error: err.Error()})
		os.Exit(90)
	}
	os.Exit(0)
}
func runCrashWorker(dir string) error {
	encoder := json.NewEncoder(os.Stdout)
	var file *crashLog
	ops := defaultOps()
	ops.wrap = func(raw *os.File) logFile { file = &crashLog{File: raw, events: encoder}; return file }
	db, err := openDisk(dir, Options{}, ops)
	if err != nil {
		return err
	}
	defer db.Close()
	if err := encoder.Encode(crashEvent{Event: "ready"}); err != nil {
		return err
	}
	decoder := json.NewDecoder(os.Stdin)
	for {
		var req crashRequest
		if err := decoder.Decode(&req); err != nil {
			if errors.Is(err, io.EOF) {
				return db.Close()
			}
			return err
		}
		switch req.Stop {
		case "", "before_write", "partial_header", "partial_body", "after_write", "before_sync", "after_sync", "before_ack", "after_ack":
		default:
			return fmt.Errorf("unknown checkpoint: %q", req.Stop)
		}
		file.point = req.Stop
		var seq uint64
		switch req.Op {
		case "put":
			seq, err = db.Put(req.Key, req.Value)
		case "delete":
			seq, err = db.Delete(req.Key)
		default:
			return fmt.Errorf("unknown operation: %q", req.Op)
		}
		if err != nil {
			return err
		}
		if req.Stop == "before_ack" {
			file.pause(req.Stop)
		}
		if err := encoder.Encode(crashEvent{Event: "ack", Sequence: seq}); err != nil {
			return err
		}
		if req.Stop == "after_ack" {
			file.pause(req.Stop)
		}
	}
}

type crashChild struct {
	cmd      *exec.Cmd
	input    io.WriteCloser
	requests *json.Encoder
	events   *json.Decoder
	stderr   bytes.Buffer
	waited   bool
}

func startCrashChild(t *testing.T, dir string) *crashChild {
	t.Helper()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	child := &crashChild{cmd: exec.CommandContext(ctx, exe, "-test.run=^TestCrashWorker$")}
	child.cmd.Env = append(os.Environ(), "KV_CRASH_WORKER=1", "KV_CRASH_DIR="+dir)
	child.input, err = child.cmd.StdinPipe()
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	output, err := child.cmd.StdoutPipe()
	if err != nil {
		child.input.Close()
		cancel()
		t.Fatal(err)
	}
	child.cmd.Stderr = &child.stderr
	if err := child.cmd.Start(); err != nil {
		child.input.Close()
		cancel()
		t.Fatal(err)
	}
	child.requests = json.NewEncoder(child.input)
	child.events = json.NewDecoder(output)
	t.Cleanup(func() {
		if !child.waited {
			child.cmd.Process.Kill()
			child.wait()
		}
		child.input.Close()
		cancel()
	})
	if event := child.read(t); event.Event != "ready" {
		t.Fatalf("worker not ready: %+v", event)
	}
	return child
}
func (c *crashChild) wait() error { err := c.cmd.Wait(); c.waited = true; return err }
func (c *crashChild) read(t *testing.T) crashEvent {
	t.Helper()
	var event crashEvent
	if err := c.events.Decode(&event); err != nil {
		c.cmd.Process.Kill()
		c.wait()
		t.Fatalf("worker protocol: %v; stderr=%s", err, c.stderr.String())
	}
	if event.Event == "error" {
		t.Fatalf("worker: %s", event.Error)
	}
	return event
}
func (c *crashChild) send(t *testing.T, req crashRequest) {
	t.Helper()
	if err := c.requests.Encode(req); err != nil {
		t.Fatal(err)
	}
}
func (c *crashChild) kill(t *testing.T) {
	t.Helper()
	if err := c.cmd.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	err := c.wait()
	var exit *exec.ExitError
	if !errors.As(err, &exit) {
		t.Fatalf("expected SIGKILL: %v", err)
	}
	status, ok := exit.Sys().(syscall.WaitStatus)
	if !ok || !status.Signaled() || status.Signal() != syscall.SIGKILL {
		t.Fatalf("unexpected exit: %v", err)
	}
}

func modelAfter(history []crashRequest) map[string][]byte {
	model := map[string][]byte{}
	for _, req := range history {
		if req.Op == "delete" {
			delete(model, req.Key)
		} else {
			model[req.Key] = append([]byte{}, req.Value...)
		}
	}
	return model
}
func recoveredModel(t *testing.T, db *DB, keys []string) map[string][]byte {
	t.Helper()
	model := map[string][]byte{}
	for _, key := range keys {
		value, err := db.Get(key)
		if errors.Is(err, ErrNotFound) {
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		model[key] = append([]byte{}, value...)
	}
	report, err := db.Inspect()
	if err != nil {
		t.Fatal(err)
	}
	if report.LiveKeys != len(model) {
		t.Fatalf("unexpected extra keys: report=%+v model=%v", report, model)
	}
	return model
}

func TestCrashRecoveryCheckpoints(t *testing.T) {
	for _, op := range []string{"put", "delete"} {
		for _, point := range []string{"before_write", "partial_header", "partial_body", "after_write", "before_sync", "after_sync", "before_ack", "after_ack"} {
			t.Run(op+"/"+point, func(t *testing.T) {
				dir := t.TempDir()
				initial := mustOpen(t, dir)
				if err := initial.Close(); err != nil {
					t.Fatal(err)
				}
				child := startCrashChild(t, dir)
				var acknowledged []crashRequest
				baseline := []crashRequest{
					{Op: "put", Key: "keep", Value: []byte{0, 255, 10}},
					{Op: "put", Key: "target", Value: []byte("old")},
					{Op: "put", Key: "gone", Value: []byte("gone")},
					{Op: "delete", Key: "gone"},
					{Op: "put", Key: "empty", Value: []byte{}},
				}
				for _, req := range baseline {
					child.send(t, req)
					event := child.read(t)
					if event.Event != "ack" || event.Sequence != uint64(len(acknowledged)+1) {
						t.Fatalf("bad ack: %+v", event)
					}
					acknowledged = append(acknowledged, req)
				}
				before, err := Inspect(dir)
				if !errors.Is(err, ErrLocked) {
					t.Fatalf("worker lost lock: %+v %v", before, err)
				}
				pending := crashRequest{Op: op, Key: "target", Value: []byte{0, 1, 255, 2}, Stop: point}
				if op == "delete" {
					pending.Value = nil
				}
				child.send(t, pending)
				if point == "after_ack" {
					event := child.read(t)
					if event.Event != "ack" || event.Sequence != 6 {
						t.Fatalf("bad final ack: %+v", event)
					}
					acknowledged = append(acknowledged, pending)
				}
				event := child.read(t)
				if event.Event != "stopped" || event.Point != point {
					t.Fatalf("wrong checkpoint: %+v", event)
				}
				child.kill(t)
				scan, scanErr := Inspect(dir)
				if point == "partial_header" || point == "partial_body" {
					if !errors.Is(scanErr, record.ErrIncomplete) || scan.LatestSequence != 5 {
						t.Fatalf("partial scan: %+v %v", scan, scanErr)
					}
				} else if scanErr != nil {
					t.Fatal(scanErr)
				}
				var diagnostic bytes.Buffer
				db, err := OpenWithOptions(dir, Options{Diagnostics: &diagnostic})
				if err != nil {
					t.Fatal(err)
				}
				defer db.Close()
				got := recoveredModel(t, db, []string{"keep", "target", "gone", "empty"})
				report, err := db.Inspect()
				if err != nil {
					t.Fatal(err)
				}
				committed := modelAfter(acknowledged)
				withPending := modelAfter(append(append([]crashRequest{}, acknowledged...), pending))
				matchesCommitted := reflect.DeepEqual(got, committed) && report.LatestSequence == uint64(len(acknowledged))
				matchesPending := point != "after_ack" && reflect.DeepEqual(got, withPending) && report.LatestSequence == uint64(len(acknowledged)+1)
				if !matchesCommitted && !matchesPending {
					t.Fatalf("recovery lost ack or exposed partial value: point=%s got=%v seq=%d history=%v", point, got, report.LatestSequence, acknowledged)
				}
				switch point {
				case "before_write", "partial_header", "partial_body":
					if !matchesCommitted {
						t.Fatal("incomplete operation became visible")
					}
				case "after_sync", "before_ack":
					if !matchesPending {
						t.Fatal("complete synced operation missing after process kill")
					}
				case "after_ack":
					if !matchesCommitted {
						t.Fatal("acknowledged operation missing")
					}
				}
				if point == "partial_header" || point == "partial_body" {
					if diagnostic.Len() == 0 || report.LogBytes != scan.ValidBytes {
						t.Fatal("tail repair not diagnosed or wrong boundary")
					}
				} else if diagnostic.Len() != 0 {
					t.Fatalf("unexpected repair: %s", diagnostic.String())
				}
				// Reacquired flock and the next sequence must also work after the kill.
				put(t, db, "next", []byte("ok"), report.LatestSequence+1)
				t.Logf("op=%s point=%s acknowledged=%d recovered_sequence=%d old_bytes=%d valid_bytes=%d repaired=%t", op, point, len(acknowledged), report.LatestSequence, scan.LogBytes, scan.ValidBytes, diagnostic.Len() != 0)
			})
		}
	}
}

func TestCrashHarnessNormalExit(t *testing.T) {
	dir := t.TempDir()
	db := mustOpen(t, dir)
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	child := startCrashChild(t, dir)
	req := crashRequest{Op: "put", Key: "normal", Value: []byte{0, 255}}
	child.send(t, req)
	if event := child.read(t); event.Event != "ack" || event.Sequence != 1 {
		t.Fatalf("bad ack: %+v", event)
	}
	if err := child.input.Close(); err != nil {
		t.Fatal(err)
	}
	if err := child.wait(); err != nil {
		t.Fatalf("normal exit: %v %s", err, child.stderr.String())
	}
	reopened := mustOpen(t, dir)
	value(t, reopened, "normal", req.Value)
}

func TestCrashHarnessRejectsInvalidRequest(t *testing.T) {
	dir := t.TempDir()
	db := mustOpen(t, dir)
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	before := logBytes(t, dir)
	child := startCrashChild(t, dir)
	child.send(t, crashRequest{Op: "put", Key: "k", Stop: "unknown"})
	var event crashEvent
	if err := child.events.Decode(&event); err != nil {
		t.Fatal(err)
	}
	if event.Event != "error" {
		t.Fatalf("invalid request accepted: %+v", event)
	}
	if err := child.wait(); err == nil {
		t.Fatal("worker protocol failure reported success")
	}
	if !bytes.Equal(before, logBytes(t, dir)) {
		t.Fatal("invalid request changed log")
	}
	reopened := mustOpen(t, dir)
	if report, err := reopened.Inspect(); err != nil || report.RecordCount != 0 {
		t.Fatal(report, err)
	}
}

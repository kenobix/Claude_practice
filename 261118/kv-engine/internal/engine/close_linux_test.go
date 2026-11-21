//go:build linux

package engine

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"reflect"
	"sort"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/recovery"
)

// A rendezvous inside actual file I/O fixes which operation owns DB.mu.
// Counters remain race-safe even if the implementation under test regresses.
type closeGateFile struct {
	*os.File
	point                 string
	fail                  bool
	entered               chan struct{}
	release               chan struct{}
	once                  sync.Once
	reads, writes, closes atomic.Int32
}

func (f *closeGateFile) stop(point string) bool {
	if f.point != point {
		return false
	}
	f.once.Do(func() { close(f.entered); <-f.release })
	return f.fail
}
func (f *closeGateFile) ReadAt(p []byte, offset int64) (int, error) {
	f.reads.Add(1)
	if f.stop("read") {
		return 0, syscall.EIO
	}
	return f.File.ReadAt(p, offset)
}
func (f *closeGateFile) WriteAt(p []byte, offset int64) (int, error) {
	f.writes.Add(1)
	return f.File.WriteAt(p, offset)
}
func (f *closeGateFile) Sync() error {
	if f.stop("sync") {
		return syscall.EIO
	}
	return f.File.Sync()
}
func (f *closeGateFile) Close() error {
	f.closes.Add(1)
	failed := f.stop("close")
	err := f.File.Close()
	if failed {
		return errors.Join(err, syscall.EIO)
	}
	return err
}
func armCloseGate(t *testing.T, f *closeGateFile, point string, fail bool) func() {
	t.Helper()
	f.point = point
	f.fail = fail
	f.entered = make(chan struct{})
	f.release = make(chan struct{})
	var once sync.Once
	release := func() { once.Do(func() { close(f.release) }) }
	t.Cleanup(release)
	return release
}
func awaitCloseResult[T any](t *testing.T, ch <-chan T) T {
	t.Helper()
	select {
	case result := <-ch:
		return result
	case <-time.After(5 * time.Second):
		t.Fatal("Close concurrency rendezvous timed out")
		var zero T
		return zero
	}
}

type closeAPIResult struct {
	op, key string
	seq     uint64
	value   []byte
	report  recovery.Report
	err     error
}

func callCloseAPI(db *DB, op, key string) closeAPIResult {
	r := closeAPIResult{op: op, key: key}
	switch op {
	case "put":
		r.seq, r.err = db.Put(key, []byte{0, 255, 1})
	case "delete":
		r.seq, r.err = db.Delete(key)
	case "get":
		r.value, r.err = db.Get(key)
	case "inspect":
		r.report, r.err = db.Inspect()
	default:
		panic("invalid test operation")
	}
	return r
}
func closeFixture(t *testing.T) (string, *DB, *closeGateFile) {
	t.Helper()
	dir := t.TempDir()
	var file *closeGateFile
	ops := defaultOps()
	ops.wrap = func(f *os.File) logFile { file = &closeGateFile{File: f}; return file }
	db, err := openDisk(dir, Options{Create: true}, ops)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := db.Close(); err != nil {
			t.Error(err)
		}
	})
	put(t, db, "target", []byte("old"), 1)
	put(t, db, "keep", []byte{0, 255, 1}, 2)
	put(t, db, "empty", nil, 3)
	return dir, db, file
}
func checkCloseResources(t *testing.T, dir string, file *closeGateFile) {
	t.Helper()
	if file.closes.Load() != 1 {
		t.Fatalf("close count=%d", file.closes.Load())
	}
	if _, err := file.File.Stat(); !errors.Is(err, os.ErrClosed) {
		t.Fatalf("log FD not closed: %v", err)
	}
	lock, err := probeLock(dir)
	if err != nil {
		t.Fatalf("lock not released: %v", err)
	}
	if err := lock.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestDiskCloseWaitsForIO(t *testing.T) {
	for _, op := range []string{"put", "delete", "get"} {
		for _, fail := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/eio=%t", op, fail), func(t *testing.T) {
				dir, db, file := closeFixture(t)
				point := "sync"
				if op == "get" {
					point = "read"
				}
				release := armCloseGate(t, file, point, fail)
				result := make(chan closeAPIResult, 1)
				go func() { result <- callCloseAPI(db, op, "target") }()
				awaitCloseResult(t, file.entered)
				// The file gate is reached with the DB mutex held, not based on a sleep.
				if db.mu.TryLock() {
					db.mu.Unlock()
					t.Fatal("operation did not hold mutex during I/O")
				}
				closing := make(chan error, 1)
				started := make(chan struct{})
				go func() { close(started); closing <- db.Close() }()
				awaitCloseResult(t, started)
				if file.closes.Load() != 0 {
					t.Fatal("Close reached file while operation was paused")
				}
				if _, err := Inspect(dir); !errors.Is(err, ErrLocked) {
					t.Fatalf("lock released during I/O: %v", err)
				}
				release()
				got := awaitCloseResult(t, result)
				if fail {
					if !errors.Is(got.err, syscall.EIO) || got.seq != 0 || got.value != nil {
						t.Fatalf("failure response: %+v", got)
					}
				} else if got.err != nil || op != "get" && got.seq != 4 || op == "get" && !bytes.Equal(got.value, []byte("old")) {
					t.Fatalf("operation response: %+v", got)
				}
				if err := awaitCloseResult(t, closing); err != nil {
					t.Fatal(err)
				}
				checkCloseResources(t, dir, file)
				// Full writes remain observable even when the injected Sync returned EIO.
				again := mustOpen(t, dir)
				value(t, again, "keep", []byte{0, 255, 1})
				value(t, again, "empty", nil)
				expectedSeq := uint64(3)
				switch op {
				case "put":
					value(t, again, "target", []byte{0, 255, 1})
					expectedSeq = 4
				case "delete":
					if _, err := again.Get("target"); !errors.Is(err, ErrNotFound) {
						t.Fatal(err)
					}
					expectedSeq = 4
				case "get":
					value(t, again, "target", []byte("old"))
				}
				if report, err := again.Inspect(); err != nil || report.LatestSequence != expectedSeq {
					t.Fatal(report, err)
				}
			})
		}
	}
}

func TestDiskCloseWinsAPI(t *testing.T) {
	for _, op := range []string{"put", "delete", "get", "inspect"} {
		for _, fail := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/close_eio=%t", op, fail), func(t *testing.T) {
				dir, db, file := closeFixture(t)
				before := logBytes(t, dir)
				reads, writes := file.reads.Load(), file.writes.Load()
				release := armCloseGate(t, file, "close", fail)
				closing := make(chan error, 1)
				go func() { closing <- db.Close() }()
				awaitCloseResult(t, file.entered)
				followers := make(chan error, 7)
				for i := 0; i < 7; i++ {
					go func() { followers <- db.Close() }()
				}
				result := make(chan closeAPIResult, 1)
				started := make(chan struct{})
				go func() { close(started); result <- callCloseAPI(db, op, "target") }()
				awaitCloseResult(t, started)
				if _, err := Inspect(dir); !errors.Is(err, ErrLocked) {
					t.Fatalf("lock released before data.log close: %v", err)
				}
				release()
				closeErr := awaitCloseResult(t, closing)
				if fail && !errors.Is(closeErr, syscall.EIO) || !fail && closeErr != nil {
					t.Fatal(closeErr)
				}
				for i := 0; i < 7; i++ {
					if err := awaitCloseResult(t, followers); err != nil {
						t.Fatalf("concurrent repeated Close: %v", err)
					}
				}
				got := awaitCloseResult(t, result)
				if !errors.Is(got.err, ErrClosed) || got.seq != 0 || got.value != nil || !reflect.DeepEqual(got.report, recovery.Report{}) {
					t.Fatalf("post-close response: %+v", got)
				}
				if err := db.Close(); err != nil {
					t.Fatalf("repeated Close: %v", err)
				}
				checkCloseResources(t, dir, file)
				if reads != file.reads.Load() || writes != file.writes.Load() || !bytes.Equal(before, logBytes(t, dir)) {
					t.Fatal("API performed I/O after Close")
				}
				again := mustOpen(t, dir)
				value(t, again, "target", []byte("old"))
				value(t, again, "empty", nil)
				if report, err := again.Inspect(); err != nil || report.LatestSequence != 3 {
					t.Fatal(report, err)
				}
			})
		}
	}
}

func TestDiskConcurrentCloseHistory(t *testing.T) {
	for round := 0; round < 4; round++ {
		t.Run(fmt.Sprint(round), func(t *testing.T) {
			dir, db, file := closeFixture(t)
			history := []crashRequest{{Op: "put", Key: "target", Value: []byte("old")}, {Op: "put", Key: "keep", Value: []byte{0, 255, 1}}, {Op: "put", Key: "empty", Value: []byte{}}}
			keys := []string{"target", "keep", "empty"}
			for i := 0; i < 16; i++ {
				key := fmt.Sprintf("delete-%d", i)
				keys = append(keys, key, fmt.Sprintf("put-%d", i))
				put(t, db, key, []byte("baseline"), uint64(len(history)+1))
				history = append(history, crashRequest{Op: "put", Key: key, Value: []byte("baseline")})
			}
			start := make(chan struct{})
			results := make(chan closeAPIResult, 72)
			for i := 0; i < 16; i++ {
				for _, op := range []string{"put", "delete", "get", "inspect"} {
					key := "keep"
					if op == "put" || op == "delete" {
						key = fmt.Sprintf("%s-%d", op, i)
					}
					go func() { <-start; results <- callCloseAPI(db, op, key) }()
				}
			}
			for i := 0; i < 8; i++ {
				go func() { <-start; results <- closeAPIResult{op: "close", err: db.Close()} }()
			}
			close(start)
			var updates, snapshots []closeAPIResult
			for i := 0; i < 72; i++ {
				got := awaitCloseResult(t, results)
				if got.op == "close" {
					if got.err != nil {
						t.Fatal(got.err)
					}
					continue
				}
				if errors.Is(got.err, ErrClosed) {
					if got.seq != 0 || got.value != nil || !reflect.DeepEqual(got.report, recovery.Report{}) {
						t.Fatal(got)
					}
					continue
				}
				if got.err != nil {
					t.Fatal(got.err)
				}
				switch got.op {
				case "put", "delete":
					updates = append(updates, got)
				case "get":
					if !bytes.Equal(got.value, []byte{0, 255, 1}) {
						t.Fatal(got)
					}
				case "inspect":
					snapshots = append(snapshots, got)
				}
			}
			checkCloseResources(t, dir, file)
			sort.Slice(updates, func(i, j int) bool { return updates[i].seq < updates[j].seq })
			for _, got := range updates {
				if got.seq != uint64(len(history)+1) {
					t.Fatalf("noncontiguous successful sequence: %+v", updates)
				}
				req := crashRequest{Op: got.op, Key: got.key}
				if got.op == "put" {
					req.Value = []byte{0, 255, 1}
				}
				history = append(history, req)
			}
			for _, snapshot := range snapshots {
				r := snapshot.report
				if r.LatestSequence < 19 || r.LatestSequence > uint64(len(history)) {
					t.Fatal(r)
				}
				prefix := history[:r.LatestSequence]
				var puts, deletes uint64
				for _, req := range prefix {
					if req.Op == "put" {
						puts++
					} else {
						deletes++
					}
				}
				if r.Status != "ok" || r.RecordCount != r.LatestSequence || r.PutCount != puts || r.DeleteCount != deletes || r.LiveKeys != len(modelAfter(prefix)) {
					t.Fatalf("inconsistent inspect snapshot: %+v", r)
				}
			}
			again := mustOpen(t, dir)
			if !reflect.DeepEqual(recoveredModel(t, again, keys), modelAfter(history)) {
				t.Fatal("Close race lost successful update or exposed rejected update")
			}
			if r, err := again.Inspect(); err != nil || r.LatestSequence != uint64(len(history)) {
				t.Fatal(r, err)
			}
			t.Logf("successful updates=%d inspect snapshots=%d closes=8", len(updates), len(snapshots))
		})
	}
}

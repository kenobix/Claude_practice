//go:build linux

package engine

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"reflect"
	"strings"
	"testing"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

// The generator and dictionary oracle do not use the record encoder or disk index.
func seededCrashHistory(seed int64) ([]crashRequest, []string) {
	keys := []string{"target", "empty", "gone", "missing", "日本語", strings.Repeat("k", 256), "a", "b"}
	history := []crashRequest{
		{Op: "put", Key: "empty", Value: []byte{}},
		{Op: "put", Key: "gone", Value: []byte{0, 255}},
		{Op: "delete", Key: "gone"},
		{Op: "delete", Key: "missing"},
		{Op: "put", Key: "target", Value: []byte("old")},
		{Op: "put", Key: "target", Value: []byte("overwritten")},
	}
	rng := rand.New(rand.NewSource(seed))
	for i := 0; i < 48; i++ {
		req := crashRequest{Op: "delete", Key: keys[rng.Intn(len(keys))]}
		if rng.Intn(3) != 0 {
			req.Op = "put"
			req.Value = make([]byte, []int{0, 1, 17, 4096}[rng.Intn(4)])
			_, _ = rng.Read(req.Value)
		}
		history = append(history, req)
	}
	// Ensure that either final operation changes observable state, in every seed.
	history = append(history, crashRequest{Op: "put", Key: "target", Value: []byte("before pending")})
	return history, append(keys, "never-written", "continued")
}

func acknowledgeCrashRequest(t *testing.T, child *crashChild, req crashRequest, history *[]crashRequest) {
	t.Helper()
	child.send(t, req)
	event := child.read(t)
	if event.Event != "ack" || event.Sequence != uint64(len(*history)+1) {
		t.Fatalf("bad ack: %+v history=%+v", event, *history)
	}
	// Only receipt of this event adds the request to the parent-owned success history.
	*history = append(*history, req)
}

func TestSeededCrashRecovery(t *testing.T) {
	points := []string{"normal_exit", "before_write", "partial_header", "partial_body", "after_write", "before_sync", "after_sync", "before_ack", "after_ack"}
	for _, seed := range []int64{42, 261118, 20261117} {
		for _, op := range []string{"put", "delete"} {
			for _, point := range points {
				t.Run(fmt.Sprintf("seed=%d/%s/%s", seed, op, point), func(t *testing.T) {
					history, keys := seededCrashHistory(seed)
					// Keep successful runs compact; emit the exact artificial workload on failure.
					t.Cleanup(func() {
						if t.Failed() {
							encoded, _ := json.Marshal(history)
							t.Logf("generated_history=%s", encoded)
						}
					})
					dir := t.TempDir()
					initial := mustOpen(t, dir)
					if err := initial.Close(); err != nil {
						t.Fatal(err)
					}
					child := startCrashChild(t, dir)
					var acknowledged []crashRequest
					for _, req := range history {
						acknowledgeCrashRequest(t, child, req, &acknowledged)
					}
					pending := crashRequest{Op: op, Key: "target", Stop: point}
					if op == "put" {
						pending.Value = []byte{255, 0, 10, 128, 1}
					}
					if point == "normal_exit" {
						pending.Stop = ""
					}
					if point == "after_ack" || point == "normal_exit" {
						acknowledgeCrashRequest(t, child, pending, &acknowledged)
					} else {
						child.send(t, pending)
					}
					if point == "normal_exit" {
						if err := child.input.Close(); err != nil {
							t.Fatal(err)
						}
						if err := child.wait(); err != nil {
							t.Fatalf("normal exit: %v", err)
						}
					} else {
						if event := child.read(t); event.Event != "stopped" || event.Point != point {
							t.Fatalf("wrong stop: %+v", event)
						}
						child.kill(t)
					}

					before := logBytes(t, dir)
					scan, scanErr := Inspect(dir)
					partial := point == "partial_header" || point == "partial_body"
					if partial {
						if !errors.Is(scanErr, record.ErrIncomplete) || scan.Status != "incomplete_tail" || scan.LatestSequence != uint64(len(history)) || scan.InvalidOffset == nil || *scan.InvalidOffset != scan.ValidBytes {
							t.Fatalf("incomplete scan: %+v %v", scan, scanErr)
						}
					} else if scanErr != nil || scan.Status != "ok" {
						t.Fatalf("scan: %+v %v", scan, scanErr)
					}
					if !bytes.Equal(before, logBytes(t, dir)) {
						t.Fatal("inspect modified log")
					}
					var diagnostic bytes.Buffer
					db, err := OpenWithOptions(dir, Options{Diagnostics: &diagnostic})
					if err != nil {
						t.Fatal(err)
					}
					defer db.Close()
					report, err := db.Inspect()
					if err != nil {
						t.Fatal(err)
					}
					recovered := recoveredModel(t, db, keys)
					selected := acknowledged
					if report.LatestSequence == uint64(len(acknowledged)+1) && point != "after_ack" && point != "normal_exit" {
						selected = append(append([]crashRequest{}, acknowledged...), pending)
					} else if report.LatestSequence != uint64(len(acknowledged)) {
						t.Fatalf("lost ack or unexpected sequence: ack=%d report=%+v", len(acknowledged), report)
					}
					if !reflect.DeepEqual(recovered, modelAfter(selected)) {
						t.Fatalf("model mismatch: got=%v history=%+v", recovered, selected)
					}
					switch point {
					case "before_write", "partial_header", "partial_body":
						if len(selected) != len(history) {
							t.Fatal("incomplete operation visible")
						}
					case "after_sync", "before_ack", "after_ack", "normal_exit":
						if len(selected) != len(history)+1 {
							t.Fatal("synced operation missing after process exit")
						}
					}
					// Compute byte and operation counts directly from the format, independently of Encode.
					expectedBytes := int64(record.FileHeaderSize)
					var puts, deletes uint64
					for _, req := range selected {
						expectedBytes += int64(record.HeaderSize + len(req.Key) + len(req.Value) + 4)
						if req.Op == "put" {
							puts++
						} else {
							deletes++
						}
					}
					if report.Status != "ok" || report.RecordCount != uint64(len(selected)) || report.PutCount != puts || report.DeleteCount != deletes || report.LogBytes != expectedBytes || report.ValidBytes != expectedBytes {
						t.Fatalf("bad recovered report: %+v expected bytes=%d puts=%d deletes=%d", report, expectedBytes, puts, deletes)
					}
					if partial {
						var event struct {
							Event           string `json:"event"`
							OldSize         int64  `json:"old_size"`
							LastValidOffset int64  `json:"last_valid_offset"`
						}
						if err := json.Unmarshal(diagnostic.Bytes(), &event); err != nil {
							t.Fatal(err)
						}
						if event.Event != "tail_truncate_planned" || event.OldSize != int64(len(before)) || event.LastValidOffset != expectedBytes || !bytes.Equal(logBytes(t, dir), before[:expectedBytes]) {
							t.Fatal("incorrect repair boundary/diagnostic")
						}
					} else if diagnostic.Len() != 0 || !bytes.Equal(before, logBytes(t, dir)) {
						t.Fatal("unexpected repair")
					}
					if err := db.Close(); err != nil {
						t.Fatal(err)
					}
					// A second Open must be idempotent and retain the complete recovered dictionary.
					diagnostic.Reset()
					again, err := OpenWithOptions(dir, Options{Diagnostics: &diagnostic})
					if err != nil {
						t.Fatal(err)
					}
					defer again.Close()
					secondReport, err := again.Inspect()
					if err != nil {
						t.Fatal(err)
					}
					if diagnostic.Len() != 0 || !reflect.DeepEqual(secondReport, report) || !bytes.Equal(logBytes(t, dir), before[:expectedBytes]) || !reflect.DeepEqual(recoveredModel(t, again, keys), recovered) {
						t.Fatal("second Open changed state")
					}
					put(t, again, "continued", []byte{0, 255}, report.LatestSequence+1)
					if err := again.Close(); err != nil {
						t.Fatal(err)
					}
					final := mustOpen(t, dir)
					expected := modelAfter(selected)
					expected["continued"] = []byte{0, 255}
					finalReport, err := final.Inspect()
					if err != nil {
						t.Fatal(err)
					}
					if finalReport.LatestSequence != report.LatestSequence+1 || !reflect.DeepEqual(recoveredModel(t, final, keys), expected) {
						t.Fatal("continued write did not survive reopen")
					}
					t.Logf("ack=%d recovered=%d bytes=%d repair=%t next=%d", len(acknowledged), report.LatestSequence, report.LogBytes, partial, report.LatestSequence+1)
				})
			}
		}
	}
}

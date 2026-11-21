package recovery

import (
	"bytes"
	"errors"
	"io"
	"testing"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

func TestScanBoundaries(t *testing.T) {
	one, _ := record.Encode(record.Record{Sequence: 1, Op: record.Put, Key: "k", Value: []byte("v")})
	two, _ := record.Encode(record.Record{Sequence: 2, Op: record.Delete, Key: "k"})
	data := append(record.FileHeader(), one...)
	data = append(data, two...)
	for cut := 0; cut <= len(data); cut++ {
		result, err := Scan(bytes.NewReader(data[:cut]), int64(cut))
		switch {
		case cut < 16:
			if !errors.Is(err, record.ErrCorrupt) {
				t.Fatalf("cut=%d: %v", cut, err)
			}
		case cut == 16 || cut == 54 || cut == 91:
			if err != nil || result.Report.Status != "ok" {
				t.Fatal(cut, err)
			}
		default:
			if !errors.Is(err, record.ErrIncomplete) {
				t.Fatal(cut, err)
			}
		}
		if cut >= 54 && cut < 91 {
			if result.Report.ValidBytes != 54 || result.Report.LatestSequence != 1 || len(result.Index) != 1 {
				t.Fatal(cut, result)
			}
		}
	}
	result, err := Scan(bytes.NewReader(data), int64(len(data)))
	if err != nil || len(result.Index) != 0 || result.Report.DeleteCount != 1 {
		t.Fatal(result, err)
	}
}

type badReadAt struct{}

func (badReadAt) ReadAt([]byte, int64) (int, error) { return 0, errors.New("injected read") }
func TestScanReadErrors(t *testing.T) {
	result, err := Scan(badReadAt{}, 16)
	if err == nil || result.Report.Status != "io_error" {
		t.Fatal(result, err)
	}
	// A source that disappears before the advertised snapshot boundary is not a recoverable tail.
	result, err = Scan(bytes.NewReader(record.FileHeader()), 40)
	if !errors.Is(err, io.ErrUnexpectedEOF) || result.Report.Status != "io_error" {
		t.Fatal(result, err)
	}
}

func TestScanHeaderEarlyEOF(t *testing.T) {
	header := record.FileHeader()
	for _, cut := range []int{0, 8, len(header) - 1} {
		result, err := Scan(bytes.NewReader(header[:cut]), int64(len(header)))
		report := result.Report
		var problem *Problem
		if !errors.Is(err, io.ErrUnexpectedEOF) || errors.Is(err, record.ErrCorrupt) ||
			!errors.As(err, &problem) || problem.Offset != 0 || problem.LastValidOffset != 0 ||
			report.Status != "io_error" || report.InvalidOffset == nil || *report.InvalidOffset != 0 ||
			report.ValidBytes != 0 || report.LogBytes != int64(len(header)) {
			t.Fatalf("cut=%d report=%+v err=%v", cut, report, err)
		}
	}
}

package recovery

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

// Position identifies a validated latest PUT without retaining its value.
type Position struct {
	Offset   int64
	Sequence uint64
}
type Report struct {
	FormatVersion  uint16  `json:"format_version"`
	LogBytes       int64   `json:"log_bytes"`
	ValidBytes     int64   `json:"valid_bytes"`
	RecordCount    uint64  `json:"record_count"`
	PutCount       uint64  `json:"put_count"`
	DeleteCount    uint64  `json:"delete_count"`
	LiveKeys       int     `json:"live_keys"`
	LatestSequence uint64  `json:"latest_sequence"`
	Status         string  `json:"status"`
	InvalidOffset  *int64  `json:"invalid_offset"`
	Reason         *string `json:"reason"`
}

func (r Report) MarshalJSON() ([]byte, error) {
	type plain Report
	if r.Status != "unsupported_version" {
		return json.Marshal(plain(r))
	}
	b, err := json.Marshal(plain(r))
	if err != nil {
		return nil, err
	}
	var fields map[string]any
	if err := json.Unmarshal(b, &fields); err != nil {
		return nil, err
	}
	for _, name := range []string{"format_version", "valid_bytes", "record_count", "put_count", "delete_count", "live_keys", "latest_sequence"} {
		fields[name] = nil
	}
	return json.Marshal(fields)
}

type Result struct {
	Report Report
	Index  map[string]Position
}
type Problem struct {
	Offset          int64
	LastValidOffset int64
	Cause           error
}

func (p *Problem) Error() string {
	return fmt.Sprintf("offset %d (last valid %d): %v", p.Offset, p.LastValidOffset, p.Cause)
}
func (p *Problem) Unwrap() error { return p.Cause }

// Scan validates a bounded snapshot. It never modifies the log or skips bad records.
func Scan(file io.ReaderAt, size int64) (Result, error) {
	result := Result{Report: Report{LogBytes: size, Status: "ok"}, Index: make(map[string]Position)}
	fail := func(offset int64, err error) (Result, error) {
		status := "io_error"
		switch {
		case errors.Is(err, record.ErrIncomplete):
			status = "incomplete_tail"
		case errors.Is(err, record.ErrUnsupportedVersion):
			status = "unsupported_version"
		case errors.Is(err, record.ErrCorrupt):
			status = "corrupt"
		}
		result.Report.Status = status
		result.Report.InvalidOffset = &offset
		reason := err.Error()
		result.Report.Reason = &reason
		result.Report.LiveKeys = len(result.Index)
		return result, &Problem{offset, result.Report.ValidBytes, err}
	}
	if size < 0 || size > record.MaxLogSize {
		return fail(0, fmt.Errorf("%w: log size", record.ErrCorrupt))
	}
	reader := io.NewSectionReader(file, 0, size)
	if err := record.ReadFileHeader(reader); err != nil {
		return fail(0, err)
	}
	result.Report.FormatVersion = record.Version
	result.Report.ValidBytes = record.FileHeaderSize
	for result.Report.ValidBytes < size {
		offset := result.Report.ValidBytes
		r, err := record.Decode(reader, result.Report.LatestSequence+1)
		if err != nil {
			consumed, _ := reader.Seek(0, io.SeekCurrent)
			if errors.Is(err, io.EOF) || errors.Is(err, record.ErrIncomplete) && consumed != size {
				err = io.ErrUnexpectedEOF
			}
			return fail(offset, err)
		}
		end, _ := reader.Seek(0, io.SeekCurrent)
		if r.Op == record.Put {
			result.Index[r.Key] = Position{offset, r.Sequence}
			result.Report.PutCount++
		} else {
			delete(result.Index, r.Key)
			result.Report.DeleteCount++
		}
		result.Report.RecordCount++
		result.Report.LatestSequence = r.Sequence
		result.Report.ValidBytes = end
	}
	result.Report.LiveKeys = len(result.Index)
	return result, nil
}

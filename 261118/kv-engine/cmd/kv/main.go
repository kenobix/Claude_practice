// Command kv provides synchronous persistent operations and an in-memory session.
package main

import (
	"bufio"
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"unicode/utf8"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/engine"
	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/recovery"
)

const maxRequestSize = 2 << 20

var errInput = errors.New("invalid input")

type request struct {
	Op    string  `json:"op"`
	Key   *string `json:"key"`
	Value *string `json:"value_base64"`
}

const usage = `Usage: kv put|get|delete|inspect --db DIR [--key KEY] [--value-file FILE]
       kv memory | kv -version | kv -h
Persistent put reads bytes from --value-file FILE (use - for stdin).
Persistent mode (Linux): updates sync data.log before success.
Memory mode only: all data is lost when the process exits.
For kv memory, send one JSON object per line:
  {"op":"put","key":"k","value_base64":"dg=="}
  {"op":"get","key":"k"}
  {"op":"delete","key":"k"}
The memory session stops at the first error and creates no database files.
`

func main() { os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr)) }

func run(args []string, in io.Reader, out, errOut io.Writer) int {
	if len(args) == 1 && (args[0] == "-h" || args[0] == "--help") || len(args) == 2 && args[0] == "memory" && (args[1] == "-h" || args[1] == "--help") {
		if _, err := io.WriteString(out, usage); err != nil {
			return fail(errOut, err)
		}
		return 0
	}
	if len(args) == 1 && (args[0] == "-version" || args[0] == "--version") {
		if _, err := fmt.Fprintln(out, "kv stage2 (synchronous log; Linux only)"); err != nil {
			return fail(errOut, err)
		}
		return 0
	}
	if len(args) > 0 && (args[0] == "put" || args[0] == "get" || args[0] == "delete" || args[0] == "inspect") {
		return runPersistent(args, in, out, errOut)
	}
	if len(args) != 1 || args[0] != "memory" {
		return fail(errOut, fmt.Errorf("%w: use kv memory or kv -h", errInput))
	}
	if _, err := io.WriteString(errOut, "{\"event\":\"memory_only\",\"persistent\":false}\n"); err != nil {
		return 6
	}
	db := engine.NewMemory()
	defer db.Close()
	reader := bufio.NewReader(in)
	for {
		line, err := readLine(reader)
		if errors.Is(err, io.EOF) {
			return 0
		}
		if err != nil {
			return fail(errOut, err)
		}
		response, err := execute(db, line)
		if err != nil {
			return fail(errOut, err)
		}
		if err := json.NewEncoder(out).Encode(response); err != nil {
			return fail(errOut, err)
		}
	}
}

// readLine bounds allocations even when a malicious request has no newline.
func readLine(r *bufio.Reader) ([]byte, error) {
	var line []byte
	for {
		part, err := r.ReadSlice('\n')
		if len(line)+len(part) > maxRequestSize {
			return nil, fmt.Errorf("%w: request exceeds 2 MiB including newline", errInput)
		}
		line = append(line, part...)
		if errors.Is(err, bufio.ErrBufferFull) {
			continue
		}
		if errors.Is(err, io.EOF) && len(line) > 0 {
			return line, nil
		}
		return line, err
	}
}

func execute(db *engine.Memory, line []byte) (any, error) {
	if !utf8.Valid(line) {
		return nil, fmt.Errorf("%w: JSON must be UTF-8", errInput)
	}
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.DisallowUnknownFields()
	var req request
	if err := decoder.Decode(&req); err != nil {
		return nil, fmt.Errorf("%w: %v", errInput, err)
	}
	if err := decoder.Decode(new(any)); !errors.Is(err, io.EOF) {
		return nil, fmt.Errorf("%w: one object per line required", errInput)
	}
	if req.Key == nil {
		return nil, fmt.Errorf("%w: key is required", errInput)
	}
	if err := record.ValidateKey(*req.Key); err != nil {
		return nil, err
	}
	switch req.Op {
	case "put":
		if req.Value == nil {
			return nil, fmt.Errorf("%w: put requires value_base64 (empty string is allowed)", errInput)
		}
		if len(*req.Value) > base64.StdEncoding.EncodedLen(record.MaxValueSize) {
			return nil, record.ErrValueTooLarge
		}
		value, err := base64.StdEncoding.Strict().DecodeString(*req.Value)
		if err != nil {
			return nil, fmt.Errorf("%w: invalid base64", errInput)
		}
		seq, err := db.Put(*req.Key, value)
		if err != nil {
			return nil, err
		}
		return struct {
			Sequence uint64 `json:"sequence"`
		}{seq}, nil
	case "get", "delete":
		if req.Value != nil {
			return nil, fmt.Errorf("%w: value_base64 is only valid for put", errInput)
		}
		if req.Op == "delete" {
			seq, err := db.Delete(*req.Key)
			if err != nil {
				return nil, err
			}
			return struct {
				Sequence uint64 `json:"sequence"`
			}{seq}, nil
		}
		value, err := db.Get(*req.Key)
		if err != nil {
			return nil, err
		}
		return struct {
			Found bool   `json:"found"`
			Value string `json:"value_base64"`
		}{true, base64.StdEncoding.EncodeToString(value)}, nil
	default:
		return nil, fmt.Errorf("%w: unsupported op", errInput)
	}
}

func fail(out io.Writer, err error) int {
	code, name := 6, "io"
	switch {
	case errors.Is(err, errInput), errors.Is(err, record.ErrInvalidKey), errors.Is(err, record.ErrValueTooLarge):
		code, name = 2, "invalid_input"
	case errors.Is(err, engine.ErrNotFound):
		code, name = 3, "not_found"
	case errors.Is(err, engine.ErrLocked):
		code, name = 4, "locked"
	case errors.Is(err, record.ErrCorrupt), errors.Is(err, record.ErrIncomplete), errors.Is(err, record.ErrUnsupportedVersion):
		code, name = 5, "corrupt"
	case errors.Is(err, engine.ErrLogFull):
		code, name = 7, "log_full"
	case errors.Is(err, engine.ErrSequenceExhausted):
		code, name = 7, "sequence_exhausted"
	}
	var offset, last *int64
	var problem *recovery.Problem
	if errors.As(err, &problem) {
		offset = &problem.Offset
		last = &problem.LastValidOffset
	}
	if json.NewEncoder(out).Encode(struct {
		Error           string `json:"error"`
		Message         string `json:"message"`
		Offset          *int64 `json:"offset,omitempty"`
		LastValidOffset *int64 `json:"last_valid_offset,omitempty"`
	}{name, err.Error(), offset, last}) != nil {
		return 6
	}
	return code
}

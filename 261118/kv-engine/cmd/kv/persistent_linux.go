//go:build linux

package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/engine"
	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

func runPersistent(args []string, in io.Reader, out, errOut io.Writer) int {
	op := args[0]
	flags := flag.NewFlagSet(op, flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	dir := flags.String("db", "", "existing DB directory")
	var key, path string
	if op != "inspect" {
		flags.StringVar(&key, "key", "", "UTF-8 key")
	}
	if op == "put" {
		flags.StringVar(&path, "value-file", "", "binary input file, or - for stdin")
	}
	if err := flags.Parse(args[1:]); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			if _, err = io.WriteString(out, usage); err != nil {
				return fail(errOut, err)
			}
			return 0
		}
		return fail(errOut, fmt.Errorf("%w: %v", errInput, err))
	}
	if flags.NArg() != 0 || *dir == "" {
		return fail(errOut, fmt.Errorf("%w: --db required; positional arguments are not accepted", errInput))
	}
	if op != "inspect" {
		if err := record.ValidateKey(key); err != nil {
			return fail(errOut, err)
		}
	}
	var value []byte
	if op == "put" {
		if path == "" {
			return fail(errOut, fmt.Errorf("%w: --value-file required", errInput))
		}
		reader := in
		var file *os.File
		if path != "-" {
			var err error
			file, err = os.Open(path)
			if err != nil {
				return fail(errOut, err)
			}
			reader = file
		}
		var err error
		value, err = io.ReadAll(io.LimitReader(reader, record.MaxValueSize+1))
		if file != nil {
			err = errors.Join(err, file.Close())
		}
		if err != nil {
			return fail(errOut, err)
		}
		if err = record.ValidateValue(value); err != nil {
			return fail(errOut, err)
		}
	}
	if op == "inspect" {
		report, err := engine.Inspect(*dir)
		// An invalid scan still produces a report. Open/lock failures produce none.
		if report.Status != "" {
			if outErr := json.NewEncoder(out).Encode(report); outErr != nil {
				return fail(errOut, outErr)
			}
		}
		if err != nil {
			return fail(errOut, err)
		}
		return 0
	}
	db, err := engine.OpenWithOptions(*dir, engine.Options{Create: op == "put" || op == "delete", Diagnostics: errOut})
	if err != nil {
		return fail(errOut, err)
	}
	var seq uint64
	switch op {
	case "put":
		seq, err = db.Put(key, value)
	case "delete":
		seq, err = db.Delete(key)
	case "get":
		value, err = db.Get(key)
	}
	err = errors.Join(err, db.Close())
	if err != nil {
		return fail(errOut, err)
	}
	if op == "get" {
		n, err := out.Write(value)
		if err == nil && n != len(value) {
			err = io.ErrShortWrite
		}
		if err != nil {
			return fail(errOut, err)
		}
	} else if err := json.NewEncoder(out).Encode(struct {
		Sequence uint64 `json:"sequence"`
	}{seq}); err != nil {
		return fail(errOut, err)
	}
	return 0
}

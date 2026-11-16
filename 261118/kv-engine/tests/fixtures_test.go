package tests

import (
	"bytes"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/engine"
	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

func fixtures(t *testing.T) [][]byte {
	t.Helper()
	text, err := os.ReadFile("fixtures.md")
	if err != nil {
		t.Fatal(err)
	}
	parts := strings.Split(string(text), "```text\n")
	if len(parts) != 4 {
		t.Fatal("expected three documented fixtures")
	}
	result := make([][]byte, 3)
	for i, part := range parts[1:] {
		text := strings.SplitN(part, "```", 2)[0]
		result[i], err = hex.DecodeString(strings.Join(strings.Fields(text), ""))
		if err != nil {
			t.Fatal(err)
		}
	}
	return result
}

func TestNormalFixture(t *testing.T) {
	data := fixtures(t)[0]
	if len(data) != 91 || !bytes.Equal(data[:16], record.FileHeader()) {
		t.Fatal("file header or size changed")
	}
	reader := bytes.NewReader(data)
	if err := record.ReadFileHeader(reader); err != nil {
		t.Fatal(err)
	}
	var output = record.FileHeader()
	for _, want := range []record.Record{{Sequence: 1, Op: record.Put, Key: "k", Value: []byte("v")}, {Sequence: 2, Op: record.Delete, Key: "k"}} {
		got, err := record.Decode(reader, want.Sequence)
		if err != nil || got.Sequence != want.Sequence || got.Op != want.Op || got.Key != want.Key || !bytes.Equal(got.Value, want.Value) {
			t.Fatalf("fixture mismatch: %v %+v", err, got)
		}
		encoded, err := record.Encode(want)
		if err != nil {
			t.Fatal(err)
		}
		output = append(output, encoded...)
	}
	if !bytes.Equal(data, output) {
		t.Fatal("encoding differs from fixed documented hex")
	}
	if _, err := record.Decode(reader, 3); !errors.Is(err, io.EOF) {
		t.Fatal(err)
	}
}

func TestIncompleteFixture(t *testing.T) {
	data := fixtures(t)[1]
	reader := bytes.NewReader(data)
	if err := record.ReadFileHeader(reader); err != nil {
		t.Fatal(err)
	}
	if _, err := record.Decode(reader, 1); err != nil {
		t.Fatal(err)
	}
	if len(data)-reader.Len() != 54 {
		t.Fatal("wrong valid boundary")
	}
	if _, err := record.Decode(reader, 2); !errors.Is(err, record.ErrIncomplete) {
		t.Fatal(err)
	}
	if len(data) != 64 {
		t.Fatal("fixture length")
	}
}

func TestCorruptFixture(t *testing.T) {
	data := fixtures(t)[2]
	original := bytes.Clone(data)
	reader := bytes.NewReader(data)
	if err := record.ReadFileHeader(reader); err != nil {
		t.Fatal(err)
	}
	if _, err := record.Decode(reader, 1); !errors.Is(err, record.ErrCorrupt) {
		t.Fatal(err)
	}
	if len(data)-reader.Len() != 54 {
		t.Fatal("decoder skipped to subsequent record")
	}
	if !bytes.Equal(data, original) {
		t.Fatal("decoder mutated input")
	}
}

func TestDiskFixtureCompatibility(t *testing.T) {
	for i, data := range fixtures(t) {
		dir := t.TempDir()
		path := filepath.Join(dir, "data.log")
		if err := os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
		var diagnostic bytes.Buffer
		db, err := engine.OpenWithOptions(dir, engine.Options{Diagnostics: &diagnostic})
		if i == 2 {
			if db != nil || !errors.Is(err, record.ErrCorrupt) {
				t.Fatal(db, err)
			}
		} else {
			if err != nil {
				t.Fatal(err)
			}
			if i == 1 {
				v, err := db.Get("k")
				if err != nil || string(v) != "v" {
					t.Fatal(v, err)
				}
			}
			if err := db.Close(); err != nil {
				t.Fatal(err)
			}
		}
		got, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		want := data
		if i == 1 {
			want = data[:54]
		}
		if !bytes.Equal(got, want) {
			t.Fatal("fixture compatibility changed")
		}
	}
}

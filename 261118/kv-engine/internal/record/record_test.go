package record

import (
	"bytes"
	"encoding/binary"
	"errors"
	"hash/crc32"
	"io"
	"math"
	"reflect"
	"strings"
	"testing"
)

func encoded(t testing.TB, r Record) []byte {
	t.Helper()
	b, err := Encode(r)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestRoundTrip(t *testing.T) {
	for _, tc := range []struct {
		name string
		r    Record
	}{
		{"put", Record{1, Put, "key", []byte{0, 255, 10}}},
		{"empty", Record{1, Put, "empty", []byte{}}},
		{"delete", Record{2, Delete, "key", []byte{}}},
		{"maximum", Record{math.MaxUint64, Put, strings.Repeat("é", 128), bytes.Repeat([]byte{255}, MaxValueSize)}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b := encoded(t, tc.r)
			reader := bytes.NewReader(b)
			got, err := Decode(reader, tc.r.Sequence)
			if err != nil || got.Sequence != tc.r.Sequence || got.Op != tc.r.Op || got.Key != tc.r.Key || !bytes.Equal(got.Value, tc.r.Value) {
				t.Fatalf("round trip mismatch: %v", err)
			}
			if reader.Len() != 0 {
				t.Fatal("record not consumed exactly")
			}
			b2 := encoded(t, got)
			if !bytes.Equal(b, b2) {
				t.Fatal("re-encoding changed bytes")
			}
		})
	}
}

func TestEncodeRejectsInvalidInput(t *testing.T) {
	for _, tc := range []struct {
		name string
		r    Record
		err  error
	}{
		{"empty-key", Record{1, Put, "", nil}, ErrInvalidKey},
		{"long-key", Record{1, Put, strings.Repeat("k", 257), nil}, ErrInvalidKey},
		{"invalid-utf8", Record{1, Put, string([]byte{255}), nil}, ErrInvalidKey},
		{"nul", Record{1, Put, "a\x00b", nil}, ErrInvalidKey},
		{"long-value", Record{1, Put, "k", make([]byte, MaxValueSize+1)}, ErrValueTooLarge},
		{"zero-sequence", Record{0, Put, "k", nil}, ErrCorrupt},
		{"unknown-op", Record{1, 3, "k", nil}, ErrCorrupt},
		{"delete-value", Record{1, Delete, "k", []byte{1}}, ErrCorrupt},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b, err := Encode(tc.r)
			if b != nil || !errors.Is(err, tc.err) {
				t.Fatalf("got %v", err)
			}
		})
	}
}

func TestAllTruncations(t *testing.T) {
	b := encoded(t, Record{1, Put, "key", []byte("value")})
	for cut := 0; cut < len(b); cut++ {
		_, err := Decode(bytes.NewReader(b[:cut]), 1)
		want := ErrIncomplete
		if cut == 0 {
			want = io.EOF
		}
		if !errors.Is(err, want) {
			t.Fatalf("cut=%d got %v want %v", cut, err, want)
		}
	}
}

func TestCRCFailures(t *testing.T) {
	b := encoded(t, Record{1, Put, "key", []byte("value")})
	// Every single-byte bit flip in a complete record must be detected.
	for i := range b {
		bad := bytes.Clone(b)
		bad[i] ^= 1
		got, err := Decode(bytes.NewReader(bad), 1)
		if !errors.Is(err, ErrCorrupt) || !reflect.DeepEqual(got, Record{}) {
			t.Fatalf("byte=%d got %v", i, err)
		}
	}
	// Even with no body, a complete bad header is corruption, not a short tail.
	b[28] ^= 1
	if _, err := Decode(bytes.NewReader(b[:32]), 1); !errors.Is(err, ErrCorrupt) {
		t.Fatal(err)
	}
}

// This reader fails the test if validation attempts to read a body for an invalid header.
type headerOnly struct {
	t *testing.T
	b []byte
}

func (r *headerOnly) Read(p []byte) (int, error) {
	if len(r.b) == 0 {
		r.t.Fatal("read body before rejecting invalid header")
	}
	n := copy(p, r.b)
	r.b = r.b[n:]
	return n, nil
}

func TestInvalidHeadersBeforeBodyAllocation(t *testing.T) {
	for _, tc := range []struct {
		name   string
		change func([]byte)
	}{
		{"total-zero", func(b []byte) { binary.LittleEndian.PutUint32(b, 0) }},
		{"total-huge", func(b []byte) { binary.LittleEndian.PutUint32(b, math.MaxUint32) }},
		{"total-mismatch", func(b []byte) { binary.LittleEndian.PutUint32(b, 100) }},
		{"key-zero", func(b []byte) { binary.LittleEndian.PutUint32(b[16:], 0) }},
		{"key-long", func(b []byte) { binary.LittleEndian.PutUint32(b[16:], 257) }},
		{"key-huge", func(b []byte) { binary.LittleEndian.PutUint32(b[16:], math.MaxUint32) }},
		{"value-long", func(b []byte) { binary.LittleEndian.PutUint32(b[20:], MaxValueSize+1) }},
		{"both-huge", func(b []byte) {
			binary.LittleEndian.PutUint32(b[16:], math.MaxUint32)
			binary.LittleEndian.PutUint32(b[20:], math.MaxUint32)
		}},
		{"unknown-op", func(b []byte) { b[12] = 3 }},
		{"delete-value", func(b []byte) { b[12] = byte(Delete) }},
		{"reserved-13", func(b []byte) { b[13] = 1 }},
		{"reserved-14", func(b []byte) { b[14] = 1 }},
		{"reserved-15", func(b []byte) { b[15] = 1 }},
		{"reserved-24", func(b []byte) { b[24] = 1 }},
		{"zero-sequence", func(b []byte) { binary.LittleEndian.PutUint64(b[4:], 0) }},
		{"sequence-gap", func(b []byte) { binary.LittleEndian.PutUint64(b[4:], 2) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b := encoded(t, Record{1, Put, "k", []byte("v")})[:HeaderSize]
			tc.change(b)
			binary.LittleEndian.PutUint32(b[28:], crc32.Checksum(b[:28], crcTable))
			_, err := Decode(&headerOnly{t, b}, 1)
			if !errors.Is(err, ErrCorrupt) {
				t.Fatal(err)
			}
		})
	}
}

func TestSequenceValidation(t *testing.T) {
	b := encoded(t, Record{2, Put, "k", nil})
	for _, expected := range []uint64{0, 1, 3, math.MaxUint64} {
		if _, err := Decode(bytes.NewReader(b), expected); !errors.Is(err, ErrCorrupt) {
			t.Fatalf("expected %d: %v", expected, err)
		}
	}
}

func TestInvalidKeyWithValidCRC(t *testing.T) {
	for _, v := range []byte{0, 255} {
		b := encoded(t, Record{1, Put, "k", nil})
		b[32] = v
		binary.LittleEndian.PutUint32(b[len(b)-4:], crc32.Checksum(b[:len(b)-4], crcTable))
		if _, err := Decode(bytes.NewReader(b), 1); !errors.Is(err, ErrCorrupt) {
			t.Fatal(err)
		}
	}
}

func TestFileHeader(t *testing.T) {
	header := FileHeader()
	if err := ReadFileHeader(bytes.NewReader(header)); err != nil {
		t.Fatal(err)
	}
	for cut := 0; cut < FileHeaderSize; cut++ {
		if err := ReadFileHeader(bytes.NewReader(header[:cut])); !errors.Is(err, ErrCorrupt) {
			t.Fatalf("cut %d: %v", cut, err)
		}
	}
	for _, offset := range []int{0, 10, 12} {
		bad := bytes.Clone(header)
		bad[offset] ^= 1
		if err := ReadFileHeader(bytes.NewReader(bad)); !errors.Is(err, ErrCorrupt) {
			t.Fatal(err)
		}
	}
	header[8] = 2
	if err := ReadFileHeader(bytes.NewReader(header)); !errors.Is(err, ErrUnsupportedVersion) {
		t.Fatal(err)
	}
}

type failReader struct{ err error }

func (r failReader) Read([]byte) (int, error) { return 0, r.err }

func TestReadErrors(t *testing.T) {
	injected := errors.New("injected read failure")
	if err := ReadFileHeader(failReader{injected}); !errors.Is(err, injected) {
		t.Fatal(err)
	}
	if _, err := Decode(failReader{injected}, 1); !errors.Is(err, injected) {
		t.Fatal(err)
	}
	b := encoded(t, Record{1, Put, "k", nil})
	if _, err := Decode(io.MultiReader(bytes.NewReader(b[:HeaderSize]), failReader{injected}), 1); !errors.Is(err, injected) || errors.Is(err, ErrIncomplete) {
		t.Fatal(err)
	}
}

func TestDecoderConsumesOneRecord(t *testing.T) {
	one := encoded(t, Record{1, Put, "k", nil})
	two := encoded(t, Record{2, Delete, "k", nil})
	reader := bytes.NewReader(append(one, two...))
	if _, err := Decode(reader, 1); err != nil || reader.Len() != len(two) {
		t.Fatal(err)
	}
	if _, err := Decode(reader, 2); err != nil {
		t.Fatal(err)
	}
	if _, err := Decode(reader, 3); !errors.Is(err, io.EOF) {
		t.Fatal(err)
	}
}

func FuzzDecode(f *testing.F) {
	f.Add([]byte{}, uint64(1))
	f.Add(encoded(f, Record{1, Put, "k", []byte("v")}), uint64(1))
	f.Add(bytes.Repeat([]byte{255}, HeaderSize), uint64(1))
	f.Fuzz(func(t *testing.T, b []byte, expected uint64) {
		r, err := Decode(bytes.NewReader(b), expected)
		if err == nil {
			out := encoded(t, r)
			if len(out) > MaxRecordSize || !bytes.HasPrefix(b, out) {
				t.Fatal("decoded invalid prefix")
			}
		}
	})
}

func FuzzFileHeader(f *testing.F) {
	f.Add(FileHeader())
	f.Add([]byte{})
	f.Fuzz(func(t *testing.T, b []byte) {
		if err := ReadFileHeader(bytes.NewReader(b)); err == nil && !bytes.HasPrefix(b, FileHeader()) {
			t.Fatal("accepted unknown header")
		}
	})
}

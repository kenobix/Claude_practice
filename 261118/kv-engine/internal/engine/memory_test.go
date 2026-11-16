package engine

import (
	"bytes"
	"errors"
	"fmt"
	"math"
	"math/rand"
	"strings"
	"sync"
	"testing"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

func TestMemoryOperations(t *testing.T) {
	db := NewMemory()
	defer db.Close()
	if _, err := db.Get("k"); !errors.Is(err, ErrNotFound) {
		t.Fatal(err)
	}
	for i, value := range [][]byte{[]byte("first"), []byte("overwrite"), {}, nil} {
		seq, err := db.Put("k", value)
		if err != nil || seq != uint64(i+1) {
			t.Fatalf("sequence %d: %v", seq, err)
		}
		got, err := db.Get("k")
		if err != nil || got == nil || !bytes.Equal(got, value) {
			t.Fatalf("value %v error %v", got, err)
		}
	}
	for i := 0; i < 2; i++ {
		seq, err := db.Delete("k")
		if err != nil || seq != uint64(5+i) {
			t.Fatal(seq, err)
		}
		if _, err := db.Get("k"); !errors.Is(err, ErrNotFound) {
			t.Fatal(err)
		}
	}
	if seq, err := db.Put("k", []byte{0, 255}); err != nil || seq != 7 {
		t.Fatal(seq, err)
	}
}

func TestMemoryCopies(t *testing.T) {
	var db Memory
	input := []byte("value")
	if _, err := db.Put("k", input); err != nil {
		t.Fatal(err)
	}
	input[0] = 'X'
	got, err := db.Get("k")
	if err != nil {
		t.Fatal(err)
	}
	got[0] = 'Y'
	again, err := db.Get("k")
	if err != nil || string(again) != "value" {
		t.Fatal(string(again), err)
	}
}

func TestKeyBoundaries(t *testing.T) {
	for _, tc := range []struct {
		name, key string
		valid     bool
	}{
		{"one", "k", true}, {"max", strings.Repeat("k", 256), true},
		{"max-utf8", strings.Repeat("é", 128), true}, {"empty", "", false},
		{"over", strings.Repeat("k", 257), false}, {"over-utf8", strings.Repeat("é", 129), false},
		{"invalid-utf8", string([]byte{255}), false}, {"nul", "a\x00b", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			db := NewMemory()
			seq, err := db.Put(tc.key, nil)
			if tc.valid {
				if err != nil || seq != 1 {
					t.Fatal(seq, err)
				}
				if _, err := db.Get(tc.key); err != nil {
					t.Fatal(err)
				}
				if _, err := db.Delete(tc.key); err != nil {
					t.Fatal(err)
				}
			} else {
				if !errors.Is(err, ErrInvalidKey) || seq != 0 {
					t.Fatal(seq, err)
				}
				if _, err := db.Get(tc.key); !errors.Is(err, ErrInvalidKey) {
					t.Fatal(err)
				}
				if _, err := db.Delete(tc.key); !errors.Is(err, ErrInvalidKey) {
					t.Fatal(err)
				}
				if seq, err := db.Put("valid", nil); seq != 1 || err != nil {
					t.Fatal("invalid input advanced sequence")
				}
			}
		})
	}
}

func TestValueBoundaries(t *testing.T) {
	db := NewMemory()
	for i, size := range []int{0, 1, record.MaxValueSize} {
		value := bytes.Repeat([]byte{255}, size)
		seq, err := db.Put("k", value)
		if err != nil || seq != uint64(i+1) {
			t.Fatal(seq, err)
		}
		got, err := db.Get("k")
		if err != nil || !bytes.Equal(got, value) {
			t.Fatal(err)
		}
	}
	if seq, err := db.Put("k", make([]byte, record.MaxValueSize+1)); seq != 0 || !errors.Is(err, ErrValueTooLarge) {
		t.Fatal(seq, err)
	}
	got, err := db.Get("k")
	if err != nil || len(got) != record.MaxValueSize || got[0] != 255 {
		t.Fatal("rejected write changed value")
	}
	if seq, err := db.Delete("k"); seq != 4 || err != nil {
		t.Fatal(seq, err)
	}
}

func TestUnicodeKeysAreNotNormalized(t *testing.T) {
	db := NewMemory()
	db.Put("é", []byte("composed"))
	db.Put("e\u0301", []byte("decomposed"))
	a, _ := db.Get("é")
	b, _ := db.Get("e\u0301")
	if string(a) != "composed" || string(b) != "decomposed" {
		t.Fatal("keys normalized")
	}
}

func TestClose(t *testing.T) {
	db := NewMemory()
	db.Put("k", nil)
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Get("k"); !errors.Is(err, ErrClosed) {
		t.Fatal(err)
	}
	if _, err := db.Put("k", nil); !errors.Is(err, ErrClosed) {
		t.Fatal(err)
	}
	if _, err := db.Delete("k"); !errors.Is(err, ErrClosed) {
		t.Fatal(err)
	}
}

func TestSequenceExhaustion(t *testing.T) {
	db := NewMemory()
	db.sequence = math.MaxUint64 - 1
	if seq, err := db.Put("k", []byte("last")); err != nil || seq != math.MaxUint64 {
		t.Fatal(seq, err)
	}
	if _, err := db.Put("k", []byte("bad")); !errors.Is(err, ErrSequenceExhausted) {
		t.Fatal(err)
	}
	if _, err := db.Delete("k"); !errors.Is(err, ErrSequenceExhausted) {
		t.Fatal(err)
	}
	value, err := db.Get("k")
	if err != nil || string(value) != "last" {
		t.Fatal(string(value), err)
	}
}

func TestReferenceModel(t *testing.T) {
	for _, seed := range []int64{1, 42, 20261118} {
		t.Run(fmt.Sprint(seed), func(t *testing.T) {
			db := NewMemory()
			model := map[string][]byte{}
			rng := rand.New(rand.NewSource(seed))
			var sequence uint64
			for step := 0; step < 1000; step++ {
				key := fmt.Sprint(rng.Intn(16))
				switch rng.Intn(3) {
				case 0:
					value := make([]byte, rng.Intn(32))
					rng.Read(value)
					seq, err := db.Put(key, value)
					sequence++
					if err != nil || seq != sequence {
						t.Fatal(seq, err)
					}
					model[key] = bytes.Clone(value)
				case 1:
					seq, err := db.Delete(key)
					sequence++
					if err != nil || seq != sequence {
						t.Fatal(seq, err)
					}
					delete(model, key)
				}
				for k := 0; k < 16; k++ {
					key := fmt.Sprint(k)
					want, exists := model[key]
					got, err := db.Get(key)
					if !exists {
						if !errors.Is(err, ErrNotFound) {
							t.Fatal(err)
						}
					} else if err != nil || !bytes.Equal(got, want) {
						t.Fatalf("seed=%d step=%d key=%s: %v", seed, step, key, err)
					}
				}
			}
		})
	}
}

func TestConcurrentCalls(t *testing.T) {
	db := NewMemory()
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			key := fmt.Sprint(i)
			for j := 0; j < 100; j++ {
				if _, err := db.Put(key, []byte(key)); err != nil {
					t.Error(err)
				}
				if v, err := db.Get(key); err != nil || string(v) != key {
					t.Error(err)
				}
				if _, err := db.Delete(key); err != nil {
					t.Error(err)
				}
			}
		}(i)
	}
	wg.Wait()
	if db.sequence != 1600 {
		t.Fatal(db.sequence)
	}
}

package engine

import (
	"bytes"
	"errors"
	"math"
	"sync"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

var (
	ErrNotFound          = errors.New("key not found")
	ErrClosed            = errors.New("database closed")
	ErrSequenceExhausted = errors.New("sequence exhausted")
	ErrInvalidKey        = record.ErrInvalidKey
	ErrValueTooLarge     = record.ErrValueTooLarge
)

// Memory is an ephemeral, serialized KV store. Its zero value is ready to use.
// It does not persist data or implement a log capacity limit.
type Memory struct {
	mu       sync.Mutex
	data     map[string][]byte
	sequence uint64
	closed   bool
}

func NewMemory() *Memory { return &Memory{} }

func (m *Memory) Put(key string, value []byte) (uint64, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return 0, ErrClosed
	}
	if err := record.ValidateKey(key); err != nil {
		return 0, err
	}
	if err := record.ValidateValue(value); err != nil {
		return 0, err
	}
	if m.sequence == math.MaxUint64 {
		return 0, ErrSequenceExhausted
	}
	if m.data == nil {
		m.data = make(map[string][]byte)
	}
	m.data[key] = bytes.Clone(value)
	m.sequence++
	return m.sequence, nil
}

func (m *Memory) Get(key string) ([]byte, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return nil, ErrClosed
	}
	if err := record.ValidateKey(key); err != nil {
		return nil, err
	}
	value, ok := m.data[key]
	if !ok {
		return nil, ErrNotFound
	}
	// Always return a non-nil empty slice for an existing empty value.
	return append([]byte{}, value...), nil
}

func (m *Memory) Delete(key string) (uint64, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return 0, ErrClosed
	}
	if err := record.ValidateKey(key); err != nil {
		return 0, err
	}
	if m.sequence == math.MaxUint64 {
		return 0, ErrSequenceExhausted
	}
	delete(m.data, key)
	m.sequence++
	return m.sequence, nil
}

func (m *Memory) Close() error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.closed = true
	m.data = nil
	return nil
}

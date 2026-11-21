package record

import (
	"encoding/binary"
	"errors"
	"fmt"
	"hash/crc32"
	"io"
	"strings"
	"unicode/utf8"
)

const (
	MaxKeySize     = 256
	MaxValueSize   = 1 << 20
	FileHeaderSize = 16
	HeaderSize     = 32
	MaxRecordSize  = HeaderSize + MaxKeySize + MaxValueSize + 4
	MaxLogSize     = 256 << 20
	Version        = 1
)

var (
	ErrInvalidKey         = errors.New("key must be 1..256 UTF-8 bytes without NUL")
	ErrValueTooLarge      = errors.New("value exceeds 1 MiB")
	ErrCorrupt            = errors.New("corrupt record")
	ErrIncomplete         = errors.New("incomplete record")
	ErrUnsupportedVersion = errors.New("unsupported file version")
	crcTable              = crc32.MakeTable(crc32.Castagnoli)
)

type Operation byte

const (
	Put    Operation = 1
	Delete Operation = 2
)

// Record owns its decoded value. Sequence must be nonzero.
type Record struct {
	Sequence uint64
	Op       Operation
	Key      string
	Value    []byte
}

func ValidateKey(key string) error {
	if len(key) < 1 || len(key) > MaxKeySize || !utf8.ValidString(key) || strings.IndexByte(key, 0) >= 0 {
		return ErrInvalidKey
	}
	return nil
}

func ValidateValue(value []byte) error {
	if len(value) > MaxValueSize {
		return ErrValueTooLarge
	}
	return nil
}

func FileHeader() []byte {
	h := make([]byte, FileHeaderSize)
	copy(h, "KVELOG01")
	binary.LittleEndian.PutUint16(h[8:10], Version)
	binary.LittleEndian.PutUint16(h[10:12], FileHeaderSize)
	return h
}

// ReadFileHeader does not treat a short existing file header as a repairable tail.
func ReadFileHeader(r io.Reader) error {
	var h [FileHeaderSize]byte
	if _, err := io.ReadFull(r, h[:]); err != nil {
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return fmt.Errorf("%w: short file header: %w", ErrCorrupt, err)
		}
		return fmt.Errorf("read file header: %w", err)
	}
	if string(h[:8]) != "KVELOG01" {
		return fmt.Errorf("%w: magic", ErrCorrupt)
	}
	if binary.LittleEndian.Uint16(h[8:10]) != Version {
		return ErrUnsupportedVersion
	}
	if binary.LittleEndian.Uint16(h[10:12]) != FileHeaderSize || binary.LittleEndian.Uint32(h[12:16]) != 0 {
		return fmt.Errorf("%w: file header fields", ErrCorrupt)
	}
	return nil
}

func Encode(r Record) ([]byte, error) {
	if err := ValidateKey(r.Key); err != nil {
		return nil, err
	}
	if err := ValidateValue(r.Value); err != nil {
		return nil, err
	}
	if r.Sequence == 0 || (r.Op != Put && r.Op != Delete) || (r.Op == Delete && len(r.Value) != 0) {
		return nil, fmt.Errorf("%w: sequence or operation", ErrCorrupt)
	}
	size := HeaderSize + len(r.Key) + len(r.Value) + 4
	b := make([]byte, size)
	binary.LittleEndian.PutUint32(b[:4], uint32(size))
	binary.LittleEndian.PutUint64(b[4:12], r.Sequence)
	b[12] = byte(r.Op)
	binary.LittleEndian.PutUint32(b[16:20], uint32(len(r.Key)))
	binary.LittleEndian.PutUint32(b[20:24], uint32(len(r.Value)))
	binary.LittleEndian.PutUint32(b[28:32], crc32.Checksum(b[:28], crcTable))
	copy(b[32:], r.Key)
	copy(b[32+len(r.Key):], r.Value)
	binary.LittleEndian.PutUint32(b[size-4:], crc32.Checksum(b[:size-4], crcTable))
	return b, nil
}

// Decode reads one record, requiring the exact next sequence supplied by the caller.
// EOF at a record boundary returns io.EOF; a short last record returns ErrIncomplete.
// Lengths and header CRC are validated before allocating at most MaxRecordSize-HeaderSize.
func Decode(r io.Reader, expected uint64) (Record, error) {
	var h [HeaderSize]byte
	n, err := io.ReadFull(r, h[:])
	if err != nil {
		if n == 0 && errors.Is(err, io.EOF) {
			return Record{}, io.EOF
		}
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return Record{}, ErrIncomplete
		}
		return Record{}, fmt.Errorf("read record header: %w", err)
	}
	if crc32.Checksum(h[:28], crcTable) != binary.LittleEndian.Uint32(h[28:32]) {
		return Record{}, fmt.Errorf("%w: header CRC", ErrCorrupt)
	}
	size := uint64(binary.LittleEndian.Uint32(h[:4]))
	sequence := binary.LittleEndian.Uint64(h[4:12])
	op := Operation(h[12])
	keySize := uint64(binary.LittleEndian.Uint32(h[16:20]))
	valueSize := uint64(binary.LittleEndian.Uint32(h[20:24]))
	if expected == 0 || sequence != expected || (op != Put && op != Delete) || h[13] != 0 || h[14] != 0 || h[15] != 0 || binary.LittleEndian.Uint32(h[24:28]) != 0 {
		return Record{}, fmt.Errorf("%w: header fields or sequence", ErrCorrupt)
	}
	if keySize < 1 || keySize > MaxKeySize || valueSize > MaxValueSize || (op == Delete && valueSize != 0) || size != HeaderSize+keySize+valueSize+4 {
		return Record{}, fmt.Errorf("%w: length", ErrCorrupt)
	}
	body := make([]byte, int(size)-HeaderSize)
	if _, err := io.ReadFull(r, body); err != nil {
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return Record{}, ErrIncomplete
		}
		return Record{}, fmt.Errorf("read record body: %w", err)
	}
	checksum := crc32.Update(crc32.Checksum(h[:], crcTable), crcTable, body[:len(body)-4])
	if checksum != binary.LittleEndian.Uint32(body[len(body)-4:]) {
		return Record{}, fmt.Errorf("%w: record CRC", ErrCorrupt)
	}
	key := string(body[:int(keySize)])
	if err := ValidateKey(key); err != nil {
		return Record{}, fmt.Errorf("%w: %v", ErrCorrupt, err)
	}
	return Record{Sequence: sequence, Op: op, Key: key, Value: body[int(keySize) : len(body)-4]}, nil
}

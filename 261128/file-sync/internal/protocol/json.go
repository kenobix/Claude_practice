package protocol

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"strconv"
	"unicode/utf8"
)

var ErrJSON = errors.New("invalid JSON")

// DecodeChange enforces exact keys, types, and the single-object wire format.
func DecodeChange(data []byte) (Change, error) {
	var c Change
	fields, err := object(data, "hub_generation", "client_id", "op_id", "path", "base_revision", "content_hash", "size", "deleted")
	if err != nil {
		return c, err
	}
	for key, target := range map[string]*string{
		"hub_generation": &c.Generation, "client_id": &c.Client, "op_id": &c.Operation, "path": &c.Path,
	} {
		if !decodeType(fields[key], '"', target) {
			return c, ErrJSON
		}
	}
	if !decodeInteger(fields["base_revision"], &c.Base) || !decodeInteger(fields["size"], &c.Size) ||
		(!decodeType(fields["deleted"], 't', &c.Deleted) && !decodeType(fields["deleted"], 'f', &c.Deleted)) {
		return c, ErrJSON
	}
	if !bytes.Equal(fields["content_hash"], []byte("null")) {
		var hash string
		if !decodeType(fields["content_hash"], '"', &hash) {
			return c, ErrJSON
		}
		c.Hash = &hash
	}
	return c, nil
}

func decodeInteger(raw json.RawMessage, target *int64) bool {
	n, err := strconv.ParseInt(string(raw), 10, 64)
	*target = n
	return err == nil
}

func decodeType(raw json.RawMessage, first byte, out any) bool {
	return len(raw) > 0 && raw[0] == first && json.Unmarshal(raw, out) == nil
}

func object(data []byte, names ...string) (map[string]json.RawMessage, error) {
	if !ValidJSONText(data) {
		return nil, ErrJSON
	}
	d := json.NewDecoder(bytes.NewReader(data))
	if tok, err := d.Token(); err != nil || tok != json.Delim('{') {
		return nil, ErrJSON
	}
	fields := make(map[string]json.RawMessage, len(names))
	allowed := make(map[string]bool, len(names))
	for _, name := range names {
		allowed[name] = true
	}
	for d.More() {
		key, err := d.Token()
		k, ok := key.(string)
		if err != nil || !ok || !allowed[k] || fields[k] != nil {
			return nil, ErrJSON
		}
		var value json.RawMessage
		if err := d.Decode(&value); err != nil {
			return nil, ErrJSON
		}
		fields[k] = value
	}
	if tok, err := d.Token(); err != nil || tok != json.Delim('}') || len(fields) != len(names) {
		return nil, ErrJSON
	}
	if _, err := d.Token(); err != io.EOF {
		return nil, ErrJSON
	}
	return fields, nil
}

// The standard decoder replaces unpaired surrogates with U+FFFD. On this wire
// they are invalid, while an explicitly encoded U+FFFD is a distinct valid name.
func ValidJSONText(data []byte) bool {
	if !utf8.Valid(data) {
		return false
	}
	for i := 0; i < len(data); i++ {
		if data[i] != '\\' {
			continue
		}
		i++
		if i >= len(data) {
			return false
		}
		if data[i] != 'u' {
			continue
		}
		if i+4 >= len(data) {
			return false
		}
		n, err := strconv.ParseUint(string(data[i+1:i+5]), 16, 16)
		if err != nil || (n >= 0xdc00 && n <= 0xdfff) {
			return false
		}
		i += 4
		if n >= 0xd800 && n <= 0xdbff {
			if i+6 >= len(data) || data[i+1] != '\\' || data[i+2] != 'u' {
				return false
			}
			low, err := strconv.ParseUint(string(data[i+3:i+7]), 16, 16)
			if err != nil || low < 0xdc00 || low > 0xdfff {
				return false
			}
			i += 6
		}
	}
	return true
}

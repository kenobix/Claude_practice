package protocol

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"strings"
)

const (
	MaxJSON          = 8 << 10
	MaxPaths         = 4096
	MaxReceipts      = 8192
	GenerationHeader = "X-Hub-Generation"
	ClientHeader     = "X-Client-ID"
	Binary           = "application/octet-stream"
	JSON             = "application/json"
)

type Entry struct {
	Path      string  `json:"path"`
	Revision  int64   `json:"revision"`
	Hash      *string `json:"content_hash"`
	Size      int64   `json:"size"`
	Deleted   bool    `json:"deleted"`
	Client    string  `json:"client_id"`
	Operation string  `json:"op_id"`
}

type Manifest struct {
	Client     string  `json:"-"`
	Space      string  `json:"space_id"`
	Generation string  `json:"hub_generation"`
	Entries    []Entry `json:"entries"`
}

type Change struct {
	Generation string  `json:"hub_generation"`
	Client     string  `json:"client_id"`
	Operation  string  `json:"op_id"`
	Path       string  `json:"path"`
	Base       int64   `json:"base_revision"`
	Hash       *string `json:"content_hash"`
	Size       int64   `json:"size"`
	Deleted    bool    `json:"deleted"`
}

type Receipt struct {
	Generation string `json:"hub_generation"`
	Operation  string `json:"op_id"`
	Result     string `json:"result"`
	Entry      Entry  `json:"entry"`
}

type Blob struct {
	Hash string `json:"content_hash"`
	Size int64  `json:"size"`
}

type Problem struct {
	Code       string   `json:"error"`
	Generation string   `json:"hub_generation,omitempty"`
	Current    *Entry   `json:"-"`
	Conflicts  []string `json:"conflicts,omitempty"`
	Status     int      `json:"-"`
}

func (p *Problem) Error() string { return fmt.Sprintf("hub HTTP %d: %s", p.Status, p.Code) }

func IsHex(value string, length int) bool {
	return len(value) == length && strings.Trim(value, "0123456789abcdef") == ""
}

func ValidName(value string) bool {
	return len(value) > 0 && len(value) <= 32 && value[0] != '-' &&
		strings.Trim(value, "abcdefghijklmnopqrstuvwxyz0123456789-") == ""
}

func NewID() (string, error) {
	var entropy [16]byte
	if _, err := rand.Read(entropy[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(entropy[:]), nil
}

func TokenDigest(token string) ([32]byte, bool) {
	raw, err := base64.RawURLEncoding.Strict().DecodeString(token)
	return sha256.Sum256([]byte(token)), err == nil && len(raw) == 32 &&
		base64.RawURLEncoding.EncodeToString(raw) == token
}

func MaxCandidate(size int64, limit int64, hash *string, deleted bool) bool {
	if deleted {
		return size == 0 && hash == nil
	}
	return size >= 0 && size <= limit && hash != nil && IsHex(*hash, 64)
}

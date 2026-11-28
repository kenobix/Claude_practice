package safefs

import (
	"errors"
	"fmt"
	"strings"
	"unicode"
	"unicode/utf8"
)

const (
	Management = ".file-sync"
	MaxSize    = 10 << 20
	MaxFiles   = 1000
	MaxDirs    = 32001
)

var (
	ErrPath      = errors.New("invalid path")
	ErrType      = errors.New("unsupported file type or link count")
	ErrLimit     = errors.New("resource limit")
	ErrStructure = errors.New("file and directory namespace conflict")
	ErrChanged   = errors.New("file changed during operation")
)

func Validate(path string) error {
	if len(path) == 0 || len(path) > 1024 || !utf8.ValidString(path) {
		return fmt.Errorf("%w: %q", ErrPath, path)
	}
	parts := strings.Split(path, "/")
	if len(parts) > 32 {
		return fmt.Errorf("%w: too many components in %q", ErrPath, path)
	}
	for _, part := range parts {
		if !validComponent(part) || part == Management {
			return fmt.Errorf("%w: invalid component in %q", ErrPath, path)
		}
	}
	return nil
}

func validComponent(name string) bool {
	return len(name) > 0 && len(name) <= 255 && utf8.ValidString(name) &&
		name != "." && name != ".." && !strings.ContainsAny(name, "/\\") &&
		strings.IndexFunc(name, func(r rune) bool { return unicode.Is(unicode.Cc, r) }) == -1
}

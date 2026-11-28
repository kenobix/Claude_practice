package safefs

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/sys/unix"
)

func requireOK(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

func makeRoot(t *testing.T) (string, *Dir, *Volume) {
	t.Helper()
	base := t.TempDir()
	requireOK(t, os.Mkdir(filepath.Join(base, "root"), 0700))
	volume, err := OpenVolume(base)
	requireOK(t, err)
	t.Cleanup(volume.Close)
	root, err := volume.LockRoot("root")
	requireOK(t, err)
	t.Cleanup(root.Close)
	return base, root, volume
}

func TestValidatePaths(t *testing.T) {
	for _, path := range []string{"", "/", "a//b", "a/..", "a/./b", "a\\b", "a/", "\x00",
		"a\xff", "a\u001f", "a\u007f", "a\u0085", ".file-sync", "x/.file-sync",
		strings.Repeat("é", 128), strings.Repeat("d/", 32) + "f",
		strings.Repeat(strings.Repeat("z", 255)+"/", 4) + "p"} {
		if err := Validate(path); !errors.Is(err, ErrPath) {
			t.Errorf("Validate(%q) = %v", path, err)
		}
	}
	for _, path := range []string{"a", "A", ".FILE-SYNC", "é.txt", "e\u0301.txt", strings.Repeat("é", 127) + "a",
		strings.Repeat("d/", 31) + "f", strings.Repeat(strings.Repeat("z", 255)+"/", 3) + strings.Repeat("a", 254) + "/x"} {
		if err := Validate(path); err != nil {
			t.Errorf("Validate(%q) = %v", path, err)
		}
	}
}

func TestP1_05_ParentSwapBeforeFD(t *testing.T) {
	base, root, _ := makeRoot(t)
	before := filepath.Join(base, "root", "parent")
	outside := filepath.Join(base, "outside")
	requireOK(t, os.Mkdir(before, 0700))
	requireOK(t, os.Mkdir(outside, 0700))
	requireOK(t, os.WriteFile(filepath.Join(before, "leaf"), []byte("inside"), 0600))
	requireOK(t, os.WriteFile(filepath.Join(outside, "leaf"), []byte("sentinel"), 0600))
	_, err := root.Scan()
	requireOK(t, err)
	path := "parent/leaf"
	requireOK(t, Validate(path))
	// Swap the parent after validation and before the O_PATH acquisition.
	original := filepath.Join(base, "root", "saved")
	requireOK(t, os.Rename(before, original))
	requireOK(t, os.Symlink(outside, before))
	gotFD, _, err := root.node(path)
	if gotFD >= 0 {
		unix.Close(gotFD)
	}
	if !errors.Is(err, unix.ELOOP) {
		t.Fatalf("O_PATH followed replacement parent: %v", err)
	}
	if _, _, err := root.ReadUser(path, nil); !errors.Is(err, unix.ELOOP) {
		t.Fatalf("read followed replacement parent: %v", err)
	}
	if _, _, err := root.Parent(path, false); !errors.Is(err, unix.ELOOP) {
		t.Fatalf("parent open followed replacement parent: %v", err)
	}
	for name, expected := range map[string]string{outside: "sentinel", original: "inside"} {
		data, err := os.ReadFile(filepath.Join(name, "leaf"))
		requireOK(t, err)
		if !bytes.Equal(data, []byte(expected)) {
			t.Fatalf("changed %s: %q", name, data)
		}
	}
}

func TestLockAndNoReplace(t *testing.T) {
	base, root, volume := makeRoot(t)
	if second, err := volume.LockRoot("root"); !errors.Is(err, unix.EWOULDBLOCK) {
		if second != nil {
			second.Close()
		}
		t.Fatalf("second lock: %v", err)
	}
	stage, err := root.PrivateDir("stage", true)
	requireOK(t, err)
	defer stage.Close()
	f, id, err := stage.Create("incoming")
	requireOK(t, err)
	_, err = f.WriteString("new")
	requireOK(t, err)
	requireOK(t, f.Sync())
	requireOK(t, f.Close())
	requireOK(t, os.WriteFile(filepath.Join(base, "root", "leaf"), []byte("old"), 0600))
	if err := stage.Install("incoming", id, root, "leaf", false); !errors.Is(err, unix.EEXIST) {
		t.Fatalf("rename to existing path: %v", err)
	}
	for name, want := range map[string]string{"leaf": "old", "stage/incoming": "new"} {
		data, err := os.ReadFile(filepath.Join(base, "root", name))
		requireOK(t, err)
		if string(data) != want {
			t.Fatalf("%s: %q", name, data)
		}
	}
}

func TestScanDirectoryLimit(t *testing.T) {
	base, root, _ := makeRoot(t)
	for i := 1; i < MaxDirs; i++ {
		requireOK(t, os.Mkdir(filepath.Join(base, "root", fmt.Sprintf("d%05d", i)), 0700))
	}
	s, err := root.Scan()
	requireOK(t, err)
	if len(s.Dirs) != MaxDirs || len(s.Files) != 0 {
		t.Fatalf("expected %d directories and no files: %d, %d", MaxDirs, len(s.Dirs), len(s.Files))
	}
	requireOK(t, os.Mkdir(filepath.Join(base, "root", "over-limit"), 0700))
	if _, err := root.Scan(); !errors.Is(err, ErrLimit) {
		t.Fatalf("expected directory limit: %v", err)
	}
}

func TestUnreadableDirectoryFailsTheScan(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("requires an unprivileged euid for an EACCES fixture")
	}
	base, root, _ := makeRoot(t)
	child := filepath.Join(base, "root", "unreadable")
	requireOK(t, os.Mkdir(child, 0700))
	defer func() { requireOK(t, os.Chmod(child, 0700)) }()
	requireOK(t, os.Chmod(child, 0000))
	if _, err := root.Scan(); !errors.Is(err, unix.EACCES) {
		t.Fatalf("expected EACCES instead of a successful empty scan: %v", err)
	}
}

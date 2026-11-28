package local

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
	"golang.org/x/sys/unix"
)

const notes = "notes.txt"

type fixtureEntry struct {
	UnitHex string `json:"unit_hex"`
	Repeat  int
	safefs.Digest
	SHA256 string `json:"sha256"`
}

type fixtureFile struct {
	Contents map[string]fixtureEntry
}

func fixture(t *testing.T, id string) ([]byte, safefs.Digest) {
	t.Helper()
	raw, err := os.ReadFile("../../fixtures/expected.json")
	must(t, err)
	var input fixtureFile
	must(t, json.Unmarshal(raw, &input))
	f, ok := input.Contents[id]
	if !ok {
		t.Fatalf("fixture %q not found", id)
	}
	unit, err := hex.DecodeString(f.UnitHex)
	must(t, err)
	data := bytes.Repeat(unit, f.Repeat)
	digest := independentDigest(data)
	if digest.Hash != f.SHA256 || digest.Size != f.Size {
		t.Fatalf("fixture %q: got %+v, want %s/%d", id, digest, f.SHA256, f.Size)
	}
	return data, digest
}

func independentDigest(data []byte) safefs.Digest {
	return safefs.Digest{Hash: fmt.Sprintf("%x", sha256.Sum256(data)), Size: int64(len(data))}
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

type lab struct {
	volume, a, b string
}

func newLab(t *testing.T) lab {
	t.Helper()
	// The volume contents are disposable, and the source and destination are siblings.
	v := t.TempDir()
	l := lab{volume: v, a: filepath.Join(v, "A"), b: filepath.Join(v, "B")}
	must(t, os.Mkdir(l.a, 0700))
	must(t, os.Mkdir(l.b, 0700))
	must(t, Init(t.Context(), v, "B"))
	return l
}

func (l lab) request(revision int64) Request {
	return Request{Volume: l.volume, Source: "A", Destination: "B", Revision: revision}
}

func (l lab) stage(name string) string {
	return filepath.Join(l.b, ".file-sync", "stage", name)
}

func (l lab) status(t *testing.T) Status {
	t.Helper()
	status, err := Inspect(t.Context(), l.volume, "B")
	must(t, err)
	return status
}

func writeAt(t *testing.T, root, path string, data []byte) {
	t.Helper()
	path = filepath.Join(root, path)
	must(t, os.MkdirAll(filepath.Dir(path), 0700))
	must(t, os.WriteFile(path, data, 0600))
}

func assertFile(t *testing.T, root, path string, want []byte, digest safefs.Digest) {
	t.Helper()
	actual, err := os.ReadFile(filepath.Join(root, path))
	must(t, err)
	if !bytes.Equal(actual, want) || independentDigest(actual) != digest {
		t.Fatalf("wrong content %q: got %+v, want %+v", path, independentDigest(actual), digest)
	}
}

func assertBase(t *testing.T, status Status, path string, revision int64, digest safefs.Digest) {
	t.Helper()
	if actual := status.Bases[path]; actual != (Base{Revision: revision, Digest: digest}) {
		t.Fatalf("base %q: got %+v, want %d/%+v", path, actual, revision, digest)
	}
}

func assertUntouched(t *testing.T, l lab) {
	t.Helper()
	entries, err := Scan(l.volume, "B")
	must(t, err)
	status := l.status(t)
	if len(status.Bases) != 0 || len(status.Plans) != 0 || len(entries) != 0 {
		t.Fatalf("unexpected destination change: entries=%v status=%+v", entries, status)
	}
}

func assertCompleted(t *testing.T, l lab, expected map[string]string, revision int64) {
	t.Helper()
	entries, err := Scan(l.volume, "B")
	must(t, err)
	status := l.status(t)
	if len(entries) != len(expected) || len(status.Bases) != len(expected) || len(status.Plans) != len(expected) {
		t.Fatalf("entries=%d baselines=%d plans=%d: want %d for each", len(entries), len(status.Bases), len(status.Plans), len(expected))
	}
	want := make(map[string]safefs.Digest)
	for path, id := range expected {
		digest, cached := want[id]
		if !cached {
			_, digest = fixture(t, id)
			want[id] = digest
		}
		assertBase(t, status, path, revision, digest)
	}
	for _, entry := range entries {
		id, ok := expected[entry.Path]
		if !ok || entry.Digest != want[id] {
			t.Fatalf("scan: unexpected entry %+v", entry)
		}
		data, digest := fixture(t, id)
		assertFile(t, l.a, entry.Path, data, digest)
		assertFile(t, l.b, entry.Path, data, digest)
	}
	for _, p := range status.Plans {
		if p.Status != "complete" || p.Target != status.Bases[p.Path] || p.Saved {
			t.Fatalf("plan not completed correctly: %+v", p)
		}
	}
	stage, err := os.ReadDir(l.stage(""))
	must(t, err)
	if len(stage) != 0 {
		t.Fatalf("completed copy retained temp: %v", stage)
	}
}

func seedP(t *testing.T, l lab) {
	t.Helper()
	p, _ := fixture(t, "P")
	writeAt(t, l.a, notes, p)
	must(t, Copy(t.Context(), l.request(1)))
}

func TestP1_01_NormalCopy(t *testing.T) {
	l := newLab(t)
	seedP(t, l)
	assertCompleted(t, l, map[string]string{notes: "P"}, 1)
	must(t, Copy(t.Context(), l.request(1)))
	assertCompleted(t, l, map[string]string{notes: "P"}, 1)
}

func TestP1_02_SIGKILL(t *testing.T) {
	for _, point := range []string{"incoming-two-bytes", "renamed", "before-baseline-commit", "baseline-committed"} {
		t.Run(point, func(t *testing.T) {
			l := newLab(t)
			seedP(t, l)
			p, pHash := fixture(t, "P")
			q, qHash := fixture(t, "Q")
			writeAt(t, l.a, notes, q)
			killAtBoundary(t, l, point)
			assertFile(t, l.a, notes, q, qHash)
			status := l.status(t)
			revision, digest, planStatus := int64(1), pHash, "pending"
			if point == "baseline-committed" {
				revision, digest, planStatus = 2, qHash, "complete"
			}
			assertBase(t, status, notes, revision, digest)
			plan := findPlan(t, status, 2)
			if plan.Status != planStatus || plan.Before != pHash || plan.Target.Digest != qHash || plan.Saved {
				t.Fatalf("incorrect persisted plan: %+v", plan)
			}
			if point == "incoming-two-bytes" {
				assertFile(t, l.b, notes, p, pHash)
				partial := []byte{0x68, 0x65}
				partialHash := safefs.Digest{Hash: "372f7e2fd2d01ce2a1d71dc072acbba4c6fd25a1087cd7f153f4ec0ce37e1ede", Size: 2}
				assertFile(t, l.stage(""), plan.Incoming(), partial, partialHash)
				t.Logf("after SIGKILL: B=P/%s base=1 pending=Q/%s temp=%s (%d bytes)",
					pHash.Hash, qHash.Hash, partialHash.Hash, partialHash.Size)
			} else {
				assertFile(t, l.b, notes, q, qHash)
				_, err := os.Lstat(l.stage(plan.Incoming()))
				if !errors.Is(err, os.ErrNotExist) {
					t.Fatalf("candidate after rename: %v", err)
				}
				assertFile(t, l.stage(""), plan.Backup(), p, pHash)
				t.Logf("after SIGKILL: B=Q/%s base=%d plan=%s temp=absent backup=P/%s",
					qHash.Hash, revision, planStatus, pHash.Hash)
			}
			if point == "before-baseline-commit" {
				must(t, Copy(t.Context(), l.request(2)))
			} else {
				must(t, Recover(t.Context(), l.request(0)))
			}
			status = l.status(t)
			assertFile(t, l.a, notes, q, qHash)
			assertFile(t, l.b, notes, q, qHash)
			assertBase(t, status, notes, 2, qHash)
			if findPlan(t, status, 2).Status != "complete" || findPlan(t, status, 1).Status != "complete" {
				t.Fatalf("plan not completed: %+v", status)
			}
			stage, err := os.ReadDir(l.stage(""))
			must(t, err)
			if len(stage) != 0 {
				t.Fatalf("stage not empty: %v", stage)
			}
			t.Logf("after recovery: A=B=Q/%s base=2 plan=complete temp=absent", qHash.Hash)
		})
	}
}

func findPlan(t *testing.T, s Status, revision int64) Plan {
	t.Helper()
	for _, p := range s.Plans {
		if p.Target.Revision == revision {
			return p
		}
	}
	t.Fatalf("no plan for revision %d in %+v", revision, s)
	return Plan{}
}

func killAtBoundary(t *testing.T, l lab, point string, extra ...string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(t.Context(), 15*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^$")
	cmd.Env = append(append(os.Environ(), "FILE_SYNC_TEST_CHILD="+point, "FILE_SYNC_TEST_VOLUME="+l.volume), extra...)
	stdout, err := cmd.StdoutPipe()
	must(t, err)
	stdin, err := cmd.StdinPipe()
	must(t, err)
	defer stdin.Close()
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	must(t, cmd.Start())
	defer func() {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	}()
	line, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil || line != point+"\n" {
		waitErr := cmd.Wait()
		t.Fatalf("failure before %q: got %q/%v, wait=%v, stderr=%s", point, line, err, waitErr, stderr.String())
	}
	must(t, cmd.Process.Signal(syscall.SIGKILL))
	err = cmd.Wait()
	var exit *exec.ExitError
	if !errors.As(err, &exit) {
		t.Fatalf("expected child killed, got %v", err)
	}
	status := exit.Sys().(syscall.WaitStatus)
	if !status.Signaled() || status.Signal() != syscall.SIGKILL {
		t.Fatalf("unexpected child shutdown: %v", status)
	}
}

// The child reports the write boundary and waits for SIGKILL while holding the locks.
func TestMain(m *testing.M) {
	point := os.Getenv("FILE_SYNC_TEST_CHILD")
	if point == "" {
		os.Exit(m.Run())
	}
	req := lab{volume: os.Getenv("FILE_SYNC_TEST_VOLUME")}.request(2)
	notify := func(ev event) error {
		if ev.point == strings.TrimPrefix(strings.TrimPrefix(point, "p3-"), "p4-") {
			if _, err := fmt.Fprintln(os.Stdout, point); err != nil {
				return err
			}
			var wait [1]byte
			_, err := io.ReadFull(os.Stdin, wait[:])
			return err
		}
		return nil
	}
	var err error
	if strings.HasPrefix(point, "p4-") {
		remote, remoteErr := protocol.NewClient(os.Getenv("FILE_SYNC_TEST_HUB"), localTestToken(2))
		if remoteErr != nil {
			err = remoteErr
		} else {
			err = Sync(context.Background(), NetworkRequest{Volume: req.Volume, Root: "B", Remote: remote, notify: notify})
		}
	} else if strings.HasPrefix(point, "p3-") {
		l := lab{volume: req.Volume}
		err = resumeDelete(context.Background(), l, notify)
	} else {
		err = run(context.Background(), req, false, notify)
	}
	fmt.Fprintln(os.Stderr, "child returned without SIGKILL:", err)
	os.Exit(1)
}

func TestP1_03_HashAndExternalEdit(t *testing.T) {
	for _, point := range []string{"tampered-incoming", "before-final-check", "restart-neither"} {
		t.Run(point, func(t *testing.T) {
			l := newLab(t)
			seedP(t, l)
			p, pHash := fixture(t, "P")
			q, qHash := fixture(t, "Q")
			r, rHash := fixture(t, "R")
			writeAt(t, l.a, notes, q)
			var err error
			switch point {
			case "tampered-incoming":
				err = run(t.Context(), l.request(2), false, func(ev event) error {
					if ev.point == "incoming-written" {
						writeAt(t, l.stage(""), "substitution", r)
						return os.Rename(l.stage("substitution"), l.stage(ev.plan.Incoming()))
					}
					return nil
				})
			case "before-final-check":
				err = run(t.Context(), l.request(2), false, func(ev event) error {
					if ev.point == point {
						writeAt(t, l.b, notes, r)
					}
					return nil
				})
			default:
				killAtBoundary(t, l, "incoming-two-bytes")
				writeAt(t, l.b, notes, r)
				err = Recover(t.Context(), l.request(0))
			}
			status := l.status(t)
			assertBase(t, status, notes, 1, pHash)
			plan := findPlan(t, status, 2)
			assertFile(t, l.a, notes, q, qHash)
			if point == "tampered-incoming" {
				if !errors.Is(err, ErrHash) || errors.Is(err, ErrConflict) || plan.Status != "rejected" {
					t.Fatalf("want rejected hash: %v %+v", err, plan)
				}
				assertFile(t, l.b, notes, p, pHash)
				assertFile(t, l.stage(""), plan.Incoming(), r, rHash)
			} else {
				if !errors.Is(err, ErrConflict) || plan.Status != "conflict" || !plan.Saved || plan.Local != rHash {
					t.Fatalf("want saved conflict: %v %+v", err, plan)
				}
				assertFile(t, l.b, notes, r, rHash)
				assertFile(t, l.stage(""), plan.Incoming(), q, qHash)
				assertFile(t, l.stage(""), plan.LocalCopy(), r, rHash)
				if point == "before-final-check" {
					assertFile(t, l.stage(""), plan.Backup(), p, pHash)
				}
				if err := Recover(t.Context(), l.request(0)); !errors.Is(err, ErrConflict) {
					t.Fatalf("conflict was not held: %v", err)
				}
			}
		})
	}
}

func TestP1_04_InvalidNamesDoNotApply(t *testing.T) {
	longPath := strings.Repeat(strings.Repeat("a", 255)+"/", 4) + "z"
	bad := map[string]string{
		"absolute": "/outside", "dot": "a/./b", "parent": "a/../b", "empty-element": "a//b",
		"trailing-slash": "a/", "empty": "", "nul": "a\x00b", "backslash": "a\\b", "non-utf8": "a\xff",
		"control": "bad\u0085name", "reserved": "a/.file-sync", "element-limit": strings.Repeat("a", 256),
		"path-limit": longPath, "depth-limit": strings.Repeat("a/", 32) + "b",
	}
	for label, path := range bad {
		t.Run(label, func(t *testing.T) {
			l := newLab(t)
			p, pHash := fixture(t, "P")
			writeAt(t, l.a, notes, p)
			req := l.request(1)
			req.Path = path
			if err := safefs.Validate(path); !errors.Is(err, safefs.ErrPath) {
				t.Fatalf("want invalid path: %q: %v", path, err)
			}
			switch label {
			case "backslash", "non-utf8", "control", "reserved", "path-limit", "depth-limit":
				writeAt(t, l.a, path, p)
				if err := Copy(t.Context(), l.request(1)); !errors.Is(err, safefs.ErrPath) {
					t.Fatalf("want invalid source scan: %v", err)
				}
				assertFile(t, l.a, path, p, pHash)
			default:
				if path != "" {
					if err := Copy(t.Context(), req); !errors.Is(err, safefs.ErrPath) {
						t.Fatalf("want invalid requested path: %v", err)
					}
				}
			}
			assertUntouched(t, l)
			assertFile(t, l.a, notes, p, pHash)
		})
	}
}

func TestP1_05_LinksAndSpecialFiles(t *testing.T) {
	for _, name := range []string{"external-symlink", "internal-symlink", "symlink-parent", "hard-link", "fifo", "socket"} {
		t.Run(name, func(t *testing.T) {
			l := newLab(t)
			p, pHash := fixture(t, "P")
			r, rHash := fixture(t, "R")
			writeAt(t, l.a, notes, p)
			outside := filepath.Join(l.volume, "outside")
			writeAt(t, outside, "sentinel", r)
			trap := filepath.Join(l.a, "trap")
			want := error(safefs.ErrType)
			switch name {
			case "external-symlink":
				must(t, os.Symlink(filepath.Join(outside, "sentinel"), trap))
				want = unix.ELOOP
			case "internal-symlink":
				must(t, os.Symlink(notes, trap))
				want = unix.ELOOP
			case "symlink-parent":
				must(t, os.Symlink(outside, trap))
				want = unix.ELOOP
			case "hard-link":
				must(t, os.Link(filepath.Join(outside, "sentinel"), trap))
			case "fifo":
				must(t, unix.Mkfifo(trap, 0600))
			case "socket":
				// Linux limits sockaddr_un.sun_path to 108 bytes. Procfs makes the
				// pathname independent of the length of the test's temp directory.
				dir, err := os.Open(l.a)
				must(t, err)
				defer dir.Close()
				fd, err := unix.Socket(unix.AF_UNIX, unix.SOCK_STREAM|unix.SOCK_CLOEXEC, 0)
				must(t, err)
				defer unix.Close(fd)
				address := &unix.SockaddrUnix{Name: fmt.Sprintf("/proc/self/fd/%d/trap", dir.Fd())}
				must(t, unix.Bind(fd, address))
				must(t, unix.Listen(fd, 1))
			}
			before, err := os.Lstat(trap)
			must(t, err)
			if err := Copy(t.Context(), l.request(1)); !errors.Is(err, want) {
				t.Fatalf("expected %v: got %v", want, err)
			}
			after, err := os.Lstat(trap)
			must(t, err)
			if !os.SameFile(before, after) {
				t.Fatal("special input changed")
			}
			assertUntouched(t, l)
			assertFile(t, outside, "sentinel", r, rHash)
			assertFile(t, l.a, notes, p, pHash)
		})
	}
}

func TestP1_05_IsolatedMountAndDevice(t *testing.T) {
	for _, name := range []string{"device", "bind-mount"} {
		t.Run(name, func(t *testing.T) {
			if os.Getenv("FILE_SYNC_ISOLATED_TESTS") != "1" {
				t.Skip("requires an isolated non-initial user and private mount namespace")
			}
			uidMap, err := os.ReadFile("/proc/self/uid_map")
			must(t, err)
			fields := strings.Fields(string(uidMap))
			if len(fields) != 3 || fields[0] != "0" || fields[1] == "0" || fields[2] != "1" {
				t.Fatal("refusing to create OS samples without an isolated user mapping")
			}
			l := newLab(t)
			p, pHash := fixture(t, "P")
			r, rHash := fixture(t, "R")
			writeAt(t, l.a, notes, p)
			outside := filepath.Join(l.volume, "outside")
			writeAt(t, outside, "sentinel", r)
			trap := filepath.Join(l.a, "trap")
			want := error(safefs.ErrType)
			if name == "device" {
				err = unix.Mknod(trap, unix.S_IFCHR|0600, int(unix.Mkdev(1, 3)))
			} else {
				must(t, os.Mkdir(trap, 0700))
				err = unix.Mount(outside, trap, "", unix.MS_BIND, "")
				if err == nil {
					defer func() { must(t, unix.Unmount(trap, 0)) }()
				}
				want = unix.EXDEV
			}
			if errors.Is(err, unix.EPERM) || errors.Is(err, unix.EACCES) {
				t.Skipf("namespace cannot create %s: %v", name, err)
			}
			must(t, err)
			if err := Copy(t.Context(), l.request(1)); !errors.Is(err, want) {
				t.Fatalf("expected %v: got %v", want, err)
			}
			assertUntouched(t, l)
			assertFile(t, outside, "sentinel", r, rHash)
			assertFile(t, l.a, notes, p, pHash)
		})
	}
}

func TestP1_06_ValidNames(t *testing.T) {
	l := newLab(t)
	expected := map[string]string{
		strings.Repeat("é", 127) + "a": "P",
		strings.Repeat(strings.Repeat("b", 255)+"/", 3) + strings.Repeat("c", 254) + "/d": "P",
		strings.Repeat("d/", 31) + "last":                                                 "P",
		"A.txt":                                                                           "P", "a.txt": "Q", "é.txt": "R", "e\u0301.txt": "S",
	}
	for path, id := range expected {
		data, _ := fixture(t, id)
		writeAt(t, l.a, path, data)
	}
	must(t, Copy(t.Context(), l.request(1)))
	assertCompleted(t, l, expected, 1)
}

func TestP1_07_Resources(t *testing.T) {
	for _, id := range []string{"X", "M", "N"} {
		t.Run(id, func(t *testing.T) {
			l := newLab(t)
			data, digest := fixture(t, id)
			writeAt(t, l.a, notes, data)
			err := Copy(t.Context(), l.request(1))
			if id == "N" {
				if !errors.Is(err, safefs.ErrLimit) {
					t.Fatalf("want size refusal: %v", err)
				}
				assertUntouched(t, l)
				assertFile(t, l.a, notes, data, digest)
				return
			}
			must(t, err)
			assertCompleted(t, l, map[string]string{notes: id}, 1)
		})
	}
	for _, count := range []int{999, 1000, 1001} {
		t.Run(fmt.Sprint(count), func(t *testing.T) {
			l := newLab(t)
			data, digest := fixture(t, "E")
			expected := make(map[string]string)
			for i := 0; i < count; i++ {
				name := fmt.Sprintf("%04d.txt", i)
				writeAt(t, l.a, name, data)
				expected[name] = "E"
			}
			err := Copy(t.Context(), l.request(1))
			if count > 1000 {
				if !errors.Is(err, safefs.ErrLimit) {
					t.Fatalf("want count refusal: %v", err)
				}
				assertUntouched(t, l)
				for name := range expected {
					assertFile(t, l.a, name, data, digest)
				}
				return
			}
			must(t, err)
			assertCompleted(t, l, expected, 1)
		})
	}
}

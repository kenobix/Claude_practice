//go:build linux

// kvbench measures the real synchronous engine using disposable artificial data.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"runtime"
	"runtime/debug"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/engine"
	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

type config struct {
	keys                       []int
	valueBytes, opens, updates int
	parent                     string
}
type openSample struct {
	Nanoseconds int64  `json:"nanoseconds"`
	HeapBefore  uint64 `json:"heap_before_bytes"`
	HeapLive    uint64 `json:"heap_live_bytes"`
	HeapDelta   int64  `json:"heap_delta_bytes"`
}
type caseResult struct {
	Keys            int          `json:"keys"`
	InitialLogBytes int64        `json:"initial_log_bytes"`
	FinalLogBytes   int64        `json:"final_log_bytes"`
	Opens           []openSample `json:"opens"`
	UpdateNS        []int64      `json:"update_nanoseconds"`
	Verified        bool         `json:"verified"`
}
type environment struct {
	Go         string `json:"go"`
	OS         string `json:"os"`
	Arch       string `json:"arch"`
	Kernel     string `json:"kernel"`
	CPU        string `json:"cpu"`
	Filesystem string `json:"filesystem_type"`
	Revision   string `json:"source_revision"`
	BuildDirty bool   `json:"build_dirty"`
	CPUs       int    `json:"logical_cpus"`
	GOMAXPROCS int    `json:"gomaxprocs"`
	GOGC       string `json:"gogc_env"`
	GOMEMLIMIT string `json:"gomemlimit_env"`
}

type report struct {
	Schema      int          `json:"schema_version"`
	Started     string       `json:"started_utc"`
	Environment environment  `json:"environment"`
	ValueBytes  int          `json:"value_bytes"`
	KeyBytes    int          `json:"key_bytes"`
	Sync        string       `json:"sync"`
	Cache       string       `json:"cache"`
	Cases       []caseResult `json:"cases"`
}

func parse(args []string) (config, error) {
	c := config{}
	f := flag.NewFlagSet("kvbench", flag.ContinueOnError)
	f.SetOutput(io.Discard)
	keys := f.String("keys", "1000,10000,100000", "comma-separated key counts")
	f.StringVar(&c.parent, "work-dir", "", "existing disposable-data parent directory (required)")
	f.IntVar(&c.valueBytes, "value-bytes", 128, "value bytes")
	f.IntVar(&c.opens, "open-samples", 3, "warm Open samples")
	f.IntVar(&c.updates, "update-samples", 100, "synchronous overwrite samples")
	if err := f.Parse(args); err != nil {
		return c, err
	}
	if f.NArg() != 0 || c.parent == "" {
		return c, errors.New("--work-dir required; positional arguments rejected")
	}
	seen := map[int]bool{}
	for _, s := range strings.Split(*keys, ",") {
		n, err := strconv.Atoi(s)
		if err != nil || n < 1 || n > 1000000 || seen[n] {
			return c, fmt.Errorf("invalid or duplicate keys: %q", s)
		}
		seen[n] = true
		c.keys = append(c.keys, n)
	}
	if len(c.keys) > 10 || c.valueBytes < 0 || c.valueBytes > record.MaxValueSize || c.opens < 1 || c.opens > 100 || c.updates < 1 || c.updates > 10000 {
		return c, errors.New("out of range: keys cases<=10, value 0..1MiB, opens 1..100, updates 1..10000")
	}
	for _, n := range c.keys {
		if logSize(n+c.updates, c.valueBytes) > record.MaxLogSize {
			return c, errors.New("planned log exceeds 256 MiB")
		}
	}
	return c, nil
}

// Keys are always 16 ASCII bytes, including at the maximum accepted count.
func key(i int) string { return fmt.Sprintf("key-%012d", i) }
func logSize(records, valueBytes int) int64 {
	return record.FileHeaderSize + int64(records)*int64(record.HeaderSize+16+valueBytes+4)
}
func metadata(parent string) (environment, error) {
	var fs syscall.Statfs_t
	if err := syscall.Statfs(parent, &fs); err != nil {
		return environment{}, err
	}
	kernel, err := os.ReadFile("/proc/sys/kernel/osrelease")
	if err != nil {
		return environment{}, err
	}
	cpu, err := os.ReadFile("/proc/cpuinfo")
	if err != nil {
		return environment{}, err
	}
	model := "unknown"
	for _, line := range strings.Split(string(cpu), "\n") {
		if name, v, ok := strings.Cut(line, ":"); ok && strings.TrimSpace(name) == "model name" {
			model = strings.TrimSpace(v)
			break
		}
	}
	e := environment{Go: runtime.Version(), OS: runtime.GOOS, Arch: runtime.GOARCH, Kernel: strings.TrimSpace(string(kernel)), CPU: model, Filesystem: fmt.Sprintf("0x%x", fs.Type), CPUs: runtime.NumCPU(), GOMAXPROCS: runtime.GOMAXPROCS(0), GOGC: os.Getenv("GOGC"), GOMEMLIMIT: os.Getenv("GOMEMLIMIT")}
	if b, ok := debug.ReadBuildInfo(); ok {
		for _, s := range b.Settings {
			switch s.Key {
			case "vcs.revision":
				e.Revision = s.Value
			case "vcs.modified":
				e.BuildDirty = s.Value == "true"
			}
		}
	}
	return e, nil
}
func populate(dir string, n, valueBytes int) (err error) {
	db, err := engine.Open(dir)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, db.Close()) }()
	v := bytes.Repeat([]byte{0x5a}, valueBytes)
	for i := 0; i < n; i++ {
		if seq, e := db.Put(key(i), v); e != nil {
			return e
		} else if seq != uint64(i+1) {
			return errors.New("populate sequence mismatch")
		}
	}
	return nil
}
func verify(db *engine.DB, n, updates, valueBytes int) error {
	r, err := db.Inspect()
	if err != nil {
		return err
	}
	if r.Status != "ok" || r.LiveKeys != n || r.RecordCount != uint64(n+updates) || r.LatestSequence != uint64(n+updates) || r.DeleteCount != 0 || r.PutCount != uint64(n+updates) || r.ValidBytes != r.LogBytes || r.LogBytes != logSize(n+updates, valueBytes) {
		return fmt.Errorf("report mismatch: %+v", r)
	}
	for i := 0; i < n; i++ {
		b := byte(0x5a)
		if i < updates {
			b = 0xa5
		}
		want := bytes.Repeat([]byte{b}, valueBytes)
		got, e := db.Get(key(i))
		if e != nil {
			return e
		}
		if !bytes.Equal(got, want) {
			return fmt.Errorf("value mismatch at key %d", i)
		}
	}
	return nil
}
func sampleOpen(dir string, n, valueBytes int) (s openSample, err error) {
	runtime.GC()
	var before, live runtime.MemStats
	runtime.ReadMemStats(&before)
	start := time.Now()
	db, err := engine.OpenWithOptions(dir, engine.Options{})
	elapsed := time.Since(start)
	if err != nil {
		return s, err
	}
	defer func() { err = errors.Join(err, db.Close()) }()
	// GC and correctness checks are outside the timed Open interval.
	runtime.GC()
	runtime.ReadMemStats(&live)
	runtime.KeepAlive(db)
	s = openSample{elapsed.Nanoseconds(), before.HeapAlloc, live.HeapAlloc, int64(live.HeapAlloc) - int64(before.HeapAlloc)}
	return s, verify(db, n, 0, valueBytes)
}
func overwrite(dir string, n, samples, valueBytes int) (times []int64, err error) {
	db, err := engine.OpenWithOptions(dir, engine.Options{})
	if err != nil {
		return nil, err
	}
	defer func() { err = errors.Join(err, db.Close()) }()
	v := bytes.Repeat([]byte{0xa5}, valueBytes)
	for i := 0; i < samples; i++ {
		k := key(i % n)
		start := time.Now()
		seq, e := db.Put(k, v)
		elapsed := time.Since(start)
		if e != nil {
			return nil, e
		}
		if seq != uint64(n+i+1) {
			return nil, errors.New("overwrite sequence mismatch")
		}
		times = append(times, elapsed.Nanoseconds())
	}
	return times, nil
}
func verifyReopen(dir string, n, updates, valueBytes int) (err error) {
	db, err := engine.OpenWithOptions(dir, engine.Options{})
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, db.Close()) }()
	return verify(db, n, updates, valueBytes)
}
func measure(c config, n int) (r caseResult, err error) {
	dir, err := os.MkdirTemp(c.parent, "kvbench-")
	if err != nil {
		return r, err
	}
	// Remove only the unique directory created by this call; never open caller data.
	defer func() { err = errors.Join(err, os.RemoveAll(dir)) }()
	if err = populate(dir, n, c.valueBytes); err != nil {
		return r, err
	}
	r = caseResult{Keys: n, InitialLogBytes: logSize(n, c.valueBytes), FinalLogBytes: logSize(n+c.updates, c.valueBytes)}
	for i := 0; i < c.opens; i++ {
		s, e := sampleOpen(dir, n, c.valueBytes)
		if e != nil {
			return r, e
		}
		r.Opens = append(r.Opens, s)
	}
	r.UpdateNS, err = overwrite(dir, n, c.updates, c.valueBytes)
	if err != nil {
		return r, err
	}
	if err = verifyReopen(dir, n, c.updates, c.valueBytes); err != nil {
		return r, err
	}
	r.Verified = true
	return r, nil
}
func writeOutput(out io.Writer, b []byte) error {
	n, err := out.Write(b)
	if err == nil && n != len(b) {
		return io.ErrShortWrite
	}
	return err
}
func run(args []string, out, errOut io.Writer) int {
	c, err := parse(args)
	if errors.Is(err, flag.ErrHelp) {
		err = writeOutput(out, []byte("kvbench --work-dir DIR [--keys 1000,10000,100000] [--value-bytes 128] [--open-samples 3] [--update-samples 100]\nUses private temporary DBs; synchronous PUT; warm same-process Open; JSON stdout.\n"))
		if err != nil {
			fmt.Fprintln(errOut, err)
			return 1
		}
		return 0
	}
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 2
	}
	env, err := metadata(c.parent)
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 1
	}
	r := report{Schema: 1, Started: time.Now().UTC().Format(time.RFC3339), Environment: env, ValueBytes: c.valueBytes, KeyBytes: 16, Sync: "File.Sync per PUT; Open includes DB/parent directory Sync", Cache: "same-process reopen; OS page cache retained; no cache reset"}
	for _, n := range c.keys {
		item, e := measure(c, n)
		if e != nil {
			fmt.Fprintln(errOut, e)
			return 1
		}
		r.Cases = append(r.Cases, item)
	}
	encoded, err := json.Marshal(r)
	if err == nil {
		encoded = append(encoded, '\n')
		err = writeOutput(out, encoded)
	}
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 1
	}
	return 0
}
func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

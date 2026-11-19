//go:build linux

package engine

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"sync"
	"syscall"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/recovery"
)

var (
	ErrLocked           = errors.New("database locked")
	ErrLogFull          = errors.New("log exceeds 256 MiB")
	ErrRecoveryRequired = errors.New("database requires reopen and recovery")
)

type Options struct {
	// Create permits creating data.log in an existing directory. Existing logs are never reset.
	Create bool
	// Diagnostics receives the pre-truncation recovery event. Nil uses os.Stderr.
	Diagnostics io.Writer
}

type logFile interface {
	io.ReaderAt
	io.WriterAt
	Stat() (os.FileInfo, error)
	Sync() error
	Truncate(int64) error
	Close() error
}

type diskOps struct {
	wrap    func(*os.File) logFile
	syncDir func(string) error
}

func defaultOps() diskOps { return diskOps{func(f *os.File) logFile { return f }, syncDirectory} }
func syncDirectory(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	return errors.Join(f.Sync(), f.Close())
}

// DB owns a Linux advisory lock and a single synchronous append log. Use Open.
type DB struct {
	mu     sync.Mutex
	file   logFile
	lock   *os.File
	state  recovery.Result
	failed bool
	closed bool
}

func Open(dir string) (*DB, error) { return OpenWithOptions(dir, Options{Create: true}) }
func OpenWithOptions(dir string, options Options) (*DB, error) {
	return openDisk(dir, options, defaultOps())
}

func resolveDBDirectory(dir string) (string, error) {
	if dir == "" {
		return "", &os.PathError{Op: "stat", Path: dir, Err: syscall.ENOENT}
	}
	if !filepath.IsAbs(dir) {
		cwd, err := os.Getwd()
		if err != nil {
			return "", err
		}
		// Resolve symlink components before collapsing "..".
		dir = cwd + string(filepath.Separator) + dir
	}
	return filepath.EvalSymlinks(dir)
}

func lockDirectory(dir string) (*os.File, error) {
	info, err := os.Stat(dir)
	if err != nil {
		return nil, err
	}
	if !info.IsDir() {
		return nil, fmt.Errorf("not a directory: %s", dir)
	}
	lock, err := os.OpenFile(filepath.Join(dir, "LOCK"), os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0600)
	if err != nil {
		return nil, err
	}
	lockInfo, statErr := lock.Stat()
	if statErr != nil {
		return nil, errors.Join(statErr, lock.Close())
	}
	if !lockInfo.Mode().IsRegular() {
		return nil, errors.Join(fmt.Errorf("LOCK is not a regular file"), lock.Close())
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		closeErr := lock.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) || errors.Is(err, syscall.EAGAIN) {
			err = ErrLocked
		}
		return nil, errors.Join(err, closeErr)
	}
	return lock, nil
}

func openDisk(dir string, options Options, ops diskOps) (db *DB, err error) {
	dir, err = resolveDBDirectory(dir)
	if err != nil {
		return nil, err
	}
	lock, err := lockDirectory(dir)
	if err != nil {
		return nil, err
	}
	var file logFile
	defer func() {
		if err != nil {
			if file != nil {
				err = errors.Join(err, file.Close())
			}
			err = errors.Join(err, lock.Close())
		}
	}()
	path := filepath.Join(dir, "data.log")
	raw, err := os.OpenFile(path, os.O_RDWR|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	created := false
	if errors.Is(err, os.ErrNotExist) && options.Create {
		entries, readErr := os.ReadDir(dir)
		if readErr != nil {
			return nil, readErr
		}
		for _, entry := range entries {
			if entry.Name() != "LOCK" {
				return nil, fmt.Errorf("new DB requires an empty directory (apart from LOCK)")
			}
		}
		raw, err = os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_RDWR|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0600)
		created = err == nil
	}
	if err != nil {
		return nil, err
	}
	file = ops.wrap(raw)
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, fmt.Errorf("data.log is not a regular file")
	}
	if created {
		if err = writeAllAt(file, record.FileHeader(), 0); err != nil {
			return nil, err
		}
		if err = file.Sync(); err != nil {
			return nil, err
		}
	}
	// Even a previous incomplete initialization may have left unsynced names.
	if err = ops.syncDir(dir); err != nil {
		return nil, err
	}
	if err = ops.syncDir(filepath.Dir(dir)); err != nil {
		return nil, err
	}
	info, err = file.Stat()
	if err != nil {
		return nil, err
	}
	state, scanErr := recovery.Scan(file, info.Size())
	if scanErr != nil {
		if !errors.Is(scanErr, record.ErrIncomplete) {
			return nil, scanErr
		}
		diagnostic := options.Diagnostics
		if diagnostic == nil {
			diagnostic = os.Stderr
		}
		event := struct {
			Event           string `json:"event"`
			OldSize         int64  `json:"old_size"`
			LastValidOffset int64  `json:"last_valid_offset"`
			Reason          string `json:"reason"`
		}{"tail_truncate_planned", info.Size(), state.Report.ValidBytes, scanErr.Error()}
		encoded, encodeErr := json.Marshal(event)
		if encodeErr != nil {
			return nil, fmt.Errorf("recovery diagnostic: %w", encodeErr)
		}
		encoded = append(encoded, '\n')
		n, writeErr := diagnostic.Write(encoded)
		if writeErr == nil && n != len(encoded) {
			writeErr = io.ErrShortWrite
		}
		if writeErr != nil {
			return nil, fmt.Errorf("recovery diagnostic: %w", writeErr)
		}
		if err = file.Truncate(state.Report.ValidBytes); err != nil {
			return nil, err
		}
		if err = file.Sync(); err != nil {
			return nil, err
		}
		state.Report.LogBytes = state.Report.ValidBytes
		state.Report.Status = "ok"
		state.Report.InvalidOffset = nil
		state.Report.Reason = nil
	}
	return &DB{file: file, lock: lock, state: state}, nil
}

func writeAllAt(file io.WriterAt, data []byte, offset int64) error {
	for len(data) > 0 {
		n, err := file.WriteAt(data, offset)
		if n < 0 || n > len(data) {
			return io.ErrShortWrite
		}
		if err != nil {
			return err
		}
		if n == 0 {
			return io.ErrShortWrite
		}
		offset += int64(n)
		data = data[n:]
	}
	return nil
}

func (db *DB) ready() error {
	if db.closed {
		return ErrClosed
	}
	if db.failed {
		return ErrRecoveryRequired
	}
	return nil
}
func (db *DB) Put(key string, value []byte) (uint64, error) { return db.update(record.Put, key, value) }
func (db *DB) Delete(key string) (uint64, error)            { return db.update(record.Delete, key, nil) }
func (db *DB) update(op record.Operation, key string, value []byte) (uint64, error) {
	db.mu.Lock()
	defer db.mu.Unlock()
	if err := db.ready(); err != nil {
		return 0, err
	}
	if err := record.ValidateKey(key); err != nil {
		return 0, err
	}
	if err := record.ValidateValue(value); err != nil {
		return 0, err
	}
	report := &db.state.Report
	if report.LatestSequence == math.MaxUint64 {
		return 0, ErrSequenceExhausted
	}
	size := int64(record.HeaderSize + len(key) + len(value) + 4)
	if size > record.MaxLogSize-report.LogBytes {
		return 0, ErrLogFull
	}
	seq := report.LatestSequence + 1
	encoded, err := record.Encode(record.Record{Sequence: seq, Op: op, Key: key, Value: value})
	if err != nil {
		return 0, err
	}
	offset := report.LogBytes
	if err = writeAllAt(db.file, encoded, offset); err == nil {
		err = db.file.Sync()
	}
	if err != nil {
		db.failed = true
		return 0, fmt.Errorf("append/sync: %w", err)
	}
	if op == record.Put {
		db.state.Index[key] = recovery.Position{Offset: offset, Sequence: seq}
		report.PutCount++
	} else {
		delete(db.state.Index, key)
		report.DeleteCount++
	}
	report.RecordCount++
	report.LatestSequence = seq
	report.LogBytes += size
	report.ValidBytes = report.LogBytes
	report.LiveKeys = len(db.state.Index)
	return seq, nil
}

func (db *DB) Get(key string) ([]byte, error) {
	db.mu.Lock()
	defer db.mu.Unlock()
	if err := db.ready(); err != nil {
		return nil, err
	}
	if err := record.ValidateKey(key); err != nil {
		return nil, err
	}
	position, ok := db.state.Index[key]
	if !ok {
		return nil, ErrNotFound
	}
	r, err := record.Decode(io.NewSectionReader(db.file, position.Offset, db.state.Report.LogBytes-position.Offset), position.Sequence)
	if err == nil && (r.Key != key || r.Op != record.Put) {
		err = fmt.Errorf("%w: index mismatch", record.ErrCorrupt)
	}
	if err != nil {
		db.failed = true
		return nil, err
	}
	return append([]byte{}, r.Value...), nil
}
func (db *DB) Inspect() (recovery.Report, error) {
	db.mu.Lock()
	defer db.mu.Unlock()
	if err := db.ready(); err != nil {
		return recovery.Report{}, err
	}
	return db.state.Report, nil
}
func (db *DB) Close() error {
	db.mu.Lock()
	defer db.mu.Unlock()
	if db.closed {
		return nil
	}
	db.closed = true
	// Keep the lock until after data.log is closed; never unlink LOCK.
	return errors.Join(db.file.Close(), db.lock.Close())
}

// Inspect validates an existing log without repairing it. It still excludes all writers.
func Inspect(dir string) (recovery.Report, error) {
	return inspectDisk(dir, defaultOps().wrap)
}

func inspectDisk(dir string, wrap func(*os.File) logFile) (report recovery.Report, err error) {
	dir, err = resolveDBDirectory(dir)
	if err != nil {
		return report, err
	}
	lock, err := lockDirectory(dir)
	if err != nil {
		return report, err
	}
	defer func() { err = errors.Join(err, lock.Close()) }()
	raw, err := os.OpenFile(filepath.Join(dir, "data.log"), os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return report, err
	}
	file := wrap(raw)
	defer func() { err = errors.Join(err, file.Close()) }()
	info, err := file.Stat()
	if err != nil {
		return report, err
	}
	if !info.Mode().IsRegular() {
		return report, fmt.Errorf("data.log is not a regular file")
	}
	result, err := recovery.Scan(file, info.Size())
	return result.Report, err
}

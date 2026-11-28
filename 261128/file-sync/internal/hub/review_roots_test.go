package hub

import (
	"bytes"
	"os"
	"path/filepath"
	"sync"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/local"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func denyManagement(t *testing.T, root string, err error) {
	t.Helper()
	if err == nil {
		t.Fatal("overlapping setup succeeded")
	}
	for _, path := range []string{safefs.Management, filepath.Join(safefs.Management, "state", safefs.StateDB)} {
		if _, err := os.Lstat(filepath.Join(root, path)); !os.IsNotExist(err) {
			t.Fatalf("new management entry %s: %v", path, err)
		}
	}
}

func assertManagedDatabase(t *testing.T, root string, want []byte) {
	t.Helper()
	data := readManagedDatabase(t, root)
	if !bytes.Equal(data, want) || protocol.Digest(data) != protocol.Digest(want) {
		t.Fatal("previous state database was changed")
	}
}

func readManagedDatabase(t *testing.T, root string) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(root, safefs.Management, "state", safefs.StateDB))
	must(t, err)
	return data
}

func TestReviewOverlappingRootInitialization(t *testing.T) {
	for _, scenario := range []string{"parent_first", "child_first", "client_then_hub", "hub_then_client"} {
		t.Run(scenario, func(t *testing.T) {
			volume := t.TempDir()
			outer := filepath.Join(volume, "A")
			inner := filepath.Join(outer, "nested")
			p, expected := sample(t, "P")
			hubFirst := scenario == "hub_then_client"
			if hubFirst {
				must(t, os.Mkdir(outer, 0700))
				must(t, Init(t.Context(), volume, "A", testProvision()))
			}
			must(t, os.MkdirAll(inner, 0700))
			// A hub-init candidate must be empty so that its ancestor is the
			// deciding factor. Use the sentinel outside that candidate.
			sentinel := filepath.Join(inner, "sample")
			if scenario == "client_then_hub" || hubFirst {
				sentinel = filepath.Join(volume, "sample")
			}
			must(t, os.WriteFile(sentinel, p, 0600))
			existing := outer
			switch scenario {
			case "child_first":
				existing = inner
				must(t, local.Init(t.Context(), outer, "nested"))
			case "parent_first", "client_then_hub":
				must(t, local.Init(t.Context(), volume, "A"))
			}
			oldDB := readManagedDatabase(t, existing)
			switch scenario {
			case "child_first":
				denyManagement(t, outer, local.Init(t.Context(), volume, "A"))
				denyManagement(t, outer, Init(t.Context(), volume, "A", testProvision()))
			case "client_then_hub":
				denyManagement(t, inner, Init(t.Context(), outer, "nested", testProvision()))
			default:
				// The selected volume is itself the managed ancestor.
				denyManagement(t, inner, local.Init(t.Context(), outer, "nested"))
			}

			data, err := os.ReadFile(sentinel)
			must(t, err)
			if !bytes.Equal(data, p) || protocol.Digest(data) != expected {
				t.Fatal("sentinel changed")
			}
			assertManagedDatabase(t, existing, oldDB)
			if scenario == "parent_first" {
				entries, err := local.Scan(volume, "A")
				must(t, err)
				if len(entries) != 1 || entries[0].Path != "nested/sample" || entries[0].Digest != expected {
					t.Fatalf("existing root changed: %+v", entries)
				}
			}
		})
	}
}

func TestReviewSiblingRootsAndConcurrentOverlap(t *testing.T) {
	t.Run("siblings_with_common_prefix", func(t *testing.T) {
		volume := t.TempDir()
		for _, name := range []string{"A", "AB", "hub"} {
			must(t, os.Mkdir(filepath.Join(volume, name), 0700))
		}
		must(t, local.Init(t.Context(), volume, "A"))
		must(t, local.Init(t.Context(), volume, "AB"))
		must(t, Init(t.Context(), volume, "hub", testProvision()))
		for _, name := range []string{"A", "AB"} {
			_, err := local.Scan(volume, name)
			must(t, err)
		}
	})
	t.Run("parent_and_child_setup_race", func(t *testing.T) {
		for i := 0; i < 12; i++ {
			volume := t.TempDir()
			outer := filepath.Join(volume, "A")
			inner := filepath.Join(outer, "nested")
			must(t, os.MkdirAll(inner, 0700))
			start := make(chan struct{})
			var wg sync.WaitGroup
			wg.Add(2)
			var parentErr, childErr error
			go func() {
				defer wg.Done()
				<-start
				parentErr = local.Init(t.Context(), volume, "A")
			}()
			go func() {
				defer wg.Done()
				<-start
				childErr = local.Init(t.Context(), outer, "nested")
			}()
			close(start)
			wg.Wait()
			if parentErr == nil && childErr == nil {
				t.Fatal("both overlapping roots initialized")
			}
			if parentErr != nil {
				denyManagement(t, outer, parentErr)
			}
			if childErr != nil {
				denyManagement(t, inner, childErr)
			}
		}
	})
	t.Run("child_reservation_blocks_parent_before_first_write", func(t *testing.T) {
		volume := t.TempDir()
		outer := filepath.Join(volume, "A")
		inner := filepath.Join(outer, "nested")
		must(t, os.MkdirAll(inner, 0700))
		v, err := safefs.OpenVolume(outer)
		must(t, err)
		defer v.Close()
		child, err := v.LockRootForSetup("nested")
		must(t, err)
		defer child.Close()
		denyManagement(t, outer, local.Init(t.Context(), volume, "A"))
	})
}

func TestReviewOfflineAndReconcileDestinations(t *testing.T) {
	l := newLab(t)
	child := filepath.Join(l.dir, "A", "nested")
	must(t, os.Mkdir(child, 0700))
	l.write(t, "A", "nested/sample", "P")
	old := readManagedDatabase(t, filepath.Join(l.dir, "A"))
	req := l.req(t, "A", 0)
	req.Root = "A/nested"
	_, err := local.Reconcile(t.Context(), req, "")
	denyManagement(t, child, err)
	l.stop()
	_, err = Backup(t.Context(), l.dir, "hub", "backup")
	must(t, err)
	for _, name := range []string{"A/new", "A/nested/new"} {
		target := filepath.Join(l.dir, name)
		_, err := Restore(t.Context(), l.dir, "backup", name, l.provision)
		denyManagement(t, target, err)
		if _, err := os.Lstat(target); !os.IsNotExist(err) {
			t.Fatalf("restore published nested target: %v", err)
		}
	}
	_, err = Backup(t.Context(), l.dir, "hub", "A/new")
	if err == nil {
		t.Fatal("backup created work inside a managed client")
	}
	_, err = Restore(t.Context(), l.dir, "backup", "restored", l.provision)
	must(t, err)
	h, err := Open(t.Context(), l.dir, "restored")
	must(t, err)
	must(t, h.Close())
	l.assertBody(t, "A/nested/sample", "P")
	assertManagedDatabase(t, filepath.Join(l.dir, "A"), old)
	entries, err := os.ReadDir(child)
	must(t, err)
	if len(entries) != 1 || entries[0].Name() != "sample" {
		t.Fatalf("unexpected preparation entries: %v", entries)
	}
}

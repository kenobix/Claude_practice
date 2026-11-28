package hub

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/local"
)

func TestReviewStructuralHoldPreservesEntireBlockedGroup(t *testing.T) {
	l := newLab(t)
	g := l.manifest(t).Generation
	a, b := l.req(t, "A", 0), l.req(t, "B", 1)
	must(t, os.Mkdir(filepath.Join(l.dir, "A", "x"), 0700))
	l.write(t, "A", "x/old", "P")
	l.write(t, "A", "separate", "P")
	must(t, local.Register(t.Context(), a, "seed"))
	must(t, local.Register(t.Context(), b, "pull"))
	l.write(t, "B", "x/local", "R")
	must(t, os.Remove(filepath.Join(l.dir, "A", "x/old")))
	must(t, os.Remove(filepath.Join(l.dir, "A", "x")))
	must(t, local.Sync(t.Context(), a))
	l.write(t, "A", "x", "Q")
	l.write(t, "A", "separate", "S")
	must(t, local.Sync(t.Context(), a))

	l.pending(t, b)
	l.file(t, "B", "x/old", "P", 1)
	l.file(t, "B", "x/local", "R", 0)
	l.file(t, "B", "separate", "S", 2)
	l.assertBase(t, "B", "x", "", 0)
	status := l.status(t, "B")
	for _, path := range []string{"x", "x/old", "x/local"} {
		if status.Holds[path] != "namespace_conflict" {
			t.Fatalf("missing hold for %q: %v", path, status.Holds)
		}
	}
	for _, plan := range status.Plans {
		if plan.Path == "x" || plan.Path == "x/local" || (plan.Path == "x/old" && plan.Target.Revision != 1) {
			t.Fatalf("unstarted group was mutated: %+v", plan)
		}
	}
	if len(status.Outbox) != 0 {
		t.Fatalf("unexpected outbox: %+v", status.Outbox)
	}
	stage, err := os.ReadDir(filepath.Join(l.dir, "B", ".file-sync", "stage"))
	must(t, err)
	for _, f := range stage {
		if strings.HasSuffix(f.Name(), ".trash") {
			t.Fatalf("unexpected deletion backup: %s", f.Name())
		}
	}
	l.assertHistory(t, g, "client-a:separate:1:applied", "client-a:x/old:1:applied", "client-a:x/old:2:applied",
		"client-a:separate:2:applied", "client-a:x:1:applied")
}

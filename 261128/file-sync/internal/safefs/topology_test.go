package safefs

import (
	"bufio"
	"strings"
	"testing"
)

func TestReviewSelectedVolumeMountAncestry(t *testing.T) {
	const listing = `31 20 8:48 / / rw - ext4 /dev/example rw
32 31 8:48 /data /data rw - ext4 /dev/example rw
33 32 8:48 /data/tmp /data/tmp rw - ext4 /dev/example rw
34 31 8:48 /data/hidden /elsewhere rw - ext4 /dev/example rw
35 34 8:48 /data/hidden/tmp /elsewhere/tmp rw - ext4 /dev/example rw
36 31 0:41 /@home /home rw - btrfs /dev/example2 rw,subvolid=258,subvol=/@home
37 31 0:41 /@home/A/nested /elsewhere-btrfs rw - btrfs /dev/example2 rw,subvolid=258,subvol=/@home
`
	for _, tc := range []struct {
		id string
		ok bool
	}{
		{"31", true}, {"32", true}, {"33", true}, {"34", false}, {"35", false}, {"36", true}, {"37", false}, {"38", false},
	} {
		t.Run(tc.id, func(t *testing.T) {
			err := verifyVolumeMount(bufio.NewScanner(strings.NewReader(listing)), tc.id)
			if (err == nil) != tc.ok {
				t.Fatalf("mount %s: allowed = %t, want %t (%v)", tc.id, err == nil, tc.ok, err)
			}
		})
	}
}

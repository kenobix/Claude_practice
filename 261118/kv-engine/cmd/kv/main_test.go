package main

import (
	"bufio"
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/kenobix/Claude_practice/261118/kv-engine/internal/record"
)

func invoke(args []string, input string) (int, string, string) {
	var out, errOut bytes.Buffer
	code := run(args, strings.NewReader(input), &out, &errOut)
	return code, out.String(), errOut.String()
}

func TestMemorySession(t *testing.T) {
	input := `{"op":"put","key":"k","value_base64":"AP8="}
{"op":"get","key":"k"}
{"op":"put","key":"k","value_base64":""}
{"op":"get","key":"k"}
{"op":"delete","key":"k"}
{"op":"delete","key":"missing"}`
	code, out, stderr := invoke([]string{"memory"}, input)
	want := "{\"sequence\":1}\n{\"found\":true,\"value_base64\":\"AP8=\"}\n{\"sequence\":2}\n{\"found\":true,\"value_base64\":\"\"}\n{\"sequence\":3}\n{\"sequence\":4}\n"
	if code != 0 || out != want || !strings.Contains(stderr, `"persistent":false`) {
		t.Fatalf("code=%d stdout=%q stderr=%q", code, out, stderr)
	}
}

func TestMissingKeyStopsSession(t *testing.T) {
	code, out, stderr := invoke([]string{"memory"}, "{\"op\":\"get\",\"key\":\"missing\"}\n{\"op\":\"put\",\"key\":\"k\",\"value_base64\":\"\"}\n")
	if code != 3 || out != "" || !strings.Contains(stderr, `"error":"not_found"`) {
		t.Fatal(code, out, stderr)
	}
}

func TestSessionDoesNotPersist(t *testing.T) {
	if code, _, _ := invoke([]string{"memory"}, `{"op":"put","key":"k","value_base64":""}`); code != 0 {
		t.Fatal(code)
	}
	if code, _, _ := invoke([]string{"memory"}, `{"op":"get","key":"k"}`); code != 3 {
		t.Fatal(code)
	}
}

func TestInvalidRequests(t *testing.T) {
	for _, tc := range []struct{ name, input string }{
		{"invalid-json", "{"}, {"blank", "\n"}, {"array", "[]"}, {"null", "null"},
		{"trailing-json", `{"op":"get","key":"k"} {}`},
		{"trailing-garbage", `{"op":"get","key":"k"} x`},
		{"missing-key", `{"op":"get"}`}, {"empty-key", `{"op":"get","key":""}`},
		{"null-key", `{"op":"get","key":null}`}, {"key-type", `{"op":"get","key":1}`},
		{"nul-key", `{"op":"get","key":"a\u0000b"}`},
		{"long-key", `{"op":"get","key":"` + strings.Repeat("k", 257) + `"}`},
		{"invalid-utf8", "{\"op\":\"get\",\"key\":\"" + string([]byte{255}) + "\"}"},
		{"unknown-field", `{"op":"get","key":"k","extra":1}`},
		{"missing-op", `{"key":"k"}`}, {"unknown-op", `{"op":"inspect","key":"k"}`},
		{"missing-value", `{"op":"put","key":"k"}`},
		{"invalid-base64", `{"op":"put","key":"k","value_base64":"!"}`},
		{"noncanonical-base64", `{"op":"put","key":"k","value_base64":"Zh=="}`},
		{"value-for-get", `{"op":"get","key":"k","value_base64":""}`},
		{"value-for-delete", `{"op":"delete","key":"k","value_base64":""}`},
		{"oversized-line", strings.Repeat(" ", maxRequestSize+1)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			code, out, stderr := invoke([]string{"memory"}, tc.input)
			if code != 2 || out != "" || !strings.Contains(stderr, `"error":"invalid_input"`) {
				t.Fatalf("code=%d out=%q stderr=%q", code, out, stderr)
			}
		})
	}
}

func TestCLIValueLimits(t *testing.T) {
	for _, size := range []int{0, record.MaxValueSize, record.MaxValueSize + 1} {
		value := base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{255}, size))
		input := `{"op":"put","key":"k","value_base64":"` + value + `"}`
		code, _, _ := invoke([]string{"memory"}, input)
		want := 0
		if size > record.MaxValueSize {
			want = 2
		}
		if code != want {
			t.Fatalf("size %d: code %d want %d", size, code, want)
		}
	}
}

func TestArguments(t *testing.T) {
	for _, tc := range []struct {
		args []string
		code int
	}{
		{nil, 2}, {[]string{"put"}, 2}, {[]string{"memory", "--db", "x"}, 2},
		{[]string{"-h"}, 0}, {[]string{"--help"}, 0}, {[]string{"memory", "-h"}, 0},
		{[]string{"-version"}, 0}, {[]string{"--version"}, 0},
	} {
		t.Run(strings.Join(tc.args, "_"), func(t *testing.T) {
			code, _, _ := invoke(tc.args, "")
			if code != tc.code {
				t.Fatal(code)
			}
		})
	}
}

type brokenIO struct{}

func (brokenIO) Read([]byte) (int, error)  { return 0, errors.New("read failure") }
func (brokenIO) Write([]byte) (int, error) { return 0, errors.New("write failure") }

func TestIOErrors(t *testing.T) {
	input := `{"op":"put","key":"k","value_base64":""}`
	for _, tc := range []struct {
		name        string
		args        []string
		in          io.Reader
		out, errOut io.Writer
	}{
		{"stdin", []string{"memory"}, brokenIO{}, io.Discard, io.Discard},
		{"stdout", []string{"memory"}, strings.NewReader(input), brokenIO{}, io.Discard},
		{"stderr", []string{"memory"}, strings.NewReader(input), io.Discard, brokenIO{}},
		{"help", []string{"-h"}, strings.NewReader(""), brokenIO{}, io.Discard},
		{"version", []string{"-version"}, strings.NewReader(""), brokenIO{}, io.Discard},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if code := run(tc.args, tc.in, tc.out, tc.errOut); code != 6 {
				t.Fatal(code)
			}
		})
	}
}

func TestCLIProcessExitCodes(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "kv")
	build := exec.Command("go", "build", "-o", binary, ".")
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build: %v %s", err, out)
	}
	for _, tc := range []struct {
		name, input string
		code        int
	}{
		{"success", `{"op":"put","key":"k","value_base64":""}`, 0},
		{"missing", `{"op":"get","key":"k"}`, 3},
		{"invalid", `{"op":"get","key":""}`, 2},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cmd := exec.Command(binary, "memory")
			cmd.Stdin = strings.NewReader(tc.input)
			var out, stderr bytes.Buffer
			cmd.Stdout = &out
			cmd.Stderr = &stderr
			err := cmd.Run()
			code := 0
			if err != nil {
				var exit *exec.ExitError
				if !errors.As(err, &exit) {
					t.Fatal(err)
				}
				code = exit.ExitCode()
			}
			if code != tc.code {
				t.Fatal(code, stderr.String())
			}
			for _, line := range strings.Split(strings.TrimSpace(stderr.String()), "\n") {
				if !json.Valid([]byte(line)) {
					t.Fatalf("stderr not JSON: %q", line)
				}
			}
		})
	}
}

func TestLineLimitBoundaries(t *testing.T) {
	for _, size := range []int{maxRequestSize - 1, maxRequestSize, maxRequestSize + 1} {
		line := strings.Repeat(" ", size-1) + "\n"
		got, err := readLine(bufio.NewReader(strings.NewReader(line)))
		if size <= maxRequestSize {
			if err != nil || len(got) != size {
				t.Fatal(size, len(got), err)
			}
		} else if !errors.Is(err, errInput) {
			t.Fatal(err)
		}
	}
}

func TestEmptyAndCRLFInput(t *testing.T) {
	if code, out, _ := invoke([]string{"memory"}, ""); code != 0 || out != "" {
		t.Fatal(code, out)
	}
	input := "{\"op\":\"put\",\"key\":\"k\",\"value_base64\":\"\"}\r\n{\"op\":\"get\",\"key\":\"k\"}\r\n"
	if code, out, _ := invoke([]string{"memory"}, input); code != 0 || !strings.Contains(out, `"found":true`) {
		t.Fatal(code, out)
	}
}

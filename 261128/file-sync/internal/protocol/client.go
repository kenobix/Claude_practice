package protocol

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

type Client struct {
	endpoint string
	token    string
	http     *http.Client
}

func Loopback(address string) bool {
	host, port, err := net.SplitHostPort(address)
	n, parseErr := strconv.ParseUint(port, 10, 16)
	return err == nil && host == "127.0.0.1" && parseErr == nil && n != 0 &&
		strconv.FormatUint(n, 10) == port
}

func NewClient(address, token string) (*Client, error) {
	_, valid := TokenDigest(token)
	if !Loopback(address) || !valid {
		return nil, errors.New("expected 127.0.0.1:port and valid FILE_SYNC_TOKEN")
	}
	return &Client{
		endpoint: "http://" + address, token: token,
		http: &http.Client{
			Timeout:       60 * time.Second,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
			Transport: &http.Transport{
				DialContext:       (&net.Dialer{Timeout: 5 * time.Second}).DialContext,
				DisableKeepAlives: true, DisableCompression: true,
				MaxResponseHeaderBytes: 32 << 10,
			},
		},
	}, nil
}

func Digest(data []byte) safefs.Digest {
	sum := sha256.Sum256(data)
	return safefs.Digest{Hash: hex.EncodeToString(sum[:]), Size: int64(len(data))}
}

func VerifyEntry(e Entry) bool {
	return e.Revision > 0 && ValidName(e.Client) && IsHex(e.Operation, 32) &&
		safefs.Validate(e.Path) == nil && MaxCandidate(e.Size, safefs.MaxSize, e.Hash, e.Deleted)
}

func (c *Client) Manifest(ctx context.Context) (Manifest, error) {
	var result Manifest
	err := retry(ctx, func() error {
		result = Manifest{}
		resp, err := c.request(ctx, http.MethodGet, "/v1/manifest", "", nil)
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		if resp.StatusCode != 200 {
			return readProblem(resp)
		}
		clients := resp.Header.Values(ClientHeader)
		if len(clients) != 1 || !ValidName(clients[0]) || !hasMedia(resp, JSON) ||
			!decodeResponse(resp.Body, 32<<20, &result) || !ValidName(result.Space) ||
			!IsHex(result.Generation, 32) || result.Entries == nil || !ValidManifestEntries(result.Entries) {
			return errResponse
		}
		result.Client = clients[0]
		return nil
	})
	return result, err
}

func ValidManifestEntries(entries []Entry) bool {
	if len(entries) > MaxPaths {
		return false
	}
	live := make(map[string]bool)
	for i, e := range entries {
		if !VerifyEntry(e) || (i > 0 && entries[i-1].Path >= e.Path) {
			return false
		}
		if !e.Deleted {
			live[e.Path] = true
		}
	}
	if len(live) > safefs.MaxFiles {
		return false
	}
	for path := range live {
		for i := 0; i < len(path); i++ {
			if path[i] == '/' && live[path[:i]] {
				return false
			}
		}
	}
	return true
}

// Put makes one request. The caller verifies the immutable outgoing bytes and
// owns the attempt budget shared with any later blob_missing response.
func (c *Client) Put(ctx context.Context, generation string, d safefs.Digest, data []byte) error {
	if Digest(data) != d {
		return errors.New("outgoing blob does not match stored request")
	}
	var blob Blob
	return c.json(ctx, http.MethodPut, "/v1/blobs/"+d.Hash, generation, data, MaxJSON, &blob,
		func(status int) bool { return status == 200 || status == 201 },
		func() bool { return blob.Hash == d.Hash && blob.Size == d.Size })
}

func (c *Client) Get(ctx context.Context, generation string, d safefs.Digest) ([]byte, error) {
	var data []byte
	err := retry(ctx, func() error {
		resp, err := c.request(ctx, http.MethodGet, "/v1/blobs/"+d.Hash, generation, nil)
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		if resp.StatusCode != 200 {
			return readProblem(resp)
		}
		if !hasMedia(resp, Binary) || resp.ContentLength != d.Size || len(resp.TransferEncoding) != 0 {
			return errResponse
		}
		got, err := io.ReadAll(io.LimitReader(resp.Body, d.Size+1))
		if err != nil || Digest(got) != d {
			return errResponse
		}
		data = got
		return nil
	})
	return data, err
}

// Post makes exactly one attempt. The caller owns the persisted request and
// shares the two-attempt budget with blob_missing recovery.
func (c *Client) Post(ctx context.Context, change Change, body []byte) (Receipt, error) {
	var result Receipt
	err := c.json(ctx, http.MethodPost, "/v1/changes", "", body, MaxJSON, &result,
		func(status int) bool { return status == 200 },
		func() bool {
			e := result.Entry
			if !VerifyEntry(e) || result.Generation != change.Generation || result.Operation != change.Operation ||
				e.Path != change.Path || e.Deleted || change.Hash == nil || *e.Hash != *change.Hash || e.Size != change.Size {
				return false
			}
			switch result.Result {
			case "applied":
				return e.Revision-1 == change.Base && e.Client == change.Client && e.Operation == change.Operation
			case "unchanged":
				return e.Revision == change.Base
			default:
				return false
			}
		})
	return result, err
}

var errResponse = errors.New("incomplete or invalid hub response")

func Retryable(err error) bool {
	var p *Problem
	return errors.Is(err, errResponse) || (errors.As(err, &p) && p.Status == 503)
}

func retry(ctx context.Context, perform func() error) error {
	err := perform()
	if Retryable(err) && ctx.Err() == nil {
		return perform()
	}
	return err
}

func (c *Client) request(ctx context.Context, method, path, generation string, data []byte) (*http.Response, error) {
	var body io.Reader
	if data != nil {
		body = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.endpoint+path, body)
	if err != nil {
		return nil, errResponse
	}
	// Never include the request, URL or credentials in a diagnostic.
	req.Header.Set("Authorization", "Bearer "+c.token)
	if generation != "" {
		req.Header.Set(GenerationHeader, generation)
	}
	switch method {
	case http.MethodPut:
		req.Header.Set("Content-Type", Binary)
	case http.MethodPost:
		req.Header.Set("Content-Type", JSON)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, errResponse
	}
	if len(resp.Header.Values("Content-Encoding")) != 0 || resp.StatusCode >= 300 && resp.StatusCode < 400 {
		resp.Body.Close()
		return nil, errResponse
	}
	return resp, nil
}

func (c *Client) json(ctx context.Context, method, path, generation string, data []byte, limit int64, target any,
	statusOK func(int) bool, bodyOK func() bool) error {
	resp, err := c.request(ctx, method, path, generation, data)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if !statusOK(resp.StatusCode) {
		return readProblem(resp)
	}
	if !hasMedia(resp, JSON) || !decodeResponse(resp.Body, limit, target) || !bodyOK() {
		return errResponse
	}
	return nil
}

func hasMedia(resp *http.Response, expected string) bool {
	values := resp.Header.Values("Content-Type")
	if len(values) != 1 {
		return false
	}
	kind, _, err := mime.ParseMediaType(values[0])
	return err == nil && kind == expected
}

func decodeResponse(r io.Reader, limit int64, target any) bool {
	data, err := io.ReadAll(io.LimitReader(r, limit+1))
	if err != nil || int64(len(data)) > limit || !ValidJSONText(data) {
		return false
	}
	keys := json.NewDecoder(bytes.NewReader(data))
	if !uniqueValue(keys, 0) {
		return false
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if d.Decode(target) != nil {
		return false
	}
	_, err = d.Token()
	return err == io.EOF
}

func uniqueValue(d *json.Decoder, depth int) bool {
	if depth > 8 {
		return false
	}
	t, err := d.Token()
	if err != nil {
		return false
	}
	delim, ok := t.(json.Delim)
	if !ok {
		return true
	}
	seen := make(map[string]bool)
	for d.More() {
		if delim == '{' {
			key, err := d.Token()
			k, ok := key.(string)
			if err != nil || !ok || strings.ToLower(k) != k || seen[k] {
				return false
			}
			seen[k] = true
		}
		if !uniqueValue(d, depth+1) {
			return false
		}
	}
	close, err := d.Token()
	return err == nil && (delim == '{' && close == json.Delim('}') || delim == '[' && close == json.Delim(']'))
}

func readProblem(resp *http.Response) error {
	if !hasMedia(resp, JSON) {
		return errResponse
	}
	wire := struct {
		Problem
		Current *Entry `json:"current"`
	}{}
	if !decodeResponse(resp.Body, 1<<20, &wire) || wire.Code == "" {
		return errResponse
	}
	wire.Problem.Status = resp.StatusCode
	wire.Problem.Current = wire.Current
	return &wire.Problem
}

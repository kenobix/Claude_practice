package hub

import (
	"context"
	"encoding/json"
	"io"
	"mime"
	"net/http"
	"strconv"
	"strings"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func (h *Hub) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	path := r.URL.EscapedPath()
	var methods string
	switch {
	case path == "/v1/manifest":
		methods = "GET"
	case path == "/v1/changes":
		methods = "POST"
	case strings.HasPrefix(path, "/v1/blobs/"):
		methods = "GET, PUT"
	default:
		writeProblem(w, problem(404, "not_found"))
		return
	}
	if r.Method != methods && !(methods == "GET, PUT" && (r.Method == "GET" || r.Method == "PUT")) {
		w.Header().Set("Allow", methods)
		writeProblem(w, problem(405, "method_not_allowed"))
		return
	}
	auth := r.Header.Values("Authorization")
	if len(auth) != 1 {
		unauthorized(w)
		return
	}
	token, ok := strings.CutPrefix(auth[0], "Bearer ")
	p, valid := h.authenticate(token)
	if !ok || !valid {
		unauthorized(w)
		return
	}
	if r.URL.RawQuery != "" || r.URL.ForceQuery || len(r.Header.Values("Content-Encoding")) != 0 {
		writeProblem(w, problem(400, "invalid_request"))
		return
	}
	switch path {
	case "/v1/manifest":
		if hasBody(r) {
			writeProblem(w, problem(400, "invalid_request"))
			return
		}
		h.manifest(w, r, p)
	case "/v1/changes":
		h.change(w, r, p)
	default:
		h.blob(w, r, p, strings.TrimPrefix(path, "/v1/blobs/"))
	}
}

func unauthorized(w http.ResponseWriter) {
	w.Header().Set("WWW-Authenticate", "Bearer")
	writeProblem(w, problem(401, "unauthorized"))
}

func problem(status int, code string) *protocol.Problem {
	return &protocol.Problem{Status: status, Code: code}
}

func mismatch(generation string) *protocol.Problem {
	return &protocol.Problem{Status: 409, Code: "generation_mismatch", Generation: generation}
}

func writeJSON(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", protocol.JSON)
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(data)
}

func writeProblem(w http.ResponseWriter, p *protocol.Problem) {
	if p.Code == "revision_mismatch" {
		writeJSON(w, p.Status, struct {
			*protocol.Problem
			Current *protocol.Entry `json:"current"`
		}{p, p.Current})
		return
	}
	writeJSON(w, p.Status, p)
}

func hasBody(r *http.Request) bool {
	return r.ContentLength != 0 || len(r.TransferEncoding) != 0
}

func media(r *http.Request, expected string) bool {
	values := r.Header.Values("Content-Type")
	if len(values) != 1 {
		return false
	}
	kind, params, err := mime.ParseMediaType(values[0])
	return err == nil && kind == expected &&
		(len(params) == 0 || (expected == protocol.JSON && len(params) == 1 && strings.EqualFold(params["charset"], "utf-8")))
}

func (h *Hub) blob(w http.ResponseWriter, r *http.Request, p principal, hash string) {
	if r.Method == http.MethodPut {
		switch {
		case !media(r, protocol.Binary):
			writeProblem(w, problem(415, "unsupported_media_type"))
			return
		case len(r.TransferEncoding) != 0:
			writeProblem(w, problem(400, "invalid_request"))
			return
		case r.ContentLength < 0 || len(r.Header.Values("Content-Length")) != 1:
			writeProblem(w, problem(411, "length_required"))
			return
		case r.ContentLength > safefs.MaxSize:
			writeProblem(w, problem(413, "body_too_large"))
			return
		}
	} else if hasBody(r) || len(r.Header.Values("Range")) != 0 {
		writeProblem(w, problem(400, "invalid_request"))
		return
	}
	generation := r.Header.Values(protocol.GenerationHeader)
	switch {
	case len(generation) != 1 || !protocol.IsHex(generation[0], 32):
		writeProblem(w, problem(400, "invalid_request"))
	case generation[0] != p.generation:
		writeProblem(w, mismatch(p.generation))
	case !protocol.IsHex(hash, 64):
		writeProblem(w, problem(400, "invalid_request"))
	case r.Method == http.MethodPut:
		status, failure := h.putBlob(context.WithoutCancel(r.Context()), p, hash, r.ContentLength, r.Body)
		if failure != nil {
			writeProblem(w, failure)
			return
		}
		if err := h.at("before-blob-response"); err != nil {
			h.mu.Lock()
			failure := h.unavailable()
			h.mu.Unlock()
			writeProblem(w, failure)
			return
		}
		writeJSON(w, status, protocol.Blob{Hash: hash, Size: r.ContentLength})
	default:
		data, failure := h.getBlob(context.WithoutCancel(r.Context()), p, hash)
		if failure != nil {
			writeProblem(w, failure)
			return
		}
		w.Header().Set("Content-Type", protocol.Binary)
		w.Header().Set("Content-Length", strconv.Itoa(len(data)))
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(data)
	}
}

func (h *Hub) change(w http.ResponseWriter, r *http.Request, p principal) {
	if !media(r, protocol.JSON) {
		writeProblem(w, problem(415, "unsupported_media_type"))
		return
	}
	if r.ContentLength > protocol.MaxJSON {
		writeProblem(w, problem(413, "body_too_large"))
		return
	}
	data, err := io.ReadAll(io.LimitReader(r.Body, protocol.MaxJSON+1))
	if len(data) > protocol.MaxJSON {
		writeProblem(w, problem(413, "body_too_large"))
		return
	}
	change, decodeErr := protocol.DecodeChange(data)
	if err != nil || decodeErr != nil || !protocol.IsHex(change.Generation, 32) ||
		!protocol.IsHex(change.Operation, 32) || !protocol.ValidName(change.Client) || change.Base < 0 {
		writeProblem(w, problem(400, "invalid_request"))
		return
	}
	if change.Client != p.client {
		writeProblem(w, problem(403, "client_mismatch"))
		return
	}
	if change.Generation != p.generation {
		writeProblem(w, mismatch(p.generation))
		return
	}
	if safefs.Validate(change.Path) != nil || !protocol.MaxCandidate(change.Size, safefs.MaxSize, change.Hash, change.Deleted) {
		writeProblem(w, problem(400, "invalid_request"))
		return
	}
	result, failure := h.accept(context.WithoutCancel(r.Context()), p, change)
	if failure != nil {
		writeProblem(w, failure)
		return
	}
	w.Header().Set("Content-Type", protocol.JSON)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(result)
}

package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/hub"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/local"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
)

const usage = `usage:
  file-sync scan    -volume DIR -root PATH
  file-sync init    -volume DIR -root PATH
  file-sync status  -volume DIR -root PATH
  file-sync copy    -volume DIR -source PATH -destination PATH -revision N [-path PATH]
  file-sync recover -volume DIR -source PATH -destination PATH
  file-sync hub-init -volume DIR -root PATH -space SPACE -client-a ID -client-b ID
  file-sync hub-backup -volume DIR -source HUB -destination NEW_BACKUP
  file-sync hub-restore -volume DIR -source BACKUP -destination NEW_HUB -space SPACE -client-a ID -client-b ID
  file-sync hub -volume DIR -root PATH [-listen 127.0.0.1:8080]
  file-sync register -volume DIR -root PATH -hub 127.0.0.1:8080 (--seed | --pull | --reconcile [--approve SHA256])
  file-sync sync-once -volume DIR -root PATH -hub 127.0.0.1:8080
  file-sync conflicts -volume DIR -root PATH -hub 127.0.0.1:8080
  file-sync resolve -volume DIR -root PATH -hub 127.0.0.1:8080 -path PATH
                    --generation ID --revision N --choose local|hub|delete|file [--candidate PATH]`

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, os.Args[1:], os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, err)
		switch {
		case errors.Is(err, local.ErrConflict):
			os.Exit(3)
		case errors.Is(err, flag.ErrHelp):
			return
		default:
			os.Exit(1)
		}
	}
}

func run(ctx context.Context, args []string, output io.Writer) error {
	if len(args) == 0 {
		return errors.New(usage)
	}
	command := args[0]
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	volume := flags.String("volume", "", "absolute trusted volume directory")
	var root *string
	var req local.Request
	var address, space, clientA, clientB string
	var seed, pull, reconcile bool
	var approval string
	var selection local.ResolveRequest
	switch command {
	case "scan", "init", "status", "hub-init", "hub", "register", "sync-once", "conflicts", "resolve":
		root = flags.String("root", "", "root relative to volume")
		switch command {
		case "hub":
			flags.StringVar(&address, "listen", "127.0.0.1:8080", "loopback HTTP address")
		case "hub-init":
			flags.StringVar(&space, "space", "default", "sync space ID")
			flags.StringVar(&clientA, "client-a", "client-a", "first client ID")
			flags.StringVar(&clientB, "client-b", "client-b", "second client ID")
		case "register", "sync-once", "conflicts", "resolve":
			flags.StringVar(&address, "hub", "127.0.0.1:8080", "loopback hub address")
			switch command {
			case "register":
				flags.BoolVar(&seed, "seed", false, "seed a hub with no rows")
				flags.BoolVar(&pull, "pull", false, "pull into an empty client root")
				flags.BoolVar(&reconcile, "reconcile", false, "preview or explicitly approve re-registration")
				flags.StringVar(&approval, "approve", "", "SHA-256 printed by an unchanged reconciliation preview")
			case "resolve":
				flags.StringVar(&selection.Path, "path", "", "relative conflict path")
				flags.StringVar(&selection.Generation, "generation", "", "displayed hub generation")
				flags.Int64Var(&selection.Revision, "revision", -1, "displayed latest hub revision")
				flags.StringVar(&selection.Choice, "choose", "", "local, hub, delete, or file")
				flags.StringVar(&selection.File, "candidate", "", "root-relative file to use with --choose file")
			}
		}
	case "copy", "recover", "hub-backup", "hub-restore":
		if command == "hub-restore" {
			flags.StringVar(&space, "space", "default", "current sync space ID")
			flags.StringVar(&clientA, "client-a", "client-a", "first current client ID")
			flags.StringVar(&clientB, "client-b", "client-b", "second current client ID")
		}
		flags.StringVar(&req.Source, "source", "", "source root relative to volume")
		flags.StringVar(&req.Destination, "destination", "", "destination root relative to volume")
		if command == "copy" {
			flags.Int64Var(&req.Revision, "revision", 0, "target revision")
			flags.StringVar(&req.Path, "path", "", "restrict copy to this relative path")
		}
	default:
		return errors.New(usage)
	}
	if err := flags.Parse(args[1:]); err != nil {
		return fmt.Errorf("%w\n%s", err, usage)
	}
	if flags.NArg() != 0 {
		return errors.New(usage)
	}
	explicitPath := false
	flags.Visit(func(f *flag.Flag) {
		if f.Name == "path" {
			explicitPath = true
		}
	})
	if command == "copy" && explicitPath && req.Path == "" {
		return errors.New("invalid path: empty -path")
	}
	req.Volume = *volume
	switch command {
	case "scan":
		entries, err := local.Scan(req.Volume, *root)
		if err != nil {
			return err
		}
		return json.NewEncoder(output).Encode(entries)
	case "init":
		return local.Init(ctx, req.Volume, *root)
	case "status":
		status, err := local.Inspect(ctx, req.Volume, *root)
		if err != nil {
			return err
		}
		return json.NewEncoder(output).Encode(status)
	case "hub-init":
		return hub.Init(ctx, *volume, *root, hub.Provision{Space: space, Clients: [2]hub.Client{
			{ID: clientA, Token: os.Getenv("FILE_SYNC_TOKEN_A")},
			{ID: clientB, Token: os.Getenv("FILE_SYNC_TOKEN_B")},
		}})
	case "hub-backup":
		info, err := hub.Backup(ctx, *volume, req.Source, req.Destination)
		if err != nil {
			return err
		}
		return json.NewEncoder(output).Encode(info)
	case "hub-restore":
		info, err := hub.Restore(ctx, *volume, req.Source, req.Destination, hub.Provision{Space: space, Clients: [2]hub.Client{
			{ID: clientA, Token: os.Getenv("FILE_SYNC_TOKEN_A")},
			{ID: clientB, Token: os.Getenv("FILE_SYNC_TOKEN_B")},
		}})
		if err != nil {
			return err
		}
		return json.NewEncoder(output).Encode(info)
	case "hub":
		return serve(ctx, *volume, *root, address, output)
	case "register", "sync-once", "conflicts", "resolve":
		remote, err := protocol.NewClient(address, os.Getenv("FILE_SYNC_TOKEN"))
		if err != nil {
			return err
		}
		req := local.NetworkRequest{Volume: *volume, Root: *root, Remote: remote}
		switch command {
		case "sync-once":
			return local.Sync(ctx, req)
		case "conflicts":
			list, err := local.ListConflicts(ctx, req)
			if err != nil {
				return err
			}
			return json.NewEncoder(output).Encode(list)
		case "resolve":
			return local.Resolve(ctx, req, selection)
		}
		if reconcile && !seed && !pull {
			report, err := local.Reconcile(ctx, req, approval)
			if report.Manifest.Generation == "" {
				return err
			}
			return errors.Join(err, json.NewEncoder(output).Encode(report))
		}
		if seed == pull || reconcile || approval != "" {
			return errors.New("select exactly one of --seed, --pull or --reconcile")
		}
		mode := "seed"
		if pull {
			mode = "pull"
		}
		return local.Register(ctx, req, mode)
	case "copy":
		return local.Copy(ctx, req)
	default:
		return local.Recover(ctx, req)
	}
}

func serve(ctx context.Context, volume, root, address string, output io.Writer) (err error) {
	if !protocol.Loopback(address) {
		return errors.New("hub must listen on 127.0.0.1 with an explicit port")
	}
	storage, err := hub.Open(ctx, volume, root)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, storage.Close()) }()
	listener, err := net.Listen("tcp4", address)
	if err != nil {
		return err
	}
	defer listener.Close()
	server := &http.Server{
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 60 * time.Second, WriteTimeout: 60 * time.Second,
		IdleTimeout: 30 * time.Second, MaxHeaderBytes: 32 << 10,
	}
	active := make(chan struct{}, 8)
	server.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case active <- struct{}{}:
			defer func() { <-active }()
			storage.ServeHTTP(w, r)
		case <-r.Context().Done():
		}
	})
	if _, err := fmt.Fprintln(output, "listening", listener.Addr()); err != nil {
		return err
	}
	result := make(chan error, 1)
	go func() { result <- server.Serve(listener) }()
	select {
	case <-ctx.Done():
		err = server.Shutdown(context.Background())
		return errors.Join(err, serveResult(<-result))
	case err = <-result:
		return errors.Join(serveResult(err), server.Shutdown(context.Background()))
	}
}

func serveResult(err error) error {
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

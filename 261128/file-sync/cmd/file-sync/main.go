package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/local"
)

const usage = `usage:
  file-sync scan    -volume DIR -root PATH
  file-sync init    -volume DIR -root PATH
  file-sync status  -volume DIR -root PATH
  file-sync copy    -volume DIR -source PATH -destination PATH -revision N [-path PATH]
  file-sync recover -volume DIR -source PATH -destination PATH`

func main() {
	if err := run(context.Background(), os.Args[1:], os.Stdout); err != nil {
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
	switch command {
	case "scan", "init", "status":
		root = flags.String("root", "", "root relative to volume")
	case "copy", "recover":
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
	if explicitPath && req.Path == "" {
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
	case "copy":
		return local.Copy(ctx, req)
	default:
		return local.Recover(ctx, req)
	}
}

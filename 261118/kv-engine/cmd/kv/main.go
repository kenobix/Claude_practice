// Command kv is the stage-0 development scaffold. It does not open databases.
package main

import (
	"flag"
	"fmt"
	"os"
)

func main() {
	version := flag.Bool("version", false, "print scaffold version")
	flag.Usage = func() {
		fmt.Fprintln(flag.CommandLine.Output(), "Usage: kv [-version]")
		fmt.Fprintln(flag.CommandLine.Output(), "Stage 0: database operations are not implemented.")
	}
	flag.Parse()
	if flag.NArg() != 0 {
		flag.Usage()
		os.Exit(2)
	}
	if *version {
		fmt.Println("kv stage0 (no database operations)")
		return
	}
	flag.Usage()
}

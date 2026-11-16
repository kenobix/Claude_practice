// Package recovery validates log snapshots and rebuilds the latest-key position index.
// Scan never modifies files; the engine applies the narrowly defined incomplete-tail repair.
package recovery

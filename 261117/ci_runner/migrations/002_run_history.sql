-- Match cursor pagination without changing the immutable 001 schema.
CREATE INDEX runs_history ON runs (created_at DESC, id DESC);

-- Bounded opaque log payloads, committed with the fenced attempt/run result.
-- This budget covers retained bytes, not PostgreSQL metadata, WAL or backups.
CREATE TABLE log_storage_budget (
    singleton boolean PRIMARY KEY CHECK (singleton),
    capacity_bytes bigint NOT NULL DEFAULT 67108864 CHECK (capacity_bytes BETWEEN 0 AND 67108864),
    used_bytes bigint NOT NULL DEFAULT 0 CHECK (used_bytes BETWEEN 0 AND capacity_bytes)
);
INSERT INTO log_storage_budget(singleton) VALUES (true);

CREATE TABLE attempt_logs (
    attempt_id uuid PRIMARY KEY REFERENCES attempts(id),
    stdout bytea NOT NULL,
    stderr bytea NOT NULL,
    observed_bytes bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (octet_length(stdout) + octet_length(stderr) <= 1048576),
    CHECK (observed_bytes >= octet_length(stdout) + octet_length(stderr))
);

CREATE FUNCTION guard_attempt_log_storage() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    retained bigint;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        UPDATE log_storage_budget SET used_bytes = 0 WHERE singleton;
        RETURN NULL;
    ELSIF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'attempt logs are immutable' USING ERRCODE = '23514';
    END IF;
    retained := octet_length(NEW.stdout)::bigint + octet_length(NEW.stderr);
    UPDATE log_storage_budget SET used_bytes = used_bytes + retained
        WHERE singleton AND used_bytes + retained <= capacity_bytes;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'retained log budget exhausted' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER attempt_logs_storage_guard BEFORE INSERT OR UPDATE OR DELETE ON attempt_logs
    FOR EACH ROW EXECUTE FUNCTION guard_attempt_log_storage();
-- Dedicated test DB truncation (including TRUNCATE runs CASCADE) resets accounting.
CREATE TRIGGER attempt_logs_truncate AFTER TRUNCATE ON attempt_logs
    FOR EACH STATEMENT EXECUTE FUNCTION guard_attempt_log_storage();

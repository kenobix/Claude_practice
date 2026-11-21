ALTER TABLE attempts
    ADD COLUMN claimable_at timestamptz,
    ADD COLUMN execution_ms integer CHECK (execution_ms >= 0);
CREATE INDEX attempts_finished ON attempts (finished_at DESC, id DESC) WHERE finished_at IS NOT NULL;

ALTER TABLE artifacts ADD COLUMN payload bytea;
-- Preserve any pre-existing metadata-only rows. New artifacts must have bytes.
ALTER TABLE artifacts ADD CONSTRAINT artifacts_result_payload CHECK (
    payload IS NOT NULL AND relative_name = 'result.json'
    AND storage_key = attempt_id::text || '/result.json'
    AND size_bytes = octet_length(payload) AND size_bytes BETWEEN 1 AND 4096
    AND sha256 = encode(sha256(payload), 'hex')
) NOT VALID;

CREATE TABLE artifact_storage_budget (
    singleton boolean PRIMARY KEY CHECK (singleton),
    capacity_bytes bigint NOT NULL DEFAULT 16777216 CHECK (capacity_bytes BETWEEN 0 AND 16777216),
    used_bytes bigint NOT NULL DEFAULT 0 CHECK (used_bytes BETWEEN 0 AND capacity_bytes)
);
INSERT INTO artifact_storage_budget(singleton) VALUES (true);

CREATE FUNCTION guard_result_artifact_storage() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        UPDATE artifact_storage_budget SET used_bytes = 0 WHERE singleton;
        RETURN NULL;
    ELSIF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'result artifacts are immutable' USING ERRCODE = '23514';
    END IF;
    UPDATE artifact_storage_budget SET used_bytes = used_bytes + NEW.size_bytes
        WHERE singleton AND used_bytes + NEW.size_bytes <= capacity_bytes;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'result artifact budget exhausted' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER artifacts_storage_guard AFTER INSERT OR UPDATE OR DELETE ON artifacts
    FOR EACH ROW EXECUTE FUNCTION guard_result_artifact_storage();
CREATE TRIGGER artifacts_truncate AFTER TRUNCATE ON artifacts
    FOR EACH STATEMENT EXECUTE FUNCTION guard_result_artifact_storage();

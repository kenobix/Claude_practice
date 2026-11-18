"""Server-owned allowlist. Request data never supplies execution instructions."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RepositoryPolicy:
    repository_id: str = 'kenobix/Claude_practice'
    ref: str = 'refs/heads/main'
    workflow_version: str = 'fixture-v1'


POLICY = RepositoryPolicy()
BODY_LIMIT = 4096
SCHEMA_VERSION = 6

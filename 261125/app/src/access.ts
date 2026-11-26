import type { DocumentAccess } from '../shared/protocol.ts';
import type { LocalCache, LocalDraft } from './cache.ts';

export async function lookupAccess(id: string): Promise<DocumentAccess | string> {
  const response = await fetch(`/api/documents/${id}`);
  const data = await response.json();
  return response.ok ? data as DocumentAccess : String(data.error);
}

export async function quarantineDrafts(cache: LocalCache, drafts: LocalDraft[], id: string, reason: string, currentRevision = Infinity) {
  await Promise.all(drafts.filter((draft) => draft.documentId === id && !draft.blocked && draft.revision < currentRevision)
    .map((draft) => cache.quarantine(draft.scope, reason)));
}

export async function reviewDrafts(cache: LocalCache) {
  const drafts = await cache.drafts();
  const ids = new Set(drafts.filter((draft) => !draft.blocked).map((draft) => draft.documentId));
  await Promise.all([...ids].map(async (id) => {
    const access = await lookupAccess(id);
    if (access === 'DOCUMENT_DELETED' || access === 'ACCESS_DENIED') await quarantineDrafts(cache, drafts, id, access);
    if (typeof access !== 'string') await quarantineDrafts(cache, drafts, id, 'GRANT_CHANGED', access.grant_revision);
  }));
}

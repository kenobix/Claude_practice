import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import type { Access, DocumentSummary, User } from '../shared/protocol.ts';
import type { RoomStatus } from './room.ts';
import { deleteCache, openCache, readDraftText, withCacheLock } from './cache.ts';
import type { LocalCache, LocalDraft } from './cache.ts';
import { reviewDrafts } from './access.ts';
import { draftReason, errorText, roleLabel } from './words.ts';
import './style.css';

type Session = { user: User | null; accounts: User[] };
const initialStatus: RoomStatus = {
  connection: 'joining', sequence: null, role: null, users: [], problem: '', localSave: 'idle', serverSave: 'idle',
  retrying: false, sessionExpired: false,
};
const localLabel = { idle: '', writing: '端末に保存中', saved: '端末内保存済み', failed: '端末内保存に失敗' };
const serverLabel = { idle: '', waiting: 'サーバー保存待ち', saved: 'サーバー保存済み', blocked: 'サーバーへの保存を停止' };

function downloadText(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name + '.txt';
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/${path}`, {
    method,
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(errorText(data.error));
  return data;
}

function Login({ accounts, onLogin, busy }: { accounts: User[]; onLogin: (id: string, password: string) => void; busy: boolean }) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onLogin(String(form.get('userId')), String(form.get('password')));
  }

  return <main className="login">
    <div className="login-intro">
      <div className="note-mark" aria-hidden="true"><span /><span /><span /></div>
      <h1>共同ノート</h1>
    </div>
    <form className="login-form" onSubmit={submit}>
      <h2>ログイン</h2>
      <label>アカウント<select name="userId" autoComplete="username" required>
        {accounts.map(({ id, name }) => <option value={id} key={id}>{name}</option>)}
      </select></label>
      <label>パスワード<input name="password" type="password" autoComplete="current-password" required /></label>
      <p className="muted">起動時にターミナルに表示されたパスワードを使います。</p>
      <button className="primary" disabled={busy}>ログイン</button>
    </form>
  </main>;
}

function DocumentList({ documents, selected, choose, refresh, create, busy }: {
  documents: DocumentSummary[]; selected: string | null; choose: (id: string) => void;
  refresh: () => void; create: (title: string) => void; busy: boolean;
}) {
  const [title, setTitle] = useState('');
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!title.trim()) return;
    create(title);
    setTitle('');
  }

  return <aside className="document-index" aria-label="文書一覧">
    <div className="section-heading"><h2>文書</h2><button disabled={busy} onClick={refresh}>一覧を更新</button></div>
    {documents.length === 0 && <p className="muted">開ける文書はありません。</p>}
    <nav aria-label="開ける文書">
      {documents.map((document) => <button className="document-link" key={document.id}
        aria-current={document.id === selected ? 'true' : undefined} onClick={() => choose(document.id)}>
        <span>{document.title}</span><small>{roleLabel[document.role]}</small>
      </button>)}
    </nav>
    <form className="new-document" onSubmit={submit}>
      <label>新しい文書<input name="title" placeholder="文書のタイトル" value={title} maxLength={80} required
        onChange={(event) => setTitle(event.target.value)} /></label>
      <button className="primary" disabled={busy}>作成</button>
    </form>
  </aside>;
}

function Room({ document, user, cache, draftsChanged, reconnect, reauthenticate }: {
  document: DocumentSummary; user: User; cache: LocalCache;
  draftsChanged: () => void; reconnect: () => void; reauthenticate: () => void;
}) {
  const [status, setStatus] = useState(initialStatus);
  const editor = useRef<HTMLDivElement>(null);
  const binding = useRef<ReturnType<typeof import('./room.ts').openRoom> | null>(null);
  useEffect(() => {
    let cancelled = false;
    void import('./room.ts').then(({ openRoom }) => {
      if (!cancelled && editor.current) {
        binding.current = openRoom(document.id, user, editor.current, initialStatus, cache, setStatus, draftsChanged, reconnect);
      }
    }).catch(() => {
      if (!cancelled) setStatus({ ...initialStatus, connection: 'offline', problem: '編集画面を読み込めませんでした。接続し直してください。' });
    });
    return () => { cancelled = true; binding.current?.close(); binding.current = null; };
  }, [document.id, user, cache, draftsChanged, reconnect]);

  return <>
    <article className="page" aria-label="文書編集">
      <header className="page-heading">
        <h1>{document.title}</h1>
        <div className="page-details">
          <span role="status" data-connection={status.connection} className="connection">
            {status.connection === 'online' ? '接続中' : status.connection === 'joining' ? '接続を確認中' : '未接続'}
          </span>
          {status.role && <span>{roleLabel[status.role]}</span>}
          {status.sequence !== null && <span>受理順番号 <output aria-label="受理順番号">{status.sequence}</output></span>}
          <span data-local-save={status.localSave} aria-live="polite">{localLabel[status.localSave]}</span>
          <span data-server-save={status.serverSave} aria-live="polite">{serverLabel[status.serverSave]}</span>
        </div>
        {status.retrying && <p className="muted" role="status">{errorText('RATE_LIMIT')}</p>}
      </header>
      {status.problem && <div className="room-error"><p role="alert">{status.problem}</p>
        {status.volatileDraft !== undefined && <button onClick={() => downloadText(`draft-${document.id}`, status.volatileDraft!)}>
          未保存の本文を書き出す
        </button>}
        {status.legacyBody !== undefined ? <button onClick={() => downloadText(`saved-${document.id}`, status.legacyBody!)}>
          保存済みの本文を書き出す
        </button> : <button onClick={async () => {
          await binding.current?.beforeReconnect();
          if (status.sessionExpired) reauthenticate();
          else reconnect();
        }}>{status.sessionExpired ? 'ログインし直す' : '接続し直す'}</button>}</div>}
      <div className="editor" ref={editor} />
    </article>
    <section className="presence" aria-label="接続中の利用者">
      <h2>接続中の利用者</h2>
      {status.users.length > 0 ? <ul>{status.users.map((person) => <li key={person.id}>
        <span className="person-dot" data-user={person.id} aria-hidden="true" />{person.name}
        {user.id === person.id && <span className="muted">（自分）</span>}
      </li>)}</ul> : <p className="muted">接続後に表示します。</p>}
    </section>
  </>;
}

function Sharing({ document, user, accounts, update, remove, busy }: {
  document: DocumentSummary; user: User; accounts: User[]; update: (id: string, role: Access) => void;
  remove: () => void; busy: boolean;
}) {
  if (document.role !== 'owner') return null;
  return <section className="sharing">
    <h2>共有設定</h2>
    {accounts.filter((person) => person.id !== user.id).map((person) => <label key={person.id} className="grant-row">
      <span>{person.name}</span>
      <select aria-label={`${person.name}の権限`} value={document.access?.[person.id] ?? ''} disabled={busy}
        onChange={(event) => update(person.id, (event.target.value || null) as Access)}>
        <option value="">アクセスなし</option>
        <option value="editor">編集者</option>
        <option value="viewer">閲覧者</option>
      </select>
    </label>)}
    <button className="delete-document" disabled={busy} onClick={() => {
      if (window.confirm('この文書を削除しますか？ 削除後はこの文書に接続できません。')) remove();
    }}>文書を削除</button>
  </section>;
}

function Drafts({ drafts, documents, exportDraft }: {
  drafts: LocalDraft[]; documents: DocumentSummary[]; exportDraft: (draft: LocalDraft) => void;
}) {
  if (!drafts.length) return null;
  return <section className="drafts" aria-label="同期を停止した下書き">
    <h2>同期を停止した下書き</h2>
    <ul>{drafts.map((draft) => <li key={draft.scope}>
      <strong>{documents.find(({ id }) => id === draft.documentId)?.title ?? draft.documentId}</strong>
      <small>付与版 {draft.revision}</small>
      <p>{draftReason(draft.blocked!)}</p>
      <button onClick={() => exportDraft(draft)}>テキストを書き出す</button>
    </li>)}</ul>
  </section>;
}

function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [selected, setSelected] = useState<DocumentSummary | null>(null);
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [cache, setCache] = useState<LocalCache | null>(null);
  const [cacheReady, setCacheReady] = useState(false);
  const cacheRef = useRef<LocalCache | null>(null);
  const [drafts, setDrafts] = useState<LocalDraft[]>([]);
  const draftRead = useRef(0);
  const current = documents.find((document) => document.id === selected?.id);
  const userId = session?.user?.id;
  const reconnect = useCallback(() => setRetry((attempt) => attempt + 1), []);

  const draftsChanged = useCallback(() => {
    const active = cacheRef.current;
    if (!active) return;
    const generation = ++draftRead.current;
    void active.drafts().then((items) => {
      if (cacheRef.current === active && draftRead.current === generation) setDrafts(items.filter((item) => item.blocked));
    }).catch(() => {
      if (cacheRef.current === active) setProblem(errorText('LOCAL_CACHE'));
    });
  }, []);

  function clearWorkspace() {
    cacheRef.current?.close();
    cacheRef.current = null;
    setCache(null);
    setDocuments([]);
    setSelected(null);
    setDrafts([]);
  }

  useEffect(() => {
    setCacheReady(false);
    if (!userId) return;
    let cancelled = false;
    let opened: LocalCache | undefined;
    function endWorkspace() {
      if (!cancelled) {
        clearWorkspace();
        setSession((previous) => previous && { ...previous, user: null });
      }
    }
    void withCacheLock(userId, async () => {
      const current = await api<Session>('session');
      if (cancelled) return;
      if (current.user?.id !== userId) return endWorkspace();
      return openCache(userId, endWorkspace);
    }).then(async (active) => {
      if (!active) return;
      opened = active;
      if (cancelled || active.signal.aborted) return active.close();
      cacheRef.current = active;
      setCache(active);
      setCacheReady(true);
      await reviewDrafts(active);
      draftsChanged();
    }).catch(() => {
      if (!cancelled && (cacheRef.current === opened || !opened)) setProblem(errorText('LOCAL_CACHE'));
    }).finally(() => {
      if (!cancelled) setCacheReady(true);
    });
    return () => { cancelled = true; opened?.close(); };
  }, [userId, draftsChanged]);

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setProblem('');
    try {
      await action();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : errorText('SERVER_ERROR'));
    } finally {
      setBusy(false);
    }
  }

  async function refreshDocuments() {
    const data = await api<{ documents: DocumentSummary[] }>('documents');
    setDocuments(data.documents);
    const active = cacheRef.current;
    if (active) {
      await reviewDrafts(active);
      draftsChanged();
    }
  }

  function loadSession() {
    void perform(async () => {
      const data = await api<Session>('session');
      if (!data.user || data.user.id !== userId) clearWorkspace();
      setSession(data);
      if (data.user) await refreshDocuments();
    });
  }

  useEffect(loadSession, []);

  if (!session) return <main className="boot" aria-live="polite">
    {problem ? <><p role="alert">{problem}</p><button onClick={loadSession}>読み直す</button></> : <p>読み込み中</p>}
  </main>;

  if (!session.user) return <>
    {problem && <p role="alert" className="app-error">{problem}</p>}
    <Login accounts={session.accounts} busy={busy} onLogin={(userId, password) => void perform(async () => {
      const { user } = await api<{ user: User }>('session', 'POST', { userId, password });
      setSession({ ...session, user });
      await refreshDocuments();
    })} />
  </>;

  const user = session.user;
  return <div className="workspace">
    <header className="masthead">
      <span className="brand"><span className="brand-mark" aria-hidden="true" />共同ノート</span>
      <div className="current-user"><span>{user.name}</span><button disabled={busy || !cacheReady} onClick={() => void perform(async () => {
        clearWorkspace();
        await withCacheLock(user.id, async () => {
          await api('session', 'DELETE');
          await deleteCache(user.id);
        });
        setSession({ ...session, user: null });
      })}>ログアウト</button></div>
    </header>
    {problem && <p role="alert" className="app-error">{problem}</p>}
    <main className="desk">
      <div>
        <DocumentList documents={documents} selected={selected?.id ?? null}
          choose={(id) => setSelected(documents.find((item) => item.id === id) ?? null)} busy={busy}
          refresh={() => void perform(refreshDocuments)} create={(title) => void perform(async () => {
            const { document } = await api<{ document: DocumentSummary }>('documents', 'POST', { title });
            setDocuments((old) => [document, ...old]);
            setSelected(document);
          })} />
        <Drafts drafts={drafts} documents={documents} exportDraft={(draft) => void perform(async () => {
          const active = cacheRef.current;
          if (!active) return;
          const { text, incomplete } = await readDraftText(active, draft.scope);
          if (cacheRef.current !== active) return;
          downloadText(`draft-${draft.documentId}-${draft.revision}`, text);
          if (incomplete) setProblem('端末に残っていた下書きのうち、復元できた本文を書き出しました。');
        })} />
      </div>
      {selected && cache ? <div className="document" data-document-id={selected.id}>
        <Room key={`${selected.id}:${retry}`} document={current ?? selected} user={user} cache={cache}
          draftsChanged={draftsChanged} reconnect={reconnect} reauthenticate={loadSession} />
        {current && <Sharing document={current} user={user} accounts={session.accounts} busy={busy}
          remove={() => void perform(async () => {
            await api(`documents/${current.id}`, 'DELETE');
            setDocuments((old) => old.filter((item) => item.id !== current.id));
          })}
          update={(id, role) => void perform(async () => {
            const { document } = await api<{ document: DocumentSummary }>(`documents/${current.id}/access/${id}`, 'PUT', { role });
            setDocuments((old) => old.map((item) => item.id === document.id ? document : item));
          })} />}
      </div> : <section className="empty-page"><h1>文書を開く</h1>
        <p>一覧から選ぶか、新しい文書を作成してください。</p></section>}
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);

import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import type { Access, DocumentSummary, User } from '../shared/protocol.ts';
import type { RoomStatus } from './room.ts';
import { errorText, roleLabel } from './words.ts';
import './style.css';

type Session = { user: User | null; accounts: User[] };
const initialStatus: RoomStatus = { connection: 'joining', sequence: null, role: null, users: [], problem: '' };

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

function Room({ document, user, reconnect }: { document: DocumentSummary; user: User; reconnect: () => void }) {
  const [status, setStatus] = useState(initialStatus);
  const editor = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let cancelled = false;
    let disconnect: (() => void) | undefined;
    void import('./room.ts').then(({ openRoom }) => {
      if (!cancelled && editor.current) disconnect = openRoom(document.id, user, editor.current, initialStatus, setStatus);
    }).catch(() => {
      if (!cancelled) setStatus({ ...initialStatus, connection: 'offline', problem: '編集画面を読み込めませんでした。接続し直してください。' });
    });
    return () => { cancelled = true; disconnect?.(); };
  }, [document.id, user]);

  return <>
    <article className="page" aria-label="文書編集">
      <header className="page-heading">
        <h1>{document.title}</h1>
        <div className="page-details">
          <span role="status" data-connection={status.connection} className="connection">
            {status.connection === 'online' ? '接続中' : status.connection === 'joining' ? '接続を確認中' : '未接続'}
          </span>
          <span>{roleLabel[status.role ?? document.role]}</span>
          {status.sequence !== null && <span>受理順番号 <output aria-label="受理順番号">{status.sequence}</output></span>}
        </div>
      </header>
      {status.problem && <div className="room-error"><p role="alert">{status.problem}</p>
        <button onClick={reconnect}>接続し直す</button></div>}
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

function Sharing({ document, user, accounts, update, busy }: {
  document: DocumentSummary; user: User; accounts: User[]; update: (id: string, role: Access) => void; busy: boolean;
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
  </section>;
}

function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const current = documents.find((document) => document.id === selected);

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
  }

  function loadSession() {
    void perform(async () => {
      const data = await api<Session>('session');
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
      <div className="current-user"><span>{user.name}</span><button disabled={busy} onClick={() => void perform(async () => {
        await api('session', 'DELETE');
        setDocuments([]);
        setSelected(null);
        setSession({ ...session, user: null });
      })}>ログアウト</button></div>
    </header>
    {problem && <p role="alert" className="app-error">{problem}</p>}
    <main className="desk">
      <DocumentList documents={documents} selected={selected} choose={setSelected} busy={busy}
        refresh={() => void perform(refreshDocuments)} create={(title) => void perform(async () => {
          const { document } = await api<{ document: DocumentSummary }>('documents', 'POST', { title });
          setDocuments((old) => [document, ...old]);
          setSelected(document.id);
        })} />
      {current ? <div className="document" data-document-id={current.id}>
        <Room key={`${current.id}:${retry}`} document={current} user={user} reconnect={() => setRetry(retry + 1)} />
        <Sharing document={current} user={user} accounts={session.accounts} busy={busy}
          update={(id, role) => void perform(async () => {
            const { document } = await api<{ document: DocumentSummary }>(`documents/${current.id}/access/${id}`, 'PUT', { role });
            setDocuments((old) => old.map((item) => item.id === document.id ? document : item));
          })} />
      </div> : <section className="empty-page"><h1>文書を開く</h1>
        <p>一覧から選ぶか、新しい文書を作成してください。</p></section>}
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);

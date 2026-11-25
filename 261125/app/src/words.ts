import type { Role } from '../shared/protocol.ts';

export const roleLabel: Record<Role, string> = { owner: '所有者', editor: '編集者', viewer: '閲覧者' };

const errors: Record<string, string> = {
  LOGIN_FAILED: 'アカウントまたはパスワードを確認してください。',
  SESSION_EXPIRED: 'セッションの有効期限が切れました。ログインし直してください。',
  ACCESS_DENIED: '文書を開く権限がありません。',
  GRANT_CHANGED: '共有設定が変わりました。文書に接続し直してください。',
  WRITE_FORBIDDEN: 'この文書は閲覧専用です。',
  TEXT_LIMIT: '本文が上限を超えました。本文を控えてから接続し直してください。',
  UPDATE_LIMIT: '更新が大きすぎます。本文を控えてから接続し直してください。',
  STATE_LIMIT: '文書のデータが上限を超えました。本文を控えてから接続し直してください。',
  INVALID_TITLE: '文書のタイトルを1〜80文字で入力してください。',
  INVALID_UPDATE: '更新を反映できませんでした。本文を控えてから接続し直してください。',
  MISSING_DEPENDENCY: '一部の更新が届いていません。本文を控えてから接続し直してください。',
  RATE_LIMIT: '更新が短時間に集中しました。本文を控え、時間をおいて接続し直してください。',
};

export function errorText(code: string) {
  return errors[code] ?? '操作を完了できませんでした。接続を確認してからやり直してください。';
}

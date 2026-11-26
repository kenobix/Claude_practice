import type { Role } from '../shared/protocol.ts';

export const roleLabel: Record<Role, string> = { owner: '所有者', editor: '編集者', viewer: '閲覧者' };

const errors: Record<string, string> = {
  LOGIN_FAILED: 'アカウントまたはパスワードを確認してください。',
  SESSION_EXPIRED: 'セッションの有効期限が切れました。ログインし直してください。',
  ACCESS_DENIED: '文書を開く権限がありません。',
  DOCUMENT_DELETED: '文書が削除されました。この文書への同期を停止しました。',
  GRANT_CHANGED: '共有設定が変わりました。文書に接続し直してください。',
  WRITE_FORBIDDEN: 'この文書は閲覧専用です。',
  TEXT_LIMIT: '本文が上限を超えました。端末に残った下書きを書き出せます。',
  UPDATE_LIMIT: '更新が上限を超えました。端末に残った下書きを書き出せます。',
  STATE_LIMIT: '文書のデータが上限を超えました。端末に残った下書きを書き出せます。',
  FRAME_LIMIT: '通信メッセージが上限を超えました。同期を停止しました。',
  INVALID_TITLE: '文書のタイトルを1〜80文字で入力してください。',
  INVALID_UPDATE: '更新を反映できませんでした。',
  MISSING_DEPENDENCY: '一部の更新が届いていません。接続し直して必要な更新を送ってください。',
  RATE_LIMIT: '更新が短時間に集中したため、少し待ってから自動で再送します。',
  STORAGE_RECOVERY: 'サーバーが保存状態を確認しています。時間をおいて接続し直してください。',
  MESSAGE_ID_CONFLICT: '同じ更新IDに異なる内容が送られました。同期を停止しました。',
  SYNC_SEQUENCE: '更新の順序を確認できませんでした。接続し直してください。',
  LOCAL_CACHE: '端末内のデータを読み込めませんでした。',
};

export function errorText(code: string) {
  return errors[code] ?? '操作を完了できませんでした。接続を確認してからやり直してください。';
}

export function draftReason(code: string) {
  if (code === 'GRANT_CHANGED') return '共有設定の変更前に作成した下書きです。';
  return errorText(code);
}

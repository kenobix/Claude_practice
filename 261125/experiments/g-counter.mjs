export function increment(counter, replica) {
  const next = new Map(counter);
  next.set(replica, (next.get(replica) ?? 0) + 1);
  return next;
}

export function merge(left, right) {
  const merged = new Map(left);
  for (const [replica, count] of right) {
    merged.set(replica, Math.max(merged.get(replica) ?? 0, count));
  }
  return merged;
}

export function total(counter) {
  return [...counter.values()].reduce((sum, count) => sum + count, 0);
}

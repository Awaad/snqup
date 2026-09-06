/**
 * The offline outbox.
 *
 * This module exists because TanStack Query is NOT an offline queue, and using
 * it as one loses exchanges (ADR-0026).
 *
 * TanStack's mutation retry and persistence are designed for "this request will
 * probably succeed shortly". The requirement here is different in kind:
 *
 *   - an exchange happened in a basement with no signal
 *   - hold it durably across app restart and force-quit
 *   - send it in order, hours later if necessary
 *   - never send it twice
 *
 * An exchange lost to a network failure fails in front of another person, at the
 * exact moment the product is supposed to prove itself. That is the one failure
 * this product cannot have.
 *
 * Boundary: TanStack reads local state. This module reconciles local against
 * server. Keep that sharp, or there are two sources of truth.
 */

/** Operations that can be queued. Extend deliberately, not casually. */
export type OutboxKind =
  | 'exchange.create'
  | 'connection.note.update'
  | 'connection.tags.update'
  | 'connection.reminder.set'
  | 'card.update';

export interface OutboxEntry {
  /**
   * Client-generated UUIDv7, and this BECOMES the server id (ADR-0010).
   *
   * That is what makes sync idempotent by construction rather than by
   * reconciliation: a retry carries the same id, so a duplicate insert is a
   * no-op instead of a second row.
   */
  readonly id: string;

  /**
   * Sent as `Idempotency-Key`. Held 24h server-side in Valkey.
   * Mandatory on every mutation - offline retry makes duplicates the normal
   * case, not an edge case (contracts/api-conventions.md).
   */
  readonly idempotencyKey: string;

  readonly kind: OutboxKind;
  readonly payload: unknown;

  readonly createdAt: string;
  /** Drives exponential backoff. */
  readonly attempts: number;
  readonly lastAttemptAt: string | null;
  readonly lastError: string | null;
}

export interface Outbox {
  /**
   * Durably enqueue. Must survive force-quit: the write completes before this
   * resolves. Do not batch, do not defer.
   */
  enqueue(entry: Omit<OutboxEntry, 'attempts' | 'lastAttemptAt' | 'lastError'>): Promise<void>;

  /**
   * Dispatch pending entries in insertion order.
   *
   * Order matters: a note update for a connection that has not synced yet must
   * not be sent first. Stop on the first entry that fails so ordering holds.
   */
  flush(): Promise<void>;

  /** Surfaced to the user as a pending indicator. Never hide queued work. */
  pendingCount(): Promise<number>;

  /** For the sync status screen. */
  list(): Promise<readonly OutboxEntry[]>;
}

/**
 * Conflict resolution (ADR-0016).
 *
 * Deliberately different per field type. A single record-level last-write-wins
 * would silently destroy notes when someone edits on two devices, and notes are
 * the highest-value user-authored data in the product.
 */
export type ConflictStrategy =
  /** Structured card fields. Per FIELD, not per record: editing a phone number
   *  on one device must not revert a headline changed on another. */
  | { kind: 'field-last-write-wins' }
  /** Notes. Both texts preserved, marker shown, user resolves.
   *  Visible complexity beats invisible data loss. */
  | { kind: 'merge-with-marker' }
  /** Tags. Removals tombstoned so a delete is not resurrected by a stale add. */
  | { kind: 'set-union' }
  /** Entitlements, event membership, connection existence. Never merged. */
  | { kind: 'server-authoritative' };

export const CONFLICT_STRATEGY: Record<string, ConflictStrategy> = {
  'card.fields': { kind: 'field-last-write-wins' },
  'connection.note': { kind: 'merge-with-marker' },
  'connection.tags': { kind: 'set-union' },
  entitlements: { kind: 'server-authoritative' },
  'event.membership': { kind: 'server-authoritative' },
  'connection.existence': { kind: 'server-authoritative' },
};

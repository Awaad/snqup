/**
 * Hand-written surface over the generated types.
 *
 * `generated.ts` is produced from the API's openapi.json and must never be
 * edited by hand (ADR-0014). This file is reviewed normally.
 */

export type { paths, components, operations } from './generated.js';

/**
 * Error envelope from contracts/api-conventions.md.
 *
 * `code` is what clients switch on and render from their own locale files.
 * `message` is developer-facing and MUST NOT be shown to a user (ADR-0011).
 */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
    request_id: string | null;
  };
}

export class ApiError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    readonly details: Record<string, unknown> = {},
    readonly requestId: string | null = null,
  ) {
    super(code);
    this.name = 'ApiError';
  }
}

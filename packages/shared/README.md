# @acme/shared

Cross-platform logic with **no I/O and no platform assumptions**. Validation schemas,
formatting helpers, pure functions.

If it needs React, a network call, or a filesystem, it does not belong here.

This exists so `apps/web` and `apps/mobile` can share validation without importing each
other, which is forbidden (ADR-0014).

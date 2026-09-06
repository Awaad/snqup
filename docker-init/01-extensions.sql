-- Mirrors schema/schema.sql. Extensions must exist before migrations run.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";
-- Unused in v1. Event-scoped discovery will want embeddings for industry
-- similarity (ADR-0023). Enabling later is trivial but easy to forget.
CREATE EXTENSION IF NOT EXISTS "vector";

"""Knowledge: versioned documents, chunks with vector + FTS indexes, FAQ and exact answer cache (LLD-3).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- array_to_string() is only STABLE, so a generated column can't call it directly (D-020)
    CREATE FUNCTION heading_text(text[]) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
      AS $$ SELECT coalesce(array_to_string($1, ' '), '') $$;
    CREATE TABLE kb_documents (doc_id text, version text, title text, doc_type text, family text,
      status text CHECK (status IN ('active','superseded','deleted')), effective_from date, effective_to date,
      supersedes text, storage_uri text, checksum text, language text DEFAULT 'en', source text DEFAULT 'seed',
      created_at timestamptz DEFAULT now(), PRIMARY KEY (doc_id, version));
    -- FR-KB-05: at most one active version per document (D-020)
    CREATE UNIQUE INDEX kb_documents_one_active ON kb_documents (doc_id) WHERE status = 'active';
    CREATE TABLE kb_chunks (id bigserial PRIMARY KEY, doc_id text, version text, ord int, heading_path text[],
      anchor text, content text, token_count int, metadata jsonb, embedding vector(768),
      tsv tsvector GENERATED ALWAYS AS
        (to_tsvector('english', heading_text(heading_path) || ' ' || coalesce(content, ''))) STORED,
      FOREIGN KEY (doc_id, version) REFERENCES kb_documents ON DELETE CASCADE);
    CREATE INDEX kb_chunks_hnsw ON kb_chunks USING hnsw (embedding vector_cosine_ops);
    CREATE INDEX kb_chunks_tsv ON kb_chunks USING gin (tsv);
    CREATE INDEX kb_chunks_family ON kb_chunks ((metadata->>'family'));
    CREATE INDEX kb_chunks_doc ON kb_chunks (doc_id, version, ord);

    CREATE TABLE faq_entries (id text PRIMARY KEY, category text, question text, answer text, source_doc text,
      status text DEFAULT 'active', origin text DEFAULT 'seed', created_at timestamptz DEFAULT now());
    CREATE TABLE faq_variants (id bigserial PRIMARY KEY, faq_id text REFERENCES faq_entries ON DELETE CASCADE,
      text text, normalized text, embedding vector(768));
    CREATE INDEX faq_variants_hnsw ON faq_variants USING hnsw (embedding vector_cosine_ops);
    CREATE UNIQUE INDEX faq_variants_normalized ON faq_variants (normalized);
    CREATE TABLE answer_cache (key text PRIMARY KEY, answer jsonb, source_docs text[], hits int DEFAULT 0,
      created_at timestamptz DEFAULT now(), expires_at timestamptz);   -- exact cache (public answers only)
    CREATE INDEX answer_cache_source_docs ON answer_cache USING gin (source_docs);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS answer_cache, faq_variants, faq_entries, kb_chunks, kb_documents; "
               "DROP FUNCTION IF EXISTS heading_text(text[]);")

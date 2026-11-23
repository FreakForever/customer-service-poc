"""Core: extensions, ops tables, catalog mirror, conversations and messages (LLD-3).

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;

    -- ops (seeded from mock-data/data/ops)
    CREATE TABLE customers (id text PRIMARY KEY, company text NOT NULL, industry text, city text,
      service_centre text, email_domain text, contacts jsonb NOT NULL DEFAULT '[]', created_at date);
    CREATE INDEX customers_email_domain ON customers (email_domain);
    CREATE TABLE assets (serial_no text PRIMARY KEY, model text NOT NULL, family text NOT NULL,
      customer_id text REFERENCES customers(id), dispatched_on date, commissioned_on date,
      warranty_end date, site_city text, running_hours int);
    CREATE INDEX assets_customer ON assets (customer_id);
    CREATE TABLE amc_contracts (id text PRIMARY KEY, customer_id text REFERENCES customers(id), plan text,
      start_date date, end_date date, status text, serials text[], visits_used int, price_inr int);
    CREATE TABLE tickets (id text PRIMARY KEY, customer_id text REFERENCES customers(id), serial_no text,
      model text, error_code text, priority text CHECK (priority IN ('P1','P2','P3')), status text,
      channel text, opened_at timestamptz, assigned_engineer text, scheduled_visit date, summary text,
      history jsonb DEFAULT '[]', conversation_id uuid);
    CREATE INDEX tickets_customer ON tickets (customer_id);
    CREATE SEQUENCE ticket_seq START 141;  -- new ids SR-2026-00141...
    CREATE TABLE parts_orders (id text PRIMARY KEY, customer_id text, lines jsonb, status text, placed_on date,
      dispatch_from text, awb text);
    CREATE TABLE leads (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), conversation_id uuid, customer_id text,
      product_interest text, application text, throughput text, location text, contact jsonb,
      status text DEFAULT 'new', created_at timestamptz DEFAULT now());

    -- catalog mirror (for tools & consistency checks; KG is the reasoning store)
    CREATE TABLE models (id text PRIMARY KEY, family text, name text, specs jsonb);
    CREATE TABLE error_codes (id text PRIMARY KEY, family text, code text, title text, display_text text,
      severity text, safety_stop bool, causes jsonb, source text DEFAULT 'seed');
    CREATE TABLE spare_parts (part_no text PRIMARY KEY, family text, name text, price_inr int, consumable bool,
      fits_models text[], stock jsonb, lead_time_days int, source text DEFAULT 'seed');

    -- conversations (FR-CH-02: state jsonb carries active agent, entities and open slots across turns)
    CREATE TABLE conversations (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), channel text, customer_id text,
      external_thread_id text, subject text, status text DEFAULT 'bot', -- bot | handoff | human | review | closed
      active_agent text, state jsonb DEFAULT '{}', assigned_to text, priority text,
      created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(), expires_at timestamptz);
    CREATE UNIQUE INDEX conversations_thread ON conversations (channel, external_thread_id);
    CREATE INDEX conversations_expires ON conversations (expires_at);
    CREATE TABLE messages (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      conversation_id uuid REFERENCES conversations ON DELETE CASCADE,
      role text CHECK (role IN ('customer','assistant','agent','system')), content text, citations jsonb,
      actions jsonb, external_message_id text UNIQUE, trace_id uuid, delivery text DEFAULT 'sent', -- draft|sent|failed
      created_at timestamptz DEFAULT now());
    CREATE INDEX messages_conversation ON messages (conversation_id, created_at);
    CREATE TABLE attachments (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      message_id uuid REFERENCES messages ON DELETE CASCADE, filename text, mime text, bytes int, storage_uri text);
    CREATE TABLE mailbox_messages (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      box text CHECK (box IN ('inbox','outbox')), message_id text UNIQUE, in_reply_to text, references_ text,
      from_addr text, to_addr text, subject text, body_text text, body_html text, processed bool DEFAULT false,
      created_at timestamptz DEFAULT now());  -- demo mailbox (D-016)
    CREATE TABLE feedback (id bigserial PRIMARY KEY, message_id uuid, trace_id uuid, rating smallint, reason text,
      created_at timestamptz DEFAULT now());
    """)


def downgrade() -> None:
    # Extensions stay: they are shared and also created by infra/gcp/db_init.py.
    op.execute("""
    DROP TABLE IF EXISTS feedback, mailbox_messages, attachments, messages, conversations,
      spare_parts, error_codes, models, leads, parts_orders, tickets, amc_contracts, assets, customers;
    DROP SEQUENCE IF EXISTS ticket_seq;
    """)

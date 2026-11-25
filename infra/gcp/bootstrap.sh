#!/usr/bin/env bash
# Idempotent GCP bootstrap for one environment (LLD-18, D-015). Safe to re-run: every resource is
# checked before it is created, and secrets are only generated when missing.
#
#   ENV=dev bash infra/gcp/bootstrap.sh
#
# Optional inputs:
#   GCP_PROJECT        defaults to `gcloud config get-value project`
#   NEO4J_CREDS_FILE   AuraDB credentials .txt (NEO4J_URI/USERNAME/PASSWORD/DATABASE) — loaded into Secret
#                      Manager only when the secret is missing, or always with NEO4J_FORCE=1 (after rotation)
#   BUDGET_AMOUNT      monthly budget in the billing account currency; budget alert skipped when unset
#   MAIL_ADAPTER=gmail also creates the mcs-gmail Pub/Sub topic
set -euo pipefail

ENV="${ENV:-dev}"
PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GCP_REGION:-asia-south1}"
SQL_INSTANCE="mcs-${ENV}"
SQL_TIER="${SQL_TIER:-db-f1-micro}"
BUCKET="${GCS_BUCKET:-mcs-${ENV}-uploads}"
AR_REPO="mcs"
P="mcs-${ENV}"   # secret name prefix
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

[[ -n "$PROJECT" ]] || { echo "No GCP project: set GCP_PROJECT or gcloud config set project" >&2; exit 1; }
[[ "$ENV" == dev || "$ENV" == demo ]] || { echo "ENV must be dev or demo" >&2; exit 1; }
g() { gcloud --project "$PROJECT" --quiet "$@"; }
say() { printf '\n== %s\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }

say "Project $PROJECT · env $ENV · region $REGION"

say "APIs"
g services enable run.googleapis.com sqladmin.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com secretmanager.googleapis.com aiplatform.googleapis.com storage.googleapis.com \
  pubsub.googleapis.com cloudscheduler.googleapis.com gmail.googleapis.com iam.googleapis.com \
  billingbudgets.googleapis.com

say "Artifact Registry: $AR_REPO"
exists g artifacts repositories describe "$AR_REPO" --location "$REGION" \
  || g artifacts repositories create "$AR_REPO" --repository-format docker --location "$REGION" \
       --description "Matsui CS POC images"

say "Cloud Storage: gs://$BUCKET"
exists g storage buckets describe "gs://$BUCKET" \
  || g storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access \
       --public-access-prevention

say "Service accounts"
sa() { echo "$1@$PROJECT.iam.gserviceaccount.com"; }
for name in mcs-api mcs-worker mcs-web mcs-build; do
  exists g iam service-accounts describe "$(sa $name)" \
    || g iam service-accounts create "$name" --display-name "Matsui CS $name"
done
bind() {  # bind <sa-name> <role>...
  local who="serviceAccount:$(sa "$1")"; shift
  for role in "$@"; do
    g projects add-iam-policy-binding "$PROJECT" --member "$who" --role "$role" --condition None >/dev/null
  done
}
RUNTIME_ROLES=(roles/cloudsql.client roles/aiplatform.user roles/secretmanager.secretAccessor
               roles/storage.objectAdmin roles/logging.logWriter)
bind mcs-api "${RUNTIME_ROLES[@]}"
bind mcs-worker "${RUNTIME_ROLES[@]}"
bind mcs-web roles/logging.logWriter
bind mcs-build roles/run.admin roles/iam.serviceAccountUser roles/artifactregistry.writer \
  roles/logging.logWriter roles/storage.objectViewer roles/cloudsql.client roles/secretmanager.secretAccessor

say "Secrets ($P-*)"
secret_exists() { exists g secrets versions access latest --secret "$1"; }
put_secret() {  # put_secret <name> <value>  — creates the secret if needed, adds a version
  exists g secrets describe "$1" || g secrets create "$1" --replication-policy automatic >/dev/null
  printf '%s' "$2" | g secrets versions add "$1" --data-file=- >/dev/null
  echo "  set $1"
}
ensure_random() { secret_exists "$1" && echo "  ok  $1" || put_secret "$1" "$(openssl rand -hex 24)"; }
ensure_random "$P-db-password"
ensure_random "$P-db-ro-password"
ensure_random "$P-nextauth-secret"
# the web app reads only its own secrets, not the whole project's
g secrets add-iam-policy-binding "$P-nextauth-secret" --member "serviceAccount:$(sa mcs-web)" --role roles/secretmanager.secretAccessor >/dev/null

if [[ -n "${NEO4J_CREDS_FILE:-}" ]]; then
  [[ -r "$NEO4J_CREDS_FILE" ]] || { echo "Cannot read NEO4J_CREDS_FILE=$NEO4J_CREDS_FILE" >&2; exit 1; }
  cred() { grep -E "^$1=" "$NEO4J_CREDS_FILE" | head -1 | cut -d= -f2-; }
  for pair in uri:NEO4J_URI user:NEO4J_USERNAME password:NEO4J_PASSWORD database:NEO4J_DATABASE; do
    key="${pair%%:*}"; var="${pair#*:}"; val="$(cred "$var")"
    [[ -n "$val" ]] || { [[ "$key" == database ]] && val="neo4j" || { echo "$var missing" >&2; exit 1; }; }
    if [[ "${NEO4J_FORCE:-0}" == 1 ]] || ! secret_exists "$P-neo4j-$key"; then put_secret "$P-neo4j-$key" "$val"
    else echo "  ok  $P-neo4j-$key"; fi
  done
else
  for key in uri user password database; do
    secret_exists "$P-neo4j-$key" && echo "  ok  $P-neo4j-$key" \
      || echo "  MISSING $P-neo4j-$key — create AuraDB Free and re-run with NEO4J_CREDS_FILE=<credentials.txt>"
  done
fi

say "Cloud SQL: $SQL_INSTANCE (Postgres 16, $SQL_TIER)"
if ! exists g sql instances describe "$SQL_INSTANCE"; then
  echo "  creating (takes ~10 minutes)…"
  g sql instances create "$SQL_INSTANCE" --database-version POSTGRES_16 --edition ENTERPRISE \
    --tier "$SQL_TIER" --region "$REGION" --storage-type SSD --storage-size 10 --storage-auto-increase \
    --no-backup --deletion-protection
fi
for db in mcs mcs_test; do
  exists g sql databases describe "$db" --instance "$SQL_INSTANCE" \
    || g sql databases create "$db" --instance "$SQL_INSTANCE"
done
DB_PW="$(g secrets versions access latest --secret "$P-db-password")"
# `users set-password` is idempotent and keeps the user's password equal to the secret.
if g sql users list --instance "$SQL_INSTANCE" --format 'value(name)' | grep -qx mcs; then
  g sql users set-password mcs --instance "$SQL_INSTANCE" --password "$DB_PW" >/dev/null
else
  g sql users create mcs --instance "$SQL_INSTANCE" --password "$DB_PW" >/dev/null
fi
echo "  user mcs ok"

say "Database init: vector extension + read-only role mcs_ro"
DB_RO_PW="$(g secrets versions access latest --secret "$P-db-ro-password")"
(cd "$ROOT/services/agent" && \
  CLOUDSQL_INSTANCE="$PROJECT:$REGION:$SQL_INSTANCE" DB_PASSWORD="$DB_PW" DB_RO_PASSWORD="$DB_RO_PW" \
  uv run --quiet python "$HERE/db_init.py" mcs mcs_test)

if [[ "${MAIL_ADAPTER:-demo}" == gmail ]]; then
  say "Pub/Sub: mcs-gmail"
  exists g pubsub topics describe mcs-gmail || g pubsub topics create mcs-gmail
fi

say "Budget alert"
if [[ -n "${BUDGET_AMOUNT:-}" ]]; then
  BILLING="$(g billing projects describe "$PROJECT" --format 'value(billingAccountName)' | sed 's#billingAccounts/##')"
  NAME="mcs-$ENV-$PROJECT"
  if g billing budgets list --billing-account "$BILLING" --format 'value(displayName)' 2>/dev/null | grep -qx "$NAME"; then
    echo "  ok  $NAME"
  else
    g billing budgets create --billing-account "$BILLING" --display-name "$NAME" \
      --budget-amount "$BUDGET_AMOUNT" --filter-projects "projects/$PROJECT" \
      --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0
  fi
else
  echo "  skipped (set BUDGET_AMOUNT to create one)"
fi

say "Done — next: make env && make dev"

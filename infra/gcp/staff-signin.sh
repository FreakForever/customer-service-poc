#!/usr/bin/env bash
# Store the Google OAuth client and the staff allow-list in Secret Manager (asked for here, never on the command line or in git),
# and let only the web service read them. Then deploy again.   ENV=dev bash infra/gcp/staff-signin.sh
set -euo pipefail
P="mcs-${ENV:-dev}"
PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
put() {
  gcloud --project "$PROJECT" secrets describe "$P-$1" >/dev/null 2>&1 || gcloud --project "$PROJECT" secrets create "$P-$1" --replication-policy automatic
  printf '%s' "$2" | gcloud --project "$PROJECT" secrets versions add "$P-$1" --data-file=- >/dev/null
  gcloud --project "$PROJECT" secrets add-iam-policy-binding "$P-$1" --member "serviceAccount:mcs-web@$PROJECT.iam.gserviceaccount.com" \
    --role roles/secretmanager.secretAccessor >/dev/null
  echo "stored $P-$1"
}
read -rp  "OAuth client ID: " id
read -rsp "OAuth client secret (not shown): " secret; echo
read -rp  "Staff (for example admin:you@example.com,agent:colleague@example.com): " staff
[[ "$id" == *.apps.googleusercontent.com && -n "$secret" && "$staff" == *@* ]] || { echo "check the three values" >&2; exit 1; }
put google-client-id "$id"; put google-client-secret "$secret"; put staff-allowlist "$staff"

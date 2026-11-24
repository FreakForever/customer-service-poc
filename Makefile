# Matsui CS POC — see LLD-18. No local Docker: images build on Cloud Build, services run on GCP.
-include .env
export

ENV ?= dev
# cloud-sql-proxy from PATH, else the copy `gcloud components install cloud-sql-proxy` puts next to gcloud.
PROXY := $(shell command -v cloud-sql-proxy 2>/dev/null || python3 -c "import os,shutil;print(os.path.join(os.path.dirname(os.path.realpath(shutil.which('gcloud'))),'cloud-sql-proxy'))")
TAG :=$(shell git rev-parse --short HEAD 2>/dev/null)$(shell git diff --quiet HEAD 2>/dev/null || echo -dirty)

.PHONY: bootstrap-gcp env proxy dev api-dev worker-dev web-dev migrate kg-load seed seed-kb eval-retrieval eval-router eval-cache eval-t2 eval-transactional eval-judge graph test ci deploy-dev deploy-demo \
        eval demo-emails demo-upload docs

bootstrap-gcp: ; ENV=dev bash infra/gcp/bootstrap.sh
env:          ; bash infra/gcp/env.sh dev > .env && echo "wrote .env"
proxy:        ; $(PROXY) $$CLOUDSQL_INSTANCE --port 5432
dev:          ; $(MAKE) -j4 proxy api-dev worker-dev web-dev
api-dev:      ; cd services/agent && uv run uvicorn app.main:app --reload --port 8000
worker-dev:   ; cd services/agent && uv run python -m app.worker
web-dev:      ; DEMO_STAFF_LOGIN=true NEXTAUTH_URL=http://localhost:3000 pnpm -C apps/web dev
migrate:      ; cd services/agent && uv run alembic upgrade head
graph:        ; cd services/agent && uv run python ../../scripts/export_graph.py
eval-router:  ; cd services/agent && uv run python ../../eval/router.py
eval-t2:      ; cd services/agent && uv run python ../../eval/t2.py
eval-transactional: ; cd services/agent && uv run python ../../eval/transactional.py
eval-judge:   ; cd services/agent && uv run python ../../eval/judge.py
eval-cache:   ; cd services/agent && uv run python ../../eval/cache.py
eval-retrieval: ; cd services/agent && uv run python ../../eval/retrieval.py
seed-kb:      ; cd services/agent && uv run python ../../scripts/seed.py --kb
kg-load:      ; cd services/agent && uv run python ../../mock-data/kg/load_kg.py --wipe
seed: migrate ; uv run --no-project python mock-data/generate.py && cd services/agent && uv run python ../../scripts/seed.py && uv run python ../../mock-data/kg/load_kg.py --wipe && uv run python ../../scripts/seed.py --kb
test:         ; cd services/agent && uv run pytest -q && pnpm -C apps/web test
ci:           ; gcloud builds submit --config infra/gcp/cloudbuild.ci.yaml --region asia-south1 --ignore-file=infra/gcp/ci.gcloudignore .
deploy-dev:   ; gcloud builds submit --config infra/gcp/cloudbuild.deploy.yaml --region asia-south1 --substitutions=_ENV=dev,_TAG=$(TAG) .
deploy-demo:  ; gcloud builds submit --config infra/gcp/cloudbuild.deploy.yaml --region asia-south1 --substitutions=_ENV=demo,_TAG=$(TAG) .
eval:         ; uv run python eval/run.py --mode all
demo-emails:  ; cd services/agent && uv run python ../../scripts/demo_emails.py
demo-upload:  ; uv run python scripts/demo_upload.py mock-data/upload-samples
docs:         ; uv run --no-project --with-requirements tools/requirements.txt python tools/sync_docs.py

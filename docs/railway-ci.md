# Backend deployment without GitHub-hosted runners

`ci/Dockerfile.verified-backend` composes the existing backend and medical image
recipes with mandatory validation stages. Generate it with
`python ci/render-verified-dockerfile.py` after either recipe changes.

The mapping in `ci/railway-ci-manifest.json` runs the test, compilation and safety
assertion commands directly from all seven production push workflows. Workflow
hashes and the complete production workflow inventory are checked first: adding
or changing a workflow requires reviewing its migration mapping. Python 3.11,
Python 3.12 for Flash, and PostgreSQL 16 match the original jobs. Each workflow
gets a separate disposable database. A failure, timeout, skipped test or empty
test result stops the build. No test receives deployment secrets or production
database access. GitHub Actions files remain available for normal use.

The medical worker image recipe is included verbatim as a build stage. Its
fail-closed authenticated health check runs within that image; no model weights
or GPU inference are used. Docker-in-Docker is unnecessary. The final backend
image does not contain PostgreSQL, test environments or the CUDA image.

Git checkout/setup/install steps are supplied by Railway's immutable source and
the build stages. Stage 8 acceptance runs with immediate failure propagation;
its GitHub status-publication step is not executed or impersonated. Railway
build logs contain the independent results. Build receipts bind every stage to
`RAILWAY_GIT_COMMIT_SHA` and the source-file digest. Source paths are enumerated
in `ci/railway-source-files.json`; regenerate that sorted list from `git ls-files`
including new files when the source inventory changes.

Before switching the production service, build and run the receipt verifier on
the disposable Railway validation service. Configure production to use this
Dockerfile and pre-deploy command `python ci/railway-verified-ci.py verify`.
Only after validation succeeds, replace Railway's GitHub-specific Wait for CI
setting with this build/pre-deploy gate. Keep the existing healthcheck and live
deployment until the new release succeeds. A failed build never replaces it.

This removes the dependency on paid/unlocked GitHub Actions. Railway resource
usage, including builds and the Bonsai worker, remains billable to Railway.

The first complete build exposed a separate upstream failure: the RADAR GitHub
repository returned 404. The medical recipe now retrieves the authors' official
Zenodo v3 source archive, DOI `10.5281/zenodo.21504519`, verified against SHA-256
`777dbdccb1b925ef84e08578749fe6ff12cead60cd2f7245447e7ee22d1a854a`.
Its embedded code license is Apache-2.0; the separate model weight restrictions
remain unchanged. The archive cannot prove the former Git commit, so metadata
reports a null commit and the actual archive digest. This source needs new
inference acceptance: `VELIA_MEDICAL_ACCEPTED_SOURCE_SHA256` stays unset and
readiness remains false even with weights present. Do not set it as part of the
Flash rollout. The authenticated health and privacy/licensing tests remain gates.

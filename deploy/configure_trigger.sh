#!/usr/bin/env bash
set -euo pipefail
project=ai-investigation-engine-512912
region=europe-west3
service=ai-investigation-engine
trigger=61c02136-b7c3-4edb-8dc2-7d7c5802d395
repository=cloud-run-source-deploy
build_account="ai-investigation-build@${project}.iam.gserviceaccount.com"
runtime_account=415474464012-compute@developer.gserviceaccount.com
if [[ "${1:-}" != --apply ]]; then
  printf '%s\n' "Prepared setup for EXISTING trigger $trigger ($project, global):" \
    "- Verify its GitHub repository is rohanjebrown/ai-investigation-engine." \
    "- Reuse/create the keyless identity $build_account." \
    '- Grant repository Artifact Registry writer, existing-service Run developer,' \
    '  actAs on the existing runtime identity, and project log writer.' \
    '- Update that trigger to cloudbuild.yaml and ^main$; do not create a trigger.' \
    'Run with --apply only after the deployment guide preflight is complete.'
  exit 0
fi
actual="$(gcloud builds triggers describe "$trigger" --project="$project" --region=global --format='value(github.owner,github.name)' --quiet)"
[[ "$actual" == $'rohanjebrown\tai-investigation-engine' ]] || { echo 'Existing trigger repository differs; refusing changes'; exit 1; }
if ! gcloud iam service-accounts describe "$build_account" --project="$project" --quiet >/dev/null 2>&1; then
  gcloud iam service-accounts create ai-investigation-build --project="$project" --display-name='AI Investigation main-build deployer' --quiet
fi
gcloud artifacts repositories add-iam-policy-binding "$repository" --project="$project" --location="$region" \
  --member="serviceAccount:$build_account" --role=roles/artifactregistry.writer --quiet >/dev/null
gcloud run services add-iam-policy-binding "$service" --project="$project" --region="$region" \
  --member="serviceAccount:$build_account" --role=roles/run.developer --quiet >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$runtime_account" --project="$project" \
  --member="serviceAccount:$build_account" --role=roles/iam.serviceAccountUser --quiet >/dev/null
gcloud projects add-iam-policy-binding "$project" --member="serviceAccount:$build_account" \
  --role=roles/logging.logWriter --condition=None --quiet >/dev/null
gcloud builds triggers update github "$trigger" --project="$project" --region=global \
  --build-config=cloudbuild.yaml --branch-pattern='^main$' \
  --service-account="projects/$project/serviceAccounts/$build_account" --quiet
echo 'Updated the existing main trigger; no build, push, deployment, keys, or billing change was performed.'

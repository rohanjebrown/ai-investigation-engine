"""Fail-closed deployment to an existing service; never create/reset resources."""

import argparse
import json
import re
import subprocess
from copy import deepcopy
from hashlib import sha256
from pathlib import Path


def gcloud_json(arguments):
    result = subprocess.run(["gcloud", *arguments, "--format=json", "--quiet"], capture_output=True, text=True)
    if result.returncode:
        # Raw SDK/CLI output may echo configuration. Keep it out of build logs.
        raise RuntimeError(
            f"gcloud {arguments[0]} operation failed (exit {result.returncode}); check IAM and service readiness"
        )
    return json.loads(result.stdout or "{}")


def validate_commit(branch, commit):
    if branch != "main" or re.fullmatch(r"[0-9a-f]{40}", commit or "") is None:
        raise ValueError("Only a GitHub main push with a full commit SHA may deploy")


def protected_config(service):
    spec = service["spec"]["template"]["spec"]
    annotations = service["spec"]["template"].get("metadata", {}).get("annotations", {})
    return {
        "runtime": {
            key: deepcopy(spec.get(key))
            for key in ("serviceAccountName", "containerConcurrency", "timeoutSeconds", "volumes")
        },
        "containers": [
            {
                key: deepcopy(container.get(key))
                for key in (
                    "name",
                    "command",
                    "args",
                    "env",
                    "ports",
                    "resources",
                    "volumeMounts",
                    "startupProbe",
                    "livenessProbe",
                    "readinessProbe",
                )
            }
            for container in spec["containers"]
        ],
        "revision_annotations": {
            key: value
            for key, value in annotations.items()
            if key not in ("run.googleapis.com/client-name", "run.googleapis.com/client-version")
        },
        "service_annotations": {
            key: service["metadata"].get("annotations", {}).get(key)
            for key in (
                "run.googleapis.com/ingress",
                "run.googleapis.com/maxScale",
                "run.googleapis.com/minScale",
                "run.googleapis.com/invoker-iam-disabled",
            )
        },
        "traffic": service["spec"].get("traffic"),
    }


def validate_service(service, policy, *, container_name, runtime_account, sql_connection):
    spec = service["spec"]["template"]["spec"]
    if spec.get("serviceAccountName") != runtime_account:
        raise ValueError("Existing runtime service account differs from the reviewed identity")
    containers = [item for item in spec["containers"] if item.get("name") == container_name]
    if len(containers) != 1:
        raise ValueError("Existing target container was not found; refusing to create another")
    container = containers[0]
    if container.get("command") or container.get("args"):
        raise ValueError("Existing command/args override the image entry point; review before deployment")
    env = {item["name"]: item for item in container.get("env", [])}
    required = (
        "DB_CONNECTION_MODE",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "GEMINI_API_KEY",
        "GEMINI_GENERATION_MODEL",
        "SERVING_CHUNKING_CONFIGURATION_ID",
        "ADMIN_API_TOKEN",
        "FORWARDED_ALLOW_IPS",
    )
    missing = [
        name for name in required if not env.get(name, {}).get("value") and not env.get(name, {}).get("valueFrom")
    ]
    if missing:
        raise ValueError("Missing runtime configuration: " + ", ".join(missing))
    for name in ("DB_PASSWORD", "GEMINI_API_KEY", "ADMIN_API_TOKEN"):
        if not env[name].get("valueFrom", {}).get("secretKeyRef"):
            raise ValueError(f"{name} must be an existing runtime Secret Manager reference")
    if (
        env["DB_CONNECTION_MODE"].get("value") != "cloudsql_socket"
        or env["DB_HOST"].get("value") != "/cloudsql/" + sql_connection
        or env["DB_PORT"].get("value") != "5432"
    ):
        raise ValueError("Cloud Run must use the reviewed Cloud SQL Unix socket, not development localhost")
    if env["FORWARDED_ALLOW_IPS"].get("value") != "*":
        raise ValueError("Cloud Run proxy trust must be explicitly configured for HTTPS authentication")
    if env.get("ADMIN_REQUIRE_HTTPS", {}).get("value", "true").lower() != "true":
        raise ValueError("Administrator HTTPS enforcement must remain enabled")
    annotations = service["spec"]["template"].get("metadata", {}).get("annotations", {})
    if sql_connection not in annotations.get("run.googleapis.com/cloudsql-instances", "").split(","):
        raise ValueError("Reviewed Cloud SQL attachment is missing")
    service_annotations = service["metadata"].get("annotations", {})
    if service_annotations.get("run.googleapis.com/invoker-iam-disabled", "false") == "true":
        raise ValueError("Public invocation bypass requires a separate aggregate quota-protection design")
    if any(
        member in ("allUsers", "allAuthenticatedUsers")
        for binding in policy.get("bindings", [])
        if binding.get("role") == "roles/run.invoker"
        for member in binding.get("members", [])
    ):
        raise ValueError("Unrestricted public Gemini endpoints are not approved by this pipeline")
    if (
        str(
            service_annotations.get(
                "run.googleapis.com/maxScale", annotations.get("autoscaling.knative.dev/maxScale", "")
            )
        )
        != "1"
    ):
        raise ValueError("A conservative explicit maximum of one instance is required")
    traffic = service["spec"].get("traffic", [])
    if len(traffic) != 1 or not traffic[0].get("latestRevision") or traffic[0].get("percent") != 100:
        raise ValueError("Non-latest/split traffic requires explicit rollout review; it will not be reset")
    return protected_config(service)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("validate", "preflight", "deploy"))
    parser.add_argument("--branch")
    parser.add_argument("--commit")
    for name in (
        "project",
        "region",
        "service",
        "container",
        "runtime-account",
        "sql-connection",
        "image-file",
        "image-prefix",
    ):
        parser.add_argument("--" + name)
    args = parser.parse_args()
    if args.operation == "validate":
        validate_commit(args.branch, args.commit)
        print("Validated main commit identity")
        return
    common = ["--project=" + args.project, "--region=" + args.region]

    def describe():
        return gcloud_json(["run", "services", "describe", args.service, *common])

    service = describe()
    policy = gcloud_json(["run", "services", "get-iam-policy", args.service, *common])
    before = validate_service(
        service,
        policy,
        container_name=args.container,
        runtime_account=args.runtime_account,
        sql_connection=args.sql_connection,
    )
    if args.operation == "preflight":
        print("Existing service runtime/secret/connectivity/private-access preflight passed")
        return
    image = Path(args.image_file).read_text().strip()
    if re.fullmatch(re.escape(args.image_prefix) + r"@sha256:[0-9a-f]{64}", image) is None:
        raise ValueError("Deployment requires the exact pushed Artifact Registry digest")
    previous = service.get("status", {}).get("latestReadyRevisionName")
    print("Previous ready revision:", previous)
    gcloud_json(
        ["run", "services", "update", args.service, *common, "--container=" + args.container, "--image=" + image]
    )
    updated = describe()
    if protected_config(updated) != before:
        raise RuntimeError(
            "Protected service configuration changed; inspect service and roll back traffic before continuing"
        )
    target = next(c for c in updated["spec"]["template"]["spec"]["containers"] if c["name"] == args.container)
    if target["image"] != image or updated.get("status", {}).get("latestCreatedRevisionName") != updated.get(
        "status", {}
    ).get("latestReadyRevisionName"):
        raise RuntimeError("The intended image/revision is not ready")
    digest = sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    print("Ready revision:", updated["status"]["latestReadyRevisionName"])
    print("Deployed immutable image:", image)
    print("Preserved runtime configuration fingerprint:", digest)


if __name__ == "__main__":
    main()

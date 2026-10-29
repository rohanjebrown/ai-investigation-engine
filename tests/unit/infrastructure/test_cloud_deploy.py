"""Deployment gates operate on synthetic configuration, never on cloud resources."""

import importlib.util
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("cloud_deploy", ROOT / "deploy/cloud_run.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


@pytest.fixture
def service():
    env = {
        "DB_CONNECTION_MODE": "cloudsql_socket",
        "DB_HOST": "/cloudsql/project:region:db",
        "DB_PORT": "5432",
        "DB_NAME": "existing",
        "DB_USER": "existing",
        "GEMINI_GENERATION_MODEL": "operator-selected",
        "SERVING_CHUNKING_CONFIGURATION_ID": "7",
        "FORWARDED_ALLOW_IPS": "*",
    }
    values = [{"name": key, "value": value} for key, value in env.items()]
    values += [
        {"name": name, "valueFrom": {"secretKeyRef": {"name": name.lower(), "key": "1"}}}
        for name in ("DB_PASSWORD", "GEMINI_API_KEY", "ADMIN_API_TOKEN")
    ]
    return {
        "metadata": {"annotations": {"run.googleapis.com/maxScale": "1", "run.googleapis.com/ingress": "all"}},
        "spec": {
            "traffic": [{"latestRevision": True, "percent": 100}],
            "template": {
                "metadata": {"annotations": {"run.googleapis.com/cloudsql-instances": "project:region:db"}},
                "spec": {
                    "serviceAccountName": "runtime@example.test",
                    "containerConcurrency": 10,
                    "timeoutSeconds": 300,
                    "containers": [
                        {
                            "name": "existing",
                            "image": "old",
                            "env": values,
                            "ports": [{"containerPort": 8080}],
                            "resources": {"limits": {"cpu": "1000m", "memory": "512Mi"}},
                        }
                    ],
                },
            },
        },
        "status": {"latestReadyRevisionName": "revision-old"},
    }


def validate(service, policy=None):
    return deploy.validate_service(
        service,
        policy or {},
        container_name="existing",
        runtime_account="runtime@example.test",
        sql_connection="project:region:db",
    )


@pytest.mark.parametrize("branch,commit", [("feature", "a" * 40), ("main", "latest"), ("main", "")])
def test_only_main_full_commit_can_deploy(branch, commit):
    with pytest.raises(ValueError, match="main push"):
        deploy.validate_commit(branch, commit)


def test_image_change_is_only_unprotected_runtime_field(service):
    before = validate(service)
    changed = deepcopy(service)
    changed["spec"]["template"]["spec"]["containers"][0]["image"] = "new@sha256:" + "a" * 64
    assert deploy.protected_config(changed) == before
    changed["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = "proxy"
    assert deploy.protected_config(changed) != before


def test_placeholder_missing_configuration_fails_closed(service):
    service["spec"]["template"]["spec"]["containers"][0].pop("env")
    with pytest.raises(ValueError, match="Missing runtime configuration"):
        validate(service)


def test_plaintext_secret_is_rejected_without_echo(service):
    env = service["spec"]["template"]["spec"]["containers"][0]["env"]
    item = next(item for item in env if item["name"] == "DB_PASSWORD")
    item.clear()
    item.update(name="DB_PASSWORD", value="synthetic-private-password")
    with pytest.raises(ValueError, match="Secret Manager") as error:
        validate(service)
    assert "synthetic-private-password" not in str(error.value)


@pytest.mark.parametrize("member", ["allUsers", "allAuthenticatedUsers"])
def test_public_gemini_access_is_rejected(service, member):
    with pytest.raises(ValueError, match="Unrestricted public"):
        validate(service, {"bindings": [{"role": "roles/run.invoker", "members": [member]}]})


def test_rollback_traffic_is_never_silently_reset(service):
    service["spec"]["traffic"] = [{"revisionName": "revision-old", "percent": 100}]
    with pytest.raises(ValueError, match="traffic"):
        validate(service)


def test_cli_failure_redacts_sdk_output(monkeypatch):
    monkeypatch.setattr(
        deploy.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="synthetic-secret-content"),
    )
    with pytest.raises(RuntimeError) as error:
        deploy.gcloud_json(["run", "services", "describe", "existing"])
    assert "synthetic-secret-content" not in str(error.value)


def test_deploy_uses_digest_and_only_updates_existing_image(service, monkeypatch, tmp_path):
    image = "region-docker.pkg.dev/project/repo/service@sha256:" + "a" * 64
    image_file = tmp_path / "image.txt"
    image_file.write_text(image)
    updated = deepcopy(service)
    updated["spec"]["template"]["spec"]["containers"][0]["image"] = image
    updated["status"] = {"latestReadyRevisionName": "revision-new", "latestCreatedRevisionName": "revision-new"}
    replies = iter([service, {}, {}, updated])
    calls = []

    def fake(arguments):
        calls.append(arguments)
        return next(replies)

    monkeypatch.setattr(deploy, "gcloud_json", fake)
    monkeypatch.setattr(
        "sys.argv",
        [
            "cloud_run.py",
            "deploy",
            "--project=project",
            "--region=region",
            "--service=existing",
            "--container=existing",
            "--runtime-account=runtime@example.test",
            "--sql-connection=project:region:db",
            "--image-file=" + str(image_file),
            "--image-prefix=region-docker.pkg.dev/project/repo/service",
        ],
    )
    deploy.main()
    assert calls[2] == [
        "run",
        "services",
        "update",
        "existing",
        "--project=project",
        "--region=region",
        "--container=existing",
        "--image=" + image,
    ]

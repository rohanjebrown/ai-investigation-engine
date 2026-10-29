"""Replace the existing inline build oneof with the repository quality gate."""

import argparse
import json
import subprocess
from urllib.request import Request, urlopen


def gated_trigger(current):
    if (
        current.get("github", {}).get("owner") != "rohanjebrown"
        or current["github"].get("name") != "ai-investigation-engine"
    ):
        raise ValueError("Unexpected repository; refusing trigger change")
    updated = dict(current)
    updated.pop("build", None)
    updated["filename"] = "cloudbuild.yaml"
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gcloud", default="gcloud")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    url = "https://cloudbuild.googleapis.com/v1/projects/ai-investigation-engine-512912/triggers/61c02136-b7c3-4edb-8dc2-7d7c5802d395"
    token = subprocess.run(
        [args.gcloud, "auth", "print-access-token"], capture_output=True, text=True, check=True
    ).stdout.strip()
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        current = json.load(response)
    updated = gated_trigger(current)
    if not args.apply:
        print("Preview: replace existing inline build with cloudbuild.yaml; retain GitHub, identity and substitutions")
        return
    request = Request(
        url + "?updateMask=build,filename", method="PATCH", headers=headers, data=json.dumps(updated).encode()
    )
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    assert result.get("filename") == "cloudbuild.yaml" and not result.get("build")
    assert result["github"] == current["github"] and result["serviceAccount"] == current["serviceAccount"]
    print("Verified existing trigger now uses cloudbuild.yaml; inline deploy removed. No build/deployment started.")


if __name__ == "__main__":
    main()

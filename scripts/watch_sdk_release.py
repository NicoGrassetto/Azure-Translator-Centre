"""Open an issue for Copilot cloud agent when PyPI has a newer stable Translator SDK.

Run by .github/workflows/watch-sdk-releases.yml. Compares the pin in requirements.txt with the latest
stable, non-yanked release on PyPI. When that release is newer and no issue tracks it yet, opens an
issue describing the upgrade and assigns it to Copilot, which opens a pull request.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from packaging.utils import InvalidSdistFilename, InvalidWheelFilename, parse_sdist_filename, parse_wheel_filename
from packaging.version import Version

PACKAGE = "azure-ai-translation-text"
LABEL = "sdk-release"
COPILOT = "copilot-swe-agent[bot]"
REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"


def pinned_version() -> Version:
    pin = re.search(
        rf"^\s*{re.escape(PACKAGE)}\s*==\s*([^\s;#]+)",
        REQUIREMENTS.read_text(encoding="utf-8"),
        re.IGNORECASE | re.MULTILINE,
    )
    if pin is None:
        sys.exit(f"{REQUIREMENTS.name} doesn't pin {PACKAGE}==<version>")
    return Version(pin.group(1))


def file_version(filename: str) -> Version | None:
    try:
        if filename.endswith(".whl"):
            return parse_wheel_filename(filename)[1]
        return parse_sdist_filename(filename)[1]
    except (InvalidSdistFilename, InvalidWheelFilename):
        return None


def latest_stable_version() -> Version:
    request = urllib.request.Request(
        f"https://pypi.org/simple/{PACKAGE}/",
        headers={"Accept": "application/vnd.pypi.simple.v1+json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        files = json.load(response)["files"]

    # A release stays installable while any of its files isn't yanked.
    stable = set()
    for file in files:
        version = file_version(file["filename"])
        if version is not None and not version.is_prerelease and not file.get("yanked"):
            stable.add(version)
    if not stable:
        sys.exit(f"PyPI has no installable stable release of {PACKAGE}")
    return max(stable)


def gh(*args: str, payload: dict | None = None, token: str | None = None) -> str:
    env = {**os.environ, "GH_TOKEN": token} if token else None
    stdin = json.dumps(payload) if payload is not None else None
    return subprocess.run(["gh", *args], input=stdin, env=env, check=True, stdout=subprocess.PIPE, text=True).stdout


def issue_body(pinned: Version, latest: Version, note: str | None) -> str:
    body = f"""\
[{PACKAGE} {latest}](https://pypi.org/project/{PACKAGE}/{latest}/) is on PyPI; `requirements.txt` pins {pinned}.

Adopt it by following the **SDK upgrades** section of `.github/copilot-instructions.md`:

1. Pin `{PACKAGE}=={latest}` in `requirements.txt` and run `pip install -r requirements.txt`.
2. Run `python scripts/check_sdk_coverage.py` and update `notebook.ipynb` until it passes.
3. Read the release notes for every version after {pinned} and apply the changes the check can't detect.
4. Open a pull request that summarizes the SDK changes and the notebook changes.
"""
    if note:
        body += f"\n> [!IMPORTANT]\n> {note}\n"
    env = os.environ
    run_url = f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
    return body + f"\n<sub>Opened by [Watch SDK releases]({run_url}).</sub>\n"


def main() -> int:
    pinned, latest = pinned_version(), latest_stable_version()
    if latest <= pinned:
        print(f"requirements.txt pins {PACKAGE} {pinned}; the latest stable release is {latest}. Nothing to do.")
        return 0

    repo = os.environ["GITHUB_REPOSITORY"]
    title = f"Adopt {PACKAGE} {latest}"
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/issues?labels={LABEL}&state=all&per_page=100"))
    issues = [issue for page in pages for issue in page if "pull_request" not in issue]
    if tracked := next((issue for issue in issues if issue["title"] == title), None):
        print(f"::notice::#{tracked['number']} already tracks {PACKAGE} {latest}.")
        return 0
    if pending := next((issue for issue in issues if issue["state"] == "open"), None):
        print(f"::notice::{PACKAGE} {latest} will be proposed once #{pending['number']} is closed.")
        return 0

    token = os.environ.get("COPILOT_ASSIGNMENT_TOKEN")
    note = None
    if latest.major > pinned.major:
        note = (
            f"{latest} is a new major version and may redesign the client API, so Copilot wasn't assigned "
            "automatically. Read the release notes, then assign this issue to Copilot to attempt the migration."
        )
    elif not token:
        note = (
            "Copilot wasn't assigned because the `COPILOT_ASSIGNMENT_TOKEN` secret isn't set. "
            "Assign this issue to Copilot to start the upgrade."
        )

    description = f"New {PACKAGE} release to adopt"
    gh("label", "create", LABEL, "--repo", repo, "--color", "1D76DB", "--description", description, "--force")
    issue = json.loads(
        gh(
            "api",
            "--method",
            "POST",
            f"repos/{repo}/issues",
            "--input",
            "-",
            payload={"title": title, "body": issue_body(pinned, latest, note), "labels": [LABEL]},
        )
    )
    number = issue["number"]
    print(f"::notice::Opened #{number} for {PACKAGE} {latest}: {issue['html_url']}")
    if note:
        return 0

    assignment = {
        "assignees": [COPILOT],
        "agent_assignment": {
            "target_repo": repo,
            "base_branch": os.environ["GITHUB_REF_NAME"],
            "custom_instructions": "",
            "custom_agent": "",
            "model": "",
        },
    }
    try:
        assigned = json.loads(
            gh(
                "api",
                "--method",
                "POST",
                f"repos/{repo}/issues/{number}/assignees",
                "--input",
                "-",
                payload=assignment,
                token=token,
            )
        )
    except subprocess.CalledProcessError:
        assigned = {}
    # GitHub silently ignores assignees it can't assign, so confirm Copilot is on the issue.
    if any("copilot" in assignee["login"].lower() for assignee in assigned.get("assignees") or []):
        print(f"::notice::Assigned Copilot to #{number}.")
        return 0

    gh(
        "api",
        "--method",
        "POST",
        f"repos/{repo}/issues/{number}/comments",
        "--input",
        "-",
        payload={
            "body": (
                "Copilot couldn't be assigned automatically. Check that Copilot cloud agent is enabled for this "
                "repository and that the `COPILOT_ASSIGNMENT_TOKEN` secret holds a valid, unexpired token with the "
                "required permissions, then assign this issue to Copilot."
            )
        },
    )
    print(f"::error::Couldn't assign Copilot to #{number}.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

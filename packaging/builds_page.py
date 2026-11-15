"""Write a Markdown page listing every TunerBox build: releases, and the builds of each branch.

    python packaging/builds_page.py                  prints the page
    python packaging/builds_page.py --out builds.md  writes it to a file

Reads GitHub's API with GITHUB_TOKEN (any token that can read the repo; in Actions the built-in one).
With GIST_ID and GIST_TOKEN (a token with the "gist" scope) it also updates that gist's builds.md.
Standard library only.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "pasiegel/tunerbox")
WORKFLOW = "release.yml"
BRANCHES = ("testing", "main")
COMMITS = 40                      # newest commits listed per branch
API = "https://api.github.com"
# artifact name -> (platform heading, file type)
PLATFORMS = (("windows", "Windows", "installer + portable zip"),
             ("macos", "macOS (Apple Silicon)", "dmg"),
             ("linux", "Linux x64", "tar.gz"))


def get(path, token=None, params=""):
    url = path if path.startswith("http") else f"{API}{path}{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "tunerbox-builds"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < 2:
                time.sleep(3)
                continue
            raise


def runs(token, pages=3):
    out = []
    for page in range(1, pages + 1):
        d = get(f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs", token, f"?per_page=100&page={page}")
        out += d.get("workflow_runs") or []
        if len(d.get("workflow_runs") or []) < 100:
            break
    return out


def day(ts):
    return (ts or "")[:10]


def short(msg, n=60):
    line = (msg or "").splitlines()[0] if msg else ""
    line = line.replace("|", "/")
    return line if len(line) <= n else line[: n - 1] + "…"


def release_section(token):
    rels = [r for r in get(f"/repos/{REPO}/releases", token, "?per_page=30") if not r.get("draft")]
    lines = ["## Releases (main)", "",
             "Tagged versions. These downloads never expire and don't need a GitHub login. "
             f"Docker: `ghcr.io/{REPO.split('/')[0]}/tunerbox:<version>` (`:latest` is the newest).", "",
             "| Version | Date | Windows installer | Windows portable | macOS | Linux |", "|---|---|---|---|---|---|"]
    for r in rels:
        a = {x["name"]: x["browser_download_url"] for x in r.get("assets") or []}

        def pick(end, label):
            n = next((k for k in a if k.endswith(end)), None)
            return f"[{label}]({a[n]})" if n else "—"
        pre = " (pre-release)" if r.get("prerelease") else ""
        lines.append(f"| [{r['tag_name']}]({r['html_url']}){pre} | {day(r.get('published_at'))} | {pick('.exe', 'exe')} | "
                     f"{pick('-windows-portable.zip', 'zip')} | {pick('-macos-arm64.dmg', 'dmg')} | "
                     f"{pick('-linux-x64.tar.gz', 'tar.gz')} |")
    if not rels:
        lines.append("| — | | | | | |")
    return lines


def branch_section(branch, all_runs, token):
    commits = get(f"/repos/{REPO}/commits", token, f"?sha={branch}&per_page={COMMITS}")
    # newest run per commit on this branch (pushes and manual runs)
    by_sha = {}
    for r in all_runs:
        if r.get("head_branch") == branch and r["head_sha"] not in by_sha:
            by_sha[r["head_sha"]] = r
    arts = {}
    for sha, r in by_sha.items():
        # still running counts: the list is written by the last job of the build itself
        if r.get("conclusion") == "success" or r.get("status") != "completed":
            d = get(f"/repos/{REPO}/actions/runs/{r['id']}/artifacts", token, "?per_page=20")
            arts[sha] = {x["name"]: x for x in d.get("artifacts") or [] if not x.get("expired")}
    built = [c for c in commits if c["sha"] in by_sha]
    head = f"## Branch `{branch}`"
    if not built:
        return [head, "", f"No builds of `{branch}` yet (builds start with the next push).", ""]
    lines = [head, ""]
    for key, title, kind in PLATFORMS:
        latest = next((c for c in commits if key in arts.get(c["sha"], {})), None)
        n_built = sum(1 for c in commits if key in arts.get(c["sha"], {}))
        if latest:
            r, x = by_sha[latest["sha"]], arts[latest["sha"]][key]
            summ = (f"<b>{title}</b> ({kind}): {n_built} builds · latest #{r['run_number']} ({day(r['created_at'])}) · "
                    f"<a href=\"https://github.com/{REPO}/actions/runs/{r['id']}/artifacts/{x['id']}\">zip</a>")
        else:
            summ = f"<b>{title}</b> ({kind}): no downloadable build"
        lines += ["<details>", f"<summary>{summ}</summary>", "",
                  "| Date | Build | SHA | Commit | Download |", "|---|---|---|---|---|"]
        for c in commits:
            sha = c["sha"]
            r = by_sha.get(sha)
            date = day(c["commit"]["committer"]["date"])
            msg = short(c["commit"]["message"])
            link = f"[`{sha[:7]}`](https://github.com/{REPO}/commit/{sha})"
            if not r:
                lines.append(f"| {date} | — | {link} | {msg} | — |")
                continue
            build = f"[#{r['run_number']}]({r['html_url']})"
            x = arts.get(sha, {}).get(key)
            if x:
                dl = f"[zip](https://github.com/{REPO}/actions/runs/{r['id']}/artifacts/{x['id']}) ({x['size_in_bytes'] / 1e6:.0f} MB)"
            elif r.get("status") != "completed":
                dl = "building…"
            elif r.get("conclusion") == "success":
                dl = "expired"
            else:
                dl = r.get("conclusion") or "—"
            lines.append(f"| {date} | {build} | {link} | {msg} | {dl} |")
        lines += ["", "</details>", ""]
    return lines


def page(token):
    all_runs = runs(token)
    out = ["# TunerBox builds", "",
           f"_Repo: [`{REPO}`](https://github.com/{REPO}) · Generated: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}_", "",
           "Branch builds are for testing: they're downloaded as a zip from GitHub Actions (you need to be signed in "
           "to GitHub), and GitHub deletes them after 90 days. For everyday use, take a release.", ""]
    out += release_section(token) + [""]
    for b in BRANCHES:
        out += branch_section(b, all_runs, token)
    return "\n".join(out) + "\n"


def update_gist(gist_id, token, text):
    body = json.dumps({"description": "TunerBox builds", "files": {"builds.md": {"content": text}}}).encode()
    req = urllib.request.Request(f"{API}/gists/{gist_id}", data=body, method="PATCH",
                                 headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                                          "User-Agent": "tunerbox-builds", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["html_url"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()
    text = page(os.environ.get("GITHUB_TOKEN"))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    gist, gtok = os.environ.get("GIST_ID"), os.environ.get("GIST_TOKEN")
    if gist and gtok:
        print("Updated", update_gist(gist, gtok, text))
    elif not a.out:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()

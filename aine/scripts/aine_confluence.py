#!/usr/bin/env python3
"""AINE Confluence client for GitHub Actions.

Every Confluence call goes through the AINE Forge proxy and runs AS THE USER who
triggered the run (AINE_TICKET + HMAC with AINE_PROXY_SECRET).

Manual "Run workflow" runs have no ticket. They work only if the optional fallback
secrets CONF_BASE / CONF_EMAIL / CONF_TOKEN (and COPILOT_GITHUB_TOKEN) are set.

Commands (python aine/scripts/aine_confluence.py <command> --help):
  prepare           read the BRD, the existing spec and its comments into work/
  publish-spec      publish the spec, label it for review, comment on the BRD, reset BRD labels
  brd-fill          write the drafted BRD sections / team / target date back to the BRD
  business-changes  append "Change requests from spec review" to the BRD
  approve           baseline the spec and comment on the BRD
  design-prepare    read the BRD, approved spec and (for updates) a design page into work/
  design-publish    create/update a design page and label it for review
  design-approve    record design approval
  copilot-token     print the triggering user's Copilot token (stdout only)
  revoke            end the run ticket
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brd_fill  # noqa: E402

# Title prefixes: repository variables, same defaults as the Forge app's AINE_*_PREFIX variables
SPEC_PREFIX = os.environ.get("SPEC_TITLE_PREFIX") or "Functional Spec – "
BRD_PREFIX = os.environ.get("BRD_TITLE_PREFIX") or "Business Requirements"
DESIGN_KINDS = {
    "hld": "HLD - ",
    "infra": "Infra Design - ",
    "cloud": "Cloud Design - ",
    "plan": "Project Plan - ",
}
if os.environ.get("DESIGN_TITLE_PREFIXES"):          # JSON, e.g. {"hld":"HLD - ","infra":"Infra Design - ",...}
    DESIGN_KINDS.update(json.loads(os.environ["DESIGN_TITLE_PREFIXES"]))
LBL = {
    "REQ_INIT": "aine-requirement-initiate",
    "REQ_UPDATE": "aine-requirement-update",
    "SPEC_DRAFTED": "aine-spec-drafted",
    "SPECS_REVIEW": "aine-specs-review",
    "SPECS_UPDATE": "aine-specs-update",
    "SPECS_APPROVE": "aine-specs-approve",
    "DESIGN_REVIEW": "aine-design-review",
    "DESIGN_UPDATE": "aine-design-update",
    "DESIGN_APPROVE": "aine-design-approve",
}


# --------------------------------------------------------------------------- HTTP

class ConfError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:600]}")
        self.status = status
        self.body = body


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def call(method, path, body=None):
    """One Confluence call (or proxy command). Returns parsed JSON, text, or None."""
    ticket = os.environ.get("AINE_TICKET", "").strip()
    if ticket:
        payload = json.dumps({"ticket": ticket, "method": method, "path": path, "body": body},
                             separators=(",", ":"))  # ASCII-only, so the HMAC matches byte-for-byte
        ts = str(int(time.time()))
        sig = hmac.new(os.environ["AINE_PROXY_SECRET"].encode(), f"{ts}.{payload}".encode(),
                       hashlib.sha256).hexdigest()
        req = urllib.request.Request(os.environ["AINE_PROXY_URL"], data=payload.encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "x-aine-ts": ts, "x-aine-signature": sig})
    else:
        if method == "REVOKE":
            return None
        if method == "COPILOT_TOKEN":
            return {"token": os.environ.get("COPILOT_GITHUB_TOKEN", ""), "login": "fallback-secret"}
        base, email, tok = (os.environ.get(k, "") for k in ("CONF_BASE", "CONF_EMAIL", "CONF_TOKEN"))
        if not (base and email and tok):
            sys.exit("No AINE_TICKET (runs must start from Confluence) and no fallback CONF_* secrets.")
        auth = base64.b64encode(f"{email}:{tok}".encode()).decode()
        headers = {"Accept": "application/json", "Authorization": f"Basic {auth}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=headers)

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                txt = r.read().decode("utf-8", errors="replace")
                break
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", errors="replace")
            if e.code in (429, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise ConfError(e.code, txt) from None
    if not txt.strip():
        return None
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return txt


def q(v):
    return urllib.parse.quote(str(v), safe="")


# --------------------------------------------------------------------------- Confluence helpers

def get_page(pid):
    return call("GET", f"/wiki/api/v2/pages/{q(pid)}?body-format=storage")


def page_url(p):
    links = p.get("_links", {})
    return (links.get("base", os.environ.get("CONF_BASE", "")) or "") + links.get("webui", "")


def children(pid):
    res = call("GET", f"/wiki/api/v2/pages/{q(pid)}/children?limit=250") or {}
    return res.get("results", [])


def labels(pid):
    res = call("GET", f"/wiki/api/v2/pages/{q(pid)}/labels?limit=250") or {}
    return [l["name"] for l in res.get("results", [])]


def add_label(pid, name):
    call("POST", f"/wiki/rest/api/content/{q(pid)}/label", [{"prefix": "global", "name": name}])
    log(f"  + label {name} on {pid}")


def remove_label(pid, name):
    try:
        call("DELETE", f"/wiki/rest/api/content/{q(pid)}/label?name={q(name)}")
        log(f"  - label {name} on {pid}")
    except ConfError as e:
        if e.status != 404:
            raise


def add_comment(pid, html):
    call("POST", "/wiki/api/v2/footer-comments",
         {"pageId": str(pid), "body": {"representation": "storage", "value": html}})


def create_page(space_id, parent_id, title, html):
    return call("POST", "/wiki/api/v2/pages", {
        "spaceId": str(space_id), "status": "current", "title": title, "parentId": str(parent_id),
        "body": {"representation": "storage", "value": html}})


def update_page(pid, title, html, message):
    current = call("GET", f"/wiki/api/v2/pages/{q(pid)}")      # latest version, avoids 409
    return call("PUT", f"/wiki/api/v2/pages/{q(pid)}", {
        "id": str(pid), "status": "current", "title": title,
        "body": {"representation": "storage", "value": html},
        "version": {"number": current["version"]["number"] + 1, "message": message}})


def comments_markdown(pid):
    out = []
    for kind in ("footer-comments", "inline-comments"):
        res = call("GET", f"/wiki/api/v2/pages/{q(pid)}/{kind}?body-format=storage&limit=250") or {}
        for c in res.get("results", []):
            text = storage_to_md(c.get("body", {}).get("storage", {}).get("value", "")).strip()
            if not text or text.startswith("AINE "):          # skip AINE's own comments
                continue
            sel = (c.get("properties") or {}).get("inlineOriginalSelection")
            head = f"### Comment {c.get('id')} ({kind.split('-')[0]})"
            out.append(head + (f"\n> On: \"{sel}\"" if sel else "") + "\n\n" + text + "\n")
    return "\n".join(out)


# --------------------------------------------------------------------------- conversions

def pandoc(text, src, dst):
    r = subprocess.run(["pandoc", "-f", src, "-t", dst, "--wrap=none"], input=text,
                       capture_output=True, text=True, check=True)
    return r.stdout


def storage_to_md(html):
    return pandoc(html or "", "html", "gfm")


VOID = re.compile(r"<(br|hr|img|col|input)(\b[^>]*?)(?<!/)>", re.I)


def md_to_storage(md):
    html = pandoc(md, "gfm", "html")
    html = VOID.sub(r"<\1\2 />", html)                       # storage format is XHTML
    html = re.sub(r"<input[^>]*/>", "", html)                 # no task-list checkboxes
    return html


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------------------------------- GitHub helpers

def gh_output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={v}" for k, v in kv.items()]
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    for line in lines:
        log(f"  output {line}")


def write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


def load_meta(work):
    return json.loads(Path(work, "meta.json").read_text(encoding="utf-8"))


def save_meta(work, meta):
    write(Path(work, "meta.json"), json.dumps(meta, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- commands: intake

def resolve_brd(page):
    title = page["title"]
    if title.startswith(SPEC_PREFIX):
        log(f"Label was added on spec page {page['id']}; using parent BRD {page['parentId']}")
        return get_page(page["parentId"])
    if any(title.startswith(p) for p in DESIGN_KINDS.values()):
        sys.exit(f"'{title}' is a Stage 2 design page; it is handled by aine-design.yml, not Intake.")
    if not title.startswith(BRD_PREFIX):
        sys.exit(f"'{title}' is neither a BRD ('{BRD_PREFIX}…') nor a spec ('{SPEC_PREFIX}…').")
    return page


def find_spec(brd_id):
    for c in children(brd_id):
        if c.get("title", "").startswith(SPEC_PREFIX):
            return get_page(c["id"])
    return None


def cmd_prepare(a):
    work = Path(a.work)
    page = get_page(a.page)
    brd = resolve_brd(page)
    brd_html = brd["body"]["storage"]["value"]
    write(work / "brd.html", brd_html)
    write(work / "brd.md", storage_to_md(brd_html))
    # The BRD's own section headings (template-independent), for the BRD-fill prompt
    write(work / "brd-headings.md", "\n".join(f"- {h}" for h in brd_fill.headings(brd_html)) or "(no headings)")

    spec = find_spec(brd["id"])
    meta = {
        "triggered_page": str(a.page),
        "brd": {"id": brd["id"], "title": brd["title"], "version": brd["version"]["number"],
                "url": page_url(brd), "spaceId": brd["spaceId"]},
        "spec": None,
        "feature": f"001-brd-{brd['id']}",
    }
    if spec:
        spec_html = spec["body"]["storage"]["value"]
        write(work / "spec-current.html", spec_html)
        write(work / "spec-current.md", storage_to_md(spec_html))
        write(work / "comments.md", comments_markdown(spec["id"]) or "(no comments)\n")
        meta["spec"] = {"id": spec["id"], "title": spec["title"], "version": spec["version"]["number"],
                        "url": page_url(spec)}
    save_meta(work, meta)
    mode = "update" if spec else "new"
    log(f"BRD {brd['id']} '{brd['title']}' v{brd['version']['number']}; spec: "
        f"{spec['id'] if spec else 'none'}; MODE={mode}")
    gh_output(brd_id=brd["id"], brd_title=brd["title"], brd_version=brd["version"]["number"],
              brd_url=page_url(brd), spec_id=spec["id"] if spec else "",
              spec_version=spec["version"]["number"] if spec else 0,
              mode=mode, feature=meta["feature"])


def cmd_publish_spec(a):
    meta = load_meta(a.work)
    brd = meta["brd"]
    md = Path(a.spec_md).read_text(encoding="utf-8")
    html = md_to_storage(md)
    title = SPEC_PREFIX + brd["title"]

    if meta["spec"]:
        sid = meta["spec"]["id"]
        # Remove the trigger label BEFORE publishing, so this edit doesn't start another
        # aine-specs-update run.
        remove_label(sid, LBL["SPECS_UPDATE"])
        res = update_page(sid, title, html, f"AINE ({a.action}) from BRD v{brd['version']}")
        log(f"Updated spec page {sid} → v{res['version']['number']}")
    else:
        res = create_page(brd["spaceId"], brd["id"], title, html)
        sid = res["id"]
        log(f"Created spec page {sid}")
    meta["spec"] = {"id": sid, "title": title, "version": res["version"]["number"], "url": page_url(res)}
    save_meta(a.work, meta)

    remove_label(sid, LBL["SPECS_REVIEW"])        # removed and re-added so "label added" fires each time
    add_label(sid, LBL["SPECS_REVIEW"])

    open_q = len(re.findall(r"\[NEEDS CLARIFICATION", md))
    verb = "drafted" if a.action == "initiate" and res["version"]["number"] == 1 else "updated"
    add_comment(brd["id"],
                f"<p>AINE {verb} the functional spec: <a href=\"{esc(meta['spec']['url'])}\">{esc(title)}</a> "
                f"(v{res['version']['number']}, from BRD v{brd['version']}). "
                f"Open clarifications: <strong>{open_q}</strong>. Run by {esc(os.environ.get('BY', 'AINE'))}.</p>")

    for name in labels(brd["id"]):                 # BRD ends every run with only aine-spec-drafted
        if name != LBL["SPEC_DRAFTED"]:
            remove_label(brd["id"], name)
    if LBL["SPEC_DRAFTED"] not in labels(brd["id"]):
        add_label(brd["id"], LBL["SPEC_DRAFTED"])
    gh_output(spec_id=sid, spec_url=meta["spec"]["url"], open_clarifications=open_q)


def cmd_brd_fill(a):
    meta = load_meta(a.work)
    brd = get_page(meta["brd"]["id"])               # re-read: the BRD may have changed
    fill = Path(a.fill).read_text(encoding="utf-8") if Path(a.fill).exists() else ""
    spec_md = Path(a.spec_md).read_text(encoding="utf-8")
    new_html, changes = brd_fill.merge(brd["body"]["storage"]["value"], spec_md, fill, md_to_storage)
    if new_html is None:
        log("BRD fill: nothing to change")
        return
    update_page(brd["id"], brd["title"], new_html, "AINE: BRD updated from the edited spec")
    add_comment(brd["id"], "<p>AINE updated this BRD from the edited functional spec: "
                + esc("; ".join(changes)) + ".</p>")
    log("BRD fill: " + "; ".join(changes))


def parse_business_changes(text):
    items = []
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]\s+(.*\S)", line)
        if m and not m.group(1).lower().startswith("none"):
            items.append(m.group(1))
    return items


def cmd_business_changes(a):
    p = Path(a.file)
    items = parse_business_changes(p.read_text(encoding="utf-8")) if p.exists() else []
    if not items:
        log("No business changes to send to the BRD")
        return
    meta = load_meta(a.work)
    brd = get_page(meta["brd"]["id"])
    html = brd["body"]["storage"]["value"]
    known = re.sub(r"<[^>]+>", " ", html)
    new = [i for i in items if re.sub(r"\s+", " ", i)[:80] not in re.sub(r"\s+", " ", known)]
    if not new:
        log("Business changes already in the BRD (HANDLED)")
        return
    section = (f"<h3>Change requests from spec review ({date.today().isoformat()})</h3><ul>"
               + "".join(f"<li>{esc(i)}</li>" for i in new) + "</ul>")
    update_page(brd["id"], brd["title"], html + section, "AINE: change requests from spec review")
    add_comment(brd["id"], f"<p>AINE added {len(new)} change request(s) from the spec review. "
                           f"Please review them in <strong>Change requests from spec review</strong>.</p>")
    log(f"Added {len(new)} change request(s) to the BRD")


def cmd_approve(a):
    meta = load_meta(a.work)
    spec = meta["spec"]
    if not spec:
        sys.exit("No spec page found under the BRD; nothing to approve.")
    for name in (LBL["SPECS_REVIEW"], LBL["SPECS_UPDATE"]):
        remove_label(spec["id"], name)
    add_comment(meta["brd"]["id"],
                f"<p>AINE: Spec approved at version {spec['version']} "
                f"(<a href=\"{esc(spec['url'])}\">{esc(spec['title'])}</a>) by "
                f"{esc(os.environ.get('BY', 'the Architect'))}. Design phase triggered.</p>")
    log(f"Spec {spec['id']} baselined at v{spec['version']}")


# --------------------------------------------------------------------------- commands: design

def design_kind_of(title):
    for kind, prefix in DESIGN_KINDS.items():
        if title.startswith(prefix):
            return kind
    return None


def cmd_design_prepare(a):
    work = Path(a.work)
    page = get_page(a.page)
    kind = design_kind_of(page["title"])
    if kind:
        brd = get_page(page["parentId"])
        write(work / "design-current.md", storage_to_md(page["body"]["storage"]["value"]))
        write(work / "comments.md", comments_markdown(page["id"]) or "(no comments)\n")
    else:
        brd = resolve_brd(page)
    write(work / "brd.md", storage_to_md(brd["body"]["storage"]["value"]))
    spec = find_spec(brd["id"])
    if not spec:
        sys.exit("No functional spec under the BRD; approve the spec first.")
    write(work / "spec.md", storage_to_md(spec["body"]["storage"]["value"]))
    existing = {design_kind_of(c["title"]): c["id"] for c in children(brd["id"]) if design_kind_of(c["title"])}
    meta = {"brd": {"id": brd["id"], "title": brd["title"], "spaceId": brd["spaceId"], "url": page_url(brd)},
            "spec": {"id": spec["id"], "version": spec["version"]["number"]},
            "design_pages": existing, "triggered_kind": kind,
            "triggered_page": page["id"] if kind else None}
    save_meta(work, meta)
    kinds = [kind] if kind else list(DESIGN_KINDS)
    gh_output(brd_id=brd["id"], kinds=",".join(kinds), triggered_kind=kind or "")


def cmd_design_publish(a):
    meta = load_meta(a.work)
    brd = meta["brd"]
    title = DESIGN_KINDS[a.kind] + brd["title"]
    html = md_to_storage(Path(a.md).read_text(encoding="utf-8"))
    pid = meta["design_pages"].get(a.kind)
    if pid:
        remove_label(pid, LBL["DESIGN_UPDATE"])            # before the edit, as for specs
        res = update_page(pid, title, html, f"AINE design update from spec v{meta['spec']['version']}")
    else:
        res = create_page(brd["spaceId"], brd["id"], title, html)
        pid = res["id"]
        meta["design_pages"][a.kind] = pid
        save_meta(a.work, meta)
    remove_label(pid, LBL["DESIGN_REVIEW"])
    add_label(pid, LBL["DESIGN_REVIEW"])
    add_comment(brd["id"], f"<p>AINE published <a href=\"{esc(page_url(res))}\">{esc(title)}</a> "
                           f"(v{res['version']['number']}) for review.</p>")
    log(f"Published {title} ({pid}) v{res['version']['number']}")


def cmd_design_approve(a):
    meta = load_meta(a.work)
    pid = meta["triggered_page"]
    if not pid:
        sys.exit("design-approve needs a design page")
    remove_label(pid, LBL["DESIGN_REVIEW"])
    remove_label(pid, LBL["DESIGN_UPDATE"])
    title = DESIGN_KINDS[meta["triggered_kind"]] + meta["brd"]["title"]
    add_comment(meta["brd"]["id"], f"<p>AINE: <strong>{esc(title)}</strong> approved by "
                                   f"{esc(os.environ.get('BY', 'the Architect'))}.</p>")


# --------------------------------------------------------------------------- commands: tokens

def cmd_copilot_token(a):
    r = call("COPILOT_TOKEN", "/")
    if not isinstance(r, dict) or not r.get("token"):
        sys.exit(f"No Copilot token for the triggering user: {r}. "
                 "They must link GitHub in Confluence (Apps → AINE – Connect GitHub).")
    log(f"Copilot will run as GitHub user: {r.get('login')}")
    print(r["token"])


def cmd_revoke(a):
    call("REVOKE", "/")
    log("Run ticket revoked")


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("prepare"); s.add_argument("--page", required=True); s.add_argument("--work", default="work")
    s.set_defaults(fn=cmd_prepare)
    s = sub.add_parser("publish-spec"); s.add_argument("--work", default="work")
    s.add_argument("--spec-md", required=True); s.add_argument("--action", required=True)
    s.set_defaults(fn=cmd_publish_spec)
    s = sub.add_parser("brd-fill"); s.add_argument("--work", default="work")
    s.add_argument("--fill", required=True); s.add_argument("--spec-md", required=True)
    s.set_defaults(fn=cmd_brd_fill)
    s = sub.add_parser("business-changes"); s.add_argument("--work", default="work"); s.add_argument("--file", required=True)
    s.set_defaults(fn=cmd_business_changes)
    s = sub.add_parser("approve"); s.add_argument("--work", default="work"); s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("design-prepare"); s.add_argument("--page", required=True); s.add_argument("--work", default="work")
    s.set_defaults(fn=cmd_design_prepare)
    s = sub.add_parser("design-publish"); s.add_argument("--work", default="work")
    s.add_argument("--kind", required=True, choices=list(DESIGN_KINDS)); s.add_argument("--md", required=True)
    s.set_defaults(fn=cmd_design_publish)
    s = sub.add_parser("design-approve"); s.add_argument("--work", default="work"); s.set_defaults(fn=cmd_design_approve)
    s = sub.add_parser("copilot-token"); s.set_defaults(fn=cmd_copilot_token)
    s = sub.add_parser("revoke"); s.set_defaults(fn=cmd_revoke)

    a = ap.parse_args()
    try:
        a.fn(a)
    except ConfError as e:
        log(f"::error::Confluence call failed: {e}")
        if e.status == 403:
            log("The triggering user may not have permission for this page, or the run ticket expired.")
        sys.exit(1)


if __name__ == "__main__":
    main()

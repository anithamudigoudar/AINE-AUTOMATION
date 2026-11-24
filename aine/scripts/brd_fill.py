#!/usr/bin/env python3
"""Spec → BRD merge that doesn't depend on the BRD template.

The BRD's own headings define its sections, whatever the template looks like.
`work/brd-fill.md` (written by Copilot) contains only the sections that should change,
each under a heading copied verbatim from the BRD:

    ## <exact BRD heading text>
    <new content in markdown>

    ## [Before first heading]
    <new content for anything above the BRD's first heading, e.g. a team table>

Rules:
- Only sections listed in the fill are touched; everything else stays as it is.
- Headings are matched on their text (case and spacing ignored); the heading level doesn't matter.
- A section's content runs from its heading to the next heading of any level.
- A section is never shortened in tables: if the draft has fewer table rows than the BRD section,
  the BRD section is kept (so requirement rows are never dropped).
- Headings that don't exist in the BRD are skipped and reported.

Library use:  new_html, changes = merge(brd_html, spec_md, fill_md, md_to_storage)   # None = no change
              headings(brd_html) → list of the BRD's heading texts (for the prompt)
CLI use:      brd_fill.py BRD.html SPEC.md FILL.md OUT.html   → exit 0 = changed, exit 3 = no change
"""
import re
import sys

PREAMBLE = "[before first heading]"
HEADING = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.S | re.I)


def text(html):
    t = re.sub(r"<[^>]+>", " ", html or "")
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", t).strip()


def norm(heading):
    return re.sub(r"\s+", " ", heading).strip().strip("#").strip().lower()


def headings(brd_html):
    """The BRD's heading texts, in order."""
    return [text(m.group(2)) for m in HEADING.finditer(brd_html) if text(m.group(2))]


def brd_sections(brd_html):
    """{normalised heading: (start, end)} of each section's content, plus the preamble."""
    heads = list(HEADING.finditer(brd_html))
    spans = {PREAMBLE: (0, heads[0].start() if heads else len(brd_html))}
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(brd_html)
        spans.setdefault(norm(text(h.group(2))), (h.end(), end))   # first occurrence wins
    return spans


def fill_sections(fill_md):
    """[(normalised heading, original heading, markdown body)] from brd-fill.md."""
    out, cur, buf = [], None, []
    for line in (fill_md or "").splitlines():
        m = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if m:
            if cur is not None:
                out.append((norm(cur), cur, "\n".join(buf).strip()))
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out.append((norm(cur), cur, "\n".join(buf).strip()))
    return [s for s in out if s[2]]


def table_rows_md(md):
    rows = [l for l in md.splitlines() if l.strip().startswith("|")]
    rows = [r for r in rows if not re.match(r"^\s*\|[\s:|-]+\|\s*$", r)]        # drop separator rows
    return max(0, len(rows) - (1 if rows else 0))                                 # minus header row


def table_rows_html(html):
    n = 0
    for tr in re.findall(r"<tr\b.*?</tr>", html, re.S | re.I):
        if "<th" in tr.lower():
            continue
        cells = [text(c) for c in re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", tr, re.S | re.I)]
        if any(cells):
            n += 1
    return n


def merge(brd_html, spec_md, fill_md, md_to_storage):
    changes = []
    html = brd_html
    spans = brd_sections(html)
    todo = []
    for key, original, body in fill_sections(fill_md):
        if key not in spans:
            changes.append(f"skipped '{original}' (no such heading in the BRD)")
            continue
        todo.append((spans[key], original, body))
    # Replace from the end backwards so earlier offsets stay valid
    for (start, end), original, body in sorted(todo, key=lambda x: x[0][0], reverse=True):
        old = html[start:end]
        if table_rows_md(body) < table_rows_html(old):
            changes.append(f"kept '{original}' (draft had fewer table rows than the BRD)")
            continue
        new = md_to_storage(body)
        if text(new) != text(old):
            html = html[:start] + new + html[end:]
            changes.append(f"updated '{original}'")
    real = [c for c in changes if c.startswith("updated")]
    return (html if real and html != brd_html else None), changes


def _pandoc_md_to_storage(md):
    import subprocess
    out = subprocess.run(["pandoc", "-f", "gfm", "-t", "html", "--wrap=none"], input=md,
                         capture_output=True, text=True, check=True).stdout
    return re.sub(r"<(br|hr|img|col)(\b[^>]*?)(?<!/)>", r"<\1\2 />", out, flags=re.I)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    brd, spec, fill, out = sys.argv[1:]
    new_html, changes = merge(open(brd, encoding="utf-8").read(), open(spec, encoding="utf-8").read(),
                              open(fill, encoding="utf-8").read(), _pandoc_md_to_storage)
    for c in changes:
        print(c)
    if new_html is None:
        sys.exit(3)
    open(out, "w", encoding="utf-8").write(new_html)

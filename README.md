# DEV-AINE — AI-Native Engineering (per-user)

AINE turns a Confluence **BRD** into a **functional spec** (GitHub Spec Kit + Copilot CLI), keeps the BRD and spec in sync, and drafts the **design** documents after approval.

Everything runs **as the person who triggered it** in Confluence:
- Confluence reads/writes → the AINE Forge app proxy (`asUser`), using a run ticket
- Copilot / Spec Kit → that person's own GitHub Copilot seat (linked once in Confluence: **Apps → AINE – Connect GitHub**)

Confluence: the space(s) listed in the AINE Forge app's `AINE_SITES` setting. Triggers: the AINE Forge app (no Confluence automation rules).

## Labels

| Label | Page | Effect |
|---|---|---|
| `aine-requirement-initiate` | BRD | Draft the spec |
| `aine-requirement-update` | BRD | Added automatically on a BRD edit → update the spec |
| `aine-spec-drafted` | BRD | Set by AINE after each run |
| `aine-specs-review` | Spec | Set by AINE: ready for the Architect |
| `aine-specs-update` | Spec | Architect: apply my edits/comments |
| `aine-specs-approve` | Spec | Architect: approve → baseline + start Design |
| `aine-design-update` / `aine-design-approve` | HLD / Infra / Cloud / Plan pages | Regenerate with comments / approve |

## Repository layout

```
.github/workflows/aine-intake.yml      Stage 1: spec draft, update, triage, BRD fill, approval
.github/workflows/aine-design.yml      Stage 2: HLD, Infra Design, Cloud Design, Project Plan
aine/scripts/aine_confluence.py        Confluence client (via the Forge proxy, as the user)
aine/scripts/brd_fill.py               Spec → BRD merge (sections 1–5, team, target date)
aine/scripts/run_copilot.sh            Runs Copilot CLI on the triggering user's token
aine/scripts/render_prompt.py          Fills {{PLACEHOLDERS}} in prompt templates
aine/prompts/*.md                      Prompts: specify-new, clarify-brd, specs-update, brd-fill, design
aine/speckit/constitution.md           Copied into Spec Kit's constitution on every run
aine/speckit/spec-template-aine-sections.md  Appended to Spec Kit's spec template
aine/context/*.md                      Project context and tool catalogue for Spec Kit
```

Don't commit `.specify/`, `specs/`, `work/` or the Spec Kit files under `.github/` (prompts, skills, agents): the workflow creates them on every run.

## Sites (GitHub Environments)

This repo serves **every** Confluence site. The same AINE Forge app is installed on each site; its `AINE_SITES` setting maps the site to a name. Each name has a GitHub **Environment** here, holding:

| Environment secret | Value |
|---|---|
| `AINE_PROXY_URL` | That site's AINE Forge web trigger URL |

| Environment | Confluence site | Space(s) |
|---|---|---|
| `<site>` | `<your-site>.atlassian.net` | `<SPACEKEY>` |

## Secrets and variables (repository level, shared by all sites)

| Name | Type | Value |
|---|---|---|
| `AINE_PROXY_SECRET` | Secret | HMAC secret shared with the Forge app (same as its `AINE_PROXY_SECRET`) |
| `AINE_APP_PRIVATE_KEY` | Secret | `aine-automation` GitHub App private key (.pem) |
| `AINE_APP_CLIENT_ID` | Secret | `aine-automation` GitHub App Client ID |
| `SPEC_TITLE_PREFIX` | Variable | `Functional Spec – ` (en dash, trailing space) |
| `BRD_TITLE_PREFIX` | Variable | *(optional)* default `Business Requirements` |
| `DESIGN_TITLE_PREFIXES` | Variable | *(optional)* JSON, default `{"hld":"HLD - ","infra":"Infra Design - ","cloud":"Cloud Design - ","plan":"Project Plan - "}` |
| `SPECKIT_REF` | Variable | Spec Kit tag, e.g. `v1.0.3` |
| `COPILOT_MODEL` | Variable | *(optional)* Copilot model name |
| `CONFLUENCE_BASE` | Variable | *(optional, manual runs only)* `https://<your-site>.atlassian.net` |
| `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN`, `COPILOT_GITHUB_TOKEN` | Secrets | *(optional, manual runs only)* leave unset so every run is per-user |

# AINE Constitution

## Core Principles

### I. BRD Is the Source of Record
The Confluence BRD is the only source of business requirements. A spec must never add
scope that is not in the BRD.

### II. Full Traceability
Every functional requirement (FR-xxx) traces to a BRD requirement number, and every
requirement has at least one acceptance scenario.

### III. Testable Requirements
Requirements are written so they can be tested. Acceptance scenarios use
Given / When / Then.

### IV. No Guessing
Anything unclear is marked [NEEDS CLARIFICATION: question] and listed under
"Open Questions for the Architect".

### V. Stable Identifiers
Requirement IDs (FR-001, NFR-001) never change across revisions. New items get new IDs;
removed items are marked "Withdrawn"; nothing is renumbered.

### VI. What and Why, Not How
Specs describe user value and behaviour. Technology choices belong in the plan.

### VII. Minimal Change on Update
When updating an existing spec, change only the sections affected by the BRD change or
the Architect's comments, and record each change in the Change Log.

### VIII. Business Changes Go to the BRD First
A review comment that changes scope, goals, success criteria, priorities or users is a
business change. Never apply it to the spec directly; record it as a change request for
the BRD and mark it "Pending BRD change" in the spec. Comments that stay within the BRD's
scope (clarifications, behaviour, wording, acceptance scenarios) are functional changes
and are applied to the spec.

## Platform Constraints
Follow the **Platform constraints** section of `aine/context/project-context.md`. They are
project-specific and override any default assumption about technology or hosting.

## Output Rules
- Use only markdown headings, lists and tables (no raw HTML).

## Governance
This constitution overrides other guidance. Changes are made by pull request and
approved by the Architect.

**Version**: 1.0.0 | **Ratified**: 2026-11-10 | **Last Amended**: 2026-11-10

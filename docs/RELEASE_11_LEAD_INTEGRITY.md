# Release 11 — Lead Integrity

Release 11 separates sales-ready contractors from permit professionals and
research-only parties. It corrects the existing database before the new queue
rules are used.

## What changes

- **Project Records defaults to verified contractors.**
- Architects/engineers, owners/developers, and unverified permit contacts have
  separate research queues.
- Each project response includes lead type, verification status, source field,
  and the reason the party is in the queue.
- Phoenix `PROFESS_NAME`, Mesa/Peoria applicant fallbacks, and Scottsdale
  responsible-party fallbacks no longer count as contractor evidence.
- Derived company metrics and permit timeline rows are rebuilt after links are
  corrected, preventing stale architect activity from surviving.
- “Todd & Associates” from Phoenix `PROFESS_NAME` is retained as research
  evidence but removed from contractor Project Records.

## Production rollout

1. Pull the merged Release 11 branch.
2. Double-click `ApplyCorridorIQLeadIntegrity.bat`.
3. Wait for **VALIDATION PASSED**.
4. CorridorIQ restarts automatically on the Release 11 build.
5. Open **Project Records**. The initial queue is **Verified contractors**.

The rollout creates a complete timestamped SQLite backup before changing any
record:

`pipeline/db/backups/corridoriq.pre-lead-integrity.YYYYMMDD-HHMMSS.db`

A JSON before/after report is written beside the backup. The command fails
closed when another program owns port 8780, the database quick check fails,
permit/project assignments disagree, or a nonverified company remains linked
as a project contractor.

## What this release does not ingest

Release 11 fixes identity and workflow integrity. It does not yet add AZROC,
ADOT advertisements/awards, UCC filings, or new contact research.

Those sources belong in Release 12 after the contractor queue is clean, so
external enrichment attaches to the correct company instead of amplifying bad
assignments.

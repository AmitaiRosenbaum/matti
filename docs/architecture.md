# Architecture

`matti.cli` owns command routing; `matti.config` validates project settings and paths.
`matti.download` owns source parsing, yt-dlp execution, archive and collection resume.
`matti.lyrics.pipeline` coordinates scanning, provider selection, checkpoints and reports.
`matti.lyrics.providers` contains source adapters; `matching` validates candidate identity,
text and timing; `http` owns HTTP cache, deduplication, throttling and host backoff.
`matti.lyrics.embed` owns reversible tag writes; `matti.audio` verifies MP3 payloads.
`matti.library` provides read-only coverage. `matti.state` provides atomic JSON writes.
`matti.ledger` stores stage execution history in SQLite. `matti.legacy` isolates curated,
project-specific maintenance from the generic workflow.

## State and compatibility

Downloader and lyrics checkpoints retain their existing formats to avoid rebuilding
history or changing media paths. SQLite records stage runs; it is not yet the canonical
track database. Track identity in lyrics checkpoints is still a relative path plus a
metadata fingerprint. Renaming files can trigger another lookup, but existing sidecars
are preserved. A future stable track-ID migration should be explicit and transactional.

The package contains no user media or curated source lists. Installation location does
not determine library paths. Profiles supplied with this checkout demonstrate existing
project migration; they are user data, not bundled defaults. The old scripts only import
package modules and dispatch their original entry points, avoiding duplicate implementations.

## Extending providers

Add an adapter in `lyrics/providers.py` with `(client, track)` inputs and a list of
candidate dictionaries returned by `result`. Check identity before constructing results.
Register it in the ordered provider sequence in `pipeline.fetch_one`. Use the shared
HTTP client and duration validation; never fabricate timestamps. Add fixture-based tests
for identity mismatches, unavailable responses and the provider's timing format.

## Operating limits

CLI write stages acquire an OS-managed library lock, released on exit or crash.
HTTP concurrency is bounded within the lyrics stage. Compatibility scripts and direct
module calls bypass the CLI lock and should not run concurrently on the same library.
A stopped run can have an unfinished SQLite entry; per-stage checkpoints control resume.
MP3 is the supported format. Source tags are currently the downloaded/display tags;
separate immutable source metadata, a unified track database, and GUI APIs remain future
extensions. No generic metadata rewriting is enabled until its rules are configurable.

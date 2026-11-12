# Security Policy

## Supported version

Security fixes are applied to the latest version on the default branch.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature when it is available. Do not publish task titles, state files, local paths, transcripts, credentials, or other private data in a public issue.

## Security boundaries

- The Skill should use official Codex task tools for reading and renaming tasks.
- It must not edit Codex databases, rollout files, caches, or application files.
- The local state file contains old and new title plaintext for recovery. Treat it as private local data and never commit it.
- Historical and batch renames require an exact preview and explicit user approval.
- A manually changed title must not be overwritten automatically.


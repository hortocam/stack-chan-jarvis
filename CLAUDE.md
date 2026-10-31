# CLAUDE.md

The rules for this repository live in **[`AGENTS.md`](./AGENTS.md)** — read that file first.

Hermes loads exactly one project-context file per repo, choosing the first that exists in
this order: `.hermes.md` → `AGENTS.md` → `CLAUDE.md` → `.cursorrules`. Because `AGENTS.md`
exists here, it is authoritative and this file is not loaded by Hermes. It is kept so that
tools which look for `CLAUDE.md` find their way to the same rules.

Do not duplicate rules here. Edit `AGENTS.md`.

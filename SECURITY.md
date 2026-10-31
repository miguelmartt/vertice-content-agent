# Security

## Reporting a vulnerability

Please report it privately through GitHub: **Security → Report a vulnerability** on this repository. Don't open a public issue, and don't include real tokens, chat ids or hostnames in the report.

You'll get an answer within a week. Fixes are released as a new version and noted in [CHANGELOG.md](CHANGELOG.md).

## Threat model

The agent holds several secrets and writes code that ends up running on a production web server, so it is built around these assumptions.

### Untrusted input

- **Search data.** Search Console queries and Google autocomplete suggestions can be influenced by anyone.
- **Model output.** Claude's plans and drafts can be steered by that data (prompt injection) or simply be wrong.
- **Telegram updates.** Anyone can message the bot.
- **The website repository.** Its code runs on the server, but the agent doesn't trust it with its secrets.

### What protects what

| Risk | Mitigation |
|---|---|
| A draft injects Blade or PHP into the site (code execution in CI and production) | The model returns content blocks, never HTML. All text is HTML-escaped and `@`, `{`, `}` are encoded as entities (`vca/render.py`). Metadata that goes into `@extends` parameters can't contain them. A final check rejects any Blade syntax outside the fixed template. Internal links are only emitted for existing paths made of `[a-z0-9/-]`. |
| A draft adds spam or phishing links | External links must be plain `https://` URLs, get `rel="nofollow noopener noreferrer"` and their domains are listed in the Telegram review and in the PR. |
| Someone else drives the bot | Updates are only handled when both the chat and the sender are `VCA_TELEGRAM_CHAT_ID` (your private chat). Everything else is ignored. |
| A stale or replayed button publishes something you didn't review | The "Publish" button carries a fingerprint of the draft it was sent with. The merge is pinned to the commit whose CI was checked (`sha` in the merge call). |
| Secrets leak to other programs | PHP (which loads the site's registry files) and git get a minimal environment with no agent tokens. Git gets its own token through an extra header scoped to `https://github.com/`. Claude Code only gets `CLAUDE_*` variables, never an API key. |
| Secrets leak in logs or messages | Errors are redacted (Telegram, GitHub, Claude and Google token formats, `Authorization` headers). `Authorization` is not forwarded on redirects to another host or to `http`. |
| The model gets tools | Claude Code runs with `--tools ""`, no MCP, no user or project settings, no session persistence and in an empty working directory. |
| The service is compromised | systemd sandbox: dedicated user, read-only system, writes only to its state folder, `NoExecPaths` on that folder except the Claude Code binary (which is read-only), no capabilities, restricted syscalls, address families and namespaces. |
| The config file is interpreted as code | `/etc/vertice-content-agent/env` is `root:vca 0640` and is read as data by systemd and by `python -m vca --env-file`, never sourced by a shell. |

### Least-privilege credentials

- **GitHub:** a fine-grained token for one repository, with Contents and Pull requests read and write, and Actions, Commit statuses and Metadata read.
- **Search Console:** OAuth with the read-only scope `webmasters.readonly`.
- **Claude:** a subscription token from `claude setup-token`, with a daily call cap enforced by the agent.
- **Telegram:** a bot token used only for long polling. No webhook and no open port.

### Known limits

- **Contents write lets the token push to `main`.** The agent only pushes `contenido/<slug>` branches and merges through the API. If your account can bypass branch protection, a stolen token could still push to `main` directly. Protect `main` with a ruleset that requires a pull request and passing checks, without bypass, if that matters to you.
- **The draft review is a human step.** The preview is sent as an HTML file to read on your phone, and the PR shows the exact Blade view. Read the PR diff for anything unusual before you tap "Publish".
- **Google autocomplete has no official API.** The agent uses the public suggest endpoint at a low rate.

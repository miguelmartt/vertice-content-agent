# Changelog

All notable changes to this project are documented here.

## [0.2.0] - 2026-10-31

First public release, after an independent security review.

### Security

- **Fix (critical):** Blade directive injection. Model text with an accented letter right before `@` (`está@php(...)`) or with a Blade comment (`{{-- --}}php(...)`) got past the old escaping and compiled into PHP. All text now has `@`, `{` and `}` encoded as HTML entities. Code blocks no longer use `@verbatim`. A final check rejects any Blade syntax outside the template, and `keywords` are validated too.
- **Fix:** the worker thread could die silently if Telegram failed while an error was being reported, and so could the bot thread on an unexpected response. Both now survive. If a thread dies anyway, the service exits so systemd restarts it.
- **Fix:** the bot now checks the sender as well as the chat.
- **Fix:** subprocesses get a minimal environment. PHP and git no longer see the agent's tokens, and Claude Code only gets `CLAUDE_*` variables (never `ANTHROPIC_API_KEY`).
- **Fix:** the merge is pinned to the commit whose CI was checked.
- **Fix:** a "Publish" button from an earlier draft no longer publishes a newer one.
- **Fix:** `Authorization` is dropped on redirects to another host or to `http`, and more token formats are redacted from errors.
- **Fix:** the model's `intent` is validated and escaped. Telegram attributes are escaped with quotes. AI text in PR bodies can't @mention GitHub users.
- External links get `rel="nofollow noopener noreferrer"` and their domains are listed for review.
- Hardened systemd unit (capabilities, syscalls, namespaces, `NoExecPaths`, read-only Claude Code binary, `UMask=0077`).
- The `vca` shortcut reads the config with `--env-file` as data instead of sourcing it with bash. The installer enforces `root:vca 0640` on it.
- CI: `persist-credentials: false`, timeouts and Dependabot for the pinned actions.

### Changed

- Owner-specific values are now settings:
  - `VCA_BRAND`, `VCA_OWNER` and `VCA_AUTHOR` (used in the prompts) are required;
  - `VCA_GITHUB_REPO`, `VCA_GSC_SITE` and `VCA_SITE_URL` have no defaults;
  - `VCA_DEPLOY_COMMAND` is new.
- CI is read through GitHub Actions when the token can't read check runs (fine-grained tokens don't support Checks).
- `vca check` verifies that CI can be read. `vca check --claude` makes one real call.

## [0.1.0] - 2026-10-31

- First version: research (Search Console and Google autocomplete), weekly plan, drafts with Claude Code, PRs, CI watch, merge, live check, measurement and learning, monthly services report, Telegram bot, systemd installer.

# vertice-content-agent

[Leer en español](README.es.md)

A content agent for the blog of a Laravel website. It runs on your VPS as a systemd service, researches what people search for, drafts articles with Claude Code and asks for your approval on Telegram before anything gets published.

I built it for the [VérticeDev](https://verticedev.es) blog and I'm releasing it as is, under the MIT license. Prompts and messages are in Spanish.

- **Researches** every Monday what people search for: real Search Console queries (90 days) and Google autocomplete, seeded with your services, your top queries and any seeds you add.
- **Proposes** three topics a week: improvements to articles that already get impressions, and new articles with demand that relate to one of your services. Every proposal comes with its numbers and an angle only you can bring.
- **Drafts** with Claude Code on your subscription, not the API. It validates the draft against the site's rules, writes the entry into the `Blog.php` registry plus its Blade view and opens a PR.
- **Publishes** when you tap "Publicar": it rebuilds the branch on top of the current `main`, waits for green CI and merges. You still run the deploy yourself.
- **Learns** by measuring each article in Search Console at 14, 28 and 56 days, and from what you approve, reject and comment.
- **Reviews your services** on the 1st of every month: which services get searches, which don't, and what demand none of them covers.

Python standard library only (3.9+), SQLite and Telegram long polling: no dependencies and no open ports.

## How it works

```
Monday 08:00 ─ research ─ Search Console + autocomplete → scored candidates
              └ plan ──── Claude picks 3 data-backed topics → Telegram [Draft] [Adjust] [Reject]
Draft ─────── Claude writes content blocks (not HTML) → validation → Blade view + Blog.php → PR → Telegram
              [Publish] [Request changes] [Discard]
Publish ───── branch rebuilt on main with today's date → green CI → merge of the reviewed commit
every 15 min ─ is it live? → measure at 14, 28 and 56 days → adjust the weights
1st of month ─ services report + learning memo
```

### What "learning" means here

No model is trained. There is an improvement loop with three parts:

1. **Weighted scoring.** Each keyword gets signals between 0 and 1: impressions, position opportunity (4–20), autocomplete demand, fit with a service, whether an article already covers it, and intent (commercial, comparison, local). The score is the weighted sum.
2. **Outcome-based updates.** At 28 and 56 days after publishing, the agent compares what it predicted with what Search Console measured and updates the weights with gradient descent: signals that were right gain weight. With little traffic, the first useful updates take two or three months.
3. **Your decisions.** What you approve and reject, by category, intent and kind, multiplies the score. Rejection reasons and your comments on drafts go into a monthly memo that Claude summarises and that is used when planning.

`/aprendizaje` in Telegram shows the weights, your preferences and the memo.

### What it doesn't do

- It never publishes without your tap.
- It doesn't deploy or touch the production folder of the site: it works on its own clone.
- It doesn't change your services: it only comments on them in the monthly report.
- It doesn't make up numbers or experiences: whatever it needs from you, it asks in the draft.

## Security

The agent treats everything the model writes and everything it reads from Google as untrusted: a piece of text could try to sneak into the article. So:

- **The model never writes HTML or Blade.** It returns content blocks that the agent turns into HTML. Every `@`, `{` and `}` in the text is stored as an HTML entity: readers see the same thing and Blade has nothing to compile. A final check rejects the draft if any Blade syntax is left outside the template.
- **Claude has no tools**: no file, network or shell access, in an empty folder and with only its own token.
- **Each process gets the minimum.** PHP and git never see the agent's tokens. Claude never receives an API key, so it always uses your subscription.
- **Only you** can use the bot: your user, in your private chat with it.
- **Nothing is merged without you.** Each "Publish" button is tied to its draft, and the merge only happens on the commit whose CI passed.
- **A sandboxed systemd service**: it can only write to its state folder, can't execute anything from it except Claude Code, and can't modify that binary.

Full threat model and how to report a vulnerability: [SECURITY.md](SECURITY.md).

## Telegram commands

| Command | What it does |
|---|---|
| `/plan` | Open proposals and drafts |
| `/investigar` | Research and plan now |
| `/estado` | What it's doing and what's pending |
| `/aprendizaje` | Weights, preferences and memo |
| `/informe` | Services report now |
| `/cancelar` | Forget the reply it was waiting for |

## What it expects from your site

It is built for a Laravel site that stores its blog the way VérticeDev does:

- an article registry in `app/Support/Blog.php`: a private `posts()` method returning an array, a `CATEGORIES` constant, and `Services` and `Portfolio` registries next to it;
- one view per article in `resources/views/blog/<slug>.blade.php` that extends `layouts.article`;
- GitHub Actions CI that renders the pages, so a broken article never reaches `main`.

If your site is different, the parts to adapt are `vca/site.py` and `vca/render.py`. Everything else (research, learning, Telegram, GitHub) doesn't depend on Laravel. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Setup

### 1. On your computer

1. **Telegram bot.** Create it with @BotFather and keep the token. Message it privately and get your id from `https://api.telegram.org/bot<TOKEN>/getUpdates` (`message.chat.id`).
2. **Claude Code.** `claude setup-token` creates a long-lived token for your subscription: that's `CLAUDE_CODE_OAUTH_TOKEN`.
3. **Search Console.** In Google Cloud, create an OAuth client of type "Desktop app", enable the Search Console API and set the publishing status to "In production" (in "Testing", the token expires after 7 days). Then:

   ```bash
   python -m vca gsc-auth --client-secrets path/to/client.json
   ```

   Sign in with the owner account. It prints the three `VCA_GSC_*` variables, with read-only access.
4. **GitHub.** Create a fine-grained token for your site's repository only, with Contents and Pull requests read and write, and Actions, Commit statuses and Metadata read. Fine-grained tokens don't support Checks: the agent reads CI through Actions.

### 2. On the VPS (as root)

```bash
git clone https://github.com/miguelmartt/vertice-content-agent.git /root/vertice-content-agent
bash /root/vertice-content-agent/deploy/install.sh
runuser -u vca -- env HOME=/var/lib/vertice-content-agent bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
nano /etc/vertice-content-agent/env      # your details and tokens
bash /root/vertice-content-agent/deploy/install.sh   # checks everything and starts the service
vca check --claude                       # optional: one real call to Claude
journalctl -u vertice-content-agent -f
```

The installer creates the `vca` system user, copies the code to `/opt/vertice-content-agent`, keeps state in `/var/lib/vertice-content-agent` (SQLite and its clone of the site) and sandboxes the service. It won't start until `vca check` passes.

To update: `git -C /root/vertice-content-agent pull` and run `install.sh` again.

To try it without opening PRs, set `VCA_DRY_RUN=1`: Telegram works, but no branches are pushed.

## Command line

On the VPS, the installer adds a `vca` shortcut that runs as the service user with its configuration:

```bash
vca check            # configuration and connections (--claude: also one real call)
vca research         # candidates in the console, no Claude or Telegram
vca plan             # plan now
vca learn            # measure and learn now
vca report           # services report now
vca status           # status and weights
```

On your computer, the same with `python -m vca --env-file my.env <command>` from the repository folder.

## Known limits

- Google autocomplete has no official API: it gives demand signals, not volumes. The agent makes at most 300 requests a week, with pauses, and if Google throttles it (429) it stops until the next week.
- Claude Code uses your subscription's quota. There's a daily call cap (`VCA_LLM_MAX_CALLS_PER_DAY`); a weekly plan plus one draft take about 2 or 3 calls.
- With little traffic, outcome-based learning is slow. Until there's data, the default signals and your decisions drive the scoring.

## Tests

```bash
python -m unittest discover -s tests -t .
```

They include a full run with fakes for Telegram, GitHub and Search Console (plan, approval, draft, PR, CI, merge, production and measurement) and the security cases: Blade injection, bot authorization, stale buttons, and secrets in errors and subprocesses.

## License

[MIT](LICENSE) © 2026 VerticeDev

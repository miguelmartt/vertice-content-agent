# Contributing

Issues and pull requests are welcome.

## Ground rules

- **Standard library only.** The agent runs on a stock VPS Python (3.9 on AlmaLinux 9) with no virtualenv. Don't add dependencies.
- **Python 3.9 compatible.** CI runs the suite on 3.9 and 3.12. Watch for 3.10+ APIs (`match`, `X | Y` outside annotations, `Path.write_text(newline=...)`, `zip(strict=...)`).
- **Human in the loop.** Nothing reaches the website without a button press in Telegram. Keep it that way: new actions that change the website repo, GitHub or the Telegram chat need an explicit confirmation step.
- **Treat model output and search data as untrusted.** Anything that ends up in a Blade view or in `Blog.php` goes through `vca/render.py` and its validation. Don't write raw model output to disk.

## Local development

```bash
python -m unittest discover -s tests -t .
bash -n deploy/install.sh
```

The tests use fakes for Telegram, GitHub, Search Console and Claude, so they need no network and no tokens. Tests that call PHP are skipped when `php` is not on the `PATH`.

To try the research step against your own Search Console property without Telegram or Claude:

```bash
python -m vca research --limit 20
```

## Adapting it to another site

The website side lives in two modules:

- `vca/site.py` reads the blog registry (`app/Support/Blog.php`) and writes new entries and views.
- `vca/render.py` turns the model's content blocks into a Blade view.

A site with a different structure needs its own version of both. The rest (research, learning, Telegram, GitHub) does not depend on Laravel.

## Reporting security issues

Please don't open a public issue. See [SECURITY.md](SECURITY.md). Never include real tokens, chat ids or hostnames in issues, pull requests or test fixtures.

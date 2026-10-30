import shutil
import tempfile
import unittest
from pathlib import Path

from vca import site
from vca.site import SiteRepo

BLOG_PHP = """<?php

namespace App\\Support;

class Blog
{
    public const CATEGORIES = ['DevOps', 'Negocio'];

    public static function all(): array
    {
        return self::posts();
    }

    /**
     * Artículos.
     */
    private static function posts(): array
    {
        return [
            ['slug' => 'uno', 'title' => 'Uno', 'excerpt' => 'Primero', 'category' => 'DevOps', 'date' => '2026-01-01', 'reading' => '4 min'],
            ['slug' => 'dos', 'title' => 'Dos', 'excerpt' => 'Segundo', 'category' => 'Negocio', 'date' => '2025-12-01', 'reading' => '5 min'],
        ];
    }
}
"""

SERVICES_PHP = """<?php

namespace App\\Support;

class Services
{
    private static function services(): array
    {
        return [['slug' => 'web', 'name' => 'Webs', 'group' => 'software', 'seo' => ['title' => 'Webs']]];
    }
}
"""

PORTFOLIO_PHP = """<?php

namespace App\\Support;

class Portfolio
{
    private static function projects(): array
    {
        return [['slug' => 'arraystock', 'name' => 'ArrayStock']];
    }
}
"""


class RegistryTest(unittest.TestCase):
    def test_registry_line_escapes_and_orders(self):
        line = site.registry_line({"reading": "6 min", "slug": "x", "title": "It's", "excerpt": "e",
                                   "category": "DevOps", "date": "2026-10-30"})
        self.assertEqual(
            line,
            "            ['slug' => 'x', 'title' => 'It\\'s', 'excerpt' => 'e', 'category' => 'DevOps', "
            "'date' => '2026-10-30', 'reading' => '6 min'],",
        )

    def test_insert_post_goes_first(self):
        out = site.insert_post(BLOG_PHP, "            ['slug' => 'nuevo'],")
        posts = out.split("return [\n", 1)[1]
        self.assertTrue(posts.startswith("            ['slug' => 'nuevo'],\n            ['slug' => 'uno'"))

    def test_replace_post(self):
        out = site.replace_post(BLOG_PHP, "dos", "            ['slug' => 'dos', 'modified' => 'x'],")
        self.assertIn("['slug' => 'dos', 'modified' => 'x'],", out)
        self.assertNotIn("'Segundo'", out)
        self.assertIn("'Primero'", out)

    def test_replace_missing_post_fails(self):
        with self.assertRaises(site.SiteError):
            site.replace_post(BLOG_PHP, "tres", "x")

    def test_extract_sections(self):
        view = ("@extends('layouts.article', [])\n\n@section('intro')\n    Hola <strong>mundo</strong>\n@endsection\n\n"
                "@section('body')\n    <h2>Uno</h2>\n    <p>Ver <a href=\"{{ url('/blog/x') }}\">esto</a></p>\n"
                "    <ul>\n        <li>a</li>\n    </ul>\n@endsection\n")
        sections = site.extract_sections(view)
        self.assertEqual(sections["intro"], "Hola mundo")
        self.assertIn("## Uno", sections["body"])
        self.assertIn("- a", sections["body"])


@unittest.skipUnless(shutil.which("php"), "hace falta PHP")
class SiteRepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        support = self.tmp / "app" / "Support"
        support.mkdir(parents=True)
        (support / "Blog.php").write_text(BLOG_PHP, encoding="utf-8")
        (support / "Services.php").write_text(SERVICES_PHP, encoding="utf-8")
        (support / "Portfolio.php").write_text(PORTFOLIO_PHP, encoding="utf-8")
        (self.tmp / "resources" / "views" / "blog").mkdir(parents=True)
        self.repo = SiteRepo(self.tmp, "o/r", "", "main", "git", "php", "Bot", "bot@example.com")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_reads_private_registries(self):
        data = self.repo.load()
        self.assertEqual([p["slug"] for p in data.posts], ["uno", "dos"])
        self.assertEqual(data.categories, ["DevOps", "Negocio"])
        self.assertIn("/servicios/web", data.paths)
        self.assertIn("/portfolio/arraystock", data.paths)

    def test_write_new_article_keeps_php_valid(self):
        entry = {"slug": "tres", "title": "Tres", "excerpt": "It's new", "category": "DevOps",
                 "date": "2026-10-30", "reading": "7 min"}
        paths = self.repo.write_article("tres", "@section('body')\n@endsection\n", entry, "new")
        self.repo.lint(paths)
        self.assertEqual([p["slug"] for p in self.repo.load().posts], ["tres", "uno", "dos"])
        self.assertTrue((self.tmp / "resources/views/blog/tres.blade.php").exists())

    def test_write_improvement_replaces_entry(self):
        entry = {"slug": "dos", "title": "Dos mejor", "excerpt": "Segundo", "category": "Negocio",
                 "date": "2025-12-01", "modified": "2026-10-30", "reading": "9 min"}
        self.repo.write_article("dos", "x", entry, "improve")
        post = self.repo.load().posts[1]
        self.assertEqual(post["title"], "Dos mejor")
        self.assertEqual(post["modified"], "2026-10-30")


if __name__ == "__main__":
    unittest.main()

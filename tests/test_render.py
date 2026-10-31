import unittest

from vca import render
from tests.helpers import CATEGORIES, good_draft, site_data

PATHS = site_data().paths


def validate(draft, kind="new", slug="software-gestion-medida", min_words=900):
    site = site_data()
    return render.validate(draft, kind=kind, slug=slug, existing_slugs=site.post_slugs,
                           categories=CATEGORIES, site_paths=site.paths, min_words=min_words)


class InlineTest(unittest.TestCase):
    def test_escapes_html(self):
        self.assertEqual(render.render_inline("<script>x</script> & y", PATHS), "&lt;script&gt;x&lt;/script&gt; &amp; y")

    def test_neutralizes_blade(self):
        out = render.render_inline("Usa {{ $x }} y {!! $y !!} o @foreach, pero hola@verticedev.es", PATHS)
        self.assertNotRegex(out, r"[@{}]")
        self.assertIn("&#123;&#123; $x &#125;&#125;", out)
        self.assertIn("&#64;foreach", out)
        self.assertIn("hola&#64;verticedev.es", out)

    def test_blade_bypasses_are_neutralized(self):
        # Blade no usa \w Unicode: una letra acentuada antes de @ no la protegía. Y los
        # comentarios {{-- --}} se quitaban antes de compilar, dejando un @ suelto.
        for payload in ("está@php(system('id'))", "nº@include('x')", "x {{--a--}}php(system('id'))"):
            out = render.render_inline(payload, PATHS)
            self.assertNotRegex(out, r"[@{}]", payload)
            draft = good_draft()
            draft["blocks"].append({"type": "p", "text": payload})
            draft["blocks"].append({"type": "table", "headers": [payload, "b"], "rows": [[payload, "c"]]})
            self.assertEqual(validate(draft), [], payload)
            self.assertNotRegex(render.TEMPLATE_RE.sub("", render.render_view(draft, PATHS)), r"[@{}]", payload)

    def test_bold_code_and_internal_link(self):
        out = render.render_inline("**Clave**: usa `pnpm` y mira [el servicio](/servicios/apps-medida/)", PATHS)
        self.assertIn("<strong>Clave</strong>", out)
        self.assertIn("<code>pnpm</code>", out)
        self.assertIn("<a href=\"{{ url('/servicios/apps-medida') }}\">el servicio</a>", out)

    def test_code_span_is_not_formatted(self):
        self.assertIn("<code>**no**</code>", render.render_inline("`**no**`", PATHS))

    def test_unknown_internal_link_is_an_error(self):
        errors = []
        out = render.render_inline("[x](/blog/no-existe)", PATHS, errors)
        self.assertEqual(out, "x")
        self.assertTrue(errors)

    def test_external_link_and_rejected_scheme(self):
        errors = []
        out = render.render_inline("[docs](https://laravel.com/docs) y [mal](javascript:alert(1))", PATHS, errors)
        self.assertIn('href="https://laravel.com/docs" target="_blank" rel="nofollow noopener noreferrer"', out)
        self.assertNotIn("javascript:", out.split("mal")[0])
        self.assertTrue(errors)

    def test_internal_link_path_must_be_plain(self):
        errors = []
        paths = PATHS | {"/blog/x')}}{{system('id')"}
        out = render.render_inline("[x](/blog/x')}}{{system('id'))", paths, errors)
        self.assertNotIn("url(", out)
        self.assertTrue(errors)


class BlocksTest(unittest.TestCase):
    def test_code_block_is_escaped_without_verbatim(self):
        errors = []
        code = "<?php echo '{{ x }}'; @endverbatim @php"
        out = render.render_blocks([{"type": "code", "language": "php", "code": code}], PATHS, errors)
        self.assertNotIn("@verbatim", out)
        self.assertIn("&lt;?php echo &#x27;&#123;&#123; x &#125;&#125;&#x27;; &#64;endverbatim &#64;php", out)
        self.assertNotRegex(out, r"[@{}]")
        self.assertEqual(errors, [])

    def test_unknown_language_is_an_error(self):
        errors = []
        render.render_blocks([{"type": "code", "language": "rust", "code": "fn main() {}"}], PATHS, errors)
        self.assertTrue(errors)

    def test_table_has_data_labels(self):
        out = render.render_blocks(
            [{"type": "table", "headers": ["Aspecto", "npm"], "rows": [["Disco", "**mucho**"]]}], PATHS, [])
        self.assertIn('<table class="table-stack">', out)
        self.assertIn('<th scope="col">Aspecto</th>', out)
        self.assertIn('<td data-label="npm"><strong>mucho</strong></td>', out)


class ViewTest(unittest.TestCase):
    def test_view_structure(self):
        view = render.render_view(good_draft(), PATHS)
        self.assertTrue(view.startswith("@extends('layouts.article', [\n"))
        self.assertIn("'seoTitle' => 'Software de gestión a medida: cómo elegir',", view)
        self.assertIn("@section('intro')", view)
        self.assertIn("@section('body')", view)
        self.assertNotIn("<h1", view)

    def test_php_strings_are_escaped(self):
        draft = good_draft()
        draft["cta"] = "¿Hablamos? It's fine \\ ok"
        self.assertIn("'cta' => '¿Hablamos? It\\'s fine \\\\ ok',", render.render_view(draft, PATHS))


class ValidateTest(unittest.TestCase):
    def test_good_draft_passes(self):
        self.assertEqual(validate(good_draft()), [])

    def test_length_limits(self):
        draft = good_draft()
        draft["description"] = "Demasiado corta"
        draft["seoTitle"] = "x" * 61
        errors = validate(draft)
        self.assertTrue(any("description" in e for e in errors))
        self.assertTrue(any("seoTitle" in e for e in errors))

    def test_blade_in_metadata_is_rejected(self):
        draft = good_draft()
        draft["headline"] = "Usa @vite en Laravel"
        draft["keywords"] = ["laravel", "x {{-- y"]
        errors = validate(draft)
        self.assertTrue(any("headline" in e for e in errors))
        self.assertTrue(any("palabra clave" in e for e in errors))

    def test_external_links_are_listed(self):
        draft = good_draft()
        draft["blocks"].append({"type": "p", "text": "Ver [docs](https://Laravel.com/docs) y [otra](https://example.org/a)"})
        self.assertEqual(render.external_links(draft), ["example.org", "laravel.com"])

    def test_debug_calls_rejected_even_in_code(self):
        draft = good_draft()
        draft["blocks"].append({"type": "code", "language": "php", "code": "dd($user);"})
        self.assertTrue(any("dd(" in e for e in validate(draft)))

    def test_thin_content_and_few_sections(self):
        errors = validate(good_draft(words_per_paragraph=10, sections=2))
        self.assertTrue(any("palabras" in e for e in errors))
        self.assertTrue(any("<h2>" in e for e in errors))

    def test_slug_rules(self):
        self.assertTrue(any("Ya existe" in e for e in validate(good_draft(), slug="pnpm-vs-npm-ventajas")))
        self.assertTrue(any("No existe" in e for e in validate(good_draft(), kind="improve", slug="nuevo")))
        self.assertEqual(validate(good_draft(), kind="improve", slug="pnpm-vs-npm-ventajas"), [])

    def test_reading_time(self):
        self.assertEqual(render.finalize(good_draft())["reading"], "5 min")

    def test_preview_has_absolute_links(self):
        html = render.render_preview(good_draft(), PATHS, origin="https://verticedev.es")
        self.assertIn('href="https://verticedev.es/servicios/apps-medida"', html)


if __name__ == "__main__":
    unittest.main()

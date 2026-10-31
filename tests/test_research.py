import unittest

from vca import learning, research
from vca.suggest import Suggest, Suggestion
from tests.helpers import site_data

SITE_URL = "https://verticedev.es"


class IntentTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(research.classify_intent("aplicaciones moviles murcia"), "local")
        self.assertEqual(research.classify_intent("software a medida para empresas"), "commercial")
        self.assertEqual(research.classify_intent("npm o pnpm"), "comparison")
        self.assertEqual(research.classify_intent("pnpm vs npm"), "comparison")
        self.assertEqual(research.classify_intent("qué es pnpm"), "informational")


class AggregateTest(unittest.TestCase):
    def test_sums_and_weights_position(self):
        rows = [
            {"query": "npm o pnpm", "page": f"{SITE_URL}/blog/pnpm-vs-npm-ventajas", "clicks": 1, "impressions": 90, "position": 7.0},
            {"query": "npm o pnpm", "page": f"{SITE_URL}/blog", "clicks": 0, "impressions": 10, "position": 17.0},
        ]
        cand = research.aggregate_gsc(rows)["npm o pnpm"]
        self.assertEqual(cand.impressions, 100)
        self.assertEqual(cand.clicks, 1)
        self.assertEqual(cand.position, 8.0)
        self.assertTrue(cand.page.endswith("/blog/pnpm-vs-npm-ventajas"))


class CoverageTest(unittest.TestCase):
    def test_coverage_by_gsc_page(self):
        cand = research.Candidate("diferencia entre npm y pnpm", page=f"{SITE_URL}/blog/pnpm-vs-npm-ventajas", position=7.0)
        self.assertEqual(research.coverage(cand, site_data(), SITE_URL), "pnpm-vs-npm-ventajas")

    def test_coverage_by_terms(self):
        cand = research.Candidate("laravel horizon produccion")
        self.assertEqual(research.coverage(cand, site_data(), SITE_URL), "laravel-queues-horizon-produccion")

    def test_uncovered(self):
        self.assertEqual(research.coverage(research.Candidate("agentes de ia para pymes"), site_data(), SITE_URL), "")

    def test_service_match(self):
        self.assertEqual(research.match_service("software de gestión a medida", site_data()), "apps-medida")
        self.assertEqual(research.match_service("receta de paella", site_data()), "")


class CandidatesTest(unittest.TestCase):
    def test_build_merges_sources_and_scores(self):
        rows = [{"query": "software de gestion a medida", "page": f"{SITE_URL}/servicios/apps-medida",
                 "clicks": 0, "impressions": 140, "position": 64.2}]
        suggestions = {
            "software de gestion a medida": Suggestion("software de gestión a medida", hits=3, best_rank=2),
            "pnpm que es": Suggestion("pnpm que es", hits=1, best_rank=5),
        }
        cands = research.build_candidates(rows, suggestions, site_data(), SITE_URL,
                                          learning.merged_weights({}), {})
        top = cands[0]
        self.assertEqual(top.impressions, 140)
        self.assertEqual(top.suggest_hits, 3)
        self.assertEqual(top.service, "apps-medida")
        self.assertEqual(top.features["demand"], 0.75)
        self.assertGreater(top.score, cands[-1].score)

    def test_seeds_are_unique(self):
        seeds = research.seeds_from(site_data(), {}, ["Agentes IA", "agentes ia"])
        self.assertEqual(len(seeds), len({s.lower() for s in seeds}))
        self.assertIn("agentes ia", seeds)


class SuggestTest(unittest.TestCase):
    def test_expand_counts_hits_and_rank(self):
        calls = []

        def fetch(q):
            calls.append(q)
            return ["pnpm vs npm", "pnpm install"] if "pnpm" in q else []

        s = Suggest(fetch=fetch, sleep=lambda _: None, max_requests=100)
        found = s.expand(["pnpm"])
        self.assertEqual(found["pnpm vs npm"].hits, len(calls))
        self.assertEqual(found["pnpm vs npm"].best_rank, 1)
        self.assertEqual(found["pnpm install"].best_rank, 2)

    def test_respects_request_cap(self):
        s = Suggest(fetch=lambda q: ["x"], sleep=lambda _: None, max_requests=3)
        s.expand(["a", "b", "c"])
        self.assertEqual(s.requests, 3)


if __name__ == "__main__":
    unittest.main()

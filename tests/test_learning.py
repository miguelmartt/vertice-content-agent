import unittest

from vca import learning


class LearningTest(unittest.TestCase):
    def test_outcome_score_bounds(self):
        self.assertEqual(learning.outcome_score(0, 0, None), 0.0)
        self.assertEqual(learning.outcome_score(5000, 50, 3.0), 1.0)
        self.assertGreater(learning.outcome_score(300, 2, 15.0), learning.outcome_score(30, 0, 60.0))

    def test_improve_score_neutral_and_direction(self):
        self.assertAlmostEqual(learning.improve_score(100, 100, 2, 2), 0.5)
        self.assertGreater(learning.improve_score(100, 400, 1, 6), 0.5)
        self.assertLess(learning.improve_score(400, 100, 6, 1), 0.5)

    def test_update_moves_prediction_toward_target(self):
        weights = learning.merged_weights({})
        features = {"bias": 1.0, "impressions": 0.8, "demand": 0.5}
        before = learning.predict(weights, features)
        updated = learning.update_weights(weights, features, target=0.0)
        self.assertLess(learning.predict(updated, features), before)
        self.assertEqual(updated["opportunity"], weights["opportunity"])  # señal ausente, peso intacto

    def test_update_with_zero_factor_is_noop(self):
        weights = learning.merged_weights({})
        self.assertEqual(learning.update_weights(weights, {"bias": 1.0}, 1.0, factor=0.0), weights)

    def test_weights_are_bounded(self):
        weights = learning.merged_weights({})
        for _ in range(500):
            weights = learning.update_weights(weights, {"impressions": 1.0}, target=1.5)
        self.assertLessEqual(weights["impressions"], learning.WEIGHT_BOUNDS[1])

    def test_preferences(self):
        neutral = learning.pref_multiplier({}, {"category": "DevOps", "intent": "comparison", "kind": "new"})
        self.assertAlmostEqual(neutral, 1.0)
        prefs = {("category", "Negocio"): (0, 4)}
        disliked = learning.pref_multiplier(prefs, {"category": "Negocio", "intent": "x", "kind": "new"})
        self.assertLess(disliked, 1.0)

    def test_stored_weights_override_defaults_only_for_known_names(self):
        merged = learning.merged_weights({"impressions": 0.9, "ruido": 3.0})
        self.assertEqual(merged["impressions"], 0.9)
        self.assertNotIn("ruido", merged)


if __name__ == "__main__":
    unittest.main()

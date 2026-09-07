import unittest

from ai_enrichment import _parse_ai_results


class AIEnrichmentTests(unittest.TestCase):
    def test_parse_valid_results(self):
        content = """{
          "results": [
            {"id": 0, "relevant": true, "summary": "A mental-health call.", "reason": "Direct topic match."},
            {"id": 1, "relevant": false, "summary": "An engineering call.", "reason": "Outside the selected topics."}
          ]
        }"""
        results = _parse_ai_results(content)
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["relevant"])
        self.assertFalse(results[1]["relevant"])

    def test_ignore_malformed_result(self):
        content = '{"results":[{"id":"zero","relevant":"yes"}]}'
        self.assertEqual(_parse_ai_results(content), [])


if __name__ == "__main__":
    unittest.main()

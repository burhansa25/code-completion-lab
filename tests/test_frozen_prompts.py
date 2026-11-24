"""Golden prompt hashes captured from original, retired lab prompt implementations."""
import hashlib
import unittest
from benchmark_v2.prompts import make_prompt, PREFIX_SUFFIX
from benchmark_v2 import cases

class FrozenPromptTests(unittest.TestCase):
    def test_smallbasic_template_preserves_historical_prompt_bytes(self):
        prompts = [
            ("TextWindow", None, "a18b893bcd465ad0358266ce"),
            ("TextWindow", ". ID ( Exprs )", "00661aa73e57a1678f14ed03"),
            ("a = 7", "NUM", "1cfa028f170e08d403903625"),
            ("", None, "3fd7f02a7c8524f6abfad5ef"),
        ]
        for prefix, structure, original_hash_prefix in prompts:
            with self.subTest(prefix=prefix, structure=structure):
                actual = hashlib.sha256(make_prompt(prefix, structure).encode()).hexdigest()
                self.assertTrue(actual.startswith(original_hash_prefix))

    def test_c_fim_template_hash_and_scoring_profile(self):
        self.assertEqual(hashlib.sha256(PREFIX_SUFFIX.encode()).hexdigest(),
                         "86fc49a0db6e1aac4e87d5adc60bab0dd94af6497670d505062d51fa37435775")
        self.assertIn("PREFIX START", cases.build_prompt({"language": "c"}, "int x", ";", None,
                                                        profile="lab", completion="infill"))

if __name__ == '__main__':
    unittest.main()

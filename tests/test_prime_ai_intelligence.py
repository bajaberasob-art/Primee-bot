import unittest

import prime_ai_intelligence


class TestPrimeAIIntelligence(unittest.TestCase):
    def test_semantic_probe_skips_ordinary_chat(self):
        self.assertFalse(
            prime_ai_intelligence.should_probe_semantic_action(
                "وش رايك في الاسم الجديد؟",
                [{"role": "user", "content": "تكلم معي عن التصميم"}],
            )
        )

    def test_semantic_probe_catches_direct_and_indirect_commands(self):
        self.assertTrue(
            prime_ai_intelligence.should_probe_semantic_action(
                "غير اسم القناة"
            )
        )
        self.assertTrue(
            prime_ai_intelligence.should_probe_semantic_action(
                "خلها مثل قبل",
                [{"role": "user", "content": "غير اسم القناة إلى الأخبار"}],
            )
        )

    def test_semantic_probe_ignores_common_arabic_substrings(self):
        self.assertFalse(
            prime_ai_intelligence.should_probe_semantic_action(
                "خلني أفكر شوي قبل أرد عليك"
            )
        )
        self.assertFalse(
            prime_ai_intelligence.should_probe_semantic_action(
                "هذا الكلام غير مناسب للنقاش"
            )
        )

    def test_semantic_probe_does_not_promote_unrelated_pronoun_chat(self):
        self.assertFalse(
            prime_ai_intelligence.should_probe_semantic_action(
                "هذا ممتاز",
                [{"role": "user", "content": "شرح لي الفكرة"}],
            )
        )

    def test_repetition_guard_detects_same_answer_without_extra_api_call(self):
        conversation = [
            {"role": "assistant", "content": "تم تعديل القناة بنجاح ويمكنك الآن استخدامها."},
        ]
        self.assertTrue(
            prime_ai_intelligence.response_repeats_recent(
                "تم تعديل القناة بنجاح ويمكنك الآن استخدامها.",
                conversation,
            )
        )

    def test_repetition_guard_allows_different_answer(self):
        conversation = [
            {"role": "assistant", "content": "تم تعديل القناة بنجاح ويمكنك الآن استخدامها."},
        ]
        self.assertFalse(
            prime_ai_intelligence.response_repeats_recent(
                "القناة أصبحت جاهزة، وتقدر تستخدمها الآن.",
                conversation,
            )
        )

    def test_preference_signals_are_low_risk_and_explicit(self):
        self.assertEqual(
            prime_ai_intelligence.extract_preference_signals(
                "رد علي باختصار وبدون إيموجي وبالعربي"
            ),
            {
                "response_length": "short",
                "emoji_usage": 0,
                "language": "Arabic",
            },
        )


if __name__ == "__main__":
    unittest.main()

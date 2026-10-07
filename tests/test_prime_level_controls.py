import unittest

from streak_experience import DEFAULT_DUPLICATE_TEMPLATE
from prime_level_controls import (
    controls_with_defaults,
    render_template,
    validate_controls,
)


class PrimeLevelControlTests(unittest.TestCase):
    def test_defaults_preserve_legacy_rank_channels_and_levelup_title(self):
        controls = controls_with_defaults(
            {},
            {
                "command_rank_channels": [123, "456"],
                "levelup_title": "Legacy title",
            },
        )
        self.assertTrue(controls["rank"]["imageOnly"])
        self.assertEqual(controls["rank"]["channels"], ["123", "456"])
        self.assertEqual(controls["levelup"]["embedTitle"], "Legacy title")
        self.assertFalse(controls["periodic"]["daily"]["enabled"])

    def test_unknown_message_variables_are_safe(self):
        self.assertEqual(
            render_template("Hi {user}: {future_variable}", {"user": "A"}),
            "Hi A: {future_variable}",
        )

    def test_duplicate_template_migrates_only_the_old_default(self):
        legacy = (
            "🔥 تم تسجيل ستريكك اليوم بالفعل.\n"
            "ستريكك الحالي: {streak} يوم\n"
            "أفضل ستريك: {best} يوم\n"
            "المرحلة: {stage_name}\n"
            "ارجع غدًا لتسجيل اليوم التالي."
        )
        controls = controls_with_defaults({
            "streak": {"messages": {"duplicate": {"message": legacy}}}
        })
        self.assertEqual(
            controls["streak"]["messages"]["duplicate"]["message"],
            DEFAULT_DUPLICATE_TEMPLATE,
        )

        custom = "قالب مخصص {streak}"
        controls = controls_with_defaults({
            "streak": {"messages": {"duplicate": {"message": custom}}}
        })
        self.assertEqual(
            controls["streak"]["messages"]["duplicate"]["message"],
            custom,
        )

    def test_inactive_streak_templates_and_records_are_preserved(self):
        controls = controls_with_defaults({
            "streak": {
                "messages": {
                    "success": {"enabled": True, "message": "نجاح قديم {streak}"},
                    "stageUp": {"enabled": True, "message": "مرحلة قديمة {stage_name}"},
                    "milestone": {"enabled": True, "message": "إنجاز قديم {threshold}"},
                },
                "stages": [{
                    "stage_key": "spark",
                    "threshold": 3,
                    "name": "شرارة",
                    "message": "رسالة المرحلة القديمة",
                }],
                "milestones": [{
                    "threshold": 7,
                    "message": "رسالة الإنجاز القديمة",
                    "enabled": True,
                }],
            }
        })
        self.assertEqual(
            controls["streak"]["messages"]["success"]["message"],
            "نجاح قديم {streak}",
        )
        self.assertEqual(
            controls["streak"]["messages"]["stageUp"]["message"],
            "مرحلة قديمة {stage_name}",
        )
        self.assertEqual(
            controls["streak"]["messages"]["milestone"]["message"],
            "إنجاز قديم {threshold}",
        )
        self.assertEqual(
            controls["streak"]["stages"][0]["message"],
            "رسالة المرحلة القديمة",
        )
        self.assertEqual(
            controls["streak"]["milestones"][0]["message"],
            "رسالة الإنجاز القديمة",
        )

    def test_validation_normalizes_ids_and_rejects_enabled_schedule_without_channel(self):
        controls = controls_with_defaults()
        controls["rank"]["channels"] = ["123"]
        controls["levelup"]["mentionRole"] = "45"
        controls["periodic"]["daily"].update({
            "enabled": True,
            "channel": "678",
            "timezone": "Asia/Aden",
            "time": "09:30",
        })
        checked = validate_controls(
            controls,
            {},
            validate_channel=lambda raw, messageable=False: int(raw),
            validate_role=lambda raw: int(raw),
            validate_assignable_role=lambda raw: int(raw),
        )
        self.assertEqual(checked["rank"]["channels"], ["123"])
        self.assertEqual(checked["levelup"]["mentionRole"], "45")
        self.assertEqual(checked["periodic"]["daily"]["channel"], "678")

        controls["periodic"]["daily"]["channel"] = ""
        with self.assertRaisesRegex(ValueError, "requires a channel"):
            validate_controls(
                controls,
                {},
                validate_channel=lambda raw, messageable=False: int(raw),
                validate_role=lambda raw: int(raw),
                validate_assignable_role=lambda raw: int(raw),
            )


if __name__ == "__main__":
    unittest.main()
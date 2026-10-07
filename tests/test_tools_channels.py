import ast
import unittest
from pathlib import Path

from cogs.command_meta import MASTER_COMMANDS_REGISTRY


def _command_names():
    tree = ast.parse(Path("cogs/tools_channels.py").read_text())
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "command"
            ):
                continue
            for keyword in decorator.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    names.append(keyword.value.value)
    return names


class ToolsChannelsTests(unittest.TestCase):
    def test_step_five_commands_are_unique_and_have_metadata(self):
        names = _command_names()
        self.assertEqual(len(names), 40)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(set(names).issubset(MASTER_COMMANDS_REGISTRY))

    def test_step_five_groups_are_present_in_main(self):
        source = Path("main.py").read_text()
        self.assertIn('"ToolsChannelsCog"', source)
        self.assertIn('"security_report"', source)
        self.assertIn('"steal_sticker"', source)

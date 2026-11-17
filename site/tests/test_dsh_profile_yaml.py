"""The site profile's cordis YAML must parse as intended, so every `!!js` value is one expression.

A `!!js` value is a plain YAML scalar. Unquoted, an expression with `: ` inside it (a ternary
`a ? b : c`) silently becomes a mapping: DSH 0.1.7's js-yaml accepted that and the config value was
garbage (`{"[object Object]": "..."}`, so SWARM_BOARD_PROJECTS never applied); DSH 0.2's config
editor rejects the whole file ("Nested mappings are not allowed in compact mappings"), and then no
setting can be saved at all (the 0.2 welcome notice could not be dismissed). Quote such expressions.

  cd site && PYTHONPATH=.:tests python3 -B -m unittest test_dsh_profile_yaml
"""
import pathlib, unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILES = sorted(p for p in ROOT.rglob("cordis*.yml")
               if "node_modules" not in p.parts and not any(x.startswith(".") for x in p.relative_to(ROOT).parts))


class JsExpr(str):
    """A `!!js` value: the expression text."""


class Loader(yaml.SafeLoader):
    pass


def _js(loader, node):
    if not isinstance(node, yaml.ScalarNode):
        raise yaml.constructor.ConstructorError(None, None, "!!js must be a scalar expression, got %s" % node.id,
                                                node.start_mark)
    return JsExpr(loader.construct_scalar(node))


Loader.add_constructor("tag:yaml.org,2002:js", _js)


def js_values(value, path="$"):
    if isinstance(value, JsExpr):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from js_values(v, "%s.%s" % (path, k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from js_values(v, "%s[%d]" % (path, i))


def mappings_under_js_keys(text):
    """Keys whose value line starts with `!!js` but parsed into a mapping (the silent failure)."""
    bad = []
    data = yaml.load(text, Loader=Loader)

    def walk(v, path):
        if isinstance(v, dict):
            for k, x in v.items():
                if isinstance(x, dict) and any(isinstance(kk, (JsExpr, dict)) or kk is None for kk in x):
                    bad.append("%s.%s" % (path, k))
                walk(x, "%s.%s" % (path, k))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, "%s[%d]" % (path, i))
    walk(data, "$")
    return bad


class ProfileYamlTest(unittest.TestCase):
    def test_there_are_profile_files(self):
        names = {p.relative_to(ROOT).as_posix() for p in FILES}
        self.assertIn("site/dsh-profile/cordis.patch.yml", names)

    def test_every_file_parses_and_every_js_value_is_one_expression(self):
        for p in FILES:
            with self.subTest(file=p.relative_to(ROOT).as_posix()):
                text = p.read_text(encoding="utf-8")
                data = yaml.load(text, Loader=Loader)  # raises on "mapping values are not allowed here"
                self.assertEqual(mappings_under_js_keys(text), [])
                for path, expr in js_values(data):
                    self.assertTrue(expr.strip(), "%s: empty !!js" % path)

    def test_board_projects_expression_parses_whole(self):
        data = yaml.load((ROOT / "site/dsh-profile/cordis.patch.yml").read_text(encoding="utf-8"), Loader=Loader)
        board = next(r for r in data if r.get("id") == "swarm-board")
        expr = board["config"]["projects"]
        self.assertIsInstance(expr, JsExpr)
        self.assertIn("SWARM_BOARD_PROJECTS", expr)
        self.assertIn(r"/^\d+$/", expr, "the number test keeps exactly one backslash")
        self.assertTrue(expr.rstrip().endswith("x !== 0)"), "the whole expression, not a fragment after ': '")

    def test_an_unquoted_ternary_is_caught(self):
        broken = "- id: x\n  config:\n    v: !!js a ? b : c\n"
        with self.assertRaises(yaml.YAMLError):
            yaml.load(broken, Loader=Loader)


if __name__ == "__main__":
    unittest.main()

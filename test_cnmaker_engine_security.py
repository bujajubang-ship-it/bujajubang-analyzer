import ast
import pathlib
import unittest


class CnmakerEngineSecurityTest(unittest.TestCase):
    def test_sensitive_top_level_values_come_from_environment(self):
        engine = pathlib.Path(__file__).parent / "cnmaker_engine"
        sensitive = ("KEY", "SECRET", "TOKEN", "PASSWORD")
        failures = []
        for path in engine.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if not any(word in target.id.upper() for word in sensitive):
                        continue
                    dump = ast.dump(value)
                    if "getenv" not in dump and "environ" not in dump:
                        failures.append(f"{path.name}:{node.lineno}:{target.id}")
        self.assertEqual(failures, [], "민감정보로 보이는 값을 환경변수로 옮겨주세요: " + ", ".join(failures))


if __name__ == "__main__":
    unittest.main()

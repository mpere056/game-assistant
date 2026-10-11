"""Every source file must at least compile: the app itself isn't imported by the other tests, and a
broken string in app.py once reached main unnoticed (2026-10-10)."""
import pathlib
import py_compile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class CompileTests(unittest.TestCase):
    def test_all_sources_compile(self):
        bad = []
        for p in sorted((ROOT / 'game_assistant').rglob('*.py')):
            try:
                py_compile.compile(str(p), doraise=True, cfile=str(ROOT / 'runtime' / 'compile_check.pyc'))
            except py_compile.PyCompileError as e:
                bad.append(f'{p.relative_to(ROOT)}: {e.msg.splitlines()[-1]}')
        self.assertEqual(bad, [])

    def test_app_imports(self):
        import game_assistant.app  # noqa: F401

    def test_no_control_characters(self):
        """A regex's backslash-b once became a real backspace character (shell escaping), twice."""
        bad = []
        for p in sorted(list((ROOT / 'game_assistant').rglob('*.py')) + list((ROOT / 'tests').rglob('*.py'))):
            b = p.read_bytes()
            if any(c < 32 and c not in (9, 10, 13) for c in b):
                bad.append(str(p.relative_to(ROOT)))
        self.assertEqual(bad, [])

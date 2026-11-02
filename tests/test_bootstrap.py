"""처음 실행 점검 테스트 — PySide6 설치 제안(gui/bootstrap.py)과 원본 CSV 받기(tagdb.fetch_source, `tagorg fetch`).

실제 pip·네트워크·메시지 창은 쓰지 않는다(모두 바꿔 끼움).
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import ROOT, tagdb  # noqa: F401  (fixtures 가 저장소 루트를 sys.path 에 넣는다)
from gui import bootstrap

import tagorg  # noqa: E402


def fake_version(versions):
    def version(dist):
        if dist in versions:
            return versions[dist]
        raise importlib.metadata.PackageNotFoundError(dist)
    return version


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.spec = mock.patch.object(bootstrap.importlib.util, "find_spec", return_value=object())
        self.spec.start()
        self.addCleanup(self.spec.stop)

    def check(self, versions, importer=lambda name: None):
        with mock.patch.object(bootstrap.importlib.metadata, "version", fake_version(versions)):
            return bootstrap.check(importer)

    def test_required_version_from_requirements(self):
        self.assertGreaterEqual(bootstrap.required_version(), (6, 6))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "req.txt")
            path.write_text("# 주석\nPySide6>=6.8.1\n", encoding="utf-8")
            self.assertEqual(bootstrap.required_version(path), (6, 8, 1))
            path.write_text("pyside6_essentials >= 6.7\n", encoding="utf-8")
            self.assertEqual(bootstrap.required_version(path), (6, 7))
            self.assertEqual(bootstrap.required_version(Path(tmp, "없음.txt")), ())

    def test_missing(self):
        with mock.patch.object(bootstrap.importlib.util, "find_spec", return_value=None):
            self.assertEqual(bootstrap.check().kind, "missing")

    def test_old_version_detected_without_importing(self):
        imported = []
        problem = self.check({"PySide6": "6.5.3"}, imported.append)
        self.assertEqual(problem.kind, "old")
        self.assertIn("6.5.3", problem.detail)
        self.assertEqual(imported, [])          # 낡은 판을 불러와 DLL 을 잡으면 다시 설치가 실패한다

    def test_essentials_version_wins(self):
        self.assertIsNone(self.check({"PySide6-Essentials": "6.12.0", "PySide6": "6.5.0"}))

    def test_broken_import(self):
        def importer(name):
            raise ImportError("DLL load failed while importing QtCore")
        problem = self.check({"PySide6-Essentials": "6.12.0"}, importer)
        self.assertEqual(problem.kind, "broken")
        self.assertIn("DLL load failed", problem.detail)

    def test_ok_without_metadata(self):
        self.assertIsNone(self.check({}))      # 메타데이터 없이 깔린 경우(직접 빌드 등): 불러와지면 된다


class EnsureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patches = [
            mock.patch.object(bootstrap, "INSTALL_LOG", Path(self._tmp.name, "local", "gui-install.log")),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop(bootstrap.RETRY_ENV, None)
        self.told = []
        self.asked = []

    def run_ensure(self, problem, answer=True, pip_code=0, log=""):
        calls = []

        def call(cmd, **kw):
            calls.append((cmd, kw))
            if "pip" in cmd and "--log" in cmd:
                Path(cmd[cmd.index("--log") + 1]).write_text(log, encoding="utf-8")
                return pip_code
            return 0 if "ensurepip" in cmd else 7      # 다시 띄운 GUI 의 종료 코드

        def ask(dialog, text):
            self.asked.append(text)
            return answer

        with mock.patch.object(bootstrap, "check", return_value=problem), \
                mock.patch.object(bootstrap.subprocess, "call", side_effect=call), \
                mock.patch.object(bootstrap, "_ask", side_effect=ask), \
                mock.patch.object(bootstrap, "_tell", side_effect=lambda dialog, text: self.told.append(text)), \
                mock.patch.object(bootstrap.importlib.util, "find_spec", return_value=object()):
            code = bootstrap.ensure(dialog=True, relaunch=["pythonw", "tagorg_gui.pyw"])
        return code, calls

    def test_ok_starts_gui(self):
        code, calls = self.run_ensure(None)
        self.assertIsNone(code)
        self.assertEqual((calls, self.asked, self.told), ([], [], []))

    def test_install_then_relaunch(self):
        code, calls = self.run_ensure(bootstrap.Problem("missing", "없음"))
        self.assertEqual(code, 7)
        pip, relaunch = calls
        self.assertEqual(pip[0][1:4], ["-m", "pip", "install"])
        self.assertIn(str(bootstrap.REQUIREMENTS), pip[0])
        self.assertNotIn("--force-reinstall", pip[0])
        self.assertEqual(relaunch[0], ["pythonw", "tagorg_gui.pyw"])
        self.assertEqual(relaunch[1]["env"][bootstrap.RETRY_ENV], "1")
        self.assertIn("python", self.asked[0].lower())      # 어느 Python 에 까는지 보여 준다
        self.assertIn("-m pip install", self.asked[0])      # 직접 칠 명령도

    def test_declined_installs_nothing(self):
        code, calls = self.run_ensure(bootstrap.Problem("missing", "없음"), answer=False)
        self.assertEqual((code, calls), (1, []))

    def test_pip_failure_shows_errors(self):
        log = ("2026-10-29T01:00:00,000 Looking in indexes\n"
               "2026-10-29T01:00:01,000 ERROR: Could not find a version that satisfies the requirement\n"
               "ERROR: No matching distribution found for PySide6-Essentials>=6.6\n")
        code, calls = self.run_ensure(bootstrap.Problem("missing", "없음"), pip_code=1, log=log)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)                     # 다시 띄우지 않는다
        self.assertIn("ERROR: Could not find a version", self.told[0])
        self.assertIn("ERROR: No matching distribution", self.told[0])
        self.assertNotIn("2026-10-29T", self.told[0])
        self.assertIn(str(bootstrap.INSTALL_LOG), self.told[0])

    def test_broken_is_not_reinstalled_automatically(self):
        code, calls = self.run_ensure(bootstrap.Problem("broken", "DLL load failed"))
        self.assertEqual((code, calls, self.asked), (1, [], []))
        self.assertIn("--force-reinstall", self.told[0])

    def test_os_error_while_installing_is_reported(self):
        # 리뷰 지적: 로그 폴더·python 실행이 OSError 로 실패하면 안내 없이 끝났다
        for where in ("log", "call"):
            self.told.clear()
            with self.subTest(where), contextlib.ExitStack() as stack:
                if where == "log":
                    stack.enter_context(mock.patch.object(bootstrap.Path, "unlink", side_effect=PermissionError("잠김")))
                stack.enter_context(mock.patch.object(bootstrap, "check", return_value=bootstrap.Problem("missing", "")))
                stack.enter_context(mock.patch.object(bootstrap.subprocess, "call",
                                                      side_effect=FileNotFoundError("python 없음")))
                stack.enter_context(mock.patch.object(bootstrap, "_ask", return_value=True))
                stack.enter_context(mock.patch.object(bootstrap, "_tell",
                                                      side_effect=lambda dialog, text: self.told.append(text)))
                self.assertEqual(bootstrap.ensure(dialog=True, relaunch=["x"]), 1)
                self.assertIn("설치하지 못했습니다", self.told[0])
                self.assertIn("잠김" if where == "log" else "python 없음", self.told[0])
                self.assertIn("-m pip install", self.told[0])

    def test_pyw_reports_bootstrap_crash(self):
        import runpy
        with mock.patch.object(bootstrap, "ensure", side_effect=OSError("예상 못 한 오류")), \
                mock.patch.object(bootstrap, "report_crash") as report, \
                self.assertRaises(SystemExit) as ctx:
            runpy.run_path(str(ROOT / "tagorg_gui.pyw"), run_name="__main__")
        self.assertEqual(ctx.exception.code, 1)
        self.assertIsInstance(report.call_args[0][0], OSError)

    def test_after_install_does_not_ask_again(self):
        os.environ[bootstrap.RETRY_ENV] = "1"
        code, calls = self.run_ensure(bootstrap.Problem("missing", "없음"))
        self.assertEqual((code, calls, self.asked), (1, [], []))
        self.assertIn("설치했지만", self.told[0])

    def test_ensurepip_when_pip_missing(self):
        calls = []

        def call(cmd, **kw):
            calls.append(cmd)
            return 0

        with mock.patch.object(bootstrap.importlib.util, "find_spec", return_value=None), \
                mock.patch.object(bootstrap.subprocess, "call", side_effect=call):
            ok, errors = bootstrap.install(bootstrap.Problem("missing", ""), new_console=False)
        self.assertTrue(ok)
        self.assertEqual(calls[0][1:3], ["-m", "ensurepip"])
        self.assertEqual(calls[1][1:4], ["-m", "pip", "install"])

    def test_console_python_beside_pythonw(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("pythonw.exe", "python.exe"):
                Path(tmp, name).touch()
            with mock.patch.object(bootstrap.sys, "executable", str(Path(tmp, "pythonw.exe"))):
                self.assertEqual(Path(bootstrap.console_python()).name, "python.exe")
            with mock.patch.object(bootstrap.sys, "executable", str(Path(tmp, "python.exe"))):
                self.assertEqual(Path(bootstrap.console_python()).name, "python.exe")

    def test_console_without_tty_does_not_wait(self):
        err = io.StringIO()
        with mock.patch.object(bootstrap.sys, "stdin", io.StringIO("y\n")), contextlib.redirect_stderr(err):
            self.assertFalse(bootstrap._ask(False, "설치할까요"))
        self.assertIn("설치할까요", err.getvalue())


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FetchSourceTests(unittest.TestCase):
    DATA = b"1girl,0,100,\nsolo,0,90,\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name, "source")
        self.dest = self.dir / "src.csv"
        self.requests = []

    def opener(self, data):
        def open_(request, timeout):
            self.requests.append(request.full_url)
            return FakeResponse(data)
        return open_

    def entry(self, data=DATA, url="https://github.com/o/r/blob/main/tag-lists/src.csv"):
        return {"file": "src.csv", "url": url, "sha256": hashlib.sha256(data).hexdigest().upper()}

    def test_download_url(self):
        self.assertEqual(tagdb.download_url("https://github.com/o/r/blob/main/a/b.csv"),
                         "https://github.com/o/r/raw/main/a/b.csv")
        self.assertEqual(tagdb.download_url("https://example.com/blob/x.csv"), "https://example.com/blob/x.csv")

    def test_fetch_verifies_sha256(self):
        tagdb.fetch_source(self.entry(), self.dest, opener=self.opener(self.DATA))
        self.assertEqual(self.dest.read_bytes(), self.DATA)
        self.assertEqual(self.requests, ["https://github.com/o/r/raw/main/tag-lists/src.csv"])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["src.csv"])     # 임시 파일 남지 않음

    def test_mismatch_writes_nothing(self):
        with self.assertRaisesRegex(ValueError, "sha256"):
            tagdb.fetch_source(self.entry(), self.dest, opener=self.opener(b"<html>not found</html>"))
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_network_error_writes_nothing(self):
        def broken(request, timeout):
            raise OSError("연결 끊김")
        with self.assertRaises(OSError):
            tagdb.fetch_source(self.entry(), self.dest, opener=broken)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_needs_url_and_sha256(self):
        with self.assertRaisesRegex(ValueError, "url·sha256"):
            tagdb.fetch_source({"file": "src.csv", "url": "https://x"}, self.dest, opener=self.opener(self.DATA))

    def manifest(self, entry):
        self.dir.mkdir(parents=True, exist_ok=True)
        manifest = self.dir / "sources.json"
        manifest.write_text(json.dumps({"current": "src.csv", "files": [entry]}), encoding="utf-8")
        return mock.patch.multiple(tagdb, SOURCE_DIR=self.dir, MANIFEST=manifest)

    def test_current_source_missing_is_fetchable(self):
        with self.manifest(self.entry()):
            with self.assertRaises(tagdb.SourceMissing) as ctx:
                tagdb.current_source()
        self.assertIsInstance(ctx.exception, FileNotFoundError)     # 기존 except FileNotFoundError 도 그대로 잡는다
        self.assertTrue(ctx.exception.fetchable)
        self.assertEqual(ctx.exception.path, self.dest)
        self.assertIn("tagorg.py fetch", str(ctx.exception))
        with self.manifest({"file": "src.csv", "url": "https://x"}):
            with self.assertRaises(tagdb.SourceMissing) as ctx:
                tagdb.current_source()
        self.assertFalse(ctx.exception.fetchable)
        self.assertIn("https://x", str(ctx.exception))

    def run_cli(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(tagdb.urllib.request, "urlopen", self.opener(self.DATA)):
            code = tagorg.main(["fetch"])
        return code, out.getvalue(), err.getvalue()

    def test_cli_fetch(self):
        with self.manifest(self.entry()):
            code, out, _ = self.run_cli()
            self.assertEqual(code, 0)
            self.assertIn("받았습니다", out)
            self.assertEqual(self.dest.read_bytes(), self.DATA)
            code, out, _ = self.run_cli()                   # 이미 있으면 받지 않는다
            self.assertEqual((code, len(self.requests)), (0, 1))
            self.assertIn("이미 있습니다", out)
            self.dest.write_bytes(b"broken")                 # 손상되면 다시 받는다
            code, out, _ = self.run_cli()
            self.assertEqual((code, len(self.requests)), (0, 2))
            self.assertEqual(self.dest.read_bytes(), self.DATA)

    def test_cli_fetch_failure(self):
        with self.manifest(self.entry(b"other")):
            code, _, err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("sha256", err)
        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()

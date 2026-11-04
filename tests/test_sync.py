"""노드 팩 동기화 테스트 — 임시 폴더만 쓴다."""

from __future__ import annotations

import contextlib
import gzip
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fixtures import ROOT, build, builder, core, overlay_rows
from tools import sync

import tagorg  # noqa: E402  (fixtures 가 저장소 루트를 sys.path 에 넣는다)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.target = tmp / "ComfyUI_BMK_Nodes"
        self.target.mkdir()
        (self.target / "__init__.py").write_text('"""ComfyUI_BMK_Nodes — 통합 로더"""\n', encoding="utf-8")
        self.pack = tmp / "tagpack.json.gz"
        builder.write_pack(build()[0], self.pack)
        self.core = ROOT / sync.CORE_REL
        self.editor_rel = f"{sync.EDITOR_REL}/editor.js"
        self.model_rel = f"{sync.EDITOR_REL}/model.js"

    def tearDown(self):
        self._tmp.cleanup()

    def core_lf(self):
        return self.core.read_bytes().replace(b"\r\n", b"\n")

    def run_sync(self, **kw):
        return {a.rel: a.state for a in sync.sync(self.target, self.core, self.pack, **kw)}

    def test_refuses_other_folders(self):
        (self.target / "__init__.py").write_text("something else", encoding="utf-8")
        with self.assertRaises(sync.SyncError):
            self.run_sync()
        with self.assertRaises(sync.SyncError):
            sync.sync(self.target / "missing", self.core, self.pack)

    def test_first_sync_creates_files_and_info(self):
        states = self.run_sync()
        self.assertEqual(set(states.values()), {"create"})
        self.assertEqual((self.target / sync.CORE_REL).read_bytes(), self.core_lf())
        self.assertEqual((self.target / sync.PACK_REL).read_bytes(), self.pack.read_bytes())
        info = json.loads((self.target / sync.INFO_REL).read_text(encoding="utf-8"))
        self.assertEqual(info["format"], sync.INFO_FORMAT)
        self.assertIn("character_swap", info["presets"])
        self.assertIn("default", info["layouts"])
        self.assertIn("upper body", info["quick"]["framing"])     # 노드의 크롭/시점 콤보(팩을 읽지 않고)
        self.assertIn("from behind", info["quick"]["view"])
        self.assertIn("looking back", info["quick"]["gaze"])
        self.assertIn("kneeling", info["quick"]["pose"])
        self.assertEqual(info["files"][sync.PACK_REL], sync.sha256_bytes(self.pack.read_bytes()))
        self.assertEqual(list(self.target.rglob("*.sync-tmp")), [])
        for rel, source in ((self.editor_rel, "editor.js"), (self.model_rel, "model.js")):
            expected = (ROOT / sync.EDITOR_SRC / source).read_bytes().replace(b"\r\n", b"\n")
            self.assertEqual((self.target / rel).read_bytes(), expected)
            self.assertEqual(info["files"][rel], sync.sha256_bytes(expected))

    def test_second_sync_is_noop(self):
        self.run_sync()
        before = {p: p.stat().st_mtime_ns for p in self.target.rglob("*") if p.is_file()}
        states = self.run_sync()
        self.assertEqual(set(states.values()), {"unchanged"})
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in self.target.rglob("*") if p.is_file()})

    def test_new_pack_updates(self):
        self.run_sync()
        changed = overlay_rows({"smile": {**overlay_rows()["smile"], "rating": "s"}})
        builder.write_pack(build(changed)[0], self.pack)
        states = self.run_sync()
        self.assertEqual(states[sync.PACK_REL], "update")
        self.assertEqual(states[sync.CORE_REL], "unchanged")
        self.assertEqual(states[sync.INFO_REL], "update")

    def test_manual_edit_blocks_until_force(self):
        self.run_sync()
        edited = self.target / sync.CORE_REL
        edited.write_text("# 노드 팩에서 직접 고침\n", encoding="utf-8")
        pack_before = (self.target / sync.PACK_REL).read_bytes()
        builder.write_pack(build(overlay_rows({"smile": {**overlay_rows()["smile"], "rating": "q"}}))[0], self.pack)
        with self.assertRaises(sync.SyncConflict) as cm:
            self.run_sync()
        self.assertEqual(cm.exception.paths, [sync.CORE_REL])
        # 충돌이 있으면 다른 파일도 쓰지 않는다
        self.assertEqual((self.target / sync.PACK_REL).read_bytes(), pack_before)
        self.assertEqual(edited.read_text(encoding="utf-8"), "# 노드 팩에서 직접 고침\n")
        states = self.run_sync(force=True)
        self.assertEqual(states[sync.CORE_REL], "conflict")
        self.assertEqual(edited.read_bytes(), self.core_lf())

    def test_unknown_existing_file_is_conflict(self):
        (self.target / sync.CORE_REL).write_text("# 처음 보는 파일\n", encoding="utf-8")
        with self.assertRaises(sync.SyncConflict):
            self.run_sync()

    def test_dry_run_writes_nothing(self):
        states = self.run_sync(dry_run=True)
        self.assertEqual(set(states.values()), {"create"})
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ["__init__.py"])

    def test_bad_pack_is_rejected(self):
        self.pack.write_bytes(b"not a pack")
        with self.assertRaises(sync.SyncError):
            self.run_sync()
        self.pack.write_bytes(b"\x1f\x8b broken gzip")
        with self.assertRaises(sync.SyncError):
            self.run_sync()
        self.assertFalse((self.target / sync.DATA_REL).exists())

    def test_crlf_checkout_is_not_a_conflict(self):
        self.run_sync()
        dest = self.target / sync.CORE_REL
        dest.write_bytes(dest.read_bytes().replace(b"\n", b"\r\n"))   # git 이 CRLF 로 다시 체크아웃한 상태
        self.assertEqual(self.run_sync()[sync.CORE_REL], "unchanged")

    def test_core_is_written_with_lf(self):
        crlf_core = Path(self._tmp.name) / "core_crlf.py"
        crlf_core.write_bytes(self.core.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        sync.sync(self.target, crlf_core, self.pack)
        self.assertNotIn(b"\r\n", (self.target / sync.CORE_REL).read_bytes())
        self.assertEqual(sync.sync(self.target, self.core, self.pack)[0].state, "unchanged")

    def editor_copy(self):
        """고쳐 볼 수 있는 편집기 모듈 사본(정본 저장소 모양)."""
        repo = Path(self._tmp.name) / "repo"
        shutil.copytree(ROOT / sync.EDITOR_SRC, repo / sync.EDITOR_SRC)
        return repo

    def test_editor_change_updates_only_that_file(self):
        repo = self.editor_copy()
        self.run_sync(editor_repo=repo)
        with (repo / sync.EDITOR_SRC / "model.js").open("ab") as f:
            f.write(b"// x\n")
        states = self.run_sync(editor_repo=repo)
        self.assertEqual(states[self.model_rel], "update")
        self.assertEqual(states[self.editor_rel], "unchanged")
        self.assertEqual(states[sync.CORE_REL], "unchanged")
        self.assertEqual(states[sync.INFO_REL], "update")
        self.assertTrue((self.target / self.model_rel).read_bytes().endswith(b"// x\n"))

    def test_editor_is_written_with_lf(self):
        repo = self.editor_copy()
        path = repo / sync.EDITOR_SRC / "editor.js"
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        self.run_sync(editor_repo=repo)
        self.assertNotIn(b"\r\n", (self.target / self.editor_rel).read_bytes())
        self.assertEqual(self.run_sync()[self.editor_rel], "unchanged")   # 줄바꿈만 다른 정본은 같은 내용

    def test_editor_crlf_checkout_is_not_a_conflict(self):
        self.run_sync()
        dest = self.target / self.editor_rel
        dest.write_bytes(dest.read_bytes().replace(b"\n", b"\r\n"))     # git 이 CRLF 로 다시 체크아웃한 상태
        self.assertEqual(self.run_sync()[self.editor_rel], "unchanged")

    def test_manual_editor_edit_blocks_until_force(self):
        self.run_sync()
        edited = self.target / self.editor_rel
        edited.write_text("// 노드 팩에서 직접 고침\n", encoding="utf-8")
        before = (self.target / self.model_rel).read_bytes()
        (self.target / self.model_rel).write_bytes(before + b"\n// x\n")
        with self.assertRaises(sync.SyncConflict) as cm:
            self.run_sync()
        self.assertEqual(sorted(cm.exception.paths), sorted([self.editor_rel, self.model_rel]))
        self.assertEqual(edited.read_text(encoding="utf-8"), "// 노드 팩에서 직접 고침\n")
        states = self.run_sync(force=True)
        self.assertEqual(states[self.editor_rel], "conflict")
        self.assertEqual((self.target / self.model_rel).read_bytes(), before)

    def test_unknown_existing_editor_is_conflict(self):
        dest = self.target / self.model_rel
        dest.parent.mkdir(parents=True)
        dest.write_text("// 처음 보는 파일\n", encoding="utf-8")
        with self.assertRaises(sync.SyncConflict) as cm:
            self.run_sync()
        self.assertEqual(cm.exception.paths, [self.model_rel])
        self.assertFalse((self.target / sync.CORE_REL).exists())          # 충돌이 있으면 다른 파일도 쓰지 않는다

    def test_missing_editor_is_rejected_before_writing(self):
        repo = self.editor_copy()
        (repo / sync.EDITOR_SRC / "model.js").unlink()
        with self.assertRaises(sync.SyncError):
            self.run_sync(editor_repo=repo)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ["__init__.py"])

    def test_dirty_editor_is_recorded(self):
        repo = self.editor_copy()

        def git(*args):
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                           check=True, capture_output=True)
        git("init")
        git("add", "-A")
        git("commit", "-m", "x")
        self.assertFalse(sync.source_revision(repo)["dirty"])
        with (repo / sync.EDITOR_SRC / "editor.js").open("ab") as f:
            f.write(b"// x\n")
        self.assertTrue(sync.source_revision(repo)["dirty"])

    def test_plain_json_pack_is_compressed(self):
        plain = Path(self._tmp.name) / "tagpack.json"
        plain.write_bytes(gzip.decompress(self.pack.read_bytes()))
        sync.sync(self.target, self.core, plain)
        installed = self.target / sync.PACK_REL
        self.assertEqual(installed.read_bytes()[:2], b"\x1f\x8b")
        self.assertEqual(core.read_pack_data(str(installed))["format"], core.PACK_FORMAT)

    def test_broken_info_does_not_crash(self):
        self.run_sync()
        info = self.target / sync.INFO_REL
        for bad in ("null", "[]", '{"format": "tagorg-pack-info", "files": ["x"]}', "{broken"):
            info.write_text(bad, encoding="utf-8")
            states = self.run_sync()               # 내용이 같은 파일은 그대로, 기록만 다시 쓴다
            self.assertEqual(states[sync.CORE_REL], "unchanged")
            self.assertEqual(states[sync.INFO_REL], "update")
        info.write_text("[]", encoding="utf-8")
        (self.target / sync.CORE_REL).write_text("# 바뀜\n", encoding="utf-8")
        with self.assertRaises(sync.SyncConflict):
            self.run_sync()
        self.assertEqual(self.run_sync(force=True)[sync.CORE_REL], "conflict")

    def test_incomplete_info_is_regenerated(self):
        self.run_sync()
        path = self.target / sync.INFO_REL
        info = json.loads(path.read_text(encoding="utf-8"))
        del info["presets"]
        path.write_text(json.dumps(info), encoding="utf-8")
        self.assertEqual(self.run_sync()[sync.INFO_REL], "update")
        self.assertIn("presets", json.loads(path.read_text(encoding="utf-8")))

    def test_force_rewrites_info(self):
        sync.sync(self.target, self.core, self.pack, repo=None)
        states = {a.rel: a.state for a in sync.sync(self.target, self.core, self.pack, force=True,
                                                    repo=ROOT)}
        self.assertEqual(states[sync.INFO_REL], "update")

    @unittest.skipUnless(sys.platform == "win32", "Windows junction")
    def test_link_out_of_target_is_refused(self):
        import _winapi
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        _winapi.CreateJunction(str(outside), str(self.target / sync.DATA_REL))
        with self.assertRaises(sync.SyncError):
            self.run_sync()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(sys.platform == "win32", "Windows junction")
    def test_link_out_of_target_for_js_is_refused(self):
        import _winapi
        outside = Path(self._tmp.name) / "outside_js"
        outside.mkdir()
        _winapi.CreateJunction(str(outside), str(self.target / "js"))
        with self.assertRaises(sync.SyncError):
            self.run_sync()
        self.assertEqual(list(outside.iterdir()), [])


class EditorModuleTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node 가 없음")
    def test_import_does_nothing(self):
        """ComfyUI 가 js/ 아래 .js 를 전부 import 하므로 두 모듈은 import 만으로 아무 일도 하지 않아야 한다."""
        urls = [(ROOT / sync.EDITOR_SRC / name).as_uri() for name in sync.EDITOR_FILES]
        script = """
for (const name of ["setInterval", "setTimeout", "fetch", "addEventListener"]) {
  globalThis[name] = () => { throw new Error(`import 중에 ${name} 호출`); };
}
const before = new Set(Reflect.ownKeys(globalThis));
for (const url of %s) await import(url);
const added = Reflect.ownKeys(globalThis).filter((k) => !before.has(k));
if (added.length) throw new Error(`전역 추가: ${added.map(String)}`);
""" % json.dumps(urls)
        # document·window 를 건드리면 node 에는 없으므로 ReferenceError 로 실패한다
        result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)


class SyncCliTests(unittest.TestCase):
    def test_dry_run_does_not_touch_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nodes"
            target.mkdir()
            (target / "__init__.py").write_text("ComfyUI_BMK_Nodes", encoding="utf-8")
            pack = build()[0]
            built = []

            def fake_build(args):
                built.append(args.out)
                builder.write_pack(pack, Path(args.out) if args.out else Path(tmp) / "would_touch_build")
                return 0

            with mock.patch.object(tagorg, "_cmd_build", fake_build), \
                    mock.patch.object(tagorg, "SYNC_TARGET", Path(tmp) / "sync.json"), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tagorg.main(["sync", "--to", str(target), "--dry-run"]), 0)
            self.assertEqual(len(built), 1)
            self.assertIsNotNone(built[0])           # 임시 경로에 빌드
            self.assertFalse((Path(tmp) / "would_touch_build").exists())
            self.assertFalse((Path(tmp) / "sync.json").exists())
            self.assertEqual(sorted(p.name for p in target.iterdir()), ["__init__.py"])


if __name__ == "__main__":
    unittest.main()

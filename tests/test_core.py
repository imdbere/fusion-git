import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "FusionGit"))

from fusiongit import store  # noqa: E402
from fusiongit.gitcli import lfs_available, normalize_remote  # noqa: E402
from fusiongit.repo import MergeBlocked, Repo, clone_repo, content_hash, init_repo  # noqa: E402

DESIGN = "mechanical/Bracket.f3d"


def write(repo, path, data):
    full = repo.abspath(path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


def read(repo, path):
    with open(repo.abspath(path), "rb") as f:
        return f.read()


class GitTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fusiongit-test-")
        empty_config = os.path.join(self.tmp, "gitconfig")
        open(empty_config, "w").close()
        mock_env = {
            "GIT_CONFIG_GLOBAL": empty_config, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
            "FUSIONGIT_HOME": os.path.join(self.tmp, "home"),
        }
        self._saved_env = {k: os.environ.get(k) for k in mock_env}
        os.environ.update(mock_env)

        remote = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True)
        self.alice = init_repo(os.path.join(self.tmp, "alice"))
        self.alice.git.run("remote", "add", "origin", remote)
        write(self.alice, ".gitattributes", "*.f3d binary\n")
        write(self.alice, DESIGN, b"\x00design-v1")
        write(self.alice, "firmware/main.c", "int main() {}\n")
        self.alice.commit_files([".gitattributes", DESIGN, "firmware/main.c"], "initial")
        self.alice.push()
        self.bob = clone_repo(remote, os.path.join(self.tmp, "bob"))

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    def change_and_push(self, repo, path, data, message):
        write(repo, path, data)
        repo.commit_files([path], message)
        repo.push()


class CommitTests(GitTestCase):
    def test_commit_only_includes_given_files(self):
        write(self.alice, "firmware/main.c", "int main() { return 1; }\n")
        self.alice.git.run("add", "firmware/main.c")
        write(self.alice, DESIGN, b"\x00design-v2")
        self.alice.commit_files([DESIGN], "design only")
        committed = self.alice.git.run("show", "--name-only", "--format=", "HEAD").splitlines()
        self.assertEqual(committed, [DESIGN])
        self.assertIn("M  firmware/main.c", self.alice.git.run("status", "--porcelain"))

    def test_commit_without_changes_is_noop(self):
        self.assertEqual(self.alice.commit_files([DESIGN], "nothing"), "")

    def test_push_sets_upstream(self):
        self.assertEqual(self.alice.upstream(), "origin/main")


class PullTests(GitTestCase):
    def test_up_to_date(self):
        self.bob.fetch()
        self.assertEqual(self.bob.plan_pull(DESIGN).kind, "up_to_date")

    def test_fast_forward_design_change(self):
        self.change_and_push(self.alice, DESIGN, b"\x00design-v2", "v2")
        self.bob.fetch()
        plan = self.bob.plan_pull(DESIGN)
        self.assertEqual((plan.kind, plan.remote_changed), ("fast_forward", True))
        self.bob.apply_pull(plan)
        self.assertEqual(read(self.bob, DESIGN), b"\x00design-v2")

    def test_fast_forward_other_file_only(self):
        self.change_and_push(self.alice, "firmware/main.c", "int main() { return 2; }\n", "fw")
        self.bob.fetch()
        plan = self.bob.plan_pull(DESIGN)
        self.assertEqual((plan.kind, plan.remote_changed), ("fast_forward", False))

    def test_local_only_ahead_is_up_to_date(self):
        write(self.bob, DESIGN, b"\x00bob")
        self.bob.commit_files([DESIGN], "bob")
        self.bob.fetch()
        self.assertEqual(self.bob.plan_pull(DESIGN).kind, "up_to_date")

    def test_diverged_without_design_conflict_merges(self):
        self.change_and_push(self.alice, "firmware/main.c", "int main() { return 2; }\n", "fw")
        write(self.bob, DESIGN, b"\x00bob-design")
        self.bob.commit_files([DESIGN], "bob design")
        self.bob.fetch()
        plan = self.bob.plan_pull(DESIGN)
        self.assertEqual((plan.kind, plan.local_changed, plan.remote_changed), ("merge", True, False))
        self.bob.apply_pull(plan)
        self.assertEqual(read(self.bob, DESIGN), b"\x00bob-design")
        self.assertIn(b"return 2", read(self.bob, "firmware/main.c"))
        self.assertEqual(self.bob.status(DESIGN).behind, 0)

    def _diverge_design(self):
        self.change_and_push(self.alice, DESIGN, b"\x00alice", "alice")
        write(self.bob, DESIGN, b"\x00bob")
        self.bob.commit_files([DESIGN], "bob")
        self.bob.fetch()
        plan = self.bob.plan_pull(DESIGN)
        self.assertEqual(plan.kind, "conflict")
        return plan

    def test_conflict_keep_mine(self):
        plan = self._diverge_design()
        self.bob.resolve_conflict(plan, DESIGN, "ours")
        self.assertEqual(read(self.bob, DESIGN), b"\x00bob")
        status = self.bob.status(DESIGN)
        self.assertEqual((status.ahead, status.behind, status.file_modified), (2, 0, False))

    def test_conflict_take_theirs(self):
        plan = self._diverge_design()
        self.bob.resolve_conflict(plan, DESIGN, "theirs")
        self.assertEqual(read(self.bob, DESIGN), b"\x00alice")
        self.assertEqual(self.bob.status(DESIGN).behind, 0)

    def test_conflict_resolves_exports_with_the_design(self):
        step = "mechanical/Bracket.step"
        for repo, tag in ((self.alice, b"alice"), (self.bob, b"bob")):
            write(repo, DESIGN, b"\x00" + tag)
            write(repo, step, b"STEP " + tag)
            repo.commit_files([DESIGN, step], tag.decode())
        self.alice.push()
        self.bob.fetch()
        group = [DESIGN, step, "mechanical/Bracket.png"]
        plan = self.bob.plan_pull(group)
        self.assertEqual(plan.kind, "conflict")
        self.bob.resolve_conflict(plan, group, "theirs")
        self.assertEqual((read(self.bob, DESIGN), read(self.bob, step)), (b"\x00alice", b"STEP alice"))
        self.assertEqual(self.bob.status(DESIGN).behind, 0)

    def test_conflict_in_other_file_aborts(self):
        self.change_and_push(self.alice, "firmware/main.c", "alice\n", "fw alice")
        write(self.alice, DESIGN, b"\x00alice")
        self.alice.commit_files([DESIGN], "alice design")
        self.alice.push()
        write(self.bob, "firmware/main.c", "bob\n")
        write(self.bob, DESIGN, b"\x00bob")
        self.bob.commit_files(["firmware/main.c", DESIGN], "bob both")
        self.bob.fetch()
        plan = self.bob.plan_pull(DESIGN)
        with self.assertRaises(MergeBlocked) as ctx:
            self.bob.resolve_conflict(plan, DESIGN, "ours")
        self.assertEqual(ctx.exception.conflicting, ["firmware/main.c"])
        self.assertFalse(self.bob.git.ok("rev-parse", "-q", "--verify", "MERGE_HEAD"))
        self.assertEqual(read(self.bob, DESIGN), b"\x00bob")

    def test_file_at_upstream(self):
        self.change_and_push(self.alice, DESIGN, b"\x00remote-version", "v2")
        self.bob.fetch()
        self.assertEqual(self.bob.file_at("@{u}", DESIGN), b"\x00remote-version")

    def test_status_counts(self):
        self.change_and_push(self.alice, DESIGN, b"\x00v2", "v2")
        write(self.bob, "firmware/main.c", "local\n")
        self.bob.commit_files(["firmware/main.c"], "local")
        self.bob.fetch()
        status = self.bob.status(DESIGN)
        self.assertEqual((status.branch, status.ahead, status.behind), ("main", 1, 1))


class SetupTests(GitTestCase):
    def test_binary_attributes_are_idempotent(self):
        repo = init_repo(os.path.join(self.tmp, "fresh"))
        self.assertTrue(repo.ensure_attributes(["*.f3d", "*.step"], use_lfs=False))
        self.assertFalse(repo.ensure_attributes(["*.f3d", "*.step"], use_lfs=False))
        with open(os.path.join(repo.root, ".gitattributes")) as f:
            self.assertEqual(f.read(), "*.f3d binary\n*.step binary\n")

    def test_ignored_patterns_are_idempotent(self):
        self.assertTrue(self.alice.ensure_ignored(["_XRef_/", ".DS_Store"]))
        self.assertFalse(self.alice.ensure_ignored(["_XRef_/", ".DS_Store"]))
        write(self.alice, "mechanical/_XRef_/Bracket.f3d", b"\x00cache")
        self.assertEqual(self.alice.git.run("status", "--porcelain", "--", "mechanical"), "")

    def test_relpath_roundtrip(self):
        path = self.alice.relpath(self.alice.abspath(DESIGN))
        self.assertEqual(path, DESIGN)

    def test_content_hash_tracks_file(self):
        before = content_hash(self.alice.abspath(DESIGN))
        write(self.alice, DESIGN, b"\x00changed")
        self.assertNotEqual(before, content_hash(self.alice.abspath(DESIGN)))


@unittest.skipUnless(lfs_available(), "git-lfs not installed")
class LfsTests(GitTestCase):
    def test_pointer_is_detected_and_replaced(self):
        remote = os.path.join(self.tmp, "lfs-remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True)
        author = init_repo(os.path.join(self.tmp, "lfs-author"))
        author.git.run("remote", "add", "origin", remote)
        self.assertTrue(author.ensure_attributes(["*.f3d"], use_lfs=True))
        self.assertTrue(author.uses_lfs())
        write(author, DESIGN, b"\x00real-design")
        author.commit_files([".gitattributes", DESIGN], "lfs design")
        author.push()

        with mock.patch.dict(os.environ, {"GIT_LFS_SKIP_SMUDGE": "1"}):
            clone = clone_repo(remote, os.path.join(self.tmp, "lfs-clone"))
        self.assertTrue(clone.is_lfs_pointer(DESIGN))
        clone.fetch_lfs_file(DESIGN)
        self.assertFalse(clone.is_lfs_pointer(DESIGN))
        self.assertEqual(read(clone, DESIGN), b"\x00real-design")


class StoreTests(GitTestCase):
    def test_links(self):
        store.set_link("id1", repoPath=self.alice.root, pathInRepo=DESIGN, syncedHash="abc")
        store.set_link("id1", dirty=True, lineage="urn:lineage")
        self.assertEqual(store.get_link("id1")["syncedHash"], "abc")
        self.assertTrue(store.get_link("id1")["dirty"])
        key, link = store.find_link(self.alice.root + os.sep, DESIGN)
        self.assertEqual((key, link["lineage"]), ("id1", "urn:lineage"))
        store.remove_link("id1")
        self.assertIsNone(store.get_link("id1"))
        self.assertEqual(store.find_link(self.alice.root, DESIGN), (None, None))

    def test_repo_config_defaults_merge(self):
        with open(os.path.join(self.alice.root, store.REPO_CONFIG_NAME), "w") as f:
            f.write('{"exports": {"stl": true}}')
        config = store.load_repo_config(self.alice.root)
        self.assertEqual(config["exports"], {"step": True, "stl": True, "thumbnail": True})
        self.assertEqual(config["designsDir"], "mechanical")


class RemoteUrlTests(unittest.TestCase):
    def test_equivalent_urls(self):
        forms = ["https://github.com/Owner/Repo.git", "git@github.com:owner/repo.git",
                 "ssh://git@github.com:22/owner/repo", "https://user@github.com/owner/repo/"]
        self.assertEqual({normalize_remote(u) for u in forms}, {"github.com/owner/repo"})


if __name__ == "__main__":
    unittest.main()

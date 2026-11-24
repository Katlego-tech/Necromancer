import io
import os
import tarfile
import tempfile
import unittest

from spirits.enron import extract


def make_tar(path, members):
    """members: list of (name, kind, data) where kind in file|dir|symlink."""
    with tarfile.open(path, "w:gz") as tar:
        for name, kind, data in members:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = data
                tar.addfile(info)
            else:
                payload = data.encode()
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = os.path.join(self.tmp.name, "dest")
        self.archive = os.path.join(self.tmp.name, "a.tar.gz")

    def tearDown(self):
        self.tmp.cleanup()

    def test_extracts_normal_maildir(self):
        make_tar(self.archive, [("maildir", "dir", None), ("maildir/u/inbox/1.", "file", "hello")])
        s = extract.safe_extract(self.archive, self.dest)
        self.assertEqual(s.files, 1)
        with open(os.path.join(self.dest, "maildir/u/inbox/1.")) as f:
            self.assertEqual(f.read(), "hello")

    def test_rejects_parent_traversal(self):
        make_tar(self.archive, [("../evil", "file", "x")])
        with self.assertRaises(extract.UnsafeArchiveError):
            extract.safe_extract(self.archive, self.dest)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "evil")))

    def test_rejects_absolute_path(self):
        make_tar(self.archive, [("/tmp/necromancer_abs_test", "file", "x")])
        with self.assertRaises(extract.UnsafeArchiveError):
            extract.safe_extract(self.archive, self.dest)
        self.assertFalse(os.path.exists("/tmp/necromancer_abs_test"))

    def test_symlinks_are_skipped_not_created(self):
        make_tar(self.archive, [("link", "symlink", "/etc/passwd"), ("ok", "file", "y")])
        s = extract.safe_extract(self.archive, self.dest)
        self.assertEqual(s.skipped_special, ["link"])
        self.assertFalse(os.path.lexists(os.path.join(self.dest, "link")))
        self.assertTrue(os.path.exists(os.path.join(self.dest, "ok")))

    def test_size_cap(self):
        make_tar(self.archive, [("big", "file", "x" * 100)])
        with self.assertRaises(extract.UnsafeArchiveError):
            extract.safe_extract(self.archive, self.dest, max_bytes=10)

    def test_rerun_skips_existing_files(self):
        make_tar(self.archive, [("f", "file", "abc")])
        extract.safe_extract(self.archive, self.dest)
        s = extract.safe_extract(self.archive, self.dest)
        self.assertEqual((s.files, s.skipped_existing), (0, 1))


if __name__ == "__main__":
    unittest.main()

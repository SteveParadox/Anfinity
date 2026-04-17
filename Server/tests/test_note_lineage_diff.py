import unittest
import importlib.util
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ["DEBUG"] = "false"

spec = importlib.util.spec_from_file_location("test_target_notes", ROOT / "app" / "api" / "notes.py")
notes = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["test_target_notes"] = notes
spec.loader.exec_module(notes)


class NoteLineageDiffTests(unittest.TestCase):
    def test_word_level_diff_segments_capture_add_delete_and_unchanged(self) -> None:
        previous = "Alpha beta gamma"
        current = "Alpha beta delta gamma"

        segments = notes.build_word_diff_segments(previous, current)

        self.assertTrue(segments)
        current_projection = "".join(
            segment["text"] for segment in segments if segment["type"] != "deleted"
        )
        self.assertEqual(current_projection, current)

        segment_types = [segment["type"] for segment in segments]
        self.assertIn("unchanged", segment_types)
        self.assertIn("added", segment_types)

        added_segments = [segment for segment in segments if segment["type"] == "added"]
        self.assertTrue(any("delta" in segment["text"] for segment in added_segments))

    def test_deleted_segments_preserve_removed_text(self) -> None:
        previous = "Alpha beta gamma"
        current = "Alpha gamma"

        segments = notes.build_word_diff_segments(previous, current)

        deleted_segments = [segment for segment in segments if segment["type"] == "deleted"]
        self.assertTrue(deleted_segments)
        self.assertTrue(any("beta" in segment["text"] for segment in deleted_segments))


if __name__ == "__main__":
    unittest.main()

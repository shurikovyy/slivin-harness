from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from slivin_harness.control_plane import (
    ArtifactVisibility,
    ControlPlaneError,
    ControllerPlane,
    SelfVerifyBinding,
    is_within,
    safe_artifact_name,
)


class ControlPlaneTests(unittest.TestCase):
    def test_private_plane_is_separate_and_public_mirror_is_explicit(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-control-plane-"))
        plane = ControllerPlane(root)
        private_path = plane.write_private_json("state/run.json", {"authority": True})
        public_path = plane.write_public_json("run.json", {"diagnostic": True})
        self.assertTrue(is_within(root / "controller_private", private_path))
        self.assertEqual(public_path, (root / "run.json").resolve())
        self.assertNotEqual(private_path, public_path)

    def test_artifact_paths_reject_escape_absolute_drive_and_unc(self) -> None:
        for raw in (
            "../x", "/tmp/x", "C:/temp/x", r"C:\temp\x",
            r"\\server\share\x", "file:stream", "NUL.txt", "name. ",
        ):
            with self.subTest(raw=raw), self.assertRaises(ControlPlaneError):
                safe_artifact_name(raw)
        self.assertEqual(safe_artifact_name("nested/state.json"), "nested/state.json")

    def test_canonical_containment_accepts_alias_and_rejects_sibling(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-containment-"))
        private = root / "controller_private"
        private.mkdir()
        alias = private / "temporary-segment" / ".." / "state.json"
        sibling = root / "controller_private_backup" / "state.json"
        self.assertTrue(is_within(private, alias))
        self.assertFalse(is_within(private, sibling))

    def test_private_receipt_is_bound_to_every_revision_dimension(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-receipt-"))
        plane = ControllerPlane(root)
        binding = SelfVerifyBinding(
            candidate_id="c1",
            task_contract_rev=1,
            plan_rev=1,
            implementation_contract_rev=2,
            verification_plan_rev=3,
            runtime_env_id=4,
            attempt_id=1,
        )
        plane.issue_self_verify_receipt(binding=binding, claim={"passed": True})
        self.assertTrue(plane.verify_self_verify_receipt(binding=binding))
        stale = SelfVerifyBinding(**{**binding.to_dict(), "verification_plan_rev": 4})
        self.assertFalse(plane.verify_self_verify_receipt(binding=stale))

    def test_tampered_receipt_is_rejected(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-receipt-tamper-"))
        plane = ControllerPlane(root)
        binding = SelfVerifyBinding(
            candidate_id="c1",
            task_contract_rev=None,
            plan_rev=1,
            implementation_contract_rev=1,
            verification_plan_rev=None,
            runtime_env_id=1,
            attempt_id=1,
        )
        path = plane.issue_self_verify_receipt(binding=binding, claim={"passed": True})
        data = json.loads(path.read_text(encoding="utf-8"))
        data["binding"]["candidate_id"] = "tampered"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(plane.verify_self_verify_receipt(binding=binding))

    def test_sensitive_fingerprint_is_keyed_and_context_bound(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-private-hmac-"))
        plane = ControllerPlane(root)
        first = plane.keyed_fingerprint(b"TOKEN=secret\n", context="runtime-file:.env")
        repeat = plane.keyed_fingerprint(b"TOKEN=secret\n", context="runtime-file:.env")
        other_path = plane.keyed_fingerprint(
            b"TOKEN=secret\n", context="runtime-file:.env.local"
        )
        self.assertEqual(first, repeat)
        self.assertNotEqual(first, other_path)
        self.assertNotEqual(first, __import__("hashlib").sha256(b"TOKEN=secret\n").hexdigest())

    def test_scratch_class_is_non_authoritative(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-scratch-"))
        plane = ControllerPlane(root)
        scratch = plane.write_text(
            "probe/output.txt", "ok", visibility=ArtifactVisibility.SCRATCH
        )
        self.assertTrue(is_within(root / "scratch", scratch))
        self.assertFalse(is_within(plane.private_root, scratch))

    def test_write_json_once_rejects_overwrite(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-immutable-artifact-"))
        plane = ControllerPlane(root)
        path = plane.write_json_once(
            "final/final_acceptance.json",
            {"candidate_id": "candidate-1"},
            visibility=ArtifactVisibility.PRIVATE,
        )
        original = path.read_bytes()
        with self.assertRaisesRegex(ControlPlaneError, "already exists"):
            plane.write_json_once(
                "final/final_acceptance.json",
                {"candidate_id": "candidate-2"},
                visibility=ArtifactVisibility.PRIVATE,
            )
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

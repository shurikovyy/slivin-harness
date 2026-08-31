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
    safe_artifact_name,
)


class ControlPlaneTests(unittest.TestCase):
    def test_private_plane_is_separate_and_public_mirror_is_explicit(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-control-plane-"))
        plane = ControllerPlane(root)
        private_path = plane.write_private_json("state/run.json", {"authority": True})
        public_path = plane.write_public_json("run.json", {"diagnostic": True})
        self.assertTrue(private_path.is_relative_to(root / "controller_private"))
        self.assertEqual(public_path, root / "run.json")
        self.assertNotEqual(private_path, public_path)

    def test_artifact_paths_reject_escape_absolute_drive_and_unc(self) -> None:
        for raw in (
            "../x", "/tmp/x", "C:/temp/x", r"C:\temp\x",
            r"\\server\share\x", "file:stream", "NUL.txt", "name. ",
        ):
            with self.subTest(raw=raw), self.assertRaises(ControlPlaneError):
                safe_artifact_name(raw)
        self.assertEqual(safe_artifact_name("nested/state.json"), "nested/state.json")

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

    def test_scratch_class_is_non_authoritative(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-scratch-"))
        plane = ControllerPlane(root)
        scratch = plane.write_text(
            "probe/output.txt", "ok", visibility=ArtifactVisibility.SCRATCH
        )
        self.assertTrue(scratch.is_relative_to(root / "scratch"))
        self.assertFalse(scratch.is_relative_to(plane.private_root))


if __name__ == "__main__":
    unittest.main()

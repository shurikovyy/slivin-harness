from __future__ import annotations

import unittest

from tools.release_check import (
    MODEL_BACKED_STAGES, RELEASE_STAGES, execute_mandatory_stage_sequence,
    validate_release_stage_order,
)
from tools.replay_model_artifact_transcripts import REPLAY_TESTS
from tools.replay_codex_transport import REPLAY_CASES
from tools.replay_native_command_drift import REPLAY_TESTS as NATIVE_COMMAND_REPLAY_TESTS


class ReleaseArtifactReplayGateTests(unittest.TestCase):
    def test_release_gate_refuses_real_models_when_transcript_replay_fails(self) -> None:
        visited = execute_mandatory_stage_sequence(
            RELEASE_STAGES, lambda name: name != "artifact_replay",
        )
        self.assertEqual(visited[-1], "artifact_replay")
        self.assertNotIn("native_roles", visited)
        self.assertNotIn("real_models", visited)

    def test_release_gate_reaches_real_models_only_after_transcript_replay_passes(self) -> None:
        visited = execute_mandatory_stage_sequence(RELEASE_STAGES, lambda _name: True)
        self.assertEqual(visited[-6:], ("artifact_replay", "transport_replay",
            "native_command_replay", "entrypoint_boot", "native_roles", "real_models"))
        self.assertEqual(len(REPLAY_TESTS), 3)
        self.assertEqual(len(REPLAY_CASES), 4)
        self.assertEqual(len(NATIVE_COMMAND_REPLAY_TESTS), 4)

    def test_transport_replay_failure_prevents_all_model_backed_stages(self) -> None:
        visited = execute_mandatory_stage_sequence(
            RELEASE_STAGES, lambda name: name != "transport_replay",
        )
        self.assertEqual(visited[-1], "transport_replay")
        self.assertFalse(MODEL_BACKED_STAGES.intersection(visited))

    def test_native_command_replay_failure_prevents_all_model_backed_stages(self) -> None:
        visited = execute_mandatory_stage_sequence(
            RELEASE_STAGES, lambda name: name != "native_command_replay",
        )
        self.assertEqual(visited[-1], "native_command_replay")
        self.assertFalse(MODEL_BACKED_STAGES.intersection(visited))

    def test_entrypoint_boot_failure_prevents_all_model_backed_stages(self) -> None:
        visited = execute_mandatory_stage_sequence(
            RELEASE_STAGES, lambda name: name != "entrypoint_boot",
        )
        self.assertEqual(visited[-1], "entrypoint_boot")
        self.assertFalse(MODEL_BACKED_STAGES.intersection(visited))

    def test_model_backed_stages_cannot_move_before_replay(self) -> None:
        self.assertEqual(MODEL_BACKED_STAGES, {"native_roles", "real_models"})
        self.assertEqual(RELEASE_STAGES, (
            "self_check", "boundary", "stateful", "mutations", "mixed_runners",
            "artifact_replay", "transport_replay", "native_command_replay",
            "entrypoint_boot", "native_roles", "real_models",
        ))
        validate_release_stage_order(RELEASE_STAGES)
        for invalid in (
            ("native_roles", "artifact_replay", "transport_replay",
             "native_command_replay", "entrypoint_boot", "real_models"),
            ("real_models", "artifact_replay", "transport_replay",
             "native_command_replay", "entrypoint_boot", "native_roles"),
            ("native_roles", "real_models"),
            ("artifact_replay", "artifact_replay", "transport_replay",
             "native_command_replay", "entrypoint_boot", "native_roles", "real_models"),
            ("artifact_replay", "native_roles", "transport_replay",
             "native_command_replay", "entrypoint_boot", "real_models"),
            ("transport_replay", "artifact_replay", "native_command_replay",
             "entrypoint_boot", "native_roles", "real_models"),
            ("artifact_replay", "native_command_replay", "transport_replay",
             "entrypoint_boot", "native_roles", "real_models"),
            ("artifact_replay", "transport_replay", "entrypoint_boot",
             "native_command_replay", "native_roles", "real_models"),
            ("artifact_replay", "native_roles", "real_models"),
            ("self_check", "future_model_stage", "artifact_replay",
             "transport_replay", "native_command_replay", "entrypoint_boot",
             "native_roles", "real_models"),
            ("artifact_replay", "transport_replay", "native_command_replay",
             "entrypoint_boot", "future_model_stage", "native_roles", "real_models"),
            ("artifact_replay", "transport_replay", "native_command_replay",
             "native_roles", "entrypoint_boot", "real_models"),
            ("artifact_replay", "transport_replay", "native_command_replay",
             "native_roles", "real_models"),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                validate_release_stage_order(invalid)


if __name__ == "__main__":
    unittest.main()

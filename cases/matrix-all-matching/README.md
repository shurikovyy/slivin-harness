# Matrix all-matching benchmark

`workspace/` is intentionally not tracked by the Harness repository.

After cloning Harness on a machine:

1. Copy/extract the broken `_90` project **contents** into `workspace/`.
2. Run from the Harness repository root:

   ```bash
   python tools/prepare_workspace.py cases/matrix-all-matching/workspace
   ```

3. Run the case:

   ```bash
   ./run cases/matrix-all-matching/task.toml
   ```

A full `_92` copy is not required. The held-out grader was calibrated once against
`_90`/`_92`; the repository stores a hash-bound calibration certificate. If the
grader or its check definition changes, Harness refuses to run until it is
recalibrated.

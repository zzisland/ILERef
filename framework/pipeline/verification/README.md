# Verification

This package validates generated candidates, integrates them into source, and collects the evidence used for acceptance.

| Module | Responsibility |
|---|---|
| `candidate/` | Candidate extraction and executable contract checks |
| `reintegration/` | Source identity, ranges, hashes, file composition, and structural checks |
| `engineering/` | Isolated workspaces, Maven compilation, and target-test execution evidence |
| `outcome/` | Detector identity, issue re-detection, and attribution to the target instance |
| `acceptance/` | Acceptance decisions from collected validation evidence |
| `repair/` | Repair eligibility and re-entry through candidate validation; disabled in the bundled AR example |

`file_gate.py` coordinates engineering validation and outcome checks within the validation workspace. `evidence.py` defines the evidence bundle, and `integrity.py` checks the source/candidate hash chain. The single-instance entry point performs a final contract, structure, and evidence consistency review before accepting a candidate.

Run the [minimal end-to-end example](../../../README.md#minimal-end-to-end-example) to exercise these stages with a newly generated model candidate.

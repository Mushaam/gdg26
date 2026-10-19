# AUDIT.md — WAFER-GUARD wafer-defect detector

**Verdict: NO-GO.** The original F1 ≥ 0.90 came from leakage and a synthetic test set. After the repairs, the model's honest
test result is F1 = 0.00 (it catches none of the 17 real defects in the test set).

## Q1. The three silent bugs and the fixes

**1. Data leakage** (original preprocessing cell: `fit_transform` on all rows → `SMOTE` on all rows → random `train_test_split`)
* Why it is wrong: the imputer and scaler learned from validation/test rows and the future; SMOTE created synthetic defects *before* the split, so
  blends of the same real defects landed in both train and test; the test set was 50 % defective instead of ~6.6 %; the defect rate drifts over time
  (14 % in the first fifth of the data, 3.5 % in the middle), so a random split lets the model see the future.
* Evidence: re-running the original, **274 of 586 test rows (46.8 %) and 93.5 % of test defects were synthetic**; F1 0.9915, ROC-AUC 1.0000.
  With only the leakage removed (quick ablation, random split, train-only fit, natural prevalence) ROC-AUC fell to 0.574 and F1@0.5 to 0.0.
* Fix: chronological split (first 60 % train, next 20 % validation, last 20 % test); imputation/scaling/column selection fitted on train rows only;
  no SMOTE, a class-weighted loss instead (`pos_weight` = 12.18, from train only); validation is used only for choosing the epoch, `λ`, seed and the threshold.
  Notebook assertions check that scaler statistics equal the train-only statistics and that a test row's features do not depend on other rows.

**2. Corrupted loss gradients (silent broadcasting)** (original training loop: `torch.mean((reconstructed - batch_x) ** 2)`)
* Why it is wrong: the loader added a channel axis, so `batch_x` was `[64,1,590]` while the reconstruction was `[64,590]`. The subtraction silently
  became `[64,64,590]`: every sample was compared with every other sample (2,416,640 pairs instead of 37,760). The minimum of that loss is the batch average, so the decoder ignored its input.
* Evidence (same data and seed, only the loss changed): true test reconstruction error 0.691 with the bug vs 0.310 fixed; predicting the training mean gives 0.693.
* Fix: all tensors are `[batch, features]`; the loss compares sample *i* with sample *i* only; shape asserts in the model, loss and training loop; unit tests (zero loss for perfect
  reconstruction, equals an explicit loop, gradient equals `2(r−t)·mask/Σmask`, old `[B,1,F]` call is rejected); a check that gradients reach the encoder, decoder and head.

**3. Invariant blindness: the legacy Probe Station PS-L invariant was ignored** (original scaling of `sensor_0`, `sensor_1`)
* I read "invariant blindness" as the pipeline being blind to the fixed rule in the data, since the brief does not define the term. Verified rule: the 146 wafers (9.32 %) with
  `sensor_0 == 0.0` are exactly the wafers whose `sensor_1` is 100, 200 or 300; there are no exceptions. These are a sentinel and error codes, not measurements.
  Scaling them as continuous values inflated the std of `sensor_0` by 11.8× and of `sensor_1` by 8.5×, and shrank the real variation to 8 % of the scaled range.
  (Constant columns are also handled: the 122 columns with zero variance in training are dropped.)
* Fix: `is_legacy` flag + one-hot codes 100/200/300; the two legacy cells are marked "not measured", excluded from scaling statistics and from the reconstruction loss.
  Because a dense network needs a number there, they are set to the training mean (0 after scaling); the flag tells the model that this 0 is not a reading. This is a trade-off, not a real value.

## Q2. True evaluation metrics (natural prevalence, last 20 % of time, evaluated once)

Test set: **314 wafers, 17 defective, prevalence 5.41%** (train 76/1002, validation 11/251).
The whole dataset is 6.64 % defective; the final chronological slice happens to be 5.41%, and I did not change the split to force 6.6 %.
Threshold 0.5832 was chosen on validation (maximum F1) and then frozen.

| Precision | Recall | F1 | ROC-AUC | Average precision |
|---|---|---|---|---|
| 0.000 | 0.000 | 0.000 | 0.572 (95 % CI 0.41–0.74) | 0.118 (95 % CI 0.05–0.27) |

Confusion matrix: TN = 292, FP = 5, FN = 17, TP = 0. No-skill average precision = 0.054.
The release gate (F1 ≥ 0.9) is **NOT MET**. The ROC-AUC interval includes 0.5, so the notebook shows no reliable detection ability.

## Limitations (not verified or not claimed)
* Only 17 defective test wafers and 11 validation defects: intervals are wide, and the validation-chosen epoch and threshold are noisy.
* The reason for the weak signal was not investigated (possible causes: few positives for 468 features, drift, little signal in the data).
* Before the final pipeline existed, `audit_evidence.py` (quick ablations used for the evidence above) scored a simple model on the same last-20 % rows. No choice in the notebook was made from it, and the
  model, split and selection rules were not changed after the notebook's first evaluation, but strictly the test region was looked at once before that.
* The legacy 0 fill is a documented trade-off (above). Clipping at ±10 and dropping train-constant columns are fixed choices, not tuned.
* `secom_legacy_mask_EVALUATOR_ONLY.csv` was not used anywhere (training, preprocessing, selection, thresholds); the legacy rule is derived from the telemetry itself.

## Reproduce
Put `secom_telemetry_modified.csv` next to the notebook, install `torch scikit-learn pandas numpy joblib`, and run `wafer_guard_fixed.ipynb` top to bottom (CPU, fixed seeds;
≈15 minutes on a slow 2-core machine). It was executed from a clean kernel with no errors and gave the same numbers on repeated runs (torch 2.14.1, scikit-learn 1.9.1, pandas 3.0.5, numpy 2.5.3).
The "before" numbers come from `python audit_evidence.py secom_telemetry_modified.csv` (also needs `imbalanced-learn`; log in `audit_evidence_output.txt`).

# VELIA Medical Intelligence worker

Self-hosted GPU data plane for abdominal contrast-enhanced CT research support.

## Scope

- RADAR only: abdominal contrast-enhanced CT.
- Input: one NIfTI volume (`.nii`/`.nii.gz`) or a ZIP containing exactly one DICOM CT series.
- Output: structured per-finding model scores.
- Scores are **not calibrated clinical probabilities** and are never returned as a definitive diagnosis.
- Raw input is deleted after inference; only a temporary result state is retained for Railway reconciliation.
- No arbitrary URLs, shell commands, user code, or plugins are exposed by the worker API.

## Licensing boundary

The upstream repository code is Apache-2.0, while the public RADAR Hugging Face weights are
CC-BY-NC-SA-4.0. The image intentionally does **not** contain or automatically download model
weights. Public weights may be prepared only after explicitly setting
`VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK=true`.

For a commercial VELIA launch, use a separately licensed RADAR checkpoint or another provider
behind the same worker contract.

## Model preparation

Mount a persistent volume at `/models/radar`, then explicitly run:

```bash
VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK=true \
RADAR_MODEL_ROOT=/models/radar \
python prepare_weights.py
```

Required runtime files are downloaded from `radar-generalist/RADAR`.

## Runtime

Required environment:

- `VELIA_MEDICAL_WORKER_AUTH_TOKEN`
- `VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK=true` for public research weights
- `RADAR_MODEL_ROOT=/models/radar`

Start:

```bash
python app.py
```

The control plane should expose this worker only through VELIA's authenticated Railway API.

# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

**OMRChecker** — a Python + OpenCV **CLI batch tool** that reads OMR (optical
mark recognition) answer sheets: image/PDF in → detected bubbles → CSV out. It
is MIT-licensed (upstream: Udayraj123/OMRChecker).

This fork exists to be **integrated into the "Classwise" Django web app** as an
exam-grading feature. That integration is fully designed in
[`0003-omr-integration.md`](0003-omr-integration.md) — **read it before doing any
integration work; it is the source of truth** (new `omr` Django app, subprocess
invocation, `student_id` bubble block, mandatory teacher review, `django-rq`
queue, 4-phase roadmap). It is written in Thai.

Current status: **Phase 0–1** of that roadmap — a standalone [`web_test/`](web_test/)
Flask UI to prove the engine works and tune templates before touching Django.

## Run the engine (CLI)

```bash
pip install -r requirements.txt        # then also: pip install opencv-python
python main.py -i samples/sample1 -o outputs
```

Key flags (`main.py`): `-i/--inputDir` (dirs, recursive), `-o/--outputDir`,
`-l/--setLayout` (show template overlay to tune, no processing),
`-a/--autoAlign` (experimental), `-d/--debug` (**inverted**: passing `-d` turns
tracebacks *off*).

## Gotchas (bitten us before)

- **`opencv-python` is NOT in `requirements.txt`** even though `import cv2` is
  everywhere. Install it separately or the engine fails at import.
  `requirements.web.txt` pins it.
- **Headless / server use:** keep `outputs.show_image_level = 0`. Any value ≥ 1
  opens a blocking `cv2.imshow`/`waitKey` window; ≥ 6 also blocks on a
  matplotlib `plt.show()`. Note `samples/sample1/config.json` sets it to `5` —
  override it when running non-interactively.
- **Results CSV filename is `Results_<hour><AM/PM>.csv`** and is **appended** to.
  Two runs in the same clock hour into the same output dir mix results — use a
  fresh output dir per run.
- Bad JSON in `template.json`/`config.json` calls `exit(1)` (not an exception)
  — one reason integration shells out as a subprocess instead of importing.

## Code map

| Path | Role |
|------|------|
| `main.py` | CLI entry: argparse → `entry_point_for_args` |
| `src/entry.py` | Orchestration: walk dirs, load configs, `process_files` → `_process_single_image` |
| `src/core.py` | Detection core — `ImageInstanceOps.read_omr_response` (thresholding, marking, overlay) |
| `src/template.py` | `Template`, `FieldBlock`, `Bubble` — parses `template.json` into a bubble grid |
| `src/processors/` | Preprocessors: `CropPage`, `CropOnMarkers`, `FeatureBasedAlignment`, `builtins` |
| `src/evaluation.py` | Scoring against an answer key (`evaluation.json`) |
| `src/utils/image.py` | `ImageUtils.load_omr_image` — image + PDF (PyMuPDF) loading |
| `src/utils/file.py` | `Paths`, `setup_outputs_for_template` — output dirs + CSV columns |
| `src/schemas/` | jsonschema for `template.json` / `config.json` / `evaluation.json` |
| `src/defaults/` | Default config (`CONFIG_DEFAULTS` DotMap) and template values |
| `web_test/` | Standalone Flask test UI (see its README) |

## Inputs (per processing directory)

- `template.json` **(required)** — bubble layout. Required top-level keys:
  `pageDimensions`, `bubbleDimensions`, `preProcessors`, `fieldBlocks`. Each
  field block gives either a `fieldType` preset (`QTYPE_INT`, `QTYPE_MCQ4/5`,
  `QTYPE_MCQ4/5_RTL`, `QTYPE_INT_FROM_1`) or explicit `bubbleValues` +
  `direction`, plus `origin`, `bubblesGap`, `labelsGap`, `fieldLabels`
  (ranges like `q1..4` expand). See `samples/sample1/template.json`.
- `config.json` (optional) — tuning overrides.
- `evaluation.json` (optional) — answer key → `score` column.
- A marker image (e.g. `omr_marker.jpg`) — only if the template uses
  `CropOnMarkers` (referenced via `options.relativePath`).

## Output

Under `<outputDir>/<input-subdir>/`: `Results/Results_*.csv` (columns:
`file_id, input_path, output_path, score, <template fields...>`),
`CheckedOMRs/<name>` (annotated overlay images), `Manual/ErrorFiles.csv` &
`Manual/MultiMarkedFiles.csv`.

## The web test UI

```bash
pip install -r requirements.txt -r requirements.web.txt
python web_test/app.py      # http://127.0.0.1:5000
```

It calls `main.py` as a **subprocess** (same as the planned Django app),
per-run temp dir, forces headless config. See [`web_test/README.md`](web_test/README.md).

## Conventions for integration work

When you eventually build the Django `omr` app, follow
[`0003-omr-integration.md`](0003-omr-integration.md): subprocess (don't import
`main`), write scores only via the existing `grades.services.bulk_record_scores`,
always keep the teacher-review-before-save step, and never run the OMR engine
inside a web request (queue it).

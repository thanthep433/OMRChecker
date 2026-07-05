# OMRChecker — Web Test UI

A tiny **standalone** Flask app to prove the OMR engine reads *your* answer
sheets correctly and to tune `template.json` quickly — **before** integrating
OMRChecker into the Classwise Django app.

> This is a throwaway dev tool, not production. The real integration plan lives
> in [`../0003-omr-integration.md`](../0003-omr-integration.md). This UI mirrors
> that plan's approach (calls `main.py` as a **subprocess**), so it doubles as a
> smoke test of the eventual Django `omr` app's data path.

## Run

```bash
pip install -r requirements.txt -r requirements.web.txt   # includes opencv-python
python web_test/app.py
# open http://127.0.0.1:5000
```

## What it does

1. Pick a **template folder** (any dir under `samples/` or
   `web_test/templates_data/` that has a `template.json`).
2. Upload one or more **images / PDFs**.
3. Optionally **edit `template.json`** in the browser and re-run to see the
   overlay move — the fast tuning loop.
4. See per-sheet: the **overlay image** (marked bubbles), the **detected
   answers** table, and the **score** (if the template has `evaluation.json`).
   Download the raw **Results CSV** the engine produced.

## How it works (see `runner.py`)

Each run gets its own temp dir under the OS temp folder (`omr_web_runs/<id>/`):

- copies the template's config files (`template.json`, `config.json`,
  `evaluation.json`) + any marker / answer-key image the template references
  into `in/` — but **not** the sample sheet images (you upload your own);
- forces `outputs.show_image_level = 0` so the engine never blocks on a
  `cv2.imshow`/`waitKey` GUI popup (e.g. `samples/sample1` ships
  `show_image_level: 5`);
- runs `python main.py -i in -o out`;
- reads back `out/**/Results/Results_*.csv` and `out/**/CheckedOMRs/`.

## Tuning your own templates

`samples/` is read-only. To tune, copy a sample into
`web_test/templates_data/<name>/` and edit there — the **Save** button writes
back only to `templates_data/`.

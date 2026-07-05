"""Thin wrapper around the OMRChecker CLI for the standalone web test UI.

The web UI never imports the OMR engine in-process. Instead it shells out to
``python main.py -i <in> -o <out>`` exactly the way the planned Django ``omr``
app will (see ``0003-omr-integration.md``). That keeps this test tool faithful
to the real integration path and sidesteps the engine's ``exit(1)``-on-bad-JSON
and DotMap-config quirks.

Everything for a single run lives in its own directory under RUNS_DIR so a run
never appends onto a previous run's ``Results_<hour><AM/PM>.csv`` and so Flask
can serve the overlay images / CSV back to the browser.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "samples"
# Where users may keep their own tuned templates (samples/ stays read-only).
TEMPLATES_DATA_DIR = REPO_ROOT / "web_test" / "templates_data"
RUNS_DIR = Path(tempfile.gettempdir()) / "omr_web_runs"

# Config files the engine looks for in each processing directory. These must sit
# next to the images, so we copy them into the temp input dir (NOT the sample
# sheet images, which the user replaces with their own uploads).
CONFIG_FILENAMES = ("template.json", "config.json", "evaluation.json")
RUN_TTL_SECONDS = 60 * 60  # keep finished runs around for an hour, then reap


# --------------------------------------------------------------------------- #
# Template discovery
# --------------------------------------------------------------------------- #
def list_templates():
    """Return [{'id', 'label', 'path'}] for every dir containing a template.json.

    ``id`` is the repo-relative posix path (stable, used as a form value).
    Samples come first, then the user's own templates_data/ folders.
    """
    found = []
    for base, group in ((SAMPLES_DIR, "sample"), (TEMPLATES_DATA_DIR, "mine")):
        if not base.exists():
            continue
        for tj in sorted(base.rglob("template.json")):
            rel = tj.parent.relative_to(REPO_ROOT).as_posix()
            found.append({"id": rel, "label": f"[{group}] {rel}", "path": tj.parent})
    return found


def _resolve_template_dir(template_id):
    """Map a template id back to an absolute dir, guarding against traversal."""
    candidate = (REPO_ROOT / template_id).resolve()
    if not str(candidate).startswith(str(REPO_ROOT)):
        raise ValueError("Invalid template id")
    if not (candidate / "template.json").is_file():
        raise ValueError(f"No template.json in '{template_id}'")
    return candidate


def read_template_json(template_id):
    """Return the raw text of a template's template.json (for the editor box)."""
    return (_resolve_template_dir(template_id) / "template.json").read_text(
        encoding="utf-8"
    )


def save_template_json(template_id, text):
    """Persist edited template.json back to its folder.

    samples/ is treated as read-only on purpose; tuned templates belong in
    web_test/templates_data/. Raises ValueError with a helpful message otherwise.
    """
    json.loads(text)  # fail fast on invalid JSON before touching disk
    template_dir = _resolve_template_dir(template_id)
    if SAMPLES_DIR in template_dir.parents or template_dir == SAMPLES_DIR:
        raise ValueError(
            "samples/ is read-only. Copy it into web_test/templates_data/<name>/ "
            "and edit there."
        )
    (template_dir / "template.json").write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Running the engine
# --------------------------------------------------------------------------- #
def _copy_template_assets(template_dir, in_dir):
    """Copy config files + any marker/answer-key images the template references.

    Deliberately skips the sample sheet images/PDFs so they aren't processed as
    if they were uploads.
    """
    for name in CONFIG_FILENAMES:
        src = template_dir / name
        if src.is_file():
            shutil.copy2(src, in_dir / name)

    template = json.loads((template_dir / "template.json").read_text(encoding="utf-8"))
    for pre in template.get("preProcessors", []):
        rel = (pre.get("options") or {}).get("relativePath")
        if rel:
            _copy_relative(template_dir, in_dir, rel)

    # evaluation.json may point at an answer-key CSV living beside it.
    eval_path = template_dir / "evaluation.json"
    if eval_path.is_file():
        try:
            ev = json.loads(eval_path.read_text(encoding="utf-8"))
            rel = (ev.get("options") or {}).get("answer_key_csv_path")
            if rel:
                _copy_relative(template_dir, in_dir, rel)
        except json.JSONDecodeError:
            pass


def _copy_relative(template_dir, in_dir, rel):
    src = (template_dir / rel).resolve()
    if src.is_file():
        dst = in_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _force_headless(in_dir):
    """Ensure config.json never triggers blocking cv2/matplotlib windows.

    Sample configs (e.g. samples/sample1) set show_image_level: 5, which would
    hang the subprocess on waitKey. We merge over just the outputs section so
    the template's threshold/dimension tuning is preserved.
    """
    config_path = in_dir / "config.json"
    config = {}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            config = {}
    outputs = dict(config.get("outputs", {}))
    outputs["show_image_level"] = 0
    outputs.setdefault("save_detections", True)
    config["outputs"] = outputs
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def run_omr(template_id, uploaded_files, template_json_override=None):
    """Run the OMR engine over uploaded images. Returns a result dict.

    uploaded_files: list of (filename, bytes).
    template_json_override: edited template.json text from the UI, or None.
    """
    _reap_old_runs()
    template_dir = _resolve_template_dir(template_id)

    run_id = uuid.uuid4().hex
    run_dir = RUNS_DIR / run_id
    in_dir = run_dir / "in"
    out_dir = run_dir / "out"
    in_dir.mkdir(parents=True, exist_ok=True)

    _copy_template_assets(template_dir, in_dir)

    if template_json_override is not None and template_json_override.strip():
        json.loads(template_json_override)  # validate before writing
        (in_dir / "template.json").write_text(template_json_override, encoding="utf-8")

    _force_headless(in_dir)

    saved = 0
    for filename, data in uploaded_files:
        safe = Path(filename).name  # strip any path components
        if not safe:
            continue
        (in_dir / safe).write_bytes(data)
        saved += 1
    if saved == 0:
        raise ValueError("No image/PDF files were uploaded.")

    proc = subprocess.run(
        [sys.executable, "main.py", "-i", str(in_dir), "-o", str(out_dir)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )

    return _collect_results(run_id, run_dir, out_dir, proc)


# --------------------------------------------------------------------------- #
# Result collection
# --------------------------------------------------------------------------- #
def _collect_results(run_id, run_dir, out_dir, proc):
    import csv

    result = {
        "run_id": run_id,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "columns": [],
        "rows": [],
        "errors": [],
        "multi_marked": [],
        "csv_url": None,
    }

    results_csvs = sorted(out_dir.rglob("Results/Results_*.csv"))
    if results_csvs:
        results_csv = results_csvs[0]
        result["csv_url"] = f"/runs/{run_id}/{_rel(results_csv, run_dir)}"
        with results_csv.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            meta = {"file_id", "input_path", "output_path", "score"}
            answer_cols = [c for c in (reader.fieldnames or []) if c not in meta]
            result["columns"] = answer_cols
            for row in reader:
                result["rows"].append(
                    {
                        "file_id": row.get("file_id", ""),
                        "score": row.get("score", ""),
                        "answers": {c: row.get(c, "") for c in answer_cols},
                        "overlay_url": _overlay_url(run_id, run_dir, out_dir, row),
                    }
                )

    result["errors"] = _read_manual_csv(out_dir, run_dir, "ErrorFiles.csv")
    result["multi_marked"] = _read_manual_csv(out_dir, run_dir, "MultiMarkedFiles.csv")
    return result


def _read_manual_csv(out_dir, run_dir, filename):
    import csv

    rows = []
    for path in out_dir.rglob(f"Manual/{filename}"):
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows.append(row.get("file_id", "") or next(iter(row.values()), ""))
    return rows


def _overlay_url(run_id, run_dir, out_dir, row):
    # output_path in the CSV points straight at the saved marked image.
    cand = Path(row.get("output_path", ""))
    if cand.is_file():
        return f"/runs/{run_id}/{_rel(cand, run_dir)}"
    # Fallback: multi-row sheets land under CheckedOMRs/_MULTI_/.
    name = row.get("file_id", "")
    if name:
        for p in out_dir.rglob(name):
            if "CheckedOMRs" in p.parts and p.is_file():
                return f"/runs/{run_id}/{_rel(p, run_dir)}"
    return None


def _rel(path, run_dir):
    return path.resolve().relative_to(run_dir.resolve()).as_posix()


def _reap_old_runs():
    if not RUNS_DIR.exists():
        return
    cutoff = time.time() - RUN_TTL_SECONDS
    for child in RUNS_DIR.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            pass

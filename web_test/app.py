"""Standalone Flask test UI for OMRChecker.

Purpose: prove the OMR engine reads *your* answer sheets correctly and let you
tune template.json quickly, BEFORE integrating into the Classwise Django app.
This is a throwaway dev tool — not for production. Run locally:

    pip install -r requirements.txt -r requirements.web.txt
    python web_test/app.py
    # open http://127.0.0.1:5000

See ../0003-omr-integration.md for the real Django integration plan.
"""

from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

import runner

app = Flask(__name__)
app.secret_key = "omr-web-test-ui-dev-only"  # dev tool; not a secret


def _default_template_id(templates):
    for t in templates:
        if t["id"].endswith("samples/sample1"):
            return t["id"]
    return templates[0]["id"] if templates else None


@app.route("/")
def index():
    templates = runner.list_templates()
    selected = request.args.get("template") or _default_template_id(templates)
    template_json = ""
    error = None
    if selected:
        try:
            template_json = runner.read_template_json(selected)
        except (ValueError, OSError) as exc:
            error = str(exc)
    return render_template(
        "index.html",
        templates=templates,
        selected=selected,
        template_json=template_json,
        error=error,
    )


@app.route("/run", methods=["POST"])
def run():
    template_id = request.form.get("template", "")
    template_json = request.form.get("template_json", "")
    uploaded = [
        (f.filename, f.read())
        for f in request.files.getlist("images")
        if f and f.filename
    ]
    try:
        result = runner.run_omr(template_id, uploaded, template_json_override=template_json)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index", template=template_id))
    except Exception as exc:  # noqa: BLE001 - surface any engine failure to the page
        flash(f"Run failed: {exc}", "error")
        return redirect(url_for("index", template=template_id))
    return render_template("result.html", result=result, template_id=template_id)


@app.route("/save-template", methods=["POST"])
def save_template():
    template_id = request.form.get("template", "")
    template_json = request.form.get("template_json", "")
    try:
        runner.save_template_json(template_id, template_json)
        flash("Saved template.json", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("index", template=template_id))


@app.route("/runs/<run_id>/<path:relpath>")
def run_file(run_id, relpath):
    """Serve overlay images and result CSVs produced by a run."""
    run_dir = (runner.RUNS_DIR / run_id).resolve()
    if not str(run_dir).startswith(str(runner.RUNS_DIR.resolve())) or not run_dir.is_dir():
        abort(404)
    as_attachment = request.args.get("download") == "1"
    return send_from_directory(run_dir, relpath, as_attachment=as_attachment)


if __name__ == "__main__":
    app.run(debug=True, port=5000)

"""
Generate project-level and crystal-level HTML index pages for SPREAD results.

Directory structure expected:
    {results_path}/
        index.html                              ← project index (this module)
        {CrystalName}/
            index.html                          ← crystal index (this module)
            processing/
                {pipeline}/
                    run_1/
                        index.html
                        plots/
                        data/
            spread/
                {pipeline}/
                    run_1/
                        index.html
                        plots/
                        data/
"""
from __future__ import annotations

import html as _html
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CSS = """
body  { font-family: Arial, sans-serif; margin: 24px; color: #222; max-width: 1200px; }
h1    { color: #1a4a8a; }
h2    { color: #2a6ab0; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-top: 28px; }
.meta { color: #666; font-size: 13px; margin-bottom: 16px; }
.breadcrumb { font-size: 13px; color: #888; margin-bottom: 12px; }
.breadcrumb a { color: #2a6ab0; text-decoration: none; }
.breadcrumb a:hover { text-decoration: underline; }
table { border-collapse: collapse; font-size: 13px; margin-top: 8px; width: 100%; }
th, td { border: 1px solid #bbb; padding: 5px 10px; text-align: left; }
th { background: #e8eef8; }
tr:nth-child(even) { background: #f7f9fd; }
a.run-link { color: #1a4a8a; text-decoration: none; font-weight: bold; }
a.run-link:hover { text-decoration: underline; }
.none { color: #999; font-style: italic; }
"""

_TIMESTAMP_FMT = "%Y-%m-%d %H:%M"


def _now() -> str:
    return datetime.now().strftime(_TIMESTAMP_FMT)


def _run_dirs(base: Path) -> list[Path]:
    """Return sorted run_N subdirectories under *base*."""
    if not base.is_dir():
        return []
    return sorted(
        (d for d in base.iterdir() if d.is_dir() and d.name.startswith("run_")),
        key=lambda d: int(d.name.split("_", 1)[1]) if d.name.split("_", 1)[1].isdigit() else 0,
    )


def _run_label(run_dir: Path) -> str:
    """Return a short human label for a run directory."""
    idx = run_dir.name  # e.g. "run_1"
    index_html = run_dir / "index.html"
    mtime = ""
    if index_html.exists():
        ts = datetime.fromtimestamp(index_html.stat().st_mtime)
        mtime = ts.strftime(_TIMESTAMP_FMT)
    return idx, mtime


# ---------------------------------------------------------------------------
# Crystal index
# ---------------------------------------------------------------------------

def generate_crystal_index(
    crystal_results_dir: Path,
    project_name: str,
    crystal_name: str,
) -> str:
    """
    Scan *crystal_results_dir* for processing and spread runs, write index.html.
    Returns the absolute path to the generated file.
    """
    crystal_results_dir.mkdir(parents=True, exist_ok=True)

    project_index = crystal_results_dir.parent / "index.html"
    back_link = (
        f'<a href="../index.html">{_html.escape(project_name)}</a> / '
        if project_index.exists()
        else f'{_html.escape(project_name)} / '
    )

    # --- Collect processing runs ---
    proc_rows: list[str] = []
    for pipeline in ("xia2-dials", "xia2-3dii", "autoPROC"):
        pipeline_dir = crystal_results_dir / "processing" / pipeline
        for run_dir in _run_dirs(pipeline_dir):
            label, mtime = _run_label(run_dir)
            rel = run_dir.relative_to(crystal_results_dir)
            link = f'<a class="run-link" href="{rel}/index.html">{label}</a>'
            proc_rows.append(
                f"<tr><td>{_html.escape(pipeline)}</td><td>{link}</td>"
                f"<td>{mtime}</td></tr>"
            )

    proc_section = ""
    if proc_rows:
        proc_section = (
            "<h2>Processing Results</h2>"
            "<table><tr><th>Pipeline</th><th>Run</th><th>Generated</th></tr>"
            + "".join(proc_rows)
            + "</table>"
        )
    else:
        proc_section = "<h2>Processing Results</h2><p class='none'>No processing runs found.</p>"

    # --- Collect SPREAD (phenix) runs ---
    spread_rows: list[str] = []
    for pipeline in ("xia2-dials", "xia2-3dii", "autoPROC"):
        pipeline_dir = crystal_results_dir / "spread" / pipeline
        for run_dir in _run_dirs(pipeline_dir):
            label, mtime = _run_label(run_dir)
            rel = run_dir.relative_to(crystal_results_dir)
            link = f'<a class="run-link" href="{rel}/index.html">{label}</a>'
            spread_rows.append(
                f"<tr><td>{_html.escape(pipeline)}</td><td>{link}</td>"
                f"<td>{mtime}</td></tr>"
            )

    spread_section = ""
    if spread_rows:
        spread_section = (
            "<h2>SPREAD Anomalous Refinement</h2>"
            "<table><tr><th>Pipeline</th><th>Run</th><th>Generated</th></tr>"
            + "".join(spread_rows)
            + "</table>"
        )
    else:
        spread_section = (
            "<h2>SPREAD Anomalous Refinement</h2>"
            "<p class='none'>No SPREAD runs found.</p>"
        )

    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{_html.escape(crystal_name)} &mdash; {_html.escape(project_name)}</title>
  <style>{_CSS}</style>
</head>
<body>
<p class="breadcrumb">{back_link}{_html.escape(crystal_name)}</p>
<h1>{_html.escape(crystal_name)}</h1>
<p class="meta">Project: {_html.escape(project_name)} &nbsp;&mdash;&nbsp; Generated: {_now()}</p>

{proc_section}

{spread_section}

</body>
</html>
"""
    out = crystal_results_dir / "index.html"
    out.write_text(content, encoding="utf-8")
    return str(out)


# ---------------------------------------------------------------------------
# Project index
# ---------------------------------------------------------------------------

def generate_project_index(results_path: Path, project_name: str) -> str:
    """
    Scan *results_path* for crystal subdirectories, write index.html.
    Returns the absolute path to the generated file.
    """
    results_path.mkdir(parents=True, exist_ok=True)

    crystal_rows: list[str] = []
    for crystal_dir in sorted(results_path.iterdir()):
        if not crystal_dir.is_dir():
            continue
        crystal_index = crystal_dir / "index.html"
        if not crystal_index.exists():
            continue
        crystal_name = crystal_dir.name

        # Count processing and spread runs
        n_proc = sum(
            len(_run_dirs(crystal_dir / "processing" / pl))
            for pl in ("xia2-dials", "xia2-3dii", "autoPROC")
        )
        n_spread = sum(
            len(_run_dirs(crystal_dir / "spread" / pl))
            for pl in ("xia2-dials", "xia2-3dii", "autoPROC")
        )

        mtime = datetime.fromtimestamp(crystal_index.stat().st_mtime).strftime(_TIMESTAMP_FMT)
        link = f'<a class="run-link" href="{_html.escape(crystal_name)}/index.html">{_html.escape(crystal_name)}</a>'
        crystal_rows.append(
            f"<tr><td>{link}</td><td>{n_proc}</td><td>{n_spread}</td><td>{mtime}</td></tr>"
        )

    if crystal_rows:
        table = (
            "<table>"
            "<tr><th>Crystal</th><th>Processing runs</th>"
            "<th>SPREAD runs</th><th>Last updated</th></tr>"
            + "".join(crystal_rows)
            + "</table>"
        )
    else:
        table = "<p class='none'>No crystal results found yet.</p>"

    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{_html.escape(project_name)} &mdash; SPREAD Results</title>
  <style>{_CSS}</style>
</head>
<body>
<h1>{_html.escape(project_name)}</h1>
<p class="meta">SPREAD Results &nbsp;&mdash;&nbsp; Generated: {_now()}</p>

<h2>Crystals</h2>
{table}

</body>
</html>
"""
    out = results_path / "index.html"
    out.write_text(content, encoding="utf-8")
    return str(out)

"""
Scan a processing directory for autoPROC results, generate matplotlib plots,
and write a self-contained HTML report (plots embedded as base64).

Directory layout expected:
    proc_path/
        {energy}eV/
            {images}img/
                aP.log          <- main autoPROC log (written by the job script)
"""
from __future__ import annotations

import base64
import html as _html
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from spread_gui.core.autoproc_parser import AutoprocStats, Stats3, parse_autoproc_log

# {energy_eV: {wedge_images: AutoprocStats | None}}
ResultMap = dict[int, dict[int, Optional[AutoprocStats]]]


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------

def collect_results(proc_path: Path, pipeline: str = "autoPROC") -> ResultMap:
    """
    Scan *proc_path* for aP.log files produced by autoPROC.

    Returns a nested dict keyed by energy (int, eV) then wedge size (int, images).
    A value of None means the directory exists but parsing found no STARANISO block
    (job failed or still running).
    """
    results: ResultMap = {}
    energy_re = re.compile(r'^(\d+)eV$')
    images_re = re.compile(r'^(\d+)img$')

    if not proc_path.is_dir():
        return results

    for eentry in sorted(proc_path.iterdir()):
        em = energy_re.match(eentry.name)
        if not em or not eentry.is_dir():
            continue
        energy = int(em.group(1))
        results[energy] = {}
        for wentry in sorted(eentry.iterdir()):
            wm = images_re.match(wentry.name)
            if not wm or not wentry.is_dir():
                continue
            wedge = int(wm.group(1))
            ap_log = wentry / "aP.log"
            if ap_log.exists():
                results[energy][wedge] = parse_autoproc_log(ap_log)
            else:
                autoproc_dir = wentry / "autoPROC"
                if autoproc_dir.is_dir():
                    results[energy][wedge] = None

    return results


def next_run_number(out_root: Path) -> int:
    """Return the next available run index under *out_root* (1-based)."""
    existing = [
        d for d in out_root.iterdir()
        if d.is_dir() and d.name.startswith("run_") and d.name[4:].isdigit()
    ] if out_root.is_dir() else []
    if not existing:
        return 1
    return max(int(d.name[4:]) for d in existing) + 1


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_report(
    results: ResultMap,
    out_dir: Path,
    pipeline: str,
    proc_path_str: str,
    log: Callable[[str], None],
    project_name: str = "",
    crystal_name: str = "",
    results_path: str = "",
) -> str:
    """
    Generate a self-contained HTML report inside *out_dir*.
    Plots are embedded as base64; no external PNG or CSV files are written.
    Returns the absolute path to index.html.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    energies = sorted(results.keys())
    wedge_set: set[int] = set()
    for wd in results.values():
        wedge_set.update(wd.keys())
    wedges = sorted(wedge_set)

    if not energies or not wedges:
        raise ValueError("No autoPROC processing directories found under the given path.")

    n_e = len(energies)
    log(f"Energies  : {', '.join(str(e) + ' eV' for e in energies)}")
    log(f"Wedge sizes: {', '.join(str(w) for w in wedges)}")

    cmap_name = 'tab10' if n_e <= 10 else 'tab20'
    cmap = plt.cm.get_cmap(cmap_name)
    colors = [cmap(i / max(n_e - 1, 1)) for i in range(n_e)]

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _get(stats: AutoprocStats, attr: str, idx: Optional[int]) -> Optional[float]:
        v = getattr(stats, attr, None)
        if v is None:
            return None
        if idx is None:
            return float(v)
        if isinstance(v, tuple):
            return v[idx]
        return None

    def _plot_panel(ax, attr: str, idx: Optional[int], title: str, ylabel: str) -> None:
        for i, energy in enumerate(energies):
            xs, ys = [], []
            for w in wedges:
                stats = results[energy].get(w)
                if stats is None:
                    continue
                val = _get(stats, attr, idx)
                if val is None:
                    continue
                xs.append(w)
                ys.append(val)
            if xs:
                ax.plot(xs, ys, 'o-', color=colors[i], label=f"{energy} eV",
                        linewidth=1.5, markersize=4)
        ax.set_title(title, fontsize=9)
        ax.set_xlabel("Wedge size (images)", fontsize=8)
        ax.set_ylabel(ylabel, fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3)
        if n_e > 1:
            ax.legend(fontsize=6, loc='best')

    def _fig_to_b64(fig) -> str:
        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()

    # Accumulate (section_title, base64_png)
    plot_files: list[tuple[str, str]] = []

    def _render_1panel(attr: str, idx: Optional[int], title: str, ylabel: str) -> None:
        fig, ax = plt.subplots(figsize=(7, 4))
        _plot_panel(ax, attr, idx, title, ylabel)
        fig.tight_layout()
        plot_files.append((title, _fig_to_b64(fig)))
        log(f"  {title}")

    def _render_3panel(attr: str, title: str, ylabel: str) -> None:
        fig, axes = plt.subplots(1, 3, figsize=(15, 4))
        for j, shell in enumerate(("Overall", "Low shell", "High shell")):
            _plot_panel(axes[j], attr, j, f"{title} — {shell}", ylabel)
        fig.tight_layout()
        plot_files.append((title, _fig_to_b64(fig)))
        log(f"  {title}")

    # -----------------------------------------------------------------------
    # Generate all plots
    # -----------------------------------------------------------------------
    log("Generating plots…")

    _render_1panel("high_res",               0,    "High resolution limit (STARANISO)", "d (Å)")
    _render_1panel("completeness_ellip",     0,    "Completeness (ellipsoidal)",        "Completeness (%)")
    _render_1panel("multiplicity",           0,    "Multiplicity",                      "Multiplicity")
    _render_3panel("i_over_sigma",                 "I/σ(I)",                            "I/σ(I)")
    _render_3panel("rmerge",                       "Rmerge (all I+ & I-)",              "Rmerge")
    _render_3panel("cc_half",                      "CC½",                               "CC½")
    _render_3panel("anom_completeness_ellip",      "Anomalous completeness (ellipsoidal)", "Completeness (%)")
    _render_3panel("anom_multiplicity",            "Anomalous multiplicity",            "Multiplicity")

    log("  Unit cell parameters")
    plot_files.append(("Unit cell parameters", _render_unit_cell(results, energies, wedges, colors)))

    log("  Diffraction limits (STARANISO)")
    plot_files.append(("Diffraction limits (STARANISO)", _render_diff_limits(results, energies, wedges, colors)))

    log("Writing HTML report…")
    html_path = out_dir / "index.html"
    _write_html(
        html_path, results, energies, wedges, plot_files,
        pipeline, proc_path_str, project_name, crystal_name, results_path,
        out_dir,
    )
    log(f"Report: {html_path}")
    return str(html_path)


# ---------------------------------------------------------------------------
# Standalone plot renderers
# ---------------------------------------------------------------------------

def _render_unit_cell(results: ResultMap, energies: list[int], wedges: list[int], colors: list) -> str:
    params = [
        ("cell_a",     "a (Å)"),
        ("cell_b",     "b (Å)"),
        ("cell_c",     "c (Å)"),
        ("cell_alpha", "α (°)"),
        ("cell_beta",  "β (°)"),
        ("cell_gamma", "γ (°)"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    for idx, (attr, ylabel) in enumerate(params):
        ax = axes[idx // 3][idx % 3]
        for i, energy in enumerate(energies):
            xs, ys = [], []
            for w in wedges:
                stats = results[energy].get(w)
                if stats is None:
                    continue
                val = getattr(stats, attr, None)
                if val is None:
                    continue
                xs.append(w)
                ys.append(val)
            if xs:
                ax.plot(xs, ys, 'o-', color=colors[i], label=f"{energy} eV",
                        linewidth=1.5, markersize=4)
        ax.set_title(ylabel, fontsize=9)
        ax.set_xlabel("Wedge size (images)", fontsize=8)
        ax.set_ylabel(ylabel, fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3)
        if len(energies) > 1:
            ax.legend(fontsize=6, loc='best')
    fig.suptitle("Unit cell parameters", fontsize=11)
    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def _render_diff_limits(results: ResultMap, energies: list[int], wedges: list[int], colors: list) -> str:
    axes_info = [
        ("diff_limit_astar", "a*"),
        ("diff_limit_bstar", "b*"),
        ("diff_limit_cstar", "c*"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    for j, (attr, axis_label) in enumerate(axes_info):
        ax = axes[j]
        for i, energy in enumerate(energies):
            xs, ys = [], []
            for w in wedges:
                stats = results[energy].get(w)
                if stats is None:
                    continue
                val = getattr(stats, attr, None)
                if val is None:
                    continue
                xs.append(w)
                ys.append(val)
            if xs:
                ax.plot(xs, ys, 'o-', color=colors[i], label=f"{energy} eV",
                        linewidth=1.5, markersize=4)
        ax.set_title(f"Diffraction limit along {axis_label}", fontsize=9)
        ax.set_xlabel("Wedge size (images)", fontsize=8)
        ax.set_ylabel("d (Å)", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3)
        if len(energies) > 1:
            ax.legend(fontsize=6, loc='best')
    fig.suptitle("Diffraction limits (STARANISO principal axes)", fontsize=11)
    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def _anchor(title: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', title.lower()).strip('_')


def _summary_table_html(results, energies, wedges) -> str:
    header = (
        "<tr><th>Energy (eV)</th><th>Wedge (img)</th>"
        "<th>Resolution (Å)</th><th>Completeness (%)</th>"
        "<th>Multiplicity</th><th>I/σ(I)</th><th>CC½</th><th>Rmerge</th></tr>"
    )
    rows = []
    for e in energies:
        for w in wedges:
            stats = results[e].get(w)
            if stats is None:
                continue
            def _f(v, idx=None):
                if v is None:
                    return "—"
                val = v[idx] if (idx is not None and isinstance(v, tuple)) else v
                return f"{val:.2f}" if val is not None else "—"
            rows.append(
                f"<tr>"
                f"<td>{e}</td><td>{w}</td>"
                f"<td>{_f(stats.high_res, 0)}</td>"
                f"<td>{_f(stats.completeness_ellip, 0)}</td>"
                f"<td>{_f(stats.multiplicity, 0)}</td>"
                f"<td>{_f(stats.i_over_sigma, 0)}</td>"
                f"<td>{_f(stats.cc_half, 0)}</td>"
                f"<td>{_f(stats.rmerge, 0)}</td>"
                f"</tr>"
            )
    return "<table>" + header + "".join(rows) + "</table>"


def _write_html(
    html_path: Path,
    results: ResultMap,
    energies: list[int],
    wedges: list[int],
    plot_files: list[tuple[str, str]],
    pipeline: str,
    proc_path_str: str,
    project_name: str,
    crystal_name: str,
    results_path: str,
    out_dir: Path,
) -> None:
    n_total = sum(len(wd) for wd in results.values())
    n_ok    = sum(1 for wd in results.values() for s in wd.values() if s is not None)
    n_fail  = n_total - n_ok
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    proc_path = Path(proc_path_str)

    # Breadcrumb
    crystal_index = out_dir.parent.parent.parent / "index.html"
    proj_index    = crystal_index.parent / "index.html"
    breadcrumb_parts = []
    if proj_index.exists():
        breadcrumb_parts.append(f'<a href="../../../index.html">{_html.escape(project_name)}</a>')
    elif project_name:
        breadcrumb_parts.append(_html.escape(project_name))
    if crystal_index.exists():
        breadcrumb_parts.append(f'<a href="../../index.html">{_html.escape(crystal_name)}</a>')
    elif crystal_name:
        breadcrumb_parts.append(_html.escape(crystal_name))
    breadcrumb_parts.append(f"{_html.escape(pipeline)} — {out_dir.name}")
    breadcrumb = " / ".join(breadcrumb_parts)

    header = "<th>Energy</th>" + "".join(f"<th>{w} img</th>" for w in wedges)
    rows: list[str] = []
    for energy in energies:
        cells = [f"<td>{energy} eV</td>"]
        for w in wedges:
            if w not in results[energy]:
                cells.append('<td class="na">—</td>')
            elif results[energy][w] is None:
                cells.append('<td class="fail">&#x2717; Failed</td>')
            else:
                cells.append('<td class="ok">&#x2713; OK</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")

    status_table = (
        '<table class="status">'
        f"<tr>{header}</tr>"
        + "".join(rows)
        + "</table>"
    )

    toc = "".join(
        f'<li><a href="#{_anchor(t)}">{_html.escape(t)}</a></li>'
        for t, _ in plot_files
    )
    plot_sections = "".join(
        f'<h3 id="{_anchor(t)}">{_html.escape(t)}</h3>'
        f'<img src="data:image/png;base64,{b64}" alt="{_html.escape(t)}">'
        for t, b64 in plot_files
    )

    summary_table = _summary_table_html(results, energies, wedges)

    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>autoPROC Analysis</title>
  <style>
    body  {{ font-family: Arial, sans-serif; margin: 24px; color: #222; max-width: 1400px; }}
    h1    {{ color: #1a4a8a; }}
    h2    {{ color: #2a6ab0; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-top: 32px; }}
    h3    {{ color: #333; margin-top: 28px; }}
    .meta {{ color: #666; font-size: 13px; }}
    .breadcrumb {{ font-size: 13px; color: #888; margin-bottom: 12px; }}
    .breadcrumb a {{ color: #2a6ab0; text-decoration: none; }}
    .breadcrumb a:hover {{ text-decoration: underline; }}
    table.status {{ border-collapse: collapse; font-size: 13px; margin-top: 8px; }}
    table.status th, table.status td {{ border: 1px solid #bbb; padding: 4px 10px; }}
    table.status th {{ background: #e8eef8; }}
    table {{ border-collapse: collapse; font-size: 13px; margin-top: 8px; }}
    th, td {{ border: 1px solid #bbb; padding: 4px 10px; }}
    th {{ background: #e8eef8; }}
    td.ok   {{ background: #d4edda; text-align: center; }}
    td.fail {{ background: #f8d7da; text-align: center; }}
    td.na   {{ background: #f0f0f0; text-align: center; color: #999; }}
    img {{ border: 1px solid #ddd; margin: 6px 0; max-width: 100%; }}
    ul.toc {{ columns: 2; }}
  </style>
</head>
<body>
<p class="breadcrumb">{breadcrumb}</p>
<h1>autoPROC Analysis</h1>
<p class="meta">Generated: {timestamp}</p>
<p class="meta">Processing path: {_html.escape(proc_path_str)}</p>
<p class="meta">Statistics from: STARANISO ellipsoidal analysis (final block)</p>

<h2>Run Status</h2>
<p>
  {n_total} run{'' if n_total == 1 else 's'} found &mdash;
  <span style="color:#1a7a3c">{n_ok} successful</span>,
  <span style="color:#c0392b">{n_fail} failed / incomplete</span>
</p>
{status_table}

<h2>Summary</h2>
{summary_table}

<h2>Plots</h2>
<ul class="toc">
{toc}
</ul>

{plot_sections}

</body>
</html>
"""
    html_path.write_text(content, encoding='utf-8')

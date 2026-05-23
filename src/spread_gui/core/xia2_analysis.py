"""
Scan a processing directory for xia2 results, generate matplotlib plots,
and write an HTML report.

Directory layout expected:
    proc_path/
        {energy}eV/
            {images}img/
                xia2-dials/   or   xia2-3dii/
                    xia2.txt
"""
from __future__ import annotations

import csv
import html as _html
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

# Use the non-interactive Agg backend before importing pyplot so that no
# display connection is required (runs fine in a QThread).
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from spread_gui.core.xia2_parser import Xia2Stats, Stats3, parse_xia2_txt

# {energy_eV: {wedge_images: Xia2Stats | None}}
ResultMap = dict[int, dict[int, Optional[Xia2Stats]]]


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------

def collect_results(proc_path: Path, pipeline: str) -> ResultMap:
    """
    Scan *proc_path* for xia2.txt results produced by *pipeline*
    ('xia2-dials' or 'xia2-3dii').

    Returns a nested dict keyed first by energy (int, eV) then by wedge size
    (int, images).  A value of None means the run was attempted but produced
    no statistics table (failed run).
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
            xia2_txt = wentry / pipeline / "xia2.txt"
            results[energy][wedge] = (
                parse_xia2_txt(xia2_txt) if xia2_txt.exists() else None
            )

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
    Generate PNG plots, CSV data files, and an HTML report inside *out_dir*.
    Calls *log* with progress messages.
    Returns the absolute path to index.html.
    """
    plots_dir = out_dir / "plots"
    data_dir  = out_dir / "data"
    plots_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    energies = sorted(results.keys())
    wedge_set: set[int] = set()
    for wd in results.values():
        wedge_set.update(wd.keys())
    wedges = sorted(wedge_set)

    if not energies or not wedges:
        raise ValueError("No processing directories found under the given path.")

    n_e = len(energies)
    log(f"Energies  : {', '.join(str(e) + ' eV' for e in energies)}")
    log(f"Wedge sizes: {', '.join(str(w) for w in wedges)}")

    # Choose a qualitative colormap with enough distinct colours.
    cmap_name = 'tab10' if n_e <= 10 else 'tab20'
    cmap = plt.cm.get_cmap(cmap_name)
    colors = [cmap(i / max(n_e - 1, 1)) for i in range(n_e)]

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _get(stats: Xia2Stats, attr: str, idx: Optional[int]) -> Optional[float]:
        """Extract a single float from a Stats3 tuple (idx=0/1/2) or scalar (idx=None)."""
        v = getattr(stats, attr, None)
        if v is None:
            return None
        if idx is None:
            return float(v)
        if isinstance(v, tuple):
            return v[idx]
        return None

    def _plot_panel(
        ax,
        attr: str,
        idx: Optional[int],
        title: str,
        ylabel: str,
    ) -> None:
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

    # Accumulate (section_title, filename) for the HTML table of contents.
    plot_files: list[tuple[str, str]] = []

    def _save_csv_1(attr: str, idx: Optional[int], csv_name: str) -> None:
        """Save one CSV: rows=wedge sizes, columns=energies."""
        rows = []
        header = ["wedge_img"] + [f"{e}_eV" for e in energies]
        for w in wedges:
            row = [w]
            for e in energies:
                stats = results[e].get(w)
                val = _get(stats, attr, idx) if stats else None
                row.append(f"{val:.6g}" if val is not None else "")
            rows.append(row)
        with open(data_dir / csv_name, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)

    def _save_csv_3(attr: str, csv_name: str) -> None:
        """Save one CSV with overall/low/high shell columns for each energy."""
        shells = ["overall", "low", "high"]
        header = ["wedge_img"] + [f"{e}_eV_{s}" for e in energies for s in shells]
        rows = []
        for w in wedges:
            row = [w]
            for e in energies:
                stats = results[e].get(w)
                for idx in range(3):
                    val = _get(stats, attr, idx) if stats else None
                    row.append(f"{val:.6g}" if val is not None else "")
            rows.append(row)
        with open(data_dir / csv_name, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)

    def _save_1panel(attr: str, idx: Optional[int], title: str, ylabel: str,
                     fname: str, csv_name: str) -> None:
        fig, ax = plt.subplots(figsize=(7, 4))
        _plot_panel(ax, attr, idx, title, ylabel)
        fig.tight_layout()
        fig.savefig(plots_dir / fname, dpi=100, bbox_inches='tight')
        plt.close(fig)
        plot_files.append((title, fname))
        log(f"  {fname}")
        _save_csv_1(attr, idx, csv_name)

    def _save_3panel(attr: str, title: str, ylabel: str,
                     fname: str, csv_name: str) -> None:
        fig, axes = plt.subplots(1, 3, figsize=(15, 4))
        for j, shell in enumerate(("Overall", "Low shell", "High shell")):
            _plot_panel(axes[j], attr, j, f"{title} — {shell}", ylabel)
        fig.tight_layout()
        fig.savefig(plots_dir / fname, dpi=100, bbox_inches='tight')
        plt.close(fig)
        plot_files.append((title, fname))
        log(f"  {fname}")
        _save_csv_3(attr, csv_name)

    # -------------------------------------------------------------------
    # Generate all plots + CSVs
    # -------------------------------------------------------------------
    log("Generating plots and CSV files…")

    _save_1panel("high_res",       0,    "High resolution limit",    "d (Å)",          "high_res.png",          "high_res.csv")
    _save_1panel("completeness",   0,    "Completeness",             "Completeness (%)", "completeness.png",    "completeness.csv")
    _save_1panel("multiplicity",   0,    "Multiplicity",             "Multiplicity",   "multiplicity.png",      "multiplicity.csv")
    _save_3panel("i_over_sigma",         "I/σ(I)",                   "I/σ(I)",         "i_sigma.png",           "i_sigma.csv")
    _save_3panel("rmerge_ipm",           "Rmerge(I+/-)",             "Rmerge",         "rmerge_ipm.png",        "rmerge_ipm.csv")
    _save_3panel("cc_half",              "CC½",                      "CC½",            "cc_half.png",           "cc_half.csv")
    _save_1panel("wilson_b",       None, "Wilson B factor",          "B (Å²)",         "wilson_b.png",          "wilson_b.csv")
    _save_3panel("anom_completeness",    "Anomalous completeness",   "Completeness (%)", "anom_completeness.png", "anom_completeness.csv")
    _save_3panel("anom_multiplicity",    "Anomalous multiplicity",   "Multiplicity",   "anom_multiplicity.png", "anom_multiplicity.csv")
    _save_1panel("anom_slope",     None, "Anomalous slope",          "Slope",          "anom_slope.png",        "anom_slope.csv")

    # Unit cell — 2×3 grid (a, b, c / α, β, γ)
    log("  unit_cell.png")
    _make_unit_cell_plot(results, energies, wedges, colors, plots_dir)
    plot_files.append(("Unit cell parameters", "unit_cell.png"))
    _save_unit_cell_csv(results, energies, wedges, data_dir)

    # -------------------------------------------------------------------
    # HTML report
    # -------------------------------------------------------------------
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
# Unit cell plot + CSV
# ---------------------------------------------------------------------------

def _make_unit_cell_plot(
    results: ResultMap,
    energies: list[int],
    wedges: list[int],
    colors: list,
    plots_dir: Path,
) -> None:
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
    fig.savefig(plots_dir / "unit_cell.png", dpi=100, bbox_inches='tight')
    plt.close(fig)


def _save_unit_cell_csv(
    results: ResultMap,
    energies: list[int],
    wedges: list[int],
    data_dir: Path,
) -> None:
    params = ["cell_a", "cell_b", "cell_c", "cell_alpha", "cell_beta", "cell_gamma"]
    header = ["wedge_img"] + [f"{e}_eV_{p}" for e in energies for p in params]
    rows = []
    for w in wedges:
        row = [w]
        for e in energies:
            stats = results[e].get(w)
            for p in params:
                val = getattr(stats, p, None) if stats else None
                row.append(f"{val:.6g}" if val is not None else "")
        rows.append(row)
    with open(data_dir / "unit_cell.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def _summary_table_html(
    results: ResultMap,
    energies: list[int],
    wedges: list[int],
) -> str:
    """Numerical summary: best resolution and completeness per energy/wedge."""
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
            def _f(v, idx=None, fmt=".2f"):
                if v is None:
                    return "—"
                val = v[idx] if (idx is not None and isinstance(v, tuple)) else v
                return f"{val:{fmt}}" if val is not None else "—"
            rows.append(
                f"<tr>"
                f"<td>{e}</td><td>{w}</td>"
                f"<td>{_f(stats.high_res, 0)}</td>"
                f"<td>{_f(stats.completeness, 0)}</td>"
                f"<td>{_f(stats.multiplicity, 0)}</td>"
                f"<td>{_f(stats.i_over_sigma, 0)}</td>"
                f"<td>{_f(stats.cc_half, 0)}</td>"
                f"<td>{_f(stats.rmerge_ipm, 0)}</td>"
                f"</tr>"
            )
    return (
        "<table>" + header + "".join(rows) + "</table>"
    )


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
    crystal_index = out_dir.parent.parent.parent / "index.html"  # spread/{pipeline}/run_N → crystal
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

    # ---- Run-status table ----
    header = "<th>Energy</th>" + "".join(f"<th>{w} img</th>" for w in wedges)
    rows: list[str] = []
    for energy in energies:
        cells = [f"<td>{energy} eV</td>"]
        for w in wedges:
            summary_html = proc_path / f"{energy}eV" / f"{w}img" / pipeline / "xia2.html"
            if summary_html.exists():
                link_open  = f'<a href="file://{summary_html}" target="_blank">'
                link_close = '</a>'
            else:
                link_open = link_close = ''
            if w not in results[energy]:
                cells.append('<td class="na">—</td>')
            elif results[energy][w] is None:
                cells.append(f'<td class="fail">{link_open}&#x2717; Failed{link_close}</td>')
            else:
                cells.append(f'<td class="ok">{link_open}&#x2713; OK{link_close}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")

    status_table = (
        '<table class="status">'
        f"<tr>{header}</tr>"
        + "".join(rows)
        + "</table>"
    )

    # ---- Plots section ----
    toc = "".join(
        f'<li><a href="#{fn}">{_html.escape(t)}</a></li>'
        for t, fn in plot_files
    )
    plot_sections = "".join(
        f'<h3 id="{fn}">{_html.escape(t)}</h3>'
        f'<a href="plots/{fn}"><img src="plots/{fn}" alt="{_html.escape(t)}"></a>'
        for t, fn in plot_files
    )

    summary_table = _summary_table_html(results, energies, wedges)

    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>xia2 Analysis &mdash; {_html.escape(pipeline)}</title>
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
<h1>xia2 Analysis &mdash; {_html.escape(pipeline)}</h1>
<p class="meta">Generated: {timestamp}</p>
<p class="meta">Processing path: {_html.escape(proc_path_str)}</p>

<h2>Run Status</h2>
<p>
  {n_total} run{'' if n_total == 1 else 's'} found &mdash;
  <span style="color:#1a7a3c">{n_ok} successful</span>,
  <span style="color:#c0392b">{n_fail} failed</span>
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

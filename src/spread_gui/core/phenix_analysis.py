"""
Generate a self-contained HTML report for phenix.refine anomalous refinement results.

Plots f' and f'' versus energy:
  - Summary: one figure per anomalous group (f' and f'' side-by-side), one line per wedge
  - Per-wedge: f' and f'' side-by-side, one line per group
  - R-work / R-free vs energy: one line per wedge size

Plots are embedded as base64 in the HTML report.
One CSV per wedge is also written alongside index.html ({images}img.csv):
  columns: energy_eV, group, f_prime, f_double_prime, r_work, r_free
"""
from __future__ import annotations

import base64
import csv
import html as _html
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Callable

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.cm as cm

from spread_gui.core.phenix_parser import PhenixResult, group_short_label


# ---------------------------------------------------------------------------
# Figure helpers — return (base64_png, ...)
# ---------------------------------------------------------------------------

def _group_summary_fig(
    results: dict[tuple[int, int], PhenixResult],
    energies: list[int],
    wedges: list[int],
    sel: str,
) -> str:
    """Render f'/f'' summary for one anomalous group. Returns base64 PNG."""
    colors = cm.tab10.colors
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    fig.suptitle(group_short_label(sel))

    for wi, images in enumerate(sorted(wedges)):
        xs, fps, fdps = [], [], []
        for energy in sorted(energies):
            res = results.get((energy, images))
            if res is None:
                continue
            for g in res.groups:
                if g.selection == sel:
                    xs.append(energy)
                    fps.append(g.f_prime)
                    fdps.append(g.f_double_prime)
                    break
        c = colors[wi % len(colors)]
        if xs:
            ax1.plot(xs, fps,  marker="o", color=c, label=f"{images} img")
            ax2.plot(xs, fdps, marker="o", color=c, label=f"{images} img")

    for ax, lbl in ((ax1, "f'"), (ax2, "f''")):
        ax.set_xlabel("Energy (eV)")
        ax.set_ylabel(lbl)
        ax.set_title(f"{lbl} vs Energy")
        ax.set_xticks(sorted(energies))
        ax.tick_params(axis="x", rotation=45)
        ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1),
                  borderaxespad=0, fontsize="small")

    plt.tight_layout()
    return _fig_to_b64(fig)


def _wedge_fig(
    results: dict[tuple[int, int], PhenixResult],
    energies: list[int],
    images: int,
    groups: list[str],
) -> str:
    """Render f'/f'' per-wedge figure. Returns base64 PNG."""
    colors = cm.tab10.colors
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    fig.suptitle(f"{images} images")

    for gi, sel in enumerate(groups):
        label = group_short_label(sel)
        xs, fps, fdps = [], [], []
        for energy in sorted(energies):
            res = results.get((energy, images))
            if res is None:
                continue
            for g in res.groups:
                if g.selection == sel:
                    xs.append(energy)
                    fps.append(g.f_prime)
                    fdps.append(g.f_double_prime)
                    break
        c = colors[gi % len(colors)]
        if xs:
            ax1.plot(xs, fps,  marker="o", color=c, label=label)
            ax2.plot(xs, fdps, marker="o", color=c, label=label)

    for ax, lbl in ((ax1, "f'"), (ax2, "f''")):
        ax.set_xlabel("Energy (eV)")
        ax.set_ylabel(lbl)
        ax.set_title(f"{lbl} vs Energy")
        ax.set_xticks(sorted(energies))
        ax.tick_params(axis="x", rotation=45)
        ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1),
                  borderaxespad=0, fontsize="small")

    plt.tight_layout()
    return _fig_to_b64(fig)


def _rwork_rfree_fig(
    results: dict[tuple[int, int], PhenixResult],
    energies: list[int],
    wedges: list[int],
) -> str:
    """Render R-work / R-free vs energy figure. Returns base64 PNG."""
    colors = cm.tab10.colors
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    fig.suptitle("R-work and R-free vs Energy")

    for wi, images in enumerate(sorted(wedges)):
        xs_rw, ys_rw, xs_rf, ys_rf = [], [], [], []
        for energy in sorted(energies):
            res = results.get((energy, images))
            if res is None:
                continue
            if res.r_work_final is not None:
                xs_rw.append(energy)
                ys_rw.append(res.r_work_final)
            if res.r_free_final is not None:
                xs_rf.append(energy)
                ys_rf.append(res.r_free_final)
        c = colors[wi % len(colors)]
        if xs_rw:
            ax1.plot(xs_rw, ys_rw, marker="o", color=c, label=f"{images} img")
        if xs_rf:
            ax2.plot(xs_rf, ys_rf, marker="o", color=c, label=f"{images} img")

    for ax, lbl in ((ax1, "R-work"), (ax2, "R-free")):
        ax.set_xlabel("Energy (eV)")
        ax.set_ylabel(lbl)
        ax.set_title(f"{lbl} vs Energy")
        ax.set_xticks(sorted(energies))
        ax.tick_params(axis="x", rotation=45)
        ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1),
                  borderaxespad=0, fontsize="small")

    plt.tight_layout()
    return _fig_to_b64(fig)


def _fig_to_b64(fig) -> str:
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def _summary_table(
    results: dict[tuple[int, int], PhenixResult],
    energies: list[int],
    images: int,
    groups: list[str],
) -> str:
    """HTML table of R-work/R-free and f'/f'' per energy for one wedge."""
    group_headers = "".join(
        f"<th>f' / f'' ({group_short_label(s)})</th>" for s in groups
    )
    header = (
        f"<tr><th>Energy (eV)</th><th>R-work</th><th>R-free</th>{group_headers}</tr>"
    )
    rows = []
    for energy in sorted(energies):
        res = results.get((energy, images))
        if res is None:
            continue
        rw = f"{res.r_work_final:.4f}" if res.r_work_final is not None else "—"
        rf = f"{res.r_free_final:.4f}" if res.r_free_final is not None else "—"
        vals = ""
        sel_to_g = {g.selection: g for g in res.groups}
        for sel in groups:
            g = sel_to_g.get(sel)
            if g:
                vals += f"<td>{g.f_prime:.3f} / {g.f_double_prime:.3f}</td>"
            else:
                vals += "<td>—</td>"
        rows.append(f"<tr><td>{energy}</td><td>{rw}</td><td>{rf}</td>{vals}</tr>")

    return (
        "<table border='1' cellpadding='4' cellspacing='0' "
        "style='border-collapse:collapse;font-family:monospace;font-size:0.88em;"
        "margin-bottom:12px;'>"
        + header
        + "".join(rows)
        + "</table>"
    )


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def _write_csvs(
    results: dict[tuple[int, int], PhenixResult],
    energies: list[int],
    wedges: list[int],
    groups: list[str],
    out_dir: Path,
) -> None:
    """Write one CSV per wedge size ({images}img.csv) into out_dir.

    Columns: energy_eV, group, f_prime, f_double_prime, r_work, r_free
    r_work and r_free are repeated for every group row at the same energy.
    """
    for images in sorted(wedges):
        path = out_dir / f"{images}img.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["energy_eV", "group", "f_prime", "f_double_prime", "r_work", "r_free"])
            for energy in sorted(energies):
                res = results.get((energy, images))
                if res is None:
                    continue
                rw = res.r_work_final if res.r_work_final is not None else ""
                rf = res.r_free_final if res.r_free_final is not None else ""
                sel_to_g = {g.selection: g for g in res.groups}
                for sel in groups:
                    g = sel_to_g.get(sel)
                    if g is not None:
                        w.writerow([energy, group_short_label(sel), g.f_prime, g.f_double_prime, rw, rf])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_report(
    results: dict[tuple[int, int], PhenixResult],
    out_dir: Path,
    pipeline: str,
    proc_path: str,
    run: int,
    progress: Callable[[str], None] | None = None,
    pdb_model: str = "",
    anomalous_def: str = "",
    macro_cycles: int = 0,
    project_name: str = "",
    crystal_name: str = "",
) -> str:
    """
    Generate a self-contained HTML report inside *out_dir*.
    Plots are embedded as base64; no external PNG or CSV files are written.
    Returns the path to index.html as a string.
    Raises ValueError if *results* is empty.
    """
    if progress is None:
        progress = lambda _: None  # noqa: E731

    if not results:
        raise ValueError(
            "No phenix refinement results found — nothing to plot.\n\n"
            "Make sure the correct pipeline and run number are selected and "
            "that the refinement jobs have completed."
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    energies = sorted({e for e, _ in results})
    wedges   = sorted({i for _, i in results})

    first_key = min(results)
    groups: list[str] = [g.selection for g in results[first_key].groups]
    seen = set(groups)
    for res in results.values():
        for g in res.groups:
            if g.selection not in seen:
                groups.append(g.selection)
                seen.add(g.selection)

    # ---- CSV export ----
    progress("Writing CSV data files…")
    _write_csvs(results, energies, wedges, groups, out_dir)

    # ---- Summary plots (one per group) ----
    progress("Generating summary plots…")
    group_summary_sections: list[str] = []
    for sel in groups:
        label = group_short_label(sel)
        b64 = _group_summary_fig(results, energies, wedges, sel)
        group_summary_sections.append(
            f'<h2 id="summary_{_html.escape(label)}">Summary — {_html.escape(label)} (all wedges)</h2>\n'
            f'<img src="data:image/png;base64,{b64}" alt="Summary {_html.escape(label)}">'
        )

    # ---- R-work / R-free vs energy ----
    progress("Generating R-work/R-free plot…")
    rr_b64 = _rwork_rfree_fig(results, energies, wedges)
    rr_section = (
        '<h2 id="rwork_rfree">R-work and R-free vs Energy</h2>\n'
        f'<img src="data:image/png;base64,{rr_b64}" alt="Rwork Rfree">'
    )

    # ---- Per-wedge plots ----
    wedge_sections: list[str] = []
    for images in wedges:
        progress(f"Generating wedge plot: {images} images…")
        b64   = _wedge_fig(results, energies, images, groups)
        table = _summary_table(results, energies, images, groups)
        wedge_sections.append(
            f'<h2 id="wedge_{images}img">{images} images</h2>\n'
            f'<img src="data:image/png;base64,{b64}" alt="{images} images">\n'
            + table
        )

    # ---- HTML ----
    progress("Writing HTML report…")
    html_path = out_dir / "index.html"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Breadcrumb: out_dir = {results_path}/{crystal}/spread/{pipeline}/run_N
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
    breadcrumb_parts.append(f"SPREAD {_html.escape(pipeline)} — {out_dir.name}")
    breadcrumb = " / ".join(breadcrumb_parts)

    toc_items = [f'<li><a href="#summary_{group_short_label(s)}">Summary {group_short_label(s)}</a></li>' for s in groups]
    toc_items.append('<li><a href="#rwork_rfree">R-work / R-free</a></li>')
    toc_items += [f'<li><a href="#wedge_{w}img">{w} images</a></li>' for w in wedges]
    toc = "<ul>" + "".join(toc_items) + "</ul>"

    csv_link_items = " &nbsp;|&nbsp; ".join(
        f'<a href="{w}img.csv">{w}img.csv</a>' for w in wedges
    )
    csv_links = f'<p class="meta">Data: {csv_link_items}</p>'

    meta_lines = []
    if pdb_model:
        meta_lines.append(f"<b>PDB model:</b> {_html.escape(pdb_model)}")
    if anomalous_def:
        meta_lines.append(f"<b>Anomalous groups:</b> {_html.escape(anomalous_def)}")
    if macro_cycles:
        meta_lines.append(f"<b>Macro cycles:</b> {macro_cycles}")
    meta_lines.append(f"<b>Pipeline:</b> {_html.escape(pipeline)}")
    meta_lines.append(f"<b>Run:</b> {run}")
    meta_lines.append(f"<b>Processing path:</b> {_html.escape(proc_path)}")
    meta_block = " &nbsp;|&nbsp; ".join(meta_lines)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SPREAD Analysis — {_html.escape(pipeline)} run {run}</title>
<style>
body {{ font-family: Arial, sans-serif; max-width: 1600px; margin: 0 auto; padding: 16px; color: #222; }}
h1 {{ color: #1a4a8a; }}
h2 {{ color: #2a6ab0; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-top: 32px; }}
.meta {{ color: #666; font-size: 13px; margin-bottom: 8px; }}
.breadcrumb {{ font-size: 13px; color: #888; margin-bottom: 12px; }}
.breadcrumb a {{ color: #2a6ab0; text-decoration: none; }}
.breadcrumb a:hover {{ text-decoration: underline; }}
img {{ display: block; margin-bottom: 12px; border: 1px solid #ddd; max-width: 100%; }}
table {{ margin-bottom: 12px; border-collapse: collapse; font-size: 13px; }}
th, td {{ border: 1px solid #bbb; padding: 4px 10px; }}
th {{ background: #e8eef8; }}
a {{ color: #1a4a8a; }}
</style>
</head>
<body>
<p class="breadcrumb">{breadcrumb}</p>
<h1>SPREAD Anomalous Refinement Analysis</h1>
<p class="meta">{meta_block}</p>
<p class="meta">Generated: {timestamp}</p>

<h2>Contents</h2>
{toc}
{csv_links}

{"".join(group_summary_sections)}

{rr_section}

{"".join(wedge_sections)}
</body>
</html>
"""
    html_path.write_text(html, encoding="utf-8")
    progress(f"Report written to {html_path}")
    return str(html_path)

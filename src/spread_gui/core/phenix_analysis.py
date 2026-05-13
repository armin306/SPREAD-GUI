"""
Generate an HTML report for phenix.refine anomalous refinement results.

Plots f' and f'' versus energy:
  - Overview: one subplot per anomalous group, one line per wedge size
  - Per-wedge sections: f' and f'' side-by-side, one line per group
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Callable

from spread_gui.core.phenix_parser import PhenixResult, group_short_label


def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _overview_fig(
    results: dict[tuple[int, int], PhenixResult],
    energies: list[int],
    wedges: list[int],
    groups: list[str],
    attr: str,
    ylabel: str,
    title: str,
) -> str:
    """Return an HTML <img> tag for an overview plot (one subplot per group)."""
    import matplotlib.pyplot as plt
    import matplotlib.cm as cm

    n = len(groups)
    fig, axes = plt.subplots(1, max(n, 1), figsize=(4 * max(n, 1), 4), squeeze=False)
    fig.suptitle(title)

    for gi, sel in enumerate(groups):
        ax = axes[0][gi]
        ax.set_title(group_short_label(sel))
        ax.set_xlabel("Energy (eV)")
        ax.set_ylabel(ylabel)
        colors = cm.tab10.colors
        for wi, images in enumerate(sorted(wedges)):
            xs, ys = [], []
            for energy in sorted(energies):
                res = results.get((energy, images))
                if res is None:
                    continue
                for g in res.groups:
                    if g.selection == sel:
                        xs.append(energy)
                        ys.append(getattr(g, attr))
                        break
            if xs:
                ax.plot(xs, ys, marker="o", color=colors[wi % len(colors)],
                        label=f"{images} img")
        ax.set_xticks(sorted(energies))
        ax.tick_params(axis="x", rotation=45)
        ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1),
                  borderaxespad=0, fontsize="small")

    plt.tight_layout()
    tag = f'<img src="data:image/png;base64,{_fig_to_b64(fig)}" style="max-width:100%;">'
    plt.close(fig)
    return tag


def _wedge_fig(
    results: dict[tuple[int, int], PhenixResult],
    energies: list[int],
    images: int,
    groups: list[str],
) -> str:
    """Return an HTML <img> tag with f' and f'' side-by-side for one wedge."""
    import matplotlib.pyplot as plt
    import matplotlib.cm as cm

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
            ax1.plot(xs, fps, marker="o", color=c, label=label)
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
    tag = f'<img src="data:image/png;base64,{_fig_to_b64(fig)}" style="max-width:100%;">'
    plt.close(fig)
    return tag


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
        rw = f"{res.r_work_final:.4f}" if res.r_work_final is not None else "\u2014"
        rf = f"{res.r_free_final:.4f}" if res.r_free_final is not None else "\u2014"
        vals = ""
        sel_to_g = {g.selection: g for g in res.groups}
        for sel in groups:
            g = sel_to_g.get(sel)
            if g:
                vals += f"<td>{g.f_prime:.3f} / {g.f_double_prime:.3f}</td>"
            else:
                vals += "<td>\u2014</td>"
        rows.append(f"<tr><td>{energy}</td><td>{rw}</td><td>{rf}</td>{vals}</tr>")

    return (
        "<table border='1' cellpadding='4' cellspacing='0' "
        "style='border-collapse:collapse;font-family:monospace;font-size:0.88em;"
        "margin-bottom:12px;'>"
        + header
        + "".join(rows)
        + "</table>"
    )


def generate_report(
    results: dict[tuple[int, int], PhenixResult],
    out_dir: Path,
    pipeline: str,
    proc_path: str,
    run: int,
    progress: Callable[[str], None] | None = None,
) -> str:
    """
    Generate an HTML report from *results* and write it to *out_dir*.

    Returns the path to the HTML file as a string.
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

    energies = sorted({e for e, _ in results})
    wedges   = sorted({i for _, i in results})

    # Determine a stable group order from the result with the lowest energy.
    first_key = min(results)
    groups: list[str] = [g.selection for g in results[first_key].groups]
    seen = set(groups)
    for res in results.values():
        for g in res.groups:
            if g.selection not in seen:
                groups.append(g.selection)
                seen.add(g.selection)

    progress("Generating overview plots…")
    fp_overview = _overview_fig(
        results, energies, wedges, groups,
        "f_prime", "f'", "f\u2019 vs Energy \u2014 all wedges",
    )
    fdp_overview = _overview_fig(
        results, energies, wedges, groups,
        "f_double_prime", "f\u2019\u2019", "f\u2019\u2019 vs Energy \u2014 all wedges",
    )

    wedge_sections: list[str] = []
    for images in wedges:
        progress(f"Generating wedge plot: {images} images\u2026")
        img_tag = _wedge_fig(results, energies, images, groups)
        table   = _summary_table(results, energies, images, groups)
        wedge_sections.append(
            f"<h2>{images} images</h2>\n{img_tag}\n{table}"
        )

    progress("Writing HTML report\u2026")
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "phenix_analysis.html"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Phenix Analysis \u2014 {pipeline} run {run}</title>
<style>
body {{ font-family: sans-serif; max-width: 1600px; margin: 0 auto; padding: 16px; }}
h1 {{ color: #2c3e50; }}
h2 {{ color: #34495e; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-top: 32px; }}
img {{ display: block; margin-bottom: 12px; }}
table {{ margin-bottom: 12px; }}
</style>
</head>
<body>
<h1>Phenix Anomalous Refinement Analysis</h1>
<p>
  <b>Pipeline:</b> {pipeline} &nbsp;
  <b>Run:</b> {run} &nbsp;
  <b>Path:</b> {proc_path}
</p>

<h2>Overview \u2014 f\u2019 (all wedges)</h2>
{fp_overview}

<h2>Overview \u2014 f\u2019\u2019 (all wedges)</h2>
{fdp_overview}

{"".join(wedge_sections)}
</body>
</html>
"""
    html_path.write_text(html, encoding="utf-8")
    progress(f"Report written to {html_path}")
    return str(html_path)

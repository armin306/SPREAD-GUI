from __future__ import annotations

try:
    import requests  # type: ignore
except Exception:
    requests = None

def fetch_pdb_text(code: str, timeout: int = 20) -> str:
    code = code.strip().upper()
    url = f"https://files.rcsb.org/download/{code}.pdb"

    if requests is not None:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.text

    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")

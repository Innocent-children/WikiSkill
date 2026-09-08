"""Extract the seven verbatim Appendix E listings from arXiv's embedded downloads."""

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path


def extract(source: Path, destination: Path) -> None:
    html = source.read_text(encoding="utf-8")
    listings = re.findall(r"data:text/plain;base64,([A-Za-z0-9+/=]+)", html)
    names = ["live_math", "sealqa", "spreadsheet", "officeqa", "alfworld", "maintainer", "proposer"]
    if len(listings) != len(names):
        raise ValueError(f"Expected seven Appendix E listings, got {len(listings)}")
    manifest = {"source": "https://arxiv.org/html/2608.27454v1#A5", "license": "CC-BY-4.0", "prompts": {}}
    destination.mkdir(parents=True, exist_ok=True)
    for name, encoded in zip(names, listings):
        content = base64.b64decode(encoded, validate=True)
        if not content.decode("utf-8").startswith("You are "):
            raise ValueError(f"Unexpected listing: {name}")
        (destination / f"{name}.md").write_bytes(content)
        manifest["prompts"][f"{name}.md"] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    (destination / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("html", type=Path)
    parser.add_argument("--destination", type=Path, default=Path("wikiskill/prompts"))
    args = parser.parse_args()
    extract(args.html, args.destination)

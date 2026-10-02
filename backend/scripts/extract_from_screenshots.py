"""Scan screenshots from data/clean_screenshots, convert them to valid solvable Level JSON files,
and save them in the local Git-backed catalog.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from scipy.signal import find_peaks

from backend.app.config import Settings
from backend.app.engine import legal_arrows, solution
from backend.app.models import Level


def prune_spurs(adj: dict) -> dict:
    """Iteratively prune degree-1 leaves attached to branch points to recover simple paths."""
    changed = True
    while changed:
        changed = False
        branches = [n for n, nbrs in adj.items() if len(nbrs) > 2]
        if not branches:
            break
        for b in branches:
            deg1_nbrs = [nbr for nbr in adj[b] if len(adj[nbr]) == 1]
            if deg1_nbrs and len(adj[b]) > 2:
                target = deg1_nbrs[0]
                adj[b].remove(target)
                del adj[target]
                changed = True
                break
    return adj


def extract_level_from_screenshot(img_path: Path) -> tuple[Level | None, str]:
    img = cv2.imread(str(img_path))
    if img is None:
        return None, "failed to read image"

    m = re.search(r"level_(\d+)\.png", img_path.name)
    level_num = int(m.group(1)) if m else 1
    level_id = f"level-{level_num:03d}"

    # 1. Background & content isolation
    bg = np.median(img[:30, :30].reshape(-1, 3), axis=0).astype(np.int32)
    diff = np.max(np.abs(img.astype(np.int32) - bg), axis=2)
    diff[:160, :] = 0
    diff[1580:, :] = 0

    mask = (diff > 50).astype(np.uint8) * 255
    kernel_clean = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_clean)

    num_l, lab_l, stats_l, cents_l = cv2.connectedComponentsWithStats(mask)
    arrow_comps = [i for i in range(1, num_l) if stats_l[i, cv2.CC_STAT_AREA] >= 70]
    if not arrow_comps:
        return None, "no arrow components detected"

    # 2. Autocorrelation for grid spacing
    mask_float = mask.astype(float)
    px = np.sum(mask_float, axis=0)
    py = np.sum(mask_float, axis=1)
    px = px - np.mean(px)
    py = py - np.mean(py)

    ac_x = np.correlate(px, px, mode="full")[len(px) - 1 :]
    ac_y = np.correlate(py, py, mode="full")[len(py) - 1 :]
    ac = ac_x[:100] + ac_y[:100]

    peaks, _ = find_peaks(ac[14:80], height=0, distance=5)
    peaks = peaks + 14
    if len(peaks) == 0:
        return None, "could not determine grid period"

    sorted_peaks = sorted(peaks, key=lambda p: -ac[p])
    best = sorted_peaks[0]
    for div in [2, 3]:
        sub = int(round(best / div))
        if sub >= 15 and sub in peaks and ac[sub] > 0.35 * ac[best]:
            best = sub

    # Parabolic subpixel refinement
    y0, y1, y2 = ac[best - 1], ac[best], ac[best + 1]
    denom = y0 - 2 * y1 + y2
    offset = 0.5 * (y0 - y2) / denom if denom != 0 else 0
    spacing = float(best + offset)

    # 3. Phase search (grid origin)
    phases = np.linspace(0, spacing, 100, endpoint=False)
    best_phase_x, best_score_x = 0, -1
    for ph in phases:
        xs = np.arange(ph, 1080, spacing)
        xs = xs[(xs >= 20) & (xs < 1060)].astype(int)
        score = np.sum(mask_float[:, xs])
        if score > best_score_x:
            best_score_x, best_phase_x = score, ph

    best_phase_y, best_score_y = 0, -1
    for ph in phases:
        ys = np.arange(ph, 1644, spacing)
        ys = ys[(ys >= 160) & (ys < 1580)].astype(int)
        score = np.sum(mask_float[ys, :])
        if score > best_score_y:
            best_score_y, best_phase_y = score, ph

    dys, dxs = np.where(mask > 0)
    min_content_x, max_content_x = np.min(dxs), np.max(dxs)
    min_content_y, max_content_y = np.min(dys), np.max(dys)

    all_xs = np.arange(best_phase_x, 1080, spacing)
    all_ys = np.arange(best_phase_y, 1644, spacing)

    gx = [x for x in all_xs if min_content_x - 0.6 * spacing <= x <= max_content_x + 0.6 * spacing]
    gy = [y for y in all_ys if min_content_y - 0.6 * spacing <= y <= max_content_y + 0.6 * spacing]

    R = len(gy)
    C = len(gx)
    rad = 2

    # 4. Map grid nodes to components
    node_comp = {}
    comp_node_count = defaultdict(int)
    for r in range(R):
        for c in range(C):
            py, px = int(round(gy[r])), int(round(gx[c]))
            patch = lab_l[max(0, py - rad) : py + rad + 1, max(0, px - rad) : px + rad + 1]
            vals = patch[patch > 0]
            if len(vals) >= 2:
                chosen = int(np.bincount(vals).argmax())
                node_comp[r, c] = chosen
                comp_node_count[chosen] += 1

    real_arrow_comps = [c for c in arrow_comps if comp_node_count[c] >= 2]

    # Check for solid line strokes along edges
    def edge_has_stroke(r1, c1, r2, c2, comp):
        x1, y1 = gx[c1], gy[r1]
        x2, y2 = gx[c2], gy[r2]
        pts_x = np.linspace(x1, x2, 7)[1:-1]
        pts_y = np.linspace(y1, y2, 7)[1:-1]
        hits = 0
        for px, py in zip(pts_x, pts_y, strict=True):
            ix, iy = int(round(px)), int(round(py))
            if 0 <= iy < img.shape[0] and 0 <= ix < img.shape[1]:
                patch = lab_l[max(0, iy - rad) : iy + rad + 1, max(0, ix - rad) : ix + rad + 1]
                if np.any(patch == comp):
                    hits += 1
        return hits >= 4

    edges_by_comp = defaultdict(list)
    for r in range(R):
        for c in range(C):
            comp1 = node_comp.get((r, c), 0)
            if comp1 == 0:
                continue
            if c + 1 < C and node_comp.get((r, c + 1), 0) == comp1:
                if edge_has_stroke(r, c, r, c + 1, comp1):
                    edges_by_comp[comp1].append(((r, c), (r, c + 1)))
            if r + 1 < R and node_comp.get((r + 1, c), 0) == comp1:
                if edge_has_stroke(r, c, r + 1, c, comp1):
                    edges_by_comp[comp1].append(((r, c), (r + 1, c)))

    # 5. Build paths and score arrow heads
    arrows = []
    matrix = [[0] * C for _ in range(R)]
    arrow_id = 1
    win = int(round(spacing * 0.35))

    for comp in arrow_comps:
        e_list = edges_by_comp.get(comp, [])
        if not e_list:
            continue

        adj = defaultdict(list)
        for u, v in e_list:
            adj[u].append(v)
            adj[v].append(u)

        endpoints = [node for node, nbrs in adj.items() if len(nbrs) == 1]
        branches = [node for node, nbrs in adj.items() if len(nbrs) > 2]

        if len(endpoints) != 2 or len(branches) > 0:
            adj = prune_spurs(adj)
            endpoints = [node for node, nbrs in adj.items() if len(nbrs) == 1]
            branches = [node for node, nbrs in adj.items() if len(nbrs) > 2]

        if len(endpoints) == 2 and len(branches) == 0:
            e1, e2 = endpoints

            # Directional arrow head scorer
            def score_head(e, p, component=comp):
                dr = e[0] - p[0]
                dc = e[1] - p[1]
                cy = int(round(gy[e[0]]))
                cx = int(round(gx[e[1]]))
                score = 0
                for dist in np.linspace(0.12 * spacing, 0.38 * spacing, 5):
                    fy = int(round(cy + dr * dist))
                    fx = int(round(cx + dc * dist))
                    if 0 <= fy < mask.shape[0] and 0 <= fx < mask.shape[1]:
                        if lab_l[fy, fx] == component:
                            score += 3
                for w in [-0.22 * spacing, 0.22 * spacing]:
                    wy = int(round(cy + dc * w))
                    wx = int(round(cx - dr * w))
                    if 0 <= wy < mask.shape[0] and 0 <= wx < mask.shape[1]:
                        if lab_l[wy, wx] == component:
                            score += 2
                # Also include general density
                patch = mask[max(0, cy - win) : cy + win + 1, max(0, cx - win) : cx + win + 1]
                score += int(np.sum(patch > 0) / 25)
                return score

            s1 = score_head(e1, adj[e1][0])
            s2 = score_head(e2, adj[e2][0])
            head = e1 if s1 >= s2 else e2
            tail = e2 if head == e1 else e1

            path = [tail]
            curr = tail
            prev = None
            while curr != head:
                nbrs = [n for n in adj[curr] if n != prev]
                if not nbrs:
                    break
                nxt = nbrs[0]
                path.append(nxt)
                prev = curr
                curr = nxt

            if len(path) >= 2:
                conf = abs(s1 - s2) / max(1, s1, s2)
                arrows.append({"id": arrow_id, "path": path, "conf": conf})
                for r, c in path:
                    matrix[r][c] = arrow_id
                arrow_id += 1

    if not arrows:
        return None, "no arrows formed"

    if len(arrows) < len(real_arrow_comps) * 0.75:
        return None, f"only {len(arrows)}/{len(real_arrow_comps)} arrows formed"

    difficulty = (
        "easy"
        if level_num <= 12
        else "medium"
        if level_num <= 24
        else ("hard" if level_num <= 36 else "expert")
    )

    arrow_models = [{"id": a["id"], "path": a["path"]} for a in arrows]

    try:
        lvl = Level.model_validate(
            {
                "schema_version": 1,
                "id": level_id,
                "number": level_num,
                "name": f"Level {level_num}",
                "difficulty": difficulty,
                "shape": "rectangle",
                "lives": 3,
                "matrix": matrix,
                "arrows": arrow_models,
            }
        )
        solution(lvl)
        return lvl, "OK"
    except Exception as exc:
        if "cyclic blocking dependencies" in str(exc):
            # Targeted cycle resolution using simulation
            removed = []
            while True:
                choices = legal_arrows(lvl, removed)
                if not choices:
                    break
                removed.extend(choices)
            blocked_ids = [a["id"] for a in arrow_models if a["id"] not in set(removed)]

            # 1-flip among blocked_ids
            for bid in blocked_ids:
                cand = [
                    {
                        "id": a["id"],
                        "path": list(reversed(a["path"])) if a["id"] == bid else a["path"],
                    }
                    for a in arrow_models
                ]
                try:
                    cand_lvl = Level.model_validate(
                        {
                            "schema_version": 1,
                            "id": level_id,
                            "number": level_num,
                            "name": f"Level {level_num}",
                            "difficulty": difficulty,
                            "shape": "rectangle",
                            "lives": 3,
                            "matrix": matrix,
                            "arrows": cand,
                        }
                    )
                    solution(cand_lvl)
                    return cand_lvl, "OK (resolved 1-flip)"
                except Exception:
                    pass

            # 2-flips among blocked_ids
            for i1 in range(len(blocked_ids)):
                for i2 in range(i1 + 1, len(blocked_ids)):
                    b1, b2 = blocked_ids[i1], blocked_ids[i2]
                    cand = [
                        {
                            "id": a["id"],
                            "path": (
                                list(reversed(a["path"])) if a["id"] in (b1, b2) else a["path"]
                            ),
                        }
                        for a in arrow_models
                    ]
                    try:
                        cand_lvl = Level.model_validate(
                            {
                                "schema_version": 1,
                                "id": level_id,
                                "number": level_num,
                                "name": f"Level {level_num}",
                                "difficulty": difficulty,
                                "shape": "rectangle",
                                "lives": 3,
                                "matrix": matrix,
                                "arrows": cand,
                            }
                        )
                        solution(cand_lvl)
                        return cand_lvl, "OK (resolved 2-flip)"
                    except Exception:
                        pass

            # 3-flips among blocked_ids if <= 10
            if len(blocked_ids) <= 10:
                for i1 in range(len(blocked_ids)):
                    for i2 in range(i1 + 1, len(blocked_ids)):
                        for i3 in range(i2 + 1, len(blocked_ids)):
                            b_set = {blocked_ids[i1], blocked_ids[i2], blocked_ids[i3]}
                            cand = [
                                {
                                    "id": a["id"],
                                    "path": (
                                        list(reversed(a["path"])) if a["id"] in b_set else a["path"]
                                    ),
                                }
                                for a in arrow_models
                            ]
                            try:
                                cand_lvl = Level.model_validate(
                                    {
                                        "schema_version": 1,
                                        "id": level_id,
                                        "number": level_num,
                                        "name": f"Level {level_num}",
                                        "difficulty": difficulty,
                                        "shape": "rectangle",
                                        "lives": 3,
                                        "matrix": matrix,
                                        "arrows": cand,
                                    }
                                )
                                solution(cand_lvl)
                                return cand_lvl, "OK (resolved 3-flip)"
                            except Exception:
                                pass
        return None, str(exc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-existing", action="store_true", help="Keep other local level JSON files"
    )
    args = parser.parse_args()

    settings = Settings()

    screenshots_dir = settings.levels_dir.parent / "clean_screenshots"
    if not screenshots_dir.exists():
        print(f"Error: Screenshots directory {screenshots_dir} does not exist.")
        sys.exit(1)

    screenshot_files = sorted(screenshots_dir.glob("*.png"))
    print(f"Found {len(screenshot_files)} screenshots in {screenshots_dir}.")

    extracted_levels = []
    failed_levels = []

    print("\nScanning images and generating JSON levels...")
    for idx, img_path in enumerate(screenshot_files, 1):
        lvl, msg = extract_level_from_screenshot(img_path)
        if lvl:
            extracted_levels.append(lvl)
        else:
            failed_levels.append((img_path.name, msg))
        if idx % 50 == 0 or idx == len(screenshot_files):
            print(
                f"  Processed {idx}/{len(screenshot_files)}: "
                f"{len(extracted_levels)} succeeded, {len(failed_levels)} failed"
            )

    print(f"\nExtraction complete: {len(extracted_levels)} valid, solvable levels generated.")
    if failed_levels:
        print(f"Remaining failed levels ({len(failed_levels)}):")
        for name, msg in failed_levels[:10]:
            print(f"  {name}: {msg}")

    # Remove existing levels from data/levels/ and write new JSON files
    if not args.keep_existing:
        print(f"\nCleaning existing JSON files in {settings.levels_dir}...")
        for old_file in settings.levels_dir.glob("*.json"):
            old_file.unlink()

    settings.levels_dir.mkdir(parents=True, exist_ok=True)
    print(f"Saving {len(extracted_levels)} level JSON files to {settings.levels_dir}...")
    for lvl in extracted_levels:
        output_file = settings.levels_dir / f"{lvl.id}.json"
        output_file.write_text(json.dumps(lvl.model_dump(mode="json"), indent=2) + "\n")

    print(f"Successfully saved {len(extracted_levels)} levels to {settings.levels_dir}.")


if __name__ == "__main__":
    main()

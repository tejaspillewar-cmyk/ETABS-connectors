"""
Headless test: connect to a running ETABS instance, open a model,
and run pier_relabel.relabel() in preview mode to verify stacking logic.

Usage:
    python test_pier_relabel.py
"""
import sys
import os

# Ensure the connector directory is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MODEL_PATH = (
    r"D:\00 Project Data\26 Pune\03 WAKAD - Copy"
    r"\T 01 & T02\02 ETABS Model\02 UPDATED consultant model"
    r"\fdr\FDR_WAKAD_T1 _ULS.EDB"
)


def connect_to_etabs():
    """Attach to a running ETABS instance."""
    import comtypes.client
    try:
        helper = comtypes.client.CreateObject("ETABSv1.Helper")
        import comtypes.gen.ETABSv1 as ETABSv1
        helper = helper.QueryInterface(ETABSv1.cHelper)
    except Exception as exc:
        print(f"ERROR: Could not load ETABS COM API: {exc}")
        sys.exit(1)

    # Try to attach to a running instance
    try:
        obj = helper.GetObject("CSI.ETABS.API.ETABSObject")
        if obj is None:
            raise RuntimeError("No running ETABS found")
        obj = obj.QueryInterface(ETABSv1.cOAPI)
        SapModel = obj.SapModel
        print(f"Attached to ETABS. Active file: {SapModel.GetModelFilename()}")
        return SapModel
    except Exception as exc:
        print(f"ERROR: Could not attach to ETABS: {exc}")
        print("Make sure ETABS is running with the model open.")
        sys.exit(1)


def main():
    print("=" * 70)
    print("  PIER RELABEL TEST -- PREVIEW ONLY (no model changes)")
    print("=" * 70)
    print()

    SapModel = connect_to_etabs()

    active = SapModel.GetModelFilename()
    print(f"Active model: {active}")
    print()

    # ── Run the relabel preview ──────────────────────────────────────────
    import pier_relabel

    print("Reading model geometry...")
    walls = pier_relabel.load_walls(SapModel, log=print)
    order = pier_relabel.story_order(SapModel)
    print(f"Story order (bottom-up): {order}")
    print()

    clusters_by_story, stacks = pier_relabel.build_stacks(walls, order, log=print)
    print()

    # ── Report per-storey cluster counts ─────────────────────────────────
    print("─" * 50)
    print("  Clusters (piers) per storey:")
    print("─" * 50)
    for story in order:
        cls = clusters_by_story.get(story, [])
        print(f"    {story:30s}  {len(cls)} piers")
    print()

    # ── Show the stacks ──────────────────────────────────────────────────
    mapping = pier_relabel.assign_labels(clusters_by_story, stacks, mode="all")
    print(f"Total unique stacks (= unique pier labels): {len(stacks)}")
    print()

    # Build a story -> label set for the summary table
    labels_per_story = {}
    for story in order:
        labels = set()
        for cl in clusters_by_story.get(story, []):
            if cl.label:
                labels.add(cl.label)
        labels_per_story[story] = sorted(labels,
                                          key=lambda s: int(s[1:]) if s[1:].isdigit() else s)

    print("─" * 50)
    print("  Labels assigned per storey:")
    print("─" * 50)
    for story in reversed(order):
        labs = labels_per_story.get(story, [])
        preview = ", ".join(labs[:15])
        if len(labs) > 15:
            preview += f" ... (+{len(labs)-15} more)"
        print(f"    {story:30s}  [{len(labs):3d}]  {preview}")
    print()

    # ── Verify: same label never appears twice on one storey ─────────────
    print("─" * 50)
    print("  Validation checks:")
    print("─" * 50)
    problems = pier_relabel.diagnose(clusters_by_story)
    if problems:
        print(f"  WARNING: {len(problems)} label(s) cover >1 pier on a storey")
        for story, lab, cls in problems[:5]:
            print(f"    {lab} @ {story}: {len(cls)} piers")
    else:
        print("  OK: No label covers more than one pier on any storey")

    # Check vertical consistency: same stack → same label across storeys
    label_per_stack = {}
    consistent = True
    for i, stack in enumerate(stacks):
        labels = {cl.label for cl in stack if cl.label}
        if len(labels) > 1:
            print(f"  INCONSISTENT stack {i}: labels = {labels}")
            consistent = False
        elif labels:
            label_per_stack[i] = next(iter(labels))
    if consistent:
        print("  OK: Every vertical stack has a single consistent label")

    # Summary of changes
    changed = [w for w in walls if w.pier != mapping.get(w.name)]
    before_labels = {w.pier for w in walls if w.pier}
    after_labels = set(mapping.values())
    print()
    print(f"  Labels before: {len(before_labels)}")
    print(f"  Labels after:  {len(after_labels)}")
    print(f"  Wall areas that would change: {len(changed)}")
    print()
    print("=" * 70)
    print("  TEST COMPLETE -- no changes were made to the model")
    print("=" * 70)


if __name__ == "__main__":
    main()

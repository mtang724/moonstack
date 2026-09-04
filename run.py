"""MoonStack one-click runner.

    python run.py                 # full pipeline
    python run.py --stage analyze # only decode + analyze
    python run.py --from stack    # resume from a stage (analyze, group, stack, pixinsight, finish, photoshop, report)
"""
import argparse, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from moonstack import config, pipeline

STAGES = ["analyze", "group", "stack", "pixinsight", "finish", "photoshop", "report"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--stage", choices=STAGES, help="run only this stage")
    ap.add_argument("--from", dest="from_stage", choices=STAGES, help="resume from this stage")
    ap.add_argument("--to", dest="to_stage", choices=STAGES, help="stop after this stage")
    ap.add_argument("--no-pi", action="store_true", help="skip PixInsight")
    ap.add_argument("--no-ps", action="store_true", help="skip Photoshop")
    a = ap.parse_args()
    cfg = config.load(a.config)
    if a.no_pi:
        cfg["use_pixinsight"] = False
    if a.no_ps:
        cfg["use_photoshop"] = False
    os.makedirs(cfg["output_dir"], exist_ok=True)
    if a.stage:
        stages = [a.stage]
    else:
        i0 = STAGES.index(a.from_stage) if a.from_stage else 0
        i1 = STAGES.index(a.to_stage) + 1 if a.to_stage else len(STAGES)
        stages = STAGES[i0:i1]
    t0 = time.time()
    pipeline.run(cfg, stages)
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

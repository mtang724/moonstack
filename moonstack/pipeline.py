"""Stage orchestration. Every stage reads/writes JSON in output/ so any stage can be re-run alone."""
import os, json, time


def _log(cfg):
    path = os.path.join(cfg["output_dir"], "moonstack.log")
    f = open(path, "a", encoding="utf-8")

    def log(msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        f.write(line + "\n"); f.flush()
    return log


def _load(cfg, name):
    with open(os.path.join(cfg["output_dir"], name), "r", encoding="utf-8") as f:
        return json.load(f)


def run(cfg, stages):
    log = _log(cfg)
    log(f"=== MoonStack stages: {stages}")
    if "preflight" in stages:
        from . import preflight
        preflight.run(cfg, log=log)
    if "analyze" in stages:
        from . import analyze
        analyze.run(cfg, log=log)
    if "group" in stages:
        from . import phases
        phases.run(cfg, _load(cfg, "frames.json"), log=log)
    if "stack" in stages:
        from . import stack
        stack.run(cfg, _load(cfg, "frames.json"), _load(cfg, "groups.json"), log=log)
    if "pixinsight" in stages and cfg["use_pixinsight"]:
        from . import pi_bridge
        pi_bridge.run(cfg, _load(cfg, "groups.json"), log=log)
    if "finish" in stages:
        from . import finish
        finish.run(cfg, _load(cfg, "groups.json"), log=log)
    if "photoshop" in stages and cfg["use_photoshop"]:
        from . import ps_bridge
        ps_bridge.run(cfg, _load(cfg, "groups.json"), log=log)
    if "report" in stages:
        from . import report
        report.run(cfg, _load(cfg, "frames.json"), _load(cfg, "groups.json"), log=log)

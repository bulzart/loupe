"""A demo instance on port 8799 with throwaway data (fake pipeline run, two versions of a video, a few flags) for looking at the UI.
python tests/demo.py   ->  http://127.0.0.1:8799   passphrase: demo-passphrase"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.gettempdir()) / "loupe-demo"
os.environ["LOUPE_DATA"] = str(TMP / "data")
os.environ["ATP_HOME"] = str(TMP / "atp")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loupe_app import core, flags, media, server  # noqa: E402


def mk(path: Path, expr: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    cp = subprocess.run([media.find_tool("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", expr, "-pix_fmt", "yuv420p", str(path)], capture_output=True)
    assert cp.returncode == 0, cp.stderr[-300:]


run = TMP / "atp" / "runs" / "20261013-230824-13"
mk(run / "60-outputs" / "video-03" / "a1" / "raw.mp4", "testsrc2=size=720x1280:rate=30:duration=4,drawbox=x=180:y=520:w=260:h=260:color=red:t=fill")
mk(run / "60-outputs" / "video-03" / "a2" / "raw.mp4", "testsrc2=size=720x1280:rate=30:duration=4,drawbox=x=180:y=520:w=260:h=260:color=green:t=fill")
(run / "50-requests").mkdir(parents=True, exist_ok=True)
(run / "30-assets").mkdir(parents=True, exist_ok=True)
req = {"params": {"model": "wan3_0_prime", "prompt": "A man holds the can near a celebrity banner. Keep the camera still.", "medias": []}}
(run / "50-requests" / "video-03-request.json").write_text(json.dumps(req))
(run / "state.json").write_text(json.dumps({"run_id": run.name, "mode": "analyze", "analyze_submode": "recreate", "model": "wan3_0_prime", "stage": "production",
    "created": "2026-10-13T23:08:24+02:00", "videos": [{"video": "03", "status": "done", "qc": "regenerated once: the pack colour changed after the cut"}], "submissions": []}))
core.ensure_dirs()
if not core.admin_is_set():
    core.admin_set("demo-passphrase")
if not flags.get("CF-001"):
    flags.save({"title": "Two jobs refused within ~16 s, no reason", "kind": "refused-no-reason", "status": "cleared",
                "where": {"run": run.name, "video": "03", "attempt": "a1", "model": "wan3_0_prime", "mode": "analyze-recreate"},
                "response": "none given: job failed after ~16 s, refunded 45 credits, no reason", "prompt": req["params"]["prompt"],
                "inputs": [{"label": "Video 1", "role": "video_references", "name": "src-V03.mp4", "suspect": True, "facts": {"sha1": "ab12cd34ef56", "text_seen": ["@someuser", "CELSIUS"]}}],
                "trigger": {"text": "the swapped celebrity's real face rides in the video reference", "evidence": "I", "tags": ["real-person-face", "video-reference"], "assets": ["Video 1"], "prompt_span": "celebrity banner"},
                "cleared": {"prompt": "A man holds the can. Keep the camera still."}}, "demo", create=True)
    flags.save({"title": "Image upload marked nsfw", "kind": "media-flagged", "status": "open", "where": {"run": run.name, "video": "05", "model": "seedance_2_5"},
                "response": "media marked nsfw", "prompt": "x", "trigger": {"text": "skin-toned product photo", "tags": ["nsfw-image"]}}, "demo", create=True)
if "--port" in sys.argv:
    port = int(sys.argv[sys.argv.index("--port") + 1])
else:
    port = 8799
server.serve(port)

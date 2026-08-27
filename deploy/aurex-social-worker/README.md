# Aurex social worker

`worker.py` is the source deployed to `/opt/aurex-social-worker/worker.py` on the VPS.
It owns Instagram/Threads scheduling and TikTok status watching/retry; scheduled
TikTok posts themselves are created directly on Zernio.

Before deploying, compile the file, back up the current VPS copy, replace it,
and restart `aurex-social-worker`. The regression coverage is in
`tests/test_tiktok_worker.py`.

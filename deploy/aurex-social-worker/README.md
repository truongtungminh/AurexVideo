# Aurex social worker

`worker.py` is the source deployed to `/opt/aurex-social-worker/worker.py` on the VPS.
It owns Instagram/Threads scheduling and TikTok status watching/retry; scheduled
TikTok posts themselves are created directly on Zernio.

Instagram/Threads jobs persist their provider id after a successful publish. An
R2 upload failure is retried up to three times at five-minute intervals; errors
after the provider request starts remain failed for manual review so a retry
cannot create a duplicate post.

Before deploying, compile the file, back up the current VPS copy, replace it,
and restart `aurex-social-worker`. The regression coverage is in
`tests/test_tiktok_worker.py`.

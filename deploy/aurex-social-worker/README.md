# Aurex social worker

`worker.py` is the source deployed to `/opt/aurex-social-worker/worker.py` on the VPS.
It owns Instagram/Threads scheduling and TikTok status watching/retry; scheduled
TikTok posts themselves are created directly on Zernio. For Instagram/Threads,
the local engine uploads the rendered MP4 to public R2 before creating the VPS
job. The VPS stores that `videoUrl`, verifies it is readable at due time, and
only then calls Meta; it is not the media staging host.
Brand-scoped Instagram/Threads credentials are read from
`/etc/aurex-social-worker-social.json`; a job whose account does not match its
Brand is rejected before any provider request.

Instagram/Threads jobs persist their provider id after a successful publish and
use a unique idempotency key so a lost `/schedule` response cannot create a
second job. A missing/unreadable R2 URL is retried up to three times at
five-minute intervals; errors after the provider request starts remain failed
for manual review so a retry cannot create a duplicate post. Rows from the old
`videoPath` contract remain a compatibility fallback only.

Before deploying, compile the file, back up the current VPS copy, replace it,
and restart `aurex-social-worker`. The regression coverage is in
`tests/test_tiktok_worker.py`.

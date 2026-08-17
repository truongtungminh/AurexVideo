# Publish Instagram Reels through Cloudflare R2

AurexVideo uses this flow:

```text
final_video.mp4 → R2 public HTTPS URL → Instagram media container
→ poll FINISHED → media_publish → save media ID/permalink
```

## Requirements

- The Instagram account must be Professional (Business or Creator).
- Create a Meta developer app and grant the publishing permissions for the selected login flow.
- The MVP accepts an IG User ID and access token manually. Never commit tokens to source control.
- `Instagram Login` uses `https://graph.instagram.com`; `Facebook Login / Page token` uses `https://graph.facebook.com`.

## R2 setup

Create an R2 bucket, an Object Read & Write API token, and a public custom domain such as `https://media.example.com`. Instagram must be able to GET the video without authentication. Avoid short-lived signed URLs.

Enter the Account ID, bucket, Access Key ID, Secret Access Key, public base URL, and the `instagram` object prefix in the Upload Center.

R2 is temporary media storage. AurexVideo deletes the object after a successful publish by default; enable “Keep media on R2” when you need to retain it.

## Operation

Open Upload Center → Instagram Reels → Configure Instagram + R2, then enter the IG User ID, access token, API mode, Graph API version, and R2 credentials.

The app polls the media container until `FINISHED` before calling `media_publish`. If processing fails or times out, the R2 object is retained for troubleshooting.

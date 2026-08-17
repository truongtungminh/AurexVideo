# YouTube API upload guide

After you create an OAuth client in Google Cloud, paste the Client ID and Client Secret directly in Upload Center. You do not need to open or edit a config file.

## 1. What you need

- A Google account that can upload to the target YouTube channel.
- A Google Cloud project.
- YouTube Data API v3 enabled.
- An OAuth Client ID of type Web application.
- The `Client ID` and `Client Secret` to paste into Upload Center.

## 2. Create a Google Cloud project

1. Open Google Cloud Console: https://console.cloud.google.com/
2. Create a new project, or select an existing one.
![alt text](../assets/upload/image.png)
3. Go to **APIs & Services** → **Library**.
4. Find and enable **YouTube Data API v3**.
![alt text](../assets/upload/image-1.png)
![alt text](../assets/upload/image-3.png)

Official docs:
- Upload a video: https://developers.google.com/youtube/v3/guides/uploading_a_video
- `videos.insert`: https://developers.google.com/youtube/v3/docs/videos/insert
- YouTube Data API OAuth: https://developers.google.com/youtube/v3/guides/authentication

## 3. Configure the OAuth consent screen

1. Go to **APIs & Services** → **OAuth consent screen** → **Get Started**.
2. Fill in the app name and support email.
3. Choose **External** if you upload with a regular Google account.
4. Add the YouTube scopes required for upload.
5. Add test users while the app is still in Testing.
6. When everything works, move **Publishing status** to **In production**.

While the app remains in **Testing**, Google can make the refresh token expire after **7 days**. Move the consent screen to **In production** before distributing AurexVideo to customers.

Note: In production does not make tokens live forever. Google can still revoke tokens if the user removes access, changes security settings, leaves the app unused for a long time, or violates policy.

## 4. Create an OAuth Client ID

1. Go to **APIs & Services** → **Credentials**.
2. Click **Create credentials** → **OAuth client ID**.
3. Choose **Web application**.
4. Add the redirect URI shown in AurexVideo Upload Center.
5. Create the client and copy the Client ID and Client Secret.

## 5. Connect in AurexVideo

1. Open Upload Center.
2. Click **OAuth key**.
3. Paste Client ID and Client Secret.
4. Click **Save and connect**, then finish Google sign-in in the browser.
5. Choose the YouTube channel and upload.

You can click **OAuth key** again later to replace the saved Client ID or Client Secret. Keys and tokens are not shown again in the UI.

## 6. Common errors

- `access_denied`: the Google account is not allowed, or consent screen setup is incomplete.
- `redirect_uri_mismatch`: the redirect URI in Google Cloud does not match AurexVideo.
- `invalid_grant`: the refresh token was revoked; connect again.
- `Token expired or was revoked`: reconnect the YouTube channel. If this happens every 7 days, confirm the OAuth consent screen is **In production**.
- Missing `refresh_token`: disconnect the app in Google account permissions, then connect again and make sure the consent screen requests offline access.

## 7. Security notes

- Do not commit Client Secret or tokens into git.
- Keep Upload Center credentials only on this computer.
- If a secret leaks, create a new OAuth client and revoke the old one.

# Facebook API guide for Reels / video upload

AurexVideo uses the **Facebook Reels Publishing API**. After you have a Page ID and Page access token, paste them with **Add Page** in Upload Center.

## 1. Requirements

- A Meta developer account.
- A Meta app.
- A Facebook Page where you can create content.
- A Page access token with permission to publish video/Reels.
- App Review if you use the app beyond testers/admins/developers.
- The Page ID and Page access token to paste into Upload Center.

## 2. Create a Meta app

1. Open Meta for Developers: https://developers.facebook.com/
2. Go to **My Apps** → **Create App**.
3. Choose an app type that fits Page management.
![alt text](../assets/upload/image-5.png)
![alt text](../assets/upload/image-6.png)

Continue and create the app.

![alt text](../assets/upload/image-10.png)

Choose customize when asked.

## 3. Add products and permissions

![alt text](../assets/upload/image-22.png)

Add the products/permissions needed for Page video/Reels publishing, typically including:

- `pages_show_list`
- `pages_read_engagement`
- `pages_manage_posts`
- `pages_manage_engagement`
- permissions required by the Reels publishing flow for your app type

Use Graph API Explorer and Token Debugger while testing.

## 4. Get Page ID and Page access token

1. Open Graph API Explorer.
2. Select your app.
3. Generate a User token with the needed permissions.
4. Exchange/extend it into a long-lived Page access token for the target Page.
5. Copy the Page ID and Page access token.

Official tools:

- Graph API Explorer: https://developers.facebook.com/tools/explorer/
- Token Debugger: https://developers.facebook.com/tools/debug/accesstoken/

## 5. Connect in AurexVideo

1. Open Upload Center.
2. Click **Add Page**.
3. Paste Page ID and Page access token.
4. Click **Save Page**.
5. Choose the Page, write the caption/source comment, then upload.

## 6. Common errors

- `OAuthException` / invalid token: token expired or missing permissions; create a new Page token.
- Cannot publish: the account is not an admin/editor of the Page, or App Review is incomplete.
- Empty media / processing failed: wait for Facebook processing, then retry.
- Source comment failed: the Reel may not be ready yet; AurexVideo retries once after a short wait.

## 7. Security notes

- Do not commit Page tokens into git.
- Store tokens only inside the AurexVideo app data on this computer.
- If a token leaks, revoke it in Meta and create a new one.

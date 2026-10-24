"""YouTube Data API v3 metadata of one channel -> JSON list of {id, published, title, description, tags, duration (ISO 8601)}.
channels.list (1 unit) -> uploads playlist -> playlistItems.list (1 unit per 50) -> videos.list snippet,contentDetails (1 unit per 50).
Usage: YT_KEY=... python3 fetch_videos.py CHANNEL_ID OUT.json   (prints the quota units used; the key is never printed)"""
import json
import os
import sys
import urllib.error
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

API = "https://www.googleapis.com/youtube/v3/"


def call(endpoint, key, **params):
    """One API request -> parsed JSON. Errors are re-raised without the URL (it carries the key)."""
    url = API + endpoint + "?" + urllib.parse.urlencode({**params, "key": key})
    try:
        return json.loads(common.http_get(url, interval=0))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read())["error"]["message"]
        except Exception:
            msg = e.reason
        raise SystemExit(f"YouTube API {endpoint}: HTTP {e.code} {str(msg).replace(key, '***')}") from None


def fetch(channel_id, key, call=call):
    """-> (videos, units). `call` is replaceable for tests."""
    units = 1
    ch = call("channels", key, part="contentDetails", id=channel_id)
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while True:
        r = call("playlistItems", key, part="contentDetails", playlistId=uploads, maxResults=50, **({"pageToken": token} if token else {}))
        units += 1
        ids += [i["contentDetails"]["videoId"] for i in r["items"]]
        token = r.get("nextPageToken")
        if not token:
            break
    videos = []
    for i in range(0, len(ids), 50):
        r = call("videos", key, part="snippet,contentDetails", id=",".join(ids[i:i + 50]))
        units += 1
        for v in r["items"]:
            s = v["snippet"]
            videos.append({"id": v["id"], "published": s["publishedAt"], "title": s["title"], "description": s.get("description", ""),
                           "tags": s.get("tags", []), "duration": v["contentDetails"]["duration"]})
    return videos, units


def main(channel_id, out):
    key = os.environ.get("YT_KEY")
    if not key:
        sys.exit("YT_KEY is not set")
    videos, units = fetch(channel_id, key)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=1)
    print(f"{len(videos)} videos, quota units used: {units}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])

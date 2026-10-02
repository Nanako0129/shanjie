"""TypeSafe System One 的最小呼叫器（標準函式庫，不裝 SDK）。

key：環境變數 TYPESAFE_API_KEY，否則 ~/.config/typesafe/api_key；絕不印出或寫進檔案。
模型釘死 jev-1.13.0：jev-latest 會靜默換模型，而模型版本是判斷結果的一部分。
"""
import http.client
import json
import os
import time

MODEL = "jev-1.13.0"
HOST = "api.typesafe.ai"


def _key():
    k = os.environ.get("TYPESAFE_API_KEY") or open(os.path.expanduser("~/.config/typesafe/api_key")).read()
    return k.strip()


class Client:
    """一條持久的 HTTPS 連線；reconnect() 用來量冷連線。"""

    def __init__(self, timeout=30):
        self.timeout, self.conn, self.key = timeout, None, _key()

    def reconnect(self):
        if self.conn:
            self.conn.close()
        self.conn = http.client.HTTPSConnection(HOST, timeout=self.timeout)

    def ask(self, state, questions):
        """回傳 (answers, usage, 秒數)。非 200 直接丟例外，訊息不含 key。"""
        if self.conn is None:
            self.reconnect()
        body = json.dumps({"state": state, "model": MODEL, "questions": questions}, ensure_ascii=False)
        t = time.perf_counter()
        try:
            self.conn.request("POST", "/v1/systemone", body.encode("utf-8"),
                              {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
            resp = self.conn.getresponse()
            data = resp.read()
        except (http.client.HTTPException, OSError):
            self.reconnect()   # 連線斷了就重連一次再送
            self.conn.request("POST", "/v1/systemone", body.encode("utf-8"),
                              {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
            resp = self.conn.getresponse()
            data = resp.read()
        secs = time.perf_counter() - t
        if resp.status != 200:
            raise RuntimeError(f"jev HTTP {resp.status}: {data[:200]!r}")
        out = json.loads(data)
        return out["answers"], out.get("usage", {}), secs

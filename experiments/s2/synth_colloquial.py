"""S2：用 Cerebras 上的 gpt-oss-120b（Apache-2.0 開放權重）合成台灣口語聊天句，當 n-gram 訓練語料。

提示詞只描述主題與語氣，不放任何評測句。輸出經過濾（漢字 ≥ 70%、4–60 字、不含簡體專用字、去重），
附加寫到 ~/.cache/shanjie/work/s2/synth.txt。花費記在 synth-spend.json，超過 --budget-usd 就停。
key 從環境變數 CEREBRAS_API_KEY 讀（呼叫者從鑰匙圈讀進單一程序），不印、不寫檔。
用法：python3 experiments/s2/synth_colloquial.py --requests 50 [--workers 6] [--budget-usd 10]
"""
import argparse
import concurrent.futures as cf
import http.client
import itertools
import json
import os
import random
import re
import threading
import time

OUT = os.path.expanduser("~/.cache/shanjie/work/s2")
SRC = os.path.expanduser("~/.cache/shanjie/sources")
PRICE_IN, PRICE_OUT = 1.0, 3.0   # 每百萬 token 的保守上限估計（不是帳單），只用來擋預算
TOPICS = ["早餐午餐晚餐", "上班開會", "寫程式除錯", "人工智慧工具", "手機與電腦", "線上遊戲", "追劇看電影", "網購與團購",
          "捷運公車通勤", "學校作業考試", "看醫生與健康", "運動健身", "寵物", "出國旅行", "天氣", "朋友聚餐",
          "感情與家人", "存錢與投資", "租房搬家", "超商與外送", "演唱會與追星", "社群貼文與網紅", "加班與請假", "颱風假"]
STYLES = ["朋友在通訊軟體上的閒聊", "群組裡的簡短回覆", "抱怨或吐槽", "約時間或討論計畫", "分享心得或推薦", "問問題或求助"]
PROMPT = ("請寫 20 句台灣年輕人在{style}中會打的句子，主題是「{topic}」。\n"
          "要求：台灣用語、繁體中文、口語自然，可以用語助詞（啦、喔、欸、齁、吧、嗎、耶）和常見流行用語；"
          "每句 6 到 30 個字；不要人名、不要英文、不要數字、不要表情符號；一行一句，不要編號或引號。")
lock = threading.Lock()


def simp_only():
    s = set()
    for line in open(os.path.join(SRC, "opencc", "STCharacters.txt"), encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) == 2 and p[0] not in p[1].split(" "):
            s.add(p[0])
    return s


def call(key, prompt):
    body = {"model": "gpt-oss-120b", "messages": [{"role": "user", "content": prompt}], "max_tokens": 2000,
            "temperature": 1.0, "reasoning_effort": "low"}
    for attempt in range(5):
        conn = http.client.HTTPSConnection("api.cerebras.ai", timeout=90)
        try:
            conn.request("POST", "/v1/chat/completions", json.dumps(body, ensure_ascii=False).encode(),
                         {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            r = conn.getresponse(); data = r.read()
            if r.status == 200:
                return json.loads(data)
            if r.status in (429, 500, 502, 503):
                time.sleep(2 ** attempt + random.random()); continue
            raise SystemExit(f"HTTP {r.status}")
        except (OSError, http.client.HTTPException):
            time.sleep(2 ** attempt)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=50)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--budget-usd", type=float, default=10.0)
    a = ap.parse_args()
    key = os.environ.get("CEREBRAS_API_KEY", "").strip()
    if not key:
        raise SystemExit("missing CEREBRAS_API_KEY")
    os.makedirs(OUT, exist_ok=True)
    ledger = os.path.join(OUT, "synth-spend.json")
    spent = json.load(open(ledger))["usd"] if os.path.exists(ledger) else 0.0
    bad = simp_only()
    out_path = os.path.join(OUT, "synth.txt")
    seen = set(open(out_path, encoding="utf-8").read().splitlines()) if os.path.exists(out_path) else set()
    han = re.compile(r"[一-鿿]")
    combos = list(itertools.product(TOPICS, STYLES)); random.shuffle(combos)
    prompts = [PROMPT.format(topic=t, style=s) for t, s in itertools.islice(itertools.cycle(combos), a.requests)]
    kept = 0
    with cf.ThreadPoolExecutor(a.workers) as ex, open(out_path, "a", encoding="utf-8") as f:
        for out in ex.map(lambda p: call(key, p), prompts):
            if not out:
                continue
            u = out.get("usage", {})
            cost = (u.get("prompt_tokens", 0) * PRICE_IN + u.get("completion_tokens", 0) * PRICE_OUT) / 1e6
            text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            with lock:
                spent += cost
                for line in text.splitlines():
                    s = re.sub(r"^[\s\-•*\d.、)）]+", "", line).strip().strip("「」\"")
                    if not (4 <= len(s) <= 60) or s in seen or any(c in bad for c in s):
                        continue
                    if len(han.findall(s)) < 0.7 * len(s):
                        continue
                    seen.add(s); f.write(s + "\n"); kept += 1
                json.dump({"usd": spent}, open(ledger, "w"))
                if spent > a.budget_usd:
                    print(f"budget reached: US${spent:.2f}"); break
    print(f"kept {kept} new sentences; total {len(seen)}; ledger US${spent:.3f} (estimate)")


if __name__ == "__main__":
    main()

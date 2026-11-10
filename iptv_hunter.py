#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iptv_hunter.py  ——  IPTV 源：聚合 -> 解析 -> 去重 -> 可用性校验 -> 排序导出

零第三方依赖，Python 3.8+ 直接运行:

    python iptv_hunter.py                   # 默认: HTTP 可达 + m3u8 分片校验
    python iptv_hunter.py --deep            # 额外用 ffprobe 真拉流(需 ffmpeg 在 PATH)
    python iptv_hunter.py --workers 40 --out result
    python iptv_hunter.py --sources my.txt  # 只跑自定义源(每行一个 URL)
    python iptv_hunter.py --probe           # 只看某个 URL 通不通

产物: result.m3u / result.txt / result.report.csv

说明: 只收集公开可访问的流地址并做连通性验证，不代理、不转播、不存储视频内容。
"""

import argparse
import csv
import re
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

UA = "VLC/3.0.20 LibVLC/3.0.20"
TIMEOUT = 8.0             # 单请求超时(秒)
MAX_PER_NAME = 3          # 每个频道名最多保留几个源
CHUNK = 8192

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# ---------------------------------------------------------------- 源清单
# 都是公开聚合仓库的产物地址；失效时自己换镜像/分支即可
SOURCES = [
    ("iptv-org-cn",      "https://iptv-org.github.io/iptv/countries/cn.m3u"),
    ("iptv-org-global",  "https://iptv-org.github.io/iptv/index.m3u"),
    ("zbds-ipv4",        "https://live.zbds.top/tv/iptv4.m3u"),
    ("zbds-ipv6",        "https://live.zbds.top/tv/iptv6.m3u"),
    ("fanmingming",      "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/iptv.m3u"),
]

# ---------------------------------------------------------------- 频道名归一化
NOISE = re.compile(
    r"(4K|8K|UHD|FHD|HD|SD|1080[PI]|720[PI]|576[PI]|480[PI]|"
    r"超清|高清|标清|蓝光|超高清|原画|"
    r"H\.?26[45]|HEVC|AVC|HDR|50FPS|60FPS|25FPS|30FPS|备份|备用|测试)",
    re.I,
)


def norm_name(name: str) -> str:
    s = NOISE.sub("", name or "")
    s = re.sub(r"[\[\]()（）【】\-_·|]+", "", s)
    s = re.sub(r"\s+", "", s).upper()
    return s or (name or "").strip()


# ---------------------------------------------------------------- 解析器
ATTR = re.compile(r'([a-zA-Z0-9\-]+)="([^"]*)"')


def parse_m3u(text: str):
    out, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            head, _, tail = line.partition(",")
            attrs = dict(ATTR.findall(head))
            cur = {
                "name": tail.strip() or attrs.get("tvg-name", ""),
                "tvg_id": attrs.get("tvg-id", ""),
                "logo": attrs.get("tvg-logo", ""),
                "group": attrs.get("group-title", "") or "未分组",
            }
        elif line.startswith("#"):
            continue
        elif cur is not None:
            cur["url"] = line
            out.append(cur)
            cur = None
    return [c for c in out if c.get("url")]


def parse_txt(text: str):
    """兼容 '央视,#genre#' 分组行 + '频道名,url' 两种写法"""
    out, group = [], "未分组"
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(",#genre#"):
            group = line[:-8].strip()
            continue
        if "," not in line:
            continue
        name, _, url = line.rpartition(",")
        url = url.strip()
        if url.startswith(("http://", "https://", "rtp://", "rtsp://", "udp://")):
            out.append({"name": name.strip(), "url": url, "tvg_id": "",
                        "logo": "", "group": group})
    return out


def parse_any(text: str):
    return parse_m3u(text) if "#EXTINF" in text else parse_txt(text)


# ---------------------------------------------------------------- 抓取
def http_get(url: str, timeout=None, rng=None, limit=CHUNK):
    """返回 (status, headers, body_bytes, final_url)"""
    h = {"User-Agent": UA, "Accept": "*/*", "Connection": "close"}
    if rng:
        h["Range"] = f"bytes=0-{rng - 1}"
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout or TIMEOUT, context=SSL_CTX) as r:
        body = r.read(limit) or b""
        return r.status, dict(r.headers), body, r.geturl()


def fetch_playlist(url: str) -> str:
    _, _, body, _ = http_get(url, limit=16 * 1024 * 1024)
    return body.decode("utf-8", "ignore")


# ---------------------------------------------------------------- 校验
def check_stream(url: str, deep: bool = False):
    """
    三层判据: HTTP 可达 -> m3u8 分片可下载 / TS 同步字节 -> (可选)ffprobe 解出视频轨
    返回 dict(ok, latency_ms, kind, res, note)
    """
    r = {"ok": False, "latency_ms": 0, "kind": "", "res": "", "note": ""}
    if url.startswith(("rtp://", "udp://", "rtsp://")):
        r["note"] = "组播/RTP 需内网环境，跳过公网校验"
        return r

    t0 = time.time()
    try:
        status, headers, head, final = http_get(url, rng=CHUNK, limit=CHUNK)
    except urllib.error.HTTPError as e:
        r["note"] = f"HTTP {e.code}"
        return r
    except Exception as e:
        r["note"] = type(e).__name__
        return r

    ctype = (headers.get("Content-Type") or "").lower()

    if head[:1] == b"\x47" or "video/mp2t" in ctype:
        r.update(ok=True, kind="ts", note="ts 直连")
    elif b"#EXTM3U" in head or "mpegurl" in ctype:
        r["kind"] = "hls"
        text = head.decode("utf-8", "ignore")
        seg = next((l.strip() for l in text.splitlines()
                    if l.strip() and not l.startswith("#")), None)
        if seg:
            try:
                st, _, body, _ = http_get(urljoin(final, seg), rng=2048, limit=2048)
                if st in (200, 206) and len(body) > 188:
                    r.update(ok=True, note="m3u8 分片可下载")
            except Exception as e:
                r["note"] = f"分片 {type(e).__name__}"
        if not r["ok"]:
            r["note"] = r["note"] or "m3u8 无可下载分片"
    else:
        r["note"] = f"非流媒体响应({ctype[:24] or 'unknown'})"

    r["latency_ms"] = int((time.time() - t0) * 1000)
    if r["ok"] and deep:
        ok, info = ffprobe(url)
        r["ok"], r["res"] = ok, info
        if not ok:
            r["note"] = "ffprobe 无法解析"
    return r


def have_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


FF_RE = re.compile(r"(\d{3,4})x(\d{3,4})")


def ffprobe(url: str, timeout=12):
    """返回 (ok, '宽x高')"""
    import subprocess
    cmd = ["ffprobe", "-v", "error", "-rw_timeout", "8000000",
           "-analyzeduration", "2000000", "-probesize", "1000000",
           "-select_streams", "v:0",
           "-show_entries", "stream=codec_name,width,height",
           "-of", "default=nw=1:nk=1", url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "").strip().replace("\n", " ")
        m = FF_RE.search(out)
        return (p.returncode == 0 and bool(out), m.group(0) if m else "")
    except Exception:
        return (False, "")


# ---------------------------------------------------------------- 打分
def score(item, r):
    s = 100.0 if r["ok"] else 0.0
    if r["kind"] == "hls":
        s += 5
    m = FF_RE.search(r["res"] or "")
    if m:
        s += min(int(m.group(2)) / 1080.0, 2.0) * 10
    s -= min(r["latency_ms"] / 1000.0, 10.0)
    if item["url"].startswith("https"):
        s += 1
    return s


# ---------------------------------------------------------------- 导出
def write_outputs(rows, prefix):
    with open(prefix + ".m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for r in rows:
            f.write(f'#EXTINF:-1 tvg-id="{r["tvg_id"]}" tvg-name="{r["name"]}" '
                    f'tvg-logo="{r["logo"]}" group-title="{r["group"]}",{r["name"]}\n'
                    f'{r["url"]}\n')
    with open(prefix + ".txt", "w", encoding="utf-8") as f:
        last = None
        for r in rows:
            if r["group"] != last:
                f.write(f'{r["group"]},#genre#\n')
                last = r["group"]
            f.write(f'{r["name"]},{r["url"]}\n')
    with open(prefix + ".report.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["频道", "分组", "类型", "分辨率", "延迟ms", "备注", "地址"])
        for r in rows:
            w.writerow([r["name"], r["group"], r["kind"], r["res"],
                        r["latency_ms"], r["note"], r["url"]])


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deep", action="store_true", help="ffprobe 真拉流校验(全部存活源，慢)")
    ap.add_argument("--deep-top", type=int, default=0,
                    help="只对评分最高的 N 个存活源做 ffprobe(适合 CI，默认 0 关闭)")
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--out", default="result")
    ap.add_argument("--sources", help="自定义源列表文件(每行一个 URL)")
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--probe", help="只检测单个 URL")
    a = ap.parse_args()

    global TIMEOUT
    TIMEOUT = a.timeout

    if (a.deep or a.deep_top > 0) and not have_ffprobe():
        print("!! 未找到 ffprobe，已跳过深度校验（装好 ffmpeg 后重跑）\n")
        a.deep = False
        a.deep_top = 0

    if a.probe:
        r = check_stream(a.probe, a.deep)
        print(r)
        return

    sources = SOURCES
    if a.sources:
        with open(a.sources, encoding="utf-8") as f:
            urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        sources = [(f"custom{i}", u) for i, u in enumerate(urls)]

    # 1) 采集
    channels, seen = [], set()
    for tag, url in sources:
        try:
            items = parse_any(fetch_playlist(url))
            print(f"[采集] {tag:<16} {len(items):>5} 条  {url}")
        except Exception as e:
            print(f"[采集] {tag:<16} 失败 {type(e).__name__}: {e}")
            continue
        for it in items:
            u = it["url"].strip()
            if u in seen:
                continue
            seen.add(u)
            it["src"] = tag
            channels.append(it)
    print(f"[采集] 去重后共 {len(channels)} 个候选源\n")
    if not channels:
        sys.exit("没有抓到任何候选源，检查网络/代理或换源地址")

    # 2) 并发校验
    rows, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = {pool.submit(check_stream, c["url"], a.deep): c for c in channels}
        for fu in as_completed(futs):
            c = futs[fu]
            r = fu.result()
            done += 1
            if done % 100 == 0 or done == len(channels):
                print(f"  进度 {done}/{len(channels)}  存活 {len(rows)}  "
                      f"耗时 {time.time() - t0:.0f}s")
            if not r["ok"]:
                continue
            c.update(r)
            c["score"] = score(c, r)
            rows.append(c)

    # 2.5) 优选源深度校验：只验评分最高的 N 个，控制在 CI 预算内
    if a.deep_top > 0 and rows:
        rows.sort(key=lambda x: -x["score"])
        cand, rest = rows[:a.deep_top], rows[a.deep_top:]
        print(f"[深检] ffprobe 复验前 {len(cand)} 个优选源 ...")
        passed, t1 = [], time.time()
        with ThreadPoolExecutor(max_workers=min(16, a.workers)) as pool:
            futs = {pool.submit(ffprobe, c["url"]): c for c in cand}
            for fu in as_completed(futs):
                c = futs[fu]
                ok, info = fu.result()
                if ok:
                    c["res"] = info
                    c["score"] += 20
                    passed.append(c)
        rows = passed + rest
        print(f"[深检] 通过 {len(passed)}/{len(cand)}，耗时 {time.time() - t1:.0f}s")

    # 3) 同名频道限量 + 排序
    by_name = {}
    for r in rows:
        by_name.setdefault(norm_name(r["name"]), []).append(r)
    final = []
    for lst in by_name.values():
        lst.sort(key=lambda x: -x["score"])
        final.extend(lst[:MAX_PER_NAME])
    final.sort(key=lambda x: (x["group"], -x["score"]))

    if not final:
        sys.exit("[中止] 本次无可用源，保留上次结果，不覆盖文件")

    write_outputs(final, a.out)
    print(f"\n[完成] 候选 {len(channels)} -> 存活 {len(rows)} -> 导出 {len(final)} 条")
    print(f"       覆盖频道 {len(by_name)} 个，用时 {time.time() - t0:.0f}s")
    print(f"       输出 {a.out}.m3u / {a.out}.txt / {a.out}.report.csv")


if __name__ == "__main__":
    main()

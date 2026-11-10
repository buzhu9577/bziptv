#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iptv_hunter.py —— 央视频道 / 地方卫视 专用：采集 -> 白名单过滤 -> 去重
                  -> 连通性校验 -> 分辨率筛选(>=1080P) -> 下载测速 -> 排序导出

零第三方依赖，Python 3.8+；装了 ffmpeg/ffprobe 时分辨率判定更准。

常用:
    python iptv_hunter.py                        # 默认: 只要央视+卫视, >=1080P, >=4Mbps
    python iptv_hunter.py --sources sources.txt --workers 32
    python iptv_hunter.py --min-height 2160 --min-mbps 8      # 只要 4K 且 >=8Mbps
    python iptv_hunter.py --all-channels                      # 关闭频道白名单
    python iptv_hunter.py --probe https://xxx/live.m3u8      # 单条全项诊断

产物: result.m3u / result.txt / result.report.csv

说明: 只索引公开可访问的流地址，不代理、不转播、不存储视频内容。
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
TIMEOUT = 8.0
MAX_PER_NAME = 3
CHUNK = 8192

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

SOURCES = [
    ("iptv-org-cn",  "https://iptv-org.github.io/iptv/countries/cn.m3u"),
    ("zbds-ipv4",  "https://live.zbds.top/tv/iptv4.m3u"),
    ("zbds-ipv6",    "https://live.zbds.top/tv/iptv6.m3u"),
    ("zbds-txt",     "https://live.zbds.top/tv/iptv4.txt"),
    ("fanmingming",  "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/iptv.m3u"),
]

# ================================================================ 频道白名单
CCTV_NAMES = {
    "1": "CCTV-1 综合", "2": "CCTV-2 财经", "3": "CCTV-3 综艺",
    "4": "CCTV-4 中文国际", "5": "CCTV-5 体育", "6": "CCTV-6 电影",
    "7": "CCTV-7 国防军事", "8": "CCTV-8 电视剧", "9": "CCTV-9 纪录",
    "10": "CCTV-10 科教", "11": "CCTV-11 戏曲", "12": "CCTV-12 社会与法",
    "13": "CCTV-13 新闻", "14": "CCTV-14 少儿", "15": "CCTV-15 音乐",
    "16": "CCTV-16 奥林匹克", "17": "CCTV-17 农业农村",
    "5+": "CCTV-5+ 体育赛事", "4K": "CCTV-4K 超高清", "8K": "CCTV-8K 超高清",
}

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13,
          "十四": 14, "十五": 15, "十六": 16, "十七": 17}

# 省级卫视 + 常见城市/兵团卫视；key 为输出用的规范名
SATELLITE_ALIASES = {
    "北京卫视": ["北京卫视", "BEIJING"],
    "天津卫视": ["天津卫视", "TIANJIN"],
    "河北卫视": ["河北卫视", "HEBEI"],
    "山西卫视": ["山西卫视", "SHANXI"],
    "内蒙古卫视": ["内蒙古卫视", "INNERMONGOLIA", "NEIMENGGU"],
    "辽宁卫视": ["辽宁卫视", "LIAONING"],
    "吉林卫视": ["吉林卫视", "JILIN"],
    "黑龙江卫视": ["黑龙江卫视", "HEILONGJIANG"],
    "东方卫视": ["东方卫视", "上海东方卫视", "上海卫视", "DRAGONTV", "DRAGONTELEVISION", "SHANGHAI"],
    "江苏卫视": ["江苏卫视", "JIANGSU"],
    "浙江卫视": ["浙江卫视", "ZHEJIANG"],
    "安徽卫视": ["安徽卫视", "ANHUI"],
    "东南卫视": ["东南卫视", "福建东南卫视", "福建卫视", "FUJIAN", "SOUTHEASTTV"],
    "江西卫视": ["江西卫视", "JIANGXI"],
    "山东卫视": ["山东卫视", "SHANDONG"],
    "河南卫视": ["河南卫视", "HENAN"],
    "湖北卫视": ["湖北卫视", "HUBEI"],
    "湖南卫视": ["湖南卫视", "HUNAN"],
    "广东卫视": ["广东卫视", "GUANGDONG"],
    "广西卫视": ["广西卫视", "GUANGXI"],
    "海南卫视": ["海南卫视", "HAINAN"],
    "重庆卫视": ["重庆卫视", "CHONGQING"],
    "四川卫视": ["四川卫视", "SICHUAN"],
    "贵州卫视": ["贵州卫视", "GUIZHOU"],
    "云南卫视": ["云南卫视", "YUNNAN"],
    "西藏卫视": ["西藏卫视", "TIBET", "XIZANG"],
    "陕西卫视": ["陕西卫视", "SHAANXI"],
    "甘肃卫视": ["甘肃卫视", "GANSU"],
    "青海卫视": ["青海卫视", "QINGHAI"],
    "宁夏卫视": ["宁夏卫视", "NINGXIA"],
    "新疆卫视": ["新疆卫视", "XINJIANG"],
    "兵团卫视": ["兵团卫视", "BINGTUAN"],
    "深圳卫视": ["深圳卫视", "SHENZHEN"],
    "厦门卫视": ["厦门卫视", "XIAMEN"],
}

CCTV_ALIAS = {
    "CCTVNEWS": "13", "CCTV财经": "2", "CCTV综合": "1", "CCTV体育": "5",
    "CCTV纪录": "9", "CCTV少儿": "14", "CCTV电影": "6", "CCTV军事": "7",
}

EXCLUDE_RE = re.compile(
    r"(广播|电台|RADIO|轮播|点播|回看|录播|测试|TEST|付费|酒店|HOTEL|素材|DEMO)",
    re.I)


def clean_label(raw: str) -> str:
    """去掉 [Geo-blocked]、(Not 24/7) 之类标注"""
    s = re.sub(r"\[[^\]]*\]", "", raw or "")
    s = re.sub(r"\([^)]*\)", "", s)
    return s.strip()


def classify(raw: str):
    """返回 (规范频道名, 分组, tvg-id)；不在白名单里返回 None"""
    s = clean_label(raw)
    if not s or EXCLUDE_RE.search(s):
        return None
    up = s.upper().replace(" ", "").replace("　", "").replace("－", "-")

    # --- 央视 ---
    key = None
    if re.search(r"CCTV-?(4K|8K|UHD)", up):
        key = "8K" if "8K" in up else "4K"
    else:
        m = re.search(r"CCTV-?(\d{1,2})(\+)?", up)
        if m:
            key = m.group(1).lstrip("0") + ("+" if m.group(2) else "")
        else:
            m = re.search(r"央视([一二三四五六七八九十]+|\d{1,2})", s)
            if m:
                v = m.group(1)
                key = str(CN_NUM.get(v, v))
            else:
                for alias, num in CCTV_ALIAS.items():
                    if alias.upper() in up:
                        key = num
                        break
    if key:
        name = CCTV_NAMES.get(key)
        if name:
            return (name, "央视", "CCTV" + key)
        return None

    # --- 地方卫视（中文别名 / 英文台名）---
    has_tv = any(t in up for t in ("TV", "SATELLITE", "TELEVISION", "卫视"))
    for canon, aliases in SATELLITE_ALIASES.items():
        for a in aliases:
            if a in up and (not a.isascii() or has_tv):
                return (canon, "地方卫视", canon)
    return None


# ================================================================ 解析
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
            cur = {"name": tail.strip() or attrs.get("tvg-name", ""),
                   "tvg_id": attrs.get("tvg-id", ""),
                   "logo": attrs.get("tvg-logo", ""),
                   "group": attrs.get("group-title", "") or "未分组"}
        elif line.startswith("#"):
            continue
        elif cur is not None:
            cur["url"] = line
            out.append(cur)
            cur = None
    return [c for c in out if c.get("url")]


def parse_txt(text: str):
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


# ================================================================ HTTP
def http_get(url: str, timeout=None, rng=None, limit=CHUNK, tries=3):
    h = {"User-Agent": UA, "Accept": "*/*", "Connection": "close"}
    if rng:
        h["Range"] = f"bytes=0-{rng - 1}"
    req = urllib.request.Request(url, headers=h)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout or TIMEOUT, context=SSL_CTX) as r:
                return r.status, dict(r.headers), r.read(limit) or b"", r.geturl()
        except urllib.error.HTTPError:
            raise
        except Exception:
            if attempt + 1 == tries:
                raise
            time.sleep(0.8 * (attempt + 1))


def get_text(url: str, limit=65536) -> str:
    _, _, body, _ = http_get(url, limit=limit)
    return body.decode("utf-8", "ignore")


def fetch_playlist(url: str) -> str:
    return get_text(url, 16 * 1024 * 1024)


def have_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


def ffprobe(url: str, timeout=12):
    """返回 (ok, width, height, codec)"""
    import subprocess
    cmd = ["ffprobe", "-v", "error", "-rw_timeout", "8000000",
           "-analyzeduration", "2000000", "-probesize", "1000000",
           "-select_streams", "v:0",
           "-show_entries", "stream=codec_name,width,height",
           "-of", "default=nw=1:nk=1", url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "").strip().split()
        if p.returncode != 0 or not out:
            return (False, 0, 0, "")
        nums = [int(x) for x in out if x.isdigit()]
        w = next((x for x in nums if 100 <= x <= 8000), 0)
        h = next((x for x in nums[nums.index(w) + 1:] if 100 <= x <= 8000), 0) if w else 0
        codec = next((x for x in out if re.match(r"^[a-z0-9_]+$", x) and not x.isdigit()), "")
        return (True, w, h, codec)
    except Exception:
        return (False, 0, 0, "")


# ================================================================ 连通性校验
def check_stream(url: str):
    """HTTP 可达 + m3u8 分片可下载 / TS 同步字节"""
    r = {"ok": False, "latency_ms": 0, "kind": "", "note": ""}
    if url.startswith(("rtp://", "udp://", "rtsp://")):
        r["note"] = "组播/RTP 需内网，跳过公网校验"
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
        seg = next((l.strip() for l in head.decode("utf-8", "ignore").splitlines()
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
    return r


# ================================================================ 分辨率 + 测速
RES_RE = re.compile(r"RESOLUTION=(\d{3,4})[xX](\d{3,4})")


def media_segment(url: str):
    """
    解析 HLS：主播放列表取分辨率最高的变体 -> 媒体播放列表 -> 取一个分片。
    返回 (高度提示, 可下载的分片地址)；TS 直连则返回 (0, 原 URL)。
    """
    try:
        txt = get_text(url, 65536)
    except Exception:
        return (0, url)
    if "#EXTM3U" not in txt:          # TS 直连 / 非播放列表，直接对原 URL 测速
        return (0, url)

    hint, base = 0, url
    if "#EXT-X-STREAM-INF" in txt:
        best, best_area = None, -1
        for tag, uri in re.findall(r"#EXT-X-STREAM-INF:([^\n]*)\n([^\n]+)", txt):
            m = RES_RE.search(tag)
            area = int(m.group(1)) * int(m.group(2)) if m else 0
            if area > best_area:
                best_area, best, hint = area, uri.strip(), (int(m.group(2)) if m else 0)
        if best:
            base = urljoin(url, best)
            try:
                txt = get_text(base, 65536)
            except Exception:
                return (hint, url)

    segs = [l.strip() for l in txt.splitlines()
            if l.strip() and not l.startswith("#")]
    if not segs:
        return (hint, url)
    seg = segs[len(segs) // 2] if len(segs) > 2 else segs[-1]
    return (hint, urljoin(base, seg))


def probe_height(url: str, allow_ffprobe=True):
    """返回 (height, width, codec, 来源说明)"""
    hint, _ = media_segment(url)
    if hint:
        return (hint, int(hint * 16 / 9), "", "m3u8 RESOLUTION")
    if allow_ffprobe:
        ok, w, h, codec = ffprobe(url)
        if ok and h:
            return (h, w, codec, "ffprobe")
    return (0, 0, "", "无法判定")


def speed_test(url: str, seconds=5.0, max_bytes=4 * 1024 * 1024,
               min_bytes=256 * 1024):
    """
    实际下载分片测速。采样窗口 >= min_bytes 才算有效，避免 CDN 突发速率虚高。
    """
    r = {"speed_mbps": 0.0, "ttfb_ms": 0, "sample_kb": 0,
         "valid": False, "grade": "未测", "note": ""}
    _, seg_url = media_segment(url)
    t0 = time.time()
    total, ttfb = 0, 0
    try:
        req = urllib.request.Request(seg_url, headers={
            "User-Agent": UA, "Accept": "*/*", "Connection": "close",
            "Range": f"bytes=0-{max_bytes - 1}"})
        with urllib.request.urlopen(req, timeout=max(seconds + 2, TIMEOUT),
                                    context=SSL_CTX) as resp:
            while total < max_bytes and (time.time() - t0) < seconds:
                chunk = resp.read(65536)
                if not chunk:
                    break
                if not ttfb:
                    ttfb = int((time.time() - t0) * 1000)
                total += len(chunk)
    except Exception as e:
        r["note"] = f"测速 {type(e).__name__}"
        return r

    elapsed = max(time.time() - t0, 0.001)
    r["speed_mbps"] = round(total * 8 / elapsed / 1_000_000, 2)
    r["ttfb_ms"] = ttfb
    r["sample_kb"] = round(total / 1024)
    r["valid"] = total >= min_bytes
    if not r["valid"]:
        r["grade"] = "样本不足"
    elif r["speed_mbps"] >= 10:
        r["grade"] = "S"
    elif r["speed_mbps"] >= 6:
        r["grade"] = "A"
    elif r["speed_mbps"] >= 4:
        r["grade"] = "B"
    else:
        r["grade"] = "C"
    return r


def speed_test_multi(url: str, seconds, max_bytes, min_bytes, samples=2):
    """多次采样取最低值（保守），过滤 CDN 突发速率虚高；同时标记波动"""
    rs = [speed_test(url, seconds, max_bytes, min_bytes) for _ in range(max(1, samples))]
    valid = [r for r in rs if r["valid"]]
    pick = min(valid, key=lambda x: x["speed_mbps"]) if valid else rs[0]
    pick["samples"] = "/".join(f'{r["speed_mbps"]:.1f}' for r in rs)
    if len(valid) >= 2:
        lo = min(r["speed_mbps"] for r in valid)
        hi = max(r["speed_mbps"] for r in valid)
        if lo > 0 and hi / lo > 2.0:
            pick["note"] = (pick["note"] + " " if pick["note"] else "") + "波动大"
    return pick


def score(r):
    s = 100.0
    if r["height"]:
        s += min(r["height"] / 1080.0, 2.5) * 10
    else:
        s -= 15                      # 未知分辨率排到已判定源后面
    s += min(r["speed_mbps"], 20.0) * 2
    s -= min(r["ttfb_ms"] / 500.0, 8.0)
    s -= min(r["latency_ms"] / 1000.0, 8.0)
    if r["url"].startswith("https"):
        s += 1
    return s


# ================================================================ 导出
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
        w.writerow(["频道", "分组", "分辨率", "编码", "测速Mbps", "各次采样Mbps",
                    "TTFBms", "采样KB", "等级", "连通延迟ms", "类型", "备注", "地址"])
        for r in rows:
            w.writerow([r["name"], r["group"], f'{r["height"]}P' if r["height"] else "未知",
                        r["codec"], r["speed_mbps"], r.get("samples", "-"),
                        r["ttfb_ms"], r["sample_kb"], r["grade"],
                        r["latency_ms"], r["kind"], r["note"], r["url"]])


# ================================================================ 主流程
def diagnose(url):
    print("连通性:", check_stream(url))
    print("分辨率:", probe_height(url, have_ffprobe()))
    print("测速  :", speed_test(url))
    print("频道匹配:", classify(url))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-channels", action="store_true", help="关闭央视/卫视白名单")
    ap.add_argument("--min-height", type=int, default=1080, help="最低视频高度，默认 1080")
    ap.add_argument("--min-mbps", type=float, default=4.0, help="最低下载速率，默认 4 Mbps")
    ap.add_argument("--speed-seconds", type=float, default=5.0, help="单次测速采样时长上限")
    ap.add_argument("--speed-samples", type=int, default=2, help="测速次数，取最低值，默认 2")
    ap.add_argument("--speed-min-kb", type=int, default=256, help="有效样本最小字节(KB)")
    ap.add_argument("--no-speed", action="store_true", help="跳过测速(只按分辨率筛)")
    ap.add_argument("--allow-unknown", action="store_true",
                    help="保留无法判定分辨率的源；无 ffprobe 时自动开启")
    ap.add_argument("--strict-resolution", action="store_true",
                    help="严格模式：无法判定分辨率的源一律剔除(即使没有 ffprobe)")
    ap.add_argument("--deep-top", type=int, default=0, help="ffprobe 候选上限，0=全部")
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--out", default="result")
    ap.add_argument("--sources", help="自定义源列表文件(每行一个 URL)")
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--probe", help="只诊断单个 URL")
    a = ap.parse_args()

    global TIMEOUT
    TIMEOUT = a.timeout

    if a.probe:
        diagnose(a.probe)
        return

    ff = have_ffprobe()
    allow_unknown = (not a.strict_resolution) and (a.allow_unknown or not ff)
    if not ff:
        print("!! 本机无 ffprobe：分辨率只能靠 m3u8 RESOLUTION 标签判定"
              "（装 ffmpeg 后可自动用 ffprobe 判定）\n")

    sources = SOURCES
    if a.sources:
        with open(a.sources, encoding="utf-8") as f:
            urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        sources = [(f"custom{i}", u) for i, u in enumerate(urls)]

    # 1) 采集 + 频道白名单 + URL 去重
    channels, seen = [], set()
    for tag, url in sources:
        try:
            items = parse_any(fetch_playlist(url))
            print(f"[采集] {tag:<16} {len(items):>5} 条  {url}")
        except Exception as e:
            print(f"[采集] {tag:<16} 失败 {type(e).__name__}: {e}")
            continue
        kept = 0
        for it in items:
            u = it["url"].strip()
            if u in seen:
                continue
            cls = classify(it["name"])
            if not a.all_channels and not cls:
                continue
            seen.add(u)
            if cls:
                it["name"], it["group"], it["tvg_id"] = cls
            else:
                it["name"], it["group"] = clean_label(it["name"]), "其他"
            it["src"] = tag
            channels.append(it)
            kept += 1
        print(f"         └ 命中目标频道 {kept} 条")
    print(f"[采集] 白名单+去重后共 {len(channels)} 个候选源\n")
    if not channels:
        sys.exit("[中止] 没有命中的央视频道/地方卫视源，检查源清单或频道别名表")

    # 2) 连通性
    alive, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = {pool.submit(check_stream, c["url"]): c for c in channels}
        for fu in as_completed(futs):
            c = futs[fu]
            r = fu.result()
            done += 1
            if done % 100 == 0 or done == len(channels):
                print(f"  [连通] {done}/{len(channels)} 存活 {len(alive)}  "
                      f"耗时 {time.time() - t0:.0f}s")
            if r["ok"]:
                c.update(r)
                alive.append(c)
    print(f"[连通] 存活 {len(alive)} 条\n")

    # 3) 分辨率（先用 m3u8 标签，拿不到再 ffprobe）
    cand = alive[:a.deep_top] if a.deep_top > 0 else alive
    for r in alive:
        r.update(height=0, width=0, codec="", res_src="")
    with ThreadPoolExecutor(max_workers=min(16, a.workers)) as pool:
        futs = {pool.submit(probe_height, c["url"], ff): c for c in cand}
        for fu in as_completed(futs):
            c = futs[fu]
            h, w, codec, src = fu.result()
            c.update(height=h, width=w, codec=codec, res_src=src)

    qualified = [c for c in alive
                 if (c["height"] and c["height"] >= a.min_height)
                 or (allow_unknown and not c["height"])]
    print(f"[画质] >= {a.min_height}P 或豁免: {len(qualified)} 条 "
          f"(已判定 {sum(1 for c in qualified if c['height'])}, "
          f"未判定 {sum(1 for c in qualified if not c['height'])})\n")

    # 4) 测速
    final_rows = []
    if a.no_speed:
        final_rows = qualified
        for c in final_rows:
            c.update(speed_mbps=0.0, ttfb_ms=0, sample_kb=0,
                     grade="未测", samples="-")
    else:
        t1 = time.time()
        with ThreadPoolExecutor(max_workers=min(24, a.workers)) as pool:
            futs = {pool.submit(speed_test_multi, c["url"], a.speed_seconds,
                                4 * 1024 * 1024, a.speed_min_kb * 1024,
                                a.speed_samples): c for c in qualified}
            for fu in as_completed(futs):
                c = futs[fu]
                s = fu.result()
                c.update(s)
                if s["valid"] and s["speed_mbps"] >= a.min_mbps:
                    final_rows.append(c)
        print(f"[测速] 通过 {a.min_mbps} Mbps: {len(final_rows)}/{len(qualified)} 条，"
              f"耗时 {time.time() - t1:.0f}s\n")

    # 5) 排序 + 同名限量
    for c in final_rows:
        c["score"] = score(c)
    by_name = {}
    for c in final_rows:
        by_name.setdefault(c["name"], []).append(c)
    final = []
    for lst in by_name.values():
        lst.sort(key=lambda x: -x["score"])
        final.extend(lst[:MAX_PER_NAME])
    final.sort(key=lambda x: (x["group"], -x["score"]))

    if not final:
        sys.exit("[中止] 无满足条件的源，保留上次结果，不覆盖文件")

    write_outputs(final, a.out)
    print(f"[完成] 候选{len(channels)} → 连通{len(alive)} → 画质{len(qualified)} "
          f"→ 最终{len(final)} 条 / 频道 {len(by_name)} 个")
    print("       等级分布: " + ", ".join(
        f"{g}={sum(1 for c in final if c['grade'] == g)}"
        for g in ("S", "A", "B", "未测")))
    print(f"       输出 {a.out}.m3u / {a.out}.txt / {a.out}.report.csv")


if __name__ == "__main__":
    main()

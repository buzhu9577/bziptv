# IPTV 源自动更新（央视 / 地方卫视 · 1080P+）

每天自动抓取公开 m3u 列表，只保留**央视频道 + 地方卫视**，筛掉 1080P 以下和测速不达标的源，
把结果提交回仓库，播放器订阅一个固定 URL 即可。

## 仓库文件

| 文件 | 作用 |
|---|---|
| `iptv_hunter.py` | 白名单 / 去重 / 连通校验 / 分辨率 / 测速 / 导出，纯标准库、零依赖 |
| `sources.txt` | 上游源清单，改这个就能换源，不用动代码 |
| `.github/workflows/iptv.yml` | 定时任务 + 手动触发 + 提交结果 |
| `result.m3u` / `result.txt` / `result.report.csv` | 首次运行后自动生成并提交 |

## 筛选与测速标准

流程：**频道白名单 → 连通性 → 分辨率 → 测速 → 排序**，越靠前越便宜，避免把带宽浪费在无效源上。

### 1. 频道白名单（默认开启）

| 分组 | 覆盖 |
|---|---|
| 央视 | CCTV-1~17、CCTV-5+、CCTV-4K/8K；兼容 `央视一套`、`央视十三套`、`CCTV News`、`CCTV-1 HD` 等别名 |
| 地方卫视 | 31 个省级卫视 + 兵团 / 深圳 / 厦门卫视；兼容英文台名 `Hunan TV`、`Beijing Satellite TV`、`Dragon TV`、`Xinjiang TV` |

命中后统一改成规范名（如 `CCTV-1 综合`、`湖南卫视`），并写好 `tvg-id` 方便匹配 EPG；
`广播 / 电台 / 轮播 / 回看 / 测试 / 付费 / 酒店` 等条目直接排除。加频道改脚本顶部的
`CCTV_NAMES` / `SATELLITE_ALIASES` 两个表即可。`--all-channels` 可临时关闭白名单。

### 2. 分辨率 ≥ 1080P

判定优先级：m3u8 主播放列表的 `RESOLUTION=1920x1080` 标签（取最高变体）→ `ffprobe` 实测视频轨高度。
两者都拿不到时标为「未知」：`--strict-resolution`（CI 默认开启）直接剔除；本机没装 ffprobe 时
自动放宽为保留但排在已判定源后面。

> 想真正严格筛 1080P，**必须装 ffmpeg/ffprobe**。GitHub 的 ubuntu runner 自带，workflow 里也做了缺失时自动安装。

### 3. 测速标准（我的取值，可调）

| 指标 | 定义 | 门槛 |
|---|---|---|
| 下载速率 | 实际下载 HLS 分片 / TS 流，采样最长 5 秒或 4 MB，`bytes×8/耗时` | **≥ 4 Mbps**（H.264 1080p 常见 8 Mbps，H.265 约 4~6 Mbps） |
| 有效样本 | 单次至少下满 256 KB，否则标「样本不足」不参与排名 | 过滤 CDN 首包突发造成的虚高 |
| 采样次数 | 默认 2 次，**取较低值**作为最终速率 | 避免单次突发虚高 |
| 波动 | 两次比值 > 2× 时备注「波动大」 | 提示链路不稳 |
| TTFB | 发起请求到收到第一个字节 | 只记录并参与打分，不设硬门槛 |
| 等级 | S ≥10、A 6~10、B 4~6、C <4 Mbps | C 级淘汰 |

注意：这里测的是**可用下载带宽**，不是视频编码码率 —— 目的是判断「会不会卡」；画质由第 2 步的分辨率判定，两者独立。

### 4. 常用参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--min-height` | 1080 | 最低视频高度，改 2160 就只要 4K |
| `--min-mbps` | 4 | 最低下载速率 |
| `--speed-seconds` / `--speed-samples` | 5 / 2 | 单次采样时长 / 采样次数 |
| `--speed-min-kb` | 256 | 有效样本下限 |
| `--strict-resolution` | 关（CI 开） | 剔除所有分辨率未知的源 |
| `--all-channels` | 关 | 不做频道白名单 |
| `--no-speed` | 关 | 只按分辨率筛，跳过测速 |
| `--deep-top` | 0 | ffprobe 候选上限，0=全部 |

`result.report.csv` 会输出分辨率、编码、两次采样值、最终 Mbps、TTFB、采样量、等级、连通延迟，便于复核。

## 一、上传代码

### 方式 A：网页上传（最省事）

1. 登录 GitHub → 右上角 `+` → **New repository**
2. 仓库名随意（如 `iptv-list`），**必须选 Public**，其余都不勾，点 Create repository
3. 在新仓库页面点 **uploading an existing file**（或 `Add file` → `Upload files`）
4. 把 `iptv_hunter.py`、`sources.txt`、`.gitignore`、`README.md` 拖进去
5. 再点 `Add file` → `Create new file`，文件名填 `.github/workflows/iptv.yml`，把 workflow 内容粘进去
   （网页方式必须这样手动建路径，直接拖拽带文件夹的文件可能丢层级）
6. Commit changes

### 方式 B：Git 命令行（推荐，以后改代码方便）

先在 GitHub 网页建好空的 Public 仓库，然后在本地项目目录执行：

```bash
cd D:/workbuddy/2026-11-09-13-02-55      # 换成你自己的路径
git init -b main
git add iptv_hunter.py sources.txt .gitignore README.md .github/workflows/iptv.yml
git commit -m "init: iptv auto refresh"
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

Windows 首次 push 会弹 GitHub 登录窗口，用浏览器授权即可。若命令行要求密码：
`Settings` → `Developer settings` → `Personal access tokens (classic)` → 勾 `repo` → 生成 token，
把 token 当密码粘贴（不是账号密码）。

### 方式 C：GitHub CLI（一行）

```bash
gh auth login                 # 浏览器授权一次
gh repo create iptv-list --public --source=. --remote=origin --push
```

## 二、开权限（关键，漏了会报 Permission denied）

仓库 → **Settings** → **Actions** → **General** → 下拉到 **Workflow permissions**
→ 选 **Read and write permissions** → 勾 **Allow GitHub Actions to create and approve pull requests**
→ **Save**。

workflow 里已声明 `permissions: contents: write`，但组织级/仓库级默认设置仍可能覆盖，两边都确认一下。

## 三、手动跑一次验证（含「找不到按钮」排查）

### 3.1 按钮到底在哪

仓库顶部 **Actions** 标签 → 左侧栏 `All workflows` 下面会列出工作流，名字是 `iptv-refresh`（取自 yml 第一行的 `name:`）
→ **点它** → 页面右上方出现 **Run workflow ▾** 下拉按钮 → 点开后有个小面板，
可填 `deep_top`（默认 300）和 `min_mbps`（默认 4）
→ 再点面板里绿色的 **Run workflow** 才算真正启动。

注意：必须**先点左侧的工作流名**，直接在 Actions 首页是看不到 Run workflow 按钮的。页面上有
`This workflow has a workflow_dispatch event trigger.` 这行提示，说明手动触发已生效。

### 3.2 左侧找不到 iptv-refresh / 没有 Run workflow

按优先级逐项排查：

| 检查项 | 怎么做 |
|---|---|
| ① 文件路径精确 | 仓库 Code 页看文件树：必须是 `.github/workflows/iptv.yml`。常见错：传成了根目录 `iptv.yml`、`.github/workflow/`（少 s）、`.Github`（大写）、`iptv.yml.txt`。网页上传时要在**文件名框里输入完整路径** `.github/workflows/iptv.yml` 才会自动建目录 |
| ② 在默认分支上 | 看 Code 页左上分支下拉是不是 `main`。推到别的分支的 workflow 不会出现在 Actions 列表里，需合并到默认分支 |
| ③ Actions 没启用 | `Settings → Actions → General → Actions permissions` 选 **Allow all actions and reusable workflows**；如果是 fork 出来的仓库，默认禁用，页面顶部有黄条提示要手动点启用 |
| ④ 账号权限不足 | 只有仓库 owner 或有 write 权限的协作者能看到 Run workflow；用只读账号/未登录时按钮不显示 |
| ⑤ 刚上传没生效 | 新建 workflow 一般 1~2 分钟才出现，硬刷新 `Ctrl+F5`，或换无痕窗口再开 |
| ⑥ YAML 有语法错 | Actions 页若出现红字 `This workflow file has an error`，点进去看行号；最常见是缩进混用了 Tab |
| ⑦ 工作流被停用 | 60 天无活动的仓库会被停用，页面显示 `This workflow is disabled` + Enable workflow 按钮，点一下即可 |

用命令行直接问 GitHub 有没有识别到（最准）：

```bash
gh workflow list
gh api repos/<用户名>/<仓库名>/actions/workflows
```

列表为空 = 路径/分支不对；`state: disabled_manually` = 被停用；`state: active` 才正常。

### 3.3 完全绕开按钮的三种触发方式

**A. 改一下 sources.txt 再 push（最简单，已配好）**

workflow 里加了 `on: push` + `paths` 过滤，只要这几个文件有变动就会跑；机器人自己提交的
`result.*` 不在 paths 里，不会死循环。

```bash
echo "# refresh" >> sources.txt
git add sources.txt && git commit -m "trigger refresh" && git push
```

**B. GitHub CLI**

```bash
gh workflow run iptv.yml --ref main -f deep_top=300 -f min_mbps=4
gh run watch        # 实时看日志
```

**C. REST API**（适合脚本化）

```bash
curl -X POST \
  -H "Authorization: Bearer <token>" \
  -H "Accept: application/vnd.github+json" \
  -d '{"ref":"main","inputs":{"deep_top":"300","min_mbps":"4"}}' \
  https://api.github.com/repos/<用户名>/<仓库名>/actions/workflows/iptv.yml/dispatches
```

返回 204 即触发成功。实在不想折腾，也可以什么都不做，等北京时间次日 03:00 定时跑。

### 3.4 成功的标志

首次跑约 5~15 分钟。点进这次运行可以看实时日志；成功后：

- 步骤摘要里有频道数和文件体积
- 仓库根目录出现 `result.m3u`、`result.txt`、`result.report.csv`
- 之后每天 UTC 19:00（**北京时间次日 03:00**）自动重跑

> GitHub 的 schedule 只认 UTC；高峰期可能延迟几分钟到几十分钟，不要按整点卡。
> 60 天无任何提交的仓库会被停用定时任务——本 workflow 每天自己提交，天然不会被停。

## 四、订阅地址

```
https://raw.githubusercontent.com/<用户名>/<仓库名>/main/result.m3u
```

把它填进 VLC、TiviMate、DIYP 影音、Kodi 的「网络播放列表」即可，以后列表更新播放器自动生效。

国内 raw 域名不稳时，任选其一：

```
https://cdn.jsdelivr.net/gh/<用户名>/<仓库名>@main/result.m3u
https://gh-proxy.com/https://raw.githubusercontent.com/<用户名>/<仓库名>/main/result.m3u
```

jsDelivr 有缓存（最长约 24h），想要实时就用 raw + 代理。`result.txt` 给 DIYP / 影视仓类应用，
`result.report.csv` 用来看每个源的分辨率、两次测速采样、TTFB、等级和失败原因。

## 五、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `remote: Permission to ... denied` | Workflow permissions 没开 write，回到第二步 |
| `内容无变化，跳过提交` | 本次结果和上次一致，属正常 |
| `[中止] 无满足条件的源，保留上次结果` | 白名单 / 1080P / 测速三重筛选后为空；脚本会保护旧文件不被清空 |
| 结果条数很少 | CI 开了 `--strict-resolution`，而 ffprobe 对部分源被地域限制判不出高度。去掉该参数，或把 `--min-mbps` 降到 2 |
| 报告里大量「未知」分辨率 | 本机没装 ffprobe，且 m3u8 没写 `RESOLUTION` 标签；装 ffmpeg 后重跑 |
| 测速值高达几十上百 Mbps | 测的是 CDN 下载带宽不是视频码率，属正常；已用双采样取低值抑制突发 |
| 很多「样本不足」 | 分片太小或提前断流；调大 `--speed-seconds`、调小 `--speed-min-kb` |
| 跑满 40 分钟被杀 | 减少 `sources.txt` 条目、调低 `--workers`、把 `deep_top` 设为 0、或 `--no-speed` |
| 存活率明显偏低 | runner 在美国，部分国内源有地域限制；换国内可直连的上游或自建源 |
| 手动触发找不到按钮 | workflow 文件必须在默认分支根目录 `.github/workflows/` 下才显示 |
| 私有仓库额度 | 公开仓库 Actions 免费；私有仓库 2000 分钟/月，每天跑也够用 |

## 六、本地调试

```bash
# 装 ffmpeg（Windows: winget install Gyan.FFmpeg；Linux: apt install ffmpeg）
python iptv_hunter.py --sources sources.txt --workers 32 --timeout 8 --out result
python iptv_hunter.py --strict-resolution --min-mbps 4       # 与 CI 完全一致
python iptv_hunter.py --probe https://xxx/live.m3u8          # 单条全项诊断
python iptv_hunter.py --min-height 2160 --min-mbps 8         # 只要 4K 且 >=8Mbps
python iptv_hunter.py --all-channels --no-speed                # 排查白名单是否漏频道
```

没装 ffprobe 时脚本自动放宽分辨率筛选（保留「未知」但排到最后）并打印提示；
CI 的 ubuntu runner 装了 ffprobe 且显式带 `--strict-resolution`，会严格执行 1080P 门槛。

## 合规

只索引公开可访问的流地址，不存储、不转播视频内容；请勿加入需破解鉴权的付费频道或用于商业分发。

# IPTV 源自动更新（GitHub Actions）

每天自动抓取公开 m3u 列表、验证可用性、把结果提交回仓库，播放器订阅一个固定 URL 即可。

## 仓库文件

| 文件 | 作用 |
|---|---|
| `iptv_hunter.py` | 采集 / 去重 / 校验 / 导出，纯标准库、零依赖 |
| `sources.txt` | 上游源清单，改这个就能换源，不用动代码 |
| `.github/workflows/iptv.yml` | 定时任务 + 手动触发 + 提交结果 |
| `result.m3u` / `result.txt` / `result.report.csv` | 首次运行后自动生成并提交 |

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

## 三、手动跑一次验证

仓库 → **Actions** → 左侧 `iptv-refresh` → 右侧 **Run workflow** → 填 `deep_top`（默认 300）→ **Run workflow**。

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
`result.report.csv` 用来看每个源的延迟、分辨率和失败原因。

## 五、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `remote: Permission to ... denied` | Workflow permissions 没开 write，回到第二步 |
| `内容无变化，跳过提交` | 本次结果和上次一致，属正常 |
| `[中止] 无可用源，保留上次结果` | 上游全挂或网络受限；脚本会保护旧文件不被清空 |
| 跑满 40 分钟被杀 | 减少 `sources.txt` 条目、调低 `--workers`、把 `deep_top` 设为 0 |
| 存活率明显偏低 | runner 在美国，部分国内源有地域限制；换国内可直连的上游或自建源 |
| 手动触发找不到按钮 | workflow 文件必须在默认分支根目录 `.github/workflows/` 下才显示 |
| 私有仓库额度 | 公开仓库 Actions 免费；私有仓库 2000 分钟/月，每天跑也够用 |

## 六、本地调试

```bash
python iptv_hunter.py --sources sources.txt --workers 32 --timeout 8 --out result
python iptv_hunter.py --probe https://xxx/live.m3u8     # 单条诊断
python iptv_hunter.py --deep --workers 40                # 需要本机装了 ffmpeg
```

本机没装 `ffprobe` 时脚本会自动跳过深度校验并打印提示，不会误删源。

## 合规

只索引公开可访问的流地址，不存储、不转播视频内容；请勿加入需破解鉴权的付费频道或用于商业分发。

# 固体力学博士招聘雷达

面向 2027 届固体力学博士的个人招聘信息看板，地域优先级为上海、合肥、江苏、浙江。首页采用“宁缺毋滥”规则：只展示招聘单位官网能够确认当前仍在招，并且专业要求明确包含固体力学、力学或工程力学的具体岗位或正式招聘公告。

仅有招聘首页、人才政策页、业务方向看似匹配、专业要求不明确、已经截止或官网暂时无法验证的单位不会出现在首页。这些单位仍保留在后台监测清单中，官网以后出现合格岗位时再加入。

每条岗位卡片还包含可展开的“职员视角”，展示工作环境、压力等级、常见节奏、公开样本充足度、可信度和面试核实问题。社区讨论只用于口碑归纳，投递按钮仍只连接招聘单位官网。

在线访问：<https://zhouxutongshu.github.io/solid-mechanics-jobs/>

## 直接查看

双击 `index.html` 即可打开。由于岗位数据通过普通 JavaScript 文件加载，不需要安装 Node.js，也不需要启动后端服务器。

若浏览器对本地文件有限制，可在项目目录运行：

```bash
python3 -m http.server 8000
```

然后访问 `http://localhost:8000`。

## 每日更新机制

更新器位于 `scripts/update_jobs.py`，只使用 Python 标准库。每次运行会：

1. 并行访问 `data/job_candidates.json` 中的招聘单位官网岗位页；
2. 同时核对具体岗位/招聘公告、力学专业要求、当前有效信号和截止日期；
3. 任一证据缺失、岗位下架或截止时，将该记录从首页数据中移除；
4. 扫描 `data/watch_sources.json` 中的单位自有招聘入口，寻找新的具体岗位页；
5. 自动发现的岗位也必须在单位官网详情页同时通过专业与在招核验，才可进入首页；
6. 重建 `data/jobs.js` 和便于二次开发的 `data/jobs.json`。

手动更新：

```bash
python3 scripts/update_jobs.py
```

只检查、不写入：

```bash
python3 scripts/update_jobs.py --dry-run
```

更新器只允许从招聘单位自有域名发现新链接，并拒绝官网中跳向综合招聘平台或通用招聘 SaaS 的链接。部分官网依赖登录、验证码或前端动态接口；如果当天网络整体异常，网络保护会保留上一版数据；如果只是某个岗位无法继续验证，该岗位会暂时离开首页并继续接受后台监测。

## 固定每天 08:30 更新

### 方案一：GitHub Pages，推荐

项目已经包含两个工作流：

- `.github/workflows/update-jobs.yml`：每天北京时间 08:23 启动核验，通常在 08:30 前完成；09:07 设一次幂等备用检查；
- `.github/workflows/deploy-pages.yml`：数据变化后自动发布网页。

使用步骤：

1. 将本目录作为一个 GitHub 仓库推送到 `main` 分支；
2. 在仓库 `Settings → Pages → Source` 中选择 `GitHub Actions`；
3. 在 `Actions` 页面手动运行一次 `Deploy dashboard to GitHub Pages`；
4. 确认仓库 `Settings → Actions → General → Workflow permissions` 允许 `Read and write permissions`。

GitHub 官方说明定时任务可能延迟，负载很高时个别任务甚至可能被丢弃。因此这里采用“主任务 + 当日备用任务”；备用任务发现当天已经成功更新时会直接退出，不重复改写数据。更新器还设有网络保护：若可连接官网数量异常过低，会保留上一版数据并等待备用任务重试。

### 方案二：macOS 本地定时

运行一次：

```bash
zsh scripts/install_macos_schedule.sh
```

脚本会安装一个 `launchd` 任务，每天本地时间 08:30 运行更新器。电脑在该时间需要开机并联网；运行日志保存在 `logs/`。

若要移除任务：

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.zhouxutong.solid-mechanics-jobs.plist"
```

## 增加或修改岗位候选

人工核验过的候选岗位位于 `data/job_candidates.json`；`data/jobs.js` 和 `data/jobs.json` 是更新器根据当天官网证据生成的首页数据，不应直接手工维护。每条候选记录的关键字段为：

```json
{
  "id": "唯一标识",
  "company": "单位名称",
  "employerType": "私营企业",
  "role": "岗位名称与方向",
  "city": "上海",
  "salaryMin": 30,
  "salaryMax": 45,
  "salaryText": "30–45 万/年",
  "match": "S",
  "status": "open",
  "keywords": ["有限元", "流固耦合"],
  "reputation": "工作环境的保守描述",
  "url": "https://单位自有招聘域名.example/job",
  "deadline": "2026-10-30",
  "deadlineStatus": "dated",
  "majorEvidence": "官网专业要求明确包含固体力学",
  "officialEvidence": "官网列明具体岗位、人数或直接应聘方式",
  "evidenceAll": ["岗位标题", "固体力学", "投递截止"]
}
```

`evidenceAll` 中的每个证据片段都必须继续出现在招聘单位官网响应中，否则候选岗位不会进入首页。`deadlineStatus` 可为 `dated`（官网明确日期）、`rolling`（官网写明招满即止/长期有效）或 `not-published`（官网未公布）。首页状态统一为 `open`；已截止和仅监测单位不发布到首页。

匹配度可选 `S`、`A`、`B`。没有当前合格岗位但值得追踪的单位只加入 `data/watch_sources.json`，不要放进候选清单。旧版人工官网清单保留在 `data/official_links.json` 供复核，但不直接决定首页展示。

## 维护职员视角

职员评价数据单独保存在 `data/employee_reviews.js`，以岗位 `id` 对应主数据。这样可以保证匿名社区信息不会与招聘单位官网信息混在一起。每条记录包含：

- `pressure`：高、较高、中等、较低或待核验；
- `environment`：工作环境的保守归纳；
- `rhythm`：常见项目节奏；
- `summary`：不逐字复制原帖的员工反馈综合；
- `question`：建议在面试中向直属团队核实的问题；
- `sample`、`confidence`、`basis`：样本量、可信度和依据类型；
- `sources`：只显示平台名称，不生成第三方投递链接。

评价整理遵循三条规则：单条极端评论不直接写成结论；生产线评价不外推到博士研发岗；没有足量员工样本时明确显示“待核验”，不凭空补写。每日自动任务只更新官网招聘状态，职员评价由人工复核后更新日期。

## 数据边界

- 薪资优先显示招聘单位官网原文；官网未披露时明确标注“官网未披露”，不再用未经核验的区间冒充岗位待遇；
- 工作环境描述是基于单位性质和岗位特征的保守归纳，不是对具体团队的事实评级；
- 职员视角来自匿名、公开、具有自选择偏差的社区样本，同一单位不同部门、直属领导、基地和项目阶段可能差异很大；
- 小红书内容仅在可公开检索并能与其他样本交叉印证时纳入，不复制原帖文字；
- 所有可点击投递入口只连接招聘单位自有官网，不连接高校转载页、牛客、应届生、WonderCV、LinkedIn 或招聘聚合站；
- 截止时间只采信招聘单位官网明确写出的日期或“招满即止/长期有效”表述，第三方页面上的日期不录入；
- 自动发现只负责岗位与专业证据；团队口碑、匹配等级和薪资仍建议人工复核。

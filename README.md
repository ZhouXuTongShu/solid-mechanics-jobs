# 固体力学博士招聘雷达

面向 2027 届固体力学博士的个人招聘信息看板，地域优先级为上海、合肥、江苏、浙江。首批包含 65 条岗位，按私营企业、央国企、研究所/实验室、外资企业和产业研究平台分类。

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

1. 并行访问现有岗位的招聘单位官网；
2. 根据明确的“已结束”“职位已下线”等信号更新状态；
3. 根据截止日期自动将过期岗位标为“已截止”；
4. 对比页面内容指纹，记录招聘页是否发生变化；
5. 扫描 `data/watch_sources.json` 中配置的招聘单位自有入口，发现包含有限元、CAE、结构仿真、流固耦合、疲劳、冲击等关键词的新链接；
6. 更新 `data/jobs.js` 和便于二次开发的 `data/jobs.json`。

手动更新：

```bash
python3 scripts/update_jobs.py
```

只检查、不写入：

```bash
python3 scripts/update_jobs.py --dry-run
```

更新器只允许从招聘单位自有域名发现新链接，并拒绝官网中跳向综合招聘平台或通用招聘 SaaS 的链接。部分官网依赖登录、验证码或前端动态接口；遇到这些情况时只标记“暂未连通”，不会擅自删除岗位。

## 固定每天 08:30 更新

### 方案一：GitHub Pages，推荐

项目已经包含两个工作流：

- `.github/workflows/update-jobs.yml`：每天北京时间 08:30 更新岗位数据并提交；
- `.github/workflows/deploy-pages.yml`：数据变化后自动发布网页。

使用步骤：

1. 将本目录作为一个 GitHub 仓库推送到 `main` 分支；
2. 在仓库 `Settings → Pages → Source` 中选择 `GitHub Actions`；
3. 在 `Actions` 页面手动运行一次 `Deploy dashboard to GitHub Pages`；
4. 确认仓库 `Settings → Actions → General → Workflow permissions` 允许 `Read and write permissions`。

GitHub 的定时任务可能比 08:30 延迟几分钟，但不需要电脑保持开机。

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

## 增加或修改岗位

岗位主数据位于 `data/jobs.js`。每条记录的关键字段为：

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
  "officialStatus": "verified",
  "deadline": "2026-10-30",
  "deadlineStatus": "dated",
  "deadlineSource": "招聘单位官网",
  "lastChecked": "2026-08-26"
}
```

`deadlineStatus` 可为 `dated`（官网明确日期）、`rolling`（官网写明招满即止/长期有效）或 `not-published`（官网未公布）。没有单位自有招聘入口时，`url` 留空且 `officialStatus` 设为 `unavailable`，页面会禁用跳转按钮。

状态可选值：

- `open`：当前在招；
- `monitor`：正式批监测；
- `intern`：实习转正；
- `direct`：社招直投；
- `closed`：已截止。

匹配度可选 `S`、`A`、`B`。人工核验清单位见 `data/official_links.json`；新增官方招聘入口时，同时维护该文件和 `data/watch_sources.json`，再运行 `python3 scripts/apply_official_links.py`。

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

- 薪资是博士应届税前年现金总包的区间估算，不等于企业承诺；
- 工作环境描述是基于单位性质和岗位特征的保守归纳，不是对具体团队的事实评级；
- 职员视角来自匿名、公开、具有自选择偏差的社区样本，同一单位不同部门、直属领导、基地和项目阶段可能差异很大；
- 小红书内容仅在可公开检索并能与其他样本交叉印证时纳入，不复制原帖文字；
- 所有可点击投递入口只连接招聘单位自有官网，不连接高校转载页、牛客、应届生、WonderCV、LinkedIn 或招聘聚合站；
- 截止时间只采信招聘单位官网明确写出的日期或“招满即止/长期有效”表述，第三方页面上的日期不录入；
- 自动发现的新岗位会显示“尚未完成人工背调”，建议人工确认后再调整匹配度和薪资。

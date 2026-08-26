(() => {
  "use strict";

  const dataset = window.SOLID_MECHANICS_JOBS || { meta: {}, jobs: [] };
  const allJobs = Array.isArray(dataset.jobs) ? dataset.jobs : [];
  const reviewDataset = window.SOLID_MECHANICS_EMPLOYEE_REVIEWS || { meta: {}, reviews: {} };
  const employeeReviews = reviewDataset.reviews || {};

  const TYPE_ORDER = ["私营企业", "央国企", "研究所/实验室", "外资企业", "产业研究平台"];
  const CITY_ORDER = ["上海", "合肥", "南京", "苏州", "无锡", "常州", "徐州", "杭州", "宁波", "绍兴", "江阴", "昆山"];
  const STATUS_LABELS = {
    open: "当前在招",
    monitor: "官网监测",
    intern: "实习转正",
    direct: "社招直投",
    closed: "已截止",
  };
  const MATCH_SCORE = { S: 3, A: 2, B: 1 };
  const STATUS_SCORE = { open: 5, direct: 4, intern: 3, monitor: 2, closed: 0 };

  const state = {
    type: "all",
    city: "all",
    status: "all",
    match: "all",
    sort: "recommended",
    query: "",
    savedOnly: false,
    saved: new Set(JSON.parse(localStorage.getItem("solidMechanicsSavedJobs") || "[]")),
  };

  const elements = {
    typeTabs: document.querySelector("#typeTabs"),
    cityFilter: document.querySelector("#cityFilter"),
    statusFilter: document.querySelector("#statusFilter"),
    matchFilter: document.querySelector("#matchFilter"),
    sortFilter: document.querySelector("#sortFilter"),
    searchInput: document.querySelector("#searchInput"),
    resetFilters: document.querySelector("#resetFilters"),
    resultCount: document.querySelector("#resultCount"),
    jobGroups: document.querySelector("#jobGroups"),
    emptyState: document.querySelector("#emptyState"),
    openSavedButton: document.querySelector("#openSavedButton"),
    savedCount: document.querySelector("#savedCount"),
    radarCount: document.querySelector("#radarCount"),
    lastUpdated: document.querySelector("#lastUpdated"),
    todayLabel: document.querySelector("#todayLabel"),
    footerVersion: document.querySelector("#footerVersion"),
    urgentStrip: document.querySelector("#urgentStrip"),
    urgentJobs: document.querySelector("#urgentJobs"),
    openMetric: document.querySelector("#openMetric"),
    officialLinkCount: document.querySelector("#officialLinkCount"),
    officialUnavailableCount: document.querySelector("#officialUnavailableCount"),
    officialDeadlineCount: document.querySelector("#officialDeadlineCount"),
    employeeReviewCount: document.querySelector("#employeeReviewCount"),
    sMetric: document.querySelector("#sMetric"),
    priorityCityMetric: document.querySelector("#priorityCityMetric"),
    typeMetric: document.querySelector("#typeMetric"),
    methodButton: document.querySelector("#methodButton"),
    methodDialog: document.querySelector("#methodDialog"),
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function normalize(value) {
    return String(value ?? "").toLocaleLowerCase("zh-CN").replace(/\s+/g, "");
  }

  function formatDate(value, withYear = false) {
    if (!value) return "待核验";
    const date = new Date(`${value}T00:00:00+08:00`);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("zh-CN", {
      year: withYear ? "numeric" : undefined,
      month: "2-digit",
      day: "2-digit",
    }).format(date);
  }

  function cityRank(city) {
    const first = String(city).split(/[\/、·]/)[0];
    const rank = CITY_ORDER.indexOf(first);
    return rank === -1 ? 99 : rank;
  }

  function typeRank(type) {
    const rank = TYPE_ORDER.indexOf(type);
    return rank === -1 ? 99 : rank;
  }

  function recommendationScore(job) {
    const location = Math.max(0, 20 - cityRank(job.city));
    return location * 100 + (MATCH_SCORE[job.match] || 0) * 20 + (STATUS_SCORE[job.status] || 0) * 5 + (job.priority || 0);
  }

  function uniqueCities() {
    return [...new Set(allJobs.flatMap((job) => String(job.city).split(/[\/、]/).map((city) => city.trim())))]
      .filter(Boolean)
      .sort((a, b) => cityRank(a) - cityRank(b) || a.localeCompare(b, "zh-CN"));
  }

  function initFilters() {
    const counts = allJobs.reduce((acc, job) => {
      acc[job.employerType] = (acc[job.employerType] || 0) + 1;
      return acc;
    }, {});

    const tabs = [
      `<button class="type-tab active" type="button" data-type="all" role="tab" aria-selected="true">全部单位 <span>${allJobs.length}</span></button>`,
      ...TYPE_ORDER.filter((type) => counts[type]).map(
        (type) => `<button class="type-tab" type="button" data-type="${escapeHtml(type)}" role="tab" aria-selected="false">${escapeHtml(type)} <span>${counts[type]}</span></button>`,
      ),
    ];
    elements.typeTabs.innerHTML = tabs.join("");

    uniqueCities().forEach((city) => {
      const option = document.createElement("option");
      option.value = city;
      option.textContent = city;
      elements.cityFilter.append(option);
    });
  }

  function getFilteredJobs() {
    const query = normalize(state.query);
    const filtered = allJobs.filter((job) => {
      if (state.savedOnly && !state.saved.has(job.id)) return false;
      if (state.type !== "all" && job.employerType !== state.type) return false;
      if (state.city !== "all" && !String(job.city).includes(state.city)) return false;
      if (state.status !== "all" && job.status !== state.status) return false;
      if (state.match !== "all" && job.match !== state.match) return false;
      if (query) {
        const review = employeeReviews[job.id] || {};
        const haystack = normalize([
          job.company,
          job.role,
          job.city,
          job.employerType,
          job.reputation,
          review.pressure,
          review.environment,
          review.rhythm,
          review.summary,
          review.question,
          review.sample,
          review.basis,
          ...(review.sources || []),
          ...(job.keywords || []),
        ].join(" "));
        if (!haystack.includes(query)) return false;
      }
      return true;
    });

    return filtered.sort((a, b) => {
      if (state.sort === "updated") return String(b.lastChecked).localeCompare(String(a.lastChecked));
      if (state.sort === "salary") return (b.salaryMax || 0) - (a.salaryMax || 0);
      if (state.sort === "company") return a.company.localeCompare(b.company, "zh-CN");
      return recommendationScore(b) - recommendationScore(a) || a.company.localeCompare(b.company, "zh-CN");
    });
  }

  function groupJobs(jobs) {
    return jobs.reduce((groups, job) => {
      const key = job.employerType || "其他单位";
      if (!groups[key]) groups[key] = [];
      groups[key].push(job);
      return groups;
    }, {});
  }

  function externalIcon() {
    return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M13 11l6-6M19 13v6H5V5h6"></path></svg>`;
  }

  function getDeadlineInfo(job) {
    if (job.deadline) {
      const deadline = new Date(`${job.deadline}T23:59:59+08:00`);
      const days = Math.ceil((deadline - new Date()) / 86400000);
      if (job.status === "closed" || days < 0) {
        return { label: `${formatDate(job.deadline, true)} · 已截止`, tone: "closed" };
      }
      if (days <= 7) {
        return { label: `${formatDate(job.deadline, true)} · 剩 ${days} 天`, tone: "urgent" };
      }
      return { label: formatDate(job.deadline, true), tone: "dated" };
    }
    if (job.deadlineStatus === "rolling") return { label: "招满即止", tone: "rolling" };
    return { label: "官网未公布", tone: "unknown" };
  }

  function renderOfficialAction(job) {
    const hasOfficialUrl = job.officialStatus === "verified" && /^https?:\/\//i.test(job.url || "");
    if (!hasOfficialUrl) {
      return `<span class="source-button unavailable" role="status" title="${escapeHtml(job.officialNote || "招聘单位自有官网入口待开放")}">官网待开放</span>`;
    }
    const label = job.officialKind === "employer-job" ? "官网岗位" : "招聘官网";
    return `<a class="source-button" href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer" aria-label="打开${escapeHtml(job.company)}的招聘单位官网" title="仅连接招聘单位自有官网"><span>${label}</span>${externalIcon()}</a>`;
  }

  function pressureTone(pressure) {
    if (pressure === "高") return "high";
    if (pressure === "较高") return "elevated";
    if (pressure === "中等") return "medium";
    if (pressure === "较低") return "low";
    return "unknown";
  }

  function renderEmployeeReview(job) {
    const review = employeeReviews[job.id] || {
      pressure: "待核验",
      environment: "尚未完成公开员工样本核验。",
      rhythm: "暂无可靠信息。",
      summary: "样本不足不代表评价负面，请在面试中向未来直属团队核实。",
      question: "询问团队实际作息、周末频率、加班补偿和近一年人员流动。",
      sample: "公开样本不足",
      confidence: "低",
      basis: "待补充",
      sources: [],
    };
    const sources = (review.sources || []).length
      ? review.sources.map((source) => `<span class="review-source-chip">${escapeHtml(source)}</span>`).join("")
      : `<span class="review-source-chip empty">无足量独立样本</span>`;

    return `
      <details class="employee-review">
        <summary>
          <span class="review-heading"><i aria-hidden="true"></i><strong>职员视角</strong></span>
          <span class="pressure-pill ${pressureTone(review.pressure)}">压力 ${escapeHtml(review.pressure)}</span>
          <span class="review-sample">${escapeHtml(review.sample)}</span>
          <span class="review-chevron" aria-hidden="true">⌄</span>
        </summary>
        <div class="review-panel">
          <div class="review-grid">
            <section>
              <span>工作环境</span>
              <p>${escapeHtml(review.environment)}</p>
            </section>
            <section>
              <span>常见节奏</span>
              <p>${escapeHtml(review.rhythm)}</p>
            </section>
            <section>
              <span>员工反馈综合</span>
              <p>${escapeHtml(review.summary)}</p>
            </section>
            <section class="review-question">
              <span>面试时建议核实</span>
              <p>${escapeHtml(review.question)}</p>
            </section>
          </div>
          <div class="review-meta">
            <div class="review-sources"><b>参考样本</b>${sources}</div>
            <span>可信度 ${escapeHtml(review.confidence)} · 整理于 ${escapeHtml(formatDate(reviewDataset.meta.reviewedAt, true))}</span>
            <small title="不展示或复制原帖，避免将匿名个案误写成事实">${escapeHtml(review.basis)}</small>
          </div>
        </div>
      </details>`;
  }

  function renderJobCard(job) {
    const saved = state.saved.has(job.id);
    const status = STATUS_LABELS[job.status] || job.status;
    const salary = job.salaryText || `${job.salaryMin || "—"}–${job.salaryMax || "—"} 万/年`;
    const deadline = getDeadlineInfo(job);
    const sourceState = job.sourceState === "official-unreachable" ? " · 暂未连通" : job.officialStatus === "unavailable" ? " · 无自有入口" : "";
    const checkedAt = job.officialStatus === "unavailable" ? job.officialVerifiedAt : job.lastChecked;

    return `
      <article class="job-card" data-match="${escapeHtml(job.match)}" data-status="${escapeHtml(job.status)}">
        <div class="company-block">
          <h4>${escapeHtml(job.company)}</h4>
          <span class="company-type">${escapeHtml(job.employerType)}</span>
        </div>
        <div class="role-block">
          <div class="tag-row">
            <span class="match-tag ${job.match.toLowerCase()}">${escapeHtml(job.match)}级匹配</span>
            <span class="status-tag ${escapeHtml(job.status)}">${escapeHtml(status)}</span>
            ${(job.keywords || []).slice(0, 2).map((item) => `<span class="keyword-tag">${escapeHtml(item)}</span>`).join("")}
          </div>
          <h5 title="${escapeHtml(job.role)}">${escapeHtml(job.role)}</h5>
          <p class="reputation">${escapeHtml(job.reputation)}</p>
        </div>
        <div class="location-block">
          <span>工作城市</span>
          <strong>${escapeHtml(job.city)}</strong>
        </div>
        <div class="salary-block">
          <span>博士税前年包</span>
          <strong>${escapeHtml(salary)}</strong>
          <small class="deadline-line ${deadline.tone}" title="截止信息只采信招聘单位官网"><b>截止</b>${escapeHtml(deadline.label)}</small>
          <small class="last-check">官网核验 ${escapeHtml(formatDate(checkedAt))}${escapeHtml(sourceState)}</small>
        </div>
        <div class="card-actions">
          <button class="save-button ${saved ? "saved" : ""}" type="button" data-save-id="${escapeHtml(job.id)}" aria-label="${saved ? "取消收藏" : "收藏岗位"}" title="${saved ? "取消收藏" : "收藏岗位"}">${saved ? "★ 已收藏" : "☆ 收藏"}</button>
          ${renderOfficialAction(job)}
        </div>
        ${renderEmployeeReview(job)}
      </article>`;
  }

  function render() {
    const jobs = getFilteredJobs();
    const groups = groupJobs(jobs);
    elements.resultCount.textContent = jobs.length;
    elements.emptyState.hidden = jobs.length > 0;
    elements.jobGroups.hidden = jobs.length === 0;

    elements.jobGroups.innerHTML = Object.entries(groups)
      .sort(([typeA], [typeB]) => typeRank(typeA) - typeRank(typeB))
      .map(([type, items]) => `
        <section class="job-group">
          <div class="job-group-head"><h3>${escapeHtml(type)}</h3><span>${items.length}</span></div>
          <div class="job-list">${items.map(renderJobCard).join("")}</div>
        </section>`)
      .join("");

    elements.savedCount.textContent = state.saved.size;
    elements.openSavedButton.classList.toggle("active", state.savedOnly);
    elements.openSavedButton.querySelector("span").textContent = state.savedOnly ? "★" : "☆";
  }

  function renderOverview() {
    const officialJobs = allJobs.filter((job) => job.officialStatus === "verified" && /^https?:\/\//i.test(job.url || ""));
    const sJobs = allJobs.filter((job) => job.match === "S");
    const priorityJobs = allJobs.filter((job) => /上海|合肥/.test(job.city));
    const types = new Set(allJobs.map((job) => job.employerType));

    elements.radarCount.textContent = allJobs.length;
    elements.openMetric.textContent = officialJobs.length;
    elements.officialLinkCount.textContent = officialJobs.length;
    elements.officialUnavailableCount.textContent = allJobs.filter((job) => job.officialStatus === "unavailable").length;
    elements.officialDeadlineCount.textContent = allJobs.filter((job) => job.deadlineStatus === "dated").length;
    if (elements.employeeReviewCount) {
      elements.employeeReviewCount.textContent = allJobs.filter((job) => employeeReviews[job.id]).length;
    }
    elements.sMetric.textContent = sJobs.length;
    elements.priorityCityMetric.textContent = priorityJobs.length;
    elements.typeMetric.textContent = types.size;
    elements.todayLabel.textContent = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "short" }).format(new Date());
    elements.lastUpdated.textContent = formatDate(dataset.meta.updatedAt, true) + (dataset.meta.updatedTime ? ` ${dataset.meta.updatedTime}` : "");
    elements.footerVersion.textContent = `数据版本 ${dataset.meta.version || "—"}`;

    const now = new Date();
    const urgent = allJobs.filter((job) => {
      if (!job.deadline || !job.url || job.officialStatus !== "verified" || job.status === "closed") return false;
      const deadline = new Date(`${job.deadline}T23:59:59+08:00`);
      const days = Math.ceil((deadline - now) / 86400000);
      return days >= 0 && days <= 7;
    });
    if (urgent.length) {
      elements.urgentStrip.hidden = false;
      elements.urgentJobs.innerHTML = urgent.map((job) => `<a href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(job.company)} · ${escapeHtml(job.role)} · ${formatDate(job.deadline)}截止</a>`).join("　/　");
    }
  }

  function resetFilters() {
    state.type = "all";
    state.city = "all";
    state.status = "all";
    state.match = "all";
    state.sort = "recommended";
    state.query = "";
    state.savedOnly = false;
    elements.searchInput.value = "";
    elements.cityFilter.value = "all";
    elements.statusFilter.value = "all";
    elements.matchFilter.value = "all";
    elements.sortFilter.value = "recommended";
    document.querySelectorAll(".type-tab").forEach((tab) => {
      const active = tab.dataset.type === "all";
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    render();
  }

  function bindEvents() {
    elements.typeTabs.addEventListener("click", (event) => {
      const button = event.target.closest("[data-type]");
      if (!button) return;
      state.type = button.dataset.type;
      document.querySelectorAll(".type-tab").forEach((tab) => {
        const active = tab === button;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
      });
      render();
    });

    elements.searchInput.addEventListener("input", (event) => {
      state.query = event.target.value;
      render();
    });
    elements.cityFilter.addEventListener("change", (event) => { state.city = event.target.value; render(); });
    elements.statusFilter.addEventListener("change", (event) => { state.status = event.target.value; render(); });
    elements.matchFilter.addEventListener("change", (event) => { state.match = event.target.value; render(); });
    elements.sortFilter.addEventListener("change", (event) => { state.sort = event.target.value; render(); });
    elements.resetFilters.addEventListener("click", resetFilters);
    elements.emptyState.querySelector("button").addEventListener("click", resetFilters);

    elements.jobGroups.addEventListener("click", (event) => {
      const button = event.target.closest("[data-save-id]");
      if (!button) return;
      const id = button.dataset.saveId;
      if (state.saved.has(id)) state.saved.delete(id);
      else state.saved.add(id);
      localStorage.setItem("solidMechanicsSavedJobs", JSON.stringify([...state.saved]));
      render();
    });

    elements.openSavedButton.addEventListener("click", () => {
      state.savedOnly = !state.savedOnly;
      render();
      document.querySelector("#jobs").scrollIntoView({ behavior: "smooth" });
    });

    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        elements.searchInput.focus();
      }
      if (event.key === "Escape" && elements.methodDialog.open) elements.methodDialog.close();
    });

    elements.methodButton.addEventListener("click", () => elements.methodDialog.showModal());
    elements.methodDialog.querySelector(".dialog-close").addEventListener("click", () => elements.methodDialog.close());
    elements.methodDialog.addEventListener("click", (event) => {
      if (event.target === elements.methodDialog) elements.methodDialog.close();
    });
    window.addEventListener("scroll", () => document.querySelector(".site-header").classList.toggle("scrolled", window.scrollY > 10), { passive: true });
  }

  initFilters();
  renderOverview();
  bindEvents();
  render();
})();

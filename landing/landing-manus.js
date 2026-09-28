(function () {
  const config = window.VERA_LANDING_CONFIG || {};
  const params = new URLSearchParams(window.location.search);
  const sourceBase = config.sourceBase || "ch_start60";
  const counterId = Number(config.analyticsCounterId) || 110960947;

  function cleanPart(value) {
    return String(value || "")
      .trim()
      .replace(/[^a-zA-Z0-9_-]+/g, "_")
      .replace(/^_+|_+$/g, "");
  }

  function buildStart(platform) {
    const suffix = platform === "telegram" ? "tg" : "max";
    const campaign = cleanPart(params.get("utm_campaign"));
    const content = cleanPart(params.get("utm_content"));
    const tail = ["lp", "yd", suffix, campaign, content].filter(Boolean).join("_");
    return `${sourceBase}__${tail}`;
  }

  function sendMetrikaGoal(goalName, data) {
    try {
      if (typeof window.ym === "function" && counterId) {
        window.ym(counterId, "reachGoal", goalName, data || {});
      }
    } catch (error) {
      // Analytics must never block navigation.
    }
  }

  function track(eventName, data) {
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({ event: eventName, ...(data || {}) });

    sendMetrikaGoal(eventName, data);
  }

  function sendOncePerVisit(goalName, data) {
    const key = `vera_metrika_${counterId}_${goalName}`;
    try {
      if (window.sessionStorage && window.sessionStorage.getItem(key)) return;
      if (window.sessionStorage) window.sessionStorage.setItem(key, "1");
    } catch (error) {
      if (sendOncePerVisit.sent[goalName]) return;
      sendOncePerVisit.sent[goalName] = true;
    }

    sendMetrikaGoal(goalName, data);
  }
  sendOncePerVisit.sent = {};

  document.querySelectorAll(".js-cta").forEach((link) => {
    const platform = link.dataset.platform || "max";
    const base = platform === "telegram" ? config.telegramUrl : config.maxUrl;
    if (!base) return;

    const start = buildStart(platform);
    link.href = `${base}?start=${encodeURIComponent(start)}`;
    link.addEventListener("click", () => {
      track(platform === "telegram" ? "click_telegram" : "click_max", { platform, start });
      sendMetrikaGoal(platform === "telegram" ? "open_telegram" : "open_max", { platform, start });
      sendMetrikaGoal("main_cta_click", { platform, start });
    });
  });

  document.querySelectorAll(".js-channel").forEach((link) => {
    const channel = link.dataset.channel || "max";
    link.addEventListener("click", () => {
      track(channel === "telegram" ? "click_channel_telegram" : "click_channel_max", { channel });
      sendMetrikaGoal(channel === "telegram" ? "channel_telegram" : "channel_max", { channel });
      sendMetrikaGoal("channel_click", { channel });
    });
  });

  if (typeof IntersectionObserver !== "function") return;

  function observeView(element, goalName, threshold) {
    if (!element) return;

    let seen = false;
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!seen && entry.isIntersecting) {
          seen = true;
          sendOncePerVisit(goalName);
          observer.disconnect();
        }
      });
    }, { threshold });

    observer.observe(element);
  }

  const cta = document.getElementById("cta");
  observeView(cta, "view_final_cta", 0.35);

  if (cta) {
    let seen = false;
    const legacyCtaObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!seen && entry.isIntersecting) {
          seen = true;
          track("view_cta");
          track("scroll_cta");
          legacyCtaObserver.disconnect();
        }
      });
    }, { threshold: 0.35 });

    legacyCtaObserver.observe(cta);
  }

  observeView(document.getElementById("features"), "view_features", 0.25);
})();

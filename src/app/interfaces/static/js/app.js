(() => {
  const app = document.querySelector("[data-shiftflow-app]");
  if (!app) {
    return;
  }

  let csrfToken = null;
  let csrfBootstrapPromise = null;

  const messages = {
    AUTHENTICATION_FAILED: "The email address or password is not correct.",
    AUTHENTICATION_REQUIRED: "Your session has ended. Sign in again to continue.",
    INVALID_ACCOUNT:
      "Check the email address and make sure the password has at least 12 characters.",
    INVALID_ACCOUNT_ACTION_TOKEN:
      "That code is invalid, expired, or has already been used. Request a new code and try again.",
    RATE_LIMITED:
      "There have been too many attempts. Wait a little while before trying again.",
    RATE_LIMITING_UNAVAILABLE:
      "This service is temporarily unavailable. Please try again later.",
    ACCOUNT_LIFECYCLE_UNAVAILABLE:
      "This service is temporarily unavailable. Please try again later.",
    AUTHENTICATION_UNAVAILABLE:
      "Sign-in is temporarily unavailable. Please try again later.",
    CSRF_FAILED:
      "The request could not be completed safely. Reload the page and try again.",
    ALREADY_A_MEMBER:
      "You already belong to this company. Select it from your company list.",
    INVALID_JOINING_CODE:
      "That joining code is not valid. Check it and try again.",
    JOINING_CODE_UNAVAILABLE:
      "A joining code could not be generated. Please try again later.",
    COMPANY_REGISTRATION_FAILED:
      "The company could not be created. Please try again.",
    MEMBERSHIP_CREATION_FAILED:
      "You could not be added to the company. Please try again.",
    INVALID_AVAILABILITY:
      "Check the start and end times. Use a valid local time and make sure the end is later.",
    AVAILABILITY_OVERLAP:
      "This interval overlaps another availability interval. Adjust the dates or times and try again.",
    AVAILABILITY_SAVE_FAILED:
      "Your availability could not be saved. Please try again.",
    INVALID_SHIFT:
      "Check the shift dates and times, then try again.",
    EMPLOYEE_UNAVAILABLE:
      "This employee does not have availability covering the whole shift.",
    SHIFT_ASSIGNMENT_CONFLICT:
      "This employee is already assigned to an overlapping shift.",
    FORBIDDEN:
      "You do not have permission to change this company's schedule.",
    RESOURCE_NOT_FOUND:
      "That schedule item or employee is no longer available. Reload and try again.",
  };
  const loadingLabels = {
    register: "Creating account…",
    login: "Signing in…",
    "verify-email": "Verifying…",
    "resend-verification": "Sending code…",
    "request-reset": "Sending reset code…",
    "confirm-reset": "Updating password…",
    "register-company": "Creating company…",
    "join-company": "Joining company…",
    create: "Saving availability…",
    update: "Updating availability…",
    "create-shift": "Creating shift…",
  };

  const endpoints = {
    register: "/auth/register",
    login: "/auth/login",
    "verify-email": "/auth/email-verification/confirm",
    "resend-verification": "/auth/email-verification/request",
    "request-reset": "/auth/password-reset/request",
    "confirm-reset": "/auth/password-reset/confirm",
  };
  const companyEndpoints = {
    "register-company": "/companies",
    "join-company": "/companies/join",
  };
  const companyStorageKey = "shiftflow.selectedCompanyId";
  const companies = new Map();
  const seenCompanyCursors = new Set();
  let nextCompanyCursor = null;
  let loadingCompanies = false;
  let companyPagePromise = null;
  let currentCompanyId = null;
  let selectionLoaded = false;
  const availabilityWeekStart = new Date();
  availabilityWeekStart.setHours(0, 0, 0, 0);
  availabilityWeekStart.setDate(
    availabilityWeekStart.getDate() -
      ((availabilityWeekStart.getDay() + 6) % 7),
  );
  const availabilityItems = [];
  const seenAvailabilityCursors = new Set();
  let availabilityCursor = null;
  let availabilityCompanyId = null;
  let availabilityWeekKey = null;
  let availabilityGeneration = 0;
  let availabilityLoading = false;
  let availabilityLoadError = false;
  let availabilityLoadingMore = false;
  const scheduleWeekStart = startOfLocalWeek(new Date());
  const scheduleItems = [];
  const scheduleEmployees = new Map();
  const seenScheduleCursors = new Set();
  const seenScheduleEmployeeCursors = new Set();
  let scheduleCursor = null;
  let scheduleEmployeeCursor = null;
  let scheduleCompanyId = null;
  let scheduleWeekKey = null;
  let scheduleGeneration = 0;
  let scheduleRosterGeneration = 0;
  let scheduleLoading = false;
  let scheduleLoadError = false;
  let scheduleLoadingMore = false;

  function displayMessage(container, text, kind) {
    if (!container) {
      return;
    }

    container.textContent = text;
    container.dataset.kind = kind;
    container.setAttribute("role", kind === "error" ? "alert" : "status");
    container.setAttribute("aria-live", kind === "error" ? "assertive" : "polite");
    container.hidden = false;
  }

  function sessionMessage(text) {
    const container = document.getElementById("session-message");
    if (!container) {
      return;
    }

    container.textContent = text;
    container.classList.remove("sr-only");
    container.classList.add("session-error");
    container.setAttribute("role", "alert");
  }

  async function bootstrapCsrfToken() {
    if (csrfToken) {
      return csrfToken;
    }
    if (csrfBootstrapPromise) {
      return csrfBootstrapPromise;
    }

    csrfBootstrapPromise = (async () => {
      const response = await fetch("/auth/session/bootstrap", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(
          response.status === 401
            ? "Your session has ended. Sign in again to continue."
            : "Session security could not be refreshed. Reload the page and try again.",
        );
      }

      const payload = await response.json();
      if (
        typeof payload.csrf_token !== "string" ||
        !/^[A-Za-z0-9_-]{43}$/.test(payload.csrf_token)
      ) {
        throw new Error("Session security could not be refreshed. Reload the page.");
      }

      csrfToken = payload.csrf_token;
      document.dispatchEvent(
        new CustomEvent("shiftflow:csrf-ready", {
          detail: { token: csrfToken },
        }),
      );
      return csrfToken;
    })().finally(() => {
      csrfBootstrapPromise = null;
    });

    return csrfBootstrapPromise;
  }

  async function writeJson(endpoint, payload, { method = "POST", csrf = false } = {}) {
    const headers = {
      Accept: "application/json",
      "Content-Type": "application/json",
    };
    if (csrf) {
      headers["X-CSRF-Token"] = await bootstrapCsrfToken();
    }

    return fetch(endpoint, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      headers,
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(15000),
    });
  }

  function postJson(endpoint, payload, options = {}) {
    return writeJson(endpoint, payload, { ...options, method: "POST" });
  }

  function safeErrorMessage(response, payload) {
    const code = payload?.error?.code;
    if (response.status === 429) {
      return messages.RATE_LIMITED;
    }
    if (response.status === 503 && !messages[code]) {
      return "This service is temporarily unavailable. Please try again later.";
    }
    if (response.status === 400 && !messages[code]) {
      return "Check the information and code, then try again.";
    }
    return messages[code] || "We could not complete that request. Please try again.";
  }

  async function responsePayload(response) {
    if (response.status === 204) {
      return null;
    }
    try {
      return await response.json();
    } catch {
      return null;
    }
  }

  function clearForm(form) {
    for (const input of form.querySelectorAll("input")) {
      input.value = "";
    }
  }

  function companyStatus(message, kind = "info") {
    const status = document.querySelector("[data-company-status]");
    if (status) {
      status.textContent = message;
      status.dataset.kind = kind;
      status.setAttribute("role", kind === "error" ? "alert" : "status");
    }
    const listStatus = document.querySelector("[data-company-list-status]");
    if (listStatus && message) {
      listStatus.textContent = message;
      listStatus.dataset.kind = kind;
      listStatus.setAttribute("role", kind === "error" ? "alert" : "status");
    }
  }

  function availabilityStatus(message, kind = "info") {
    const status = document.querySelector("[data-availability-status]");
    if (status) {
      status.textContent = message;
      status.dataset.kind = kind;
      status.setAttribute("role", kind === "error" ? "alert" : "status");
    }
  }

  function localDateTime(date) {
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function localDateTimeToRFC3339(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
    if (!match) {
      return null;
    }
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const hour = Number(match[4]);
    const minute = Number(match[5]);
    const date = new Date(year, month - 1, day, hour, minute);
    if (
      date.getFullYear() !== year ||
      date.getMonth() !== month - 1 ||
      date.getDate() !== day ||
      date.getHours() !== hour ||
      date.getMinutes() !== minute
    ) {
      return null;
    }
    return localDateToRFC3339(date);
  }

  function localDateToRFC3339(date) {
    const pad = (value) => String(value).padStart(2, "0");
    const offsetMinutes = -date.getTimezoneOffset();
    const sign = offsetMinutes >= 0 ? "+" : "-";
    const offset = Math.abs(offsetMinutes);
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:00${sign}${pad(Math.floor(offset / 60))}:${pad(offset % 60)}`;
  }

  function startOfLocalWeek(date) {
    const start = new Date(date);
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
    return start;
  }

  function currentAvailabilityWeekKey() {
    return localDateTime(availabilityWeekStart);
  }

  function availabilityWindow() {
    const end = new Date(availabilityWeekStart);
    end.setDate(end.getDate() + 7);
    return {
      from: localDateToRFC3339(availabilityWeekStart),
      to: localDateToRFC3339(end),
    };
  }

  function formatAvailabilityWeek() {
    const end = new Date(availabilityWeekStart);
    end.setDate(end.getDate() + 6);
    const formatter = new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
    });
    const startLabel = formatter.format(availabilityWeekStart);
    const endLabel = new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    }).format(end);
    const label = document.querySelector("[data-availability-week-label]");
    if (label) {
      label.textContent = `${startLabel} – ${endLabel}`;
    }
    document.querySelectorAll("[data-availability-day-label]").forEach(
      (heading, index) => {
        const day = new Date(availabilityWeekStart);
        day.setDate(day.getDate() + index);
        heading.textContent = new Intl.DateTimeFormat(undefined, {
          weekday: "short",
          month: "short",
          day: "numeric",
        }).format(day);
      },
    );
    const zone =
      Intl.DateTimeFormat().resolvedOptions().timeZone ||
      "your device’s local time zone";
    document.querySelectorAll("[data-local-time-zone]").forEach((element) => {
      element.textContent = zone;
    });
  }

  function localDayDifference(start, end) {
    const startDay = Date.UTC(
      start.getFullYear(),
      start.getMonth(),
      start.getDate(),
    );
    const endDay = Date.UTC(end.getFullYear(), end.getMonth(), end.getDate());
    return Math.round((endDay - startDay) / 86400000);
  }

  function formatLocalTime(date) {
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(date);
  }

  function renderAvailability() {
    const grid = document.querySelector("[data-availability-week-grid]");
    if (!grid) {
      return;
    }
    const company = availabilityCompanyId
      ? companies.get(availabilityCompanyId)
      : null;
    const isEmployee = company?.role === "employee";
    const showGrid = isEmployee;
    const editor = document.querySelector(".availability-editor");
    if (editor) {
      editor.hidden = !isEmployee;
    }
    grid.hidden = !showGrid;
    document.querySelector("[data-availability-empty]").hidden =
      !isEmployee ||
      availabilityItems.length > 0 ||
      Boolean(availabilityCursor) ||
      availabilityLoading ||
      availabilityLoadError;
    document.querySelector("[data-availability-access]").hidden =
      !company || company.role === "employee";
    document.querySelector("[data-availability-company-empty]").hidden =
      Boolean(company) || companies.size > 0;
    document.querySelector("[data-availability-load-error]").hidden =
      !availabilityLoadError;
    const loadMore = document.querySelector("[data-availability-load-more]");
    if (loadMore) {
      loadMore.hidden = !isEmployee || !availabilityCursor;
      loadMore.disabled = availabilityLoadingMore;
      loadMore.setAttribute("aria-busy", String(availabilityLoadingMore));
    }

    grid.querySelectorAll("[data-availability-day-list]").forEach((list) => {
      list.replaceChildren();
    });
    for (const item of availabilityItems) {
      const start = new Date(item.start_at);
      const end = new Date(item.end_at);
      const weekday = Math.max(
        0,
        Math.min(
          6,
          localDayDifference(availabilityWeekStart, start),
        ),
      );
      const day = grid.querySelector(
        `[data-availability-day="${weekday}"] [data-availability-day-list]`,
      );
      if (!day || !Number.isFinite(start.valueOf()) || !Number.isFinite(end.valueOf())) {
        continue;
      }
      const delta = localDayDifference(start, end);
      const overnight = delta > 0;
      const interval = document.createElement("li");
      interval.dataset.availabilityItem = item.id;
      interval.setAttribute("data-availability-item", "");
      interval.className = "availability-interval";
      const time = document.createElement("p");
      time.className = "availability-interval-time";
      time.textContent = `${formatLocalTime(start)}–${formatLocalTime(end)}${overnight ? ` (+${delta} day${delta === 1 ? "" : "s"})` : ""}`;
      const date = document.createElement("p");
      date.className = "availability-interval-date";
      date.textContent = `${new Intl.DateTimeFormat(undefined, {
        weekday: "short",
        month: "short",
        day: "numeric",
      }).format(start)}${overnight ? ` to ${new Intl.DateTimeFormat(undefined, {
        weekday: "short",
        month: "short",
        day: "numeric",
      }).format(end)}` : ""}`;
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "button button-secondary button-compact availability-edit";
      edit.dataset.availabilityEdit = item.id;
      edit.setAttribute("aria-label", `Edit availability ${time.textContent}`);
      edit.textContent = "Edit";
      interval.append(date, time, edit);
      day.append(interval);
    }
  }

  function resetAvailabilityForm() {
    const form = document.querySelector("[data-availability-form]");
    if (!form) {
      return;
    }
    form.reset();
    form.dataset.availabilityForm = "create";
    delete form.dataset.availabilityId;
    document.querySelector("[data-availability-form-title]").textContent =
      "Add availability";
    document.querySelector("[data-availability-submit]").textContent =
      "Save availability";
    document.querySelector("[data-availability-cancel]").hidden = true;
    const feedback = form.querySelector("[data-availability-form-message]");
    feedback.hidden = true;
    feedback.textContent = "";
    delete feedback.dataset.kind;
  }

  async function loadAvailabilityPage(companyId, generation, cursor = null) {
    const { from, to } = availabilityWindow();
    const query = new URLSearchParams({ from, to, limit: "100" });
    if (cursor) {
      query.set("cursor", cursor);
    }
    try {
      const response = await fetch(
        `/companies/${encodeURIComponent(companyId)}/availability/mine?${query}`,
        {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          referrerPolicy: "no-referrer",
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(15000),
        },
      );
      if (generation !== availabilityGeneration) {
        return false;
      }
      if (response.status === 401) {
        clearStoredCompanyId();
        window.location.replace("/login");
        return false;
      }
      const payload = await responsePayload(response);
      if (!response.ok) {
        throw new Error(safeErrorMessage(response, payload));
      }
      if (
        !Array.isArray(payload?.items) ||
        !(payload.next_cursor === null || typeof payload.next_cursor === "string")
      ) {
        throw new Error("Availability could not be loaded. Try again.");
      }
      const pageItems = [];
      for (const item of payload.items) {
        if (
          typeof item?.id !== "string" ||
          item.company_id !== companyId ||
          typeof item.start_at !== "string" ||
          typeof item.end_at !== "string" ||
          !Number.isFinite(Date.parse(item.start_at)) ||
          !Number.isFinite(Date.parse(item.end_at))
        ) {
          throw new Error("Availability could not be loaded. Try again.");
        }
        pageItems.push({
          id: item.id,
          company_id: item.company_id,
          start_at: item.start_at,
          end_at: item.end_at,
        });
      }
      if (
        payload.next_cursor &&
        (payload.next_cursor === cursor ||
          seenAvailabilityCursors.has(payload.next_cursor))
      ) {
        throw new Error("Availability page could not be loaded. Try again.");
      }
      availabilityItems.push(...pageItems);
      availabilityCursor = payload.next_cursor;
      if (availabilityCursor) {
        seenAvailabilityCursors.add(availabilityCursor);
      }
      availabilityLoadError = false;
      renderAvailability();
      availabilityStatus(
        availabilityItems.length
          ? "Your availability is up to date."
          : "No availability intervals were found for this week.",
      );
      return true;
    } catch (error) {
      if (generation !== availabilityGeneration) {
        return false;
      }
      const message =
        error instanceof Error && error.name === "TimeoutError"
          ? "The availability request timed out. Check your connection and try again."
          : error instanceof Error &&
              error.message !== "Failed to fetch"
            ? error.message
            : "Availability could not be loaded. Check your connection and try again.";
      const errorPanel = document.querySelector(
        "[data-availability-load-error]",
      );
      availabilityLoadError = true;
      errorPanel.hidden = false;
      availabilityStatus(message, "error");
      return false;
    }
  }

  async function refreshAvailability(force = false) {
    if (app.dataset.page !== "availability") {
      return;
    }
    formatAvailabilityWeek();
    const selected = companies.get(storedCompanyId());
    const companyId = selected?.company_id || null;
    const weekKey = currentAvailabilityWeekKey();
    if (
      !force &&
      companyId === availabilityCompanyId &&
      weekKey === availabilityWeekKey
    ) {
      return;
    }
    availabilityGeneration += 1;
    const generation = availabilityGeneration;
    availabilityCompanyId = companyId;
    availabilityWeekKey = weekKey;
    availabilityItems.length = 0;
    availabilityCursor = null;
    availabilityLoadError = false;
    availabilityLoading = Boolean(companyId && selected.role === "employee");
    availabilityLoadingMore = false;
    seenAvailabilityCursors.clear();
    resetAvailabilityForm();
    renderAvailability();
    if (!companyId) {
      availabilityStatus(
        companies.size
          ? "Choose an employee membership to view availability."
          : "Choose a company to load your availability.",
      );
      return;
    }
    if (selected.role !== "employee") {
      availabilityStatus(
        "Availability entry is available to employee members of the selected company.",
      );
      return;
    }
    availabilityStatus("Loading your availability…");
    try {
      await loadAvailabilityPage(companyId, generation);
    } finally {
      if (generation === availabilityGeneration) {
        availabilityLoading = false;
        renderAvailability();
      }
    }
  }

  function editAvailability(id) {
    const item = availabilityItems.find((availability) => availability.id === id);
    if (!item) {
      availabilityStatus(
        "That availability interval is no longer available. Reload the page and try again.",
        "error",
      );
      return;
    }
    const form = document.querySelector("[data-availability-form]");
    const start = new Date(item.start_at);
    const end = new Date(item.end_at);
    form.dataset.availabilityForm = "update";
    form.dataset.availabilityId = item.id;
    form.querySelector("#availability-start-at").value = localDateTime(start);
    form.querySelector("#availability-end-at").value = localDateTime(end);
    document.querySelector("[data-availability-form-title]").textContent =
      "Update availability";
    document.querySelector("[data-availability-submit]").textContent =
      "Update availability";
    document.querySelector("[data-availability-cancel]").hidden = false;
    form.querySelector("[data-availability-form-message]").hidden = true;
    document.querySelector("#availability-form-title").scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
      block: "start",
    });
    form.querySelector("#availability-start-at").focus({ preventScroll: true });
  }

  async function submitAvailabilityForm(form) {
    const feedback = form.querySelector("[data-availability-form-message]");
    feedback.hidden = true;
    feedback.textContent = "";
    delete feedback.dataset.kind;
    const company = companies.get(availabilityCompanyId);
    if (!company || company.role !== "employee") {
      displayMessage(
        feedback,
        "Choose a company where you are an employee to manage availability.",
        "error",
      );
      return;
    }
    const startValue = form.querySelector("#availability-start-at").value;
    const endValue = form.querySelector("#availability-end-at").value;
    const startAt = localDateTimeToRFC3339(startValue);
    const endAt = localDateTimeToRFC3339(endValue);
    if (!startAt || !endAt || Date.parse(endAt) <= Date.parse(startAt)) {
      displayMessage(
        feedback,
        "Choose valid local times and make sure the end is later than the start.",
        "error",
      );
      return;
    }
    setSubmitting(form, true);
    try {
      const mode = form.dataset.availabilityForm;
      const id = form.dataset.availabilityId;
      const endpoint = `/companies/${encodeURIComponent(company.company_id)}/availability${mode === "update" ? `/${encodeURIComponent(id)}` : ""}`;
      const response = await writeJson(
        endpoint,
        { start_at: startAt, end_at: endAt },
        { method: mode === "update" ? "PUT" : "POST", csrf: true },
      );
      const payload = await responsePayload(response);
      if (response.status === 401) {
        clearStoredCompanyId();
        window.location.replace("/login");
        return;
      }
      if (!response.ok) {
        displayMessage(feedback, safeErrorMessage(response, payload), "error");
        return;
      }
      if (
        payload?.company_id !== company.company_id ||
        typeof payload.id !== "string" ||
        payload.start_at !== startAt &&
          Date.parse(payload.start_at) !== Date.parse(startAt) ||
        Date.parse(payload.end_at) !== Date.parse(endAt)
      ) {
        displayMessage(
          feedback,
          "The availability response could not be verified. Reload the page before continuing.",
          "error",
        );
        return;
      }
      availabilityWeekStart.setTime(startOfLocalWeek(new Date(startAt)).getTime());
      resetAvailabilityForm();
      await refreshAvailability(true);
      availabilityStatus("Availability saved.");
    } catch {
      displayMessage(
        feedback,
        "Availability could not be saved. Check your connection and try again.",
        "error",
      );
    } finally {
      setSubmitting(form, false);
    }
  }

  function scheduleStatus(message, kind = "info") {
    const status = document.querySelector("[data-schedule-status]");
    if (status) {
      status.textContent = message;
      status.dataset.kind = kind;
      status.setAttribute("role", kind === "error" ? "alert" : "status");
    }
  }

  function scheduleWindow() {
    const end = new Date(scheduleWeekStart);
    end.setDate(end.getDate() + 7);
    return {
      from: localDateToRFC3339(scheduleWeekStart),
      to: localDateToRFC3339(end),
    };
  }

  function formatScheduleWeek() {
    const end = new Date(scheduleWeekStart);
    end.setDate(end.getDate() + 6);
    const startLabel = new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
    }).format(scheduleWeekStart);
    const endLabel = new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    }).format(end);
    const label = document.querySelector("[data-schedule-week-label]");
    if (label) {
      label.textContent = `${startLabel} – ${endLabel}`;
    }
    document.querySelectorAll("[data-schedule-day-label]").forEach(
      (heading, index) => {
        const day = new Date(scheduleWeekStart);
        day.setDate(day.getDate() + index);
        heading.textContent = new Intl.DateTimeFormat(undefined, {
          weekday: "short",
          month: "short",
          day: "numeric",
        }).format(day);
      },
    );
    const zone =
      Intl.DateTimeFormat().resolvedOptions().timeZone ||
      "your device’s local time zone";
    document.querySelectorAll("[data-schedule-time-zone]").forEach((element) => {
      element.textContent = zone;
    });
  }

  function scheduleLocalDateTimeCandidates(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
    if (!match) {
      return [];
    }
    const [year, month, day, hour, minute] = match.slice(1).map(Number);
    const wallTime = Date.UTC(year, month - 1, day, hour, minute);
    const expected = [year, month - 1, day, hour, minute];
    const offsets = new Set();
    for (let quarterHours = -144; quarterHours <= 144; quarterHours += 1) {
      offsets.add(
        new Date(wallTime + quarterHours * 900000).getTimezoneOffset(),
      );
    }
    const candidates = [];
    for (const offset of offsets) {
      const candidate = new Date(wallTime + offset * 60000);
      if (
        candidate.getTimezoneOffset() === offset &&
        candidate.getFullYear() === expected[0] &&
        candidate.getMonth() === expected[1] &&
        candidate.getDate() === expected[2] &&
        candidate.getHours() === expected[3] &&
        candidate.getMinutes() === expected[4]
      ) {
        candidates.push(candidate);
      }
    }
    return candidates.sort((left, right) => left - right);
  }

  function updateScheduleOccurrence(input, wrapper, selection) {
    const candidates = scheduleLocalDateTimeCandidates(input.value);
    wrapper.hidden = candidates.length < 2;
    selection.disabled = candidates.length < 2;
    if (candidates.length < 2) {
      selection.value = "0";
    }
  }

  function scheduleInputTimestamp(input, occurrence) {
    const candidates = scheduleLocalDateTimeCandidates(input.value);
    const selected = candidates[Number(occurrence.value)] || candidates[0];
    return selected ? localDateToRFC3339(selected) : null;
  }

  function scheduleDateDifference(start, end) {
    const startDate = Date.UTC(
      start.getFullYear(),
      start.getMonth(),
      start.getDate(),
    );
    const endDate = Date.UTC(end.getFullYear(), end.getMonth(), end.getDate());
    return Math.round((endDate - startDate) / 86400000);
  }

  function renderSchedule() {
    const grid = document.querySelector("[data-schedule-week-grid]");
    if (!grid) {
      return;
    }
    const company = scheduleCompanyId
      ? companies.get(scheduleCompanyId)
      : null;
    const isAdministrator = company?.role === "administrator";
    const isMember = isAdministrator || company?.role === "employee";
    const adminTools = document.querySelector("[data-schedule-admin-tools]");
    if (adminTools) {
      adminTools.hidden = !isAdministrator;
    }
    grid.hidden = !isMember;
    document.querySelector("[data-schedule-empty]").hidden =
      !isMember ||
      scheduleItems.length > 0 ||
      Boolean(scheduleCursor) ||
      scheduleLoading ||
      scheduleLoadError;
    document.querySelector("[data-schedule-access]").hidden =
      Boolean(company) || companies.size === 0;
    document.querySelector("[data-schedule-company-empty]").hidden =
      Boolean(company) || companies.size > 0;
    document.querySelector("[data-schedule-load-error]").hidden =
      !scheduleLoadError;
    const loadMore = document.querySelector("[data-schedule-load-more]");
    if (loadMore) {
      loadMore.hidden = !isMember || !scheduleCursor;
      loadMore.disabled = scheduleLoadingMore;
      loadMore.setAttribute("aria-busy", String(scheduleLoadingMore));
    }
    const rosterLoadMore = document.querySelector(
      "[data-schedule-roster-load-more]",
    );
    if (rosterLoadMore) {
      rosterLoadMore.hidden = !isAdministrator || !scheduleEmployeeCursor;
    }

    grid.querySelectorAll("[data-schedule-day-list]").forEach((list) => {
      list.replaceChildren();
    });
    for (const item of scheduleItems) {
      const start = new Date(item.start_at);
      const end = new Date(item.end_at);
      if (
        !Number.isFinite(start.valueOf()) ||
        !Number.isFinite(end.valueOf()) ||
        end <= start
      ) {
        continue;
      }
      const dayIndex = Math.max(
        0,
        Math.min(6, scheduleDateDifference(scheduleWeekStart, start)),
      );
      const day = grid.querySelector(
        `[data-schedule-day="${dayIndex}"] [data-schedule-day-list]`,
      );
      if (!day) {
        continue;
      }
      const shift = document.createElement("li");
      shift.className = "schedule-shift";
      shift.dataset.scheduleShift = item.id;
      const dateLabel = new Intl.DateTimeFormat(undefined, {
        weekday: "short",
        month: "short",
        day: "numeric",
      });
      const dates = `${dateLabel.format(start)}${
        scheduleDateDifference(start, end) > 0
          ? ` to ${dateLabel.format(end)}`
          : ""
      }`;
      const date = document.createElement("p");
      date.className = "schedule-shift-date";
      date.textContent = dates;
      const times = document.createElement("p");
      times.className = "schedule-shift-time";
      times.textContent = `${formatLocalTime(start)}–${formatLocalTime(end)}`;
      shift.append(date, times);

      const assignment = document.createElement("p");
      assignment.className = "schedule-shift-assignment";
      assignment.dataset.scheduleAssignedTo = "";
      if (item.assignment) {
        assignment.textContent = isAdministrator
          ? `Assigned to ${item.assignment.display_name}`
          : "Your shift";
      } else {
        assignment.textContent = "Unassigned";
      }
      shift.append(assignment);

      if (isAdministrator && !item.assignment) {
        const controls = document.createElement("div");
        controls.className = "schedule-assignment-controls";
        const select = document.createElement("select");
        select.dataset.scheduleEmployeeSelect = "";
        select.setAttribute(
          "aria-label",
          `Choose an employee for ${dates}, ${times.textContent}`,
        );
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Select employee";
        select.append(placeholder);
        for (const employee of scheduleEmployees.values()) {
          const option = document.createElement("option");
          option.value = employee.membership_id;
          option.textContent = employee.display_name;
          select.append(option);
        }
        const assign = document.createElement("button");
        assign.className = "button button-secondary button-compact";
        assign.type = "button";
        assign.dataset.scheduleAssign = item.id;
        assign.textContent = "Assign";
        assign.disabled = scheduleEmployees.size === 0;
        controls.append(select, assign);
        shift.append(controls);
      }
      day.append(shift);
    }
  }

  async function loadScheduleEmployeePage(companyId, generation, cursor = null) {
    const query = new URLSearchParams({ limit: "100" });
    if (cursor) {
      query.set("cursor", cursor);
    }
    try {
      const response = await fetch(
        `/companies/${encodeURIComponent(companyId)}/employees?${query}`,
        {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          referrerPolicy: "no-referrer",
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(15000),
        },
      );
      if (generation !== scheduleRosterGeneration) {
        return false;
      }
      if (response.status === 401) {
        clearStoredCompanyId();
        window.location.replace("/login");
        return false;
      }
      const payload = await responsePayload(response);
      if (!response.ok) {
        throw new Error(safeErrorMessage(response, payload));
      }
      if (
        !Array.isArray(payload?.items) ||
        !(payload.next_cursor === null || typeof payload.next_cursor === "string")
      ) {
        throw new Error("The employee list response was invalid.");
      }
      const employees = [];
      for (const item of payload.items) {
        if (
          typeof item?.membership_id !== "string" ||
          typeof item.display_name !== "string"
        ) {
          throw new Error("The employee list response was invalid.");
        }
        employees.push({
          membership_id: item.membership_id,
          display_name: item.display_name,
        });
      }
      if (
        payload.next_cursor &&
        (payload.next_cursor === cursor ||
          seenScheduleEmployeeCursors.has(payload.next_cursor))
      ) {
        throw new Error("The employee list cursor was invalid.");
      }
      for (const employee of employees) {
        scheduleEmployees.set(employee.membership_id, employee);
      }
      scheduleEmployeeCursor = payload.next_cursor;
      if (scheduleEmployeeCursor) {
        seenScheduleEmployeeCursors.add(scheduleEmployeeCursor);
      }
      const message = document.querySelector(
        "[data-schedule-roster-message]",
      );
      if (message) {
        message.textContent = scheduleEmployees.size
          ? `${scheduleEmployees.size} eligible employee${scheduleEmployees.size === 1 ? "" : "s"} loaded.`
          : "No linked employee memberships are available to assign.";
      }
      renderSchedule();
      return true;
    } catch (error) {
      if (generation !== scheduleRosterGeneration) {
        return false;
      }
      const message = document.querySelector(
        "[data-schedule-roster-message]",
      );
      if (message) {
        message.textContent =
          error instanceof Error && error.message !== "Failed to fetch"
            ? error.message
            : "Eligible employees could not be loaded. Check your connection and try again.";
        message.dataset.kind = "error";
      }
      return false;
    }
  }

  async function loadSchedulePage(companyId, generation, cursor = null) {
    const { from, to } = scheduleWindow();
    const query = new URLSearchParams({ from, to, limit: "100" });
    if (cursor) {
      query.set("cursor", cursor);
    }
    const company = companies.get(companyId);
    const endpoint =
      company?.role === "administrator" ? "shifts" : "shifts/mine";
    try {
      const response = await fetch(
        `/companies/${encodeURIComponent(companyId)}/${endpoint}?${query}`,
        {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          referrerPolicy: "no-referrer",
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(15000),
        },
      );
      if (generation !== scheduleGeneration) {
        return false;
      }
      if (response.status === 401) {
        clearStoredCompanyId();
        window.location.replace("/login");
        return false;
      }
      const payload = await responsePayload(response);
      if (!response.ok) {
        throw new Error(safeErrorMessage(response, payload));
      }
      if (
        !Array.isArray(payload?.items) ||
        !(payload.next_cursor === null || typeof payload.next_cursor === "string")
      ) {
        throw new Error("The schedule response was invalid.");
      }
      const pageItems = [];
      for (const item of payload.items) {
        if (
          typeof item?.id !== "string" ||
          item.company_id !== companyId ||
          typeof item.start_at !== "string" ||
          typeof item.end_at !== "string" ||
          !Number.isFinite(Date.parse(item.start_at)) ||
          !Number.isFinite(Date.parse(item.end_at)) ||
          Date.parse(item.end_at) <= Date.parse(item.start_at) ||
          !(
            item.assignment === null ||
            (typeof item.assignment?.display_name === "string" &&
              typeof item.assignment.membership_id === "string")
          )
        ) {
          throw new Error("The schedule response was invalid.");
        }
        pageItems.push({
          id: item.id,
          company_id: item.company_id,
          start_at: item.start_at,
          end_at: item.end_at,
          assignment: item.assignment,
        });
      }
      if (
        payload.next_cursor &&
        (payload.next_cursor === cursor ||
          seenScheduleCursors.has(payload.next_cursor))
      ) {
        throw new Error("The schedule page cursor was invalid.");
      }
      scheduleItems.push(...pageItems);
      scheduleCursor = payload.next_cursor;
      if (scheduleCursor) {
        seenScheduleCursors.add(scheduleCursor);
      }
      scheduleLoadError = false;
      renderSchedule();
      scheduleStatus(
        scheduleItems.length
          ? "Schedule is up to date."
          : "No shifts were found for this week.",
      );
      return true;
    } catch (error) {
      if (generation !== scheduleGeneration) {
        return false;
      }
      scheduleLoadError = true;
      renderSchedule();
      const message =
        error instanceof Error && error.name === "TimeoutError"
          ? "The schedule request timed out. Check your connection and try again."
          : error instanceof Error && error.message !== "Failed to fetch"
            ? error.message
            : "The schedule could not be loaded. Check your connection and try again.";
      scheduleStatus(message, "error");
      return false;
    }
  }

  async function refreshSchedule(force = false) {
    if (app.dataset.page !== "schedule") {
      return;
    }
    formatScheduleWeek();
    const selected = companies.get(storedCompanyId());
    const companyId = selected?.company_id || null;
    const weekKey = localDateTime(scheduleWeekStart);
    if (
      !force &&
      companyId === scheduleCompanyId &&
      weekKey === scheduleWeekKey
    ) {
      return;
    }
    const generation = ++scheduleGeneration;
    const rosterGeneration = ++scheduleRosterGeneration;
    scheduleCompanyId = companyId;
    scheduleWeekKey = weekKey;
    scheduleItems.length = 0;
    scheduleEmployees.clear();
    scheduleCursor = null;
    scheduleEmployeeCursor = null;
    scheduleLoadError = false;
    scheduleLoading = Boolean(
      companyId &&
        (selected.role === "administrator" || selected.role === "employee"),
    );
    scheduleLoadingMore = false;
    seenScheduleCursors.clear();
    seenScheduleEmployeeCursors.clear();
    renderSchedule();
    if (!companyId) {
      scheduleStatus(
        companies.size
          ? "Choose a company membership to view its schedule."
          : "Choose a company to load its schedule.",
      );
      return;
    }
    if (selected.role !== "administrator" && selected.role !== "employee") {
      scheduleStatus("Your membership cannot view this schedule.", "error");
      return;
    }
    scheduleStatus("Loading the schedule…");
    if (selected.role === "administrator") {
      await loadScheduleEmployeePage(companyId, rosterGeneration);
    }
    try {
      await loadSchedulePage(companyId, generation);
    } finally {
      if (generation === scheduleGeneration) {
        scheduleLoading = false;
        renderSchedule();
      }
    }
  }

  async function createScheduleShift(form) {
    const feedback = form.querySelector("[data-schedule-form-message]");
    feedback.hidden = true;
    const company = companies.get(scheduleCompanyId);
    if (!company || company.role !== "administrator") {
      displayMessage(
        feedback,
        "Choose a company where you are an administrator to create shifts.",
        "error",
      );
      return;
    }
    const startInput = form.querySelector("#schedule-start-at");
    const endInput = form.querySelector("#schedule-end-at");
    const startAt = scheduleInputTimestamp(
      startInput,
      form.querySelector("[data-schedule-start-occurrence]"),
    );
    const endAt = scheduleInputTimestamp(
      endInput,
      form.querySelector("[data-schedule-end-occurrence]"),
    );
    if (!startAt || !endAt || Date.parse(endAt) <= Date.parse(startAt)) {
      displayMessage(
        feedback,
        "Choose valid local times and make sure the end is later than the start.",
        "error",
      );
      return;
    }
    setSubmitting(form, true);
    try {
      const response = await writeJson(
        `/companies/${encodeURIComponent(company.company_id)}/shifts`,
        { start_at: startAt, end_at: endAt },
        { csrf: true },
      );
      const payload = await responsePayload(response);
      if (response.status === 401) {
        clearStoredCompanyId();
        window.location.replace("/login");
        return;
      }
      if (!response.ok) {
        displayMessage(feedback, safeErrorMessage(response, payload), "error");
        return;
      }
      if (
        payload?.company_id !== company.company_id ||
        typeof payload.id !== "string" ||
        payload.assignment !== null ||
        Date.parse(payload.start_at) !== Date.parse(startAt) ||
        Date.parse(payload.end_at) !== Date.parse(endAt)
      ) {
        displayMessage(
          feedback,
          "The shift response could not be verified. Reload the schedule before continuing.",
          "error",
        );
        return;
      }
      scheduleWeekStart.setTime(startOfLocalWeek(new Date(startAt)).getTime());
      form.reset();
      await refreshSchedule(true);
      scheduleStatus("Shift created. Assign an eligible employee when ready.");
    } catch {
      displayMessage(
        feedback,
        "The shift could not be created. Check your connection and try again.",
        "error",
      );
    } finally {
      setSubmitting(form, false);
    }
  }

  async function assignScheduleShift(button) {
    const company = companies.get(scheduleCompanyId);
    const shiftId = button.dataset.scheduleAssign;
    const card = button.closest("[data-schedule-shift]");
    const select = card?.querySelector("[data-schedule-employee-select]");
    const membershipId = select?.value;
    if (
      !company ||
      company.role !== "administrator" ||
      !shiftId ||
      !membershipId ||
      !scheduleEmployees.has(membershipId)
    ) {
      scheduleStatus("Choose an eligible employee before assigning the shift.", "error");
      return;
    }
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
      const response = await writeJson(
        `/companies/${encodeURIComponent(company.company_id)}/shifts/${encodeURIComponent(shiftId)}/assignments`,
        { membership_id: membershipId },
        { csrf: true },
      );
      const payload = await responsePayload(response);
      if (response.status === 401) {
        clearStoredCompanyId();
        window.location.replace("/login");
        return;
      }
      if (!response.ok) {
        scheduleStatus(safeErrorMessage(response, payload), "error");
        return;
      }
      if (
        payload?.company_id !== company.company_id ||
        payload.shift_id !== shiftId ||
        payload.membership_id !== membershipId ||
        payload.display_name !== scheduleEmployees.get(membershipId).display_name
      ) {
        scheduleStatus(
          "The assignment response could not be verified. Reload the schedule before continuing.",
          "error",
        );
        return;
      }
      await refreshSchedule(true);
      scheduleStatus("Employee assigned to the shift.");
    } catch {
      scheduleStatus(
        "The assignment could not be completed. Check your connection and try again.",
        "error",
      );
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }

  function storedCompanyId() {
    if (!selectionLoaded) {
      selectionLoaded = true;
      try {
        currentCompanyId = localStorage.getItem(companyStorageKey);
      } catch {
        companyStatus(
          "Your browser could not restore the selected company. You can still choose one for this page.",
          "error",
        );
      }
    }
    return currentCompanyId;
  }

  function storeCompanyId(companyId) {
    selectionLoaded = true;
    currentCompanyId = companyId;
    try {
      if (companyId) {
        localStorage.setItem(companyStorageKey, companyId);
      } else {
        localStorage.removeItem(companyStorageKey);
      }
    } catch {
      companyStatus(
        "Your selection is active for this page but could not be saved in this browser.",
        "error",
      );
    }
  }

  function clearStoredCompanyId() {
    storeCompanyId(null);
  }

  function renderCompanyContexts() {
    const select = document.querySelector("[data-company-select]");
    const selectedId = storedCompanyId();
    const selected = selectedId ? companies.get(selectedId) : null;
    if (select) {
      select.replaceChildren();
      if (companies.size === 0) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = nextCompanyCursor
          ? "Loading companies…"
          : "No companies yet";
        select.append(option);
        select.disabled = !nextCompanyCursor;
      } else {
        select.disabled = false;
        if (selectedId && !selected) {
          const option = document.createElement("option");
          option.value = selectedId;
          option.textContent = "Selected company — load more to confirm";
          select.append(option);
        }
        for (const company of companies.values()) {
          const option = document.createElement("option");
          option.value = company.company_id;
          option.textContent = company.name;
          select.append(option);
        }
        if (selectedId) {
          select.value = selectedId;
        } else {
          select.value = companies.keys().next().value || "";
        }
      }
    }

    const loadMore = document.querySelector("[data-company-load-more]");
    if (loadMore) {
      loadMore.hidden = !nextCompanyCursor;
      loadMore.disabled = loadingCompanies;
      loadMore.setAttribute("aria-busy", String(loadingCompanies));
    }

    const name = document.querySelector("[data-dashboard-company-name]");
    const description = document.querySelector(
      "[data-dashboard-company-description]",
    );
    const empty = document.querySelector("[data-dashboard-company-empty]");
    const company = selected || companies.values().next().value || null;
    if (name) {
      name.textContent = company
        ? company.name
        : nextCompanyCursor
          ? "Load more companies to continue"
          : "No company selected";
    }
    if (description) {
      description.textContent = company
        ? `Your membership role: ${company.role}. Selection organizes this workspace; ShiftFlow checks access on every request.`
        : nextCompanyCursor
          ? "Your company list is paginated. Open Companies and load another page to find a membership."
          : "Company selection helps organize your workspace. Access is always checked by ShiftFlow.";
    }
    if (empty) {
      empty.hidden = companies.size > 0 || Boolean(nextCompanyCursor);
    }

    const list = document.querySelector("[data-company-list]");
    if (list) {
      list.replaceChildren();
      for (const item of companies.values()) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "company-card";
        button.dataset.companyItem = item.company_id;
        button.setAttribute(
          "aria-pressed",
          String(item.company_id === (selectedId || company?.company_id)),
        );
        const companyName = document.createElement("span");
        companyName.className = "company-card-name";
        companyName.textContent = item.name;
        const role = document.createElement("span");
        role.className = "company-card-role";
        role.textContent = item.role;
        button.append(companyName, role);
        list.append(button);
      }
    }

    const listEmpty = document.querySelector("[data-company-list-empty]");
    if (listEmpty) {
      listEmpty.hidden = companies.size > 0 || Boolean(nextCompanyCursor);
    }
  }

  function loadCompanyPage(cursor = null) {
    if (companyPagePromise) {
      return companyPagePromise;
    }
    loadingCompanies = true;
    renderCompanyContexts();
    companyStatus("Loading your companies…");
    companyPagePromise = Promise.resolve().then(async () => {
      try {
        const query = new URLSearchParams({ limit: "100" });
        if (cursor) {
          query.set("cursor", cursor);
        }
        const response = await fetch(`/companies/mine?${query}`, {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          referrerPolicy: "no-referrer",
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(15000),
        });
        if (response.status === 401) {
          clearStoredCompanyId();
          window.location.replace("/login");
          return false;
        }
        const payload = await responsePayload(response);
        if (!response.ok) {
          companyStatus(safeErrorMessage(response, payload), "error");
          return false;
        }
        if (
          !Array.isArray(payload?.items) ||
          !(
            payload.next_cursor === null ||
            typeof payload.next_cursor === "string"
          )
        ) {
          throw new Error("The company list response was invalid.");
        }
        for (const item of payload.items) {
          if (
            typeof item?.membership_id !== "string" ||
            typeof item?.company_id !== "string" ||
            typeof item?.name !== "string" ||
            typeof item?.role !== "string"
          ) {
            throw new Error("The company list response was invalid.");
          }
          companies.set(item.company_id, {
            membership_id: item.membership_id,
            company_id: item.company_id,
            name: item.name,
            role: item.role,
          });
        }
        if (
          payload.next_cursor &&
          (payload.next_cursor === cursor ||
            seenCompanyCursors.has(payload.next_cursor))
        ) {
          throw new Error("The company list cursor was invalid.");
        }
        nextCompanyCursor = payload.next_cursor;
        if (nextCompanyCursor) {
          seenCompanyCursors.add(nextCompanyCursor);
        }
        const previousSelection = storedCompanyId();
        if (!previousSelection && companies.size > 0) {
          storeCompanyId(companies.keys().next().value);
        } else if (
          previousSelection &&
          !companies.has(previousSelection) &&
          !nextCompanyCursor
        ) {
          storeCompanyId(companies.keys().next().value || null);
        }
        renderCompanyContexts();
        companyStatus(
          companies.size === 0 && !nextCompanyCursor
            ? "You do not belong to any companies yet."
            : "Your company list is up to date.",
        );
        return true;
      } catch {
        companyStatus(
          "We could not load your companies. Check your connection and try again.",
          "error",
        );
        return false;
      } finally {
        loadingCompanies = false;
        companyPagePromise = null;
        renderCompanyContexts();
        if (app.dataset.page === "availability") {
          void refreshAvailability();
        } else if (app.dataset.page === "schedule") {
          void refreshSchedule();
        }
      }
    });
    return companyPagePromise;
  }

  async function refreshCompanies(preferredCompanyId = null) {
    if (companyPagePromise) {
      await companyPagePromise;
    }
    companies.clear();
    nextCompanyCursor = null;
    seenCompanyCursors.clear();
    const loaded = await loadCompanyPage();
    if (!loaded || !preferredCompanyId) {
      return loaded;
    }
    while (!companies.has(preferredCompanyId) && nextCompanyCursor) {
      const cursor = nextCompanyCursor;
      if (!await loadCompanyPage(cursor)) {
        return false;
      }
    }
    if (companies.has(preferredCompanyId)) {
      storeCompanyId(preferredCompanyId);
      renderCompanyContexts();
    }
    return true;
  }

  function showJoiningCode(code) {
    const panel = document.querySelector("[data-joining-code-panel]");
    const output = document.querySelector("[data-joining-code]");
    const status = document.querySelector("[data-joining-code-status]");
    if (!panel || !output || !/^[A-Z0-9]{8}$/.test(code)) {
      throw new Error("The company joining code response was invalid.");
    }
    output.textContent = code;
    if (status) {
      status.textContent = "";
    }
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function hideJoiningCode() {
    const panel = document.querySelector("[data-joining-code-panel]");
    const output = document.querySelector("[data-joining-code]");
    const status = document.querySelector("[data-joining-code-status]");
    if (output) {
      output.textContent = "";
    }
    if (status) {
      status.textContent = "";
    }
    if (panel) {
      panel.hidden = true;
    }
  }

  async function submitCompanyForm(form) {
    const kind = form.dataset.companyForm;
    const endpoint = companyEndpoints[kind];
    if (!endpoint) {
      return;
    }
    const feedback = form.querySelector("[data-form-message]");
    if (feedback) {
      feedback.hidden = true;
      feedback.textContent = "";
      delete feedback.dataset.kind;
    }
    if (kind === "register-company") {
      hideJoiningCode();
    }
    setSubmitting(form, true);
    try {
      const payload = Object.fromEntries(new FormData(form).entries());
      const response = await postJson(endpoint, payload, { csrf: true });
      const body = await responsePayload(response);
      if (response.status === 401) {
        clearStoredCompanyId();
        window.location.replace("/login");
        return;
      }
      if (!response.ok) {
        displayMessage(feedback, safeErrorMessage(response, body), "error");
        return;
      }
      if (
        typeof body?.company_id !== "string" ||
        (kind === "register-company" &&
          (body.role !== "administrator" ||
            typeof body.joining_code !== "string" ||
            !/^[A-Z0-9]{8}$/.test(body.joining_code))) ||
        (kind === "join-company" && body.role !== "employee")
      ) {
        displayMessage(
          feedback,
          "The company response could not be verified. Reload the page before continuing.",
          "error",
        );
        return;
      }
      clearForm(form);
      if (kind === "register-company") {
        showJoiningCode(body.joining_code);
        displayMessage(
          feedback,
          "Company created. You are its administrator.",
          "success",
        );
      } else {
        displayMessage(
          feedback,
          "You joined the company successfully.",
          "success",
        );
      }
      await refreshCompanies(body.company_id);
    } catch {
      displayMessage(
        feedback,
        "We could not complete that request. Check your connection and try again.",
        "error",
      );
    } finally {
      setSubmitting(form, false);
    }
  }

  function setSubmitting(form, submitting) {
    const submit = form.querySelector('[type="submit"]');
    if (submit) {
      if (!submit.dataset.idleLabel) {
        submit.dataset.idleLabel = submit.textContent;
      }
      submit.disabled = submitting;
      submit.textContent = submitting
        ? loadingLabels[form.dataset.authForm] ||
          loadingLabels[form.dataset.availabilityForm] ||
          loadingLabels[form.dataset.scheduleForm] ||
          "Working…"
        : submit.dataset.idleLabel;
    }
    form.setAttribute("aria-busy", String(submitting));
  }

  async function submitAccountForm(form) {
    const kind = form.dataset.authForm;
    const endpoint = endpoints[kind];
    if (!endpoint) {
      return;
    }

    const feedback = form.querySelector("[data-form-message]");
    if (feedback) {
      feedback.hidden = true;
      feedback.textContent = "";
      delete feedback.dataset.kind;
    }
    setSubmitting(form, true);

    try {
      const payload = Object.fromEntries(new FormData(form).entries());
      const response = await postJson(endpoint, payload);
      const body = await responsePayload(response);
      if (!response.ok) {
        displayMessage(feedback, safeErrorMessage(response, body), "error");
        return;
      }

      if (kind === "login") {
        if (
          typeof body?.csrf_token !== "string" ||
          !/^[A-Za-z0-9_-]{43}$/.test(body.csrf_token)
        ) {
          displayMessage(
            feedback,
            "Sign-in could not be completed safely. Please try again.",
            "error",
          );
          return;
        }
        csrfToken = body.csrf_token;
        window.location.replace("/");
        return;
      }

      const successMessages = {
        register:
          "If eligible, an email with a verification code will be sent. Check your inbox, then enter the code on the verification page.",
        "verify-email": "Your email is verified. You can now sign in.",
        "resend-verification":
          "If eligible, an email with a verification code will be sent.",
        "request-reset":
          "If eligible, an email with a password reset code will be sent.",
        "confirm-reset":
          "Your password has been updated. Sign in with your new password.",
      };
      displayMessage(feedback, successMessages[kind], "success");
      clearForm(form);
    } catch {
      displayMessage(
        feedback,
        "We could not reach ShiftFlow. Check your connection and try again.",
        "error",
      );
    } finally {
      setSubmitting(form, false);
    }
  }

  async function signOut(form) {
    const feedback = document.getElementById("session-message");
    const button = form.querySelector('[type="submit"]');
    let buttonLabel = null;
    if (button) {
      buttonLabel = button.textContent;
      button.disabled = true;
      button.textContent = "Signing out…";
    }

    try {
      const response = await postJson("/auth/logout", {}, { csrf: true });
      if (response.status === 204 || response.status === 401) {
        csrfToken = null;
        clearStoredCompanyId();
        window.location.replace("/login");
        return;
      }

      const body = await responsePayload(response);
      sessionMessage(safeErrorMessage(response, body));
    } catch (error) {
      if (
        error instanceof Error &&
        error.message === "Your session has ended. Sign in again to continue."
      ) {
        window.location.replace("/login");
        return;
      }
      sessionMessage("Sign-out could not be completed. Reload the page and try again.");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = buttonLabel;
      }
    }
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    if (form.matches("[data-auth-form]")) {
      event.preventDefault();
      void submitAccountForm(form);
    } else if (form.matches("[data-auth-logout]")) {
      event.preventDefault();
      void signOut(form);
    } else if (form.matches("[data-availability-form]")) {
      event.preventDefault();
      void submitAvailabilityForm(form);
    } else if (form.matches("[data-schedule-create-form]")) {
      event.preventDefault();
      void createScheduleShift(form);
    } else if (form.matches("[data-company-form]")) {
      event.preventDefault();
      void submitCompanyForm(form);
    }
  });

  document.addEventListener("change", (event) => {
    const select = event.target;
    if (!(select instanceof HTMLSelectElement) || !select.matches("[data-company-select]")) {
      return;
    }
    if (!companies.has(select.value)) {
      return;
    }
    storeCompanyId(select.value);
    renderCompanyContexts();
    companyStatus(`Switched to ${companies.get(select.value).name}.`);
    void refreshAvailability();
    void refreshSchedule();
  });

  document.addEventListener("input", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement)) {
      return;
    }
    if (input.matches("#schedule-start-at")) {
      updateScheduleOccurrence(
        input,
        document.querySelector("[data-schedule-start-occurrence-wrap]"),
        document.querySelector("[data-schedule-start-occurrence]"),
      );
    } else if (input.matches("#schedule-end-at")) {
      updateScheduleOccurrence(
        input,
        document.querySelector("[data-schedule-end-occurrence-wrap]"),
        document.querySelector("[data-schedule-end-occurrence]"),
      );
    }
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    if (target.closest("[data-availability-previous]")) {
      availabilityWeekStart.setDate(availabilityWeekStart.getDate() - 7);
      void refreshAvailability(true);
      return;
    }
    if (target.closest("[data-availability-next]")) {
      availabilityWeekStart.setDate(availabilityWeekStart.getDate() + 7);
      void refreshAvailability(true);
      return;
    }
    if (target.closest("[data-availability-today]")) {
      availabilityWeekStart.setTime(startOfLocalWeek(new Date()).getTime());
      void refreshAvailability(true);
      return;
    }
    if (target.closest("[data-availability-load-more]")) {
      if (availabilityCursor && availabilityCompanyId) {
        const cursor = availabilityCursor;
        availabilityLoadingMore = true;
        renderAvailability();
        void loadAvailabilityPage(
          availabilityCompanyId,
          availabilityGeneration,
          cursor,
        ).finally(() => {
          availabilityLoadingMore = false;
          renderAvailability();
        });
      }
      return;
    }
    if (target.closest("[data-availability-retry]")) {
      void refreshAvailability(true);
      return;
    }
    const availabilityEdit = target.closest("[data-availability-edit]");
    if (availabilityEdit instanceof HTMLButtonElement) {
      editAvailability(availabilityEdit.dataset.availabilityEdit);
      return;
    }
    if (target.closest("[data-availability-cancel]")) {
      resetAvailabilityForm();
      document.querySelector("#availability-form-title").focus();
      return;
    }
    if (target.closest("[data-schedule-previous]")) {
      scheduleWeekStart.setDate(scheduleWeekStart.getDate() - 7);
      void refreshSchedule(true);
      return;
    }
    if (target.closest("[data-schedule-next]")) {
      scheduleWeekStart.setDate(scheduleWeekStart.getDate() + 7);
      void refreshSchedule(true);
      return;
    }
    if (target.closest("[data-schedule-today]")) {
      scheduleWeekStart.setTime(startOfLocalWeek(new Date()).getTime());
      void refreshSchedule(true);
      return;
    }
    if (target.closest("[data-schedule-load-more]")) {
      if (scheduleCursor && scheduleCompanyId) {
        const cursor = scheduleCursor;
        scheduleLoadingMore = true;
        renderSchedule();
        void loadSchedulePage(
          scheduleCompanyId,
          scheduleGeneration,
          cursor,
        ).finally(() => {
          scheduleLoadingMore = false;
          renderSchedule();
        });
      }
      return;
    }
    if (target.closest("[data-schedule-roster-load-more]")) {
      if (scheduleEmployeeCursor && scheduleCompanyId) {
        void loadScheduleEmployeePage(
          scheduleCompanyId,
          scheduleRosterGeneration,
          scheduleEmployeeCursor,
        );
      }
      return;
    }
    if (target.closest("[data-schedule-retry]")) {
      void refreshSchedule(true);
      return;
    }
    const scheduleAssign = target.closest("[data-schedule-assign]");
    if (scheduleAssign instanceof HTMLButtonElement) {
      void assignScheduleShift(scheduleAssign);
      return;
    }
    const companyButton = target.closest("[data-company-item]");
    if (companyButton instanceof HTMLButtonElement) {
      const companyId = companyButton.dataset.companyItem;
      if (companyId && companies.has(companyId)) {
        storeCompanyId(companyId);
        renderCompanyContexts();
        companyStatus(`Switched to ${companies.get(companyId).name}.`);
        void refreshAvailability();
        void refreshSchedule();
      }
      return;
    }
    if (target.closest("[data-company-load-more]")) {
      if (nextCompanyCursor) {
        void loadCompanyPage(nextCompanyCursor);
      }
      return;
    }
    if (target.closest("[data-copy-joining-code]")) {
      const output = document.querySelector("[data-joining-code]");
      const status = document.querySelector("[data-joining-code-status]");
      const code = output?.textContent || "";
      if (!code || !status) {
        return;
      }
      void (async () => {
        try {
          if (!navigator.clipboard?.writeText) {
            throw new Error("Clipboard access is unavailable.");
          }
          await navigator.clipboard.writeText(code);
          status.textContent = "Code copied. Share it only with intended employees.";
        } catch {
          status.textContent = "Could not copy the code. Select it and copy it manually.";
        }
      })();
      return;
    }
    if (target.closest("[data-hide-joining-code]")) {
      hideJoiningCode();
    }
  });

  window.addEventListener("pagehide", hideJoiningCode);

  if (app.dataset.authenticated === "true") {
    if (app.dataset.page === "availability") {
      formatAvailabilityWeek();
      renderAvailability();
    } else if (app.dataset.page === "schedule") {
      formatScheduleWeek();
      renderSchedule();
      updateScheduleOccurrence(
        document.querySelector("#schedule-start-at"),
        document.querySelector("[data-schedule-start-occurrence-wrap]"),
        document.querySelector("[data-schedule-start-occurrence]"),
      );
      updateScheduleOccurrence(
        document.querySelector("#schedule-end-at"),
        document.querySelector("[data-schedule-end-occurrence-wrap]"),
        document.querySelector("[data-schedule-end-occurrence]"),
      );
    }
    bootstrapCsrfToken().catch((error) => {
      sessionMessage(
        error instanceof Error
          ? error.message
          : "Session security could not be refreshed. Reload the page.",
      );
    });
    void loadCompanyPage();
  }
})();

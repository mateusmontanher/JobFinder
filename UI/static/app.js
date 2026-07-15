"use strict";

const list = document.querySelector("#job-list");
const status = document.querySelector("#status");
const template = document.querySelector("#job-card-template");
const refreshButton = document.querySelector("#refresh-jobs");
const languageSelect = document.querySelector("#language-select");

let catalog = {locale: "en", html_language: "en", languages: [{code: "en", name: "English"}], messages: {}, plurals: {}};
let currentJobs = [];
let statusState = {message: "Loading jobs…", values: {}};

function interpolate(message, values = {}) {
  return message.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (match, key) => (
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match
  ));
}

function translate(message, values = {}) {
  return interpolate(catalog.messages[message] || message, values);
}

function translatePlural(name, count) {
  const forms = catalog.plurals[name] || {};
  const category = new Intl.PluralRules(catalog.html_language || "en").select(count);
  return interpolate(forms[category] || forms.other || "{count} jobs loaded.", {count});
}

function translateTree(root) {
  root.querySelectorAll("[data-i18n]").forEach((element) => {
    element.textContent = translate(element.dataset.i18n);
  });
  root.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
    element.setAttribute("aria-label", translate(element.dataset.i18nAriaLabel));
  });
}

function setStatus(message, values = {}) {
  statusState = {message, values};
  status.textContent = translate(message, values);
}

function setLoadedStatus() {
  if (currentJobs.length) {
    statusState = {plural: "jobs_loaded", count: currentJobs.length};
    status.textContent = translatePlural("jobs_loaded", currentJobs.length);
  } else {
    setStatus("No jobs are currently available.");
  }
}

function refreshStatusTranslation() {
  if (statusState.plural) status.textContent = translatePlural(statusState.plural, statusState.count);
  else status.textContent = translate(statusState.message, statusState.values);
}

function safePostingUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch (_error) {
    return null;
  }
}

function applyRating(card, rating) {
  card.querySelector(".great-button").setAttribute("aria-pressed", String(rating === "great"));
  card.querySelector(".bad-button").setAttribute("aria-pressed", String(rating === "bad"));
  card.dataset.rating = rating || "";
}

async function updateRating(card, job, requestedRating) {
  const previous = card.dataset.rating || null;
  const next = previous === requestedRating ? null : requestedRating;
  const buttons = card.querySelectorAll(".rating-button");
  buttons.forEach((button) => { button.disabled = true; });
  applyRating(card, next);
  try {
    const response = await fetch(`/api/jobs/${job.id}/rating`, {
      method: next ? "PUT" : "DELETE",
      credentials: "same-origin",
      headers: next ? {"Content-Type": "application/json"} : {},
      body: next ? JSON.stringify({rating: next}) : null,
    });
    if (!response.ok) throw new Error(`rating request failed (${response.status})`);
    setStatus(next ? "Rating saved locally." : "Rating removed.");
  } catch (_error) {
    applyRating(card, previous);
    setStatus("The rating could not be saved. Please try again.");
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function createCard(job, index) {
  const card = template.content.firstElementChild.cloneNode(true);
  translateTree(card);
  card.querySelector(".company").textContent = job.company || translate("Company not provided");
  card.querySelector(".title").textContent = job.title || translate("Untitled opening");
  card.querySelector(".location").textContent = job.location || translate("Location not provided");
  card.querySelector(".match-score").textContent = translate("{percent}% match", {percent: Number(job.similarity_percent) || 0});
  card.querySelector(".description-content").textContent = job.description || translate("No description available.");
  applyRating(card, job.rating);

  const link = card.querySelector(".job-link");
  const postingUrl = safePostingUrl(job.url);
  if (postingUrl) link.href = postingUrl;
  else link.hidden = true;

  const panel = card.querySelector(".description-panel");
  const descriptionButton = card.querySelector(".description-button");
  const panelId = `job-description-${index}`;
  panel.id = panelId;
  descriptionButton.setAttribute("aria-controls", panelId);
  descriptionButton.addEventListener("click", () => {
    const expanded = descriptionButton.getAttribute("aria-expanded") !== "true";
    descriptionButton.setAttribute("aria-expanded", String(expanded));
    panel.setAttribute("aria-hidden", String(!expanded));
    panel.classList.toggle("is-expanded", expanded);
  });

  card.querySelector(".great-button").addEventListener("click", () => updateRating(card, job, "great"));
  card.querySelector(".bad-button").addEventListener("click", () => updateRating(card, job, "bad"));
  return card;
}

function renderJobs() {
  list.replaceChildren(...currentJobs.map(createCard));
}

function populateLanguageSelect() {
  languageSelect.replaceChildren(...catalog.languages.map((language) => {
    const option = document.createElement("option");
    option.value = language.code;
    option.textContent = language.name;
    return option;
  }));
  languageSelect.value = catalog.locale;
}

async function loadTranslations(requestedLocale) {
  const safeLocale = /^[A-Za-z]{2,3}(?:[-_][A-Za-z]{2})?$/.test(requestedLocale) ? requestedLocale : "en";
  const response = await fetch(`/api/i18n/${encodeURIComponent(safeLocale)}`, {credentials: "same-origin"});
  if (!response.ok) throw new Error(`translation request failed (${response.status})`);
  catalog = await response.json();
  document.documentElement.lang = catalog.html_language;
  document.title = translate("JobFinder results");
  translateTree(document);
  populateLanguageSelect();
  renderJobs();
  refreshStatusTranslation();
}

async function loadJobs() {
  list.setAttribute("aria-busy", "true");
  refreshButton.disabled = true;
  setStatus("Loading jobs…");
  try {
    const response = await fetch("/api/jobs", {credentials: "same-origin"});
    if (!response.ok) throw new Error(`jobs request failed (${response.status})`);
    const payload = await response.json();
    currentJobs = payload.jobs;
    renderJobs();
    setLoadedStatus();
  } catch (_error) {
    currentJobs = [];
    renderJobs();
    setStatus("Jobs could not be loaded. Check the local database connection and try again.");
  } finally {
    list.setAttribute("aria-busy", "false");
    refreshButton.disabled = false;
  }
}

languageSelect.addEventListener("change", async () => {
  try {
    await loadTranslations(languageSelect.value);
  } catch (_error) {
    setStatus("The action could not be completed. See logs/app.log.");
  }
});
refreshButton.addEventListener("click", loadJobs);

(async () => {
  try {
    await loadTranslations(navigator.language || "en");
  } catch (_error) {
    populateLanguageSelect();
  }
  await loadJobs();
})();

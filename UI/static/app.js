"use strict";

const list = document.querySelector("#job-list");
const status = document.querySelector("#status");
const template = document.querySelector("#job-card-template");
const refreshButton = document.querySelector("#refresh-jobs");

function setStatus(message) {
  status.textContent = message;
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
  card.querySelector(".company").textContent = job.company || "Company not provided";
  card.querySelector(".title").textContent = job.title || "Untitled opening";
  card.querySelector(".location").textContent = job.location || "Location not provided";
  card.querySelector(".match-score").textContent = `${Number(job.similarity_percent) || 0}% match`;
  card.querySelector(".description-content").textContent = job.description || "No description available.";
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

async function loadJobs() {
  list.setAttribute("aria-busy", "true");
  refreshButton.disabled = true;
  setStatus("Loading jobs…");
  try {
    const response = await fetch("/api/jobs", {credentials: "same-origin"});
    if (!response.ok) throw new Error(`jobs request failed (${response.status})`);
    const payload = await response.json();
    list.replaceChildren(...payload.jobs.map(createCard));
    setStatus(payload.jobs.length ? `${payload.jobs.length} jobs loaded.` : "No jobs are currently available.");
  } catch (_error) {
    list.replaceChildren();
    setStatus("Jobs could not be loaded. Check the local database connection and try again.");
  } finally {
    list.setAttribute("aria-busy", "false");
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener("click", loadJobs);
loadJobs();

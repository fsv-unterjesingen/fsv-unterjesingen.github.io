const state = {
  items: [],
  selectedPath: "",
  dirty: false,
  cleanFormSnapshot: null,
};

const elements = {
  stats: document.getElementById("stats"),
  grid: document.getElementById("grid"),
  cardTemplate: document.getElementById("cardTemplate"),
  editorDialog: document.getElementById("editorDialog"),
  editorShell: document.getElementById("editorShell"),
  closeDialogButton: document.getElementById("closeDialogButton"),
  selectedTitle: document.getElementById("selectedTitle"),
  selectedPath: document.getElementById("selectedPath"),
  previewImage: document.getElementById("previewImage"),
  previewVideo: document.getElementById("previewVideo"),
  metadataForm: document.getElementById("metadataForm"),
  saveButton: document.getElementById("saveButton"),
  deleteButton: document.getElementById("deleteButton"),
  saveState: document.getElementById("saveState"),
  readonlyMeta: document.getElementById("readonlyMeta"),
  searchInput: document.getElementById("searchInput"),
  missingAltFilter: document.getElementById("missingAltFilter"),
  duplicateFilter: document.getElementById("duplicateFilter"),
  refreshButton: document.getElementById("refreshButton"),
  uploadInput: document.getElementById("uploadInput"),
  fieldTitle: document.getElementById("fieldTitle"),
  fieldAltLabel: document.getElementById("fieldAltLabel"),
  fieldAlt: document.getElementById("fieldAlt"),
  fieldDescription: document.getElementById("fieldDescription"),
  fieldTags: document.getElementById("fieldTags"),
};

function setSaveState(message, isDirty = false) {
  elements.saveState.textContent = message || "";
  elements.saveState.classList.toggle("is-dirty", isDirty);
}

function formSnapshot() {
  return {
    title: elements.fieldTitle.value,
    alt: elements.fieldAlt.value,
    description: elements.fieldDescription.value,
    tags: elements.fieldTags.value,
  };
}

function snapshotForItem(item) {
  return {
    title: item.title || "",
    alt: item.alt || "",
    description: item.description || "",
    tags: (item.tags || []).join(", "),
  };
}

function snapshotsMatch(left, right) {
  return (
    left !== null &&
    right !== null &&
    left.title === right.title &&
    left.alt === right.alt &&
    left.description === right.description &&
    left.tags === right.tags
  );
}

function syncDirtyState() {
  if (!state.selectedPath || state.cleanFormSnapshot === null) {
    state.dirty = false;
    setSaveState("");
    return;
  }

  state.dirty = !snapshotsMatch(formSnapshot(), state.cleanFormSnapshot);
  setSaveState(state.dirty ? "Ungespeicherte Änderungen" : "", state.dirty);
}

function currentFilters() {
  const params = new URLSearchParams();
  if (elements.searchInput.value.trim()) {
    params.set("q", elements.searchInput.value.trim());
  }
  if (elements.missingAltFilter.checked) {
    params.set("missing_alt", "1");
  }
  if (elements.duplicateFilter.checked) {
    params.set("duplicates", "1");
  }
  return params.toString();
}

function formatMeta(item) {
  const badges = [];
  if (item.media_kind === "video") {
    badges.push('<span class="badge">Video</span>');
  }
  if (item.missing_alt) {
    badges.push('<span class="badge is-warning">Alt fehlt</span>');
  }
  if ((item.duplicate_group_size || 0) > 1) {
    badges.push(`<span class="badge">Duplikate ${item.duplicate_group_size}</span>`);
  }
  if (item.date) {
    badges.push(`<span class="badge">${item.date.slice(0, 10)}</span>`);
  }
  return badges.join("");
}

function selectedItem() {
  return state.items.find((item) => item.media_path === state.selectedPath) || null;
}

function renderGrid() {
  elements.grid.innerHTML = "";
  for (const item of state.items) {
    const fragment = elements.cardTemplate.content.cloneNode(true);
    const button = fragment.querySelector(".card");
    const image = fragment.querySelector(".cardThumb");
    const title = fragment.querySelector(".cardTitle");
    const meta = fragment.querySelector(".cardMeta");

    button.dataset.path = item.media_path;
    button.classList.toggle("is-selected", item.media_path === state.selectedPath);
    button.addEventListener("click", () => selectItem(item.media_path));

    image.src = `${item.thumb_url}?size=280`;
    image.alt = item.title || item.original_filename || "";
    title.textContent = item.title || item.original_filename || item.media_path;
    meta.innerHTML = formatMeta(item);

    elements.grid.appendChild(fragment);
  }
}

function renderReadonly(item) {
  const dimensions = item.width && item.height ? `${item.width} × ${item.height}` : "";
  const entries = [
    ["Typ", item.media_kind === "video" ? "Video" : "Bild"],
    ["Pfad", item.bundle_dir],
    ["Originaldatei", item.original_filename || ""],
    ["Mediendatei", item.original_path],
    ["Alte URL", item.old_url || ""],
    ["Auflösung", dimensions],
    ["Dateigröße", item.filesize || ""],
    ["MIME", item.mime_type || ""],
    ["SHA-256", item.sha256 || ""],
    ["WordPress ID", item.wordpress_id || ""],
    ["Duplikatgruppe", item.duplicate_group_size > 1 ? String(item.duplicate_group_size) : ""],
  ];

  elements.readonlyMeta.innerHTML = entries
    .filter(([, value]) => value !== "" && value !== null && value !== undefined)
    .map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`)
    .join("");
}

function clearPreview() {
  elements.previewImage.hidden = true;
  elements.previewImage.removeAttribute("src");
  elements.previewImage.alt = "";
  elements.previewVideo.pause();
  elements.previewVideo.hidden = true;
  elements.previewVideo.removeAttribute("src");
  elements.previewVideo.load();
}

function showPreview(item) {
  clearPreview();
  if (item.media_kind === "video") {
    elements.previewVideo.src = item.image_url;
    elements.previewVideo.hidden = false;
    return;
  }
  elements.previewImage.src = item.image_url;
  elements.previewImage.alt = item.alt || item.title || item.original_filename || "";
  elements.previewImage.hidden = false;
}

function fillForm(item) {
  elements.selectedTitle.textContent = item.title || item.original_filename || item.media_path;
  elements.selectedPath.textContent = item.media_path;
  showPreview(item);

  elements.fieldTitle.value = item.title || "";
  elements.fieldAlt.value = item.alt || "";
  elements.fieldDescription.value = item.description || "";
  elements.fieldTags.value = (item.tags || []).join(", ");
  elements.fieldAltLabel.hidden = item.media_kind !== "image";

  renderReadonly(item);
  state.cleanFormSnapshot = snapshotForItem(item);
  syncDirtyState();
}

function openEditor(item) {
  if (!item) {
    return;
  }
  fillForm(item);
  if (!elements.editorDialog.open) {
    elements.editorDialog.showModal();
  }
}

function closeEditor({ confirmDirty = true } = {}) {
  if (confirmDirty && state.dirty) {
    const confirmed = window.confirm("Ungespeicherte Änderungen verwerfen?");
    if (!confirmed) {
      return false;
    }
  }

  state.selectedPath = "";
  state.dirty = false;
  state.cleanFormSnapshot = null;
  setSaveState("");
  clearPreview();
  if (elements.editorDialog.open) {
    elements.editorDialog.close();
  }
  renderGrid();
  return true;
}

async function loadItems(preserveSelection = true) {
  const response = await fetch(`/api/images?${currentFilters()}`);
  if (!response.ok) {
    throw new Error(`Konnte Medien nicht laden (${response.status})`);
  }
  const payload = await response.json();
  state.items = payload.items || [];
  elements.stats.textContent = `${state.items.length} Medien`;

  if (!preserveSelection || !state.items.some((item) => item.media_path === state.selectedPath)) {
    state.selectedPath = "";
  }

  renderGrid();
  const item = selectedItem();
  if (item) {
    openEditor(item);
  } else if (elements.editorDialog.open) {
    elements.editorDialog.close();
  }
}

function markDirty() {
  if (!state.selectedPath) return;
  syncDirtyState();
}

function selectItem(path) {
  if (state.dirty && path !== state.selectedPath) {
    const confirmed = window.confirm("Ungespeicherte Änderungen verwerfen?");
    if (!confirmed) return;
  }
  state.selectedPath = path;
  renderGrid();
  openEditor(selectedItem());
}

async function saveSelected(event) {
  event.preventDefault();
  const item = selectedItem();
  if (!item) return;

  elements.saveButton.disabled = true;
  elements.deleteButton.disabled = true;
  setSaveState("Speichert …");

  const payload = {
    path: item.media_path,
    title: elements.fieldTitle.value.trim(),
    alt: elements.fieldAlt.value.trim(),
    description: elements.fieldDescription.value.trim(),
    tags: elements.fieldTags.value.trim(),
  };

  const response = await fetch("/api/item/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  elements.saveButton.disabled = false;
  elements.deleteButton.disabled = false;
  if (!response.ok) {
    const error = await response.text();
    setSaveState(`Fehler: ${error}`, true);
    return;
  }

  const result = await response.json();
  state.selectedPath = result.item.media_path;
  await loadItems(true);
  setSaveState("Gespeichert");
}

async function deleteSelected() {
  const item = selectedItem();
  if (!item) return;

  const label = item.title || item.original_filename || item.media_path;
  const confirmed = window.confirm(`"${label}" wirklich löschen?`);
  if (!confirmed) {
    return;
  }

  elements.saveButton.disabled = true;
  elements.deleteButton.disabled = true;
  setSaveState("Löscht …");

  const response = await fetch("/api/item/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: item.media_path }),
  });

  elements.saveButton.disabled = false;
  elements.deleteButton.disabled = false;
  if (!response.ok) {
    const error = await response.text();
    setSaveState(`Fehler: ${error}`, true);
    return;
  }

  closeEditor({ confirmDirty: false });
  await loadItems(false);
}

async function uploadFiles(event) {
  const files = [...event.target.files];
  if (files.length === 0) {
    return;
  }

  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }

  setSaveState("Importiert …");
  const response = await fetch("/api/upload", {
    method: "POST",
    body: formData,
  });

  elements.uploadInput.value = "";

  if (!response.ok) {
    setSaveState(`Import fehlgeschlagen (${response.status})`);
    return;
  }

  const payload = await response.json();
  state.selectedPath = payload.created?.[0] || "";
  await loadItems(true);
  setSaveState(`${payload.created.length} Datei(en) importiert`);
}

elements.metadataForm.addEventListener("submit", saveSelected);
elements.metadataForm.addEventListener("input", markDirty);
elements.searchInput.addEventListener("input", () => loadItems(false));
elements.missingAltFilter.addEventListener("change", () => loadItems(false));
elements.duplicateFilter.addEventListener("change", () => loadItems(false));
elements.refreshButton.addEventListener("click", () => loadItems(true));
elements.uploadInput.addEventListener("change", uploadFiles);
elements.deleteButton.addEventListener("click", deleteSelected);
elements.closeDialogButton.addEventListener("click", () => {
  closeEditor();
});
elements.editorDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeEditor();
});

loadItems(false).catch((error) => {
  elements.stats.textContent = "Fehler";
  elements.grid.innerHTML = `<p>${error.message}</p>`;
});

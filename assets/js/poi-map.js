import maplibregl from "maplibre-gl";
import params from "@params";

const config = params || {};

const {
  id = "poi-map",
  zoom = 12,
  points = [],
  styleUrl = "https://styles.trailsta.sh/openmaptiles-osm.json"
} = config;

let pointsList = points;
if (typeof pointsList === "string") {
  try {
    pointsList = JSON.parse(pointsList);
  } catch {
    pointsList = [];
  }
}
if (!Array.isArray(pointsList)) {
  pointsList = [];
}

const container = document.getElementById(id);
if (!container || container.dataset.mapReady === "true") {
  // no-op
} else {
  container.dataset.mapReady = "true";

  let mapStyle = styleUrl;
  if (styleUrl.includes("styles.trailsta.sh/openmaptiles-osm.json")) {
    const localStyleUrl = new URL(`/tiles/style/${styleUrl.split("/").pop()}`, window.location.origin).toString();
    try {
      const res = await fetch(localStyleUrl);
      if (res.ok) {
        const styleJson = await res.json();
        if (styleJson && typeof styleJson === "object") {
          if (typeof styleJson.sprite === "string" && styleJson.sprite.startsWith("/")) {
            styleJson.sprite = `${window.location.origin}${styleJson.sprite}`;
          }
          if (typeof styleJson.glyphs === "string" && styleJson.glyphs.startsWith("/")) {
            styleJson.glyphs = `${window.location.origin}${styleJson.glyphs}`;
          }
          if (styleJson.sources && typeof styleJson.sources === "object") {
            Object.values(styleJson.sources).forEach((source) => {
              if (!source || typeof source !== "object") return;
              if (typeof source.url === "string" && source.url.startsWith("/")) {
                source.url = `${window.location.origin}${source.url}`;
              }
            });
          }
        }
        mapStyle = styleJson;
      }
    } catch {
      // keep remote style fallback
    }
  }

  const initialCenter = pointsList.length ? [pointsList[0].lon, pointsList[0].lat] : [0, 0];

  const map = new maplibregl.Map({
    container: id,
    style: mapStyle,
    center: initialCenter,
    zoom,
    attributionControl: true
  });

  map.addControl(new maplibregl.NavigationControl({showCompass: false}), "top-right");

  map.on("load", () => {
    if (pointsList.length === 0) return;
    const bounds = new maplibregl.LngLatBounds();
    let activePopup = null;
    pointsList.forEach((point) => {
      const marker = new maplibregl.Marker({color: "#d93025"}).setLngLat([point.lon, point.lat]);
      if (point.title) {
        const popup = new maplibregl.Popup({
          offset: 12,
          closeButton: false,
          closeOnClick: false
        }).setText(point.title);
        const el = marker.getElement();
        const hitbox = document.createElement("span");
        hitbox.style.position = "absolute";
        hitbox.style.top = "-20px";
        hitbox.style.bottom = "-12px";
        hitbox.style.left = "-20px";
        hitbox.style.right = "-20px";
        hitbox.style.background = "transparent";
        hitbox.style.pointerEvents = "auto";
        el.appendChild(hitbox);
        hitbox.addEventListener("mouseenter", () => {
          if (activePopup && activePopup !== popup) {
            activePopup.remove();
          }
          popup.addTo(map).setLngLat([point.lon, point.lat]);
          activePopup = popup;
        });
        hitbox.addEventListener("click", () => {
          if (activePopup && activePopup !== popup) {
            activePopup.remove();
          }
          popup.addTo(map).setLngLat([point.lon, point.lat]);
          activePopup = popup;
        });
      }
      marker.addTo(map);
      bounds.extend([point.lon, point.lat]);
    });
    if (pointsList.length > 1) {
      map.fitBounds(bounds, {padding: 40, maxZoom: zoom});
    }
  });
}

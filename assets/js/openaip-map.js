import maplibregl from "maplibre-gl";
import params from "@params";

const config = params || {};

const {
  id = "openaip-map",
  lat = 48.546944,
  lon = 8.946111,
  zoom = 9,
  tileUrl = "",
  iconList = [],
  bounds = {west: 8.2, south: 48.0, east: 9.7, north: 49.0},
  styleUrl = "https://styles.trailsta.sh/openmaptiles-osm.json"
} = config;

const container = document.getElementById(id);
if (!container || container.dataset.mapReady === "true") {
  // no-op
} else {
  container.dataset.mapReady = "true";

  const localStyleUrl = new URL(`/tiles/style/${styleUrl.split("/").pop()}`, window.location.origin).toString();
  let mapStyle = styleUrl;

  const loadStyle = async () => {
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
  };

  const localBounds = bounds;
  const tileWithinBounds = (z, x, y) => {
    const n = Math.pow(2, z);
    const lon1 = (x / n) * 360 - 180;
    const lon2 = ((x + 1) / n) * 360 - 180;
    const lat1 = (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI;
    const lat2 = (Math.atan(Math.sinh(Math.PI * (1 - (2 * (y + 1)) / n))) * 180) / Math.PI;
    const tile = {
      west: lon1,
      east: lon2,
      south: Math.min(lat1, lat2),
      north: Math.max(lat1, lat2)
    };
    return (
      tile.west < localBounds.east &&
      tile.east > localBounds.west &&
      tile.south < localBounds.north &&
      tile.north > localBounds.south
    );
  };
  const parseTileCoords = (url) => {
    const match = url.match(/\/(\d+)\/(\d+)\/(\d+)\.pbf/);
    if (!match) return null;
    return {
      z: Number.parseInt(match[1], 10),
      x: Number.parseInt(match[2], 10),
      y: Number.parseInt(match[3], 10)
    };
  };

  const loadCacheManifest = async () => {
    try {
      const res = await fetch(new URL("/tiles/index.json", window.location.origin));
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.openfreemap)) {
          window.__openfreemapCachedTiles = new Set(data.openfreemap);
        }
        if (Array.isArray(data.openaip)) {
          window.__openaipCachedTiles = new Set(data.openaip);
        }
      }
    } catch {
      // ignore cache manifest failures
    }
  };

  const init = async () => {
    await Promise.all([loadStyle(), loadCacheManifest()]);

    const map = new maplibregl.Map({
      container: id,
      style: mapStyle,
      center: [lon, lat],
      zoom,
      attributionControl: false,
      transformRequest: (url, resourceType) => {
        if (resourceType !== "Tile") return {url};
        if (!url || !url.includes(".pbf")) return {url};
        let parsed;
        try {
          parsed = parseTileCoords(url);
        } catch {
          return {url};
        }
        if (!parsed) return {url};
        if (!tileWithinBounds(parsed.z, parsed.x, parsed.y)) return {url};

        const tileKey = `${parsed.z}/${parsed.x}/${parsed.y}`;
        if (url.includes("openaip.net")) {
          if (!window.__openaipCachedTiles || !window.__openaipCachedTiles.has(tileKey)) {
            return {url};
          }
          return {
            url: new URL(
              `/tiles/openaip/${parsed.z}/${parsed.x}/${parsed.y}.pbf`,
              window.location.origin
            ).toString()
          };
        }
        if (window.__openfreemapCachedTiles && window.__openfreemapCachedTiles.has(tileKey)) {
          return {
            url: new URL(
              `/tiles/openfreemap/${parsed.z}/${parsed.x}/${parsed.y}.pbf`,
              window.location.origin
            ).toString()
          };
        }
        return {url};
      }
    });

    map.addControl(new maplibregl.NavigationControl({showCompass: false}), "top-right");
    const customAttributionControl = {
      onAdd: () => {
        const wrapper = document.createElement("div");
        wrapper.className = "maplibregl-ctrl maplibregl-ctrl-attrib maplibregl-compact";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "maplibregl-ctrl-attrib-button";
        button.setAttribute("aria-label", "Datenquellen anzeigen/verbergen");
        const inner = document.createElement("div");
        inner.className = "maplibregl-ctrl-attrib-inner";
        inner.innerHTML =
          '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> | ' +
          '<a href="https://www.openaip.net/" target="_blank" rel="noopener">OpenAIP</a> | ' +
          "Luftrauminformationen ohne Gewähr";
        button.addEventListener("click", (e) => {
          e.stopPropagation();
          wrapper.classList.toggle("maplibregl-compact-show");
        });
        wrapper.append(button, inner);
        return wrapper;
      },
      onRemove: () => {}
    };
    map.addControl(customAttributionControl, "bottom-right");

    const missingIcons = new Set();
    const loadingIcons = new Set();
    const transparentCanvas = document.createElement("canvas");
    transparentCanvas.width = 1;
    transparentCanvas.height = 1;
    const addTransparentIcon = (name) => {
      if (!name || map.hasImage(name)) return;
      try {
        map.addImage(name, transparentCanvas, {pixelRatio: 1});
      } catch {
        // ignore duplicate
      }
    };
    const iconBase = "/openaip/svg/";
    const isPatternIcon = (name) => name.startsWith("diagonal_lines_");
    const isRoseIcon = (name) => name.startsWith("navaid_rose");
    const loadSvgIcon = (name) =>
      new Promise((resolve) => {
        if (!name || missingIcons.has(name) || loadingIcons.has(name)) {
          resolve();
          return;
        }
        if (map.hasImage(name)) {
          resolve();
          return;
        }
        loadingIcons.add(name);
        const url = `${window.location.origin}${iconBase}${name}.svg`;
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => {
          try {
            if (!map.hasImage(name)) {
              if (isRoseIcon(name)) {
                map.addImage(name, img, {sdf: false, pixelRatio: 2});
              } else {
                map.addImage(name, img, {sdf: !isPatternIcon(name)});
              }
            }
          } catch {
            missingIcons.add(name);
          } finally {
            loadingIcons.delete(name);
            resolve();
          }
        };
        img.onerror = () => {
          missingIcons.add(name);
          loadingIcons.delete(name);
          resolve();
        };
        img.src = url;
      });

    map.on("styleimagemissing", (e) => {
      const rawName = e.id;
      if (!rawName) return;
      const name = String(rawName).trim();
      if (!name) {
        addTransparentIcon(String(rawName));
        return;
      }
      addTransparentIcon(name);
      loadSvgIcon(name);
    });

    map.on("load", () => {
      addTransparentIcon(" ");
      let iconNames = iconList;
      if (typeof iconNames === "string") {
        try {
          iconNames = JSON.parse(iconNames);
        } catch {
          iconNames = [];
        }
      }
      if (!Array.isArray(iconNames)) {
        iconNames = Object.values(iconNames || {});
      }
      const loadIcons = iconNames.map((name) => loadSvgIcon(String(name).trim()));

      map.addSource("openaip-data", {
        type: "vector",
        tiles: [tileUrl],
        attribution:
          '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> | ' +
          '<a href="https://www.openaip.net/" target="_blank" rel="noopener">OpenAIP</a> | ' +
          "Luftrauminformationen ohne Gewähr"
      });
      map.addSource("point-features", {
        type: "geojson",
        data: {type: "FeatureCollection", features: []}
      });
      map.addSource("polygon-features", {
        type: "geojson",
        data: {type: "FeatureCollection", features: []}
      });
      map.addSource("adhoc-polygon-features", {
        type: "geojson",
        data: {type: "FeatureCollection", features: []}
      });

      const openAipLayers = config.openAipLayers || [];

      Promise.all(loadIcons).then(() => {
        const fontFallback = ["Open Sans Regular", "Arial Unicode MS Regular"];
        openAipLayers.forEach((layer) => {
          if (layer.id && map.getLayer(layer.id)) return;
          if (layer.layout && layer.layout["text-font"]) {
            layer.layout["text-font"] = fontFallback;
          }
          map.addLayer(layer);
        });

        new maplibregl.Marker({color: "#1d6ee8"}).setLngLat([lon, lat]).addTo(map);
      });
    });
  };

  init();
}

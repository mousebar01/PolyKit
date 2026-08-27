import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "PolyKit Agent",
    short_name: "PolyKit Agent",
    description: "Local web interface for the pi coding agent",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#1a1a1a",
    theme_color: "#1a1a1a",
    orientation: "any",
    categories: ["developer", "productivity"],
    lang: "zh-CN",
    icons: [
      {
        src: "/icons/icon-192.png?v=polykit-agent-2",
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icons/icon-512.png?v=polykit-agent-2",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
    ],
  };
}

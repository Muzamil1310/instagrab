import { defineConfig } from "vite";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  build: {
    rollupOptions: {
      input: {
        home: resolve(rootDir, "index.html"),
        about: resolve(rootDir, "about.html"),
        howItWorks: resolve(rootDir, "how-it-works.html"),
        faq: resolve(rootDir, "faq.html"),
        privacy: resolve(rootDir, "privacy.html"),
        terms: resolve(rootDir, "terms.html"),
        contact: resolve(rootDir, "contact.html"),
        notFound: resolve(rootDir, "404.html"),
      },
    },
  },
});

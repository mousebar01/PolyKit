import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const eslintConfig = [
  {
    ignores: ["apps/web/.next/**", "out/**"],
  },
  ...coreWebVitals,
  ...typescript,
  {
    settings: {
      next: { rootDir: "apps/web" },
    },
    rules: {
      "react-hooks/immutability": "off",
      "react-hooks/refs": "off",
      "react-hooks/set-state-in-effect": "off",
      // The Agent UI intentionally keeps a few ref-backed callbacks stable
      // across renders. React Compiler's preservation check currently rejects
      // those valid legacy patterns even though the exhaustive-deps rules
      // still cover actual dependency mistakes.
      "react-hooks/preserve-manual-memoization": "off",
    },
  },
];

export default eslintConfig;

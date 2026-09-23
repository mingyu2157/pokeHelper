// eslint-config-next 16 부터는 flat config 를 직접 export 한다.
// @eslint/eslintrc 의 FlatCompat 브리지는 더 이상 쓰지 않는다.
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const config = [
  ...nextCoreWebVitals,
  ...nextTypescript,
  { ignores: [".next/**", "node_modules/**"] },
];

export default config;

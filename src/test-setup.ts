// vitest setup

// jest-dom 的断言（toBeInTheDocument / toBeDisabled / toBeEmptyDOMElement …）
// ⚠ 这个包一直在 devDependencies 里，但从来没被 import——等于装了没启用。
//    之前的组件测试只好绕着写（toBeTruthy 之类）。这里接上，是纯增量：
//    多了一批断言，没动任何既有行为。
import '@testing-library/jest-dom/vitest';

// jsdom polyfills
Element.prototype.scrollIntoView = () => {};

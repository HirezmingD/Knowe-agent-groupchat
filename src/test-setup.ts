// vitest setup

// jest-dom 的断言（toBeInTheDocument / toBeDisabled / toBeEmptyDOMElement …）
// ⚠ 这个包一直在 devDependencies 里，但从来没被 import——等于装了没启用。
//    之前的组件测试只好绕着写（toBeTruthy 之类）。这里接上，是纯增量：
//    多了一批断言，没动任何既有行为。
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => cleanup());

// jsdom polyfills
Element.prototype.scrollIntoView = () => {};

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

Object.defineProperty(globalThis, 'ResizeObserver', {
  configurable: true,
  writable: true,
  value: ResizeObserverStub,
});

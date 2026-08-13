import { vi } from 'vitest';

/**
 * jsdom does not fetch image resources, while Avatar intentionally waits for a
 * successful preload before it swaps the glyph for an <img>. Tests that assert
 * the loaded state install this deterministic browser-image stand-in.
 */
class AutoLoadingImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private value = '';

  get src(): string {
    return this.value;
  }

  set src(value: string) {
    this.value = value;
    queueMicrotask(() => this.onload?.());
  }
}

export function installAutoLoadingImage(): void {
  vi.stubGlobal('Image', AutoLoadingImage as unknown as typeof Image);
}

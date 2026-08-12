/**
 * FileCard.fold.test.tsx — v1.0.17 · 变更集 E · AC-FC-1 / AC-FC-2
 *
 * Bounded file-card folding: dedupe order/identity unchanged; ≤6 render in full with no
 * control; >6 render the first 6 with a single keyboard-accessible toggle that reports
 * the total; folding state is local to the component instance (messages fold
 * independently); when collapsed only the visible cards are mounted (fixed DOM upper
 * bound). Preview/reveal side effects are mocked — this test is about the fold contract.
 */

import { describe, it, expect, vi, beforeEach, afterEach, cleanup } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

vi.mock('../store/filePreview', () => ({
  openPreviewWindow: vi.fn(() => Promise.resolve()),
  revealFileInFolder: vi.fn(() => Promise.resolve()),
}));

import { FileCardList, FILE_CARD_COLLAPSE_LIMIT } from './FileCard';
import type { ProducedFile } from '../store/state';

beforeEach(() => { cleanup(); });
afterEach(() => { cleanup(); });

function files(n: number, prefix = 'f'): ProducedFile[] {
  return Array.from({ length: n }, (_, i) => ({
    path: `${prefix}/${i}.txt`,
    name: `${prefix}-${i}.txt`,
  })) as ProducedFile[];
}

function cardCount(): number {
  return document.querySelectorAll('.file-card').length;
}

function toggle(): HTMLElement | null {
  return document.querySelector('.fc-fold-toggle');
}

describe('FileCardList bounded folding (AC-FC-1)', () => {
  it('0 files → renders nothing', () => {
    const { container } = render(<FileCardList files={[]} projectId="p1" />);
    expect(container.firstChild).toBeNull();
  });

  it('1 file → full, no control', () => {
    render(<FileCardList files={files(1)} projectId="p1" />);
    expect(cardCount()).toBe(1);
    expect(toggle()).toBeNull();
  });

  it('6 files → full, no control', () => {
    render(<FileCardList files={files(6)} projectId="p1" />);
    expect(cardCount()).toBe(FILE_CARD_COLLAPSE_LIMIT);
    expect(toggle()).toBeNull();
  });

  it('7 files → first 6 + a control', () => {
    render(<FileCardList files={files(7)} projectId="p1" />);
    expect(cardCount()).toBe(6);
    expect(toggle()).not.toBeNull();
  });

  it('20 files → collapsed shows 6; expand shows all; collapse returns to 6', () => {
    render(<FileCardList files={files(20)} projectId="p1" />);
    expect(cardCount()).toBe(6);
    const btn = toggle()!;
    expect(btn.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(btn);
    expect(cardCount()).toBe(20);
    expect(toggle()!.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(toggle()!);
    expect(cardCount()).toBe(6);
  });

  it('200 files → collapsed mounts only 6 (fixed DOM upper bound)', () => {
    render(<FileCardList files={files(200)} projectId="p1" />);
    expect(cardCount()).toBe(6);
    fireEvent.click(toggle()!);
    expect(cardCount()).toBe(200);
  });

  it('duplicate paths do not count toward the total', () => {
    const dupes = [...files(6), ...files(6)]; // same 6 paths twice
    render(<FileCardList files={dupes} projectId="p1" />);
    // 6 unique → no overflow control.
    expect(cardCount()).toBe(6);
    expect(toggle()).toBeNull();
  });
});

describe('FileCardList accessibility & isolation (AC-FC-2)', () => {
  it('the control is keyboard-operable and exposes aria-expanded', () => {
    render(<FileCardList files={files(10)} projectId="p1" />);
    const btn = toggle()!;
    expect(btn.tagName).toBe('BUTTON'); // natively keyboard-activatable
    expect(btn.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(btn);
    expect(btn.getAttribute('aria-expanded')).toBe('true');
  });

  it('two messages fold independently (state is local)', () => {
    render(
      <div>
        <div data-testid="m1"><FileCardList files={files(10, 'a')} projectId="p1" /></div>
        <div data-testid="m2"><FileCardList files={files(10, 'b')} projectId="p1" /></div>
      </div>,
    );
    const m1 = screen.getByTestId('m1');
    const m2 = screen.getByTestId('m2');
    // Expand only the first message.
    fireEvent.click(m1.querySelector('.fc-fold-toggle')!);
    expect(m1.querySelectorAll('.file-card').length).toBe(10);
    // The second message stays collapsed.
    expect(m2.querySelectorAll('.file-card').length).toBe(6);
  });
});
